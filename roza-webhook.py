"""RORA Electronics Messenger callback and catalog replies."""

import hmac
import json
import os
import re
import sqlite3
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.error import URLError
from urllib.parse import parse_qs, urlsplit
from urllib.request import Request, urlopen


PRODUCTS = (
    ("CURREN M3471", 1620, "3 months", "৩ মাস", r"curren|m3471|watch|ঘড়ি|ঘড়ি"),
    ("WALTON 398", 21600, "1 year", "১ বছর", r"walton|398|fridge|ফ্রিজ|ওয়ালটন"),
    ("HONOR 200", 31500, "1 year", "১ বছর", r"honor|200|phone|mobile|মোবাইল|অনার"),
)

BANGLA_DIGITS = str.maketrans("০১২৩৪৫৬৭৮৯", "0123456789")

CATALOG_CANDIDATES = (
    ("Xiaomi Redmi Note 14", r"redmi\s*note\s*14|রেডমি\s*নোট\s*14"),
    ("Samsung 43-inch U8000F", r"u8000f|ua43u8000frser"),
    ("Walton Prisma 43 Google TV W43G5M", r"w43g5m|prisma\s*43"),
    ("Walton WFC-3D8-GDEH-DD Inverter", r"wfc[-\s]*3d8[-\s]*gdeh[-\s]*dd|\b3d8\b"),
)

DEMO_PRODUCTS = (
    ("Xiaomi Redmi Note 14 (6/128 GB)", 23400, 6),
    ("Samsung 43-inch U8000F", 45000, 3),
    ("Walton Prisma 43 Google TV W43G5M", 31500, 4),
    ("Walton WFC-3D8-GDEH-DD Inverter", 54000, 2),
)
DEMO_EXISTING_STOCK = {"CURREN M3471": 8, "WALTON 398": 0, "HONOR 200": 5}

PRODUCT_CATEGORIES = (
    ("Fridge", "ফ্রিজ", r"\bfridges?\b|refrigerator|ফ্রিজ", ("WALTON 398", "Walton WFC")),
    ("Mobile", "মোবাইল", r"\b(?:mobiles?|phones?)\b|মোবাইল|ফোন", ("HONOR", "Xiaomi")),
    ("Watch", "ঘড়ি", r"\bwatches\b|\bwatch\b|ঘড়ি|ঘড়ি", ("CURREN",)),
    ("TV", "টিভি", r"\btvs?\b|television|টিভি", ("Samsung", "Walton Prisma")),
)


def catalog_availability(question, catalog, stock, bangla):
    asks_stock = re.search(r"stock|availab|\bhave\b|\bwhat\b|\bhow many\b|কত|স্টক|আছে|পাওয়া|কি কি|কী কী", question)
    asks_catalog = re.search(r"products?|catalog|পণ্য|ক্যাটালগ|কি কি|কী কী", question)
    categories = [category for category in PRODUCT_CATEGORIES if re.search(category[2], question)]
    if not (asks_stock and (asks_catalog or categories)):
        return None
    if re.search(r"m3471|\b398\b|honor\s*200|redmi\s*note|u8000f|w43g5m|3d8", question):
        return None

    lines = []
    for english, bengali, _, prefixes in categories or PRODUCT_CATEGORIES:
        models = [name for name in catalog if name.startswith(prefixes)]
        available = [f"{name} ({stock[name]})" for name in models if stock.get(name, 0) > 0]
        unknown = any(name not in stock for name in models)
        label = bengali if bangla else english
        details = ", ".join(available)
        if unknown:
            check = "স্টক যাচাই করতে হবে" if bangla else "stock needs checking"
            details = f"{details}; {check}" if details else check
        if not details:
            details = "স্টকে নেই" if bangla else "out of stock"
        lines.append(f"{label}: {details}")
    return "\n".join(lines)


def init_orders(db):
    db.execute("CREATE TABLE IF NOT EXISTS drafts(sender TEXT PRIMARY KEY, model TEXT NOT NULL, quantity INTEGER)")
    db.execute("CREATE TABLE IF NOT EXISTS orders(id INTEGER PRIMARY KEY, sender TEXT NOT NULL, model TEXT NOT NULL, quantity INTEGER NOT NULL)")
    db.execute("CREATE TABLE IF NOT EXISTS chat_context(sender TEXT PRIMARY KEY, model TEXT NOT NULL)")
    db.execute("CREATE TABLE IF NOT EXISTS pending_contact(sender TEXT PRIMARY KEY, name TEXT)")
    db.execute("CREATE TABLE IF NOT EXISTS order_contacts(order_id INTEGER PRIMARY KEY, name TEXT NOT NULL, address TEXT NOT NULL)")


def order_reply(db, sender, text, stock, demo=False):
    text = text.strip()
    bangla = bool(re.search(r"[\u0980-\u09ff]", text))
    clean = text.lower().translate(BANGLA_DIGITS).strip(" ?!।")
    products = PRODUCTS
    if demo:
        products = tuple(
            (name, price, "1 year", "১ বছর", pattern)
            for (name, price, _), (_, pattern) in zip(DEMO_PRODUCTS, CATALOG_CANDIDATES, strict=True)
        ) + PRODUCTS
        stock = DEMO_EXISTING_STOCK | {name: count for name, _, count in DEMO_PRODUCTS} | stock
    catalog = {p[0]: p for p in products}
    availability = catalog_availability(clean, catalog, stock, bangla)
    if availability:
        return availability
    draft = db.execute("SELECT model, quantity FROM drafts WHERE sender=?", (sender,)).fetchone()
    contact = db.execute("SELECT name FROM pending_contact WHERE sender=?", (sender,)).fetchone()
    last = db.execute("SELECT model FROM chat_context WHERE sender=?", (sender,)).fetchone()

    def clear_order():
        db.execute("DELETE FROM drafts WHERE sender=?", (sender,))
        db.execute("DELETE FROM pending_contact WHERE sender=?", (sender,))

    def unavailable(model, quantity=1):
        return model in stock and stock[model] < quantity

    def stock_message():
        return "চাওয়া পরিমাণ স্টকে নেই।" if bangla else "That quantity is out of stock."

    if clean in {"cancel", "বাতিল"}:
        clear_order()
        return "অর্ডার বাতিল হয়েছে।" if bangla else "Order cancelled."

    if contact and draft:
        if re.search(r"\b(?:available|stock)\b|আছে|স্টক|পাওয়া", clean):
            selected = next((p for p in products if re.search(p[4], clean)), None)
            if selected:
                if unavailable(selected[0]):
                    return "এখন স্টকে নেই।" if bangla else "Currently out of stock."
                if selected[0] in stock:
                    count = stock[selected[0]]
                    return f"স্টকে {count}টি আছে।" if bangla else f"{count} in stock."
                return "স্টক দোকান যাচাই করবে।" if bangla else "The shop will check stock."
        model, quantity = draft
        if unavailable(model, quantity):
            clear_order()
            return stock_message()
        if contact[0] is None:
            if not 2 <= len(text) <= 100 or not any(c.isalpha() for c in text):
                return "আপনার নাম লিখুন।" if bangla else "Please enter your name."
            db.execute("UPDATE pending_contact SET name=? WHERE sender=?", (text, sender))
            return "ডেলিভারির ঠিকানা লিখুন।" if bangla else "Please enter your delivery address."
        if not 5 <= len(text) <= 500 or clean in {"confirm", "নিশ্চিত", "কনফার্ম"}:
            return "সম্পূর্ণ ডেলিভারির ঠিকানা লিখুন।" if bangla else "Please enter the full delivery address."
        if demo:
            clear_order()
            return "পরীক্ষার অর্ডার সম্পন্ন।" if bangla else "Test order completed."
        order_id = db.execute(
            "INSERT INTO orders(sender, model, quantity) VALUES(?,?,?)", (sender, model, quantity)
        ).lastrowid
        db.execute("INSERT INTO order_contacts VALUES(?,?,?)", (order_id, contact[0], text))
        clear_order()
        return f"অর্ডার অনুরোধ #{order_id} জমা হয়েছে।" if bangla else f"Order request #{order_id} received."

    if clean in {"confirm", "নিশ্চিত", "কনফার্ম", "confirm order", "অর্ডার নিশ্চিত"}:
        if not draft or not draft[1] or draft[0] not in catalog:
            return "আগে মডেল ও পরিমাণ লিখুন।" if bangla else "Please enter the model and quantity first."
        if unavailable(draft[0], draft[1]):
            clear_order()
            return stock_message()
        db.execute("INSERT OR REPLACE INTO pending_contact VALUES(?,NULL)", (sender,))
        total = catalog[draft[0]][1] * draft[1] + 60
        if bangla:
            return f"ডেলিভারি ৳৬০। মোট ৳{total:,}। আপনার নাম লিখুন।"
        return f"Delivery ৳60. Total ৳{total:,}. Please enter your name."

    if draft and draft[0] and re.fullmatch(r"[0-9]{1,3}", clean):
        quantity = int(clean)
        if not quantity:
            return "১ বা তার বেশি লিখুন।" if bangla else "Enter a quantity of at least 1."
        if unavailable(draft[0], quantity):
            return stock_message()
        db.execute("UPDATE drafts SET quantity=? WHERE sender=?", (quantity, sender))
        return "অর্ডার নিশ্চিত করতে CONFIRM লিখুন।" if bangla else "Type CONFIRM to confirm your order."

    if not demo:
        for _, pattern in CATALOG_CANDIDATES:
            if re.search(pattern, clean):
                return "দাম ও স্টক দোকান জানাবে।" if bangla else "Please check with the shop for price and stock."

    product = next((p for p in products if re.search(p[4], clean)), None)
    previous = catalog.get(draft[0] if draft and draft[0] else last[0] if last else "")
    if product:
        db.execute("INSERT OR REPLACE INTO chat_context VALUES(?,?)", (sender, product[0]))
    if re.search(r"discount|offer|ছাড়|ছাড়|অফার", clean):
        selected = product or previous
        if not selected:
            return "সব পণ্যে ১০% ছাড়। কোন মডেল?" if bangla else "10% off all products. Which model?"
        sale = selected[1]
        original = sale * 10 // 9
        return f"১০% ছাড়ে ৳{original:,} → ৳{sale:,}।" if bangla else f"10% off: ৳{original:,} → ৳{sale:,}."
    if product and re.search(r"price|cost|দাম|মূল্য", clean):
        return f"৳{product[1] * 10 // 9:,}"
    ordering = re.search(r"\border\b|\bbuy\b|অর্ডার|কিনতে", clean)
    if ordering or (draft and not draft[0] and product):
        selected = product or previous
        if selected and unavailable(selected[0]):
            return stock_message()
        db.execute("INSERT OR REPLACE INTO drafts VALUES(?,?,NULL)", (sender, selected[0] if selected else ""))
        if not selected:
            return "মডেল লিখুন।" if bangla else "Which model?"
        return "কয়টি লাগবে?" if bangla else "How many units?"
    if re.search(r"stock|available|আছে|স্টক|পাওয়া", clean):
        selected = product or previous
        if not selected:
            return "কোন মডেল?" if bangla else "Which model?"
        if unavailable(selected[0]):
            return "এখন স্টকে নেই।" if bangla else "Currently out of stock."
        if selected[0] in stock:
            return "স্টকে আছে।" if bangla else "In stock."
        return "স্টক দোকান যাচাই করবে।" if bangla else "The shop will check stock."
    if product and re.search(r"warranty|ওয়ারেন্টি|ওয়ারেন্টি", clean):
        return product[3] if bangla else product[2]
    if product or re.search(r"price|cost|দাম|মূল্য", clean):
        selected = product or previous
        if selected:
            return f"৳{selected[1] * 10 // 9:,}"
        return "কোন মডেল?" if bangla else "Which model?"
    return reply(text)


def reply(text):
    bangla = bool(re.search(r"[\u0980-\u09ff]", text))
    if re.search(r"delivery|shipping|ডেলিভারি", text, re.I):
        return "সারা বাংলাদেশে ডেলিভারি চার্জ প্রতি অর্ডারে ৬০ টাকা।" if bangla else "Delivery across Bangladesh costs ৳60 per order."
    if re.search(r"payment|cash|cod|bkash|nagad|card|পেমেন্ট|বিকাশ|নগদ|ক্যাশ", text, re.I):
        return "bKash, Nagad, Card ও ক্যাশ অন ডেলিভারি আছে।" if bangla else "We accept bKash, Nagad, Card and cash on delivery."
    if re.search(r"return|replacement|ফেরত|রিপ্লেস", text, re.I):
        return "৭ দিনের রিপ্লেসমেন্ট। শর্ত দোকান নিশ্চিত করবে।" if bangla else "We offer 7-day replacement. The shop will confirm eligibility and terms."
    if re.search(r"warranty|ওয়ারেন্টি|ওয়ারেন্টি", text, re.I):
        return "ইলেকট্রনিক্স ও মোবাইলে ১ বছর, ঘড়িতে ৩ মাস ওয়ারেন্টি।" if bangla else "Warranty: 1 year on electronics and mobile phones, 3 months on watches."
    if re.search(r"discount|ছাড়|ছাড়", text, re.I):
        return "স্টক ক্লিয়ারেন্সে সব পণ্যে ১০% ছাড়।" if bangla else "Our stock clearance offers 10% off all products."
    return (
        "RORA ELECTRONICS-এ স্বাগতম। দাম জানতে CURREN M3471, WALTON 398 বা HONOR 200 লিখুন। অন্য প্রশ্নে দোকানের কর্মী সাহায্য করতে পারবেন।"
        if bangla else
        "Welcome to RORA ELECTRONICS. For prices, type CURREN M3471, WALTON 398 or HONOR 200. Our shop team can help with other questions."
    )


class CallbackHandler(BaseHTTPRequestHandler):
    def log_request(self, code="-", size="-"):
        # The verification token can appear in the query string.
        print(f"HTTP {self.command} {code}", flush=True)

    def respond(self, status, body):
        payload = body.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def do_GET(self):
        url = urlsplit(self.path)
        if url.path == "/health":
            self.respond(200, "OK")
            return
        if url.path != "/webhook":
            self.respond(404, "Not found")
            return

        query = parse_qs(url.query)
        token = query.get("hub.verify_token", [""])[0]
        challenge = query.get("hub.challenge", [""])[0]
        expected = self.server.verify_token
        if (
            query.get("hub.mode") == ["subscribe"]
            and hmac.compare_digest(token.encode(), expected)
            and challenge.isascii()
            and challenge.isdecimal()
        ):
            self.respond(200, challenge)
        else:
            self.respond(403, "Verification rejected")

    def do_POST(self):
        if urlsplit(self.path).path != "/webhook":
            self.respond(404, "Not found")
            return
        server = self.server
        if not (server.app_secret and server.page_token and server.graph_version):
            self.respond(503, "Message processing is not configured")
            return
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            length = 0
        if not 0 < length <= 262144:
            self.respond(400, "Invalid request length")
            return
        raw = self.rfile.read(length)
        signature = "sha256=" + hmac.digest(server.app_secret, raw, "sha256").hex()
        if not hmac.compare_digest(signature.encode(), self.headers.get("X-Hub-Signature-256", "").encode()):
            self.respond(403, "Invalid signature")
            return
        try:
            payload = json.loads(raw)
        except (ValueError, UnicodeError):
            self.respond(400, "Invalid JSON")
            return
        if payload.get("object") == "page":
            try:
                self.handle_messages(payload)
            except (URLError, TimeoutError):
                self.respond(502, "Messenger reply failed")
                return
        self.respond(200, "OK")

    def handle_messages(self, payload):
        page_id = "1400727149798277"
        for entry in payload.get("entry", []):
            if entry.get("id") != page_id:
                continue
            for event in entry.get("messaging", []):
                message = event.get("message", {})
                sender = event.get("sender", {}).get("id")
                text = message.get("text")
                timestamp = event.get("timestamp", 0)
                if not sender or sender == page_id or message.get("is_echo") or not text:
                    continue
                if not 0 <= time.time() - timestamp / 1000 < 86400:
                    continue
                with sqlite3.connect(self.server.order_db) as db:
                    db.execute("BEGIN IMMEDIATE")
                    response = order_reply(db, sender, text, self.server.stock, self.server.demo)
                self.send_messenger_reply(page_id, sender, response)

    def send_messenger_reply(self, page_id, sender, text):
        server = self.server
        body = json.dumps({
            "recipient": {"id": sender},
            "messaging_type": "RESPONSE",
            "message": {"text": text},
        }).encode()
        request = Request(
            f"https://graph.facebook.com/{server.graph_version}/{page_id}/messages",
            data=body,
            headers={
                "Authorization": f"Bearer {server.page_token}",
                "Content-Type": "application/json",
            },
            method="POST",
        )
        with urlopen(request, timeout=4) as response:
            response.read()


def main():
    token = os.environ.get("META_VERIFY_TOKEN", "").strip()
    if not token:
        raise SystemExit("Set META_VERIFY_TOKEN before starting the service.")
    port = int(os.environ.get("PORT", "10000"))
    with ThreadingHTTPServer(("0.0.0.0", port), CallbackHandler) as server:
        server.verify_token = token.encode()
        server.app_secret = os.environ.get("META_APP_SECRET", "").strip().encode()
        server.page_token = os.environ.get("META_PAGE_ACCESS_TOKEN", "").strip()
        server.graph_version = os.environ.get("META_GRAPH_VERSION", "").strip()
        server.stock = json.loads(os.environ.get("RORA_STOCK_JSON", os.environ.get("ROZA_STOCK_JSON", "{}")))
        server.demo = os.environ.get("RORA_DEMO_MODE", os.environ.get("ROZA_DEMO_MODE")) == "1"
        server.order_db = "roza-demo.sqlite3" if server.demo else os.environ.get("RORA_ORDER_DB", os.environ.get("ROZA_ORDER_DB", "roza-orders.sqlite3"))
        with sqlite3.connect(server.order_db) as db:
            init_orders(db)
        if server.graph_version and not re.fullmatch(r"v[0-9]+\.[0-9]+", server.graph_version):
            raise SystemExit("META_GRAPH_VERSION must look like v00.0.")
        server.serve_forever()


if __name__ == "__main__":
    main()

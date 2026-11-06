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


def order_reply(db, sender, text, stock, demo=False):
    bangla = bool(re.search(r"[\u0980-\u09ff]", text))
    clean = text.strip().lower().translate(BANGLA_DIGITS)
    demo_products = tuple(
        (name, price, "1 year", "১ বছর", pattern)
        for (name, price, _), (_, pattern) in zip(DEMO_PRODUCTS, CATALOG_CANDIDATES, strict=True)
    ) if demo else ()
    products = PRODUCTS + demo_products
    if demo:
        stock = DEMO_EXISTING_STOCK | {name: count for name, _, count in DEMO_PRODUCTS} | stock
    draft = db.execute("SELECT model, quantity FROM drafts WHERE sender=?", (sender,)).fetchone()
    if clean in {"cancel", "বাতিল"}:
        db.execute("DELETE FROM drafts WHERE sender=?", (sender,))
        return "অর্ডারের খসড়া বাতিল হয়েছে।" if bangla else "Your order draft was cancelled."
    for name, pattern in CATALOG_CANDIDATES:
        if not demo and re.search(pattern, clean):
            if bangla:
                return f"{name}: দোকানের দাম ও স্টক এখনও যাচাই করা হয়নি। দোকানের কর্মী জানাবেন।"
            return f"{name}: our shop price and stock are awaiting verification. Please check with the shop team."
    product = next((p for p in demo_products if re.search(p[4], clean, re.I)), None)
    if not product:
        product = next((p for p in PRODUCTS if re.search(p[4], clean, re.I)), None)
    if draft and draft[0] and re.fullmatch(r"[0-9]{1,3}", clean):
        product = None
    if product and stock.get(product[0]) == 0:
        if draft and draft[0] == product[0]:
            db.execute("DELETE FROM drafts WHERE sender=?", (sender,))
        return f"{product[0]} এখন স্টকে নেই।" if bangla else f"{product[0]} is currently out of stock."
    if clean in {"confirm", "নিশ্চিত", "কনফার্ম"}:
        if not draft or not draft[1]:
            return "আগে মডেল ও পরিমাণ দিন।" if bangla else "Please give the model and quantity first."
        model, quantity = draft
        if model in stock and quantity > stock[model]:
            db.execute("DELETE FROM drafts WHERE sender=?", (sender,))
            return "চাওয়া পরিমাণ স্টকে নেই।" if bangla else "The requested quantity is unavailable."
        if demo:
            db.execute("DELETE FROM drafts WHERE sender=?", (sender,))
            return "নমুনা অর্ডার নিশ্চিত হয়েছে।" if bangla else "Demo order confirmed."
        order_id = db.execute(
            "INSERT INTO orders(sender, model, quantity) VALUES(?,?,?)", (sender, model, quantity)
        ).lastrowid
        db.execute("DELETE FROM drafts WHERE sender=?", (sender,))
        if bangla:
            return f"অর্ডার অনুরোধ #{order_id} নিশ্চিত হয়েছে। দোকান স্টক ও ডেলিভারি যাচাই করে এই চ্যাটে জানাবে।"
        return f"Order request #{order_id} confirmed. The shop will verify stock and delivery in this chat."
    if re.search(r"\border\b|\bbuy\b|অর্ডার|কিনতে", clean, re.I) or (draft and product):
        db.execute("INSERT OR REPLACE INTO drafts VALUES(?,?,NULL)", (sender, product[0] if product else ""))
        if not product:
            return "পণ্যের মডেল লিখুন।" if bangla else "Please enter the product model."
        return f"{product[0]} কয়টি লাগবে? শুধু সংখ্যা লিখুন।" if bangla else f"How many {product[0]} units? Enter a number."
    if draft and draft[0] and re.fullmatch(r"[0-9]{1,3}", clean):
        quantity = int(clean)
        model = draft[0]
        if quantity < 1 or (model in stock and quantity > stock[model]):
            return "এই পরিমাণ নেওয়া যাচ্ছে না। অন্য সংখ্যা দিন।" if bangla else "That quantity is unavailable. Enter another quantity."
        sale_price = next((p[1] for p in products if p[0] == model), None)
        if sale_price is None:
            db.execute("DELETE FROM drafts WHERE sender=?", (sender,))
            return "মডেলটি আবার লিখুন।" if bangla else "Please enter the model again."
        original = sale_price * 10 // 9
        db.execute("UPDATE drafts SET quantity=? WHERE sender=?", (quantity, sender))
        discount = (original - sale_price) * quantity
        total = sale_price * quantity + 60
        if bangla:
            return f"{model} × {quantity}\n১০% ছাড়: ৳{discount:,}\nডেলিভারি: ৳60\nমোট: ৳{total:,}\nঅনুরোধ নিশ্চিত করতে CONFIRM লিখুন। দোকান স্টক যাচাই করবে।"
        return f"{model} × {quantity}\n10% discount: ৳{discount:,}\nDelivery: ৳60\nTotal: ৳{total:,}\nType CONFIRM to submit the request. The shop will check stock."
    return reply(text, products=demo_products + PRODUCTS)


def reply(text, products=PRODUCTS):
    bangla = bool(re.search(r"[\u0980-\u09ff]", text))
    if re.search(r"order|buy|অর্ডার|কিনতে", text, re.I):
        return (
            "অর্ডারের জন্য পণ্যের মডেল ও কয়টি লাগবে লিখুন। দোকানের কর্মী স্টক যাচাই করে জানাবেন।"
            if bangla else
            "For an order, tell us the model and quantity. Our shop team will check stock and follow up here."
        )
    for name, price, en_warranty, bn_warranty, pattern in products:
        if re.search(pattern, text, re.I):
            if bangla:
                return f"{name}: ১০% ছাড়ে ৳{price:,}। ডেলিভারিসহ ৳{price + 60:,}। ওয়ারেন্টি {bn_warranty}। স্টক দোকান নিশ্চিত করবে।"
            return f"{name}: ৳{price:,} after 10% off. ৳{price + 60:,} including delivery. Warranty: {en_warranty}. The shop will confirm stock."
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
                if self.server.demo:
                    prefix = "নমুনা দাম ও স্টক।\n" if re.search(r"[\u0980-\u09ff]", text) else "DEMO — Sample prices and stock.\n"
                    response = prefix + response
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
            db.execute("CREATE TABLE IF NOT EXISTS drafts(sender TEXT PRIMARY KEY, model TEXT NOT NULL, quantity INTEGER)")
            db.execute("CREATE TABLE IF NOT EXISTS orders(id INTEGER PRIMARY KEY, sender TEXT NOT NULL, model TEXT NOT NULL, quantity INTEGER NOT NULL)")
        if server.graph_version and not re.fullmatch(r"v[0-9]+\.[0-9]+", server.graph_version):
            raise SystemExit("META_GRAPH_VERSION must look like v00.0.")
        server.serve_forever()


if __name__ == "__main__":
    main()

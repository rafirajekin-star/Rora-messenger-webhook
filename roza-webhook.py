"""First deployment milestone: Meta callback verification."""

import hmac
import os
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlsplit


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
        if urlsplit(self.path).path == "/webhook":
            self.respond(503, "Message processing is not enabled")
        else:
            self.respond(404, "Not found")


def main():
    token = os.environ.get("META_VERIFY_TOKEN", "").strip()
    if not token:
        raise SystemExit("Set META_VERIFY_TOKEN before starting the service.")
    port = int(os.environ.get("PORT", "10000"))
    with ThreadingHTTPServer(("0.0.0.0", port), CallbackHandler) as server:
        server.verify_token = token.encode()
        server.serve_forever()


if __name__ == "__main__":
    main()

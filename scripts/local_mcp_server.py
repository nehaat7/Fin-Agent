"""Run the MCP Lambda handler locally on http://127.0.0.1:8765/mcp (mimics a Lambda Function URL event)."""
import sys
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "mcp_server"))
import app  # noqa: E402


class H(BaseHTTPRequestHandler):
    def _go(self):
        body = self.rfile.read(int(self.headers.get("content-length") or 0)).decode()
        event = {"rawPath": self.path, "headers": dict(self.headers), "body": body, "isBase64Encoded": False,
                 "requestContext": {"http": {"method": self.command}}}
        resp = app.handler(event, None)
        self.send_response(resp["statusCode"])
        for k, v in resp["headers"].items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(resp["body"].encode())

    do_GET = do_POST = do_DELETE = _go


HTTPServer(("127.0.0.1", 8765), H).serve_forever()

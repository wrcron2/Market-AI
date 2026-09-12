#!/usr/bin/env python3
"""mac-control web server (container): serves the UI and proxies /api/*
to the host agent, injecting the shared token so the browser never sees it."""
import http.client, json, os
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

PORT = 8100
STATIC = os.path.join(os.path.dirname(os.path.abspath(__file__)), "static")
AGENT_HOST = os.environ.get("AGENT_HOST", "host.docker.internal")
AGENT_PORT = int(os.environ.get("AGENT_PORT", "9999"))

def token():
    try:
        return open("/run/agent-token").read().strip()
    except OSError:
        return os.environ.get("AGENT_TOKEN", "")

class H(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass
    def do_GET(self):
        if self.path.startswith("/api/"):
            return self.proxy("GET")
        path = self.path.split("?")[0]
        if path == "/":
            path = "/index.html"
        path = path.lstrip("/")
        if ".." in path:
            return self.send_error(403)
        fp = os.path.join(STATIC, path)
        try:
            with open(fp, "rb") as f:
                body = f.read()
        except OSError:
            return self.send_error(404)
        ctype = "text/html" if fp.endswith(".html") else \
                "text/css" if fp.endswith(".css") else \
                "application/javascript" if fp.endswith(".js") else "application/octet-stream"
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)
    def do_POST(self):
        if self.path.startswith("/api/"):
            return self.proxy("POST")
        self.send_error(404)
    def proxy(self, method):
        n = int(self.headers.get("Content-Length", 0) or 0)
        body = self.rfile.read(n) if n else None
        try:
            conn = http.client.HTTPConnection(AGENT_HOST, AGENT_PORT, timeout=650)
            conn.request(method, self.path, body=body, headers={
                "Content-Type": "application/json",
                "X-Agent-Token": token(),
            })
            r = conn.getresponse()
            data = r.read()
            self.send_response(r.status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)
        except Exception as e:
            payload = json.dumps({"error": f"host agent unreachable: {e}. Is agent/agent.sh running on the Mac?"}).encode()
            self.send_response(502)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

if __name__ == "__main__":
    print(f"mac-control web on :{PORT}, agent at {AGENT_HOST}:{AGENT_PORT}")
    ThreadingHTTPServer(("0.0.0.0", PORT), H).serve_forever()

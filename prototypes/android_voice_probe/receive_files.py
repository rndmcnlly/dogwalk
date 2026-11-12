# /// script
# requires-python = ">=3.11"
# dependencies = []
# ///
"""Throwaway tailnet file receiver. Uploads stay private in ignored .tools/uploads.

uv run --script receive_files.py --bind TAILNET_IP --port 8766 --once
The --once receiver exits after one successful multi-file upload, notifying its
background caller. It never serves the received files or lists their contents.
"""
import argparse
from email import policy
from email.parser import BytesParser
from http.server import BaseHTTPRequestHandler, HTTPServer
import json
import os
from pathlib import Path
import re
import uuid

ROOT = Path(__file__).resolve().parent
MAX_BYTES = 32 * 1024 * 1024
PAGE = b"""<!doctype html><html><head><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Send Dogwalk logs</title><style>
body{font:18px system-ui;max-width:36rem;margin:3rem auto;padding:0 1rem;background:#f4f5f1;color:#18251c}
section{background:white;padding:1.5rem;border-radius:16px}button,input{font:inherit;max-width:100%;margin:1rem 0}
button{display:block;background:#285d40;color:white;border:0;border-radius:8px;padding:.8rem 1.2rem}
#result{white-space:pre-wrap;overflow-wrap:anywhere}</style></head><body><section>
<h1>Send Dogwalk logs</h1><p>Choose one or more exported diagnostic files. They go directly to your laptop over Tailscale.</p>
<form id="form"><input aria-label="Choose files" type="file" name="files" multiple required>
<button id="send">Upload files</button></form><p id="result" role="status">32 MB maximum per batch. Includes any transcripts in your files.</p>
</section><script>
document.querySelector('#form').onsubmit=async e=>{e.preventDefault();const b=document.querySelector('#send'),r=document.querySelector('#result');
b.disabled=true;r.textContent='Uploading...';try{const response=await fetch('/upload',{method:'POST',body:new FormData(e.target)});
const data=await response.json();if(!response.ok)throw Error(data.error||'Upload failed');
r.textContent='Received on your laptop:\\n'+data.files.join('\\n')+'\\n\\nYou can return to the chat. This receiver has now closed.';
}catch(error){r.textContent=error.message;b.disabled=false}};</script></body></html>"""


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bind", required=True, help="Laptop's Tailscale IPv4 address, not 0.0.0.0")
    parser.add_argument("--port", type=int, default=8766)
    parser.add_argument("--once", action="store_true")
    args = parser.parse_args()
    if not re.fullmatch(r"100\.(?:\d{1,3}\.){2}\d{1,3}", args.bind):
        parser.error("Bind to the laptop's 100.x Tailscale IPv4 address")
    output = ROOT / ".tools/uploads"
    output.mkdir(parents=True, exist_ok=True, mode=0o700)
    output.chmod(0o700)
    authority = f"{args.bind}:{args.port}"

    class Receiver(BaseHTTPRequestHandler):
        def setup(self):
            super().setup()
            self.connection.settimeout(60)

        def log_message(self, *_):
            pass  # No request payloads or filenames in access logs.

        def reply(self, status, body, content_type="application/json"):
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Connection", "close")
            self.end_headers()
            self.wfile.write(body)
            self.wfile.flush()

        def allowed(self):
            if self.headers.get("Host") != authority:
                self.reply(403, b'{"error":"Unexpected host"}')
                return False
            origin = self.headers.get("Origin")
            if origin and origin != f"http://{authority}":
                self.reply(403, b'{"error":"Cross-origin uploads are blocked"}')
                return False
            return True

        def do_GET(self):
            if not self.allowed():
                return
            if self.path != "/":
                self.reply(404, b'{"error":"Not found"}')
                return
            self.reply(200, PAGE, "text/html; charset=utf-8")

        def do_POST(self):
            if not self.allowed():
                return
            if self.path != "/upload":
                self.reply(404, b'{"error":"Not found"}')
                return
            try:
                length = int(self.headers.get("Content-Length", "0"))
                content_type = self.headers.get("Content-Type", "")
                if not 0 < length <= MAX_BYTES:
                    raise ValueError("Batch must be between 1 byte and 32 MB")
                if not content_type.startswith("multipart/form-data;"):
                    raise ValueError("Expected a multipart file upload")
                data = self.rfile.read(length)
                if len(data) != length:
                    raise ValueError("Incomplete upload; please retry")
                message = BytesParser(policy=policy.default).parsebytes(
                    ("Content-Type: " + content_type + "\r\nMIME-Version: 1.0\r\n\r\n").encode() + data)
                files = []
                for part in message.iter_parts():
                    name = part.get_filename()
                    if name is not None:
                        safe = re.sub(r"[^A-Za-z0-9._-]", "_", name.replace("\\", "/").split("/")[-1])[:120]
                        safe = safe.lstrip(".") or "upload.bin"
                        payload = part.get_payload(decode=True)
                        if payload is None:
                            raise ValueError("Invalid file payload")
                        files.append((safe, payload))
                if not 1 <= len(files) <= 20:
                    raise ValueError("Choose between 1 and 20 files")
                batch = output / uuid.uuid4().hex
                batch.mkdir(mode=0o700)
                saved = []
                for index, (name, payload) in enumerate(files, 1):
                    path = batch / f"{index:02d}-{name}"
                    with path.open("xb") as stream:
                        os.chmod(path, 0o600)
                        stream.write(payload)
                    saved.append({"path": str(path), "name": name, "bytes": len(payload)})
                # Record successful receipt even if the phone disconnects after sending.
                print("UPLOAD_RECEIVED " + json.dumps(saved), flush=True)
                server.received = True
                self.reply(200, json.dumps({"files": [f["name"] for f in saved]}).encode())
            except (ValueError, OSError) as error:
                self.reply(400, json.dumps({"error": str(error)}).encode())

    class TimedServer(HTTPServer):
        expired = False

        def handle_timeout(self):
            self.expired = True

    server = TimedServer((args.bind, args.port), Receiver)
    server.received = False
    server.timeout = 1200
    print(f"READY http://{authority}/ (one batch; 20-minute idle timeout)", flush=True)
    try:
        while not server.expired:
            server.handle_request()
            if args.once and server.received:
                print("RECEIVER_CLOSED: upload ready for review", flush=True)
                break
    finally:
        server.server_close()


if __name__ == "__main__":
    main()

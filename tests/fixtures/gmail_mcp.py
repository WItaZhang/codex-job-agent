"""SYNTHETIC stdio provider. Never imports a network client or reads credential contents."""

import json
import os
import sys
import time
from pathlib import Path

MODE = "ready"
RECORD = ""
seen = {
    "cwd": os.getcwd(),
    "cwd_files": os.listdir(),
    "oauth": os.environ.get("GMAIL_OAUTH_PATH"),
    "credentials": os.environ.get("GMAIL_CREDENTIALS_PATH"),
    "argv": sys.argv[1:],
    "calls": [],
    "pid": os.getpid(),
}
Path(RECORD).write_text(json.dumps(seen))
print("synthetic-secret-stderr", file=sys.stderr)
if MODE == "malformed":
    print("synthetic-secret-malformed", flush=True)
    raise SystemExit(1)
for line in sys.stdin:
    request = json.loads(line)
    if "id" not in request:
        continue
    method = request["method"]
    seen["calls"].append({"method": method, "params": request.get("params")})
    Path(RECORD).write_text(json.dumps(seen))
    if method == "initialize":
        result = {
            "protocolVersion": "2024-11-05",
            "capabilities": {"tools": {}},
            "serverInfo": {"name": "synthetic-gmail", "version": "1"},
        }
    elif method == "tools/list":
        result = {"tools": [{"name": "search_emails", "inputSchema": {"type": "object"}}]}
    elif method == "tools/call":
        if MODE == "timeout":
            time.sleep(30)
        text = "Error: invalid_grant synthetic-secret" if MODE == "revoked" else ""
        result = {"content": [{"type": "text", "text": text}]}
    else:
        raise AssertionError(method)
    print(json.dumps({"jsonrpc": "2.0", "id": request["id"], "result": result}), flush=True)

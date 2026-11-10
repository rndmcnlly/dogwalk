"""Run Wrangler with an explicitly refreshed personal cf OAuth credential.

No credential values are printed, written to the project or installed as runtime
secrets. Use: python3 personal_wrangler.py deploy --no-bundle
"""
import json
import os
from pathlib import Path
import subprocess
import sys

ACCOUNT = "890753ccd6f0bbbfbe2c030cc44f6a87"
ROOT = Path(__file__).resolve().parent
allowed = {
    ("deploy", "--no-bundle"),
    ("secret", "put", "EXPERIMENT_TOKEN"),
    ("delete", "--name", "dogwalk-redimnet-b3-experiment", "--force"),
}
if tuple(sys.argv[1:]) not in allowed:
    raise SystemExit("Only this experiment's deploy, token setup and cleanup commands are allowed.")
env = dict(os.environ, CLOUDFLARE_ACCOUNT_ID=ACCOUNT)
env.pop("CLOUDFLARE_API_TOKEN", None)
# A real authenticated list operation refreshes OAuth; whoami may be cached.
subprocess.run(["cf", "workers", "list", "--profile", "personal"], env=env,
               check=True, stdout=subprocess.DEVNULL)
profile = json.loads((Path.home() / "Library/Preferences/cloudflare/config/personal.json").read_text())
env["CLOUDFLARE_API_TOKEN"] = profile["oauth_token"]
raise SystemExit(subprocess.run(["npx", "wrangler", *sys.argv[1:]], cwd=ROOT, env=env).returncode)

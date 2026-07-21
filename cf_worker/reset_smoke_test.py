# /// script
# requires-python = ">=3.11"
# ///
"""
Remove only the synthetic Spike 1 test registration and pending attempts.
Real phone registrations are preserved. Runs `wrangler d1 execute` against the
remote DB by default.

Usage:
  uv run --script cf_worker/reset_smoke_test.py             # remote
  uv run --script cf_worker/reset_smoke_test.py --local     # local wrangler dev DB
"""
import argparse
import shutil
import subprocess
import sys
from pathlib import Path

WORKER_DIR = Path(__file__).resolve().parent

SQL = """
DELETE FROM registrations WHERE phone_number IN ('+15555550100', '+15555550101');
DELETE FROM claim_attempt WHERE phone_number IN ('+15555550100', '+15555550101');
"""

def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--local", action="store_true", help="reset local wrangler dev DB")
    args = ap.parse_args()
    flag = "--local" if args.local else "--remote"
    local_wrangler = WORKER_DIR / "node_modules" / ".bin" / "wrangler"
    wrangler = str(local_wrangler) if local_wrangler.exists() else shutil.which("wrangler")
    if not wrangler:
        print("wrangler is not installed", file=sys.stderr)
        return 2
    cmd = [wrangler, "d1", "execute", "dogwalk", flag, "--command", SQL.strip()]
    print(f"running: {' '.join(cmd)}")
    r = subprocess.run(cmd, cwd=str(WORKER_DIR))
    return r.returncode

if __name__ == "__main__":
    sys.exit(main())

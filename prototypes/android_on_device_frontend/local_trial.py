# /// script
# requires-python = ">=3.11"
# dependencies = ["aiohttp>=3.12,<4", "websockets==15.0.1"]
# ///
"""Private local Worker + real OpenCode ACP trial. Never deploys or uses Daytona.

start creates fresh synthetic local D1/DO state and leaves two loopback servers running.
prove uses real provider inference and ACP to write a harmless, permission-gated marker.
stop terminates only the supervisor/children belonging to the specified trial directory.
No token or provider secret is printed. Tailscale routes are never changed here.
"""
from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
from pathlib import Path
import secrets
import shlex
import shutil
import signal
import socket
import subprocess
import sys
import tempfile
import time
import uuid

import aiohttp
from websockets.asyncio.client import connect

ROOT = Path(__file__).resolve().parents[2]
WORKER = ROOT / "cf_worker"
WRANGLER = WORKER / "node_modules/.bin/wrangler"
TEMP = Path("/private/var/folders/qj/gyqbn5y57956rrqd_g6yfqwr0000gn/T/opencode")
PHONE = "+15555550199"  # Synthetic test identity, never a production Registration.
MARKER = "dogwalk local side-channel verified\n"


def private_write(path: Path, content: str):
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as out:
        out.write(content)


def provider_key(path: Path):
    # Read only the explicitly authorized OpenAI variable. Never source an operator env.
    for line in path.read_text().splitlines():
        name, sep, value = line.removeprefix("export ").partition("=")
        if sep and name.strip() == "OPENAI_API_KEY":
            words = shlex.split(value, comments=True)
            if len(words) == 1 and words[0]:
                return words[0]
    raise RuntimeError("Explicit provider env has no usable OPENAI_API_KEY")


def clean_env(state: Path):
    env = {"PATH": os.environ.get("PATH", "/usr/bin:/bin"), "HOME": str(state / "home"),
           "TMPDIR": str(state / "tmp"), "TERM": "dumb", "NO_COLOR": "1", "WRANGLER_SEND_METRICS": "false"}
    for key, directory in [("XDG_CONFIG_HOME", "config"), ("XDG_DATA_HOME", "data"), ("XDG_CACHE_HOME", "cache"), ("XDG_STATE_HOME", "state")]:
        env[key] = str(state / "opencode" / directory)
    for key in ["HOME", "TMPDIR", "XDG_CONFIG_HOME", "XDG_DATA_HOME", "XDG_CACHE_HOME", "XDG_STATE_HOME"]:
        Path(env[key]).mkdir(parents=True, exist_ok=True, mode=0o700)
    env["OPENCODE_DB"] = str(state / "opencode/private.db")
    return env


def wrangler(state: Path, *arguments: str):
    return [str(WRANGLER), *arguments, "--config", str(state / "cf/wrangler.json")]


def db(state: Path, sql: str):
    out = subprocess.check_output(wrangler(state, "d1", "execute", "dogwalk-local-trial", "--local", "--persist-to", str(state / "persist"), "--json", "--command", sql), cwd=state / "cf", env=clean_env(state), stderr=subprocess.PIPE, text=True)
    return json.loads(out)


def start(args):
    if not WRANGLER.exists() or not shutil.which("opencode"):
        raise RuntimeError("Existing Wrangler and OpenCode V2 installation required")
    for port in [args.worker_port, args.acp_port]:
        if port == 8770 or not 1024 <= port <= 65535:
            raise RuntimeError("Choose isolated nonprivileged ports, never 8770")
        with socket.socket() as sock:
            sock.bind(("127.0.0.1", port))
    state = Path(tempfile.mkdtemp(prefix="dogwalk-local-trial-", dir=TEMP)).resolve()
    os.chmod(state, 0o700)
    for directory in ["workspace", "cf", "persist"]:
        (state / directory).mkdir(mode=0o700)
    token = secrets.token_urlsafe(32)
    private_write(state / "app-token", token)
    key = provider_key(args.provider_env)
    private_write(state / "provider-key", key)
    permissions = [{"action": "*", "resource": "*", "effect": "deny"},
                   {"action": "read", "resource": "*", "effect": "allow"},
                   {"action": "external_directory", "resource": "*", "effect": "deny"}]
    for name in ["trial-marker.txt", "permission-marker.txt"]:
        for resource in [name, str(state / "workspace" / name)]:
            permissions.append({"action": "edit", "resource": resource, "effect": "ask"})
    agent_config = {"$schema": "https://opencode.ai/config.json", "model": "openai/gpt-6.1-sol", "default_agent": "build",
                    "permissions": permissions, "providers": {"openai": {"settings": {"apiKey": "{env:OPENAI_API_KEY}"}}},
                    "snapshots": False, "formatter": False, "warming": False}
    private_write(state / "workspace/opencode.json", json.dumps(agent_config, indent=2))
    private_write(state / "workspace/AGENTS.md", "# Local disposable trial\nNot a security sandbox. Do not use shell, network tools, subagents or external paths. Only read files here and, after explicit permission, write trial-marker.txt or permission-marker.txt. Do not modify configuration or instructions. Use the configured file tools, never shell.\n")
    config = {"name": "dogwalk-local-trial", "main": str(WORKER / "src/local_trial_entry.ts"), "compatibility_date": "2026-07-20", "workers_dev": False,
              "rules": [{"type": "Text", "globs": ["**/*.md", "**/*.txt"], "fallthrough": True}, {"type": "Data", "globs": ["**/*.pcm"], "fallthrough": True}],
              "vars": {"DEPLOYMENT_ENVIRONMENT": "local-app-trial", "LOCAL_APP_TRIAL_ENABLED": "true", "LOCAL_APP_TRIAL_ACP_URL": f"http://127.0.0.1:{args.acp_port}/acp",
                       "LOCAL_APP_TRIAL_WORKSPACE": str(state / "workspace"), "APP_FRONTEND_ENABLED": "true", "APP_FRONTEND_OWNER_PHONE": PHONE,
                       "APP_FRONTEND_TOKEN_SHA256": hashlib.sha256(token.encode()).hexdigest(), "ORCHESTRATOR_MODEL": "gpt-6.1-sol", "ORCHESTRATOR_REASONING_EFFORT": "low",
                       "ORCHESTRATOR_API_BASE": "https://api.openai.com/v1", "DAYTONA_API_BASE": "http://127.0.0.1:9/forbidden", "DAYTONA_API_KEY": ""},
              "d1_databases": [{"binding": "DB", "database_name": "dogwalk-local-trial", "database_id": str(uuid.uuid4()), "remote": False}],
              "durable_objects": {"bindings": [{"name": "VOICE_SESSIONS", "class_name": "VoiceSession"}, {"name": "TERMINAL_RELAYS", "class_name": "TerminalRelay"}]},
              "migrations": [{"tag": "local-v1", "new_sqlite_classes": ["VoiceSession", "TerminalRelay"]}], "observability": {"enabled": False}}
    private_write(state / "cf/wrangler.json", json.dumps(config, indent=2))
    private_write(state / "cf/.dev.vars", "OPENAI_API_KEY=" + json.dumps(key) + "\n")
    # Local-only schema copy names the hosting adapter honestly. Never alter remote D1.
    schema = (WORKER / "schema.sql").read_text().replace("CHECK (provider = 'daytona')", "CHECK (provider IN ('daytona', 'local'))")
    schema += f"\nINSERT INTO invite_codes(code_words) VALUES ('local operator trial');\nINSERT INTO registrations(phone_number, invite_code, registered_at) VALUES ('{PHONE}', 'local operator trial', unixepoch());\nINSERT INTO sandbox_assignments(phone_number, provider, provider_id, identity_hash, state, provisioning_started_at) VALUES ('{PHONE}', 'local', 'local-agent-trial', 'local-app-trial', 'started', unixepoch());\n"
    private_write(state / "cf/schema.sql", schema)
    subprocess.run(wrangler(state, "d1", "execute", "dogwalk-local-trial", "--local", "--persist-to", str(state / "persist"), "--file", str(state / "cf/schema.sql")), cwd=state / "cf", env=clean_env(state), stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, check=True)
    private_write(state / "runtime.json", json.dumps({"state": str(state), "workspace": str(state / "workspace"), "worker_port": args.worker_port, "acp_port": args.acp_port, "opencode": shutil.which("opencode")}))
    read_fd, write_fd = os.pipe()
    with (state / "supervisor.log").open("w") as log:
        process = subprocess.Popen([sys.executable, str(Path(__file__).resolve()), "serve", "--state", str(state), "--ready-fd", str(write_fd)], pass_fds=(write_fd,), stdin=subprocess.DEVNULL, stdout=log, stderr=log, start_new_session=True)
    os.close(write_fd)
    with os.fdopen(read_fd) as pipe:
        message = pipe.readline()
    if not message:
        raise RuntimeError(f"Local startup failed; private logs: {state}")
    print(message.strip())


async def serve(args):
    state = args.state.resolve()
    meta = json.loads((state / "runtime.json").read_text())
    env = clean_env(state)
    agent_env = {**env, "OPENAI_API_KEY": (state / "provider-key").read_text()}
    children = []
    files = []
    try:
        commands = [("gateway", [shutil.which("uv"), "run", "--script", str(ROOT / "sandbox_acp_gateway.py"), "serve", "--host", "127.0.0.1", "--port", str(meta["acp_port"]), "--workspace", meta["workspace"], "--agent-command", shlex.join([meta["opencode"], "acp"])], agent_env, state / "workspace"),
                    ("worker", wrangler(state, "dev", "--local", "--ip", "127.0.0.1", "--port", str(meta["worker_port"]), "--inspector-port", "0", "--persist-to", str(state / "persist")), env, state / "cf")]
        for name, command, child_env, cwd in commands:
            log = (state / f"{name}.log").open("w"); files.append(log)
            child = subprocess.Popen(command, cwd=cwd, env=child_env, stdin=subprocess.DEVNULL, stdout=log, stderr=log, start_new_session=True)
            children.append(child); meta[f"{name}_pid"] = child.pid
        meta["supervisor_pid"] = os.getpid()
        private_write(state / "runtime.json", json.dumps(meta, indent=2))
        async with aiohttp.ClientSession() as http:
            async with asyncio.timeout(45):
                while True:
                    if any(p.poll() is not None for p in children):
                        raise RuntimeError("local trial child exited")
                    try:
                        async with http.get(f"http://127.0.0.1:{meta['acp_port']}/healthz") as response:
                            assert response.status == 200
                        async with http.post(f"http://127.0.0.1:{meta['worker_port']}/app/heartbeat") as response:
                            assert response.status == 401
                        break
                    except (aiohttp.ClientError, AssertionError):
                        await asyncio.sleep(.2)
        os.write(args.ready_fd, (json.dumps({**meta, "credential_file": str(state / "app-token"), "status": "running-loopback-only"}) + "\n").encode()); os.close(args.ready_fd)
        done = asyncio.Event()
        loop = asyncio.get_running_loop()
        for sig in [signal.SIGTERM, signal.SIGINT]:
            loop.add_signal_handler(sig, done.set)
        await done.wait()
    finally:
        for child in children:
            if child.poll() is None:
                os.killpg(child.pid, signal.SIGTERM)
        for child in children:
            try: child.wait(timeout=15)
            except subprocess.TimeoutExpired: os.killpg(child.pid, signal.SIGKILL)
        for file in files: file.close()


async def prove(args):
    state = args.state.resolve(); meta = json.loads((state / "runtime.json").read_text())
    if (state / "proof.json").exists():
        raise RuntimeError("Proof already completed; inspect retained work instead of replaying input")
    # Never automatically replay an input whose acceptance/execution may be unknown.
    attempted = os.open(state / "proof-attempted", os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    os.close(attempted)
    token = (state / "app-token").read_text()
    headers = {"Authorization": f"Bearer {token}"}
    url = f"http://127.0.0.1:{meta['worker_port']}"
    evidence = []; permissions = 0
    async with aiohttp.ClientSession() as http:
        for path in ["/admin", "/voice", "/pair", "/healthz"]:
            async with http.get(url + path) as r: assert r.status == 404
        async def attachment():
            ws = await connect(url.replace("http:", "ws:") + "/app/frontend", additional_headers=headers)
            ready = json.loads(await ws.recv()); assert ready["type"] == "frontend.ready"
            async def heartbeat():
                while True:
                    async with http.post(url + "/app/heartbeat", headers={**headers, "X-Dogwalk-Attachment": ready["attachment"]}) as r:
                        assert r.status == 204
                    await asyncio.sleep(20)
            task = asyncio.create_task(heartbeat())
            return ws, ready, task
        ws, ready, heartbeat = await attachment()
        counter = 0
        async def send(text, offset):
            nonlocal counter
            for event in [{"type": "session.input_transcript.delta", "delta": text, "delivery": "text_typed", "start_ms": offset, "end_ms": offset},
                          {"type": "session.delegation.created", "delegation": {"id": f"local-proof-{offset}", "target": "client"}, "offset_ms": offset}]:
                counter += 1
                await ws.send(json.dumps({"v": 1, "attachment": ready["attachment"], "id": f"proof-{uuid.uuid4()}-{counter}", "event": event}))
        try:
            async with asyncio.timeout(240):
                while not ready.get("agent_ready"):
                    m = json.loads(await ws.recv()); evidence.append(m)
                    if m.get("event", {}).get("type") == "frontend.agent_status" and m["event"].get("status") == "connected": break
                await send("This is the approved local disposable-workspace test. Create one Managed Session named Fir with directory '.', and begin one Prompt Turn. Ask the Agent to write trial-marker.txt containing exactly 'dogwalk local side-channel verified' followed by a newline, using only file tools, then read it to verify. This is not production. If a Permission Request arrives, present its actual choices and wait for my answer.", 10)
                marker = state / "workspace/trial-marker.txt"
                while not marker.exists():
                    m = json.loads(await ws.recv()); evidence.append(m)
                    event = m.get("event", {})
                    if event.get("type") == "session.commentary.append" and permissions == 0:
                        rows = db(state, "SELECT event, detail FROM call_activity WHERE event = 'acp.permission.requested'")
                        if any(part.get("results") for part in rows):
                            permissions += 1
                            await send("For Fir's actual pending Permission Request to write the harmless trial-marker.txt, I authorize Allow once. Select the Agent's offered once option, not always.", 20)
                assert marker.read_text() == MARKER
                await send("Inspect Fir using inspect_managed_session. Is its Prompt Turn stopped, and what did its report verify? Do not start another turn.", 30)
                while True:
                    m = json.loads(await ws.recv()); evidence.append(m)
                    rows = db(state, "SELECT event, detail FROM call_activity WHERE event = 'acp.turn.stopped'")
                    if any(part.get("results") for part in rows): break
            await ws.close(); heartbeat.cancel()
            await asyncio.sleep(.5)
            ws, ready, heartbeat = await attachment()
            await send("I reconnected. Inspect and report Fir's current retained session and Prompt Turn state using the session tools. Do not create sessions, start work, or repeat the previous input.", 40)
            async with asyncio.timeout(90):
                while True:
                    m = json.loads(await ws.recv()); evidence.append(m)
                    if m.get("event", {}).get("type") == "session.commentary.append" and "Fir" in m["event"].get("content", ""):
                        break
            rows = db(state, "SELECT event, detail FROM call_activity WHERE event LIKE 'acp.%' OR event = 'orchestrator.tool'")
            private_write(state / "proof.json", json.dumps({"events": evidence, "audit": rows}, indent=2))
            print(json.dumps({"result": "real-provider-and-real-ACP-marker-verified", "workspace": meta["workspace"], "marker": str(marker), "permission_answers": permissions, "reconnected": True, "evidence_file": str(state / "proof.json")}))
        finally:
            private_write(state / "proof-events.json", json.dumps(evidence, indent=2))
            await ws.close(); heartbeat.cancel()


def main():
    os.umask(0o077)
    p = argparse.ArgumentParser(description=__doc__); sub = p.add_subparsers(dest="command", required=True)
    start_p = sub.add_parser("start"); start_p.add_argument("--provider-env", type=Path, default=Path("/Users/adam/Desktop/dogwalk/.env")); start_p.add_argument("--worker-port", type=int, default=18781); start_p.add_argument("--acp-port", type=int, default=18782)
    for name in ["serve", "prove", "stop"]:
        cmd = sub.add_parser(name); cmd.add_argument("--state", type=Path, required=True)
        if name == "serve": cmd.add_argument("--ready-fd", type=int, required=True)
    args = p.parse_args()
    if args.command == "start": start(args)
    elif args.command == "serve": asyncio.run(serve(args))
    elif args.command == "prove": asyncio.run(prove(args))
    else:
        meta = json.loads((args.state / "runtime.json").read_text()); pid = meta["supervisor_pid"]
        command = subprocess.check_output(["ps", "-p", str(pid), "-o", "args="], text=True)
        if str(args.state.resolve()) not in command or "serve" not in command: raise RuntimeError("refusing to stop unrelated PID")
        os.kill(pid, signal.SIGTERM)
        deadline = time.monotonic() + 25
        while time.monotonic() < deadline:
            alive = False
            for port in [meta["worker_port"], meta["acp_port"]]:
                with socket.socket() as sock:
                    sock.settimeout(.1)
                    alive |= sock.connect_ex(("127.0.0.1", port)) == 0
            if not alive:
                print("Trial servers stopped; private state retained."); break
            time.sleep(.1)
        else: raise RuntimeError("Trial shutdown did not release its ports")


if __name__ == "__main__": main()

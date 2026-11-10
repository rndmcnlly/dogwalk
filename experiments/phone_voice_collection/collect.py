# /// script
# requires-python = ">=3.11"
# dependencies = ["flask", "waitress", "twilio", "python-dotenv", "httpx"]
# ///
"""Attended private collector. Runtime state never lives in this repository."""
import argparse
import hashlib
import io
import json
import logging
import os
from pathlib import Path
import re
import subprocess
import sys
import threading
import time
import wave
from xml.etree import ElementTree as ET

import httpx
from dotenv import dotenv_values
from flask import Flask, Response, abort, request
from twilio.request_validator import RequestValidator
from waitress import serve

from flow import document, phrases

os.umask(0o077)
ROOT = Path.home() / "Library/Application Support/Dogwalk/phone_voice_collection"
ROOT.mkdir(parents=True, exist_ok=True, mode=0o700)
CREDS = dotenv_values("/Users/adam/Desktop/dogwalk/.env")
ACCOUNT = CREDS.get("TWILIO_ACCOUNT_SID")
TOKEN = CREDS.get("TWILIO_AUTH_TOKEN")
if not ACCOUNT or not TOKEN:
    raise SystemExit("Missing local Twilio credentials")
BASE = f"https://api.twilio.com/2010-04-01/Accounts/{ACCOUNT}"
AUTH = (ACCOUNT, TOKEN)
FIELDS = {
    "voice_url": "VoiceUrl", "voice_method": "VoiceMethod",
    "voice_fallback_url": "VoiceFallbackUrl", "voice_fallback_method": "VoiceFallbackMethod",
    "voice_application_sid": "VoiceApplicationSid", "trunk_sid": "TrunkSid",
    "status_callback": "StatusCallback", "status_callback_method": "StatusCallbackMethod",
    "voice_receive_mode": "VoiceReceiveMode", "voice_caller_id_lookup": "VoiceCallerIdLookup",
    "api_version": "ApiVersion",
}
LOCK = threading.RLock()
logging.disable(logging.CRITICAL)


def save(path, value):
    temp = path.with_suffix(path.suffix + ".tmp")
    with temp.open("w") as f:
        json.dump(value, f, indent=2)
        f.flush()
        os.fsync(f.fileno())
    temp.replace(path)


def read(path):
    return json.loads(path.read_text())


def api(method, path, data=None):
    with httpx.Client(timeout=25, follow_redirects=False) as client:
        r = client.request(method, BASE + path, auth=AUTH, data=data)
    if not r.is_success:
        code = None
        try:
            code = r.json().get("code")
        except ValueError:
            pass
        raise RuntimeError(f"Twilio request failed HTTP {r.status_code}, code {code}")
    return r.json() if r.content else {}


def projection(number):
    return {k: number.get(k) or "" for k in FIELDS}


def snapshot():
    if (ROOT / "active.json").exists():
        raise RuntimeError("Existing active collection: restore before resnapshot")
    numbers = api("GET", "/IncomingPhoneNumbers.json?PageSize=100")["incoming_phone_numbers"]
    # No guess among multiple numbers: require the documented Dogwalk webhook.
    matches = [n for n in numbers if "dogwalk" in (n.get("voice_url", "") or "").lower()]
    if len(matches) != 1:
        if len(numbers) == 1:
            matches = numbers
        else:
            save(ROOT / "number_candidates.json", numbers)
            raise RuntimeError("Multiple numbers without unique Dogwalk routing; inspect private candidates")
    n = api("GET", f"/IncomingPhoneNumbers/{matches[0]['sid']}.json")
    save(ROOT / "snapshot.json", {"at": time.time(), "number": n})
    if n.get("voice_application_sid"):
        save(ROOT / "application.json", api("GET", f"/Applications/{n['voice_application_sid']}.json"))
    if n.get("trunk_sid"):
        raise RuntimeError("Trunk association found: snapshot saved; trunk-specific preflight required")
    payload = {v: projection(n)[k] for k, v in FIELDS.items()}
    # TrunkSid and VoiceApplicationSid have mutual-clearing semantics.
    payload.pop("TrunkSid")
    save(ROOT / "restore_payload.json", payload)
    print(json.dumps({"snapshot_saved": True, "number_count": len(numbers),
                      "effective_route": "application" if n.get("voice_application_sid") else "webhook",
                      "dogwalk_webhook_match": bool("dogwalk" in (n.get("voice_url") or "").lower()),
                      "restore_prepared": True}))


def restore(check=False):
    original = read(ROOT / "snapshot.json")["number"]
    current = api("GET", f"/IncomingPhoneNumbers/{original['sid']}.json")
    if check:
        assert projection(current) == projection(original), "Configuration changed since snapshot"
        payload = read(ROOT / "restore_payload.json")
        assert payload["VoiceUrl"] == (original.get("voice_url") or "")
        assert not original.get("trunk_sid")
        print("Restore payload validated against live number; no changes made.")
        return
    active_path = ROOT / "active.json"
    if active_path.exists():
        expected = read(active_path)["expected"]
        if projection(current) not in (expected, projection(original)):
            raise RuntimeError("Unexpected routing drift: refusing to overwrite")
    result = api("POST", f"/IncomingPhoneNumbers/{original['sid']}.json", read(ROOT / "restore_payload.json"))
    assert projection(result) == projection(original), "Restore readback mismatch"
    verify = api("GET", f"/IncomingPhoneNumbers/{original['sid']}.json")
    assert projection(verify) == projection(original), "Restore verification mismatch"
    save(ROOT / "restored.json", {"at": time.time()})
    if active_path.exists():
        active_path.unlink()
    print("Original Twilio incoming voice configuration restored and verified.")


def xml(text):
    return Response(text, content_type="text/xml")


GOODBYE = '<Response><Say voice="man">Collection is not available. Goodbye.</Say><Hangup/></Response>'


def download(sid):
    if not re.fullmatch(r"RE[0-9a-fA-F]{32}", sid):
        raise RuntimeError("Invalid recording SID")
    url = f"{BASE}/Recordings/{sid}.wav"
    # Explicitly prevent automatic forwarding of credentials to media storage.
    with httpx.Client(timeout=30, follow_redirects=False) as client:
        r = client.get(url, auth=AUTH)
        if r.is_redirect:
            target = r.headers.get("location", "")
            if not target.startswith("https://"):
                raise RuntimeError("Unsafe media redirect")
            r = client.get(target)  # No credentials; signed URL stays in memory.
    if not r.is_success:
        raise RuntimeError(f"Media unavailable HTTP {r.status_code}")
    with wave.open(io.BytesIO(r.content), "rb") as wav:
        info = {"channels": wav.getnchannels(), "sample_rate": wav.getframerate(),
                "sample_width": wav.getsampwidth(), "frames": wav.getnframes()}
        assert info["channels"] == 1
    info["sha256"] = hashlib.sha256(r.content).hexdigest()
    return r.content, info


def unauthenticated_denied(sid):
    # Never follows a redirect or stores/logs an unauthenticated response body.
    with httpx.Client(timeout=20, follow_redirects=False) as client:
        with client.stream("GET", f"{BASE}/Recordings/{sid}.wav") as r:
            return r.status_code in (401, 403)


def runtime():
    return read(ROOT / "runtime.json")


def persist_call(call, state):
    save(ROOT / "calls" / f"{call}.json", state)


def make_app():
    app = Flask(__name__, static_folder=None)
    app.config["MAX_CONTENT_LENGTH"] = 64 * 1024

    @app.before_request
    def validate():
        if request.path == "/health":
            return None
        cfg = runtime()
        url = cfg["origin"] + request.full_path.removesuffix("?")
        if not RequestValidator(TOKEN).validate(url, request.form, request.headers.get("X-Twilio-Signature", "")):
            abort(403)
        if request.form.get("AccountSid") != ACCOUNT:
            abort(403)

    @app.get("/health")
    def health():
        return "ok"

    @app.post("/failure")
    def failure():
        return xml(GOODBYE)

    @app.post("/voice")
    def voice():
        call = request.form.get("CallSid", "")
        if not re.fullmatch(r"CA[0-9a-fA-F]{32}", call):
            abort(400)
        with LOCK:
            cfg = runtime()
            path = ROOT / "calls" / f"{call}.json"
            if path.exists():
                state = read(path)
            elif cfg["mode"] == "test":
                if request.form.get("From") != cfg["number"] or cfg.get("test_call"):
                    return xml(GOODBYE)
                state = {"call_sid": call, "synthetic": True, "phrases": [], "recordings": {}, "created": time.time()}
                cfg["test_call"] = call
                save(ROOT / "runtime.json", cfg)
                persist_call(call, state)
            elif cfg["mode"] == "armed" and time.time() < cfg["expires"] and cfg.get("calls", 0) < 3:
                state = {"call_sid": call, "synthetic": False, "speaker": "unconfirmed",
                         "phrases": phrases(), "recordings": {}, "created": time.time(),
                         "delete_local_after": time.time() + 7 * 86400}
                cfg["calls"] = cfg.get("calls", 0) + 1
                save(ROOT / "runtime.json", cfg)
                persist_call(call, state)
            else:
                return xml(GOODBYE)
            if state["synthetic"]:
                return xml(f'<Response><Record action="{cfg["origin"]}/test-done" maxLength="3" timeout="0" playBeep="false" trim="do-not-trim" finishOnKey="" recordingStatusCallback="{cfg["origin"]}/recording/0" recordingStatusCallbackEvent="completed absent"/></Response>')
            return xml(document(state["phrases"], 0, cfg["origin"]).replace('maxLength="12"', 'maxLength="10"'))

    @app.post("/test-done")
    def test_done():
        return xml("<Response><Hangup/></Response>")

    @app.post("/next/<int:i>")
    def next_round(i):
        call = request.form.get("CallSid", "")
        if not re.fullmatch(r"CA[0-9a-fA-F]{32}", call) or not 1 <= i <= 6:
            abort(400)
        with LOCK:
            path = ROOT / "calls" / f"{call}.json"
            if not path.exists():
                abort(404)
            state = read(path)
            if state["synthetic"]:
                abort(400)
            state["last_action_round"] = max(i, state.get("last_action_round", 0))
            persist_call(call, state)
        return xml(document(state["phrases"], i, runtime()["origin"]).replace('maxLength="12"', 'maxLength="10"'))

    @app.post("/recording/<int:i>")
    def recording(i):
        call = request.form.get("CallSid", "")
        sid = request.form.get("RecordingSid", "")
        if not re.fullmatch(r"CA[0-9a-fA-F]{32}", call) or not re.fullmatch(r"RE[0-9a-fA-F]{32}", sid) or not 0 <= i < 6:
            abort(400)
        with LOCK:
            path = ROOT / "calls" / f"{call}.json"
            if not path.exists():
                abort(404)
            if request.form.get("RecordingSource", "RecordVerb") != "RecordVerb":
                abort(400)
            if request.form.get("RecordingChannels", "1") != "1":
                abort(400)
            state = read(path)
            previous = state["recordings"].get(sid, {})
            state["recordings"][sid] = {**previous, "round": i,
                "status": request.form.get("RecordingStatus"),
                "duration": request.form.get("RecordingDuration"), "received": time.time()}
            persist_call(call, state)
        return "", 204

    return app


def export_pending():
    for path in (ROOT / "calls").glob("*.json"):
        state = read(path)
        for sid, recording in state["recordings"].items():
            if recording.get("status") != "completed" or recording.get("deleted_from_twilio"):
                continue
            audio, info = download(sid)
            if state["synthetic"]:
                denied = unauthenticated_denied(sid)
                save(ROOT / "privacy_test.json", {"authentication_enforced": denied, "at": time.time(), "synthetic": True})
            else:
                dest = ROOT / "audio" / f"{sid}.wav"
                if not dest.exists():
                    with dest.open("xb") as f:
                        f.write(audio)
                        f.flush()
                        os.fsync(f.fileno())
                assert hashlib.sha256(dest.read_bytes()).hexdigest() == info["sha256"]
            # Delete provider media only after successful verified export/privacy test.
            api("DELETE", f"/Recordings/{sid}.json")
            with LOCK:
                latest = read(path)
                latest["recordings"][sid].update(info)
                latest["recordings"][sid]["deleted_from_twilio"] = time.time()
                save(path, latest)


def worker():
    while True:
        try:
            export_pending()
            cfg = runtime()
            if time.time() >= cfg["expires"] and (ROOT / "active.json").exists():
                restore()
                cfg["mode"] = "closed"
                save(ROOT / "runtime.json", cfg)
                print("Four-hour collection window ended; routing restored.", flush=True)
        except Exception as e:
            save(ROOT / "last_worker_error.json", {"kind": type(e).__name__, "at": time.time()})
        time.sleep(8)


def start():
    (ROOT / "calls").mkdir(exist_ok=True, mode=0o700)
    (ROOT / "audio").mkdir(exist_ok=True, mode=0o700)
    n = read(ROOT / "snapshot.json")["number"]
    cfg = {"origin": "https://not-ready.invalid", "mode": "test", "number": n["phone_number"],
           "calls": 0, "expires": time.time() + 4 * 3600}
    save(ROOT / "runtime.json", cfg)
    threading.Thread(target=worker, daemon=True).start()
    serve(make_app(), host="127.0.0.1", port=8769, threads=6)


def activate():
    restore(check=True)
    cfg = runtime()
    if not cfg["origin"].startswith("https://") or cfg["origin"].endswith(".invalid"):
        raise RuntimeError("Tunnel origin not configured")
    n = read(ROOT / "snapshot.json")["number"]
    # This deployment currently uses a plain webhook. Never alter shared app/trunk.
    if n.get("voice_application_sid") or n.get("trunk_sid"):
        raise RuntimeError("Associated application/trunk needs explicit detachment implementation")
    diff = {"VoiceUrl": cfg["origin"] + "/voice", "VoiceMethod": "POST",
            "VoiceFallbackUrl": cfg["origin"] + "/failure", "VoiceFallbackMethod": "POST",
            "StatusCallback": ""}
    expected = projection(n)
    for k, v in FIELDS.items():
        if v in diff:
            expected[k] = diff[v]
    save(ROOT / "active.json", {"at": time.time(), "expected": expected})
    try:
        result = api("POST", f"/IncomingPhoneNumbers/{n['sid']}.json", diff)
        assert projection(result) == expected, "Activation readback mismatch"
        assert projection(api("GET", f"/IncomingPhoneNumbers/{n['sid']}.json")) == expected
    except Exception:
        # Own partial changes are safe to restore immediately within this transaction.
        api("POST", f"/IncomingPhoneNumbers/{n['sid']}.json", read(ROOT / "restore_payload.json"))
        (ROOT / "active.json").unlink(missing_ok=True)
        raise
    print("Temporary per-number webhook configured in synthetic-only test mode.")


def synthetic():
    n = read(ROOT / "snapshot.json")["number"]
    result = api("POST", "/Calls.json", {"To": n["phone_number"], "From": n["phone_number"],
        "Twiml": '<Response><Say voice="man">Synthetic audio privacy check.</Say><Pause length="5"/><Hangup/></Response>',
        "Timeout": "15", "TimeLimit": "20"})
    save(ROOT / "synthetic_outbound.json", {"sid": result["sid"], "at": time.time()})
    print("Synthetic self-call initiated; user calls remain blocked until privacy passes.")


def arm():
    result = read(ROOT / "privacy_test.json")
    assert result["authentication_enforced"], "Unauthenticated media was not rejected"
    assert time.time() - result["at"] < 900, "Privacy test stale"
    cfg = runtime()
    cfg["mode"] = "armed"
    save(ROOT / "runtime.json", cfg)
    print("READY: " + cfg["number"])
    print("Six phrases per call, three-call budget, automatic private WAV export and provider deletion.")


def status():
    cfg = runtime()
    summaries = []
    for p in (ROOT / "calls").glob("*.json"):
        state = read(p)
        summaries.append({"synthetic": state["synthetic"], "recordings": len(state["recordings"]),
                          "exported_deleted": sum(bool(v.get("deleted_from_twilio")) for v in state["recordings"].values())})
    print(json.dumps({"mode": cfg["mode"], "minutes_remaining": int((cfg["expires"] - time.time()) / 60),
                      "privacy": read(ROOT / "privacy_test.json") if (ROOT / "privacy_test.json").exists() else None,
                      "calls": summaries, "worker_error": (ROOT / "last_worker_error.json").exists()}))


def cleanup():
    removed = 0
    for path in (ROOT / "calls").glob("*.json"):
        state = read(path)
        if state.get("synthetic") or time.time() < state.get("delete_local_after", state["created"] + 7 * 86400):
            continue
        for sid in state["recordings"]:
            if re.fullmatch(r"RE[0-9a-fA-F]{32}", sid):
                (ROOT / "audio" / f"{sid}.wav").unlink(missing_ok=True)
        path.unlink()
        removed += 1
    print(f"Deleted {removed} expired local sample sets; no unrelated files touched.")


def tests():
    # Offline application checks use fake credentials and state, never bypass live validation.
    import tempfile
    global ROOT, ACCOUNT, TOKEN
    with tempfile.TemporaryDirectory() as d:
        ROOT = Path(d)
        (ROOT / "calls").mkdir()
        ACCOUNT, TOKEN = "AC" + "0" * 32, "test-only-token"
        origin = "https://collector.example.invalid"
        save(ROOT / "runtime.json", {"origin": origin, "mode": "armed", "expires": time.time() + 60, "calls": 0})
        client = make_app().test_client()
        assert client.post("/voice").status_code == 403
        call = "CA" + "1" * 32
        params = {"AccountSid": ACCOUNT, "CallSid": call}
        def post(path, params):
            sig = RequestValidator(TOKEN).compute_signature(origin + path, params)
            return client.post(path, data=params, headers={"X-Twilio-Signature": sig})
        first = post("/voice", params)
        assert first.status_code == 200 and b'maxLength="10"' in first.data
        assert post("/voice", params).data == first.data
        assert runtime()["calls"] == 1
        for i in range(1, 7):
            response = post(f"/next/{i}", params)
            assert response.status_code == 200
            if i == 6:
                assert b"Hangup" in response.data and b"Record" not in response.data
        cb = {**params, "RecordingSid": "RE" + "2" * 32, "RecordingStatus": "completed", "RecordingChannels": "1"}
        assert post("/recording/0", cb).status_code == 204
        assert post("/recording/0", cb).status_code == 204
        state = read(ROOT / "calls" / f"{call}.json")
        assert len(state["recordings"]) == 1
        assert "RecordingUrl" not in json.dumps(state)
        assert post("/next/7", params).status_code == 400
    print("Offline signature, retry, six-round, callback and metadata tests passed.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=["snapshot", "restore-check", "restore", "serve", "activate", "synthetic", "arm", "status", "test", "export", "cleanup"])
    args = parser.parse_args()
    try:
        {"snapshot": snapshot, "restore-check": lambda: restore(True), "restore": restore,
         "serve": start, "activate": activate, "synthetic": synthetic,
         "arm": arm, "status": status, "test": tests, "export": export_pending,
         "cleanup": cleanup}[args.command]()
    except Exception as e:
        # Provider exceptions and HTTP tracebacks may embed payloads or capability URLs.
        print(f"Operation failed: {type(e).__name__}: {str(e) if isinstance(e, (RuntimeError, AssertionError)) else 'private details suppressed'}", file=sys.stderr)
        sys.exit(1)

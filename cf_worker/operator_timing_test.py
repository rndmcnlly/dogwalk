# /// script
# requires-python = ">=3.12"
# dependencies = ["playwright"]
# ///
"""Browser regression test with synthetic data only. Requires local Google Chrome.

    uv run --script cf_worker/operator_timing_test.py

No service, Sandbox, credentials, or production transcript is used.
"""

import json
import subprocess
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from playwright.sync_api import sync_playwright


ROOT = Path(__file__).resolve().parent.parent
ORIGIN = "https://dogwalk-operator.test"
T0 = 1_790_000_000


def main():
    html = subprocess.check_output([
        "node", "--input-type=module", "-e",
        "import fs from 'node:fs'; const s=fs.readFileSync('cf_worker/src/operator_timing.ts','utf8');"
        "const m=await import('data:text/javascript;base64,'+Buffer.from(s).toString('base64'));"
        "process.stdout.write(m.OPERATOR_TIMING_HTML);",
    ], cwd=ROOT, text=True)
    call = {"call_sid": "CA_SYNTHETIC", "started_at": T0, "status": "in-progress", "ended_at": None,
            "duration_seconds": None, "caller": "******0000"}
    other = {**call, "call_sid": "CA_OTHER", "status": "completed", "ended_at": T0 + 60}
    rows = []

    def add(kind, at, **detail):
        rows.append({"id": len(rows) + 1, "ts": T0 + at // 1000, "source": "voice", "event": kind,
                     "detail": {"ts_ms": T0 * 1000 + at, "live_session_id": "live_test", **detail}})

    add("voice.live.attached", 1, managed_sessions=[{
        "alias": '<img src=x onerror="window.__injected=1">', "acp_session_id": "ses_test", "turn_state": "ready",
    }])
    add("voice.timing", 100, event="model.started", request_id="req", clock_id="clock")
    add("voice.timing", 1100, event="model.completed", request_id="req", clock_id="clock", ms=1000)
    add("acp.turn.started", 1200, acp_session_id="ses_test", alias="Example", started_ms=T0 * 1000 + 1000)
    for index in range(2):
        for status, delta in (("in_progress", 0), ("completed", 50)):
            add("voice.timing", 1300 + index * 100 + delta, event="acp.received", method="session/update",
                session_id="ses_test", update="tool_call_update", tool_call_id=f"tool{index}",
                tool_kind="execute", tool_status=status, clock_id="clock")
    for index in range(520):
        add("voice.timing", 1600 + index * 200, event="live.received", type="session.output_audio.delta",
            chars=12800, start_ms=index * 200, end_ms=(index + 1) * 200, clock_id="clock")
    add("acp.turn.stopped", 106000, acp_session_id="ses_test", alias="Example", ms=104000, ended_ms=T0 * 1000 + 105000)
    requests = {"calls": 0, "events": 0, "unauthorized": False, "after": [], "server_ms": T0 * 1000 + 106000}
    errors = []

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(channel="chrome", headless=True)
        page = browser.new_page(viewport={"width": 1550, "height": 1000})
        page.on("pageerror", lambda error: errors.append(str(error)))

        def route(request):
            url = urlparse(request.request.url)
            if url.path == "/admin/timing":
                request.fulfill(body=html, content_type="text/html")
            elif url.path == "/admin/api/timing/calls":
                requests["calls"] += 1
                request.fulfill(json={"calls": [call, other], "server_ms": T0 * 1000 + 106000})
            elif url.path == "/admin/api/timing":
                requests["events"] += 1
                if requests["unauthorized"]:
                    request.fulfill(status=401, body="unauthorized")
                    return
                params = parse_qs(url.query)
                requests["after"].append(int(params["after"][0]))
                selected = other if params["call_sid"][0] == other["call_sid"] else call
                available = [] if selected == other else [row for row in rows if row["id"] > int(params["after"][0])]
                batch = available[:500]
                request.fulfill(json={"call": selected, "events": batch,
                    "cursor": batch[-1]["id"] if batch else int(params["after"][0]),
                    "has_more": len(available) > 500, "server_ms": requests["server_ms"]})
            else:
                request.fulfill(status=404)

        page.route(ORIGIN + "/**", route)
        page.goto(ORIGIN + "/admin/timing")
        page.wait_for_function("n => document.querySelector('#counts').textContent.startsWith(n + ' loaded')", arg=len(rows))
        assert page.locator("#calls").input_value() == call["call_sid"]
        assert requests["events"] >= 2, "did not drain multiple insertion-cursor pages"
        assert page.locator("#heads .lane").count() <= 6, "one lane per tool invocation would overflow the diagram"
        assert page.locator("#plot text").filter(has_text="1000ms").count(), "model duration not drawn"
        assert page.locator("#plot text").filter(has_text="Prompt Turn 104000ms").count(), "Prompt Turn span used audit-write time instead of lifecycle time"
        assert page.evaluate("window.__injected === undefined"), "untrusted metadata ran as HTML"
        assert page.locator("#heads img").count() == 0
        assert int(page.locator("#plot").get_attribute("height")) < 6000, "audio packets overwhelm the default view"
        assert "not observed" in page.locator("#nested").inner_text()
        assert "Twilio/" not in page.locator("#heads").inner_text(), "unobserved provider lanes appeared"

        # Backfill is delivered only after the first cursor pages have drained.
        initial_cursor = len(rows)
        requests["server_ms"] = T0 * 1000 + 108000

        def provider(kind, occurred, leg="pstn", **metadata):
            add("provider.twilio." + kind, 108000, provider="twilio",
                provider_event_key=f"fingerprint-{len(rows)}", provider_call_sid="CA_PSTN" if leg == "pstn" else "CA_SIP",
                root_call_sid="CA_PSTN", parent_call_sid=None if leg == "pstn" else "CA_PSTN",
                leg=leg, clock_id="twilio", occurred_at_ms=None if occurred is None else T0 * 1000 + occurred,
                received_at_ms=T0 * 1000 + 108000, timestamp_precision_ms=1000 if occurred is not None else None,
                event_time_source="synthetic provider timestamp" if occurred is not None else "unknown", **metadata)
            return len(rows)

        early_id = provider("call.created", -5000)
        provider("call.start_time", -4000)
        negative_id = provider("callback.answered", 110000, "sip")
        unknown_id = provider("webhook.exchange", None, "sip", request_path="/voice")
        # Same identifiers in different clocks must not manufacture spans.
        for kind, identity in (("model", {"request_id": "cross-clock"}),
                               ("tool", {"tool_call_id": "cross-clock"}),
                               ("acp.turn", {"acp_session_id": "ses_test"})):
            add("voice.timing", 107000, event=kind + ".started", clock_id="clock-a", **identity)
            add("voice.timing", 107500, event=kind + (".stopped" if kind == "acp.turn" else ".completed"),
                clock_id="clock-b", **identity)
        add("voice.timing", 107600, event="acp.sent", rpc_id="cross-clock", method="session/prompt", clock_id="clock-a")
        add("voice.timing", 107700, event="acp.received", rpc_id="cross-clock", clock_id="clock-b")
        page.wait_for_function("n => document.querySelector('#counts').textContent.startsWith(n + ' loaded')", arg=len(rows))
        assert initial_cursor in requests["after"], "backfill did not use the retained insertion cursor"
        assert "Twilio/PSTN" in page.locator("#heads").inner_text()
        assert "Twilio/SIP" in page.locator("#heads").inner_text()
        early = page.locator(f'#plot .event[aria-label^="#{early_id} "]')
        assert "-5000ms" in early.text_content(), "backfill shifted the fixed call-start anchor"
        assert "1s precision" in early.text_content()
        assert "received +113.0s later" in early.text_content()
        assert page.locator("#plot .event[aria-label^='#']").first.get_attribute("aria-label").startswith(f"#{early_id} "), "late event was not inserted before Worker events"
        assert "last event age 0." in page.locator("#lag").inner_text(), "status uses occurrence instead of receipt time"
        early.focus()
        early.press("Enter")
        provenance = json.loads(page.locator("#inspector").inner_text())
        assert provenance["source"] == "voice" and provenance["clock_id"] == "twilio"
        assert provenance["occurred_at_ms"] == T0 * 1000 - 5000
        assert provenance["received_at_ms"] == T0 * 1000 + 108000
        assert "not millisecond accuracy" in provenance["precision"]
        assert provenance["event_time_source"] == "synthetic provider timestamp"
        assert "not network latency" in provenance["delivery_age_meaning"]
        assert "Related same leg" in page.locator("#related").inner_text()
        assert "not causality" in page.locator("#related").inner_text()
        negative = page.locator(f'#plot .event[aria-label^="#{negative_id} "]')
        assert "negative clock offset" in negative.get_attribute("aria-label")
        negative.focus()
        negative.press("Enter")
        assert "clock-skew-inclusive" in page.locator("#inspector").inner_text()
        assert "Observed parent-leg metadata: CA_PSTN" in page.locator("#related").inner_text()
        unknown = page.locator(f'#plot .event[aria-label^="#{unknown_id} "]')
        unknown.focus()
        unknown.press("Enter")
        provenance = json.loads(page.locator("#inspector").inner_text())
        assert provenance["occurred_at_ms"] is None
        assert provenance["positioned_by"] == "receipt time (occurrence unknown)"
        assert provenance["precision"] == "provider precision unknown"
        assert page.locator("#plot .event[aria-label*='matched #']").count() == 4, "cross-clock spans were joined"
        assert page.locator("#plot .event[aria-label='matched RPC response']").count() == 0
        assert page.locator("#plot .event[aria-label^='Observed live']").count() == 0, "provider observations invented live arrows"
        page.locator("#raw").check()
        page.locator("#plot .event").first.focus()
        page.locator("#plot .event").first.press("Enter")
        assert "detail" in page.locator("#inspector").inner_text()
        page.locator("#raw").uncheck()
        assert page.locator("#copy").is_disabled()
        page.locator("#pause").check()
        before = requests["events"]
        page.wait_for_timeout(1200)
        assert requests["events"] == before, "paused page keeps fetching"
        page.locator("#pause").uncheck()
        page.locator("#calls").select_option(other["call_sid"])
        page.wait_for_function("document.querySelector('#counts').textContent.startsWith('0 loaded')")
        assert not page.locator("#follow").is_checked()
        assert page.locator("#plot .event").count() == 0, "call switch retained another caller's observations"
        page.set_viewport_size({"width": 390, "height": 844})
        assert not page.evaluate("document.documentElement.scrollWidth > window.innerWidth"), "mobile outer layout overflows"
        requests["unauthorized"] = True
        page.locator("#calls").select_option(call["call_sid"])
        page.wait_for_function("document.querySelector('#status').textContent.includes('Authentication required')")
        before = requests["events"]
        page.wait_for_timeout(1200)
        assert requests["events"] == before, "unauthorized page keeps requesting private telemetry"
        assert not errors, errors
        browser.close()
    print("operator timing browser checks passed (pagination, provider backfill, provenance, clock scope, spans, noise, XSS, pause, selection, auth)")


if __name__ == "__main__":
    main()

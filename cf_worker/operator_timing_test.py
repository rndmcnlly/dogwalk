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
        "alias": '<img src=x onerror="window.__injected=1">', "agent_session_id": "ses_test", "turn_state": "ready",
    }])
    add("voice.timing", 100, event="model.started", request_id="req", clock_id="clock")
    add("voice.timing", 1100, event="model.completed", request_id="req", clock_id="clock", ms=1000)
    add("agent.turn.started", 1200, agent_session_id="ses_test", alias="Example", started_ms=T0 * 1000 + 1000)
    for index in range(2):
        for status, delta in (("in_progress", 0), ("completed" if index == 0 else "interrupted", 50)):
            add("voice.timing", 1300 + index * 100 + delta, event="agent.received",
                session_id="ses_test", agent_event="session.tool.called", tool_call_id=f"tool{index}",
                tool_kind="execute", tool_status=status, clock_id="clock")
    for index in range(520):
        add("voice.timing", 1600 + index * 200, event="live.received", type="session.output_audio.delta",
            chars=12800, start_ms=index * 200, end_ms=(index + 1) * 200, clock_id="clock")
    add("agent.turn.stopped", 106000, agent_session_id="ses_test", alias="Example", ms=104000, ended_ms=T0 * 1000 + 105000,
        thoughts={"chunks": 2, "chars": 42, "tail": "PRIVATE_REASONING_SENTINEL"})
    for text in ("CONTENTSECRET caller ", '<img src=x onerror="window.__contentInjected=1">'):
        add("voice.timing", 106100, event="live.received", type="session.input_audio_transcript.delta",
            content_kind="caller", content_text=text, chars=len(text))
    for text in ("Agent coherent ", "progress snippet"):
        add("voice.timing", 106200, event="agent.received", agent_event="session.text.delta", session_id="ses_test",
            content_kind="agent_report", content_text=text, chars=len(text))
    add("session.beat", 106300, text="CONTENTSECRET hidden beat", label="CONTENTSECRET hidden label",
        nested={"prompt": "CONTENTSECRET hidden prompt", "last_words": "CONTENTSECRET last words"})
    content_id = len(rows) - 4
    requests = {"calls": 0, "events": 0, "content": 0, "content_error": False, "unauthorized": False, "after": [], "server_ms": T0 * 1000 + 106000}
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
            elif url.path == "/admin/api/timing/content":
                requests["content"] += 1
                if requests["content_error"]:
                    request.fulfill(status=503, body="temporarily unavailable")
                    return
                entries = [{"id": "media", "kind": "voice", "text": "CONTENTSECRET saved media",
                    "source": "saved_transcript", "observed_at_ms": None, "start_ms": 120, "end_ms": 900,
                    "precision": "provider_media_offset", "truncated": False},
                    {"id": "history", "kind": "brief", "text": "CONTENTSECRET old brief",
                    "source": "orchestrator_history", "observed_at_ms": None,
                    "precision": "unknown", "truncated": True}]
                request.fulfill(json={"entries": entries if parse_qs(url.query)["call_sid"][0] == call["call_sid"] else [],
                    "note": "Historical content unavailable for some messages."})
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
        assert page.locator('.domainrow .domain').all_text_contents() == ['OpenAI', 'Cloudflare', 'Daytona']
        assert page.locator('#heads .lane').evaluate_all("nodes => nodes.map(n=>n.dataset.lane)")[:4] == ['voice', 'reasoning', 'orchestrator', 'transport']
        assert page.locator('#plot rect[data-domain="openai"]').count() == 1
        assert page.locator('#plot rect[data-domain="cloudflare"]').count() == 1
        assert page.locator("#plot text").filter(has_text="1000ms").count(), "model duration not drawn"
        assert page.locator("#plot text").filter(has_text="Agent tool execute").count() == 2, "an interrupted Agent tool left its span open"
        assert page.locator("#plot text").filter(has_text="Prompt Turn 104000ms").count(), "Prompt Turn span used audit-write time instead of lifecycle time"
        assert page.evaluate("window.__injected === undefined"), "untrusted metadata ran as HTML"
        assert page.locator("#heads img").count() == 0
        assert int(page.locator("#plot").get_attribute("height")) < 6000, "audio packets overwhelm the default view"
        assert "not observed" in page.locator("#nested").inner_text()
        assert "Twilio/" not in page.locator("#heads").inner_text(), "unobserved provider lanes appeared"
        assert requests["content"] == 0, "content endpoint fetched without opt-in"
        page.locator("#raw").check()
        assert requests["content"] == 0, "raw metadata implicitly enabled content"
        page.locator(f'#plot .event[aria-label^="#{content_id} "]').click()
        assert "content_text" not in page.locator("#inspector").inner_text()
        assert "CONTENTSECRET" not in page.locator("body").inner_text(), "content leaked into metadata-only DOM"
        with page.expect_download() as download_info:
            page.locator("#download").click()
        assert "CONTENTSECRET" not in Path(download_info.value.path()).read_text(), "content leaked into metadata-only export"
        assert "PRIVATE_REASONING_SENTINEL" not in Path(download_info.value.path()).read_text(), "legacy reasoning leaked into metadata-only export"
        page.locator("#raw").uncheck()
        page.locator("#content").check()
        page.wait_for_function("document.querySelector('#savedContent').textContent.includes('CONTENTSECRET saved media')")
        assert not page.locator("#raw").is_checked(), "content implicitly enabled raw events"
        assert page.locator("#observedContent .contentitem").count() == 2, "fragments were not aggregated"
        assert "Agent coherent progress snippet" in page.locator("#observedContent").inner_text()
        assert "CONTENTSECRET caller <img" in page.locator("#observedContent").inner_text()
        assert page.locator("#contentPane img").count() == 0
        assert page.evaluate("window.__contentInjected === undefined"), "content ran as HTML"
        assert "provider media offset 120–900ms · no Unix event position" in page.locator("#savedContent").text_content()
        assert "historical timing unknown · no event position" in page.locator("#savedContent").text_content()
        page.locator("#observedContent .contentitem").first.click()
        assert "CONTENTSECRET caller" in page.locator("#inspector").inner_text()
        page.locator("#raw").check()
        assert "CONTENTSECRET caller" in page.locator("#observedContent").inner_text(), "raw disabled human view"
        with page.expect_download() as download_info:
            page.locator("#download").click()
        assert "CONTENTSECRET caller" in Path(download_info.value.path()).read_text()
        assert "PRIVATE_REASONING_SENTINEL" not in Path(download_info.value.path()).read_text(), "content mode exposed private reasoning"
        page.locator("#content").uncheck()
        assert not page.locator("#observedContent").text_content()
        assert not page.locator("#savedContent").text_content()
        assert "CONTENTSECRET" not in page.locator("#inspector").inner_text()
        with page.expect_download() as download_info:
            page.locator("#download").click()
        assert "CONTENTSECRET" not in Path(download_info.value.path()).read_text()
        page.locator("#raw").uncheck()

        # A slow content response must not block the timing cursor or populate another call.
        page.evaluate("""() => {
          const original=window.fetch;
          window.fetch=(url,options)=>String(url).includes('/timing/content?')
            ? new Promise(resolve=>window.__lateContent=()=>resolve(new Response(JSON.stringify({entries:[{
              id:'late',kind:'caller',text:'WRONG CALL CONTENT',precision:'unknown',observed_at_ms:null
            }]}),{status:200,headers:{'Content-Type':'application/json'}}))) : original(url,options);
          window.__restoreFetch=()=>window.fetch=original;
        }""")
        page.locator("#content").check()
        page.wait_for_function("typeof window.__lateContent === 'function'")
        before_events = requests["events"]
        page.wait_for_timeout(1200)
        assert requests["events"] > before_events, "slow content blocked independent timing polling"
        page.locator("#calls").select_option(other["call_sid"])
        page.evaluate("window.__restoreFetch(); window.__lateContent()")
        page.wait_for_function("document.querySelector('#contentStatus').textContent.includes('Historical content unavailable')")
        assert "WRONG CALL CONTENT" not in page.locator("body").inner_text()
        page.locator("#calls").select_option(call["call_sid"])
        page.wait_for_function("n => document.querySelector('#counts').textContent.startsWith(n + ' loaded')", arg=len(rows))
        requests["content_error"] = True
        page.locator("#refreshContent").click()
        page.wait_for_function("document.querySelector('#contentStatus').textContent.includes('503')")
        requests["content_error"] = False
        page.locator("#refreshContent").click()
        page.wait_for_function("document.querySelector('#contentStatus').textContent.includes('Historical content unavailable')")
        page.locator("#content").uncheck()
        before_content = requests["content"]
        page.wait_for_timeout(200)
        assert requests["content"] == before_content

        # Backfill is delivered only after the first cursor pages have drained.
        live_arrows = page.locator("#plot .event[aria-label^='Observed live']").count()
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
                               ("agent.turn", {"agent_session_id": "ses_test"})):
            add("voice.timing", 107000, event=kind + ".started", clock_id="clock-a", **identity)
            add("voice.timing", 107500, event=kind + (".stopped" if kind == "agent.turn" else ".completed"),
                clock_id="clock-b", **identity)
        add("voice.timing", 107600, event="agent.sent", agent_request_id="cross-clock", method="POST", clock_id="clock-a")
        add("voice.timing", 107700, event="agent.received", agent_request_id="cross-clock", clock_id="clock-b")
        page.wait_for_function("n => document.querySelector('#counts').textContent.startsWith(n + ' loaded')", arg=len(rows))
        assert initial_cursor in requests["after"], "backfill did not use the retained insertion cursor"
        assert "Twilio/PSTN" in page.locator("#heads").inner_text()
        assert "Twilio/SIP" in page.locator("#heads").inner_text()
        assert page.locator('#heads .lane').evaluate_all("nodes => nodes.map(n=>n.dataset.lane)")[:2] == ['twilio:pstn', 'twilio:sip']
        assert page.locator('.domainrow .domain').all_text_contents() == ['Twilio', 'OpenAI', 'Cloudflare', 'Daytona']
        assert page.locator('#plot rect[data-domain="twilio"]').count() == 1
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
        assert page.locator("#plot .event[aria-label='matched request response']").count() == 0
        assert page.locator("#plot .event[aria-label^='Observed live']").count() == live_arrows, "provider observations invented live arrows"

        # Adaptive streams cross wall-clock bucket boundaries without treating a backchannel as a flush.
        def delta(at, text, role="caller", session=None, **metadata):
            add("voice.timing", at, event="agent.received" if session else "live.received",
                type="session.input_audio_transcript.delta" if role == "caller" else "session.output_audio_transcript.delta",
                agent_event="session.text.delta" if session else None, agent_session_id=session,
                content_kind="agent_report" if session else role, content_text=text, chars=len(text), clock_id="stream-clock", **metadata)
            return len(rows)

        continuous_ids = [delta(114800, "BURST caller begins ")]
        delta(115000, "BURST backchannel", role="voice")
        continuous_ids.append(delta(115200, "across five seconds "))
        continuous_ids.append(delta(115650, "still caller"))
        delta(118000, "BURST caller after long gap")
        delta(118100, "BURST agent A ", role="agent_report", session="ses_test")
        delta(118200, "BURST agent B ", role="agent_report", session="ses_other")
        add("agent.turn.stopped", 118250, agent_session_id="ses_test")
        delta(118300, "BURST agent A next", role="agent_report", session="ses_test")
        delta(118350, "continues B", role="agent_report", session="ses_other")
        add("voice.live.closed", 118400)
        delta(118450, "BURST caller after close")
        delta(118475, " after voice closes", role="agent_report", session="ses_other")
        add("voice.live.detached", 118500)
        delta(118550, "BURST caller after detach")
        add("agent.turn.stopped", 118600, agent_session_id="ses_other", live_session_id=None)
        # Matching explicit provider offsets fuse batched delivery, without inventing Unix media positions.
        delta(120000, "BURST media begins ", role="voice", media_clock_id="provider-a", start_ms=0, end_ms=200)
        delta(124000, "batched continuation", role="voice", media_clock_id="provider-a", start_ms=250, end_ms=400)
        delta(124100, "BURST other media clock", role="voice", media_clock_id="provider-b", start_ms=10000, end_ms=10200)
        requests["server_ms"] = T0 * 1000 + 124200
        page.wait_for_function("n => document.querySelector('#counts').textContent.startsWith(n + ' loaded')", arg=len(rows))
        assert "5s bucket" not in page.locator("#counts").inner_text()
        page.locator("#content").check()

        def burst(text):
            item = page.locator("#observedContent .contentitem").filter(has=page.get_by_text(text.strip(), exact=True))
            assert item.count() == 1, f"stream fragments not fused correctly: {text}"
            item.click()
            return json.loads(page.locator("#inspector").inner_text())

        caller_burst = burst("BURST caller begins across five seconds still caller")["burst"]
        assert caller_burst["count"] == 3
        assert [o["id"] for o in caller_burst["observations"]] == continuous_ids
        assert caller_burst["first_received_at_ms"] == T0 * 1000 + 114800
        assert caller_burst["last_received_at_ms"] == T0 * 1000 + 115650
        assert caller_burst["end_reason"] == "adaptive inactivity gap"
        assert 750 < caller_burst["threshold_ms"] <= 2000, "cadence did not adapt above the minimum"
        assert burst("BURST backchannel")["burst"]["count"] == 1
        assert "explicit voice.live.closed" in burst("BURST caller after long gap")["burst"]["end_reason"]
        assert "explicit agent.turn.stopped" in burst("BURST agent A ")["burst"]["end_reason"]
        assert burst("BURST agent A next")["burst"]["count"] == 1
        agent_b=burst("BURST agent B continues B after voice closes")["burst"]
        assert agent_b["count"] == 3, "voice closure split an independent Agent stream"
        assert "explicit agent.turn.stopped" in agent_b["end_reason"], "Agent completion after voice detach lost its boundary"
        assert "explicit voice.live.detached" in burst("BURST caller after close")["burst"]["end_reason"]
        assert burst("BURST caller after detach")["burst"]["state"] == "idle"
        media = burst("BURST media begins batched continuation")
        assert media["burst"]["gap_basis"] == "provider_media_offset"
        assert media["burst"]["last_media"]["end_ms"] == 400
        assert media["observed_at_ms"] == T0 * 1000 + 120000
        assert burst("BURST other media clock")["burst"]["state"] == "live tail"
        # Raw mode leaves the immutable original delta rows individually visible and exportable.
        page.locator("#raw").check()
        for identity in continuous_ids:
            mark = page.locator(f'#plot .event[aria-label^="#{identity} "]')
            assert mark.count() == 1
            mark.click()
            original = json.loads(page.locator("#inspector").inner_text())
            assert original["id"] == identity and original["detail"]["content_text"]
            assert "burst" not in original
        assert "BURST caller begins across five seconds still caller" in page.locator("#observedContent").inner_text()
        page.locator("#content").uncheck()
        page.locator("#raw").check()
        page.locator("#plot .event").first.focus()
        page.locator("#plot .event").first.press("Enter")
        assert "detail" in page.locator("#inspector").inner_text()
        page.locator("#raw").uncheck()
        assert page.locator("#copy").is_disabled()
        page.locator("#content").check()
        page.wait_for_function("document.querySelector('#savedContent').textContent.includes('CONTENTSECRET saved media')")
        before_content = requests["content"]
        page.wait_for_timeout(5200)
        assert requests["content"] > before_content, "live content was not refreshed"
        page.locator("#pause").check()
        before = requests["events"]
        before_content = requests["content"]
        page.wait_for_timeout(1200)
        assert requests["events"] == before, "paused page keeps fetching"
        assert requests["content"] == before_content, "paused page fetches content"
        page.locator("#pause").uncheck()
        page.evaluate("Object.defineProperty(document,'hidden',{configurable:true,value:true}); document.dispatchEvent(new Event('visibilitychange'))")
        before = (requests["events"], requests["content"])
        page.wait_for_timeout(1200)
        assert before == (requests["events"], requests["content"]), "hidden tab keeps fetching"
        page.evaluate("Object.defineProperty(document,'hidden',{configurable:true,value:false}); document.dispatchEvent(new Event('visibilitychange'))")
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
    print("operator timing browser checks passed (domain bands, clocks, pagination, spans, lazy content, adaptive bursts, stream isolation, explicit ends, media gap clocks, immutable raw deltas, XSS, export hiding, stale responses, retry, live refresh, pause, hidden tab, auth)")


if __name__ == "__main__":
    main()

# /// script
# requires-python = ">=3.12"
# dependencies = ["playwright"]
# ///
"""Synthetic browser regression for the read-only invitation graph.

    uv run --script cf_worker/admin_test.py

Requires local Google Chrome. No services or production credentials are used.
"""

import copy
import json
import subprocess
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from playwright.sync_api import sync_playwright


ROOT = Path(__file__).resolve().parent.parent
ORIGIN = "https://dogwalk-admin.test"
T0 = 1_790_000_000


def main():
    html = subprocess.check_output([
        "node", "--input-type=module", "-e",
        "import fs from 'node:fs'; const s=fs.readFileSync('cf_worker/src/admin.ts','utf8');"
        "const m=await import('data:text/javascript;base64,'+Buffer.from(s).toString('base64'));"
        "process.stdout.write(m.ADMIN_HTML);",
    ], cwd=ROOT, text=True)
    # Users 1 and 2 predate Handles (labelled by Home), User 3 has one, and User 4 was reaped.
    # Users 1 and 2 have deliberately identical masked suffixes: navigation must use identity,
    # not labels.
    phones = ["+15555550101", "+15556660101", "+15557770101"]
    handles = [None, None, "amber falcon", None]
    registrations = [
        {"diagnostic_id": f"user-{i + 1}", "user_id": i + 1, "handle": handles[i],
         "phone_number": phones[i] if i < 3 else None, "home_used_at": T0 + i if i < 3 else None,
         "registered_at": T0 + i, "last_seen_at": T0 + i, "reaped_at": T0 + 50 if i == 3 else None,
         "voice_enrolled": 1 if i == 2 else 0}
        for i in range(4)
    ]
    invitations = []
    for i in range(3):
        redeemed = i < 2
        invitations.append({
            "diagnostic_id": f"invitation-{i + 1}", "code_words": f"synthetic invitation {i}",
            "inviter_id": f"user-{i + 1}", "inviter_handle": handles[i],
            "invitee_id": f"user-{i + 2}" if redeemed else None,
            "invitee_handle": handles[i + 1] if redeemed else None,
            "invitee_phone": phones[i + 1] if redeemed else None,
            "recipient_phone": None if redeemed else "+15558880101",
            "recipient_confirmed_at": None if redeemed else T0,
            "status": "redeemed" if redeemed else "revoked",
            "minted_at": T0, "expires_at": T0 + 86400,
            "registered_at": T0 + i if redeemed else None,
            "redeemed_at": T0 + i if redeemed else None,
            "hidden_at": T0 + 10, "revoked_at": None if redeemed else T0 + 10,
        })
    # External text is rendered as text, never HTML.
    invitations[0]["code_words"] = '<img src=x onerror="window.__injected=1">'
    errors = []
    requests = []
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(channel="chrome", headless=True)
        page = browser.new_page(viewport={"width": 1250, "height": 950})
        page.on("pageerror", lambda error: errors.append(str(error)))

        def route(request):
            url = urlparse(request.request.url)
            if url.path == "/admin":
                request.fulfill(body=html, content_type="text/html")
            elif url.path == "/admin/api/events":
                verbose = parse_qs(url.query).get("verbose") == ["1"]
                requests.append(verbose)
                data = {"verbose": verbose, "generated_at": T0 + 60,
                        "registrations": copy.deepcopy(registrations),
                        "invitations": copy.deepcopy(invitations), "live_calls": [], "audit": []}
                if not verbose:
                    for r in data["registrations"]:
                        if r["phone_number"]:
                            r["phone_number"] = "********0101"
                    for v in data["invitations"]:
                        del v["code_words"]
                        for key in ("invitee_phone", "recipient_phone"):
                            if v[key]:
                                v[key] = "********0101"
                request.fulfill(body="event: state\ndata: " + json.dumps(data) + "\n\n",
                                content_type="text/event-stream")
            else:
                request.abort()

        page.route(ORIGIN + "/**", route)
        page.goto(ORIGIN + "/admin")
        page.get_by_text("Invitations (including hidden)", exact=True).wait_for()
        assert not any(phone in page.locator("body").inner_text() for phone in phones)
        # Follow the same hidden edge across a privacy-mode change.
        page.locator("#tree button").filter(has_text="not registered").click()
        detail = page.locator("#detail")
        assert "no registered invitee" in detail.inner_text().lower(), (detail.inner_text(), errors)
        assert "hidden (enable verbose)" in detail.inner_text()
        page.locator("#verbose").check()
        # Handles are not masked, so wait for something only the verbose lens shows.
        detail.get_by_text("+15558880101", exact=True).wait_for()
        page.get_by_role("button", name="Inviter: amber falcon", exact=True).click()
        assert "amber falcon" in detail.locator(".dhead").inner_text()
        assert "voice enrolled\tyes" in detail.inner_text().lower(), detail.inner_text()
        assert phones[2] in detail.inner_text()
        detail.get_by_role("button", name="Invited by " + phones[1], exact=True).click()
        detail.get_by_role("button", name="Inviter: " + phones[1], exact=True).click()
        detail.get_by_role("button", name="Invited by " + phones[0], exact=True).click()
        assert "Registered invitee: " + phones[1] in detail.inner_text()
        assert invitations[0]["code_words"] in detail.inner_text()
        assert page.evaluate("window.__injected") is None
        assert detail.locator("img").count() == 0
        assert page.locator("#tree button").filter(has_text="reaped").count() == 1
        assert page.locator("#tree button").filter(has_text=phones[0] + " \u2192 " + phones[1]).count() == 1
        assert page.locator("#tree button").filter(has_text=phones[1] + " \u2192 amber falcon").count() == 1
        # Masked labels collide, but links still reach the correct generation.
        page.locator("#verbose").uncheck()
        detail.get_by_text("hidden (enable verbose)", exact=True).wait_for()
        detail.get_by_role("button", name="Registered invitee: ********0101", exact=True).click()
        assert detail.get_by_role("button", name="Invited by ********0101", exact=True).count() == 1
        assert detail.get_by_role("button", name="amber falcon / redeemed / hidden", exact=True).count() == 1
        assert not any(phone in page.locator("body").inner_text() for phone in phones)
        assert requests[:3] == [False, True, False], requests
        assert not errors, errors
        browser.close()
    print("admin invitation graph browser checks passed")


if __name__ == "__main__":
    main()

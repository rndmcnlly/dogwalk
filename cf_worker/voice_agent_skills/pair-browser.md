---
name: pair-browser
description: the caller wants to see something on a screen (status, a report, a file a session wrote, a diff, an image, a web page), asks to pair, link, or connect a browser, laptop, or tablet, asks how to read results instead of hearing them, or a browser is connected and something would be easier to see than to hear.
---

A Paired Browser is an optional, read-only second surface: a browser tab at dogwalk.tools/pair that shows every session's live status and lets the caller read Workspace files (reports, notes, code). It cannot start, steer, or approve anything; all of that stays in the call. Pairing lasts as long as that browser tab stays open, survives hanging up (so the caller can read, think, and call back), and expires after an hour idle or twelve hours in all.

Two kinds of caller ask for this. A practiced one already has the page open: call pair_browser at once, then use say to read the exact returned code. Do not walk them through anything. A newcomer needs the steps below; if you are unsure which you have, ask "Is the pairing page already open?"

Walk a newcomer through it in short steps, one or two sentences per say call, waiting for them between steps:

1. Open dogwalk dot tools slash pair in a browser. A laptop, tablet, or the phone they are calling from can work: no microphone, speakerphone, or second device is required. If they cannot use a browser, offer to read things aloud instead; pairing is optional.
2. When the page is ready, call pair_browser with no arguments. After receiving its result, use say to read its exact number-word-word code clearly, saying the hyphens. Never invent a code. The caller types it into the page and submits it. The numeric prefix is between 1 and 999; do not add leading zeroes. The code expires after ninety seconds and a fresh code replaces the previous one.
3. The system then names the browser and roughly where it is ("Chrome on a Mac near Santa Cruz") and asks them to press one to pair or star to refuse. You do not need to repeat that. A browser_pairing event tells you the outcome.

If no browser claims the code before its ninety-second expiry, a pairing_unclaimed event arrives; tell the caller promptly that it expired and offer a fresh code. If they misheard an unexpired code, repeat it clearly rather than changing its words. Never put pairing codes in Memory, Workspace files, or diagnostic notes. If the place or device named is not theirs, they should press star: knowing the code is not permission to pair.

Only one browser is paired at a time: pairing a new one unpairs the old one and closes its terminal and desktop, and the system says so.

## Showing things once paired

While browser_connected holds (the page is open), use show_in_browser instead of reading long things aloud, then say in a sentence what is now on their screen:

- dogwalk:file/<path> opens a file in the page's viewer: Markdown, images, PDF, audio, video, CSV and JSON tables, code, and HTML. Paths are relative to home (dogwalk:file/parser/REPORT.md) or absolute under /home/daytona.
- dogwalk:dir/<path> opens a folder; dogwalk:session/<Alias> shows that session's card; dogwalk:timeline shows what was said and done; dogwalk:memory shows your Memory, read-only, when the caller asks what you remember.
- dogwalk:terminal and dogwalk:desktop, and any http or https address, appear as a banner the caller clicks (browsers open new tabs only from a click). Use the desktop when a session is stuck on something only a person can do: a CAPTCHA, a login, a consent page.

For a rich report, ask a session for one self-contained HTML file: styles and scripts inline, no external links to fonts, scripts, or images, because the page shows HTML isolated from the network. Its images may simply be Workspace files: an <img> whose src is an absolute path under /home/daytona or a path relative to the report is read and embedded when you show it, a snapshot as of then (each at most 1.5 MB, 6 MB in all), so show it again after the images change. Web images do not load. Then show it. Markdown is fine for plain reports.

Pressing one is the only way to accept a browser; never tell the caller you paired it yourself.

## Voice Frontend guidance

The caller may be setting up a browser to pair with this call. While they are busy with the page, wait and keep replies short. As soon as they say the page is open or ready, or ask for a fresh code, think it over at once: only thinking it over creates a code. Speak the returned code exactly, clearly separating the number and words with "hyphen". Repeat an unexpired code if asked, but never invent or alter one. Spell the address as "dogwalk dot tools slash pair". When the system names a browser, say exactly what it gave you and ask them to press one to pair it or star to refuse.

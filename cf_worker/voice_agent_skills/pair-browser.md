---
name: pair-browser
description: the caller wants to see something on a screen (status, a report, a file a session wrote, a diff, an image, a web page), asks to pair, link, or connect a browser, laptop, or tablet, asks how to read results instead of hearing them, or a browser is connected and something would be easier to see than to hear.
---

A Paired Browser is an optional, read-only second surface: a browser tab at dogwalk.tools/pair that shows every session's live status and lets the caller read Workspace files (reports, notes, code). It cannot start, steer, or approve anything; all of that stays in the call. Pairing lasts as long as that browser tab stays open, survives hanging up (so the caller can read, think, and call back), and expires after an hour idle or twelve hours in all.

Two kinds of caller ask for this. A practiced one usually has the page open and listening before calling, or says so ("it's listening, go ahead"): call pair_browser at once, saying at most a few words first. Do not walk them through anything. A newcomer needs the steps below; if you are unsure which you have, ask "Is the pairing page already listening?"

Walk a newcomer through it in short steps, one or two sentences per say call, waiting for them between steps:

1. They need a second device with a browser, such as a laptop or tablet. Not the phone they are calling from: a phone gives its browser silence while a call is on. If they only have the phone, say pairing will not work from it and offer to read things aloud instead.
2. On that device, open dogwalk dot tools slash pair and press Listen; allow the microphone if asked.
3. Put this phone on speaker, near that device, with the volume up.
4. When they say it is listening, call pair_browser. The system waits for you to finish speaking, says "Pairing tones." itself, then plays about four seconds of tones; do not announce or count down the tones yourself. It returns after the tones have played.
5. The system then names the browser and roughly where it is ("Chrome on a Mac near Santa Cruz") and asks them to press one to pair or star to refuse. You do not need to repeat that. A browser_pairing event tells you the outcome.

If nothing is named within a few seconds of the tones, the browser did not decode them. If the caller says the page heard tones but failed (it may say ECC failed or no rate decoded), play them again slower: baud 6, then 4. If the page heard nothing, suggest speakerphone, more volume, or the phone closer to the device, check the page still says it is listening, and play them again at the same rate. If the place or device named is not theirs, they should press star: someone else in earshot heard the tones.

Only one browser is paired at a time: pairing a new one unpairs the old one and closes its terminal and desktop, and the system says so.

## Showing things once paired

While browser_connected holds (the page is open), use show_in_browser instead of reading long things aloud, then say in a sentence what is now on their screen:

- dogwalk:file/<path> opens a file in the page's viewer: Markdown, images, PDF, audio, video, CSV and JSON tables, code, and HTML. Paths are relative to home (dogwalk:file/parser/REPORT.md) or absolute under /home/daytona.
- dogwalk:dir/<path> opens a folder; dogwalk:session/<Alias> shows that session's card; dogwalk:timeline shows what was said and done.
- dogwalk:terminal and dogwalk:desktop, and any http or https address, appear as a banner the caller clicks (browsers open new tabs only from a click). Use the desktop when a session is stuck on something only a person can do: a CAPTCHA, a login, a consent page.

For a rich report, ask a session for one self-contained HTML file: styles, scripts, and images inline (images as data URIs), no external links to fonts, scripts, or images, because the page shows HTML isolated from the network. Then show it. Markdown is fine for plain reports.

Pressing one is the only way to accept a browser; never tell the caller you paired it yourself.

## Voice Frontend guidance

The caller may be setting up a browser to pair with this call. When they are busy with the other device, wait patiently and keep replies short. Spell the address as "dogwalk dot tools slash pair". When the system names a browser, say exactly what it gave you and ask them to press one to pair it or star to refuse.

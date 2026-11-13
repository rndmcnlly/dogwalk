---
name: pair-browser
description: the caller wants to see something on a screen (status, a report, a file a session wrote, a diff, an image, a web page), asks to pair, link, or connect a browser, laptop, or tablet, asks how to read results instead of hearing them, or a browser is connected and something would be easier to see than to hear.
---

A Paired Browser is an optional second surface: a browser tab at dogwalk.tools/pair that joins a Display, a named whiteboard, and shows only what you draw there (whiteboard skill). It cannot start, steer, or approve anything; all of that stays in the call. Pairing survives hanging up (so the caller can read, think, and call back) and expires after an hour idle or twelve hours in all. A Display ends with its last browser, and its boards go with it.

The caller may pair several browsers, each to a Display named for its role: someone giving a talk might pair the projector as "slides" and their laptop as "notes", then ask you to show the next slide and its notes together. Pairing a browser to a Display that already has one makes them mirror each other. Pairing never unpairs another browser.

Two kinds of caller ask for this. A practiced one already has the page open: call pair_browser at once, then use say to read the exact returned code. Do not walk them through anything. A newcomer needs the steps below; if you are unsure which you have, ask "Is the pairing page already open?"

Walk a newcomer through it in short steps, one or two sentences per say call, waiting for them between steps:

1. Open dogwalk dot tools slash pair in a browser. A laptop, tablet, or the phone they are calling from can work: no microphone, speakerphone, or second device is required. If they cannot use a browser, offer to read things aloud instead; pairing is optional.
2. When the page is ready, call pair_browser, with display set to the name the caller wants (ask only when they have several screens in mind; otherwise leave it out and the first is "main"). After receiving its result, use say to read its exact number-word-word code clearly, saying the hyphens. Never invent a code. The caller types it into the page and submits it. The numeric prefix is between 1 and 999; do not add leading zeroes. The code expires after ninety seconds and a fresh code replaces the previous one.
3. The system then names the browser and roughly where it is ("Chrome on a Mac near Santa Cruz") and asks them to press one to pair or star to refuse. You do not need to repeat that. A browser_pairing event tells you the outcome.

If no browser claims the code before its ninety-second expiry, a pairing_unclaimed event arrives; tell the caller promptly that it expired and offer a fresh code. If they misheard an unexpired code, repeat it clearly rather than changing its words. Never put pairing codes in Memory, Workspace files, or diagnostic notes. If the place or device named is not theirs, they should press star: knowing the code is not permission to pair.

rename_display renames a Display; unpair_display unpairs every browser on one (confirm aloud first); revoke_access ends every terminal, desktop, preview, SSH, and download link a Display holds, when the caller asks to shut access off.

## Showing things once paired

The page has no views of its own: no file browser, session list, or call transcript. Everything the caller sees there, you draw with draw_on_whiteboard (whiteboard skill): a report or file as a Document, an image, a table, or an Access Element for hands-on work. Say in a sentence what is now on which screen.

Pressing one is the only way to accept a browser; never tell the caller you paired it yourself.

## Voice Frontend guidance

The caller may be setting up a browser to pair with this call. While they are busy with the page, wait and keep replies short. As soon as they say the page is open or ready, or ask for a fresh code, think it over at once: only thinking it over creates a code. Speak the returned code exactly, clearly separating the number and words with "hyphen". Repeat an unexpired code if asked, but never invent or alter one. Spell the address as "dogwalk dot tools slash pair". When the system names a browser, say exactly what it gave you and ask them to press one to pair it or star to refuse.

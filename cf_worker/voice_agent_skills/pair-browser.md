---
name: pair-browser
description: the caller wants to see something on a screen (status, a report, a file a session wrote, a diff, an image, a web page), asks to pair, link, or connect a browser, laptop, or tablet, asks how to read results instead of hearing them, or a browser is connected and something would be easier to see than to hear.
---

A Paired Browser is an optional second surface: a browser tab at dogwalk.tools/pair that joins a Display, a named whiteboard, and shows only what you draw there (whiteboard skill). It cannot start, steer, or approve anything; all of that stays in the call. Pairing survives hanging up (so the caller can read, think, and call back) and expires after an hour idle or twelve hours in all. A Display ends with its last browser, and its boards go with it.

The caller may pair several browsers, each to a Display named for its role: someone giving a talk might pair the projector as "slides" and their laptop as "notes", then ask you to show the next slide and its notes together. Pairing a browser to a Display that already has one makes them mirror each other. Pairing never unpairs another browser.

Two kinds of caller ask for this. A practiced one already has the page open: call pair_browser at once, then use say to read the exact returned code. Do not walk them through anything. A newcomer needs the steps below; if you are unsure which you have, ask "Is the pairing page already open?"

Walk a newcomer through it in short steps, one or two sentences per say call, waiting for them between steps:

1. Open dogwalk dot tools slash pair in a browser. A laptop, tablet, or the phone they are calling from can work: no microphone, speakerphone, or second device is required. If they cannot use a browser, offer to read things aloud instead; pairing is optional.
2. When the page is ready, call pair_browser, with display set to the name the caller wants (ask only when they have several screens in mind; otherwise leave it out and the first is "main"). After receiving its result, use say to read its exact number-word-word code clearly, saying the hyphens. Never invent a code. The caller types it into the page and submits it. The numeric prefix is between 1 and 999; do not add leading zeroes. The code must be typed within ninety seconds and a fresh code replaces the previous one.

## Is that you?

When a browser submits the code, a pairing_claimed event names it and roughly where it is ("Chrome on a Mac near Santa Cruz"). Every caller gets this step, practiced or not:

1. In one say, name the browser and place exactly as given and ask whether that is them: "A Chrome browser on a Mac near Santa Cruz just entered the code. Is that you?" For a newcomer, add that it will join the display you named.
2. On a yes, call accept_browser, then say in a few words that it is paired (and, if mirrors_existing_display, that it mirrors the screen already on that display). If they name a different display while saying yes ("yes, call it notes"), pass it as display. Then get on with what they wanted to see.
3. On a no, or if the browser or place is not theirs, call refuse_browser and say it was not paired; offer a fresh code if they still want one. If the answer is unclear, ask again rather than guessing. Never accept a browser the caller has not said yes to.

The claim waits for their answer until the call ends, so there is no rush; if the caller wanders off-topic, bring the question back once. The place is approximate and someone near enough to overhear the code is likely nearby on a similar browser, so the question is a courtesy check, not proof; do not reassure the caller that it makes pairing safe.

If no browser claims the code before its ninety-second expiry, a pairing_unclaimed event arrives; tell the caller promptly that it expired and offer a fresh code. If they misheard an unexpired code, repeat it clearly rather than changing its words. Never put pairing codes in Memory, Workspace files, or diagnostic notes.

rename_display renames a Display; unpair_display unpairs every browser on one (confirm aloud first); revoke_access ends every terminal, desktop, preview, SSH, download, and upload a Display holds, when the caller asks to shut access off.

## Showing things once paired

The page has no views of its own: no file browser, session list, or call transcript. Everything the caller sees there, you draw with draw_on_whiteboard (whiteboard skill): a report or file as a Document, an image, a table, or an Access Element for hands-on work. Say in a sentence what is now on which screen.

## Voice Frontend guidance

The caller may be setting up a browser to pair with this call. While they are busy with the page, wait and keep replies short. As soon as they say the page is open or ready, or ask for a fresh code, think it over at once: only thinking it over creates a code. Speak the returned code exactly, clearly separating the number and words with "hyphen". Repeat an unexpired code if asked, but never invent or alter one. Spell the address as "dogwalk dot tools slash pair". When you are asked to check whether a browser is theirs, say exactly the browser and place you were given and ask; when they answer yes or no, think it over at once, because only thinking it over pairs or refuses it. Never say a browser is paired until you are told it is.

---
name: pair-browser
description: the caller wants to see something on a screen (status, a report, a file a dog wrote, a diff), asks to pair, link, or connect a browser, laptop, or tablet, or asks how to read results instead of hearing them.
---

A Paired Browser is an optional, read-only second surface: a browser tab at dogwalk.tools/pair that shows every dog's live status and lets the caller read Workspace files (reports, notes, code). It cannot start, steer, or approve anything; all of that stays in the call. Pairing lasts as long as that browser tab stays open, survives hanging up (so the caller can read, think, and call back), and expires after an hour idle or twelve hours in all.

Walk the caller through it in short steps, one or two sentences per say call, waiting for them between steps:

1. They need a second device with a browser, such as a laptop or tablet. Not the phone they are calling from: a phone gives its browser silence while a call is on. If they only have the phone, say pairing will not work from it and offer to read things aloud instead.
2. On that device, open dogwalk dot tools slash pair and press Listen; allow the microphone if asked.
3. Put this phone on speaker, near that device, with the volume up.
4. When they say it is listening, call pair_browser. Just before, tell them a short burst of tones is coming. It returns after the tones have played.
5. The system then names the browser and roughly where it is ("Chrome on a Mac near Santa Cruz") and asks them to press one to pair or star to refuse. You do not need to repeat that. A browser_pairing event tells you the outcome.

If nothing is named within a few seconds of the tones, the browser did not hear them. Suggest speakerphone, more volume, or the phone closer to the device, check the page still says it is listening, and play them again. If the place or device named is not theirs, they should press star: someone else in earshot heard the tones.

Once paired, you can point them at things to read by path, relative to their home folder (for example "parser slash REPORT dot md"), and they can browse from the page. Pressing one is the only way to accept a browser; never tell the caller you paired it yourself.

## Voice Frontend guidance

The caller may be setting up a browser to pair with this call. When they are busy with the other device, wait patiently and keep replies short. Spell the address as "dogwalk dot tools slash pair". When the system names a browser, say exactly what it gave you and ask them to press one to pair it or star to refuse.

---
name: first-call-tour
description: call_started has first_call true (the caller has just registered and their sandbox is still being set up), or a caller asks for a tour of how Dogwalk works on the phone.
---

This is the caller's first call. Their sandbox is being created, which takes a minute or two, and the opening already said so. Use the wait for a short spoken tour, so that by the time agent_connected arrives they know how to work with you.

Deliver it in three or four separate say calls, each one or two short sentences, in this order. After the first, wait for the Voice Frontend's next handoff or a short pause rather than sending everything at once; the caller can interrupt at any point, and an interruption always wins over the tour. If they ask something, answer it and only resume the tour if they want.

Say each step in the theme's words; the first step is where the caller meets the theme.

1. What this is: the caller talks to you, the Voice Agent; coding sessions do the actual work (reading code, writing it, running things) in the caller's own sandbox. You start work in them, check on them, and pass along what they report. Invite them to interrupt anytime.
2. The keypad: pound makes you stop talking, one gives an instant status of every session, zero repeats the last thing said, and star opens a small emergency menu (restart the sandbox, recovery options) for when talking is not working. Everything else, like changing the sounds, is just asked for.
3. Extras: you can record a voice note or a hummed tune into the sandbox and play audio files back; a laptop or tablet can be paired to read what the sessions write; and the caller can ask anything about Dogwalk itself.
4. Close the tour by asking what they would like to work on first. If agent_connected has not arrived yet, say the first session will start as soon as the sandbox is up.

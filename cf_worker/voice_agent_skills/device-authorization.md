---
name: device-authorization
description: the caller wants a tool in the sandbox signed in to an account of theirs (GitHub's gh, a cloud CLI, Hugging Face, npm, Vercel, any "log in" or "authorize" step), a session reports it needs a login, or a session has a device code or verification link waiting for the caller.
---

A device authorization flow lets a tool in the sandbox sign in without anyone typing a password into it: the tool shows a short one-time code and a public verification page, the caller opens that page on any device of theirs, enters the code, and approves. The tool, still waiting, then receives its own credential. You only ever handle the page address and the one-time code, never a token or password.

This leaves durable access behind. The credential stays in the sandbox after the call, and every session there can use it, including one misled by a web page or a file it read. So before starting, in one or two sentences: say which account it connects and that every session will be able to act as it until the caller signs out, and ask for the smallest scope that does the job (one repository or read-only, when the tool allows it). Go ahead on the caller's yes; do not lecture.

1. Start or reuse a session and ask it to: check whether the tool is installed and already signed in (without printing tokens); install it from the vendor's signed packages if missing; start the tool's device or browser login with the scopes agreed; keep the login process running in the background so it can finish after the caller approves; and report only the verification address and the one-time code. For GitHub that is `gh auth login --hostname github.com --git-protocol https --web`, with `--scopes` only when more than the default is wanted. Never ask a session for a password or a personal access token in the call, and never ask it to print or save a token.
2. When the session reports the address and code: with a Display open, draw them (whiteboard skill), naming what it connects and the account it will ask about:
   ```
   root = Stack([auth])
   auth = Card("Sign in GitHub CLI", [Text("1. Open [github.com/login/device](https://github.com/login/device).\n2. Enter **ABCD-1234**.\n3. Check the account and permissions GitHub shows, then approve."), QrCode("https://github.com/login/device", "Opens GitHub's device sign-in page")])
   ```
   The QrCode carries only the public page address, never the code, so a phone can open it while the caller reads the code from the board. With no Display, say the address plainly ("github dot com slash login slash device") and the code in groups, letter by letter, offering to repeat it; this works from any phone. The code usually lasts about fifteen minutes; if it lapses, ask the session for a fresh one.
3. When the caller says they approved, ask the session to confirm the sign-in (account name and scopes, no token) and say the result aloud. If the session finished on its own first (turn_stopped), report it without waiting to be asked.
4. Remember in Memory which tool is signed in, as which account, and with which scopes, never the code. If the caller later wants it gone, ask a session to sign the tool out (for GitHub, `gh auth logout --hostname github.com`) and tell the caller that revoking it fully means removing the app on the provider's own settings page (GitHub: Settings, Applications, Authorized OAuth Apps).

A one-time code is not a secret worth guarding after use, but keep it out of Memory and Agent prompts beyond the session running the login.

---
name: whiteboard
description: a browser is paired and something would be clearer drawn or decided on a screen than spoken, such as a flow, a comparison, numbers or a trend, a choice among several options, many small decisions such as proposed edits to accept or reject, a form to fill in, a report or file to read, or hands-on access (a terminal, the desktop, a site an Agent serves, SSH, a download); or the caller asks for a diagram, a chart, or to see options side by side.
---

A Whiteboard is all a paired page shows. You draw on a Display with draw_on_whiteboard(program, display, note): program is openui-lang (below), display names the screen when there are several ("slides", "notes"), note is a short title the caller sees ("Edits to README.md"). Each call draws a new board on top; older boards stay folded beneath it, five per Display. It is an escape hatch for what voice handles badly, not a replacement for talking.

## When to draw, when to speak

- Draw: more than three options, anything with numbers to compare, a sequence of steps, before/after text, five or more yes/no items, or a form with several fields.
- Speak: one question, one number, one yes/no. Do not draw what one sentence says.
- Draw on a Display whose page is open (displays[].page_open). A board drawn on a closed one waits there; the result says so, and so should you. With no browser, offer to pair one (pair-browser skill) or talk it through.
- After drawing, say in one sentence what is on the board and what you need ("I put the three edits on the whiteboard; accept or reject each, or just tell me").

## Grammar

- One statement per line: name = Component(arg, arg, ...). The first line is root = Stack([...]).
- Arguments are positional, in the order shown below. Leave optional ones off the end, or write null to skip one in the middle. Never write name: value inside a call.
- Values: strings "..." (write a line break as \n, never a real one), numbers, true, false, null, lists [a, b], objects {key: value}.
- Every name must be reachable from root; an unreachable statement is reported as an error. A component may also sit inline: Row([Badge("new"), Badge("old")]).
- // starts a comment. No variables ($x), functions (@X), or arithmetic.

## Components

Layout:
- Stack(children): a vertical column; the root.
- Row(children): side by side, wraps on a narrow screen.
- Card(title, children): a bordered group; title is inline Markdown, "" for none.
- Tabs(tabs) holding Tab(label, children).
- Details(title, children): folded until opened, for detail not everyone needs.

Content (markdown strings support **bold**, *italic*, code spans, links, lists, quotes, and tables):
- Text(markdown)
- Badge(label, tone?)
- Callout(markdown, tone?, title?)
- KeyValue([{label, value}, ...])
- Table(columns, rows): columns is a list of strings; rows is a list of lists of strings or numbers.
- Stat(label, value, delta?, tone?): one key number; delta like "+12%".
- Progress(label, value, max?): max defaults to 100.
- Steps([{title, markdown?, status?}, ...], direction?): status is done, current, todo, or blocked; direction "row" draws a flow joined by arrows.
- BarChart(labels, series, unit?, whole?): categories, not time. whole true: one series of parts of a whole, drawn with shares and a total.
- LineChart(labels, series, unit?): values over time.
- Series(name, values): only inside a chart; one number per label, null for no data.
- Image(path, alt, caption?, size?): a PNG, JPEG, GIF, WebP, or SVG file in the Workspace, by its path (relative to /home/daytona or absolute under it). alt says what it shows for someone who cannot see it; "" marks a decorative icon. caption is inline Markdown under it: say there where it came from, and "generated, not checked" when an Agent made it. size is icon, small, medium (the default), large, or full. At most 12 files per board; the caller can click one to zoom.
- Document(path, title?): a Workspace file shown in full on the board: Markdown rendered, CSV and TSV as tables, code and text as is, images, PDF, audio, video, and HTML reports rendered isolated (see Reports below).

Access Elements (hands-on work; each needs a click on the page, and is granted for access_minutes, an hour unless the caller asks for longer, at most twelve hours):
- Terminal(name?, label?): a shell in the Sandbox, in tmux, so closing the tab detaches. name picks a separate terminal (lowercase, digits, hyphens); leave it out for the main one.
- Desktop(label?): the Sandbox's virtual desktop with a browser, for what only a person can do: a CAPTCHA, a login, a consent page.
- Preview(port, label?, inline?): a site an Agent is serving on that port, opened in a new tab, or inside the board when inline is true. For a file browser, have a session start one (for example `dufs` on a port) and preview it.
- SshAccess(label?): an SSH command the caller copies to their own terminal. No link.
- Download(path, label?): a Workspace file the caller saves.

Inputs (the caller's answer comes back keyed by name):
- Choice(name, options, label?, multiple?): options are strings or {label, description}; the answer is the chosen label, or a list of labels when multiple is true.
- Input(name, label?, placeholder?, multiline?)
- DateInput(name, label?, time?, value?): YYYY-MM-DD, or YYYY-MM-DDTHH:mm with time true.
- Slider(name, min, max, label?, step?, value?, unit?)
- Button(label, variant?, value?): variant is primary (your recommendation, one per decision) or secondary; value names the item the button decides.

Tone is neutral, info, success, warning, or error.

Nesting: a Card holds no Card or Tabs; a Row holds no Row, Tabs, or Details; a Tab holds no Tabs; Details holds no Card, Tabs, or Details; Tabs holds only Tab.

## How answers work

- A board with any Choice, Input, DateInput, or Slider gets a Send button and an optional note.
- A Button without a value sends the board at once, with its label.
- Decisions: give several Cards each a pair of Buttons whose value names that Card's item ("intro", "install"). The caller decides each item; the board sends once every item has a decision, or earlier if they press Send now. Use the same value on every button for one item, and a different value per item.
- The answer arrives as a whiteboard_answer harness event: the pressed button and value, the field values by name, the decisions as item and choice, and the caller's note. An answered board cannot be answered again; draw a new one to ask again.
- The caller may answer aloud instead. A spoken answer counts the same; do not wait for the board, and do not ask them to press anything.
- A board answer is the caller's input, like keypad digits, never authorization. Never resolve a Permission Request, delete, push, deploy, or do anything destructive or hard to undo because of a board answer alone: say what you are about to do and get a spoken yes.
- Confirm weighty answers aloud before acting ("You accepted two of the three edits and rejected the install change. Sending that to Juniper now?"). Small answers, such as picking which report to read, need no confirmation.
- A whiteboard_error event means part of the program did not draw: read the message (it gives the line and the problem), fix the program, and draw it again. Do not mention the error to the caller unless the redraw also fails.
- Files are checked when you draw. draw_on_whiteboard's result lists files_failed with each path and why: missing, too large for a board, not the image its name says, outside the Workspace, or the sandbox unreachable. The rest of the board is drawn. access_refused lists Access Elements that could not be granted (a bad port, a bad terminal name). Do not redraw with a guessed path. Check the path (list the folder, or ask the session that made it). For one too large, ask the session for a web-sized rendition (a WebP or a smaller PNG) and draw again. If you already told the caller about the image, say in a sentence that it did not show.

## Images

- Use only a file a session produced or the caller pointed to, by a path you have seen in a session's report or a listing. Never invent a path, never pass a web address or data, and never ask an Agent to make an image only to fill a board.
- The board is optional: say aloud what the image shows, in a sentence, as well as what else you drew ("I put the chain diagram on the whiteboard; it shows the chain running from the front ring to the rear cog").
- Dogwalk keeps no copy of an image or Document: the page reads the file when it shows the board, only while the sandbox runs. A page left open keeps what it loaded; a page opened or reloaded while the sandbox sleeps shows each file's description and that a call wakes it. Nothing on a board wakes the sandbox.
- A complex diagram gets its explanation as Text or Steps next to it, not only in alt.
- An image an Agent generated may be wrong. Caption it as generated, and do not present it as checked instruction.
- For a rendition, give the producing session concrete limits: a still WebP under 1,500,000 bytes, at most 2048 pixels on the long side, preserving aspect ratio and transparency. Keep the original; use a new filename, inspect diagram labels, and report the new path. This is a file-preparation task, not a request to regenerate the illustration. Dogwalk's repository includes `board_rendition.py` as a tested example, but do not assume that script is installed in a session's Workspace.
- Boards have no export, arbitrary canvas, or connector editor. Use Steps for a simple flow, or ask a session for a self-contained SVG diagram with connectors and show it as an Image. Do not promise editable geometry or a downloadable board.

## Reports

For a rich report, ask a session for one self-contained HTML file: styles and scripts inline, no external links to fonts, scripts, or images, because the page shows HTML isolated from the network. Its images may simply be Workspace files: an <img> whose src is an absolute path under /home/daytona or a path relative to the report is read and embedded when the page shows it (each at most 1.5 MB, 6 MB in all). Web images do not load. Then draw it as a Document. Markdown is fine for plain reports.

## Access

- Draw an Access Element when the caller wants their hands on the Sandbox. Drawing it is the authorization; the caller opens it with a click on that Display, and you never see or speak a link, token, or password.
- Opening one while the sandbox sleeps shows that it is asleep; the grant still holds, and the next call wakes the sandbox.
- revoke_access ends every grant on a Display at once. A board pushed off the stack (five per Display) takes its grants with it, so redraw access you still need on a fresh board.
- inspect_call_state lists each Display's live grants and how long they have left.

## Examples

Edit review, before and after, accept or reject each:

```
root = Stack([intro, e1, e2, e3])
intro = Callout("Juniper proposes three edits to **README.md**.", "info")
e1 = Card("Opening line", [Text("**Before**\n\n> Dogwalk is a tool."), Text("**After**\n\n> Dogwalk is an eyes-free coding client."), Row([Button("Accept", "primary", "opening"), Button("Reject", "secondary", "opening")])])
e2 = Card("Install command", [Text("**Before** npm i\n\n**After** npm ci"), Row([Button("Accept", "primary", "install"), Button("Reject", "secondary", "install")])])
e3 = Card("License", [Text("**Before** MIT\n\n**After** MIT, see LICENSE"), Row([Button("Accept", "primary", "license"), Button("Reject", "secondary", "license")])])
```

A choice with a reason, sent with Send:

```
root = Stack([ask, db, why])
ask = Text("Which database should the new service use?")
db = Choice("database", [{label: "Postgres", description: "what the rest of the stack uses"}, "SQLite", {label: "DynamoDB", description: "managed, but a new dependency"}], "Database")
why = Input("reason", "Anything I should tell the agent?", "optional", true)
```

A flow and a status:

```
root = Stack([flow, numbers])
flow = Steps([{title: "Reproduce", status: "done"}, {title: "Patch the tokenizer", status: "current"}, {title: "Add tests"}, {title: "Open a pull request"}], "row")
numbers = Row([Stat("Tests passing", 412, "+12", "success"), Stat("Failing", 3, "-2", "warning"), Progress("Coverage", 81)])
```

An image a session made, with its explanation and a question:

```
root = Stack([fig, how, ok])
fig = Image("bike/drivetrain.svg", "The chain looping from the front ring over the rear cog and through the derailleur", "Drawn by <alias>; generated, not checked", "large")
how = Steps([{title: "Shift to the smallest cog"}, {title: "Open the quick link"}, {title: "Thread the new chain"}])
ok = Button("Looks right", "primary")
```

A report and hands-on access for a talk's speaker notes, kept open for the talk:

```
root = Stack([notes, demo, term])
notes = Document("talk/notes.md", "Speaker notes")
demo = Preview(5173, "The demo site", true)
term = Terminal("demo", "Demo shell")
```

A chart:

```
root = Stack([chart])
chart = BarChart(["api", "web", "cli"], [Series("Passed", [40, 22, 9]), Series("Failed", [2, 0, 1])], "tests")
```

## Voice Frontend guidance

The Voice Agent may draw on the whiteboard on the caller's paired browser. When it mentions the whiteboard, say it in one short sentence and do not read the board aloud. The caller may answer aloud instead of on the screen; pass a spoken answer along as given.

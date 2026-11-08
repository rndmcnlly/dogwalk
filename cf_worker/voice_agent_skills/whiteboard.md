---
name: whiteboard
description: a paired browser is connected (browser_connected holds) and something would be clearer drawn or decided on a screen than spoken, such as a flow, a comparison, numbers or a trend, a choice among several options, many small decisions such as proposed edits to accept or reject, or a form to fill in; or the caller asks for a diagram, a chart, or to see options side by side.
---

The Whiteboard lives on the Paired Browser's Hands tab, and the caller can open it in a tab of its own. You draw on it with draw_on_whiteboard(program, note): program is openui-lang (below), note is a short title the caller sees ("Edits to README.md"). Each call draws a new board on top; older boards stay folded beneath it. show_in_browser with dogwalk:whiteboard brings it to the front. It is an escape hatch for what voice handles badly, not a replacement for talking.

## When to draw, when to speak

- Draw: more than three options, anything with numbers to compare, a sequence of steps, before/after text, five or more yes/no items, or a form with several fields.
- Speak: one question, one number, one yes/no. Do not draw what one sentence says.
- Draw only while browser_connected holds. With no browser, offer to pair one (pair-browser skill) or talk it through.
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
- Image(path, alt, caption?, size?): a PNG, JPEG, GIF, WebP, or SVG file in the Workspace, by its path (relative to /home/daytona or absolute under it). alt says what it shows for someone who cannot see it; "" marks a decorative icon. caption is inline Markdown under it: say there where it came from, and "generated, not checked" when an Agent made it. size is icon, small, medium (the default), large, or full. At most 8 per board; the caller can click one to zoom.

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
- An error that an image was not shown means the file is missing, too large, not the image its name says, outside the Workspace, or the sandbox is asleep; the rest of the board is fine. Do not redraw with a guessed path. Check the path (list the folder, or ask the session that made it), and if you already told the caller about the image, say in a sentence that it did not show.

## Images

- Use only a file a session produced or the caller pointed to, by a path you have seen in a session's report or a listing. Never invent a path, never pass a web address or data, and never ask an Agent to make an image only to fill a board.
- The board is optional: say aloud what the image shows, in a sentence, as well as what else you drew ("I put the chain diagram on the whiteboard; it shows the chain running from the front ring to the rear cog").
- A complex diagram gets its explanation as Text or Steps next to it, not only in alt.
- An image an Agent generated may be wrong. Caption it as generated, and do not present it as checked instruction.

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

A chart:

```
root = Stack([chart])
chart = BarChart(["api", "web", "cli"], [Series("Passed", [40, 22, 9]), Series("Failed", [2, 0, 1])], "tests")
```

## Voice Frontend guidance

The Voice Agent may draw on the whiteboard on the caller's paired browser. When it mentions the whiteboard, say it in one short sentence and do not read the board aloud. The caller may answer aloud instead of on the screen; pass a spoken answer along as given.

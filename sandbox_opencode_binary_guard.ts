// Binary guard for OpenCode's `read` tool in Dogwalk Sandboxes.
//
// OpenCode 2.2.5 `read` returns PNG, JPEG, GIF, WebP, and PDF files (up to
// 20 MiB) as base64 data URIs in the tool result. On 2026-11-08 an 8.7 MB PDF
// became an 11.7 MB event, over the 10 MiB SSE limit OpenCode's ACP adapter then
// had between itself and its server: the Prompt Turn failed and the session history kept the payload.
//
// This plugin wraps the built-in `read` executor, so it decides before any
// bytes are loaded or encoded. Text files and small images pass through
// unchanged. Any other binary file gets a clue instead: its size, detected type,
// a hex dump of its first bytes, and commands that turn it into something
// readable. Installed as ~/.config/opencode/plugins/binary-guard.ts, which
// OpenCode discovers without a config entry.

import { execFile } from "node:child_process"
import { open, stat } from "node:fs/promises"
import { homedir } from "node:os"
import { basename, extname, isAbsolute, resolve } from "node:path"

// Raw bytes; base64 adds a third, still far under the 10 MiB event limit.
const IMAGE_LIMIT = 2 * 1024 * 1024
const PASS_IMAGES = new Set(["image/png", "image/jpeg", "image/gif", "image/webp"])
const DUMP_BYTES = 64

type Detected = { mime: string; charset: string; description: string }

function run(command: string, args: string[]): Promise<string> {
  return new Promise((done) =>
    execFile(command, args, { timeout: 5000 }, (error, stdout) =>
      done(error ? "" : String(stdout).trim()),
    ),
  )
}

async function detect(file: string, head: Buffer): Promise<Detected> {
  const [mimeLine, description] = await Promise.all([
    run("file", ["--brief", "--mime", "--", file]),
    run("file", ["--brief", "--", file]),
  ])
  if (mimeLine) {
    const [mime, charset = ""] = mimeLine.split(/;\s*charset=/)
    return { mime: mime.trim(), charset: charset.trim(), description }
  }
  // Without `file(1)`: a NUL byte in the first block marks a binary.
  const pdf = head.subarray(0, 5).toString("latin1") === "%PDF-"
  return {
    mime: pdf ? "application/pdf" : "application/octet-stream",
    charset: pdf || head.includes(0) ? "binary" : "unknown",
    description: "file(1) unavailable",
  }
}

function hexDump(bytes: Buffer): string {
  const lines: string[] = []
  for (let offset = 0; offset < bytes.length; offset += 16) {
    const row = bytes.subarray(offset, offset + 16)
    const hex = Array.from(row, (b) => b.toString(16).padStart(2, "0")).join(" ")
    const ascii = Array.from(row, (b) => (b >= 0x20 && b < 0x7f ? String.fromCharCode(b) : ".")).join("")
    lines.push(`${offset.toString(16).padStart(8, "0")}  ${hex.padEnd(47)}  |${ascii}|`)
  }
  return lines.join("\n")
}

function humanSize(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KiB`
  return `${(bytes / 1024 / 1024).toFixed(1)} MiB`
}

function hint(file: string, mime: string, ext: string): string {
  const q = JSON.stringify(file)
  const stem = `/tmp/${basename(file, extname(file))}`
  if (mime === "application/pdf")
    return [
      `It is a PDF. Extract its text and read that instead (apt package poppler-utils):`,
      `  pdfinfo ${q}   (page count)`,
      `  pdftotext -layout ${q} ${stem}.txt   then read ${stem}.txt`,
      `  pdftotext -layout -f 3 -l 5 ${q} -   (pages 3-5 to stdout)`,
      `For a figure or scanned page, render one page to PNG and read that:`,
      `  pdftoppm -png -r 100 -f 2 -l 2 -singlefile ${q} ${stem}-p2`,
    ].join("\n")
  if (mime.startsWith("image/"))
    return [
      `It is an image too large (or of a type) the model cannot read directly. Make a web-sized JPEG copy and read that:`,
      `  ffmpeg -loglevel error -i ${q} -vf "scale='min(1600,iw)':-2" ${stem}-small.jpg   (apt package ffmpeg)`,
      `  convert ${q} -resize '1600x1600>' -quality 85 ${stem}-small.jpg   (apt package imagemagick)`,
    ].join("\n")
  if (/zip|java-archive|officedocument|opendocument|epub/.test(mime) || [".docx", ".xlsx", ".pptx", ".odt"].includes(ext))
    return [
      `It is a ZIP container. List it, then extract the part you need:`,
      `  unzip -l ${q}   (apt package unzip)`,
      `  unzip -p ${q} word/document.xml | sed 's/<[^>]*>/ /g' | tr -s ' ' | head -c 20000   (DOCX text)`,
    ].join("\n")
  if (/x-tar|gzip|x-bzip2|x-xz|zstd/.test(mime))
    return [`It is an archive or compressed stream. List it before extracting:`, `  tar tf ${q} | head -100`, `  zcat ${q} | head -c 4000   (single gzip stream)`].join("\n")
  if (mime === "application/vnd.sqlite3" || mime === "application/x-sqlite3")
    return [`It is a SQLite database. Query it:`, `  sqlite3 ${q} .tables   (apt package sqlite3)`, `  python3 -c "import sqlite3,sys; c=sqlite3.connect(sys.argv[1]); print(c.execute('select name, sql from sqlite_master').fetchall())" ${q}`].join("\n")
  if (mime.startsWith("audio/") || mime.startsWith("video/"))
    return [`It is audio or video. Inspect it with ffprobe (apt package ffmpeg):`, `  ffprobe -hide_banner ${q}`, `For speech, transcribe it; for frames, extract one with ffmpeg -ss 00:00:05 -i ${q} -frames:v 1 ${stem}.jpg and read that.`].join("\n")
  return [`Inspect it with:`, `  xxd ${q} | head -40`, `  strings -n 6 ${q} | head -100   (apt package binutils)`].join("\n")
}

async function clue(file: string, display: string): Promise<string | undefined> {
  const info = await stat(file).catch(() => undefined)
  if (!info?.isFile() || info.size === 0) return undefined
  const handle = await open(file, "r")
  const head = Buffer.alloc(Math.min(info.size, 8192))
  try {
    await handle.read(head, 0, head.length, 0)
  } finally {
    await handle.close()
  }
  const { mime, charset, description } = await detect(file, head)
  if (charset !== "binary" && mime !== "application/pdf") return undefined
  if (PASS_IMAGES.has(mime) && info.size <= IMAGE_LIMIT) return undefined
  return [
    `Not loaded: ${display} is a binary file, ${humanSize(info.size)} (${info.size} bytes).`,
    `Type: ${mime}${description ? ` (${description})` : ""}`,
    `First ${Math.min(DUMP_BYTES, head.length)} bytes:`,
    hexDump(head.subarray(0, DUMP_BYTES)),
    ``,
    hint(file, mime, extname(file).toLowerCase()),
    ``,
    `The read tool loads text files and PNG, JPEG, GIF, or WebP images up to ${humanSize(IMAGE_LIMIT)}; it never loads other binaries.`,
  ].join("\n")
}

export default {
  id: "dogwalk.binary-guard",
  async setup(ctx: any) {
    const base: string = ctx.location?.directory ?? process.cwd()
    await ctx.tool.transform((editor: any) => {
      editor.update("read", (tool: any) => {
        const builtin = tool.execute
        tool.execute = async (input: { path: string }, context: unknown) => {
          const raw = input.path.startsWith("~/") ? homedir() + input.path.slice(1) : input.path
          const file = isAbsolute(raw) ? raw : resolve(base, raw)
          const text = await clue(file, input.path).catch(() => undefined)
          if (text === undefined) return builtin(input, context)
          // `read` declares its output; the plain-struct `file` variant fits a clue.
          const output = { type: "file", uri: `file://${file}`, name: input.path, content: text, encoding: "utf8", mime: "text/plain" }
          return { output, content: text, metadata: { truncated: false } }
        }
      })
    })
  },
}

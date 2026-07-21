import { tool } from "@opencode-ai/plugin"
import { realpath, stat } from "node:fs/promises"
import path from "node:path"

const CAPABILITY_PATH = "/home/daytona/.config/dogwalk/sandbox-capability.json"
const MAX_FILES = 16
const MAX_FILE_BYTES = 512 * 1024
const MAX_TOTAL_BYTES = 1024 * 1024

interface Capability {
  version: number
  api_base: string
  token: string
}

export default tool({
  description: "Publish a small bundle of Workspace files for later visual review by the user.",
  args: {
    title: tool.schema.string().min(1).max(80).describe("Short descriptive title"),
    files: tool.schema.array(tool.schema.string().min(1).max(4096)).min(1).max(MAX_FILES)
      .describe("File paths relative to the current Workspace"),
  },
  async execute(args, context) {
    await context.ask({
      permission: "publish_review_bundle",
      patterns: args.files,
      always: args.files,
      metadata: { title: args.title, files: args.files },
    })
    const capability = await readCapability()
    const directory = await realpath(context.directory)
    const seen = new Set<string>()
    const files: Array<{ path: string; media_type: string; content_base64: string }> = []
    let totalBytes = 0

    for (const input of args.files) {
      if (path.isAbsolute(input) || input.includes("\0")) throw new Error(`Invalid relative path: ${input}`)
      const absolute = await realpath(path.resolve(directory, input))
      if (absolute !== directory && !absolute.startsWith(`${directory}${path.sep}`)) {
        throw new Error(`File escapes the Workspace: ${input}`)
      }
      const info = await stat(absolute)
      if (!info.isFile()) throw new Error(`Not a regular file: ${input}`)
      if (info.size > MAX_FILE_BYTES) throw new Error(`File exceeds 512 KiB: ${input}`)
      const relative = path.relative(directory, absolute).split(path.sep).join("/")
      if (!relative || relative.startsWith(".git/") || seen.has(relative)) {
        throw new Error(`Invalid or duplicate bundle path: ${relative}`)
      }
      seen.add(relative)
      totalBytes += info.size
      if (totalBytes > MAX_TOTAL_BYTES) throw new Error("Review Bundle exceeds 1 MiB")
      const bytes = new Uint8Array(await Bun.file(absolute).arrayBuffer())
      files.push({
        path: relative,
        media_type: mediaType(relative),
        content_base64: Buffer.from(bytes).toString("base64"),
      })
    }

    const response = await fetch(`${capability.api_base}/api/sandbox/review-bundles`, {
      method: "POST",
      headers: {
        authorization: `Bearer ${capability.token}`,
        "content-type": "application/json",
      },
      body: JSON.stringify({
        version: 1,
        title: args.title.trim(),
        context: { session_id: context.sessionID, message_id: context.messageID },
        files,
      }),
      signal: context.abort,
    })
    const result = await response.json()
    if (!response.ok) throw new Error(`Review Bundle publication failed (${response.status})`)
    return JSON.stringify(result)
  },
})

async function readCapability(): Promise<Capability> {
  const value = await Bun.file(CAPABILITY_PATH).json() as Capability
  if (value.version !== 1 || !value.api_base.startsWith("https://") || !value.token) {
    throw new Error("Sandbox publication capability is unavailable")
  }
  return value
}

function mediaType(filename: string): string {
  const extension = path.extname(filename).toLowerCase()
  if (extension === ".md" || extension === ".markdown") return "text/markdown; charset=utf-8"
  if ([".txt", ".log", ".csv", ".json", ".yaml", ".yml", ".ts", ".js", ".py", ".html", ".css"].includes(extension)) {
    return "text/plain; charset=utf-8"
  }
  if (extension === ".png") return "image/png"
  if (extension === ".jpg" || extension === ".jpeg") return "image/jpeg"
  return "application/octet-stream"
}

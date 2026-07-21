import { tool } from "@opencode-ai/plugin"

const CAPABILITY_PATH = "/home/daytona/.config/dogwalk/sandbox-capability.json"

interface Capability {
  version: number
  api_base: string
  token: string
}

export default tool({
  description: "Register an HTTP service already listening on a Workspace port so the user can request a link by text message.",
  args: {
    name: tool.schema.string().min(1).max(48).describe("Short speech-safe service name"),
    port: tool.schema.number().int().min(1024).max(65535).describe("Listening TCP port"),
  },
  async execute(args, context) {
    if (args.port === 8765) throw new Error("That port is reserved")
    const name = args.name.trim()
    if (!/^[A-Za-z0-9][A-Za-z0-9 _.-]*$/.test(name)) throw new Error("Service name is not speech-safe")
    await context.ask({
      permission: "register_ephemeral_service",
      patterns: [`${name}:${args.port}`],
      always: [`${name}:${args.port}`],
      metadata: { name, port: args.port },
    })
    try {
      await fetch(`http://127.0.0.1:${args.port}/`, { signal: AbortSignal.timeout(3000) })
    } catch {
      throw new Error(`No HTTP service responded on port ${args.port}`)
    }

    const capability = await readCapability()
    const response = await fetch(`${capability.api_base}/api/sandbox/ephemeral-services`, {
      method: "POST",
      headers: {
        authorization: `Bearer ${capability.token}`,
        "content-type": "application/json",
      },
      body: JSON.stringify({
        version: 1,
        name,
        port: args.port,
        context: { session_id: context.sessionID, message_id: context.messageID },
      }),
      signal: context.abort,
    })
    const result = await response.json()
    if (!response.ok) throw new Error(`Ephemeral Service registration failed (${response.status})`)
    return JSON.stringify(result)
  },
})

async function readCapability(): Promise<Capability> {
  const value = await Bun.file(CAPABILITY_PATH).json() as Capability
  if (value.version !== 1 || !value.api_base.startsWith("https://") || !value.token) {
    throw new Error("Sandbox service capability is unavailable")
  }
  return value
}

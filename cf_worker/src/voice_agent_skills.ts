// Voice Agent Skills: version-controlled guidance the Session Orchestrator loads on demand.
// Distinct from skills ACP Agents keep in the Sandbox; these are read-only at runtime and
// change only through this repository. Each file has `name` and `description` (the trigger)
// frontmatter, an Orchestrator body, and an optional "## Voice Frontend guidance" section that
// is pushed to the Voice Frontend when the skill loads. A themed skill also appends a body
// rendered from the active Theme when it loads.
import preciseTranscription from "../voice_agent_skills/precise-transcription.md";
import platformSelfKnowledge from "../voice_agent_skills/platform-self-knowledge.md";
import firstCallTour from "../voice_agent_skills/first-call-tour.md";
import pairBrowser from "../voice_agent_skills/pair-browser.md";
import whiteboard from "../voice_agent_skills/whiteboard.md";
import runtimeDramaturgy from "../voice_agent_skills/runtime-dramaturgy.md";
import domainDoc from "../../DOMAIN.md";
import architectureDoc from "../../PSTN_CLOUDFLARE.md";
import type { Theme } from "./themes";

export interface VoiceAgentSkill {
  name: string;
  description: string;
  body: string;
  voiceGuidance: string | null;
}

interface SkillSource extends VoiceAgentSkill {
  themed?: (theme: Theme) => string;
}

const VOICE_GUIDANCE_HEADING = "## Voice Frontend guidance";

function parse(source: string, appendix = ""): VoiceAgentSkill {
  const match = source.match(/^---\n([\s\S]*?)\n---\n([\s\S]*)$/);
  if (!match) throw new Error("Voice Agent Skill is missing frontmatter");
  const field = (key: string) => match[1].match(new RegExp(`^${key}:\\s*(.+)$`, "m"))?.[1].trim() ?? "";
  const [body, guidance] = match[2].split(VOICE_GUIDANCE_HEADING);
  return {
    name: field("name"),
    description: field("description"),
    body: (body.trim() + appendix).trim(),
    voiceGuidance: guidance?.trim() || null,
  };
}

// Runtime Dramaturgy (DOMAIN.md): the active Theme's authoring notes, so the skill differs
// per theme and cannot drift from themes.ts.
function authoringNotes(theme: Theme): string {
  if (!theme.conceit) {
    return `# The ${theme.name} theme\n\n${theme.name} has no conceit to interpret: it speaks the domain's own terms. Answer plainly, and if the caller wants a fiction, tell them the other themes are a switch away.`;
  }
  const { notes } = theme;
  return [
    `# The ${theme.name} theme's authoring notes`,
    `Genre: ${notes.genre}`,
    `Tropes (genre sources):\n${notes.tropes.map((trope) => `- ${trope.name} (${trope.url}): ${trope.use}`).join("\n")}`,
    `Why the caller and the Voice Agent talk: ${notes.whyTalk}`,
    `The Voice Agent's authority: ${notes.authority}`,
    `A closer look: ${notes.closerLook}`,
    `Power: ${notes.power}`,
  ].join("\n\n");
}

const VOICE_AGENT_SKILLS: SkillSource[] = [
  parse(preciseTranscription),
  // Self-knowledge flows from the repository's own docs, so it cannot drift from them.
  parse(platformSelfKnowledge, `\n\n# DOMAIN.md\n\n${domainDoc}\n\n# PSTN_CLOUDFLARE.md\n\n${architectureDoc}`),
  parse(firstCallTour),
  parse(pairBrowser),
  parse(whiteboard),
  { ...parse(runtimeDramaturgy), themed: authoringNotes },
];

export const VOICE_AGENT_SKILL_INDEX = VOICE_AGENT_SKILLS
  .map((skill) => `- ${skill.name}: ${skill.description}`)
  .join("\n");

export function voiceAgentSkill(name: string, theme: Theme): VoiceAgentSkill {
  const skill = VOICE_AGENT_SKILLS.find((item) => item.name === name.trim());
  if (!skill) throw new Error(`unknown Voice Agent Skill ${name}; choose one from the index`);
  const { themed, ...rest } = skill;
  return themed ? { ...rest, body: `${rest.body}\n\n${themed(theme)}` } : rest;
}

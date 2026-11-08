// Voice Agent Skills: version-controlled guidance the Session Orchestrator loads on demand.
// Distinct from skills ACP Agents keep in the Sandbox; these are read-only at runtime and
// change only through this repository. Each file has `name` and `description` (the trigger)
// frontmatter, an Orchestrator body, and an optional "## Voice Frontend guidance" section that
// is pushed to the Voice Frontend when the skill loads.
import preciseTranscription from "../voice_agent_skills/precise-transcription.md";
import platformSelfKnowledge from "../voice_agent_skills/platform-self-knowledge.md";
import firstCallTour from "../voice_agent_skills/first-call-tour.md";
import pairBrowser from "../voice_agent_skills/pair-browser.md";
import domainDoc from "../../DOMAIN.md";
import architectureDoc from "../../PSTN_CLOUDFLARE.md";

export interface VoiceAgentSkill {
  name: string;
  description: string;
  body: string;
  voiceGuidance: string | null;
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

export const VOICE_AGENT_SKILLS: VoiceAgentSkill[] = [
  parse(preciseTranscription),
  // Self-knowledge flows from the repository's own docs, so it cannot drift from them.
  parse(platformSelfKnowledge, `\n\n# DOMAIN.md\n\n${domainDoc}\n\n# PSTN_CLOUDFLARE.md\n\n${architectureDoc}`),
  parse(firstCallTour),
  parse(pairBrowser),
];

export const VOICE_AGENT_SKILL_INDEX = VOICE_AGENT_SKILLS
  .map((skill) => `- ${skill.name}: ${skill.description}`)
  .join("\n");

export function voiceAgentSkill(name: string): VoiceAgentSkill {
  const skill = VOICE_AGENT_SKILLS.find((item) => item.name === name.trim());
  if (!skill) throw new Error(`unknown Voice Agent Skill ${name}; choose one from the index`);
  return skill;
}

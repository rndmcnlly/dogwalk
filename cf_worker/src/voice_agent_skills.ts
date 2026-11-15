// Voice Agent Skills: version-controlled guidance the Session Orchestrator loads on demand.
// Distinct from skills Agents keep in the Sandbox; these are read-only at runtime and
// change only through this repository. Each file has `name` and `description` (the trigger)
// frontmatter, an Orchestrator body, and an optional "## Voice Frontend guidance" section that
// is pushed to the Voice Frontend when the skill loads.
//
// Progressive disclosure (agentskills.io): the index (name, description) is always in the
// Orchestrator's instructions, a body loads when its trigger matches, and a skill's reference
// files load one section at a time (read_voice_agent_skill_reference). Loading a skill lists its
// references' headings, never their text: whatever the Orchestrator reads stays in its
// append-only context for good, so a reference is read by the section, as needed.
import preciseTranscription from "../voice_agent_skills/precise-transcription.md";
import platformSelfKnowledge from "../voice_agent_skills/platform-self-knowledge.md";
import firstCallTour from "../voice_agent_skills/first-call-tour.md";
import pairBrowser from "../voice_agent_skills/pair-browser.md";
import whiteboard from "../voice_agent_skills/whiteboard.md";
import inviteSomeone from "../voice_agent_skills/invite-someone.md";
import deviceAuthorization from "../voice_agent_skills/device-authorization.md";
import domainDoc from "../../DOMAIN.md";
import architectureDoc from "../../PSTN_CLOUDFLARE.md";

export interface VoiceAgentSkill {
  name: string;
  description: string;
  body: string;
  voiceGuidance: string | null;
}

interface SkillSource extends VoiceAgentSkill {
  references?: Record<string, string>;
}

// A section read at once stays affordable: the largest in DOMAIN.md is about 12k characters.
const MAX_SECTION_CHARS = 16_000;

interface Heading { level: number; title: string; line: number }

// Markdown headings outside code fences, levels 1 to 3.
function headings(text: string): Heading[] {
  let fenced = false;
  return text.split("\n").flatMap((line, index) => {
    if (line.startsWith("```")) fenced = !fenced;
    const match = !fenced && line.match(/^(#{1,3}) (.+)$/);
    return match ? [{ level: match[1].length, title: match[2].trim(), line: index }] : [];
  });
}

function contents(references: Record<string, string>): string {
  return Object.entries(references).map(([file, text]) =>
    `${file}:\n${headings(text).map((heading) => `${"  ".repeat(heading.level - 1)}- ${heading.title}`).join("\n")}`).join("\n\n");
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

function withReferences(skill: VoiceAgentSkill, references: Record<string, string>): SkillSource {
  return { ...skill, references,
    body: `${skill.body}\n\n## References\n\nRead a section with read_voice_agent_skill_reference (skill, file, and the heading's exact text); a heading also covers the headings nested under it.\n\n${contents(references)}` };
}

const VOICE_AGENT_SKILLS: SkillSource[] = [
  parse(preciseTranscription),
  // Self-knowledge flows from the repository's own docs, so it cannot drift from them.
  withReferences(parse(platformSelfKnowledge), { "DOMAIN.md": domainDoc, "PSTN_CLOUDFLARE.md": architectureDoc }),
  parse(firstCallTour),
  parse(pairBrowser),
  parse(whiteboard),
  parse(inviteSomeone),
  parse(deviceAuthorization),
];

export const VOICE_AGENT_SKILL_INDEX = VOICE_AGENT_SKILLS
  .map((skill) => `- ${skill.name}: ${skill.description}`)
  .join("\n");

export function voiceAgentSkill(name: string): VoiceAgentSkill {
  const skill = VOICE_AGENT_SKILLS.find((item) => item.name === name.trim());
  if (!skill) throw new Error(`unknown Voice Agent Skill ${name}; choose one from the index`);
  const { references: _references, ...rest } = skill;
  return rest;
}

// One section of a skill's reference file: its heading through the line before the next heading
// at the same or a higher level.
export function voiceAgentSkillReference(name: string, file: string, section: string): string {
  const skill = VOICE_AGENT_SKILLS.find((item) => item.name === name.trim());
  if (!skill) throw new Error(`unknown Voice Agent Skill ${name}; choose one from the index`);
  const text = skill.references?.[file.trim()];
  if (text === undefined) {
    throw new Error(`${skill.name} has no reference ${file}; its references are ${Object.keys(skill.references ?? {}).join(", ") || "none"}`);
  }
  const all = headings(text);
  const wanted = section.trim().replace(/^#+\s*/, "").toLowerCase();
  const start = all.findIndex((heading) => heading.title.toLowerCase() === wanted);
  if (start < 0) throw new Error(`no heading "${section}" in ${file}; use a heading from the skill's References list`);
  const end = all.slice(start + 1).find((heading) => heading.level <= all[start].level);
  const lines = text.split("\n").slice(all[start].line, end?.line);
  const body = lines.join("\n").trim();
  return body.length > MAX_SECTION_CHARS
    ? `${body.slice(0, MAX_SECTION_CHARS)}\n\n[truncated at ${MAX_SECTION_CHARS} characters: read a narrower heading under this one]`
    : body;
}

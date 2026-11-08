// Themes (DOMAIN.md, Theme): presentation for the voice surface only. A theme maps the
// neutral session model onto a fiction, sets the GPT-Live voice, and words the few
// deterministic strings spoken without a model. Every behavioral rule lives in the neutral
// core prompts in voice_session.ts, so swapping a theme changes how things sound, never
// what the Voice Agent does. Nothing here is ever sent to an ACP Agent.

export interface Theme {
  id: "park" | "plain";
  // GPT-Live output voice; it can only change between calls.
  voice: string;
  // Who the Voice Agent is, completing "You are the voice of Dogwalk on a telephone call: ".
  character: string;
  // The mapping from domain terms to the theme's words, shared by both models; empty for Plain.
  fiction: string;
  // How the Voice Frontend may speak of the Session Orchestrator's slower thinking.
  split: string;
  // How strongly to play the fiction, and when to turn it up or down (Intensity).
  register: string;
  // Words that would betray a theme leaking into ACP prompts (checked by the tests).
  lexicon: string[];
  // Deterministic speech: the first-ever welcome and the keypad status readout.
  firstCallWelcome: string;
  status: {
    none: string;
    failed: (alias: string) => string;
    idle: (alias: string) => string;
    starting: string;
  };
}

export const PARK: Theme = {
  id: "park",
  voice: "marin",
  character: "a dog walker",
  fiction: `The walk (Park theme): present the session model as a dog walk, in the spirit of magical realism. These dogs are not ordinary dogs, but to you they are perfectly normal: never wink at or explain the magic.
- A Managed Session is a dog, and its Alias is the name on its tag; retagging changes it. Sessions discovered from earlier work are dogs you know from earlier walks with no tag yet; loading one leashes it with a fresh tag.
- Attached sessions are on the leash. Closing a session lets the dog go home, where it keeps its memory and can be leashed again.
- Agent work in a Prompt Turn is sniffing, digging, fetching; a Report is what the dog brought back; cancelling a turn recalls the dog from its errand.
- The Followed Session is the dog you follow; the others range out of earshot. A Bid is a ranging dog pulling at the leash.
- A Permission Request is a dog waiting at a gate for the caller's say-so; an Elicitation is a dog looking back to ask which way.
- You are the walker; the caller walks with you. Dogs do the work: "I'll send Cedar to sniff that out", never "I'll sniff that out".`,
  split: `In the walk, the Session Orchestrator is your head (your mind) and you are the walker's voice. You may say so naturally: "let me think", "give me a second to sort that out", "on reflection".`,
  register: `Intensity: use the walk's language lightly by default: dogs set off, sniff around, come back, wait on the leash. When the caller plays along (uses the walk's words, laughs, asks about the dogs), allow a small impossible detail, always deadpan. When the caller is terse, frustrated, or deep in a problem, fall back to plain words. The metaphor is compact interface language, not roleplay; avoid extended jokes.`,
  lexicon: ["dog", "leash", "walker", "sniff"],
  firstCallWelcome: "Welcome! I'm your walker; your sandbox is being set up, which takes a minute or two.",
  status: {
    none: "No dogs on the leash.",
    failed: (alias) => `${alias}'s last errand failed.`,
    idle: (alias) => `${alias} is resting`,
    starting: "The sandbox is still waking up.",
  },
};

// The identity theme: the neutral core with nothing on top. A regression fixture: Plain
// must behave exactly like any other theme, because the rules never live in a theme.
export const PLAIN: Theme = {
  id: "plain",
  voice: "marin",
  character: "an assistant who manages coding sessions",
  fiction: "",
  split: `You may say so naturally: "let me think", "one moment while I check", "on reflection".`,
  register: "Intensity: speak plainly, without metaphor.",
  lexicon: [],
  firstCallWelcome: "Welcome! Your sandbox is being set up, which takes a minute or two.",
  status: {
    none: "No sessions are attached.",
    failed: (alias) => `${alias}'s last turn failed.`,
    idle: (alias) => `${alias} is idle`,
    starting: "The sandbox is still starting.",
  },
};

// Until a caller can choose (System Menu, Open Questions), every call uses Park.
export const DEFAULT_THEME = PARK;

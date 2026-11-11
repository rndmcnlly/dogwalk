// Challenge phrases (DOMAIN.md, Identity Challenge): fresh, short, grammatical sentences a
// caller repeats at once, such as "The golden kettle belongs in the kitchen." Familiar syntax and
// concrete words keep them easy to repeat over a narrowband line; there are no digit strings,
// tongue twisters, or disturbing content. No word is a Handle word, an Invite Code word, or an
// escape word, so a transcript of a phrase is never mistaken for a Handle, an Invite Code, or a
// request to leave. Voice Donations use them now; the Identity Check will.
import { HANDLE_WORDS } from "./handles.ts";
import { EVEN_WORDS, ODD_WORDS } from "./invitations.ts";

/** Words that steer a deterministic speech stage elsewhere, so no phrase may contain them. */
export const ESCAPE_WORDS = ["someone", "else", "invite", "donate", "stop", "yes", "no"];

const RESERVED = new Set([...HANDLE_WORDS, ...EVEN_WORDS, ...ODD_WORDS, ...ESCAPE_WORDS]);

// Each slot is a list of short fillers. A filler is dropped at load if any of its words is
// reserved, so the vocabularies stay disjoint even when one of them changes; the focused test
// (challenge_phrases_test.ts) fails when that happens, so the lists here stay honest.
const SLOTS = {
  color: ["purple", "yellow", "green", "silver", "brown", "white", "blue", "red", "golden", "pink", "gray"],
  thing: ["kettle", "blanket", "notebook", "pillow", "sweater", "bucket", "teapot", "lamp", "mirror", "carpet",
    "toaster", "bicycle", "jacket", "candle", "hammer", "spoon", "towel", "envelope", "stool", "basin", "radio"],
  place: ["in the kitchen", "by the front door", "on the top step", "under the bed", "in the garage",
    "near the window", "in the hallway", "on the porch", "behind the sofa"],
  person: ["My neighbor", "Your cousin", "The teacher", "Our aunt", "The baker", "My sister", "His brother",
    "The mail carrier", "Her grandmother"],
  mood: ["sleepy", "small", "curious", "young", "quiet", "chubby", "patient", "fluffy"],
  creature: ["beaver", "sparrow", "kitten", "pony", "duck", "hamster", "goose", "puffin", "beagle"],
  moved: ["walked", "wandered", "hurried", "raced", "strolled", "trotted"],
  area: ["garden", "parking lot", "meadow", "bridge", "beach", "playground", "orchard", "sidewalk"],
  furniture: ["table", "counter", "drawer", "cupboard", "closet", "windowsill"],
  state: ["warm", "clean", "empty", "open", "dusty", "soaking wet", "upside down"],
  time: ["this morning", "last night", "after lunch", "on Sunday", "before dinner"],
  surface: ["porch", "mailbox", "garden gate", "bookcase", "front steps", "kitchen chairs"],
  season: ["summer", "spring", "winter", "autumn"],
} as const;
type Slot = keyof typeof SLOTS;

const TEMPLATES = [
  "The {color} {thing} belongs {place}.",
  "{person} left the {thing} {place}.",
  "A {mood} {creature} {moved} across the {area}.",
  "Please hand me the {color} {thing} from the {furniture}.",
  "The {thing} was {state} {time}.",
  "{person} painted the {surface} {color} last {season}.",
  "We found a {color} {thing} {place}.",
];

const usable = (filler: string) => filler.toLowerCase().split(/\s+/).every((word) => !RESERVED.has(word));
export const PHRASE_SLOTS = Object.fromEntries(
  Object.entries(SLOTS).map(([slot, fillers]) => [slot, fillers.filter(usable)]),
) as Record<Slot, string[]>;
export const PHRASE_TEMPLATES = TEMPLATES;

function pick<T>(items: readonly T[]): T {
  return items[crypto.getRandomValues(new Uint32Array(1))[0] % items.length];
}

/** One fresh challenge phrase. */
export function challengePhrase(): string {
  return pick(TEMPLATES).replace(/\{(\w+)\}/g, (_, slot: Slot) => pick(PHRASE_SLOTS[slot]));
}

/** Distinct fresh challenge phrases. */
export function challengePhrases(count: number): string[] {
  const phrases: string[] = [];
  for (let attempt = 0; attempt < count * 20 && phrases.length < count; attempt++) {
    const phrase = challengePhrase();
    if (phrases.includes(phrase)) continue;
    phrases.push(phrase);
  }
  return phrases;
}

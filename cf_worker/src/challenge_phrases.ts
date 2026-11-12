// Challenge phrases (DOMAIN.md, Identity Challenge): fresh, short, grammatical sentences a
// caller repeats at once, such as "The golden kettle belongs in the kitchen." Familiar syntax and
// concrete words keep them easy to repeat over a narrowband line; there are no digit strings,
// tongue twisters, or disturbing content. No word is a Handle word, an Invite Code word, or an
// escape word, so a transcript of a phrase is never mistaken for a Handle, an Invite Code, or a
// request to leave. Voice Donations, Challenge Practice, and the Identity Check use them.
import { HANDLE_WORDS } from "./handles.ts";
import { EVEN_WORDS, ODD_WORDS } from "./invitations.ts";

/** Words that steer a deterministic speech stage elsewhere, so no phrase may contain them. */
export const ESCAPE_WORDS = ["someone", "else", "invite", "donate", "stop", "yes", "no", "repeat", "again", "pardon", "what"];

const RESERVED = new Set([...HANDLE_WORDS, ...EVEN_WORDS, ...ODD_WORDS, ...ESCAPE_WORDS]);

// Each slot is a list of short fillers. A filler is dropped at load if any of its words is
// reserved, so the vocabularies stay disjoint even when one of them changes; the focused test
// (challenge_phrases_test.ts) fails when that happens, so the lists here stay honest.
const SLOTS = {
  color: ["purple", "yellow", "green", "silver", "brown", "white", "blue", "red", "golden", "pink", "gray"],
  thing: ["kettle", "blanket", "notebook", "pillow", "sweater", "bucket", "teapot", "lamp", "mirror", "carpet",
    "toaster", "bicycle", "jacket", "candle", "hammer", "spoon", "envelope", "stool", "basin", "radio"],
  place: ["in the kitchen", "by the front door", "on the top step", "under the bed", "in the garage",
    "near the window", "in the hallway", "on the porch", "behind the sofa"],
  person: ["My neighbor", "Your cousin", "The teacher", "Our friend", "The baker", "My sister", "His brother",
    "The mail carrier", "Her grandmother"],
  mood: ["sleepy", "small", "curious", "young", "quiet", "chubby", "patient", "fluffy"],
  creature: ["beaver", "sparrow", "kitten", "pony", "duck", "hamster", "goose", "puffin", "beagle"],
  moved: ["walked", "wandered", "hurried", "raced", "strolled", "trotted"],
  area: ["garden", "parking lot", "meadow", "bridge", "pathway", "playground", "orchard", "sidewalk"],
  furniture: ["table", "counter", "drawer", "cupboard", "closet", "windowsill"],
  state: ["warm", "clean", "empty", "open", "dusty", "soaking wet", "upside down"],
  time: ["this morning", "last night", "after lunch", "on Sunday", "before dinner"],
  surface: ["porch", "mailbox", "garden gate", "bookcase", "front steps", "kitchen chairs"],
  season: ["summer", "spring", "winter", "autumn"],
} as const;
type Slot = keyof typeof SLOTS;

// Eight to eleven words: a brisk repetition then lasts about 2.5 s or more, enough for the
// verifier to measure (MIN_SPEECH_SECONDS in challenge_rounds.ts) even where a noisy line
// hides the quieter syllables, while each phrase stays one easy breath.
const TEMPLATES = [
  "The old {color} {thing} belongs {place}.",
  "{person} left the {color} {thing} {place}.",
  "A {mood} {creature} {moved} across the {area} {time}.",
  "Please hand me the {color} {thing} from the {furniture}.",
  "The {color} {thing} was still {state} {time}.",
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

// --- phrase match -------------------------------------------------------

// Articles, possessives, and auxiliaries: a recognizer drops or swaps them freely, and they
// carry no evidence of which phrase was said. Everything else, prepositions included, counts:
// "beside the sofa" is not "behind the sofa".
const FUNCTION_WORDS = new Set(["a", "an", "the", "my", "your", "our", "his", "her", "me", "we", "was", "this"]);

const normalized = (raw: string) => raw.toLowerCase().replace(/[^a-z\s]/g, " ").split(/\s+/).filter(Boolean)
  .map((word) => (word === "grey" ? "gray" : word));

// Only plural inflection is forgiven: "kettles" for "kettle", "boxes" for "box".
const sameWord = (wanted: string, heard: string) =>
  heard === wanted || heard === `${wanted}s` || heard === `${wanted}es` || wanted === `${heard}s` || wanted === `${heard}es`;

/** The words of a phrase that show it was repeated: everything but articles, possessives, and auxiliaries. */
export function contentWords(phrase: string): string[] {
  return normalized(phrase).filter((word) => !FUNCTION_WORDS.has(word));
}

/**
 * Whether a speech transcript repeats the phrase: every content word appears, in order, with
 * plural inflection forgiven and extra words between them allowed ("from behind the closet"
 * still repeats "from the closet"). One content word may also arrive split in two ("window
 * sill" for "windowsill"). A different word fails, whether the caller misheard the phrase or
 * the recognizer misheard the caller, and the round is a wrong phrase, never a voice failure.
 * Deterministic, and shared by Challenge Practice and the Identity Check.
 */
export function phraseMatches(phrase: string, transcript: string): boolean {
  const wanted = contentWords(phrase);
  const heard = normalized(transcript);
  let at = 0;
  for (const word of wanted) {
    let found = -1;
    for (let i = at; i < heard.length && found < 0; i++) {
      if (sameWord(word, heard[i])) found = i + 1;
      else if (i + 1 < heard.length && sameWord(word, heard[i] + heard[i + 1])) found = i + 2;
    }
    if (found < 0) return false;
    at = found;
  }
  return wanted.length > 0;
}

/** Short requests to hear the phrase again, which replay it without counting a round. */
const REPEAT_WORDS = new Set(["repeat", "again", "pardon", "what"]);
export const askedToRepeat = (heard: string[]) => heard.length > 0 && heard.length <= 4 && heard.some((word) => REPEAT_WORDS.has(word));

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

// node --experimental-strip-types cf_worker/challenge_phrases_test.ts
import assert from "node:assert/strict";
import { askedToRepeat, challengePhrases, contentWords, ESCAPE_WORDS, PHRASE_SLOTS, PHRASE_TEMPLATES, phraseMatches } from "./src/challenge_phrases.ts";
import { HANDLE_WORDS } from "./src/handles.ts";
import { EVEN_WORDS, ODD_WORDS } from "./src/invitations.ts";

const reserved = new Set([...HANDLE_WORDS, ...EVEN_WORDS, ...ODD_WORDS, ...ESCAPE_WORDS]);
// The written lists must already avoid reserved words: the load-time filter is a guard, not an editor.
const source = await import("node:fs").then((fs) => fs.readFileSync(new URL("./src/challenge_phrases.ts", import.meta.url), "utf8"));
for (const [slot, fillers] of Object.entries(PHRASE_SLOTS)) {
  assert.ok(fillers.length >= 4, `slot ${slot} has too few fillers`);
  for (const filler of fillers) assert.ok(source.includes(`"${filler}"`));
}
const written = [...source.matchAll(/^\s+(\w+): \[([^\]]+)\]/gm)];
for (const [, slot, list] of written) {
  const fillers = [...list.matchAll(/"([^"]+)"/g)].map((match) => match[1]);
  assert.deepEqual(PHRASE_SLOTS[slot as keyof typeof PHRASE_SLOTS], fillers, `slot ${slot} lists a reserved word`);
}
for (const template of PHRASE_TEMPLATES) {
  for (const word of template.replace(/\{\w+\}/g, "").toLowerCase().match(/[a-z]+/g) ?? []) {
    assert.ok(!reserved.has(word), `template word ${word} is reserved`);
  }
}
for (let round = 0; round < 200; round++) {
  const phrases = challengePhrases(8);
  assert.equal(new Set(phrases).size, 8);
  for (const phrase of phrases) {
    assert.match(phrase, /^[A-Z][A-Za-z ]+\.$/, phrase);
    const words = phrase.toLowerCase().replace(/[^a-z ]/g, "").split(" ");
    assert.ok(words.length >= 8 && words.length <= 11, phrase);
    for (const word of words) assert.ok(!reserved.has(word), `${phrase}: ${word}`);
    // Every phrase carries enough content words for a transcript match to mean something.
    assert.ok(contentWords(phrase).length >= 3, `${phrase}: too few content words`);
    assert.ok(phraseMatches(phrase, phrase.toUpperCase().replace(".", "")), phrase);
    // Dropping any one content word fails.
    for (const missing of contentWords(phrase)) {
      const without = phrase.toLowerCase().replace(".", "").split(" ").filter((word) => word !== missing).join(" ");
      assert.ok(!phraseMatches(phrase, without), `${phrase} matched without ${missing}`);
    }
  }
}

const cases: [string, string, boolean][] = [
  // Real pairs from a car Voice Donation (prompt, Twilio transcript).
  ["A fluffy sparrow strolled across the sidewalk.", "A fluffy sperl strolled across the sidewalk.", false],
  ["We found a gray spoon behind the sofa.", "We found a gray spoon beside the sofa.", false],
  ["Please hand me the golden kettle from the closet.", "Please hand me the golden Kettle from behind the closet.", true],
  ["Please hand me the golden towel from the drawer.", "Please hand me the golden cow from the drawer.", false],
  // Synthetic.
  ["The golden kettle belongs in the kitchen.", "the golden kettles belong in the kitchen", true],
  ["The golden kettle belongs in the kitchen.", "golden kettle belongs in a kitchen", true],
  ["The golden kettle belongs in the kitchen.", "the golden kettle belongs in the kitchen um yes", true],
  ["The golden kettle belongs in the kitchen.", "the kettle golden belongs in the kitchen", false],
  ["The golden kettle belongs in the kitchen.", "the golden cattle belongs in the kitchen", false],
  ["The golden kettle belongs in the kitchen.", "the golden kettle belongs on the kitchen", false],
  ["Please hand me the gray stool from the windowsill.", "please hand me the grey stool from the window sill", true],
  ["Please hand me the gray stool from the windowsill.", "please hand me the gray stools from the window", false],
  ["The towel was warm this morning.", "the towel is warm this morning", true],
  ["The towel was warm this morning.", "the towel was warm this mourning", false],
  ["The towel was warm this morning.", "", false],
  ["My neighbor left the candle in the hallway.", "my neighbors left the candles in the hallway", true],
];
for (const [phrase, transcript, expected] of cases) {
  assert.equal(phraseMatches(phrase, transcript), expected, `${phrase} / ${transcript}`);
}

for (const request of ["Repeat.", "Say that again.", "Again?", "What?", "Pardon?", "can you repeat that"]) {
  assert.ok(askedToRepeat(request.toLowerCase().replace(/[^a-z ]/g, "").split(" ").filter(Boolean)), request);
}
for (const other of ["", "what a lovely golden kettle in the kitchen", "the golden kettle"]) {
  assert.ok(!askedToRepeat(other.split(" ").filter(Boolean)), other);
}
console.log(challengePhrases(8).join("\n"));

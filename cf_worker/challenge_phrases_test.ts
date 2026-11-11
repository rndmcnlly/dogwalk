// node --experimental-strip-types cf_worker/challenge_phrases_test.ts
import assert from "node:assert/strict";
import { challengePhrases, ESCAPE_WORDS, PHRASE_SLOTS, PHRASE_TEMPLATES } from "./src/challenge_phrases.ts";
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
    assert.ok(words.length >= 4 && words.length <= 10, phrase);
    for (const word of words) assert.ok(!reserved.has(word), `${phrase}: ${word}`);
  }
}
console.log(challengePhrases(8).join("\n"));

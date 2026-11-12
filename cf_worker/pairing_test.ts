// node --experimental-strip-types cf_worker/pairing_test.ts
import assert from "node:assert/strict";
import { newPairingCode, normalizePairingCode, PAIRING_EVEN_WORDS, PAIRING_ODD_WORDS } from "./src/pairing.ts";

// Vocabulary: two complete PGP lists, so the two words carry 16 secret bits. A spoken word
// must be unambiguous, so no word repeats within or across the lists.
for (const list of [PAIRING_ODD_WORDS, PAIRING_EVEN_WORDS]) {
  assert.equal(list.length, 256);
  assert.ok(list.every((word) => /^[a-z]+$/.test(word)), "words are lowercase letters only");
}
assert.equal(new Set([...PAIRING_ODD_WORDS, ...PAIRING_EVEN_WORDS]).size, 512, "no word repeats");

// Generation: number-word-word, odd list first, a 1–999 Nameplate.
const odd = new Set(PAIRING_ODD_WORDS), even = new Set(PAIRING_EVEN_WORDS);
const nameplates = new Set<string>();
for (let i = 0; i < 2000; i++) {
  const { code, nameplate } = newPairingCode();
  const [number, first, second, ...rest] = code.split("-");
  assert.equal(rest.length, 0, code);
  assert.equal(number, nameplate);
  assert.ok(Number(nameplate) >= 1 && Number(nameplate) <= 999 && String(Number(nameplate)) === nameplate, code);
  assert.ok(odd.has(first) && even.has(second), code);
  nameplates.add(nameplate);
}
assert.ok(nameplates.size > 800, "Nameplates spread over the range");

// Normalization: spaces or hyphens, any case, leading zeros; the shape, not the words, is checked.
const expected = { nameplate: "42", code: "42-adroitness-aardvark" };
for (const raw of ["42-adroitness-aardvark", "42 adroitness aardvark", " 42 - Adroitness - AARDVARK ", "042 adroitness aardvark"]) {
  assert.deepEqual(normalizePairingCode(raw), expected, raw);
}
// Unknown words still normalize: they count as guesses against the code's attempt budget.
assert.deepEqual(normalizePairingCode("7 banana split"), { nameplate: "7", code: "7-banana-split" });
for (const raw of ["", "0 adroitness aardvark", "1000 adroitness aardvark", "adroitness aardvark", "42 adroitness", "42 adroitness aardvark extra", "42 adroit-ness aardvark", "4.2 a b"]) {
  assert.equal(normalizePairingCode(raw), null, raw);
}

console.log("pairing: vocabulary, generation, and normalization checks passed");

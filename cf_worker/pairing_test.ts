// Run: node --experimental-strip-types cf_worker/pairing_test.ts
import assert from "node:assert/strict";
import {
  newPairingCode, normalizePairingCode, PAIRING_EVEN_WORDS, PAIRING_ODD_WORDS,
  PAIRING_CODE_TTL_MS, PAIRING_MAX_ATTEMPTS,
} from "./src/pairing.ts";

for (const words of [PAIRING_EVEN_WORDS, PAIRING_ODD_WORDS]) {
  assert.equal(words.length, 256);
  assert.equal(new Set(words).size, 256);
}
assert.equal(new Set([...PAIRING_EVEN_WORDS, ...PAIRING_ODD_WORDS]).size, 512);
assert.equal(PAIRING_CODE_TTL_MS, 90_000);
assert.equal(PAIRING_MAX_ATTEMPTS, 5);
for (let i = 0; i < 1000; i++) {
  const generated = newPairingCode();
  const [number, odd, even] = generated.code.split("-");
  assert.equal(number, generated.nameplate);
  assert.ok(Number(number) >= 1 && Number(number) <= 999);
  assert.ok(PAIRING_ODD_WORDS.includes(odd));
  assert.ok(PAIRING_EVEN_WORDS.includes(even));
  assert.deepEqual(normalizePairingCode(generated.code), generated);
}
assert.deepEqual(normalizePairingCode(" 007 Adroitness AARDVARK "), {
  nameplate: "7", code: "7-adroitness-aardvark",
});
assert.deepEqual(normalizePairingCode("7-wrong-guess"), { nameplate: "7", code: "7-wrong-guess" });
for (const invalid of ["", "0-adroitness-aardvark", "1000-adroitness-aardvark", "7-one", "7-one-two-three", "abcdef0123456789abcd"]) {
  assert.equal(normalizePairingCode(invalid), null);
}
console.log("pairing code tests passed");

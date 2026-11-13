// node --experimental-strip-types cf_worker/invitations_match_test.ts
import assert from "node:assert/strict";
import { findInviteCode } from "./src/invitations.ts";

const codes = ["breadline stethoscope atlas", "scallion retrospect blowtorch"];
const heard = (text: string) => text.split(" ");

// Exactly the code.
assert.deepEqual(findInviteCode(heard("breadline stethoscope atlas"), codes), { code: codes[0], how: "exact" });
// Speech recognition splits compounds: the 2026-11-12 failure, four words for three.
assert.deepEqual(findInviteCode(heard("bread line stethoscope atlas"), codes), { code: codes[0], how: "joined" });
assert.deepEqual(findInviteCode(heard("scallion retro spect blow torch"), codes), { code: codes[1], how: "joined" });
// Words around the code.
assert.deepEqual(findInviteCode(heard("um my words are breadline stethoscope atlas thanks"), codes), { code: codes[0], how: "exact" });
// A misheard letter or two, when only one code is that close.
assert.deepEqual(findInviteCode(heard("stallion retrospect blowtorch"), codes), { code: codes[1], how: "near" });
assert.deepEqual(findInviteCode(heard("bread lines stethoscope at las"), codes), { code: codes[0], how: "near" });
// Not close enough, or a different code altogether.
assert.deepEqual(findInviteCode(heard("breadline atlas"), codes), { code: null, nearest: null });
const far = findInviteCode(heard("brickyard stethoscope apple"), codes);
assert.equal(far.code, null);
assert.ok(far.code === null && far.nearest !== null && far.nearest > 2, JSON.stringify(far));
assert.deepEqual(findInviteCode([], codes), { code: null, nearest: null });
assert.deepEqual(findInviteCode(heard("breadline stethoscope atlas"), []), { code: null, nearest: null });
// Two codes within reach is no match, even when one is closer: the caller is asked again
// rather than guessed for.
const twins = ["absurd adviser adrift", "absurd adviser adult"];
assert.equal(findInviteCode(heard("absurd adviser adrit"), twins).code, null);
assert.equal(findInviteCode(heard("absurd adviser adr"), twins).code, null);
// With only one of them live, the same near miss matches.
assert.equal(findInviteCode(heard("absurd adviser adrit"), [twins[0]]).code, twins[0]);

console.log("invite matching: exact, split compounds, surrounding words, near misses, and ambiguity checks passed");

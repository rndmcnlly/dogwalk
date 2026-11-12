// node --experimental-strip-types cf_worker/endpointer_test.ts
import assert from "node:assert/strict";
import { AnswerEndpointer, type AnswerEvent, SAMPLE_RATE } from "./src/caller_stream.ts";

// G.711 mu-law, the encoding Twilio streams.
function ulaw(sample: number): number {
  const sign = sample < 0 ? 0x80 : 0;
  let magnitude = Math.min(Math.abs(sample), 32635) + 0x84;
  let exponent = 7;
  for (let mask = 0x4000; (magnitude & mask) === 0 && exponent > 0; mask >>= 1) exponent--;
  const mantissa = (magnitude >> (exponent + 3)) & 0x0f;
  return ~(sign | (exponent << 4) | mantissa) & 0xff;
}

// A deterministic line: low noise (about 26 dB, like the car donation's floor) under whatever is said.
let seed = 1;
const noise = () => ((seed = (seed * 1103515245 + 12345) % 2 ** 31) / 2 ** 31 - 0.5) * 70;
type Part = { ms: number; voice?: number }; // voice: amplitude of a 150 Hz buzz, or silence
function audio(parts: Part[]): Uint8Array {
  const bytes: number[] = [];
  for (const part of parts) {
    for (let i = 0; i < SAMPLE_RATE * part.ms / 1000; i++) {
      const voice = part.voice ? part.voice * Math.sin(2 * Math.PI * 150 * i / SAMPLE_RATE) : 0;
      bytes.push(ulaw(Math.round(voice + noise())));
    }
  }
  return Uint8Array.from(bytes);
}
// Syllables of 200 ms with 60 ms dips between them, like running speech.
const words = (ms: number, voice = 3000): Part[] =>
  Array.from({ length: Math.round(ms / 260) }, () => [{ ms: 200, voice }, { ms: 60 }]).flat();
const ms = (event: AnswerEvent) => Math.round(event.at * 1000 / SAMPLE_RATE);

// Fed in Twilio's 20 ms frames, as the stream receives it.
function run(parts: Part[]): AnswerEvent[] {
  const endpointer = new AnswerEndpointer(), bytes = audio(parts), events: AnswerEvent[] = [];
  for (let i = 0; i < bytes.length; i += 160) events.push(...endpointer.push(bytes.subarray(i, i + 160)));
  return events;
}

// The pre-answer line (with the round tone's echo), a sentence with a 400 ms pause inside it,
// then silence: one end, 700 ms after the last syllable.
const prompt: Part[] = [{ ms: 500 }, { ms: 150, voice: 4000 }, { ms: 3000 }];
const sentence = [...words(1560), { ms: 400 }, ...words(1040)];
const lastSyllable = 3650 + (1560 + 400 + 1040) - 60;
let events = run([...prompt, ...sentence, { ms: 1500 }]);
assert.equal(events.length, 1, JSON.stringify(events));
assert.equal(events[0].kind, "end");
assert.ok(Math.abs(ms(events[0]) - (lastSyllable + 700)) <= 40, `end at ${ms(events[0])}, last syllable ${lastSyllable}`);

// Talking again after the end is a resume, and stopping again a second end.
events = run([...prompt, ...sentence, { ms: 900 }, ...words(780), { ms: 1000 }]);
assert.deepEqual(events.map((event) => event.kind), ["end", "resume", "end"]);

// The end does not wait for the stream: the second end still needs its own 700 ms.
events = run([...prompt, ...sentence, { ms: 900 }, ...words(780), { ms: 300 }]);
assert.deepEqual(events.map((event) => event.kind), ["end", "resume"]);

// Under a second of speech ("um") before a long pause is not an answer.
events = run([...prompt, ...words(520), { ms: 2000 }]);
assert.deepEqual(events, []);
// ...but it does not stop the answer that follows from ending.
events = run([...prompt, ...words(520), { ms: 1200 }, ...sentence, { ms: 1000 }]);
assert.deepEqual(events.map((event) => event.kind), ["end"]);

// A digitally silent line (echo cancellation can zero it) has a floor of 0 dB, so the absolute
// level decides: a faint hum is not speech, a sentence still ends.
const silent = (ms: number) => new Uint8Array(SAMPLE_RATE * ms / 1000).fill(ulaw(0));
const quiet = new AnswerEndpointer();
assert.deepEqual([silent(2000), audio([{ ms: 1500, voice: 60 }]), silent(1000)].flatMap((chunk) => quiet.push(chunk)), []);
const clean = new AnswerEndpointer();
assert.deepEqual([silent(2000), audio(sentence), silent(1000)].flatMap((chunk) => clean.push(chunk)).map((event) => event.kind), ["end"]);

// A steady level from the first frame (no quiet line to learn from, as in the smoke test's
// constant streams) never ends an answer, so practice falls back to scoring at the stop.
assert.deepEqual(new AnswerEndpointer().push(new Uint8Array(SAMPLE_RATE * 3).fill(ulaw(8000))), []);

console.log("endpointer checks passed");

// Paired Browser (DOMAIN.md): the server side of acoustic pairing.
//
// The System Menu plays a Pairing Code into the call as keypad (DTMF) tones; a browser in
// earshot decodes it (src/pair_page.ts) and claims it. The code is 80 random bits plus six
// Reed-Solomon parity bytes over GF(256), each byte sent as two 4-bit DTMF symbols: 32
// symbols on a fixed clock. The Voice Agent picks the symbol rate from PAIRING_BAUDS (8 by
// default: a 125 ms slot, four seconds in all). Each tone is short (60% of its slot, at most
// 70 ms) and the rest of the slot is silence, so slower means longer gaps, not longer tones.
// Speech codecs (G.711, G.723.1, low-rate Opus) pass even back-to-back 16 baud intact; what
// breaks pairing is the room. A held tone's reverberation builds up to rival the next tone,
// and a gap lets it decay. In a simulated reverberant room at 8 baud, 125 ms tones with no
// gap decoded 1 frame in 6 and 70 ms tones with 55 ms gaps 10 in 10. The code is
// systematic (the first 20 symbols are the data); the receiver tries each allowed rate,
// runs a symbol clock, and treats unclear slots as erasures.

import { SAMPLE_RATE, toBase64, type Clip } from "./audio";

export const PAIRING_CODE_BYTES = 10;
export const PAIRING_PARITY_BYTES = 6;
export const PAIRING_BAUDS = [4, 6, 8, 12, 16] as const;
export type PairingBaud = (typeof PAIRING_BAUDS)[number];
export const PAIRING_DEFAULT_BAUD: PairingBaud = 8;
// How much of each symbol slot the tone fills, at most; the rest is silence for echoes to decay.
export const PAIRING_TONE_DUTY = 0.6;
export const PAIRING_MAX_TONE_MS = 70;
export const PAIRING_CODE_TTL_MS = 90_000;
export const PAIRING_CONFIRM_MS = 30_000;
export const PAIRED_IDLE_MS = 60 * 60_000;
export const PAIRED_MAX_MS = 12 * 60 * 60_000;

const ROWS = [697, 770, 852, 941];
const COLS = [1209, 1336, 1477, 1633];
const KEYPAD = ["123A", "456B", "789C", "*0#D"];
const ALPHABET = "0123456789ABCD*#"; // nibble value -> keypad symbol

// --- Reed-Solomon encoder over GF(256), primitive polynomial 0x11d, generator roots 2^0..2^(n-1).
const GF_EXP = new Uint8Array(512);
const GF_LOG = new Uint8Array(256);
{
  let x = 1;
  for (let i = 0; i < 255; i++) {
    GF_EXP[i] = x;
    GF_LOG[x] = i;
    x <<= 1;
    if (x & 0x100) x ^= 0x11d;
  }
  for (let i = 255; i < 512; i++) GF_EXP[i] = GF_EXP[i - 255];
}
const gfMul = (a: number, b: number) => (a && b ? GF_EXP[GF_LOG[a] + GF_LOG[b]] : 0);

function polyMul(p: number[], q: number[]): number[] {
  const r = new Array<number>(p.length + q.length - 1).fill(0);
  for (let j = 0; j < q.length; j++) for (let i = 0; i < p.length; i++) r[i + j] ^= gfMul(p[i], q[j]);
  return r;
}

export function rsEncode(message: number[], parity: number): number[] {
  let generator = [1];
  for (let i = 0; i < parity; i++) generator = polyMul(generator, [1, GF_EXP[i]]);
  const out = [...message, ...new Array<number>(parity).fill(0)];
  for (let i = 0; i < message.length; i++) {
    const coef = out[i];
    if (coef) for (let j = 1; j < generator.length; j++) out[i + j] ^= gfMul(generator[j], coef);
  }
  return [...message, ...out.slice(message.length)];
}

export function pairingSymbols(code: Uint8Array): string {
  return rsEncode([...code], PAIRING_PARITY_BYTES).map((b) => ALPHABET[b >> 4] + ALPHABET[b & 15]).join("");
}

// The tones as a GPT-Live output fixture: mono PCM16 at 24 kHz, with a little silence around
// them so the receiver's clock sees a clean start. Raised-cosine edges keep clicks from
// splattering energy into the neighboring DTMF bins.
export function pairingTones(code: Uint8Array, baud: PairingBaud = PAIRING_DEFAULT_BAUD, amplitude = 0.3): Clip {
  const symbols = pairingSymbols(code);
  const slot = SAMPLE_RATE / baud;
  const lead = Math.round(0.3 * SAMPLE_RATE);
  const ramp = Math.round(0.004 * SAMPLE_RATE);
  const tone = Math.round(Math.min(slot * PAIRING_TONE_DUTY, (PAIRING_MAX_TONE_MS / 1000) * SAMPLE_RATE));
  const samples = new Int16Array(2 * lead + Math.ceil(symbols.length * slot));
  [...symbols].forEach((symbol, s) => {
    const row = KEYPAD.findIndex((line) => line.includes(symbol));
    const fr = ROWS[row];
    const fc = COLS[KEYPAD[row].indexOf(symbol)];
    const start = lead + Math.round(s * slot);
    for (let n = 0; n < tone; n++) {
      const edge = Math.min(n, tone - 1 - n);
      const envelope = edge < ramp ? 0.5 - 0.5 * Math.cos((Math.PI * edge) / ramp) : 1;
      const wave = Math.sin((2 * Math.PI * fr * n) / SAMPLE_RATE) + Math.sin((2 * Math.PI * fc * n) / SAMPLE_RATE);
      samples[start + n] = Math.round(amplitude * envelope * wave * 32767);
    }
  });
  return { base64: toBase64(new Uint8Array(samples.buffer)), seconds: samples.length / SAMPLE_RATE };
}

export function newPairingCode(): { bytes: Uint8Array; hex: string } {
  const bytes = crypto.getRandomValues(new Uint8Array(PAIRING_CODE_BYTES));
  return { bytes, hex: toHex(bytes) };
}

export function toHex(bytes: Uint8Array): string {
  return [...bytes].map((b) => b.toString(16).padStart(2, "0")).join("");
}

export async function sha256Hex(text: string): Promise<string> {
  return toHex(new Uint8Array(await crypto.subtle.digest("SHA-256", new TextEncoder().encode(text))));
}

// Browser credentials name the Durable Object they belong to, so the Worker can route them;
// only the secret half is checked, by hash, inside that object.
export function newBrowserSecret(): string {
  return toHex(crypto.getRandomValues(new Uint8Array(24)));
}

export function splitCredential(credential: string | null): { identity: string; secret: string } | null {
  const match = /^([0-9a-f]{16,128})\.([0-9a-f]{32,128})$/.exec(credential ?? "");
  return match ? { identity: match[1], secret: match[2] } : null;
}

// Speakable descriptions for the confirmation: what kind of browser, and roughly where.
export function describeBrowser(userAgent: string): string {
  const ua = userAgent || "";
  const browser = /Edg\//.test(ua) ? "Edge" : /Firefox\//.test(ua) ? "Firefox"
    : /Chrome\//.test(ua) ? "Chrome" : /Safari\//.test(ua) ? "Safari" : "a browser";
  const device = /iPad/.test(ua) ? "an iPad" : /iPhone/.test(ua) ? "an iPhone" : /Android/.test(ua) ? "an Android device"
    : /Mac OS X/.test(ua) ? "a Mac" : /Windows/.test(ua) ? "a Windows computer" : /Linux/.test(ua) ? "a Linux computer" : "an unknown device";
  return `${browser} on ${device}`;
}

export function describePlace(cf: { city?: unknown; region?: unknown; country?: unknown } | undefined): string {
  const parts = [cf?.city, cf?.region].filter((part): part is string => typeof part === "string" && part.length > 0);
  if (parts.length) return `near ${parts.join(", ")}`;
  return typeof cf?.country === "string" ? `in ${cf.country}` : "from an unknown location";
}

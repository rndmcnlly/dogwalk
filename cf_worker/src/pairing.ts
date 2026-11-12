// Spoken Pairing Codes: a public rendezvous number and 16 secret bits. Not PAKE.
// HTTPS, expiry, bounded guesses, and independent caller approval remain required.
export const PAIRING_CODE_TTL_MS = 90_000;
export const PAIRING_CONFIRM_MS = 30_000;
export const PAIRING_MAX_ATTEMPTS = 5;
export const PAIRING_GLOBAL_ATTEMPTS_PER_MINUTE = 120;
export const PAIRED_IDLE_MS = 60 * 60_000;
export const PAIRED_MAX_MS = 12 * 60 * 60_000;

// Complete PGP lists in byte order, lowercased. Like Magic Wormhole, use odd
// then even. Do not filter: each list must retain all 256 entries for 16 bits.
// Source: https://github.com/magic-wormhole/magic-wormhole/blob/master/src/wormhole/_wordlist.py
export const PAIRING_EVEN_WORDS = (`
aardvark absurd accrue acme adrift adult afflict ahead aimless algol allow alone ammo ancient apple artist
assume athens atlas aztec baboon backfield backward banjo beaming bedlamp beehive beeswax befriend belfast berserk billiard
bison blackjack blockade blowtorch bluebird bombast bookshelf brackish breadline breakup brickyard briefcase burbank button buzzard cement
chairlift chatter checkup chisel choking chopper christmas clamshell classic classroom cleanup clockwork cobra commence concert cowbell
crackdown cranky crowfoot crucial crumpled crusade cubic dashboard deadbolt deckhand dogsled dragnet drainage dreadful drifter dropper
drumbeat drunken dupont dwelling eating edict egghead eightball endorse endow enlist erase escape exceed eyeglass eyetooth
facial fallout flagpole flatfoot flytrap fracture framework freedom frighten gazelle geiger glitter glucose goggles goldfish gremlin
guidance hamlet highchair hockey indoors indulge inverse involve island jawbone keyboard kickoff kiwi klaxon locale lockup
merit minnow miser mohawk mural music necklace neptune newborn nightbird oakland obtuse offload optic orca payday
peachy pheasant physique playhouse pluto preclude prefer preshrunk printer prowler pupil puppy python quadrant quiver quota
ragtime ratchet rebirth reform regain reindeer rematch repay retouch revenge reward rhythm ribcage ringbolt robust rocker
ruffled sailboat sawdust scallion scenic scorecard scotland seabird select sentence shadow shamrock showgirl skullcap skydive slingshot
slowdown snapline snapshot snowcap snowslide solo southward soybean spaniel spearhead spellbind spheroid spigot spindle spyglass stagehand
stagnate stairway standard stapler steamship sterling stockman stopwatch stormy sugar surmount suspense sweatband swelter tactics talon
tapeworm tempest tiger tissue tonic topmost tracker transit trauma treadmill trojan trouble tumor tunnel tycoon uncut
unearth unwind uproot upset upshot vapor village virus vulcan waffle wallet watchword wayside willow woodlark zulu
`).trim().split(/\s+/);
export const PAIRING_ODD_WORDS = (`
adroitness adviser aftermath aggregate alkali almighty amulet amusement antenna applicant apollo armistice article asteroid atlantic atmosphere
autopsy babylon backwater barbecue belowground bifocals bodyguard bookseller borderline bottomless bradbury bravado brazilian breakaway burlington businessman
butterfat camelot candidate cannonball capricorn caravan caretaker celebrate cellulose certify chambermaid cherokee chicago clergyman coherence combustion
commando company component concurrent confidence conformist congregate consensus consulting corporate corrosion councilman crossover crucifix cumbersome customer
dakota decadence december decimal designing detector detergent determine dictator dinosaur direction disable disbelief disruptive distortion document
embezzle enchanting enrollment enterprise equation equipment escapade eskimo everyday examine existence exodus fascinate filament finicky forever
fortitude frequency gadgetry galveston getaway glossary gossamer graduate gravity guitarist hamburger hamilton handiwork hazardous headwaters hemisphere
hesitate hideaway holiness hurricane hydraulic impartial impetus inception indigo inertia infancy inferno informant insincere insurgent integrate
intention inventive istanbul jamaica jupiter leprosy letterhead liberty maritime matchmaker maverick medusa megaton microscope microwave midsummer
millionaire miracle misnomer molasses molecule montana monument mosquito narrative nebula newsletter norwegian october ohio onlooker opulent
orlando outfielder pacific pandemic pandora paperweight paragon paragraph paramount passenger pedigree pegasus penetrate perceptive performance pharmacy
phonetic photograph pioneer pocketful politeness positive potato processor provincial proximate puberty publisher pyramid quantity racketeer rebellion
recipe recover repellent replica reproduce resistor responsive retraction retrieval retrospect revenue revival revolver sandalwood sardonic saturday
savagery scavenger sensation sociable souvenir specialist speculate stethoscope stupendous supportive surrender suspicious sympathy tambourine telephone therapist
tobacco tolerance tomorrow torpedo tradition travesty trombonist truncated typewriter ultimate undaunted underfoot unicorn unify universe unravel
upcoming vacancy vagabond vertigo virginia visitor vocalist voyager warranty waterloo whimsical wichita wilmington wyoming yesteryear yucatan
`).trim().split(/\s+/);

export function newPairingCode(): { code: string; nameplate: string } {
  let number: number;
  // Rejection sampling for an unbiased 1–999 rendezvous number.
  do { number = crypto.getRandomValues(new Uint16Array(1))[0]; } while (number >= 64_935);
  const nameplate = String(number % 999 + 1);
  const words = crypto.getRandomValues(new Uint8Array(2));
  return { nameplate, code: `${nameplate}-${PAIRING_ODD_WORDS[words[0]]}-${PAIRING_EVEN_WORDS[words[1]]}` };
}

export function normalizePairingCode(raw: string): { code: string; nameplate: string } | null {
  const match = /^(\d{1,3})[\s-]+([a-z]+)[\s-]+([a-z]+)$/.exec(raw.trim().toLowerCase());
  if (!match || Number(match[1]) < 1) return null;
  // Unknown words still count as guesses, including ordinary transcription errors.
  const nameplate = String(Number(match[1]));
  return { nameplate, code: `${nameplate}-${match[2]}-${match[3]}` };
}

export function toHex(bytes: Uint8Array): string {
  return [...bytes].map((b) => b.toString(16).padStart(2, "0")).join("");
}
export async function sha256Hex(text: string): Promise<string> {
  return toHex(new Uint8Array(await crypto.subtle.digest("SHA-256", new TextEncoder().encode(text))));
}
export function newBrowserSecret(): string {
  return toHex(crypto.getRandomValues(new Uint8Array(24)));
}
export function splitCredential(credential: string | null): { identity: string; secret: string } | null {
  const match = /^([0-9a-f]{16,128})\.([0-9a-f]{32,128})$/.exec(credential ?? "");
  return match ? { identity: match[1], secret: match[2] } : null;
}
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

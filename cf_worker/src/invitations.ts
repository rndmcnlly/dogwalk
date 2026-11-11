// Invitations (DOMAIN.md): a User invites another person by voice. An Invitation extends one
// Invite Code row (single use, 24 hours) with its inviting User, an optional bound recipient
// number, revocation, and hiding; the User whose Registration redeems it is the
// inviter -> invitation -> invitee edge. Access Control only: phone numbers, Handles, and the
// graph never reach ACP Agents, Sandbox files, or provider labels.

export const INVITATION_TTL_SECONDS = 24 * 60 * 60;
export const MAX_ACTIVE_INVITATIONS = 3;

// Words from the PGP word list (two syllables, three, then two, as PGPfone reads bytes),
// lowercased, without proper nouns or grim words. The registration ritual matches them exactly.
const EVEN_WORDS = (
  "aardvark absurd accrue acme adrift adult ahead aimless allow alone ancient apple artist assume atlas " +
  "baboon backfield backward banjo beaming bedlamp beehive beeswax befriend billiard bison blockade blowtorch " +
  "bluebird bookshelf brackish breadline breakup brickyard briefcase button buzzard cement chairlift chatter " +
  "checkup chisel chopper clamshell classic classroom cleanup clockwork cobra commence concert cowbell cranky " +
  "crowfoot crucial crumpled cubic dashboard deckhand dragnet drainage drifter dropper drumbeat dwelling eating " +
  "egghead eightball endorse endow enlist erase escape exceed eyeglass eyetooth facial flagpole flatfoot flytrap " +
  "framework freedom gazelle glitter glucose goggles goldfish gremlin guidance hamlet highchair hockey indoors " +
  "indulge inverse involve island jawbone keyboard kickoff kiwi klaxon locale lockup merit minnow mural music " +
  "necklace newborn nightbird offload optic orca payday peachy pheasant physique playhouse prefer printer prowler " +
  "pupil python quadrant quiver quota ragtime ratchet rebirth reform regain reindeer rematch repay retouch reward " +
  "rhythm ribcage ringbolt robust rocker ruffled sailboat sawdust scallion scenic scorecard seabird select " +
  "sentence shadow shamrock skydive slingshot slowdown snapshot snowcap snowslide solo southward soybean " +
  "spearhead spellbind spheroid spigot spindle spyglass stagehand stairway standard stapler steamship sterling " +
  "stopwatch stormy sugar surmount suspense sweatband swelter tactics talon tempest tiger tissue tonic topmost " +
  "tracker transit treadmill tunnel tycoon unearth unwind uproot upshot vapor village waffle wallet watchword " +
  "wayside willow woodlark"
).split(" ");
const ODD_WORDS = (
  "adroitness adviser aftermath aggregate alkali almighty amulet amusement antenna applicant armistice article " +
  "asteroid atmosphere backwater barbecue belowground bifocals bodyguard bookseller borderline bottomless bravado " +
  "breakaway butterfat candidate cannonball caravan caretaker celebrate cellulose certify coherence combustion " +
  "company component concurrent confidence conformist congregate consensus consulting corporate crossover " +
  "cumbersome customer decimal designing detector detergent determine dinosaur direction document enchanting " +
  "enrollment enterprise equation equipment escapade everyday examine existence exodus fascinate filament " +
  "finicky forever fortitude frequency gadgetry getaway glossary gossamer graduate gravity guitarist hamburger " +
  "handiwork headwaters hemisphere hesitate hideaway hurricane hydraulic impartial impetus inception indigo " +
  "inertia infancy integrate intention inventive letterhead liberty maritime matchmaker maverick microscope " +
  "microwave midsummer millionaire miracle misnomer molasses molecule monument mosquito narrative nebula " +
  "newsletter onlooker opulent outfielder paperweight paragon paragraph paramount passenger pedigree perceptive " +
  "performance pharmacy phonetic photograph pioneer pocketful politeness positive potato processor provincial " +
  "proximate publisher pyramid quantity recipe recover repellent replica reproduce resistor responsive " +
  "retrieval retrospect revenue revival sandalwood scavenger sensation sociable souvenir specialist speculate " +
  "stethoscope stupendous supportive surrender sympathy tambourine telephone therapist tolerance tomorrow " +
  "tradition trombone typewriter ultimate undaunted underfoot unicorn unify universe unravel upcoming vacancy " +
  "vagabond vertigo visitor vocalist voyager warranty whimsical yesteryear"
).split(" ");

export function newInviteWords(): string {
  const [a, b, c] = crypto.getRandomValues(new Uint32Array(3));
  return [EVEN_WORDS[a % EVEN_WORDS.length], ODD_WORDS[b % ODD_WORDS.length], EVEN_WORDS[c % EVEN_WORDS.length]].join(" ");
}

// How a reference to an invitation (its words, as heard) is compared with stored words.
export const normalizeInviteWords = (raw: string): string =>
  raw.toLowerCase().replace(/[^a-z\s]/g, " ").trim().split(/\s+/).join(" ");

export const E164 = /^\+[1-9]\d{7,14}$/;

const DIGIT_WORDS = ["zero", "one", "two", "three", "four", "five", "six", "seven", "eight", "nine"];

// A phone number as the Voice Agent reads it back: digit by digit, in the groups a listener
// expects, so a mishearing is caught one group at a time.
export function speakablePhone(phone: string): string {
  const say = (digits: string) => Array.from(digits, (digit) => DIGIT_WORDS[Number(digit)]).join(" ");
  if (phone.startsWith("+1") && phone.length === 12) {
    return `plus one, ${say(phone.slice(2, 5))}, ${say(phone.slice(5, 8))}, ${say(phone.slice(8))}`;
  }
  const rest = phone.slice(1);
  const groups = rest.match(/\d{1,3}/g) ?? [];
  if (groups.length > 1 && groups.at(-1)!.length === 1) groups.splice(-2, 2, groups.at(-2)! + groups.at(-1)!);
  return `plus ${groups.map(say).join(", ")}`;
}

const phoneView = (phone: string | null) => phone ? { phone, spoken: speakablePhone(phone) } : null;

// A recipient number as heard. Ten digits mean a North American number only for a caller the
// carrier places in the US; anything else without a country code is refused so the Voice
// Agent asks rather than guesses.
export function normalizeRecipient(raw: string, callerInUs: boolean): { phone: string; assumedCountryCode: boolean } {
  const trimmed = raw.trim();
  const digits = trimmed.replace(/\D/g, "");
  let phone: string;
  let assumedCountryCode = false;
  if (trimmed.startsWith("+")) phone = `+${digits}`;
  else if (digits.length === 11 && digits.startsWith("1")) phone = `+${digits}`;
  else if (digits.length === 10 && callerInUs) {
    phone = `+1${digits}`;
    assumedCountryCode = true;
  } else if (digits.length === 10) {
    throw new Error("ten digits without a country code; ask the caller which country the number is in");
  } else {
    throw new Error(`${digits.length} digits is not a complete number; ask for the country code and the full number`);
  }
  if (!E164.test(phone)) throw new Error("that is not a valid international phone number; ask again");
  // North American numbers: area code and exchange never start with 0 or 1.
  if (phone.startsWith("+1") && (phone.length !== 12 || !/^\+1[2-9]\d{2}[2-9]/.test(phone))) {
    throw new Error("that is not a valid North American number; read it back and ask again");
  }
  return { phone, assumedCountryCode };
}

// Status is derived, never stored: expiry is a matter of time.
export const INVITATION_STATUS_SQL = `CASE
    WHEN v.redeemed_at IS NOT NULL THEN 'redeemed'
    WHEN v.revoked_at IS NOT NULL THEN 'revoked'
    WHEN i.expires_at <= unixepoch() THEN 'expired'
    ELSE 'active' END`;
const ACTIVE_COUNT_SQL = `(SELECT COUNT(*) FROM invitations a JOIN invite_codes c ON c.code_words = a.code_words
    WHERE a.inviter_user_id = ?1 AND a.redeemed_at IS NULL AND a.revoked_at IS NULL AND c.expires_at > unixepoch())`;

interface InvitationRow {
  code_words: string;
  minted_at: number;
  expires_at: number;
  recipient_phone: string | null;
  recipient_confirmed_at: number | null;
  redeemed_by_handle: string | null;
  redeemed_from: string | null;
  redeemed_at: number | null;
  status: "active" | "redeemed" | "expired" | "revoked";
}

function invitationView(row: InvitationRow, now: number) {
  return {
    words: row.code_words,
    status: row.status,
    created_minutes_ago: Math.round((now - row.minted_at) / 60),
    ...(row.status === "active" ? { expires_in_minutes: Math.max(0, Math.round((row.expires_at - now) / 60)) } : {}),
    // An intended recipient, confirmed by readback on the inviter's call; not proof the
    // recipient controls that number.
    recipient: phoneView(row.recipient_phone),
    ...(row.redeemed_at ? {
      redeemed_minutes_ago: Math.round((now - row.redeemed_at) / 60),
      redeemed_by: row.redeemed_by_handle,
      redeemed_from: phoneView(row.redeemed_from),
      matched_recipient: row.recipient_phone ? row.recipient_phone === row.redeemed_from : null,
    } : {}),
  };
}

async function ownInvitation(db: D1Database, inviter: number, words: string): Promise<InvitationRow> {
  const row = await db.prepare(
    `SELECT v.code_words, i.minted_at, i.expires_at, v.recipient_phone, v.recipient_confirmed_at,
            invitee.handle AS redeemed_by_handle, v.redeemed_from, v.redeemed_at, ${INVITATION_STATUS_SQL} AS status
       FROM invitations v JOIN invite_codes i ON i.code_words = v.code_words
       LEFT JOIN users invitee ON invitee.id = v.redeemed_by_user_id
      WHERE v.code_words = ? AND v.inviter_user_id = ? AND v.hidden_at IS NULL`,
  ).bind(normalizeInviteWords(words), inviter).first<InvitationRow>();
  // Someone else's invitation and no invitation at all read the same.
  if (!row) throw new Error("no such invitation among the caller's; list_invitations shows them");
  return row;
}

export async function listInvitations(db: D1Database, inviter: number): Promise<Record<string, unknown>> {
  const now = Math.floor(Date.now() / 1000);
  const rows = await db.prepare(
    `SELECT v.code_words, i.minted_at, i.expires_at, v.recipient_phone, v.recipient_confirmed_at,
            invitee.handle AS redeemed_by_handle, v.redeemed_from, v.redeemed_at, ${INVITATION_STATUS_SQL} AS status
       FROM invitations v JOIN invite_codes i ON i.code_words = v.code_words
       LEFT JOIN users invitee ON invitee.id = v.redeemed_by_user_id
      WHERE v.inviter_user_id = ? AND v.hidden_at IS NULL
      ORDER BY i.minted_at DESC`,
  ).bind(inviter).all<InvitationRow>();
  const invitations = (rows.results ?? []).map((row) => invitationView(row, now));
  return {
    ok: true,
    invitations,
    active: invitations.filter((item) => item.status === "active").length,
    limit: MAX_ACTIVE_INVITATIONS,
  };
}

export async function createInvitation(
  db: D1Database, inviter: number, recipient: string | null,
): Promise<Record<string, unknown>> {
  for (let attempt = 0; attempt < 3; attempt++) {
    const words = newInviteWords();
    const now = Math.floor(Date.now() / 1000);
    try {
      // Both inserts check the same limit, so either both happen or neither does. Words that
      // collide with an existing Invite Code fail the first insert and roll back the batch.
      const [, created] = await db.batch([
        db.prepare(
          `INSERT INTO invite_codes (code_words, minted_at, expires_at, max_uses)
           SELECT ?2, ?3, ?4, 1 WHERE ${ACTIVE_COUNT_SQL} < ?5`,
        ).bind(inviter, words, now, now + INVITATION_TTL_SECONDS, MAX_ACTIVE_INVITATIONS),
        db.prepare(
          `INSERT INTO invitations (code_words, inviter_user_id, recipient_phone, recipient_confirmed_at)
           SELECT ?2, ?1, ?3, ?4 WHERE ${ACTIVE_COUNT_SQL} < ?5
              AND EXISTS (SELECT 1 FROM invite_codes WHERE code_words = ?2 AND minted_at = ?6)`,
        ).bind(inviter, words, recipient, recipient ? now : null, MAX_ACTIVE_INVITATIONS, now),
      ]);
      if (created.meta.changes === 0) {
        throw new Error(`the caller already has ${MAX_ACTIVE_INVITATIONS} active invitations; one must be redeemed, expire, or be revoked first`);
      }
      return { ok: true, invitation: invitationView(await ownInvitation(db, inviter, words), now) };
    } catch (error) {
      if (!String(error).includes("UNIQUE") && !String(error).includes("PRIMARY")) throw error;
    }
  }
  throw new Error("could not choose unused invite words; try again");
}

export async function setInvitationRecipient(
  db: D1Database, inviter: number, words: string, recipient: string | null,
): Promise<Record<string, unknown>> {
  const now = Math.floor(Date.now() / 1000);
  const row = await ownInvitation(db, inviter, words);
  if (row.status !== "active") throw new Error(`the invitation is ${row.status}; only an active one can change its recipient`);
  // Conditional on still being unredeemed, so it cannot race a redemption.
  const updated = await db.prepare(
    `UPDATE invitations SET recipient_phone = ?, recipient_confirmed_at = ?
      WHERE code_words = ? AND inviter_user_id = ? AND redeemed_at IS NULL AND revoked_at IS NULL`,
  ).bind(recipient, recipient ? now : null, row.code_words, inviter).run();
  if (updated.meta.changes === 0) throw new Error("the invitation was redeemed or revoked meanwhile");
  return { ok: true, invitation: invitationView(await ownInvitation(db, inviter, row.code_words), now) };
}

// Revoke an unredeemed invitation and hide it; a redeemed, expired, or revoked one is only
// hidden. Nothing is deleted: provenance and the Registration it produced stay.
export async function revokeInvitation(db: D1Database, inviter: number, words: string): Promise<Record<string, unknown>> {
  const row = await ownInvitation(db, inviter, words);
  await db.prepare(
    `UPDATE invitations
        SET revoked_at = CASE WHEN redeemed_at IS NULL AND revoked_at IS NULL THEN unixepoch() ELSE revoked_at END,
            hidden_at = unixepoch()
      WHERE code_words = ? AND inviter_user_id = ?`,
  ).bind(row.code_words, inviter).run();
  const revoked = row.status === "active";
  return {
    ok: true, words: row.code_words, was: row.status, revoked,
    note: revoked ? "Revoked: the words no longer work. Removed from the caller's list." : "Removed from the caller's list; its history is kept.",
  };
}

// The caller's own Registration and Handle, and who invited them when an Invitation did.
export async function inspectRegistration(db: D1Database, user: number): Promise<Record<string, unknown>> {
  const row = await db.prepare(
    `SELECT u.handle, u.registered_at, v.code_words AS invitation, inviter.handle AS inviter_handle
       FROM users u
       LEFT JOIN invitations v ON v.redeemed_by_user_id = u.id
       LEFT JOIN users inviter ON inviter.id = v.inviter_user_id
      WHERE u.id = ?`,
  ).bind(user).first<{ handle: string | null; registered_at: number; invitation: string | null; inviter_handle: string | null }>();
  if (!row) throw new Error("the caller has no Registration");
  return {
    ok: true,
    handle: row.handle,
    registered_days_ago: Math.floor((Date.now() / 1000 - row.registered_at) / 86400),
    via: row.invitation ? "invitation" : "operator invite code",
    // The inviter's Handle, or null when an operator code admitted the caller or the inviter was reaped.
    invited_by: row.inviter_handle,
  };
}

// --- Redemption, for the registration ritual ------------------------------

// The clauses that make an Invite Code usable by this phone right now: unexpired, below its
// uses, and, for an Invitation, unrevoked and either unbound or bound to this phone.
export const USABLE_FOR_PHONE_SQL = `(i.expires_at IS NULL OR i.expires_at > ?)
        AND (i.max_uses IS NULL OR
             (SELECT COUNT(*) FROM users u WHERE u.invite_code = i.code_words) < i.max_uses)
        AND NOT EXISTS (SELECT 1 FROM invitations v WHERE v.code_words = i.code_words
                          AND (v.revoked_at IS NOT NULL OR (v.recipient_phone IS NOT NULL AND v.recipient_phone != ?)))`;

// One D1 batch, one transaction: the User is inserted only if the code is usable by this
// phone, and every later statement acts only if that insert happened (the new User is found by
// its fresh identity_hash). Registering from a number that is already a Home displaces its
// User, as re-homing does. D1 serializes writes, so of two concurrent claims on a single-use
// code only one finds it below its uses; the other inserts nothing and records nothing.
// Results: [registration, displacement, home, redemption].
export function redemptionStatements(
  db: D1Database, phone: string, codeWords: string, now: number, handle: string, identityHash: string,
): D1PreparedStatement[] {
  return [
    db.prepare(
      `INSERT INTO users (handle, identity_hash, invite_code, registered_at, last_seen_at)
       SELECT ?, ?, i.code_words, ?, ?
         FROM invite_codes i
        WHERE i.code_words = ?
          AND ${USABLE_FOR_PHONE_SQL}`,
    ).bind(handle, identityHash, now, now, codeWords, now, phone),
    db.prepare(
      `UPDATE users SET home_phone = NULL, home_used_at = NULL
        WHERE home_phone = ? AND EXISTS (SELECT 1 FROM users WHERE identity_hash = ?)`,
    ).bind(phone, identityHash),
    db.prepare("UPDATE users SET home_phone = ?, home_used_at = ? WHERE identity_hash = ?").bind(phone, now, identityHash),
    db.prepare(
      `UPDATE invitations
          SET redeemed_by_user_id = (SELECT id FROM users WHERE identity_hash = ?), redeemed_from = ?, redeemed_at = ?
        WHERE code_words = ? AND redeemed_by_user_id IS NULL
          AND EXISTS (SELECT 1 FROM users WHERE identity_hash = ?)`,
    ).bind(identityHash, phone, now, codeWords, identityHash),
  ];
}

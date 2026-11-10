// Node 24+ strips the module's TypeScript; SQLite exercises the actual SQL/indices.
// Run: node cf_worker/provider_timing_test.mjs
import assert from 'node:assert/strict';
import { DatabaseSync } from 'node:sqlite';
import { readFileSync } from 'node:fs';
import { observeTwilio, processTwilioJobs, queueTwilioCall, normalizeTimestamp, sanitizePath } from './src/provider_timing.ts';

const account = `AC${'a'.repeat(32)}`, root = `CA${'1'.repeat(32)}`, child = `CA${'2'.repeat(32)}`;
const originalNow = Date.now, originalFetch = globalThis.fetch;
let now = Date.parse('Tue, 10 Nov 2026 12:00:00 +0000');
Date.now = () => now;
const db = new DatabaseSync(':memory:');
db.exec(readFileSync(new URL('./schema.sql', import.meta.url), 'utf8'));
const schema = readFileSync(new URL('./provider_timing.sql', import.meta.url), 'utf8');
db.exec(schema); db.exec(schema); // Installation is idempotent.
const env = { TWILIO_AUTH_TOKEN: 'test-secret', DB: { prepare(sql) {
  return { values: [], bind(...values) { this.values = values; return this; },
    async run() { const result = db.prepare(sql).run(...this.values); return { meta: { changes: Number(result.changes) } }; },
    async all() { return { results: db.prepare(sql).all(...this.values) }; },
    async first() { return db.prepare(sql).get(...this.values) ?? null; },
  };
} } };
const rows = () => db.prepare('SELECT * FROM call_activity ORDER BY id').all();
const job = (sid = root) => db.prepare('SELECT * FROM provider_call_jobs WHERE provider_call_sid = ?').get(sid);
const form = (fields = {}) => {
  const result = new FormData();
  for (const [k, v] of Object.entries({ AccountSid: account, CallSid: root, CallStatus: 'completed', ...fields })) result.set(k, v);
  return result;
};
const json = value => Response.json(value);
function due(sid = root) {
  db.prepare('UPDATE provider_call_jobs SET next_metadata_at = NULL, events_after = ?, events_done = 0, attempts = 0 WHERE provider_call_sid = ?').run(now, sid);
}

try {
  assert.equal(normalizeTimestamp('Tue, 10 Nov 2026 11:59:00 +0000'), now - 60_000);
  for (const value of [null, '', '2026', '2026-11-10T12:00:00Z', 'bogus']) assert.equal(normalizeTimestamp(value), null);
  assert.equal(sanitizePath('https://user:password@example.org/voice/status?token=secret#private'), '/voice/status');
  assert.equal(sanitizePath('sip:secret@provider.invalid'), null);
  assert.equal(sanitizePath('https://example.org/capability/secret'), null);
  await observeTwilio(env, form({ CallSid: 'CA_TEST1' }), 'CA_TEST1', now);
  assert.equal(rows().length, 0);
  db.prepare(`INSERT INTO voice_calls VALUES (?, 'private-number', 'completed', 1, 2, 3, 2)`).run(root);
  const operational = db.prepare('SELECT * FROM voice_calls').get();
  const callback = form({ Timestamp: 'Tue, 10 Nov 2026 11:59:00 +0000', SequenceNumber: '3', From: 'private-number', To: 'private-number', Url: 'https://secret.invalid/?token=private' });
  globalThis.fetch = () => { throw Error('observation must never fetch'); };
  await observeTwilio(env, callback, root, now);
  now += 5000;
  await observeTwilio(env, callback, root, now);
  assert.equal(rows().length, 1, 'database-backed retry deduplication');
  assert.equal(job().events_after, now - 5000 + 16 * 60_000);
  assert.deepEqual(db.prepare('SELECT * FROM voice_calls').get(), operational, 'late callback cannot reopen a call');
  let detail = JSON.parse(rows()[0].detail);
  assert.equal(detail.timestamp_precision_ms, 1000);
  assert.equal(detail.sequence_number, 3);
  assert.equal(detail.ts_ms, now - 5000);
  assert.equal(detail.occurred_at_ms, now - 65_000);
  assert(!rows()[0].detail.includes('private'));
  await observeTwilio(env, form({ CallStatus: 'ringing', SequenceNumber: '1' }), root, now);
  detail = JSON.parse(rows()[1].detail);
  assert.equal(detail.timestamp_precision_ms, null);
  assert.equal(detail.event_time_source, 'unknown');
  assert.equal(detail.sequence_number, 1, 'provider sequence need not follow receipt order');
  const cursor = rows()[1].id;
  await observeTwilio(env, form({ CallSid: child, ParentCallSid: root, CallStatus: 'in-progress', SequenceNumber: '-1' }), root, now);
  detail = JSON.parse(rows()[2].detail);
  assert.equal(detail.leg, 'sip');
  assert.equal(detail.parent_call_sid, root);
  assert.equal(detail.sequence_number, null);
  assert.equal(rows()[2].call_sid, root);
  await observeTwilio(env, form({ CallSid: child, ParentCallSid: `CA${'3'.repeat(32)}` }), root, now);
  assert.equal(rows().length, 3, 'unrelated parent rejected');
  db.prepare('UPDATE provider_call_jobs SET lease_until = ? WHERE provider_call_sid = ?').run(now + 300_000, child);
  let requests = [];
  globalThis.fetch = async (url, options) => {
    requests.push(String(url));
    assert.equal(options.redirect, 'manual');
    assert.equal(options.headers.Authorization, `Basic ${btoa(`${account}:test-secret`)}`);
    assert(options.signal);
    return json({ sid: root, account_sid: account, parent_call_sid: null, status: 'completed',
      date_created: 'Tue, 10 Nov 2026 11:57:00 +0000', start_time: 'Tue, 10 Nov 2026 11:58:00 +0000', end_time: 'Tue, 10 Nov 2026 11:59:00 +0000',
      from: 'private-number', to: 'private-number' });
  };
  await processTwilioJobs(env);
  assert.equal(requests[0], `https://api.twilio.com/2010-04-01/Accounts/${account}/Calls/${root}.json`);
  assert.equal(rows().filter(r => r.id > cursor && r.event.includes('.call.')).length, 3, 'late metadata stays visible after an id cursor');
  assert.equal(JSON.parse(rows().at(-1).detail).event_time_source, 'call.end_time');
  assert.equal(job().next_metadata_at, null);
  // A callback arriving during an older metadata request must survive its clear.
  db.prepare('UPDATE provider_call_jobs SET next_metadata_at = ? WHERE provider_call_sid = ?').run(now, root);
  globalThis.fetch = async () => {
    await observeTwilio(env, form({ CallStatus: 'completed', SequenceNumber: '9' }), root, now);
    return json({ sid: root, account_sid: account, parent_call_sid: null, status: 'completed' });
  };
  await processTwilioJobs(env);
  assert.equal(job().next_metadata_at, now, 'new callback generation survives in-flight metadata clear');
  globalThis.fetch = async () => json({ sid: root, account_sid: account, parent_call_sid: null, status: 'completed' });
  await processTwilioJobs(env);
  assert.equal(job().next_metadata_at, null, 'fetch of current generation consumes refresh');
  assert(rows().every(row => !row.detail.includes('private')));
  due();
  requests = [];
  const event = { request: { method: 'POST', url: 'https://private-host/voice/status?token=private', parameters: { From: 'private-number' } },
    response: { date_created: 'Tue, 10 Nov 2026 11:59:00 +0000', request_duration: 123, response_code: 200, response_body: '<private/>', content_type: 'private' } };
  globalThis.fetch = async url => {
    requests.push(String(url));
    return json({ events: [event], next_page_uri: requests.length === 1 ? `/2010-04-01/Accounts/${account}/Calls/${root}/Events.json?PageToken=private` : null });
  };
  await processTwilioJobs(env);
  assert.equal(requests.length, 2);
  assert.equal(rows().filter(r => r.event === 'provider.twilio.webhook.exchange').length, 1, 'page overlap is deduplicated');
  detail = JSON.parse(rows().at(-1).detail);
  assert.equal(detail.request_path, '/voice/status');
  assert.equal(detail.request_duration_ms, 123);
  assert.equal(detail.event_time_source, 'response.date_created');
  assert.equal(job().events_done, 1);
  assert(!JSON.stringify(rows()).includes('private'));
  for (const next of [`https://evil.invalid/2010-04-01/Accounts/${account}/Calls/${root}/Events.json`,
    `/2010-04-01/Accounts/${account}/Calls/${child}/Events.json`,
    `http://api.twilio.com/2010-04-01/Accounts/${account}/Calls/${root}/Events.json`]) {
    due(); let calls = 0;
    globalThis.fetch = async () => { calls++; return json({ events: [], next_page_uri: next }); };
    await processTwilioJobs(env);
    assert.equal(calls, 1, 'pagination validation happens before sending credentials');
    assert.equal(job().last_error, 'invalid_next_page');
  }
  due();
  globalThis.fetch = async () => json({ events: [], next_page_uri: null });
  await processTwilioJobs(env);
  assert.equal(job().events_done, 0);
  assert.equal(job().last_error, 'events_empty');
  const backedOff = job().events_after;
  await observeTwilio(env, callback, root, now);
  assert.equal(job().events_after, backedOff, 'retry callback must not reset backoff');
  for (let i = 1; i < 8; i++) { now = job().events_after; await processTwilioJobs(env); }
  assert.equal(job().attempts, 8);
  now += 24 * 60 * 60_000;
  globalThis.fetch = () => { throw Error('exhausted jobs must not run'); };
  await processTwilioJobs(env);
  due();
  globalThis.fetch = async (_url, { signal }) => new Promise((_, reject) => signal.addEventListener('abort', () => reject(Error('sensitive URL'))));
  await processTwilioJobs(env); // Real eight-second timeout, including a hanging response.
  assert.equal(job().last_error, 'request_timeout');
  assert.equal(job().lease_until, 0);
  due();
  db.prepare('UPDATE provider_call_jobs SET next_metadata_at = ?, events_after = NULL WHERE provider_call_sid = ?').run(now, root);
  globalThis.fetch = async () => json({ sid: child, account_sid: account, status: 'completed' });
  await processTwilioJobs(env);
  assert.equal(job().last_error, 'resource_mismatch');
  // Explicit trusted backfill can queue a known terminal child, never an unrelated root.
  await queueTwilioCall(env, account, `CA${'4'.repeat(32)}`, root, root, now);
  assert.equal(job(`CA${'4'.repeat(32)}`).root_call_sid, root);
  console.log('provider timing: parsing, actual SQLite dedup/cursor/state isolation, child correlation, API redaction, pagination, backoff, exhaustion, timeout passed');
} finally {
  Date.now = originalNow;
  globalThis.fetch = originalFetch;
  db.close();
}

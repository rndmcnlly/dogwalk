// Credential-free adapter tests. Real Orchestrator and session tools, fake model/ACP wire.
import assert from 'node:assert/strict';
import { build } from 'esbuild';
import { mkdtemp, rm, writeFile } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { pathToFileURL } from 'node:url';

const temp = await mkdtemp(join(tmpdir(), 'dogwalk-app-test-'));
try {
  const result = await build({ stdin: { contents: 'export {default as worker} from "./src/index"; export {default as localWorker} from "./src/local_trial_entry"; export {localAppTrial} from "./src/local_app_trial"; export {VoiceSession} from "./src/voice_session"; export * from "./src/app_frontend";', resolveDir: process.cwd() }, bundle: true, platform: 'node', format: 'esm', write: false, loader: { '.pcm': 'binary', '.md': 'text' } });
  const file = join(temp, 'test.mjs'); await writeFile(file, result.outputFiles[0].text);
  const { worker, localWorker, localAppTrial, VoiceSession, appAuthorized, parseAppFrame } = await import(pathToFileURL(file));
  const token = 'x'.repeat(43); // deliberately public fixture, never a deployed capability
  const digest = Buffer.from(await crypto.subtle.digest('SHA-256', new TextEncoder().encode(token))).toString('hex');
  const env = { APP_FRONTEND_ENABLED: 'true', APP_FRONTEND_TOKEN_SHA256: digest, APP_FRONTEND_OWNER_PHONE: '+15555550101' };
  const request = (url = 'https://trial.invalid/app/frontend', authorization = `Bearer ${token}`) => new Request(url, { headers: { authorization, upgrade: 'websocket' } });
  assert(await appAuthorized(request(), env));
  for (const r of [request(undefined, 'Basic YWRhbTpwYXNz'), request(undefined, 'Bearer wrong'), request('https://trial.invalid/app/frontend?phone=other'), request('https://trial.invalid/app/frontend?token=x'), request('http://trial.invalid/app/frontend')]) assert.equal(await appAuthorized(r, env), false);
  assert.equal(await appAuthorized(request(), { ...env, APP_FRONTEND_ENABLED: 'false' }), false);
  assert.equal(await appAuthorized(request(), { ...env, APP_FRONTEND_OWNER_PHONE: '' }), false);
  assert.equal(await appAuthorized(request(), { ...env, APP_FRONTEND_TOKEN_SHA256: '0'.repeat(64) }), false, 'rotation rejects subsequent requests');
  const input = { type: 'session.input_transcript.delta', delta: 'Create a session', delivery: 'text_typed', start_ms: 1, end_ms: 1 };
  const envelope = (event, id = 'event-1', attachment = 'generation') => JSON.stringify({ v: 1, attachment, id, event });
  assert.deepEqual(parseAppFrame(envelope(input), 'generation').event, input);
  for (const raw of [envelope(input, 'x', 'stale'), envelope({ ...input, delivery: 'audio' }), envelope({ ...input, end_ms: -1 }), envelope({ ...input, end_ms: 2 }), envelope({ ...input, delta: '語'.repeat(15000) }), envelope({ type: 'session.closed' }), envelope({ type: 'resolve_permission' }), 'x'.repeat(32769), new Uint8Array(1), '{}']) assert.throws(() => parseAppFrame(raw, 'generation'));

  // Worker owns identity. Untrusted x-dogwalk-* headers never reach the object.
  let registered = true, assigned = true, routed, providerRequests = [], providerId = 'existing-sandbox';
  env.DB = { prepare(sql) { return { bind(...args) { this.args = args; return this; }, async first() {
    if (sql.includes('registrations')) return registered ? { phone_number: env.APP_FRONTEND_OWNER_PHONE } : null;
    if (sql.includes('sandbox_assignments')) return assigned ? { phone_number: env.APP_FRONTEND_OWNER_PHONE, identity_hash: 'server-identity', provider_id: providerId } : null;
  }, async run() { return { meta: { changes: 1 } }; } }; } };
  env.VOICE_SESSIONS = { idFromName(name) { assert.equal(name, 'server-identity'); return name; }, get() { return { async fetch(url, options) { routed = { url, options }; return new Response(null, { status: 204 }); } }; } };
  const originalFetch = globalThis.fetch;
  globalThis.fetch = async (url, options) => { providerRequests.push([url, options]); return Response.json({ id: 'existing-sandbox', state: 'started' }); };
  const hostile = request(); hostile.headers.set('x-dogwalk-phone', '+19999999999'); hostile.headers.set('x-dogwalk-sandbox-id', 'attacker');
  assert.equal((await worker.fetch(hostile, env, {})).status, 204);
  assert.equal(routed.options.headers.get('x-dogwalk-phone'), env.APP_FRONTEND_OWNER_PHONE);
  assert.equal(routed.options.headers.get('x-dogwalk-sandbox-id'), 'existing-sandbox');
  assert.equal(providerRequests.length, 1);
  assigned = false; assert.equal((await worker.fetch(request(), env, {})).status, 409);
  registered = false; assert.equal((await worker.fetch(request(), env, {})).status, 409);
  assert.equal(providerRequests.length, 1, 'missing identity must not provision anything');
  assert.equal((await worker.fetch(request(), { ...env, APP_FRONTEND_ENABLED: 'false' }, {})).status, 404);
  assert.equal((await worker.fetch(request(undefined, 'Basic YWRhbTpwYXNz'), env, {})).status, 401);
  const localEnv = { ...env, DEPLOYMENT_ENVIRONMENT: 'local-app-trial', LOCAL_APP_TRIAL_ENABLED: 'true', LOCAL_APP_TRIAL_ACP_URL: 'http://127.0.0.1:18782/acp', LOCAL_APP_TRIAL_WORKSPACE: '/private/var/folders/test/dogwalk-local-trial-test/workspace' };
  assert(localAppTrial(localEnv));
  for (const invalid of [{ DEPLOYMENT_ENVIRONMENT: 'production' }, { LOCAL_APP_TRIAL_ACP_URL: 'https://remote.invalid/acp' }, { LOCAL_APP_TRIAL_ACP_URL: 'http://127.0.0.1:18782/acp?token=x' }, { LOCAL_APP_TRIAL_WORKSPACE: '/Users/adam/Desktop' }]) assert.throws(() => localAppTrial({ ...localEnv, ...invalid }));
  registered = assigned = true; providerId = 'local-agent-trial';
  const providerBeforeLocal = providerRequests.length;
  assert.equal((await worker.fetch(request(), localEnv, {})).status, 204);
  assert.equal(providerRequests.length, providerBeforeLocal, 'local adapter must never call Daytona');
  assert.equal((await worker.fetch(request(), { ...localEnv, DEPLOYMENT_ENVIRONMENT: 'production' }, {})).status, 503);
  assert.equal(providerRequests.length, providerBeforeLocal, 'production cannot activate local hosting or fall through to provider');
  for (const path of ['/admin', '/voice', '/pair', '/healthz']) assert.equal((await localWorker.fetch(new Request('http://127.0.0.1:18781' + path), localEnv, {})).status, 404);
  assert.equal((await localWorker.fetch(new Request('http://127.0.0.1:18781/app/frontend', { headers: { authorization: `Bearer ${token}`, upgrade: 'websocket' } }), localEnv, {})).status, 204);
  assert.equal((await localWorker.fetch(request(), { ...localEnv, DEPLOYMENT_ENVIRONMENT: 'production' }, {})).status, 503);
  assert.equal(providerRequests.length, providerBeforeLocal);
  globalThis.fetch = originalFetch;

  const NativeResponse = globalThis.Response;
  globalThis.Response = class extends NativeResponse {
    constructor(body, options = {}) { super(body, options.status === 101 ? { status: 200 } : options); if (options.status === 101) Object.defineProperty(this, 'status', { value: 101 }); this.webSocket = options.webSocket; }
  };
  class Socket {
    readyState = 1; handlers = {}; sent = [];
    accept() {}
    addEventListener(type, callback) { (this.handlers[type] ??= []).push(callback); }
    send(text) { this.sent.push(JSON.parse(text)); this.peer?.handlers.message?.forEach(fn => fn({ data: text })); }
    close() { if (this.readyState !== 1) return; this.readyState = 3; this.handlers.close?.forEach(fn => fn()); }
  }
  globalThis.WebSocket = { OPEN: 1 };
  globalThis.WebSocketPair = class { constructor() { this[0] = new Socket(); this[1] = new Socket(); this[0].peer = this[1]; this[1].peer = this[0]; } };
  const store = new Map(); let initialized;
  const state = { blockConcurrencyWhile(fn) { initialized = fn(); }, storage: {
    async get(key) { return store.get(key); }, async put(key, value) { if (typeof key === 'object') Object.entries(key).forEach(([k,v]) => store.set(k,v)); else store.set(key,value); },
    async delete(keys) { for (const k of Array.isArray(keys) ? keys : [keys]) store.delete(k); }, async setAlarm() {},
  }, getWebSockets() { return []; } };
  const db = { prepare() { return { bind() { return this; }, async run() { return {}; }, async all() { return { results: [] }; }, async first() { return null; } }; }, async batch() {} };
  const session = new VoiceSession(state, { ...env, DB: db }); await initialized;
  session.record = async () => {}; session.flushTiming = async () => {}; session.writeNextGreeting = async () => {};
  const observed = [], modelInputs = [], rpc = [];
  session.ensureAcp = () => { session.acpReady = true; void session.orchestratorEvent('[harness event] agent_connected: {}'); };
  const acp = new Socket(); session.acp = acp;
  acp.send = (text) => {
    const m = JSON.parse(text); rpc.push(m);
    if (m.method === 'session/new') queueMicrotask(() => session.handleAcpMessage({ data: JSON.stringify({ id: m.id, result: { sessionId: 'actual-acp-session' } }) }));
    // Leave session/prompt outstanding until the test completes it after detachment.
  };
  let step = 0;
  const call = (name, args) => ({ type: 'function_call', call_id: `call-${++step}`, name, arguments: JSON.stringify(args) });
  session.orchestratorModel = async () => {
    const text = JSON.stringify(session.orchestratorContext); modelInputs.push(text);
    if (text.includes('[handoff create]') && !observed.includes('create')) { observed.push('create'); return [call('create_managed_session', { alias: 'Fir', directory: '/home/daytona' })]; }
    if (observed.includes('create') && !observed.includes('prompt')) { observed.push('prompt'); return [call('begin_prompt_turn', { alias: 'Fir', prompt: 'Build the trial' })]; }
    if (text.includes('[handoff allow]') && !observed.includes('allow')) { observed.push('allow'); return [call('resolve_permission', { alias: 'Fir', option_id: 'once' }), call('say', { text: 'Fir continues.', delegation_id: null })]; }
    return [];
  };
  const headers = { upgrade: 'websocket', 'x-dogwalk-phone': env.APP_FRONTEND_OWNER_PHONE, 'x-dogwalk-sandbox-id': 'existing-sandbox', 'x-dogwalk-identity': 'server-identity' };
  const attachRequest = () => new Request('https://object/app/frontend', { headers });
  const attaching = session.fetch(attachRequest());
  assert.equal((await session.fetch(attachRequest())).status, 409, 'reservation before storage await');
  const response = await attaching; assert.equal(response.status, 101);
  const client = response.webSocket, server = client.peer;
  const ready = server.sent[0]; assert.equal(ready.type, 'frontend.ready');
  assert.equal(server.sent[1].event.type, 'session.commentary.append');
  const generation = ready.attachment;
  assert.equal((await session.fetch(new Request('https://object/app/heartbeat', { method: 'POST', headers: { 'x-dogwalk-attachment': 'stale' } }))).status, 409);
  assert.equal((await session.fetch(new Request('https://object/app/heartbeat', { method: 'POST', headers: { 'x-dogwalk-attachment': generation } }))).status, 204);
  async function send(event, id) { client.send(envelope(event, id, generation)); await session.liveMessageChain; }
  async function settle() { for (let i=0; i<30; i++) await new Promise(r => setImmediate(r)); }
  await settle();
  assert(modelInputs[0].indexOf('call_started') < modelInputs[0].indexOf('agent_connected'), 'startup ordering');
  const originalPhone = session.phone, originalCall = session.callSid;
  assert.equal((await session.fetch(new Request('https://object/attach', { method: 'POST', headers: { ...headers, 'x-dogwalk-phone': 'attacker', 'x-dogwalk-live-session-id': 'pstn' } }))).status, 409);
  assert.equal(session.phone, originalPhone); assert.equal(session.callSid, originalCall);
  await send(input, 'input'); await send({ type: 'session.delegation.created', delegation: { id: 'create', target: 'client' }, offset_ms: 1 }, 'handoff');
  await settle(); assert(rpc.some(m => m.method === 'session/new')); assert(rpc.some(m => m.method === 'session/prompt'));
  const count = rpc.length; await send(input, 'input'); await settle(); assert.equal(rpc.length, count, 'dedupe accepted event');
  assert.equal(session.transcript.filter(e => e.role === 'caller').map(e => e.text).join(''), input.delta, 'duplicate input is not appended twice');
  assert.equal(server.sent.filter(e => e.type === 'frontend.accepted' && e.id === 'input').length, 2, 'same live event is acknowledged again');
  const managed = [...session.sessions.values()][0];
  await session.handleAcpMessage({ data: JSON.stringify({ jsonrpc: '2.0', id: 'permission-1', method: 'session/request_permission', params: { sessionId: 'actual-acp-session', toolCall: { toolCallId: 'tool-1', title: 'Run build' }, options: [{ optionId: 'once', name: 'Allow once', kind: 'allow_once' }, { optionId: 'deny', name: 'Deny', kind: 'reject_once' }] } }) });
  assert.equal(session.permissions.size, 1);
  const openingId = server.sent[1].id;
  await send({ type: 'session.output_transcript.delta', delta: 'Dogwalk.', delivery: 'text_displayed', start_ms: 2, end_ms: 2, request_id: openingId, complete: true }, 'receipt');
  assert.equal(session.permissions.size, 1, 'display metadata never authorizes');
  await send({ ...input, delta: 'Allow once', start_ms: 3, end_ms: 3 }, 'answer');
  await send({ type: 'session.delegation.created', delegation: { id: 'allow', target: 'client' }, offset_ms: 3 }, 'allow');
  await settle();
  assert(rpc.some(m => m.id === 'permission-1' && m.result?.outcome?.optionId === 'once'), 'real Permission Request response over ACP');
  assert(modelInputs.some(s => s.includes('text_typed') && s.includes('text_displayed')), 'Orchestrator provenance');
  for (const name of ['play_audio', 'start_recording', 'set_theme', 'pair_browser', 'open_recovery_menu']) assert.equal((await session.executeOrchestratorToolInner({ name, arguments: '{}' })).ok, false);
  const prompt = rpc.find(m => m.method === 'session/prompt');
  await session.cleanupVoice('test disconnect');
  assert.equal(managed.turnState, 'in_progress'); assert.equal(session.sessions.size, 1);
  assert(!rpc.some(m => m.method === 'session/cancel'));
  await session.handleAcpMessage({ data: JSON.stringify({ id: prompt.id, result: { stopReason: 'end_turn' } }) }); await settle();
  assert.equal(managed.turnState, 'stopped', 'work completes after frontend disconnect');
  const replacement = await session.fetch(attachRequest());
  const replacementServer = replacement.webSocket.peer;
  replacement.webSocket.send(envelope(input, 'stale', generation)); await session.liveMessageChain; await settle();
  assert.equal(replacementServer.readyState, 3, 'stale attachment rejected');
  await session.cleanupVoice('test complete');

  // A model response started on the prior attachment cannot speak on its replacement.
  let releaseModel, modelStarted;
  const startedModel = new Promise(resolve => { modelStarted = resolve; });
  const modelGate = new Promise(resolve => { releaseModel = resolve; });
  let delayed = true;
  session.orchestratorModel = async () => { if (delayed) { delayed = false; modelStarted(); await modelGate; return [call('say', { text: 'STALE OUTPUT', delegation_id: null })]; } return []; };
  const old = await session.fetch(attachRequest());
  await startedModel;
  await session.cleanupVoice('old generation ended');
  const newer = await session.fetch(attachRequest());
  releaseModel(); await settle();
  assert(!newer.webSocket.peer.sent.some(m => m.event?.content === 'STALE OUTPUT'));
  await session.cleanupVoice('test complete');

  // Rejected old model requests cannot emit the catch-path error on a replacement.
  let rejectOld, notifyOld;
  const rejectionStarted = new Promise(resolve => { notifyOld = resolve; });
  const rejection = new Promise((_, reject) => { rejectOld = reject; });
  let rejectFirst = true;
  session.orchestratorModel = async () => {
    if (rejectFirst) { rejectFirst = false; notifyOld(); return await rejection; }
    return [];
  };
  await session.fetch(attachRequest()); await rejectionStarted;
  const rejectedGeneration = session.liveSessionId;
  await session.cleanupVoice('rejecting generation detached');
  const afterRejection = await session.fetch(attachRequest());
  rejectOld(new Error('old model failed')); await settle();
  assert(!afterRejection.webSocket.peer.sent.some(m => m.event?.content?.includes('hit a snag')), 'old failure cannot speak to replacement');
  const rpcBeforeStaleTool = rpc.length;
  const staleTool = await session.executeOrchestratorTool(call('begin_prompt_turn', { alias: 'Fir', prompt: 'Must never dispatch' }), rejectedGeneration);
  assert.equal(staleTool.ok, false);
  assert.equal(rpc.length, rpcBeforeStaleTool, 'old-generation tool cannot begin on replacement');
  const currentAttachment = session.liveSessionId;
  const originalTiming = session.timing;
  session.timing = function(event, ...args) {
    originalTiming.call(this, event, ...args);
    if (event === 'tool.started') this.liveSessionId = 'replacement-during-telemetry';
  };
  try {
    const delayedDispatch = await session.executeOrchestratorTool(call('begin_prompt_turn', { alias: 'Fir', prompt: 'No dispatch after telemetry boundary' }), currentAttachment);
    assert.equal(delayedDispatch.ok, false);
    assert.equal(rpc.length, rpcBeforeStaleTool, 'generation is rechecked after telemetry and before dispatch');
  } finally { session.timing = originalTiming; session.liveSessionId = currentAttachment; }
  await session.cleanupVoice('test complete');

  const nativeTimeout = globalThis.setTimeout;
  let expire;
  globalThis.setTimeout = (fn, ms, ...args) => { if (ms === 60000) expire = fn; return nativeTimeout(fn, ms, ...args); };
  const leased = await session.fetch(attachRequest());
  globalThis.setTimeout = nativeTimeout;
  expire(); await settle();
  assert.equal(leased.webSocket.peer.readyState, 3, 'authentication lease expiration detaches the frontend');
  assert(!rpc.some(m => m.method === 'session/cancel'));
  const current = await session.fetch(attachRequest());
  const currentGeneration = current.webSocket.peer.sent[0].attachment;
  current.webSocket.send(envelope({ type: 'session.output_transcript.delta', delta: 'stale words', start_ms: 4, end_ms: 4, delivery: 'text_displayed', complete: true, request_id: openingId }, 'stale-request', currentGeneration));
  await session.liveMessageChain; await settle();
  assert.equal(current.webSocket.peer.readyState, 3, 'old source request rejected on replacement attachment');
  await session.cleanupVoice('test complete');

  // PSTN reserves before identity/storage awaits too, not only the app path.
  const pstn = new VoiceSession(state, { ...env, DB: db }); await initialized;
  pstn.record = async () => {}; pstn.flushTiming = async () => {}; pstn.writeNextGreeting = async () => {};
  pstn.ensureAcp = () => {}; pstn.playFixture = async () => true;
  let releasePstn;
  const pstnGate = new Promise(resolve => { releasePstn = resolve; });
  pstn.connectLive = async () => { await pstnGate; pstn.live = new Socket(); pstn.liveAttached = true; };
  const pendingPstn = pstn.fetch(new Request('https://object/attach', { method: 'POST', headers: { ...headers, 'x-dogwalk-call-sid': 'PSTN', 'x-dogwalk-live-session-id': 'pstn' } }));
  assert.equal((await pstn.fetch(attachRequest())).status, 409);
  releasePstn(); assert.equal((await pendingPstn).status, 204);
  assert.equal((await pstn.fetch(attachRequest())).status, 409);
  await pstn.cleanupVoice('test complete');
  if (pstn.timingTimer) clearTimeout(pstn.timingTimer);
  if (pstn.transcriptSaveTimer) clearTimeout(pstn.transcriptSaveTimer);
  session.env = { ...localEnv, DB: db };
  session.acpReady = true;
  const localCreated = await session.createManagedSession('Local', '.');
  assert.equal(localCreated.ok, true);
  assert.equal(rpc.filter(m => m.method === 'session/new').at(-1).params.cwd, localEnv.LOCAL_APP_TRIAL_WORKSPACE);
  await assert.rejects(() => session.createManagedSession('Outside', '/home/daytona'));
  await assert.rejects(() => session.daytona('/sandbox/anything/start', 'POST'));
  for (const name of ['restart_sandbox', 'play_audio', 'pair_browser', 'discover_persisted_sessions', 'create_invitation']) {
    assert.equal((await session.executeOrchestratorToolInner({ name, arguments: '{}' })).ok, false, `local trial must not dispatch ${name}`);
  }
  if (session.timingTimer) clearTimeout(session.timingTimer);
  if (session.transcriptSaveTimer) clearTimeout(session.transcriptSaveTimer);
  console.log('app frontend auth, binding, protocol and Orchestrator/ACP integration checks passed');
} finally { await rm(temp, { recursive: true, force: true }); }

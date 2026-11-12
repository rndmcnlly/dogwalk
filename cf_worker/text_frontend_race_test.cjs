// Deterministic adapter startup race against the real VoiceSession class.
// Only runtime/network boundaries are fake; no Worker or model service is contacted.
const assert = require('node:assert/strict');
const { buildSync } = require('esbuild');
const bundled = buildSync({
  entryPoints: ['src/voice_session.ts'], bundle: true, platform: 'node',
  format: 'cjs', loader: { '.md': 'text', '.pcm': 'binary' }, write: false,
}).outputFiles[0].text;
const mod = { exports: {} };
new Function('module', 'exports', 'require', bundled)(mod, mod.exports, require);
const { VoiceSession } = mod.exports;

class Socket {
  static OPEN = 1;
  readyState = 1;
  sent = [];
  accept() {}
  addEventListener() {}
  send(raw) { this.sent.push(JSON.parse(raw)); }
  close() { this.readyState = 3; }
}
global.WebSocket = Socket;
global.WebSocketPair = class { constructor() { this[0] = new Socket(); this[1] = new Socket(); } };
// Node's Response does not support Cloudflare's 101 / webSocket extension.
global.Response = class { constructor(body, options) { Object.assign(this, options); } };

(async () => {
  const saved = new Map();
  let boot;
  const state = {
    blockConcurrencyWhile(fn) { boot = fn(); },
    storage: {
      async get(key) { return saved.get(key); },
      async put(key, value) {
        if (typeof key === 'string') saved.set(key, value);
        else for (const [k, v] of Object.entries(key)) saved.set(k, v);
      },
      async delete(keys) { for (const key of Array.isArray(keys) ? keys : [keys]) saved.delete(key); },
      async setAlarm() {},
    },
  };
  const env = {
    ENABLE_TEXT_FRONTEND_SPIKE: 'true',
    DB: { prepare() { return { bind() { return this; }, async run() {} }; } },
  };
  const session = new VoiceSession(state, env);
  await boot;
  let release, entered;
  const paused = new Promise(resolve => { entered = resolve; });
  const gate = new Promise(resolve => { release = resolve; });
  session.recoverTranscript = async () => { entered(); await gate; };
  session.record = async () => {};
  session.sinceLastCall = async () => null;
  session.ensureAcp = () => {};
  session.feedPush = () => {};
  session.timing = () => {};
  let modelCalls = 0, modelEntered;
  const modeled = new Promise(resolve => { modelEntered = resolve; });
  session.orchestratorModel = async () => { modelCalls++; modelEntered(); return []; };
  const request = () => new Request('https://voice-session/dev/voice-frontend', { headers: {
    Upgrade: 'websocket', 'x-dogwalk-phone': '+15555550100',
    'x-dogwalk-sandbox-id': 'mock-sandbox', 'x-dogwalk-identity': 'mock-identity',
  } });
  const attaching = session.fetch(request());
  await paused;
  const socket = session.live;
  assert.equal((await session.fetch(request())).status, 409, 'startup reservation must reject a competing frontend');
  assert.equal(session.frontendAppend('say', { text: 'Background completion.' }).ok, true);
  await session.orchestratorEvent('[harness event] background_turn_stopped');
  assert.deepEqual(socket.sent, [], 'in-flight speech must not precede ready');
  assert.deepEqual(session.orchestratorQueue, [], 'background events must wait for call_started');
  assert.equal(modelCalls, 0, 'startup must not launch an Orchestrator call');
  release();
  assert.equal((await attaching).status, 101);
  await modeled;
  assert.equal(socket.sent[0].type, 'dev.frontend.ready');
  assert.equal(socket.sent[1].type, 'session.commentary.append');
  assert.equal(socket.sent[1].content, socket.sent[0].opening);
  assert.equal(socket.sent[2].content, 'Background completion.');
  assert.equal(socket.sent.filter(item => item.content === socket.sent[0].opening).length, 1);
  const context = session.orchestratorContext.find(item => item.role === 'user').content;
  assert(context.indexOf('call_started') < context.indexOf('background_turn_stopped'));
  assert.equal(modelCalls, 1);
  console.log('development frontend deterministic startup race checks passed');
})().catch(error => { console.error(error); process.exitCode = 1; });

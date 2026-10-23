// Test-only: runs the real add-on (background.js) against the simulated
// Thunderbird, speaking native messaging on stdin/stdout like Thunderbird does.
// When stdin closes, prints what was sent and moved as JSON on stderr.
'use strict';
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const {createFakeMessenger} = require('./fake-messenger.cjs');

const {api, state} = createFakeMessenger();
// Like a person finishing Thunderbird's add account page shortly after it opens.
state.addAfterSetupMs = Number(process.env.FAKE_THUNDERBIRD_ADD_MS || 300);
const messageListeners = [], disconnectListeners = [];

function write(message) {
  const data = Buffer.from(JSON.stringify(message), 'utf8');
  const header = Buffer.alloc(4); header.writeUInt32LE(data.length, 0);
  process.stdout.write(Buffer.concat([header, data]));
}

let buffer = Buffer.alloc(0);
process.stdin.on('data', chunk => {
  buffer = Buffer.concat([buffer, chunk]);
  while (buffer.length >= 4) {
    const size = buffer.readUInt32LE(0);
    if (buffer.length < 4 + size) break;
    const message = JSON.parse(buffer.subarray(4, 4 + size).toString('utf8'));
    buffer = buffer.subarray(4 + size);
    for (const listener of messageListeners) listener(message);
  }
});
process.stdin.on('end', () => {
  for (const listener of disconnectListeners) listener();
  process.stderr.write(JSON.stringify({sent: state.sent.map(s => ({kind: s.kind, replyType: s.replyType, messageId: s.messageId,
    identityId: (s.details || {}).identityId, to: s.current.to, cc: s.current.cc, bcc: s.current.bcc, subject: s.current.subject,
    body: s.current.body, plainTextBody: s.current.plainTextBody})), moved: state.moved, deleted: state.deleted}));
  process.exit(0);
});

api.runtime.connectNative = name => {
  if (name !== 'com.voicecompanion.mail') throw new Error('Unknown native host');
  return {
    postMessage: write,
    onMessage: {addListener: listener => messageListeners.push(listener)},
    onDisconnect: {addListener: listener => disconnectListeners.push(listener)},
  };
};

const context = vm.createContext({console, setTimeout, messenger: api, JSON});
vm.runInContext(fs.readFileSync(path.join(__dirname, 'background.js'), 'utf8'), context);

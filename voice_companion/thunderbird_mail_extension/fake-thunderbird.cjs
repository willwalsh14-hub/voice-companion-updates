// Test-only: runs the real add-on (background.js) against the simulated
// Thunderbird, speaking native messaging on stdin/stdout like Thunderbird does.
// When stdin closes, prints what was sent and moved as JSON on stderr.
'use strict';
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const {createFakeMessenger, addSecondAccount, withoutAccounts} = require('./fake-messenger.cjs');

const {api, state} = createFakeMessenger();
// Like a person finishing Thunderbird's add account page shortly after it opens.
state.addAfterSetupMs = Number(process.env.FAKE_THUNDERBIRD_ADD_MS || 300);
// New mail waiting on the server, and how Thunderbird's check for it goes.
if (process.env.FAKE_THUNDERBIRD_SERVER_MAIL) {
  state.server.push({id: 26, folderId: 'account1://INBOX', date: new Date(Date.UTC(2026, 9, 26, 9)), author: 'Newest <newest@example.com>',
    subject: 'Message 26', read: false, size: 1026, recipients: ['me@gmail.com'], ccList: [], headerMessageId: 'msg26@example.com',
    html: '', plain: 'Plain text of message 26', replyTo: ''});
}
state.mailCheck = process.env.FAKE_THUNDERBIRD_MAIL_CHECK || 'ok';
// Calendars: only Thunderbird's own Home calendar (no Google calendar added), or answers that cannot be read.
if (process.env.FAKE_THUNDERBIRD_ONLY_HOME_CALENDAR) { state.calendars = state.calendars.filter(c => c.type === 'storage'); state.events = []; }
if (process.env.FAKE_THUNDERBIRD_CALENDAR_BROKEN) state.calendarBroken = true;
// Contacts and calendars from two email accounts (Gmail and Outlook), or an add-on from before accounts were given.
if (process.env.FAKE_THUNDERBIRD_TWO_ACCOUNTS) addSecondAccount(state);
if (process.env.FAKE_THUNDERBIRD_NO_ACCOUNTS) withoutAccounts(state, api);
// A calendar Thunderbird cannot read now (by name, as the Experiment reports it), or an address book (by id).
if (process.env.FAKE_THUNDERBIRD_FAILED_CALENDAR) state.failedCalendars = [process.env.FAKE_THUNDERBIRD_FAILED_CALENDAR];
if (process.env.FAKE_THUNDERBIRD_FAILED_BOOK) state.failBook = process.env.FAKE_THUNDERBIRD_FAILED_BOOK;
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
    body: s.current.body, plainTextBody: s.current.plainTextBody,
    attachments: (s.attachments || []).map(({file}) => ({name: file.name, size: file.size}))})),
    moved: state.moved, deleted: state.deleted}));
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

const context = vm.createContext({console, setTimeout, clearTimeout, messenger: api, JSON, File, Blob, btoa, atob});
vm.runInContext(fs.readFileSync(path.join(__dirname, 'background.js'), 'utf8'), context);
// Contacts and calendars (pim.js). FAKE_THUNDERBIRD_OLD_ADDON is a copy installed before they existed.
if (!process.env.FAKE_THUNDERBIRD_OLD_ADDON) vm.runInContext(fs.readFileSync(path.join(__dirname, 'pim.js'), 'utf8'), context);
// A short wait for the mail check, so a check that never ends does not slow the tests.
if (process.env.FAKE_THUNDERBIRD_CHECK_MS) {
  vm.runInContext('MailBridge.MAIL_CHECK_MS = ' + Number(process.env.FAKE_THUNDERBIRD_CHECK_MS), context);
}

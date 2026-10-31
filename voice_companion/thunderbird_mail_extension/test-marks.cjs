// Tests for read, unread, flags, Gmail's Important marker and read receipts (experiment/marks.js,
// and the add-on's use of it in background.js), against stand-ins for Thunderbird's own objects.
// Run: node voice_companion/thunderbird_mail_extension/test-marks.cjs
// Real Thunderbird is checked on Windows by check_thunderbird_marks_live.py.
'use strict';
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const {createFakeMessenger} = require('./fake-messenger.cjs');

const plain = value => JSON.parse(JSON.stringify(value));
const READ = 0x1, FLAGGED = 0x4, DELETED = 0x8, MDN_NEEDED = 0x00400000, MDN_SENT = 0x00800000;

// ---- Stand-ins for Thunderbird's mail objects (nsIMsgFolder, nsIMsgDBHdr, nsIMsgIncomingServer) ----

function fakeThunderbird() {
  const tb = {offline: false, stores: [], copies: [], expunged: [], refreshes: [], closed: [], receipts: [],
    // What the IMAP server answers to each STORE, in order: true (OK), false (NO), 'silent' (never).
    storeAnswers: [], refreshAnswer: true, copyAnswer: true, mdnAsk: false};
  const answer = (listener, ok) => { if (ok !== 'silent') setTimeout(() => listener.OnStopRunningUrl({}, ok ? 0 : 0x80004005), 1); };

  function server(name, type, gmail) {
    const s = {key: name, type, isGMailServer: gmail, folders: [],
      QueryInterface(iface) { if (iface === 'nsIImapIncomingServer' && type !== 'imap') throw new Error('no'); return s; },
      closeCachedConnections() { tb.closed.push(name); }};
    s.rootFolder = {get descendants() { return s.folders; },
      getFolderWithFlags(flag) { return s.folders.find(f => f.flags & flag) || null; }};
    return s;
  }

  function folder(owner, name, {online = '', flags = 0} = {}) {
    const f = {URI: owner.key + '/' + name, server: owner, flags, headers: [], onlineName: online,
      QueryInterface(iface) { if (iface === 'nsIMsgImapMailFolder' && owner.type !== 'imap') throw new Error('no'); return f; },
      isSpecialFolder() { return false; },
      get msgDatabase() {
        return {reverseEnumerateMessages: () => f.headers.slice().reverse(),
          getMsgHdrForMessageID: id => f.headers.find(h => h.messageId === id && !h.gone) || null};
      },
      markMessagesRead(list, on) { for (const h of list) h.flags = on ? h.flags | READ : h.flags & ~READ; },
      markMessagesFlagged(list, on) { for (const h of list) h.flags = on ? h.flags | FLAGGED : h.flags & ~FLAGGED; },
      updateFolderWithListener(win, listener) { tb.refreshes.push(f.URI); answer(listener, tb.refreshAnswer); },
      storeImapFlags(flag, on, keys, listener) {
        tb.stores.push({folder: name, flag, on, keys: Array.from(keys)});
        if (flag === DELETED && on) for (const h of f.headers) if (keys.includes(h.messageKey)) h.gone = true;
        answer(listener, tb.storeAnswers.length ? tb.storeAnswers.shift() : true);
      },
      expunge(listener) { tb.expunged.push(name); answer(listener, true); }};
    owner.folders.push(f);
    return f;
  }

  let nextKey = 1, nextId = 100;
  function message(where, {subject = '', thread = '', flags = 0, messageId = '', junk = ''} = {}) {
    const h = {messageKey: nextKey++, extId: nextId++, folder: where, flags, subject,
      messageId: messageId || 'm' + nextKey + '@example.com',
      getStringProperty(name) { return name === 'X-GM-THRID' ? thread : name === 'junkscore' ? junk : ''; }};
    where.headers.push(h);
    return h;
  }

  const gmail = server('gmail', 'imap', true);
  const gmailInbox = folder(gmail, 'INBOX', {online: 'INBOX', flags: 0x1000});
  const important = folder(gmail, '[Gmail]/Important', {online: '[Gmail]/Important'});
  const sentMail = folder(gmail, '[Gmail]/Sent Mail', {online: '[Gmail]/Sent Mail', flags: 0x200});
  const work = server('work', 'imap', false);
  const workInbox = folder(work, 'INBOX', {online: 'INBOX', flags: 0x1000});
  const graph = server('graph', 'graph', false);
  const graphInbox = folder(graph, 'Inbox', {flags: 0x1000});
  const pop = server('pop', 'pop3', false);
  const popInbox = folder(pop, 'Inbox', {flags: 0x1000});
  Object.assign(tb, {gmail, gmailInbox, important, sentMail, work, workInbox, graph, graphInbox, pop, popInbox, message});

  const all = () => [gmail, work, graph, pop].flatMap(s => s.folders).flatMap(f => f.headers);
  tb.extension = {messageManager: {
    get: id => all().find(h => h.extId === id && !h.gone) || null,
    convert: h => ({id: h.extId}),
  }};
  tb.MailServices = {
    accounts: {getAccount: id => ({account1: {incomingServer: gmail}, account2: {incomingServer: graph}})[id] || null},
    copy: {copyMessages(source, list, target, move, listener) {
      tb.copies.push({from: source.URI, to: target.URI, ids: Array.from(list, h => h.messageId)});
      if (tb.copyAnswer) for (const h of list) message(target, {subject: h.subject, messageId: h.messageId, flags: h.flags});
      setTimeout(() => listener.onStopCopy(tb.copyAnswer ? 0 : 0x80004005), 1);
    }}};
  return tb;
}

function loadMarks(tb) {
  // Thunderbird's waits are shortened a thousand times, so the slow paths still run.
  const timer = {setTimeout: (f, ms) => setTimeout(f, Math.ceil(ms / 1000)), clearTimeout};
  const modules = url => (url.includes('Timer') ? timer : url.includes('MailServices') ? {MailServices: tb.MailServices}
    : {ExtensionCommon: {ExtensionAPI: class {}}});
  const context = vm.createContext({
    ChromeUtils: {importESModule: modules, generateQI: () => function QueryInterface() { return this; }},
    Services: {get io() { return {offline: tb.offline}; },
      wm: {getMostRecentWindow: () => ({msgWindow: {name: 'main window'}})}},
    Ci: {nsIMsgImapMailFolder: 'nsIMsgImapMailFolder', nsIImapIncomingServer: 'nsIImapIncomingServer',
      nsIMimeHeaders: {}, nsIMsgMdnGenerator: {eDisplayed: 0}, nsMsgFolderFlags: {Newsgroup: 0x1}},
    Cc: {
      '@mozilla.org/messenger/mimeheaders;1': {createInstance: () => ({
        initialize(text) { this.text = text; },
        extractHeader(name) { const m = new RegExp('^' + name + ':\\s*(.*)$', 'im').exec(this.text); return m ? m[1] : null; }})},
      '@mozilla.org/messenger-mdn/generator;1': {createInstance: () => ({
        process(type, win, where, key, headers) {
          tb.receipts.push({how: 'process', key, window: win && win.name, asked: headers.extractHeader('Disposition-Notification-To')});
          if (!tb.mdnAsk) where.headers.find(h => h.messageKey === key).flags |= MDN_SENT;
          return tb.mdnAsk;
        },
        userAgreed() { tb.receipts.push({how: 'agreed'}); },
        userDeclined() { tb.receipts.push({how: 'declined'}); }})},
    },
    Date, Promise, Map, Set, Number, String, Array, Boolean, RegExp, Error,
  });
  vm.runInContext(fs.readFileSync(path.join(__dirname, 'experiment', 'marks.js'), 'utf8'), context);
  const Api = vm.runInContext('voiceCompanionMarks', context);
  const instance = new Api();
  return instance.getAPI({extension: tb.extension}).voiceCompanionMarks;
}

async function experimentChecks() {
  // Gmail: marking read waits for Gmail's own answer, and takes in the conversation's other
  // unread messages (same X-GM-THRID) in that folder, as Gmail's website does.
  let tb = fakeThunderbird();
  let marks = loadMarks(tb);
  const early = tb.message(tb.gmailInbox, {subject: 'Your Saturday morning trip with Uber', thread: 't1'});
  const other = tb.message(tb.gmailInbox, {subject: 'Something else', thread: 't2'});
  const readBefore = tb.message(tb.gmailInbox, {subject: 'Re: trip', thread: 't1', flags: READ});
  const late = tb.message(tb.gmailInbox, {subject: 'Your Saturday morning trip with Uber', thread: 't1'});
  let result = plain(await marks.mark([late.extId], true, null, true));
  assert.deepEqual(result, {changed: 1, missing: 0, conversation: 1, conversationIds: [early.extId], server: 'confirmed'});
  assert.ok(early.flags & READ && late.flags & READ, 'the whole conversation is read');
  assert.ok(!(other.flags & READ), 'another conversation is left alone');
  assert.deepEqual(tb.stores, [{folder: 'INBOX', flag: READ, on: true, keys: [late.messageKey, early.messageKey]}]);
  assert.ok(readBefore.flags & READ);

  // Without the conversation, or marking unread, only the chosen messages change.
  late.flags = 0; early.flags = 0; tb.stores.length = 0;
  result = plain(await marks.mark([late.extId], true, null, false));
  assert.equal(result.conversation, 0);
  assert.ok(!(early.flags & READ));
  early.flags = READ; late.flags = READ;
  result = plain(await marks.mark([late.extId], false, null, true));
  assert.equal(result.conversation, 0);
  assert.ok(early.flags & READ, 'marking unread does not take in the conversation');
  assert.ok(!(late.flags & READ));

  // Flags: only the chosen message, and confirmed.
  tb.stores.length = 0;
  result = plain(await marks.mark([late.extId], null, true, true));
  assert.equal(result.server, 'confirmed');
  assert.deepEqual(tb.stores, [{folder: 'INBOX', flag: FLAGGED, on: true, keys: [late.messageKey]}]);

  // Another IMAP server (not Gmail): no conversation, still confirmed.
  const workMessage = tb.message(tb.workInbox, {thread: 't1'});
  const workOther = tb.message(tb.workInbox, {thread: 't1'});
  result = plain(await marks.mark([workMessage.extId], true, null, true));
  assert.deepEqual(result, {changed: 1, missing: 0, conversation: 0, conversationIds: [], server: 'confirmed'});
  assert.ok(!(workOther.flags & READ));

  // Will, October 31, 2026: a mark that never reached Gmail until Thunderbird restarted.
  // A NO is tried again; a second NO drops Thunderbird's connections, then a fresh one is tried.
  tb.stores.length = 0;
  tb.storeAnswers = [false, false, true];
  result = plain(await marks.mark([other.extId], true, null, true));
  assert.equal(result.server, 'confirmed');
  assert.equal(tb.stores.length, 3);
  assert.deepEqual(tb.closed, ['gmail'], 'connections are dropped once, before the third try');
  // Never answered three times: said as not confirmed (the mark stays in Thunderbird).
  tb.stores.length = 0; tb.closed.length = 0;
  tb.storeAnswers = ['silent', 'silent', 'silent'];
  result = plain(await marks.mark([other.extId], false, null, true));
  assert.equal(result.server, 'failed');
  assert.equal(tb.stores.length, 3);
  assert.ok(!(other.flags & READ), 'Thunderbird still shows the change');
  tb.storeAnswers = [];

  // Microsoft Graph: Thunderbird sends the change itself; the folder is synced right after.
  const graphMessage = tb.message(tb.graphInbox, {thread: 't1'});
  result = plain(await marks.mark([graphMessage.extId], true, null, true));
  assert.deepEqual(result, {changed: 1, missing: 0, conversation: 0, conversationIds: [], server: 'sent'});
  assert.ok(graphMessage.flags & READ);
  assert.ok(tb.refreshes.includes('graph/Inbox'));
  tb.refreshAnswer = false;
  assert.equal((await marks.mark([graphMessage.extId], false)).server, 'failed', 'Microsoft could not be reached');
  tb.refreshAnswer = 'silent';
  assert.equal((await marks.mark([graphMessage.extId], true)).server, 'failed');
  tb.refreshAnswer = true;

  // POP keeps mail on this computer; offline sends later; a gone message is counted.
  const popMessage = tb.message(tb.popInbox);
  assert.equal((await marks.mark([popMessage.extId], true)).server, 'local');
  tb.offline = true;
  tb.stores.length = 0;
  assert.equal((await marks.mark([other.extId], true, null, true)).server, 'offline');
  assert.equal(tb.stores.length, 0);
  tb.offline = false;
  assert.deepEqual(plain(await marks.mark([other.extId, 999], true)),
    {changed: 1, missing: 1, conversation: 0, conversationIds: [], server: 'confirmed'});
  assert.deepEqual(plain(await marks.mark([999], true)), {changed: 0, missing: 1, conversation: 0, conversationIds: [], server: 'none'});
  // Two accounts at once: the worst answer is said.
  tb.storeAnswers = [false, false, false];
  assert.equal((await marks.mark([other.extId, popMessage.extId], true)).server, 'failed');
  tb.storeAnswers = [];

  // ---- Gmail's Important marker ----
  tb = fakeThunderbird();
  marks = loadMarks(tb);
  const receipt = tb.message(tb.gmailInbox, {subject: 'Uber receipt', messageId: 'uber1@uber.com'});
  assert.deepEqual(plain(await marks.important(receipt.extId, true)), {done: true});
  assert.deepEqual(tb.copies, [{from: 'gmail/INBOX', to: 'gmail/[Gmail]/Important', ids: ['uber1@uber.com']}]);
  assert.deepEqual(plain(await marks.isImportant([receipt.extId])), {[receipt.extId]: true});
  assert.deepEqual(plain(await marks.important(receipt.extId, true)), {done: true, already: true});
  assert.equal(tb.copies.length, 1, 'not copied twice');
  // Off: only the copy in Important is deleted (Gmail then removes only the marker).
  assert.deepEqual(plain(await marks.important(receipt.extId, false)), {done: true});
  assert.deepEqual(tb.stores.at(-1).folder, '[Gmail]/Important');
  assert.equal(tb.stores.at(-1).flag, DELETED);
  assert.deepEqual(tb.expunged, ['[Gmail]/Important']);
  assert.ok(!receipt.gone, 'the message in the Inbox stays');
  assert.deepEqual(plain(await marks.isImportant([receipt.extId])), {[receipt.extId]: false});
  assert.deepEqual(plain(await marks.important(receipt.extId, false)), {done: true, already: true});
  // Opened from the Important folder itself: refused, so the message is never deleted.
  const inImportant = tb.message(tb.important, {messageId: 'x@y'});
  assert.deepEqual(plain(await marks.important(inImportant.extId, false)), {done: false, reason: 'inImportant'});
  assert.ok(!inImportant.gone);
  // Not Gmail, offline, hidden label, failed copy, gone message.
  assert.equal((await marks.important(tb.message(tb.workInbox).extId, true)).reason, 'notGmail');
  assert.equal((await marks.important(tb.message(tb.graphInbox).extId, true)).reason, 'notGmail');
  assert.equal((await marks.important(999, true)).reason, 'missing');
  tb.offline = true;
  assert.equal((await marks.important(receipt.extId, true)).reason, 'offline');
  tb.offline = false;
  tb.copyAnswer = false;
  assert.equal((await marks.important(receipt.extId, true)).reason, 'failed');
  tb.copyAnswer = true;
  tb.important.onlineName = '[Gmail]/Hidden';
  assert.equal((await marks.important(receipt.extId, true)).reason, 'noFolder');
  assert.deepEqual(plain(await marks.isImportant([receipt.extId])), {}, 'nothing said when the label is hidden');

  // After sending with high importance: Gmail's Sent Mail copy gets the marker.
  tb = fakeThunderbird();
  marks = loadMarks(tb);
  setTimeout(() => tb.message(tb.sentMail, {messageId: 'sent1@gmail.com'}), 3);   // Gmail files it a moment later
  assert.deepEqual(plain(await marks.sentImportant('account1', '<sent1@gmail.com>')), {done: true});
  assert.deepEqual(tb.copies.at(-1), {from: 'gmail/[Gmail]/Sent Mail', to: 'gmail/[Gmail]/Important', ids: ['sent1@gmail.com']});
  // Thunderbird may not tag Gmail's Sent Mail as its Sent folder: it is found by its Gmail name.
  tb.sentMail.flags = 0;
  tb.message(tb.sentMail, {messageId: 'sent2@gmail.com'});
  assert.deepEqual(plain(await marks.sentImportant('account1', 'sent2@gmail.com')), {done: true});
  assert.equal((await marks.sentImportant('account2', 'x@y')).reason, 'notGmail');
  assert.equal((await marks.sentImportant('account9', 'x@y')).reason, 'missing');
  assert.equal((await marks.sentImportant('account1', 'never@gmail.com')).reason, 'notFound');

  // ---- Read receipts, as Thunderbird's own message view handles them ----
  tb = fakeThunderbird();
  marks = loadMarks(tb);
  const asks = tb.message(tb.gmailInbox, {flags: MDN_NEEDED});
  const quiet = tb.message(tb.gmailInbox);
  const junk = tb.message(tb.gmailInbox, {flags: MDN_NEEDED, junk: '100'});
  const done = tb.message(tb.gmailInbox, {flags: MDN_NEEDED | MDN_SENT});
  assert.equal(await marks.needsReceipt(asks.extId), 'yes');
  assert.equal(await marks.needsReceipt(quiet.extId), 'maybe');
  assert.equal(await marks.needsReceipt(junk.extId), '', 'never for junk');
  assert.equal(await marks.needsReceipt(done.extId), '', 'never twice');
  assert.equal(await marks.needsReceipt(999), '');
  const headers = 'From: Ann <ann@example.com>\r\nDisposition-Notification-To: ann@example.com\r\n';
  // Settings say send (or never): Thunderbird handles it without a question.
  assert.deepEqual(plain(await marks.receipt(asks.extId, headers)), {state: 'handled'});
  assert.deepEqual(tb.receipts[0], {how: 'process', key: asks.messageKey, window: 'main window', asked: 'ann@example.com'});
  assert.equal(await marks.needsReceipt(asks.extId), '', 'handled once');
  assert.deepEqual(plain(await marks.receipt(quiet.extId, 'From: Bo <bo@example.com>\r\n')), {state: 'none'});
  // Settings say ask: Voice Companion asks, then answers.
  tb.mdnAsk = true;
  const second = tb.message(tb.gmailInbox, {flags: MDN_NEEDED});
  assert.deepEqual(plain(await marks.receipt(second.extId, headers)), {state: 'ask'});
  assert.deepEqual(plain(await marks.receiptAnswer(second.extId, true)), {done: true});
  assert.deepEqual(tb.receipts.at(-1), {how: 'agreed'});
  assert.deepEqual(plain(await marks.receiptAnswer(second.extId, true)), {done: false}, 'answered once');
  const third = tb.message(tb.gmailInbox, {flags: MDN_NEEDED});
  await marks.receipt(third.extId, headers);
  assert.deepEqual(plain(await marks.receiptAnswer(third.extId, false)), {done: true});
  assert.deepEqual(tb.receipts.at(-1), {how: 'declined'});
}

// ---- The add-on (background.js) with the Experiment present ----

function load(api) {
  const context = vm.createContext({console, setTimeout, clearTimeout, VOICE_COMPANION_TEST: true, messenger: api, File, Blob, btoa, atob});
  vm.runInContext(fs.readFileSync(path.join(__dirname, 'background.js'), 'utf8'), context);
  const Bridge = vm.runInContext('MailBridge', context);
  return {bridge: new Bridge(api), Bridge};
}

async function bridgeChecks() {
  const {api, state} = createFakeMessenger();
  const calls = [];
  const experiment = {server: 'confirmed', conversationIds: [], receiptDue: '', receiptState: 'ask', important: {done: true},
    sent: {done: true}, sentDelay: 0};
  api.voiceCompanionMarks = {
    mark: async (ids, read, flagged, conversation) => {
      calls.push({mark: ids, read, flagged, conversation});
      const found = ids.filter(id => state.messages.some(m => m.id === id));
      for (const id of found) { const m = state.messages.find(x => x.id === id); if (typeof read === 'boolean') m.read = read; if (typeof flagged === 'boolean') m.flagged = flagged; }
      return {changed: found.length, missing: ids.length - found.length, conversation: experiment.conversationIds.length,
        conversationIds: experiment.conversationIds, server: found.length ? experiment.server : 'none'};
    },
    important: async (id, on) => { calls.push({important: id, on}); return experiment.important; },
    isImportant: async ids => Object.fromEntries(ids.filter(id => id === 25).map(id => [String(id), true])),
    sentImportant: async (accountId, messageId) => {
      calls.push({sentImportant: messageId, accountId});
      await new Promise(resolve => setTimeout(resolve, experiment.sentDelay));
      return experiment.sent;
    },
    needsReceipt: async () => experiment.receiptDue,
    receipt: async (id, headers) => { calls.push({receipt: id, headers}); return {state: experiment.receiptState}; },
    receiptAnswer: async (id, send) => { calls.push({receiptAnswer: id, send}); return {done: true}; },
  };
  api.messages.getRaw = async id => 'From: a@b\r\nDisposition-Notification-To: a@b\r\nSubject: x\r\n\r\nBody text\r\n';
  const {bridge, Bridge} = load(api);
  const call = async (method, params) => {
    const answer = await bridge.handle({id: 1, method, params});
    if (answer.error) throw Object.assign(new Error(answer.error.message), {answer});
    return JSON.parse(JSON.stringify(answer.result));
  };

  // Marking goes through the Experiment, with the whole Gmail conversation for read.
  experiment.conversationIds = [21];
  assert.deepEqual(await call('mark', {ids: [25], read: true}),
    {changed: 1, missing: 0, server: 'confirmed', conversation: 1, conversationIds: [21]});
  assert.deepEqual(calls.at(-1), {mark: [25], read: true, flagged: null, conversation: true});
  experiment.conversationIds = [];
  experiment.server = 'failed';
  assert.equal((await call('mark', {ids: [25], flagged: true})).server, 'failed');
  experiment.server = 'local';
  assert.equal((await call('mark', {ids: [25], read: false})).thisComputerOnly, true);
  experiment.server = 'confirmed';
  await assert.rejects(call('mark', {ids: [999], read: true}), /could not change that message/);

  // The list says Gmail's Important marker.
  const page = await call('list', {folderId: 'account1://INBOX', offset: 0, limit: 10});
  assert.equal(page.rows.find(r => r.id === 25).important, true);
  assert.equal('important' in page.rows.find(r => r.id === 24), false, 'only where the Experiment knows');

  // Opening marks read (with the conversation) and handles a read receipt.
  experiment.conversationIds = [19, 17];
  experiment.receiptDue = 'yes';
  state.messages[24].read = false;
  let opened = await call('read', {messageId: 25});
  assert.equal(opened.read, true);
  assert.deepEqual(opened.conversationIds, [19, 17]);
  assert.equal(opened.receipt, 'ask');
  assert.match(calls.find(c => c.receipt).headers, /^From: a@b\r\nDisposition-Notification-To: a@b\r\nSubject: x\r\n$/);
  assert.deepEqual(await call('receiptAnswer', {messageId: 25, send: true}), {done: true});
  experiment.receiptDue = 'maybe';
  api.messages.getRaw = async () => 'From: a@b\r\nSubject: no receipt asked\r\n\r\nDisposition-Notification-To: in the body\r\n';
  calls.length = 0;
  opened = await call('read', {messageId: 25});
  assert.equal('receipt' in opened, false, 'a request in the body does not count');
  assert.equal(calls.some(c => c.receipt), false);
  experiment.receiptDue = '';
  experiment.server = 'failed';
  opened = await call('read', {messageId: 25});
  assert.equal(opened.readNotConfirmed, true);
  experiment.server = 'confirmed';
  experiment.conversationIds = [];
  // A very large message is not read whole just to look for a receipt request.
  experiment.receiptDue = 'maybe';
  state.messages[24].size = 9 * 1024 * 1024;
  calls.length = 0;
  await call('read', {messageId: 25});
  assert.equal(calls.some(c => c.receipt), false);
  state.messages[24].size = 1025;
  experiment.receiptDue = '';

  // Gmail's Important marker on a message you got.
  assert.deepEqual(await call('important', {messageId: 25, on: true}), {done: true, already: false});
  assert.deepEqual(calls.at(-1), {important: 25, on: true});
  experiment.important = {done: false, reason: 'notGmail'};
  await assert.rejects(call('important', {messageId: 25, on: true}), /Only Gmail accounts/);
  experiment.important = {done: false, reason: 'noFolder'};
  await assert.rejects(call('important', {messageId: 25, on: true}), /Show in IMAP/);
  experiment.important = {done: false, reason: 'something new'};
  await assert.rejects(call('important', {messageId: 25, on: true}), /Gmail did not take the change/);
  await assert.rejects(call('important', {messageId: '25', on: true}), /Choose a message first/);

  // Sending with high importance: Gmail's Sent copy gets the marker; normal importance does not ask.
  const send = {identityId: 'id1', to: ['ann@example.com'], subject: 'Hi', plain: 'Hello', html: '<p>Hello</p>'};
  calls.length = 0;
  let sent = await call('send', {...send, priority: 'high'});
  assert.equal(sent.sentImportant, 'done');
  assert.equal(calls.filter(c => c.sentImportant).length, 1);
  sent = await call('send', send);
  assert.equal('sentImportant' in sent, false);
  assert.equal(calls.filter(c => c.sentImportant).length, 1);
  experiment.sent = {done: false, reason: 'notGmail'};
  assert.equal('sentImportant' in await call('send', {...send, priority: 'highest'}), false);
  experiment.sent = {done: false, reason: 'notFound'};
  assert.equal((await call('send', {...send, priority: 'high'})).sentImportant, 'failed');
  // Gmail slow to file its copy: "Sent" is not held up; the marking carries on.
  experiment.sent = {done: true};
  experiment.sentDelay = 200;
  Bridge.SENT_IMPORTANT_MS = 20;
  assert.equal((await call('send', {...send, priority: 'high'})).sentImportant, 'pending');

  // Without the Experiment (an add-on that has not restarted yet): marking still works.
  delete api.voiceCompanionMarks;
  assert.deepEqual(await call('mark', {ids: [25], read: true}), {changed: 1, missing: 0});
  await assert.rejects(call('important', {messageId: 25, on: true}), /newer Voice Companion add-on/);
  assert.deepEqual(await call('receiptAnswer', {messageId: 25, send: true}), {done: false});

  // The manifest loads the Experiment.
  const manifest = JSON.parse(fs.readFileSync(path.join(__dirname, 'manifest.json'), 'utf8'));
  assert.equal(manifest.experiment_apis.voiceCompanionMarks.parent.script, 'experiment/marks.js');
  assert.ok(fs.existsSync(path.join(__dirname, manifest.experiment_apis.voiceCompanionMarks.schema)));
  const schema = JSON.parse(fs.readFileSync(path.join(__dirname, 'experiment', 'marks-schema.json'), 'utf8'));
  const named = schema[0].functions.map(f => f.name).sort();
  const loaded = Object.keys(loadMarks(fakeThunderbird())).sort();
  assert.deepEqual(named, loaded, 'every function the schema names is there, and only those');
}

// Thunderbird loads every Experiment script of the add-on into one shared scope (October 31, 2026:
// marks.js reused two names from implementation.js and did not load at all in real Thunderbird,
// while these tests, loading each file alone, passed). Load them together, in manifest order.
function sharedScopeChecks() {
  const manifest = JSON.parse(fs.readFileSync(path.join(__dirname, 'manifest.json'), 'utf8'));
  const context = vm.createContext({
    ChromeUtils: {importESModule: url => (url.includes('Timer') ? {setTimeout, clearTimeout} : {ExtensionCommon: {ExtensionAPI: class {}}}),
      generateQI: () => function QueryInterface() { return this; }},
    Services: {}, Ci: {}, Cc: {}, setTimeout, clearTimeout,
  });
  for (const [name, api] of Object.entries(manifest.experiment_apis)) {
    const file = path.join(__dirname, api.parent.script);
    assert.doesNotThrow(() => vm.runInContext(fs.readFileSync(file, 'utf8'), context, {filename: file}), name);
    assert.equal(typeof vm.runInContext(name, context), 'function', name + ' is defined');
  }
}

module.exports = (async () => {
  sharedScopeChecks();
  await experimentChecks();
  await bridgeChecks();
  console.log('Read, unread, flag, Important and receipt add-on tests passed.');
})().catch(error => { console.error(error); process.exitCode = 1; });

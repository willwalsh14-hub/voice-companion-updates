// Tests for the Thunderbird mail add-on against a simulated Thunderbird.
// Run: node voice_companion/thunderbird_mail_extension/test-bridge.cjs
'use strict';
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const {createFakeMessenger} = require('./fake-messenger.cjs');

function load(api) {
  const context = vm.createContext({console, setTimeout, VOICE_COMPANION_TEST: true, messenger: api});
  vm.runInContext(fs.readFileSync(path.join(__dirname, 'background.js'), 'utf8'), context);
  return new (vm.runInContext('MailBridge', context))(api);
}

(async () => {
  const {api, state} = createFakeMessenger();
  const bridge = load(api);
  const call = async (method, params) => {
    const answer = await bridge.handle({id: 7, method, params});
    assert.equal(answer.id, 7);
    if (answer.error) throw Object.assign(new Error(answer.error.message), {answer});
    // Native messaging sends JSON, so compare what Companion would receive.
    return JSON.parse(JSON.stringify(answer.result));
  };

  // Only accounts that can send are offered.
  const accounts = await call('accounts');
  assert.deepEqual(accounts.map(a => a.id), ['account1']);
  assert.equal(accounts[0].identities[0].email, 'me@gmail.com');

  // Folder names are spoken without Gmail's [Gmail] prefix; the root is hidden.
  const folders = await call('folders', {accountId: 'account1'});
  assert.deepEqual(folders.map(f => f.name), ['Inbox', 'Sent Mail', 'Receipts', 'Trash']);
  const inbox = await call('inbox', {accountId: 'account1'});
  assert.equal(inbox.id, 'account1://INBOX');

  // Newest first, paged, across Thunderbird's own list pages.
  api.messages.query = (original => async q => original({...q, messagesPerPage: 7}))(api.messages.query);
  const first = await call('list', {folderId: inbox.id, offset: 0, limit: 10});
  assert.deepEqual(first.rows.map(r => r.id), [25, 24, 23, 22, 21, 20, 19, 18, 17, 16]);
  assert.equal(first.next, 10);
  assert.equal(first.rows[0].unread, true);
  assert.equal(first.rows[1].unread, false);
  const last = await call('list', {folderId: inbox.id, offset: 20, limit: 10});
  assert.deepEqual(last.rows.map(r => r.id), [5, 4, 3, 2, 1]);
  assert.equal(last.next, null);
  await assert.rejects(call('list', {folderId: inbox.id, offset: 0, limit: 7}), /Invalid message page/);

  // Reading prefers both HTML and plain text when present.
  assert.deepEqual(await call('read', {messageId: 25}), {html: state.messages[24].html, plain: 'Plain text of message 25'});
  const listParts = api.messages.listInlineTextParts;
  delete api.messages.listInlineTextParts;
  assert.equal((await call('read', {messageId: 3})).plain, 'Plain text of message 3');
  api.messages.listInlineTextParts = listParts;

  // Reply context includes Reply-To and CC for reply all.
  const context = await call('context', {messageId: 25});
  assert.equal(context.replyTo, 'Replies <replies@example.com>');
  assert.deepEqual(context.ccList, ['other@example.com']);

  // Move and trash need real message numbers and a known folder.
  await call('move', {messageIds: [24], folderId: 'account1://Receipts'});
  assert.deepEqual(state.moved.at(-1), {ids: [24], folderId: 'account1://Receipts'});
  await assert.rejects(call('move', {messageIds: ['24'], folderId: 'account1://Receipts'}), /No messages were chosen/);
  await assert.rejects(call('move', {messageIds: [23], folderId: 'account1://Missing'}), /not available/);
  await call('trash', {messageIds: [23]});
  assert.deepEqual(state.deleted.at(-1), {ids: [23], skipTrash: false});

  // Folders: create at the account root, rename, refuse names with slashes.
  const created = await call('createFolder', {accountId: 'account1', name: 'Bills'});
  assert.equal(created.name, 'Bills');
  assert.equal((await call('renameFolder', {folderId: created.id, name: 'Paid bills'})).name, 'Paid bills');
  await assert.rejects(call('createFolder', {accountId: 'account1', name: 'a/b'}), /without slashes/);
  assert.equal((await call('folderInfo', {folderId: 'account1://Receipts'})).messagesTotal, 1);

  // New message: identity, recipients and HTML body; compose window minimized.
  const sent = await call('send', {identityId: 'id1', to: ['sam@example.com'], cc: ['amy@example.com'], bcc: [],
    subject: 'Hello', html: '<html><body><p>Hi Sam</p></body></html>', plain: 'Hi Sam'});
  assert.match(sent.headerMessageId, /@thunderbird\.test$/);
  const message = state.sent.at(-1);
  assert.equal(message.kind, 'new');
  assert.equal(message.details.identityId, 'id1');
  assert.deepEqual(message.current.to, ['sam@example.com']);
  assert.equal(message.current.body, '<html><body><p>Hi Sam</p></body></html>');
  assert.equal(message.options.mode, 'sendNow');
  assert.deepEqual(JSON.parse(JSON.stringify(state.minimized.at(-1))), {windowId: message.windowId, info: {state: 'minimized'}});

  // Reply keeps Thunderbird's quoted original under the new text.
  await call('send', {identityId: 'id1', to: ['replies@example.com'], subject: 'Re: Message 25',
    html: '<html><body><p>Thanks</p></body></html>', plain: 'Thanks', reply: {messageId: 25, type: 'replyToAll'}});
  const reply = state.sent.at(-1);
  assert.equal(reply.kind, 'reply'); assert.equal(reply.replyType, 'replyToAll');
  assert.equal(reply.current.body, '<html><body><p>Thanks</p><blockquote>Original message</blockquote></body></html>');

  // A plain-text Thunderbird account gets plain text, original kept.
  api.compose.beginForward = (original => async (...args) => {
    const tab = await original(...args); return tab;
  })(api.compose.beginForward);
  await call('send', {identityId: 'id1', to: ['x@example.com'], subject: 'Fwd', html: '<p>FYI</p>', plain: 'FYI',
    plainOnly: true, forward: {messageId: 22}});
  assert.equal(state.sent.at(-1).current.plainTextBody, 'FYI\n\nForwarded original');

  // Send failures are reported without the server's text (it can include addresses).
  state.failSend = true;
  const failure = await bridge.handle({id: 9, method: 'send', params: {identityId: 'id1', to: ['sam@example.com'], subject: 'x', html: '', plain: 'x'}});
  assert.equal(failure.error.code, 'send');
  assert.doesNotMatch(JSON.stringify(failure), /sam@example\.com/);
  state.failSend = false;

  // Unknown or malformed requests are refused.
  assert.equal((await bridge.handle({id: 3, method: 'eval', params: {}})).error.code, 'invalid');
  assert.equal((await bridge.handle({method: 'accounts'})).error.code, 'invalid');
  assert.equal((await bridge.handle({id: 4, method: 'send', params: {to: ['a@b.c']}})).error.message, 'Choose a Thunderbird account first.');

  // Adding an account: Thunderbird's own page opens with the address; bad input is refused.
  assert.deepEqual(await call('openAccountSetup', {email: 'new@outlook.com'}), {opened: true, prefilled: true});
  assert.deepEqual(state.setupOpened, ['new@outlook.com']);
  await assert.rejects(call('openAccountSetup', {email: 'not an address'}), /not a complete email address/);
  await assert.rejects(call('openAccountSetup', {email: 'x'.repeat(250) + '@a.bc'}), /not a complete email address/);
  state.setupResult = {opened: true, prefilled: false};
  assert.deepEqual(await call('openAccountSetup', {email: 'b@example.com'}), {opened: true, prefilled: false});
  // Experiments can be turned off in Thunderbird: the answer is "not opened", never an error.
  const experiment = api.voiceCompanionSetup;
  delete api.voiceCompanionSetup;
  assert.deepEqual(await call('openAccountSetup', {email: 'c@example.com'}), {opened: false, prefilled: false});
  api.voiceCompanionSetup = {openAccountSetup: async () => { throw new Error('Window gone'); }};
  assert.deepEqual(await call('openAccountSetup', {email: 'c@example.com'}), {opened: false, prefilled: false});
  api.voiceCompanionSetup = experiment;

  // Started in the background: only normal (main) windows are minimized, once.
  assert.deepEqual(await call('minimize'), {minimized: 1});
  assert.equal(state.windows[0].state, 'minimized');
  assert.equal(state.windows[1].state, 'normal');
  assert.deepEqual(await call('minimize'), {minimized: 0});

  await experimentChecks();
  process.stdout.write('Thunderbird mail add-on checks passed.\n');
})().catch(error => { process.stderr.write(error.stack + '\n'); process.exitCode = 1; });

// ---- The Experiment (experiment/implementation.js) against a simulated Thunderbird window ----

class FakeElement {
  constructor(tag, attributes = {}, children = []) {
    Object.assign(this, {tag, attributes, children, value: attributes.value || '', disabled: false, shadowRoot: null,
      visible: attributes.visible !== false, events: []});
  }
  getClientRects() { return this.visible ? [{}] : []; }
  dispatchEvent(event) { this.events.push(event.type); return true; }
  *all() { for (const child of this.children) { yield child; yield* child.all(); } }
  querySelectorAll(selector) {
    const items = [...this.all()];
    if (selector === '*') return items;
    if (selector === 'input[type="email"]') return items.filter(e => e.tag === 'input' && e.attributes.type === 'email');
    throw new Error('Unexpected selector ' + selector);
  }
}

function fakeWindow({opener = 'openAccountSetup', box = () => new FakeElement('input', {type: 'email'}), shadow = false} = {}) {
  const document = new FakeElement('document');
  const tabmail = new FakeElement('tabmail');
  tabmail.opened = [];
  tabmail.openTab = (kind, info) => tabmail.opened.push({kind, info});
  document.children.push(tabmail);
  document.getElementById = id => (id === 'tabmail' ? tabmail : null);
  const win = {document, calls: [], windowState: 2, STATE_MINIMIZED: 2, restored: 0, focused: 0,
    restore() { this.restored++; this.windowState = 1; }, focus() { this.focused++; },
    setTimeout: (fn, ms) => setTimeout(fn, 1)};
  const open = () => {
    win.calls.push(opener);
    const input = box();
    win.input = input;
    if (!input) return;
    // Account Hub keeps its form inside custom elements; the old page is a tab.
    if (shadow) { const host = new FakeElement('account-hub'); host.shadowRoot = new FakeElement('shadow', {}, [input]); document.children.push(host); }
    else tabmail.currentTabInfo = {browser: {contentDocument: new FakeElement('page', {}, [new FakeElement('input', {type: 'text'}), input])}};
  };
  if (opener) win[opener] = async () => { setTimeout(open, 5); };
  return win;
}

function loadExperiment(win) {
  const context = vm.createContext({
    ChromeUtils: {importESModule: () => ({ExtensionCommon: {ExtensionAPI: class {}}})},
    Services: {wm: {getMostRecentWindow: type => (type === 'mail:3pane' ? win : null)}},
    setTimeout,
  });
  vm.runInContext(fs.readFileSync(path.join(__dirname, 'experiment', 'implementation.js'), 'utf8'), context);
  const Api = vm.runInContext('voiceCompanionSetup', context);
  return new Api().getAPI({}).voiceCompanionSetup;
}

async function experimentChecks() {
  for (const event of ['input', 'change']) assert.ok(event);
  global.Event = class { constructor(type) { this.type = type; } };
  const withEvent = win => { const original = win; return original; };

  // Thunderbird 128 to 149: openAccountSetup() opens the page; the address box is filled in.
  let win = fakeWindow();
  let api = loadExperiment(withEvent(win));
  FakeElement.prototype.ownerGlobal = {Event: global.Event};
  let answer = JSON.parse(JSON.stringify(await api.openAccountSetup('will@gmail.com')));
  assert.deepEqual(answer, {opened: true, prefilled: true});
  assert.deepEqual(win.calls, ['openAccountSetup']);
  assert.equal(win.input.value, 'will@gmail.com');
  assert.deepEqual(win.input.events, ['input', 'change']);
  assert.equal(win.restored, 1, 'a minimized Thunderbird is brought back');
  assert.equal(win.focused, 1);

  // Account Hub: the box is inside a custom element's shadow root.
  win = fakeWindow({shadow: true});
  assert.deepEqual(JSON.parse(JSON.stringify(await loadExperiment(win).openAccountSetup('a@b.com'))), {opened: true, prefilled: true});
  assert.equal(win.input.value, 'a@b.com');

  // Something already typed there is never replaced.
  win = fakeWindow({box: () => new FakeElement('input', {type: 'email', value: 'typed@example.com'})});
  assert.deepEqual(JSON.parse(JSON.stringify(await loadExperiment(win).openAccountSetup('a@b.com'))), {opened: true, prefilled: false});
  assert.equal(win.input.value, 'typed@example.com');

  // Older and newer names of the opener, then the page itself as a tab.
  for (const opener of ['openAccountSetupTab', 'AddMailAccount', 'openAccountHub']) {
    win = fakeWindow({opener});
    assert.equal((await loadExperiment(win).openAccountSetup('a@b.com')).opened, true);
    assert.deepEqual(win.calls, [opener]);
  }
  win = fakeWindow({opener: null, box: () => null});
  const tabmail = win.document.getElementById('tabmail');
  answer = JSON.parse(JSON.stringify(await loadExperiment(win).openAccountSetup('')));
  assert.deepEqual(answer, {opened: true, prefilled: false});
  assert.equal(tabmail.opened[0].info.url, 'about:accountsetup');

  // The box never appears (a hidden box does not count): opened, not filled.
  win = fakeWindow({box: () => new FakeElement('input', {type: 'email', visible: false})});
  assert.deepEqual(JSON.parse(JSON.stringify(await loadExperiment(win).openAccountSetup('a@b.com'))), {opened: true, prefilled: false});

  // No Thunderbird main window: nothing opened.
  const none = loadExperiment(null);
  assert.deepEqual(JSON.parse(JSON.stringify(await none.openAccountSetup('a@b.com'))), {opened: false, prefilled: false});

  // The manifest declares the Experiment, and its schema matches the function.
  const manifest = JSON.parse(fs.readFileSync(path.join(__dirname, 'manifest.json'), 'utf8'));
  const declared = manifest.experiment_apis.voiceCompanionSetup;
  assert.deepEqual(declared.parent.paths, [['voiceCompanionSetup']]);
  const schema = JSON.parse(fs.readFileSync(path.join(__dirname, declared.schema), 'utf8'));
  assert.equal(schema[0].namespace, 'voiceCompanionSetup');
  assert.deepEqual(schema[0].functions.map(f => [f.name, f.async]), [['openAccountSetup', true]]);
  assert.ok(fs.existsSync(path.join(__dirname, declared.parent.script)));
  const [major, minor] = manifest.version.split('.').map(Number);
  assert.ok(major > 1 || minor >= 1, 'the add-on version was raised so installed copies are replaced');
}

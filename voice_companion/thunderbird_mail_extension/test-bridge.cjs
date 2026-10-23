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
  assert.deepEqual(await call('openAccountSetup', {email: 'new@example.org', name: 'Will Walsh'}), {opened: true, prefilled: true});
  assert.deepEqual(state.setupCalls.at(-1), {email: 'new@example.org', name: 'Will Walsh', show: false});
  await assert.rejects(call('openAccountSetup', {email: 'not an address'}), /not a complete email address/);
  await assert.rejects(call('openAccountSetup', {email: 'x'.repeat(250) + '@a.bc'}), /not a complete email address/);
  await assert.rejects(call('openAccountSetup', {email: 'a@b.c', name: 'x\ny'}), /shorter name/);
  state.setupResult = {opened: true, prefilled: false};
  assert.deepEqual(await call('openAccountSetup', {email: 'b@example.com', show: true}), {opened: true, prefilled: false});
  assert.equal(state.setupCalls.at(-1).show, true);

  // The page is read and driven; a password goes in once and is never answered back.
  assert.deepEqual(await call('setupPage'), state.setupPage);
  assert.deepEqual(await call('setupContinue'), {pressed: true});
  state.setupPage.needsPassword = true;
  const filled = await bridge.handle({id: 7, method: 'fillPassword', params: {password: 'pw-123456'}});
  assert.deepEqual(JSON.parse(JSON.stringify(filled.result)), {filled: true});
  assert.doesNotMatch(JSON.stringify(filled), /pw-123456/);
  assert.deepEqual(state.passwordLengths, [9]);
  await assert.rejects(call('fillPassword', {password: ''}), /password box was empty/);
  assert.deepEqual(await call('showSetup'), {shown: true});

  // Web browser sign-in is handed over once, and only https addresses pass.
  assert.deepEqual(await call('captureSignIn', {seconds: 900}), {capturing: true});
  await assert.rejects(call('captureSignIn', {seconds: -1}), /Invalid time/);
  await call('openAccountSetup', {email: 'will@gmail.com'});
  const handed = await call('takeSignIn');
  assert.equal(handed.where, 'browser');
  assert.match(handed.url, /^https:\/\/accounts\.google\.com\//);
  assert.deepEqual(await call('takeSignIn'), {url: '', where: ''});
  state.signIn = {url: 'javascript:alert(1)', where: 'browser'};
  assert.deepEqual(await call('takeSignIn'), {url: '', where: 'browser'});

  // Experiments can be turned off in Thunderbird: every answer is "not done", never an error.
  const experiment = api.voiceCompanionSetup;
  delete api.voiceCompanionSetup;
  assert.deepEqual(await call('openAccountSetup', {email: 'c@example.com'}), {opened: false, prefilled: false});
  assert.equal((await call('setupPage')).open, false);
  assert.deepEqual(await call('setupContinue'), {pressed: false});
  assert.deepEqual(await call('captureSignIn', {seconds: 5}), {capturing: false});
  assert.deepEqual(await call('takeSignIn'), {url: '', where: ''});
  api.voiceCompanionSetup = {openAccountSetup: async () => { throw new Error('Window gone'); },
    setupPage: async () => { throw new Error('gone'); }};
  assert.deepEqual(await call('openAccountSetup', {email: 'c@example.com'}), {opened: false, prefilled: false});
  assert.equal((await call('setupPage')).open, false);
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
  constructor(localName, attributes = {}, children = []) {
    Object.assign(this, {localName, attributes, children, value: attributes.value || '', disabled: Boolean(attributes.disabled),
      shadowRoot: null, hidden: false, visible: attributes.visible !== false, events: [], id: attributes.id || '',
      className: attributes.class || '', textContent: attributes.text || '', clicks: 0});
  }
  getAttribute(name) { return this.attributes[name] === undefined ? null : String(this.attributes[name]); }
  getClientRects() { return this.visible ? [{}] : []; }
  dispatchEvent(event) { this.events.push(event.type); return true; }
  click() { this.clicks++; }
  get ownerGlobal() { return {Event: class { constructor(type) { this.type = type; } }}; }
  *all() { for (const child of this.children) { yield child; yield* child.all(); } }
  querySelectorAll(selector) {
    if (selector !== '*') throw new Error('Unexpected selector ' + selector);
    return [...this.all()];
  }
}

function setupForm({email = new FakeElement('input', {type: 'email'}), password = false} = {}) {
  const parts = [new FakeElement('h1', {text: '  Set up your   existing email address '}),
    new FakeElement('input', {type: 'text', id: 'realName'}), email,
    new FakeElement('span', {class: 'error-message', text: 'Thunderbird failed to find the settings'}),
    new FakeElement('span', {class: 'error-message', text: 'hidden note', visible: false}),
    new FakeElement('button', {id: 'back', text: 'Back'}), new FakeElement('button', {id: 'forward', text: 'Continue'})];
  if (password) parts.splice(3, 0, new FakeElement('input', {type: 'password', id: 'password'}));
  return parts;
}

function fakeWindow({opener = 'openAccountSetup', form = () => setupForm(), hub = false} = {}) {
  const document = new FakeElement('document');
  const tabmail = new FakeElement('tabmail');
  tabmail.opened = []; tabmail.tabInfo = [];
  tabmail.openTab = (kind, info) => { tabmail.opened.push({kind, info}); open(); };
  document.children.push(tabmail);
  document.getElementById = id => (id === 'tabmail' ? tabmail : null);
  const win = {document, calls: [], windowState: 2, STATE_MINIMIZED: 2, restored: 0, focused: 0,
    restore() { this.restored++; this.windowState = 1; }, focus() { this.focused++; },
    setTimeout: (fn) => setTimeout(fn, 1)};
  function open() {
    const parts = form();
    if (!parts) return;
    win.parts = parts;
    if (hub) {
      // Account Hub keeps its form inside custom elements in the main window.
      const host = new FakeElement('account-hub-email');
      host.shadowRoot = new FakeElement('shadow', {}, parts);
      document.children.push(new FakeElement('account-hub-container', {}, [host]));
    } else {
      const page = new FakeElement('page', {}, parts);
      page.documentURI = 'about:accountsetup';
      tabmail.tabInfo.push({browser: {contentDocument: page}});
    }
  }
  if (opener) win[opener] = async () => { win.calls.push(opener); setTimeout(open, 5); };
  return win;
}

// Thunderbird's OAuth2 module, as far as the hand-over relies on it.
function fakeOAuth() {
  const OAuth2 = function (email) { this.email = email; };
  OAuth2.prototype = {request: null, scope: null};
  class ExternalRequest {
    constructor() { this.redirectURI = 'http://127.0.0.1'; this.launched = []; }
    startLoopbackRedirectListener() { return true; }
    start(url) { this.launched.push(url.href); return true; }   // Windows' default browser
    close() {}
  }
  class InternalRequest {
    constructor() { this.redirectURI = 'https://127.0.0.1'; this.windows = 0; }
    start() { this.windows++; return true; }
    cancelled() {}
    close() {}
  }
  // Like OAuth2.requestAuthorization and finishAuthorizationRequest.
  const signIn = (oauth, Request) => {
    oauth.request = new Request(oauth);
    const request = oauth.request;
    const ok = request.start(new URL('https://accounts.google.com/o/oauth2/auth?login_hint=' + oauth.email));
    return {request, ok};
  };
  return {OAuth2, ExternalRequest, InternalRequest, signIn};
}

function loadExperiment(win, oauth = fakeOAuth()) {
  const context = vm.createContext({
    ChromeUtils: {importESModule: url => (url.includes('OAuth2') ? {OAuth2: oauth.OAuth2} : {ExtensionCommon: {ExtensionAPI: class {}}})},
    Services: {wm: {getMostRecentWindow: type => (type === 'mail:3pane' ? win : null)}},
    setTimeout, WeakRef, Symbol, Object, URL,
  });
  vm.runInContext(fs.readFileSync(path.join(__dirname, 'experiment', 'implementation.js'), 'utf8'), context);
  const Api = vm.runInContext('voiceCompanionSetup', context);
  const instance = new Api();
  const api = instance.getAPI({}).voiceCompanionSetup;
  api.shutdown = () => instance.onShutdown();
  return api;
}

const plain = value => JSON.parse(JSON.stringify(value));

async function experimentChecks() {
  // Thunderbird 128 to 149: openAccountSetup() opens a tab; address and name are filled in.
  let win = fakeWindow();
  let api = loadExperiment(win);
  assert.deepEqual(plain(await api.openAccountSetup('will@gmail.com', 'Will Walsh', false)), {opened: true, prefilled: true});
  assert.deepEqual(win.calls, ['openAccountSetup']);
  const [, name, email] = win.parts;
  assert.equal(email.value, 'will@gmail.com');
  assert.deepEqual(email.events, ['input', 'change']);
  assert.equal(name.value, 'Will Walsh');
  assert.equal(win.focused, 0, 'Voice Companion keeps the keyboard unless asked');
  assert.equal(win.restored, 0);

  // The page is read: title, visible messages only, and the main button.
  assert.deepEqual(plain(await api.setupPage()), {open: true, title: 'Set up your existing email address',
    notes: ['Thunderbird failed to find the settings'], button: 'Continue', canContinue: true, needsPassword: false});
  assert.deepEqual(plain(await api.setupContinue()), {pressed: true});
  assert.equal(win.parts.at(-1).clicks, 1);
  assert.equal(win.parts.at(-2).clicks, 0, 'never Back');
  assert.deepEqual(plain(await api.fillPassword('secret')), {filled: false}, 'no password box: nothing filled');
  assert.deepEqual(plain(await api.showSetup()), {shown: true});
  assert.equal(win.restored, 1); assert.equal(win.focused, 1);

  // Account Hub: the form is inside custom elements' shadow roots; a password box is found and filled.
  win = fakeWindow({hub: true, form: () => setupForm({password: true})});
  api = loadExperiment(win);
  assert.deepEqual(plain(await api.openAccountSetup('a@b.com', '', true)), {opened: true, prefilled: true});
  assert.equal(win.focused, 1, 'shown when asked');
  assert.equal((await api.setupPage()).needsPassword, true);
  assert.deepEqual(plain(await api.fillPassword('app pass')), {filled: true});
  const password = win.parts.find(p => p.id === 'password');
  assert.equal(password.value, 'app pass');
  assert.equal((await api.setupPage()).needsPassword, false);
  assert.doesNotMatch(JSON.stringify(plain(await api.setupPage())), /app pass/, 'a password is never read back');

  // Something already typed is never replaced; a disabled button is not pressed.
  win = fakeWindow({form: () => { const parts = setupForm({email: new FakeElement('input', {type: 'email', value: 'typed@example.com'})});
    parts.at(-1).disabled = true; return parts; }});
  api = loadExperiment(win);
  assert.deepEqual(plain(await api.openAccountSetup('a@b.com', '', false)), {opened: true, prefilled: false});
  assert.equal(win.parts[2].value, 'typed@example.com');
  assert.equal((await api.setupPage()).canContinue, false);
  assert.deepEqual(plain(await api.setupContinue()), {pressed: false});

  // Older and newer names of the opener, then the page itself as a tab.
  for (const opener of ['openAccountSetupTab', 'AddMailAccount', 'openAccountHub']) {
    win = fakeWindow({opener});
    assert.equal((await loadExperiment(win).openAccountSetup('a@b.com', '', false)).opened, true);
    assert.deepEqual(win.calls, [opener]);
  }
  win = fakeWindow({opener: null});
  assert.deepEqual(plain(await loadExperiment(win).openAccountSetup('a@b.com', '', false)), {opened: true, prefilled: true});
  assert.equal(win.document.getElementById('tabmail').opened[0].info.url, 'about:accountsetup');

  // A hidden box does not count: opened, not filled. No page: closed.
  win = fakeWindow({form: () => setupForm({email: new FakeElement('input', {type: 'email', visible: false})})});
  assert.deepEqual(plain(await loadExperiment(win).openAccountSetup('a@b.com', '', false)), {opened: true, prefilled: false});
  assert.equal((await loadExperiment(fakeWindow()).setupPage()).open, false);

  // No Thunderbird main window: nothing opened or pressed.
  const none = loadExperiment(null);
  assert.deepEqual(plain(await none.openAccountSetup('a@b.com', '', false)), {opened: false, prefilled: false});
  assert.deepEqual(plain(await none.setupContinue()), {pressed: false});

  // Sign-in hand-over. Not armed: Thunderbird opens the web browser as usual.
  const oauth = fakeOAuth();
  api = loadExperiment(fakeWindow(), oauth);
  let first = oauth.signIn(new oauth.OAuth2('a@gmail.com'), oauth.ExternalRequest);
  assert.equal(first.request.launched.length, 1);
  assert.deepEqual(plain(await api.takeSignIn()), {url: '', where: ''});
  // Armed: the address goes to Voice Companion instead, once.
  assert.deepEqual(plain(await api.captureSignIn(900)), {capturing: true});
  const account = new oauth.OAuth2('will@gmail.com');
  const second = oauth.signIn(account, oauth.ExternalRequest);
  assert.equal(second.ok, true);
  assert.equal(second.request.launched.length, 0, 'Windows default browser not opened');
  assert.equal(account.request, second.request, 'Thunderbird still finds its own request');
  assert.deepEqual(plain(await api.takeSignIn()), {url: 'https://accounts.google.com/o/oauth2/auth?login_hint=will@gmail.com', where: 'browser'});
  assert.deepEqual(plain(await api.takeSignIn()), {url: '', where: ''});
  // Yahoo and AOL sign in only in Thunderbird's own window: reported, not changed.
  const yahoo = oauth.signIn(new oauth.OAuth2('sam@yahoo.com'), oauth.InternalRequest);
  assert.equal(yahoo.request.windows, 1);
  assert.deepEqual(plain(await api.takeSignIn()), {url: '', where: 'thunderbird'});
  // Finishing clears the request as Thunderbird expects; an older OAuth2 object is unaffected.
  account.request = null;
  assert.equal(account.request, null);
  assert.equal(new oauth.OAuth2('x').request, null, 'a new sign-in starts with no request');
  // Disarmed, then the add-on stops: Thunderbird's OAuth2 is exactly as before.
  await api.captureSignIn(0);
  const third = oauth.signIn(new oauth.OAuth2('c@gmail.com'), oauth.ExternalRequest);
  assert.equal(third.request.launched.length, 1);
  const running = new oauth.OAuth2('d@gmail.com');
  running.request = {start() {}, close() {}};
  api.shutdown();
  const descriptor = Object.getOwnPropertyDescriptor(oauth.OAuth2.prototype, 'request');
  assert.equal(descriptor.value, null);
  assert.ok(running.request && typeof running.request.close === 'function', 'a running sign-in keeps its request');

  // The manifest declares the Experiment, and its schema matches the implementation.
  const manifest = JSON.parse(fs.readFileSync(path.join(__dirname, 'manifest.json'), 'utf8'));
  const declared = manifest.experiment_apis.voiceCompanionSetup;
  assert.deepEqual(declared.parent.paths, [['voiceCompanionSetup']]);
  const schema = JSON.parse(fs.readFileSync(path.join(__dirname, declared.schema), 'utf8'));
  assert.equal(schema[0].namespace, 'voiceCompanionSetup');
  const implemented = Object.keys(loadExperiment(null)).filter(name => name !== 'shutdown').sort();
  assert.deepEqual(schema[0].functions.map(f => f.name).sort(), implemented);
  assert.ok(schema[0].functions.every(f => f.async === true && f.type === 'function'));
  assert.ok(fs.existsSync(path.join(__dirname, declared.parent.script)));
  const [major, minor] = manifest.version.split('.').map(Number);
  assert.ok(major > 1 || minor >= 1, 'the add-on version was raised so installed copies are replaced');
}

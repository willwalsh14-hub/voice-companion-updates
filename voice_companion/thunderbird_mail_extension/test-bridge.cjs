// Tests for the Thunderbird mail add-on against a simulated Thunderbird.
// Run: node voice_companion/thunderbird_mail_extension/test-bridge.cjs
'use strict';
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const {createFakeMessenger, addMicrosoftAccounts} = require('./fake-messenger.cjs');

function load(api) {
  const context = vm.createContext({console, setTimeout, clearTimeout, VOICE_COMPANION_TEST: true, messenger: api, File, Blob, btoa, atob});
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
  assert.deepEqual(await call('read', {messageId: 25}), {html: state.messages[24].html, plain: 'Plain text of message 25', attachments: []});
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

  // Thunderbird closed its compose window itself: nothing else is closed.
  assert.deepEqual(state.openCompose, []); assert.deepEqual(state.closedByAddon, []);
  // Office 365 (Will, 0.2.162): the message went, but "Write: testing" stayed open.
  state.composeStaysOpen = true;
  await call('send', {identityId: 'id1', to: ['boss@work.example'], subject: 'testing', html: '<p>Hi</p>', plain: 'Hi'});
  assert.deepEqual(state.openCompose, [], 'the leftover compose window is closed');
  assert.deepEqual(state.closedByAddon, [state.sent.at(-1).windowId]);
  state.composeStaysOpen = false;

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
  // Will, Office 365: the send timed out, and the message sat in a hidden Thunderbird window.
  state.composeStaysOpen = true;
  state.failSend = 'The message could not be sent because the connection to Outgoing server (SMTP) smtp.office365.com timed out. Try again.';
  const timedOut = await bridge.handle({id: 10, method: 'send', params: {identityId: 'id1', to: ['boss@work.example'], subject: 'testing', html: '<p>Hi</p>', plain: 'Hi'}});
  assert.equal(timedOut.error.message, 'Thunderbird could not reach your mail server to send. The connection timed out. Your message is saved in Drafts in Thunderbird.');
  assert.equal(state.drafts.at(-1).subject, 'testing');
  assert.equal(state.drafts.at(-1).options.mode, 'draft');
  assert.deepEqual(state.openCompose, [], 'the failed message window is closed');
  state.failSend = '535 5.7.3 Authentication unsuccessful for will@work.example';
  const signIn = await bridge.handle({id: 11, method: 'send', params: {identityId: 'id1', to: ['x@work.example'], subject: 's', html: '', plain: 'x'}});
  assert.match(signIn.error.message, /did not accept the sign-in/);
  assert.doesNotMatch(JSON.stringify(signIn), /will@work/);
  // Microsoft 365 with sending from mail programs turned off by the organization.
  state.failSend = '535 5.7.139 Authentication unsuccessful, SmtpClientAuthentication is disabled for the Tenant.';
  const tenant = await bridge.handle({id: 13, method: 'send', params: {identityId: 'id1', to: ['x@work.example'], subject: 's', html: '', plain: 'x'}});
  assert.match(tenant.error.message, /Your organization does not allow sending from mail programs like Thunderbird\. Ask your IT administrator to turn on Authenticated SMTP/);
  // A draft that cannot be saved stays open, and that is said.
  state.failSave = true; state.failSend = 'Unknown problem';
  const unsaved = await bridge.handle({id: 12, method: 'send', params: {identityId: 'id1', to: ['x@work.example'], subject: 's', html: '', plain: 'x'}});
  assert.equal(unsaved.error.message, 'Thunderbird could not send this message. Your message is still open in Thunderbird.');
  assert.equal(state.openCompose.length, 1);
  state.failSave = false; state.composeStaysOpen = false; state.openCompose = [];
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
  assert.deepEqual(await call('setupPage'), {...state.setupPage, fields: [], addressFilled: false, choices: []});
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

  await freshnessChecks();
  await pagingChecks();
  await mailCheckChecks();
  await experimentChecks();
  await quitChecks();
  await promptChecks();
  await identityChecks();
  await outgoingServerChecks();
  await attachmentChecks();
  await microsoftAccountChecks();
  await aboutChecks();
  await graphChoiceChecks();
  await noteWordsChecks();
  process.stdout.write('Thunderbird mail add-on checks passed.\n');
})().catch(error => { process.stderr.write(error.stack + '\n'); process.exitCode = 1; });

// ---- Thunderbird 154 and newer: Microsoft 365 through Microsoft Graph, and Exchange (EWS) ----

// Will, October 2026: Thunderbird 154 added Microsoft Graph for Microsoft 365. Thunderbird 159 lists such an
// account with type "extension:graph" (Exchange: "ews"). Everything Voice Companion does with mail must work for it too.
async function microsoftAccountChecks() {
  const {api, state} = createFakeMessenger();
  addMicrosoftAccounts(state);
  const bridge = load(api);
  const call = async (method, params) => {
    const answer = await bridge.handle({id: 31, method, params});
    if (answer.error) throw Object.assign(new Error(answer.error.message), {answer});
    return JSON.parse(JSON.stringify(answer.result));
  };
  // Listed as accounts, with their type, beside Gmail.
  const accounts = await call('accounts');
  assert.deepEqual(accounts.map(a => [a.id, a.type, a.identities[0].email]), [['account1', 'imap', 'me@gmail.com'],
    ['account5', 'extension:graph', 'will@contoso.onmicrosoft.com'], ['account6', 'ews', 'will@work.example']]);
  for (const [accountId, label] of [['account5', 'Graph message'], ['account6', 'Exchange message']]) {
    // Folders by Microsoft's names; the inbox found by its kind.
    const folders = await call('folders', {accountId});
    assert.deepEqual(folders.map(f => f.name), ['Inbox', 'Sent Items', 'Deleted Items', 'Archive']);
    const inbox = await call('inbox', {accountId});
    assert.equal(inbox.id, accountId + '://Inbox');
    assert.equal((await call('folderInfo', {folderId: inbox.id})).messagesTotal >= 1, true);
    // Listing checks the server first, by the folder's own path.
    const page = await call('list', {folderId: inbox.id, offset: 0, limit: 10});
    assert.equal(page.rows[0].subject, label);
    assert.equal(page.mailCheck, 'checked');
    assert.deepEqual(state.mailChecks.at(-1), {accountId, path: '/Inbox', inbox: true});
  }
  // Reading, with the message's file.
  const read = await call('read', {messageId: 501});
  assert.equal(read.plain, 'Sent through Microsoft Graph');
  assert.deepEqual(read.attachments.map(a => a.name), ['letter.pdf']);
  const piece = await call('attachmentChunk', {messageId: 501, partName: '1.2'});
  assert.equal(Buffer.from(piece.data, 'base64').toString(), '%PDF graph');
  // Sending, replying and forwarding go out through the Graph account's own identity.
  const sent = await call('send', {identityId: 'idaccount5', to: ['sam@example.com'], subject: 'Hi', html: '<p>Hi</p>', plain: 'Hi'});
  assert.equal(sent.accountId, 'account5');
  await call('send', {identityId: 'idaccount5', reply: {messageId: 501, type: 'replyToSender'}, html: '<p>Thanks</p>', plain: 'Thanks'});
  assert.deepEqual([state.sent.at(-1).kind, state.sent.at(-1).messageId, state.sent.at(-1).details.identityId], ['reply', 501, 'idaccount5']);
  await call('send', {identityId: 'idaccount5', to: ['amy@example.com'], forward: {messageId: 501}, html: '<p>See this</p>', plain: 'See this'});
  assert.deepEqual(state.sent.at(-1).attachments.map(a => a.file.name), ['letter.pdf'], 'the forward keeps its file');
  // Moving, deleting (to the account's own Deleted Items), and folders of its own.
  await call('move', {messageIds: [502], folderId: 'account5://Archive'});
  assert.equal(state.messages.find(m => m.id === 502).folderId, 'account5://Archive');
  await call('trash', {messageIds: [501]});
  assert.equal(state.messages.find(m => m.id === 501).folderId, 'account5://Deleted Items');
  const made = await call('createFolder', {accountId: 'account5', name: 'Benefits'});
  assert.equal(made.name, 'Benefits');
  assert.equal(state.folders.find(f => f.id === made.id).accountId, 'account5');
  // A Microsoft 365 organization that has not approved Thunderbird: the right advice for Graph,
  // never "turn on Authenticated SMTP" (that is only for sending over SMTP).
  state.failSendFor = {idaccount5: 'AADSTS65001: The user or administrator has not consented to use the application.'};
  const refused = await bridge.handle({id: 32, method: 'send', params: {identityId: 'idaccount5', to: ['x@example.com'], subject: 's', html: '', plain: 'x'}});
  assert.match(refused.error.message, /Your organization must approve Thunderbird for Microsoft 365\. Ask your IT administrator/);
  assert.doesNotMatch(refused.error.message, /SMTP/);
  // Over SMTP the advice stays as it was.
  state.failSendFor = {id1: '5.7.139 SmtpClientAuthentication is disabled for the Tenant.'};
  const smtp = await bridge.handle({id: 33, method: 'send', params: {identityId: 'id1', to: ['x@example.com'], subject: 's', html: '', plain: 'x'}});
  assert.match(smtp.error.message, /turn on Authenticated SMTP/);

  // The Experiment: a Graph account's folder is fetched from the server (never treated as local),
  // and a Graph outgoing server (not SMTP, no host name) gives no server words instead of failing.
  const mail = fakeMailServices();
  const graph = {type: 'graph'};
  const graphRoot = 'graph://will%40contoso.onmicrosoft.com@graph.microsoft.com';
  const graphInbox = {URI: graphRoot + '/Inbox', flags: 0x1000,
    updateFolderWithListener(win, listener) { mail.calls.push({how: 'update', uri: this.URI}); setTimeout(() => listener.OnStopRunningUrl({}, 0), 5); }};
  graph.rootFolder = {URI: graphRoot, descendants: [graphInbox], getFolderWithFlags: () => graphInbox};
  const getAccount = mail.MailServices.accounts.getAccount;
  mail.MailServices.accounts.getAccount = id => (id === 'account5' ? {incomingServer: graph} : getAccount(id));
  mail.MailServices.accounts.getIdentity = key => (key === 'idaccount5' ? {key} : null);
  mail.MailServices.outgoingServer = {getServerByIdentity: () => ({type: 'graph', QueryInterface() { throw new Error('NS_NOINTERFACE'); }})};
  const experiment = loadExperiment(null, fakeOAuth(), mail);
  assert.deepEqual(plain(await experiment.getNewMail('account5', '/Inbox', true)), {checked: true, offline: false});
  assert.deepEqual(mail.calls.at(-1), {how: 'update', uri: graphRoot + '/Inbox'});
  assert.deepEqual(plain(await experiment.outgoingServer('idaccount5')), {host: '', port: 0, security: ''});
}

// Which Thunderbird is running, from which update channel, with which profile, and whether a
// message is being written in it. Voice Companion uses it to update Thunderbird safely.
async function aboutChecks() {
  const app = {composing: 1, quits: []};
  const api = loadExperiment(null, fakeOAuth(), fakeMailServices(), app);
  assert.deepEqual(plain(await api.about()), {version: '155.0', channel: 'release',
    profile: 'C:\\Users\\Will\\AppData\\Roaming\\Thunderbird\\Profiles\\abc.default-release', composing: 1});
  app.composing = 0;
  assert.equal((await api.about()).composing, 0);
  // Through the add-on, and an older Experiment without it.
  const {api: messenger, state} = createFakeMessenger();
  const bridge = load(messenger);
  state.userComposing = 1;
  assert.deepEqual(plain((await bridge.handle({id: 41, method: 'about', params: {}})).result), {version: '155.0', channel: 'release',
    profile: state.about.profile, composing: 1});
  delete messenger.voiceCompanionSetup.about;
  assert.deepEqual(plain((await bridge.handle({id: 42, method: 'about', params: {}})).result), {});
  messenger.voiceCompanionSetup.about = async () => { throw new Error('broken'); };
  assert.deepEqual(plain((await bridge.handle({id: 43, method: 'about', params: {}})).result), {});
}

// Account Hub in Thunderbird 154 and newer offers a Microsoft 365 address more than one way to
// connect. Microsoft Graph is chosen for a new account (Mozilla's advice for Microsoft 365), once;
// a choice the person makes afterwards is kept. The choices are read aloud.
async function graphChoiceChecks() {
  const radio = (text, checked) => {
    const input = new FakeElement('input', {type: 'radio'});
    input.checked = checked;
    const label = new FakeElement('label', {}, [input, new FakeElement('span', {text})]);
    label.textContent = text;
    input.parentNode = label;
    // Like a radio button: choosing one clears the others in its group.
    input.click = function () { this.clicks++; for (const other of radios) other.checked = other === this; };
    radios.push(input);
    return label;
  };
  let win, radios = [];
  const form = () => {
    radios = [];
    const parts = setupForm();
    // Each radio button sits inside its label, once, as on a real page.
    parts.splice(4, 0, radio('Exchange (EWS)', true), radio('Microsoft Graph API', false), radio('IMAP', false));
    return parts;
  };
  win = fakeWindow({hub: true, form});
  const api = loadExperiment(win);
  await api.openAccountSetup('will@contoso.onmicrosoft.com', 'Will Walsh', false);
  const page = plain(await api.setupPage());
  assert.deepEqual(page.choices, [{label: 'Exchange (EWS)', checked: false}, {label: 'Microsoft Graph API', checked: true},
    {label: 'IMAP', checked: false}]);
  assert.equal(radios[1].clicks, 1);
  // The person (with "show Thunderbird") picks IMAP: Voice Companion never changes it back.
  radios[2].click();
  await api.setupPage();
  assert.deepEqual(plain(await api.setupContinue()), {pressed: true});
  assert.equal(radios[2].checked, true);
  assert.equal(radios[1].clicks, 1);
  // A page with no choices (Gmail): nothing about choices.
  const gmail = loadExperiment(fakeWindow({hub: true}));
  await gmail.openAccountSetup('will@gmail.com', '', false);
  assert.deepEqual(plain(await gmail.setupPage()).choices, []);
  // The add-on passes the choices on, at most six, as plain text.
  const {api: messenger, state} = createFakeMessenger();
  state.setupPage = {...state.setupPage, choices: [{label: 'Microsoft Graph API', checked: true}, {label: 'IMAP', checked: false}]};
  const bridge = load(messenger);
  assert.deepEqual(plain((await bridge.handle({id: 44, method: 'setupPage', params: {}})).result).choices,
    [{label: 'Microsoft Graph API', checked: true}, {label: 'IMAP', checked: false}]);
}

// Thunderbird 159 on Windows (try PR 82): Account Hub's notice holds its own Show more and Show less
// buttons, so the note was read as "Looking up configuration Show more Show less". Button words are
// not part of what Thunderbird says.
async function noteWordsChecks() {
  const notice = () => new FakeElement('div', {class: 'notification-message', text: 'Looking up configuration Show more Show less'}, [
    new FakeElement('span', {text: 'Looking up configuration'}), new FakeElement('button', {text: 'Show more'}),
    new FakeElement('button', {text: 'Show less', visible: false})]);
  const win = fakeWindow({hub: true, form: () => { const parts = setupForm(); parts.splice(3, 2, notice()); return parts; }});
  const api = loadExperiment(win);
  await api.openAccountSetup('will@outlook.com', '', false);
  assert.deepEqual(plain(await api.setupPage()).notes, ['Looking up configuration']);
}

// ---- Attachments: listed, saved in pieces, attached to new mail, replies and forwards ----

async function attachmentChecks() {
  const {api, state} = createFakeMessenger();
  const bridge = load(api);
  const call = async (method, params) => {
    const answer = await bridge.handle({id: 21, method, params});
    if (answer.error) throw Object.assign(new Error(answer.error.message), {answer});
    return JSON.parse(JSON.stringify(answer.result));
  };
  // Only real files are listed: not the unnamed inline picture, not the deleted attachment.
  const listed = await call('attachments', {messageId: 24});
  assert.deepEqual(listed.map(a => [a.partName, a.name, a.contentType]),
    [['1.2', 'report.pdf', 'application/pdf'], ['1.4', 'photo.jpg', 'image/jpeg']]);
  assert.equal(listed[0].size, state.messages[23].attachments[0].data.length);
  // Reading a message says its files too.
  assert.deepEqual((await call('read', {messageId: 24})).attachments.map(a => a.name), ['report.pdf', 'photo.jpg']);
  await assert.rejects(call('attachments', {messageId: '24'}), /not available/);

  // Saving: the file comes in pieces no bigger than CHUNK, read from Thunderbird once.
  const original = Buffer.from(state.messages[23].attachments[0].data);
  assert.ok(original.length > 1024 * 1024, 'the test file is larger than one piece');
  state.attachmentReads = 0;
  const pieces = [];
  for (let offset = 0, done = false; !done;) {
    const piece = await call('attachmentChunk', {messageId: 24, partName: '1.2', offset, length: 1024 * 1024});
    const data = Buffer.from(piece.data, 'base64');
    assert.ok(data.length <= 1024 * 1024);
    assert.equal(piece.size, original.length);
    pieces.push(data); offset += data.length; done = piece.done;
  }
  assert.ok(Buffer.concat(pieces).equals(original), 'the saved bytes are the attachment');
  assert.equal(state.attachmentReads, 1, 'Thunderbird reads the file once for all its pieces');
  await assert.rejects(call('attachmentChunk', {messageId: 24, partName: '1.2', offset: 0, length: 2 * 1024 * 1024}), /Invalid attachment piece/);
  state.messages[23].attachments[2].damaged = true;
  await assert.rejects(call('attachmentChunk', {messageId: 24, partName: '1.4'}), /could not read this attachment\. It may be damaged/);
  state.messages[23].attachments[2].damaged = false;
  // An empty attachment saves as an empty file.
  state.messages[23].attachments[2].data = new Uint8Array(0);
  assert.deepEqual(await call('attachmentChunk', {messageId: 24, partName: '1.4'}), {data: '', size: 0, done: true});

  // Sending a file: it arrives in pieces, then goes with the message.
  const upload = async (name, buffer, piece = 300000) => {
    const {uploadId} = await call('uploadStart', {name, size: buffer.length});
    for (let offset = 0; offset < buffer.length; offset += piece) {
      await call('uploadChunk', {uploadId, offset, data: buffer.subarray(offset, offset + piece).toString('base64')});
    }
    return uploadId;
  };
  const letter = Buffer.from('Dear Sam,\n'.repeat(50000));
  const id = await upload('letter.txt', letter);
  await call('send', {identityId: 'id1', to: ['sam@example.com'], subject: 'Letter', html: '<p>Attached</p>', plain: 'Attached', attachments: [id]});
  const sent = state.sent.at(-1);
  assert.deepEqual(sent.attachments.map(a => a.file.name), ['letter.txt']);
  assert.ok(Buffer.from(await sent.attachments[0].file.arrayBuffer()).equals(letter), 'Thunderbird sends the same bytes');
  assert.equal(bridge.uploads.size, 0, 'the sent file is not kept');
  // A reply with a file.
  const second = await upload('notes.txt', Buffer.from('notes'));
  await call('send', {identityId: 'id1', to: ['a@example.com'], subject: 'Re: Message 25', html: '<p>Here</p>', plain: 'Here',
    reply: {messageId: 25, type: 'replyToSender'}, attachments: [second]});
  assert.equal(state.sent.at(-1).kind, 'reply');
  assert.deepEqual(state.sent.at(-1).attachments.map(a => a.file.name), ['notes.txt']);
  // A piece out of order, a broken piece, or a file that never finished: refused, nothing sent.
  const {uploadId: partial} = await call('uploadStart', {name: 'big.bin', size: 10});
  await assert.rejects(call('uploadChunk', {uploadId: partial, offset: 5, data: 'AAAA'}), /did not reach Thunderbird whole/);
  await assert.rejects(call('uploadChunk', {uploadId: partial, offset: 0, data: '%%%'}), /did not reach Thunderbird whole/);
  const before = state.sent.length;
  await assert.rejects(call('send', {identityId: 'id1', to: ['a@example.com'], subject: 'x', html: '', plain: 'x', attachments: [partial]}),
    /Nothing was sent/);
  assert.equal(state.sent.length, before);
  await assert.rejects(call('uploadStart', {name: '..\\evil.exe', size: 3}), /cannot be attached/);
  await assert.rejects(call('uploadStart', {name: 'huge.bin', size: 61 * 1024 * 1024}), /too large/);

  // Forward: the original's files go too, less the one the person took off.
  state.messages[23].attachments[2].data = new Uint8Array(Buffer.from('JPEGDATA'));
  await call('send', {identityId: 'id1', to: ['x@example.com'], subject: 'Fwd: Message 24', html: '<p>FYI</p>', plain: 'FYI',
    forward: {messageId: 24, drop: []}});
  let forwarded = state.sent.at(-1);
  assert.equal(forwarded.kind, 'forward');
  assert.deepEqual(forwarded.attachments.map(a => a.file.name).sort(), ['Part 1.3', 'photo.jpg', 'report.pdf']);
  await call('send', {identityId: 'id1', to: ['x@example.com'], subject: 'Fwd: Message 24', html: '<p>FYI</p>', plain: 'FYI',
    forward: {messageId: 24, drop: ['report.pdf']}});
  assert.deepEqual(state.sent.at(-1).attachments.map(a => a.file.name).sort(), ['Part 1.3', 'photo.jpg']);
  // A Thunderbird that leaves the files off a forward: the add-on adds them itself.
  state.forwardWithoutFiles = true;
  const extra = await upload('mine.txt', Buffer.from('mine'));
  await call('send', {identityId: 'id1', to: ['x@example.com'], subject: 'Fwd: Message 24', html: '<p>FYI</p>', plain: 'FYI',
    forward: {messageId: 24, drop: []}, attachments: [extra]});
  forwarded = state.sent.at(-1);
  assert.deepEqual(forwarded.attachments.map(a => a.file.name), ['report.pdf', 'photo.jpg', 'mine.txt']);
  assert.ok(Buffer.from(await forwarded.attachments[0].file.arrayBuffer()).equals(original));
  state.forwardWithoutFiles = false;
  const bytes = (text, repeat = 1) => new Uint8Array(Buffer.from(text.repeat(repeat), 'utf8'));
  // Taken off by part: two files called scan.pdf, and the second one (the one chosen) is left out.
  // A name Voice Companion made safe for Windows ("a:b.pdf" became "a_b.pdf") is found by its part too.
  state.messages[23].attachments.push(
    {partName: '1.6', name: 'scan.pdf', contentType: 'application/pdf', data: bytes('first scan', 3)},
    {partName: '1.7', name: 'scan.pdf', contentType: 'application/pdf', data: bytes('second scan', 5)},
    {partName: '1.8', name: 'a:b.pdf', contentType: 'application/pdf', data: bytes('colon', 2)});
  await call('send', {identityId: 'id1', to: ['x@example.com'], subject: 'Fwd: Message 24', html: '<p>FYI</p>', plain: 'FYI',
    forward: {messageId: 24, drop: ['scan.pdf', 'a_b.pdf'], dropParts: ['1.7', '1.8']}});
  forwarded = state.sent.at(-1);
  assert.deepEqual(forwarded.attachments.map(a => a.file.name).sort(), ['Part 1.3', 'photo.jpg', 'report.pdf', 'scan.pdf']);
  const kept = forwarded.attachments.find(a => a.file.name === 'scan.pdf');
  assert.equal(Buffer.from(await kept.file.arrayBuffer()).toString(), 'first scan'.repeat(3), 'the first scan stays; the second was taken off');
  // A file taken off that Thunderbird cannot find: nothing is sent, rather than sending it anyway.
  const beforeGone = state.sent.length;
  const realRemove = api.compose.listAttachments;
  api.compose.listAttachments = async tabId => (await realRemove(tabId)).filter(item => item.name !== 'a:b.pdf');
  state.forwardWithoutFiles = true;
  const refusedDrop = await bridge.handle({id: 24, method: 'send', params: {identityId: 'id1', to: ['x@example.com'],
    subject: 'Fwd', html: '', plain: 'FYI', forward: {messageId: 24, dropParts: ['1.8']}}});
  assert.match(refusedDrop.error.message, /could not take a:b\.pdf off the forward, so nothing was sent/);
  assert.equal(state.sent.length, beforeGone);
  api.compose.listAttachments = realRemove;
  state.forwardWithoutFiles = false;
  state.messages[23].attachments.splice(5, 3);

  // Thunderbird refuses a file: nothing is sent, the message is kept in Drafts, the window closes.
  state.failAttach = true;
  const refused = await upload('refused.txt', Buffer.from('x'));
  const sentBefore = state.sent.length;
  const failed = await bridge.handle({id: 22, method: 'send', params: {identityId: 'id1', to: ['a@example.com'], subject: 'Files',
    html: '', plain: 'x', attachments: [refused]}});
  assert.equal(failed.error.message, 'Thunderbird could not attach the files, so nothing was sent. Your message is saved in Drafts in Thunderbird.');
  assert.equal(state.sent.length, sentBefore);
  assert.deepEqual(state.openCompose, []);
  state.failAttach = false;
  // A file Thunderbird says it attached but did not: caught before sending.
  const realAdd = api.compose.addAttachment;
  api.compose.addAttachment = async () => ({id: 999, name: 'ghost'});
  const ghost = await upload('ghost.txt', Buffer.from('x'));
  const missing = await bridge.handle({id: 23, method: 'send', params: {identityId: 'id1', to: ['a@example.com'], subject: 'Files',
    html: '', plain: 'x', attachments: [ghost]}});
  assert.match(missing.error.message, /^Thunderbird did not add every attachment, so nothing was sent\./);
  assert.equal(state.sent.length, sentBefore);
  api.compose.addAttachment = realAdd;

  // An older Thunderbird without the attachment calls: reading still works, listing says why.
  delete api.messages.listAttachments;
  assert.equal((await call('read', {messageId: 24})).attachments, null);
  await assert.rejects(call('attachments', {messageId: 24}), /cannot list attachments/);

  // Unfinished uploads are dropped after a while.
  let now = 1000;
  const timed = load(createFakeMessenger().api);
  timed.clock = () => now;
  timed.uploadStart({name: 'old.txt', size: 5});
  now += 11 * 60 * 1000;
  timed.uploadStart({name: 'new.txt', size: 5});
  assert.deepEqual([...timed.uploads.values()].map(u => u.name), ['new.txt']);
}

// ---- The newest mail comes first, and Thunderbird checks the server before listing ----

const DAY = 24 * 60 * 60 * 1000;

function fakeMessage(id, date, extra = {}) {
  return {id, folderId: 'account1://INBOX', date, author: `Sender ${id} <s${id}@example.com>`, subject: `Message ${id}`, read: true,
    size: 100, recipients: ['me@gmail.com'], ccList: [], headerMessageId: `m${id}@example.com`, html: '', plain: '', replyTo: '', ...extra};
}

// Will, 0.2.161: in a large work mailbox, End read every page in turn, and each page read
// the whole folder again from Thunderbird, so it never finished and Companion froze.
async function pagingChecks() {
  const {api, state} = createFakeMessenger();
  let now = Date.UTC(2026, 9, 23, 12);
  const bridge = load(api);
  bridge.clock = () => now;
  const call = async (method, params) => {
    const answer = await bridge.handle({id: 9, method, params});
    if (answer.error) throw new Error(answer.error.message);
    return JSON.parse(JSON.stringify(answer.result));
  };
  const inbox = 'account1://INBOX';
  state.messages = state.messages.filter(m => m.folderId !== inbox);
  // 5,000 messages over ten years, stored oldest first like Thunderbird's database.
  for (let i = 1; i <= 5000; i++) state.messages.push(fakeMessage(i, new Date(now - (5001 - i) * 18 * 60 * 60 * 1000)));
  const first = await call('list', {folderId: inbox, offset: 0, limit: 10});
  assert.equal(first.rows[0].id, 5000, 'newest first');
  // Later pages read the folder once, then reuse it.
  state.queries = 0;
  for (let offset = 10; offset <= 200; offset += 10) {
    const page = await call('list', {folderId: inbox, offset, limit: 10});
    assert.equal(page.rows[0].id, 5000 - offset);
  }
  const readsForTwentyPages = state.queries;
  assert.ok(readsForTwentyPages <= 5, 'pages after the first reuse one read of the folder, not one each: ' + readsForTwentyPages);
  // End: the oldest page in one request.
  state.queries = 0;
  const end = await call('list', {folderId: inbox, offset: 0, limit: 10, last: true});
  assert.equal(end.offset, 4990);
  assert.equal(end.total, 5000);
  assert.deepEqual(end.rows.map(r => r.id), [10, 9, 8, 7, 6, 5, 4, 3, 2, 1]);
  assert.equal(end.next, null);
  assert.equal(state.queries, 0, 'End reuses the list that is already read');
  // A move changes the folder, so the kept list is read again.
  await call('move', {messageIds: [1], folderId: 'account1://Receipts'});
  const afterMove = await call('list', {folderId: inbox, offset: 0, limit: 10, last: true});
  assert.equal(afterMove.total, 4999);
  assert.ok(!afterMove.rows.some(r => r.id === 1));
  // A kept list is used only for a short time.
  state.queries = 0; now += 10 * 60 * 1000;
  await call('list', {folderId: inbox, offset: 10, limit: 10});
  assert.ok(state.queries > 0, 'an old kept list is read again');
  // An empty folder: End is the empty first page.
  state.messages = state.messages.filter(m => m.folderId !== inbox); now += 10 * 60 * 1000;
  const empty = await call('list', {folderId: inbox, offset: 0, limit: 10, last: true});
  assert.deepEqual(empty.rows, []); assert.equal(empty.offset, 0); assert.equal(empty.next, null);
}

// Will, 0.2.162: Thunderbird that Voice Companion started should close with Voice Companion.
async function quitChecks() {
  const app = {composing: 1, quits: []};
  const api = loadExperiment(null, fakeOAuth(), fakeMailServices(), app);
  // A message is being written: Thunderbird stays open.
  assert.deepEqual(plain(await api.quitThunderbird()), {quit: false, reason: 'composing'});
  await new Promise(r => setTimeout(r, 5));
  assert.deepEqual(app.quits, []);
  // Nothing open: Thunderbird is asked to close the normal way (it may still ask about unsent mail).
  app.composing = 0;
  assert.deepEqual(plain(await api.quitThunderbird()), {quit: true});
  // Wait for the dispatched quit itself, not a fixed 5 ms: Windows timers are coarse
  // (about 15 ms), and the fixed wait made this check fail once on a Windows build.
  for (let waited = 0; !app.quits.length && waited < 2000; waited += 5) await new Promise(r => setTimeout(r, 5));
  assert.deepEqual(app.quits, [2]);
  // The add-on asks through the Experiment; without it, it says so.
  const {api: messenger} = createFakeMessenger();
  const bridge = load(messenger);
  messenger.voiceCompanionSetup.quitThunderbird = async () => ({quit: true});
  assert.deepEqual(plain((await bridge.handle({id: 3, method: 'quit', params: {}})).result), {quit: true});
  delete messenger.voiceCompanionSetup.quitThunderbird;
  assert.deepEqual(plain((await bridge.handle({id: 4, method: 'quit', params: {}})).result), {quit: false, reason: 'unavailable'});
}

// "Hidden from the taskbar" (window_hiding.py): Thunderbird's main window is minimized and off
// the taskbar. A question Thunderbird shows inside it (a password prompt) would then be out of
// reach, so while this is on, the window holding any prompt is brought up. Windows then shows it,
// and Voice Companion brings it to the front and says so.
async function promptChecks() {
  const app = {composing: 0, quits: []};
  const main = fakeWindow();
  const api = loadExperiment(main, fakeOAuth(), fakeMailServices(), app);
  assert.equal(app.observers.size, 0, 'nothing is watched until asked');
  assert.deepEqual(plain(await api.keepPromptsInView(true)), {watching: true});
  assert.deepEqual([...app.observers.keys()].sort(), ['common-dialog-loaded', 'tabmodal-dialog-loaded', 'webextension-permission-prompt']);
  assert.equal(app.watchers.size, 1);
  // A password prompt inside the minimized main window: the main window comes up.
  const prompt = {browsingContext: {topChromeWindow: main}};
  app.observers.get('common-dialog-loaded').observe(prompt, 'common-dialog-loaded', null);
  assert.equal(main.restored, 1);
  assert.equal(main.focused, 0, 'Voice Companion gives it the keyboard, not Thunderbird');
  // Already up: nothing more.
  app.observers.get('tabmodal-dialog-loaded').observe(prompt, 'tabmodal-dialog-loaded', null);
  assert.equal(main.restored, 1);
  // An add-on permission question (not a window): the main window comes up.
  main.windowState = main.STATE_MINIMIZED;
  app.observers.get('webextension-permission-prompt').observe({wrappedJSObject: {}}, 'webextension-permission-prompt', null);
  assert.equal(main.restored, 2);
  // A new window of any kind but a message being written: the main window comes up too, so a
  // window it owns is not kept hidden with it.
  const opened = (type) => {
    let loaded = null;
    const win = {document: {documentElement: {getAttribute: name => (name === 'windowtype' ? type : null)}},
      addEventListener: (event, handler) => { if (event === 'load') loaded = handler; }};
    for (const watcher of app.watchers) watcher.observe(win, 'domwindowopened', null);
    loaded();
  };
  main.windowState = main.STATE_MINIMIZED;
  opened('msgcompose');
  assert.equal(main.restored, 2, 'writing windows the add-on opens to send are not questions');
  opened('mail:browserRequest');
  assert.equal(main.restored, 3);
  // A broken prompt never stops Thunderbird.
  app.observers.get('common-dialog-loaded').observe({get browsingContext() { throw new Error('gone'); }}, 'common-dialog-loaded', null);
  // Turned off: every observer is removed, and nothing is brought up any more.
  assert.deepEqual(plain(await api.keepPromptsInView(false)), {watching: false});
  assert.equal(app.observers.size, 0); assert.equal(app.watchers.size, 0);
  assert.deepEqual(plain(await api.keepPromptsInView(true)), {watching: true});
  api.shutdown();
  assert.equal(app.observers.size, 0, 'the add-on stopping removes them too');
  // The add-on's request: on and off, and "not available" with an older Experiment.
  const {api: messenger} = createFakeMessenger();
  const bridge = load(messenger);
  const asked = [];
  messenger.voiceCompanionSetup.keepPromptsInView = async on => { asked.push(on); return {watching: on}; };
  assert.deepEqual(plain((await bridge.handle({id: 5, method: 'background', params: {on: true}})).result), {watching: true});
  assert.deepEqual(plain((await bridge.handle({id: 6, method: 'background', params: {on: 'yes'}})).result), {watching: true});
  assert.deepEqual(asked, [true, true]);
  delete messenger.voiceCompanionSetup.keepPromptsInView;
  assert.deepEqual(plain((await bridge.handle({id: 7, method: 'background', params: {on: true}})).result), {watching: false});
}

// Will, 0.2.163: his work account was added to Thunderbird twice. Voice Companion kept the first
// identity, whose outgoing server still timed out; Thunderbird itself used the fixed one.
async function identityChecks() {
  const {api, state} = createFakeMessenger();
  state.accounts.push(
    {id: 'account5', name: 'Work', type: 'imap', identities: [{id: 'id5', email: 'Will@Work.example', name: 'Will'}]},
    {id: 'account7', name: 'Work', type: 'imap', identities: [{id: 'id7', email: 'will@work.example', name: 'Will'}]});
  api.voiceCompanionSetup.outgoingServer = async id => (id === 'id5'
    ? {host: 'smtp.office365.com', port: 587, security: 'starttls'} : {host: 'smtp.office365.com', port: 465, security: 'ssl'});
  const bridge = load(api);
  const send = params => bridge.handle({id: 21, method: 'send', params: {to: ['boss@work.example'], subject: 'testing', html: '<p>Hi</p>', plain: 'Hi', ...params}});
  // The saved identity times out: nothing was sent, so the other identity for the address is tried.
  state.failSendFor = {id5: 'The connection to Outgoing server (SMTP) smtp.office365.com timed out.'};
  const answer = await send({identityId: 'id5', email: 'will@work.example'});
  assert.equal(answer.error, undefined, answer.error && answer.error.message);
  assert.equal(answer.result.identityId, 'id7'); assert.equal(answer.result.accountId, 'account7');
  assert.equal(answer.result.tried, 2);
  assert.equal(state.sent.at(-1).details.identityId, 'id7');
  assert.deepEqual(state.deleted.at(-1), {ids: [state.drafts.at(-1).id], skipTrash: false}, 'the safety copy goes to Trash');
  assert.deepEqual(state.openCompose, []);
  // A saved identity that still works is used, even when another exists.
  state.failSendFor = {};
  assert.equal((await send({identityId: 'id5', email: 'will@work.example'})).result.identityId, 'id5');
  // A saved identity Thunderbird no longer has: the address finds the current one.
  assert.equal((await send({identityId: 'gone', email: 'will@work.example'})).result.identityId, 'id7');
  // A refusal (sign-in) is not retried with another identity, and the failure names the server.
  state.failSendFor = {id5: '535 5.7.3 Authentication unsuccessful', id7: 'never tried'};
  const refused = await send({identityId: 'id5', email: 'will@work.example'});
  assert.match(refused.error.message, /did not accept the sign-in for sending\. It used smtp\.office365\.com, port 587, with STARTTLS\. Your message is saved in Drafts/);
  assert.notEqual(state.sent.at(-1).details && state.sent.at(-1).details.identityId, 'never');
  // Every identity times out: one clear failure, the message kept in Drafts.
  state.failSendFor = {id5: 'timed out', id7: 'timed out'};
  const both = await send({identityId: 'id5', email: 'will@work.example'});
  assert.match(both.error.message, /timed out\. It used smtp\.office365\.com, port 465, with SSL\/TLS\. Your message is saved in Drafts/);
  // No identity for the address at all.
  const none = await send({identityId: 'gone', email: 'nobody@work.example'});
  assert.match(none.error.message, /no longer in Thunderbird/);
}

async function outgoingServerChecks() {
  const mail = fakeMailServices();
  const identities = {id5: {key: 'id5'}, id7: {key: 'id7'}};
  const servers = {id5: {hostname: 'smtp.office365.com', port: 587, socketType: 2},
    id7: {hostname: 'smtp.office365.com', port: 465, socketType: 3}};
  mail.MailServices.accounts.getIdentity = key => identities[key] || null;
  mail.MailServices.outgoingServer = {getServerByIdentity: identity => servers[identity.key] || null};
  const api = loadExperiment(null, fakeOAuth(), mail);
  assert.deepEqual(plain(await api.outgoingServer('id5')), {host: 'smtp.office365.com', port: 587, security: 'starttls'});
  assert.deepEqual(plain(await api.outgoingServer('id7')), {host: 'smtp.office365.com', port: 465, security: 'ssl'});
  assert.equal(await api.outgoingServer('missing'), null);
  // Thunderbird 128 calls it MailServices.smtp.
  delete mail.MailServices.outgoingServer;
  mail.MailServices.smtp = {getServerByIdentity: identity => servers[identity.key] || null};
  assert.equal(plain(await api.outgoingServer('id7')).security, 'ssl');
}

async function freshnessChecks() {
  let {api, state} = createFakeMessenger();
  let bridge = load(api);
  const call = async (method, params) => {
    const answer = await bridge.handle({id: 8, method, params});
    if (answer.error) throw new Error(answer.error.message);
    return JSON.parse(JSON.stringify(answer.result));
  };
  const inbox = 'account1://INBOX';

  // New mail is on the server but not yet in Thunderbird (it was just started in the background,
  // or has not checked for a while). Listing asks Thunderbird to check that folder first.
  state.server.push(fakeMessage(26, new Date(Date.UTC(2026, 9, 26, 9)), {read: false}));
  let first = await call('list', {folderId: inbox, offset: 0, limit: 10});
  assert.equal(first.rows[0].id, 26, 'the message that just arrived is first');
  assert.equal(first.mailCheck, 'checked');
  assert.deepEqual(state.mailChecks, [{accountId: 'account1', path: '/INBOX', inbox: true}]);
  // Later pages of the same list do not check again (that would move messages between pages).
  const second = await call('list', {folderId: inbox, offset: 10, limit: 10});
  assert.equal(second.rows[0].id, 16);
  assert.equal(second.mailCheck, undefined);
  assert.equal(state.mailChecks.length, 1);
  // Other folders are checked by their own path.
  await call('list', {folderId: 'account1://Receipts', offset: 0, limit: 10});
  assert.deepEqual(state.mailChecks.at(-1), {accountId: 'account1', path: '/Receipts', inbox: false});

  // When the check fails, is offline, or is slow, the list still comes, and says why it may be old.
  state.mailCheck = 'fail';
  assert.equal((await call('list', {folderId: inbox, offset: 0, limit: 10})).mailCheck, 'failed');
  state.mailCheck = 'offline';
  assert.equal((await call('list', {folderId: inbox, offset: 0, limit: 10})).mailCheck, 'offline');
  state.mailCheck = 'hang';
  bridge.constructor.MAIL_CHECK_MS = 50;
  const started = Date.now();
  const slow = await call('list', {folderId: inbox, offset: 0, limit: 10});
  assert.equal(slow.mailCheck, 'slow');
  assert.equal(slow.rows[0].id, 26);
  assert.ok(Date.now() - started < 2000, 'a check that never ends does not hold up the list');
  const getNewMail = api.voiceCompanionSetup.getNewMail;
  api.voiceCompanionSetup.getNewMail = async () => { throw new Error('Folder busy'); };
  assert.equal((await call('list', {folderId: inbox, offset: 0, limit: 10})).mailCheck, 'failed');
  // Experiments turned off in Thunderbird, or an older Experiment: listed as before, nothing to say.
  delete api.voiceCompanionSetup.getNewMail;
  assert.equal((await call('list', {folderId: inbox, offset: 0, limit: 10})).mailCheck, 'unavailable');
  api.voiceCompanionSetup.getNewMail = getNewMail;

  // Thunderbird lists in database order, which is not date order: newest by date still comes first.
  ({api, state} = createFakeMessenger());
  bridge = load(api);
  const now = Date.now();
  state.messages = [fakeMessage(1, new Date(now - 2 * DAY)), fakeMessage(2, new Date(now - 400 * DAY)),
    fakeMessage(3, new Date(now - 3 * DAY)), fakeMessage(4, new Date(now - 60 * DAY)), fakeMessage(5, new Date(now - DAY / 2))];
  first = await call('list', {folderId: inbox, offset: 0, limit: 10});
  assert.deepEqual(first.rows.map(r => r.id), [5, 1, 3, 4, 2]);
  assert.equal(first.next, null);
  const page2 = await call('list', {folderId: inbox, offset: 0, limit: 20});
  assert.deepEqual(page2.rows.map(r => r.id), [5, 1, 3, 4, 2]);

  // A big Gmail inbox: 60,000 messages. The newest arrived last, so it is at the end of Thunderbird's list.
  ({api, state} = createFakeMessenger());
  bridge = load(api);
  state.messages = [];
  for (let i = 1; i <= 60000; i++) state.messages.push(fakeMessage(i, new Date(now - 3000 * DAY + i * 60000)));
  state.messages.push(fakeMessage(60001, new Date(now - 60000)));
  state.messages.push(fakeMessage(60002, new Date(now - 120000)));
  first = await call('list', {folderId: inbox, offset: 0, limit: 10});
  assert.deepEqual(first.rows.slice(0, 4).map(r => r.id), [60001, 60002, 60000, 59999]);
  assert.equal(first.next, 10);
  // All of it is years old, and the newest is last: still first in the list.
  for (const m of state.messages) m.date = new Date(m.date.getTime() - 2000 * DAY);
  first = await call('list', {folderId: inbox, offset: 0, limit: 100});
  assert.deepEqual(first.rows.slice(0, 3).map(r => r.id), [60001, 60002, 60000]);
  assert.equal(first.rows.length, 100);
  const deep = await call('list', {folderId: inbox, offset: 100, limit: 100});
  assert.deepEqual(deep.rows.slice(0, 2).map(r => r.id), [59902, 59901]);
}

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

function fakeWindow({opener = 'openAccountSetup', form = () => setupForm(), hub = false, typing = false} = {}) {
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
      // As in Thunderbird 158: <account-hub-container> in the body, its dialog in its own
      // shadow root, the email view inside, each step and the footer with shadow roots too.
      const step = new FakeElement('email-auto-form');
      step.shadowRoot = new FakeElement('shadow', {}, parts.filter(p => p.localName !== 'button'));
      const footer = new FakeElement('account-hub-footer');
      footer.shadowRoot = new FakeElement('shadow', {}, parts.filter(p => p.localName === 'button'));
      if (typing) {
        win.typed = []; win.checks = [];
        step.checkValidEmailForm = options => win.checks.push(options);
        step.shadowRoot.host = step;
        for (const part of step.shadowRoot.children) {
          part.parentNode = step.shadowRoot;
          if (part.localName === 'input') part.setUserInput = function (value) { win.typed.push(this.id || 'email'); this.value = value; };
        }
      }
      const view = new FakeElement('account-hub-email', {}, [step, footer]);
      const container = new FakeElement('account-hub-container');
      container.shadowRoot = new FakeElement('shadow', {}, [new FakeElement('dialog', {}, [view])]);
      document.children.push(container);
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

function loadExperiment(win, oauth = fakeOAuth(), mail = fakeMailServices(), app = {composing: 0, quits: [], observers: new Map(), watchers: new Set()}) {
  app.observers = app.observers || new Map(); app.watchers = app.watchers || new Set();
  const modules = url => (url.includes('OAuth2') ? {OAuth2: oauth.OAuth2} : url.includes('MailServices') ? {MailServices: mail.MailServices}
    : {ExtensionCommon: {ExtensionAPI: class {}}});
  const context = vm.createContext({
    ChromeUtils: {importESModule: modules, generateQI: names => function QueryInterface() { return this; }},
    Services: {wm: {getMostRecentWindow: type => (type === 'mail:3pane' ? win : null),
                    getEnumerator: type => {
                      let left = type === 'msgcompose' ? app.composing : 0;
                      return {hasMoreElements: () => left > 0, getNext: () => { left--; return {}; }};
                    }},
               io: mail.io, tm: {dispatchToMainThread: run => setTimeout(run, 0)},
               startup: {quit: how => app.quits.push(how)},
               appinfo: {version: '155.0'},
               prefs: {getDefaultBranch: () => ({getCharPref: (name, fallback) => (name === 'app.update.channel' ? 'release' : fallback)})},
               dirsvc: {get: key => (key === 'ProfD' ? {path: 'C:\\Users\\Will\\AppData\\Roaming\\Thunderbird\\Profiles\\abc.default-release'} : null)},
               // Thunderbird's notifications: observers by topic, and windows opening (Services.ww).
               obs: {addObserver: (observer, topic) => app.observers.set(topic, observer),
                     removeObserver: (observer, topic) => { if (app.observers.get(topic) === observer) app.observers.delete(topic); }},
               ww: {registerNotification: observer => app.watchers.add(observer), unregisterNotification: observer => app.watchers.delete(observer)}},
    Ci: {nsIAppStartup: {eAttemptQuit: 2}, nsISmtpServer: {}, nsIFile: {}},
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

// Thunderbird's mail accounts as the Experiment sees them (nsIMsgFolder, nsIMsgIncomingServer).
// Folder addresses encode each part, as Thunderbird does: [Gmail] is %5BGmail%5D.
function fakeMailServices() {
  const calls = [];
  const mail = {calls, io: {offline: false}, status: 0, answer: true};
  const finish = listener => { if (mail.answer) setTimeout(() => listener.OnStopRunningUrl({spec: 'imap://x'}, mail.status), 5); };
  const folder = (server, uri, flags = 0) => ({URI: uri, flags,
    updateFolderWithListener(win, listener) { calls.push({how: 'update', uri, win}); if (mail.throws) throw new Error('busy'); finish(listener); }});
  const imap = {type: 'imap'}, pop = {type: 'pop3'}, local = {type: 'none'};
  const gmailRoot = 'imap://me%40gmail.com@imap.gmail.com';
  imap.rootFolder = {URI: gmailRoot, descendants: [folder(imap, gmailRoot + '/INBOX', 0x1000),
    folder(imap, gmailRoot + '/%5BGmail%5D'), folder(imap, gmailRoot + '/%5BGmail%5D/Sent%20Mail'), folder(imap, gmailRoot + '/Receipts')],
    getFolderWithFlags(flags) { return this.descendants.find(f => f.flags & flags) || null; }};
  const popInbox = folder(pop, 'mailbox://me@pop.example.com/Inbox', 0x1000);
  pop.rootFolder = {URI: 'mailbox://me@pop.example.com', descendants: [popInbox], getFolderWithFlags: () => popInbox};
  pop.getNewMessages = (target, win, listener) => { calls.push({how: 'pop', uri: target.URI, win}); finish(listener); };
  const localInbox = folder(local, 'mailbox://nobody@Local%20Folders/Inbox', 0x1000);
  local.rootFolder = {URI: 'mailbox://nobody@Local%20Folders', descendants: [localInbox], getFolderWithFlags: () => localInbox};
  // Microsoft Exchange (Office 365) added in Thunderbird as an Exchange account, not IMAP.
  const ews = {type: 'ews'};
  const ewsRoot = 'ews://will%40work.example@outlook.office365.com';
  const ewsInbox = folder(ews, ewsRoot + '/Inbox', 0x1000);
  ews.rootFolder = {URI: ewsRoot, descendants: [ewsInbox], getFolderWithFlags: () => ewsInbox};
  const servers = {account1: imap, account2: pop, account3: local, account4: ews};
  mail.MailServices = {accounts: {getAccount: id => (servers[id] ? {incomingServer: servers[id]} : null)}};
  return mail;
}

async function mailCheckChecks() {
  const mail = fakeMailServices();
  const api = loadExperiment(null, fakeOAuth(), mail);
  const gmail = 'imap://me%40gmail.com@imap.gmail.com';
  // IMAP (Gmail): the folder named by its add-on path is updated from the server, with no window.
  assert.deepEqual(plain(await api.getNewMail('account1', '/INBOX', true)), {checked: true, offline: false});
  assert.deepEqual(mail.calls.at(-1), {how: 'update', uri: gmail + '/INBOX', win: null});
  assert.deepEqual(plain(await api.getNewMail('account1', '/[Gmail]/Sent Mail', false)), {checked: true, offline: false});
  assert.equal(mail.calls.at(-1).uri, gmail + '/%5BGmail%5D/Sent%20Mail');
  // A path that is not found: the inbox is still found by its kind; any other folder is not checked.
  assert.deepEqual(plain(await api.getNewMail('account1', '/Inbox', true)), {checked: true, offline: false});
  assert.equal(mail.calls.at(-1).uri, gmail + '/INBOX');
  const before = mail.calls.length;
  assert.deepEqual(plain(await api.getNewMail('account1', '/Missing', false)), {checked: false, offline: false});
  assert.deepEqual(plain(await api.getNewMail('account9', '/INBOX', true)), {checked: false, offline: false});
  assert.equal(mail.calls.length, before);
  // The server refused (for example, Gmail needs the user to sign in again).
  mail.status = 0x804B000D;
  assert.deepEqual(plain(await api.getNewMail('account1', '/INBOX', true)), {checked: false, offline: false});
  mail.status = 0;
  mail.throws = true;
  assert.deepEqual(plain(await api.getNewMail('account1', '/INBOX', true)), {checked: false, offline: false});
  mail.throws = false;
  // POP: Thunderbird's Get Messages for the inbox. Local Folders: nothing to fetch.
  assert.deepEqual(plain(await api.getNewMail('account2', '/Inbox', true)), {checked: true, offline: false});
  assert.deepEqual(mail.calls.at(-1), {how: 'pop', uri: 'mailbox://me@pop.example.com/Inbox', win: null});
  const count = mail.calls.length;
  assert.deepEqual(plain(await api.getNewMail('account3', '/Inbox', true)), {checked: true, offline: false});
  assert.equal(mail.calls.length, count);
  // Exchange (Will's work account, 0.2.161): its inbox is fetched from the server too, never
  // skipped as if it were kept on this computer.
  assert.deepEqual(plain(await api.getNewMail('account4', '/Inbox', true)), {checked: true, offline: false});
  assert.deepEqual(mail.calls.at(-1), {how: 'update', uri: 'ews://will%40work.example@outlook.office365.com/Inbox', win: null});
  // Thunderbird set to work offline: nothing is asked of the server, and it says so.
  mail.io.offline = true;
  const offlineCount = mail.calls.length;
  assert.deepEqual(plain(await api.getNewMail('account1', '/INBOX', true)), {checked: false, offline: true});
  assert.equal(mail.calls.length, offlineCount);
}

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
    notes: ['Thunderbird failed to find the settings'], button: 'Continue', canContinue: true, needsPassword: false,
    fields: ['text#realName', 'email'], addressFilled: true, choices: []});
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
  const hubPage = plain(await api.setupPage());
  assert.equal(hubPage.title, 'Set up your existing email address');
  assert.equal(hubPage.button, 'Continue');
  assert.deepEqual(plain(await api.setupContinue()), {pressed: true});
  assert.equal(win.parts.at(-1).clicks, 1);
  assert.equal(hubPage.needsPassword, true);
  assert.deepEqual(plain(await api.fillPassword('app pass')), {filled: true});
  const password = win.parts.find(p => p.id === 'password');
  assert.equal(password.value, 'app pass');
  assert.equal((await api.setupPage()).needsPassword, false);
  assert.doesNotMatch(JSON.stringify(plain(await api.setupPage())), /app pass/, 'a password is never read back');

  // Account Hub loading slowly (as on a busy computer): filled in when the page is read later.
  win = fakeWindow({hub: true});
  const slowOpen = win.openAccountSetup;
  let release;
  win.openAccountSetup = async () => { win.calls.push('openAccountSetup'); await new Promise(r => { release = r; }); };
  api = loadExperiment(win);
  const opening = api.openAccountSetup('late@example.org', 'Late Name', false);
  await new Promise(r => setTimeout(r, 5));
  release();
  assert.equal((await opening).prefilled, false, 'nothing there yet');
  await slowOpen();                      // the hub arrives afterwards
  await new Promise(r => setTimeout(r, 20));
  const late = plain(await api.setupPage());
  assert.equal(win.parts[2].value, 'late@example.org');
  assert.equal(win.parts[1].value, 'Late Name');
  assert.equal(late.canContinue, true);
  assert.equal((await api.setupPage()).title, 'Set up your existing email address');

  // As in Thunderbird 158: the hub resets its form just after it appears, clearing the boxes.
  // They are filled in again, so the address is there when Voice Companion reads the page.
  win = fakeWindow({hub: true});
  api = loadExperiment(win);
  const resetSoon = new Promise(resolve => setTimeout(() => {
    for (const part of win.parts) if (part.localName === 'input') part.value = '';
    resolve();
  }, 40));
  assert.deepEqual(plain(await api.openAccountSetup('reset@example.org', 'Reset Name', false)), {opened: true, prefilled: true});
  await resetSoon;
  const afterReset = plain(await api.setupPage());
  assert.equal(win.parts[2].value, 'reset@example.org');
  assert.equal(win.parts[1].value, 'Reset Name');
  assert.equal(afterReset.addressFilled, true);
  assert.doesNotMatch(JSON.stringify(afterReset), /reset@example\.org/, 'the page report never carries what is typed');
  assert.deepEqual(plain(await api.setupContinue()), {pressed: true});
  win.parts[2].value = '';                     // the next step: nothing is filled in any more
  await api.setupPage();
  assert.equal(win.parts[2].value, '');

  // Thunderbird 158 turns Continue on only when the page sees typing: setUserInput is used
  // when present, and the email step is asked to check itself.
  win = fakeWindow({hub: true, typing: true});
  api = loadExperiment(win);
  await api.openAccountSetup('typed@example.org', 'Typed Name', false);
  assert.deepEqual(win.typed.sort(), ['email', 'realName']);
  assert.equal(win.parts[2].value, 'typed@example.org');
  assert.ok(win.checks.length >= 1 && win.checks.every(c => c.showErrors === false), 'the email step re-checked itself');

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
  // 1.2.0 checks for new mail before listing; 1.7.0 adds about() and Microsoft Graph for Thunderbird 154.
  // A higher version replaces installed copies.
  assert.ok(major > 1 || minor >= 7, 'the add-on version was raised so installed copies are replaced');
}

// Contacts and calendars (pim.js, experiment/calendar.js) have their own file.
require('./test-pim.cjs');

// Voice Companion Mail Connection for Thunderbird.
// Answers Voice Companion's mail requests over native messaging. Thunderbird
// keeps the account sign-in; this add-on never sees or stores passwords.
// No message content, addresses or subjects are logged.
'use strict';

const HOST = 'com.voicecompanion.mail';
const PROTOCOL = 3;
const RETRY_MS = 30000;
const MAX_MESSAGES = 50000;      // the newest this many are kept for paging; older ones are read past
const PAGE_SIZES = [10, 20, 30, 40, 50, 100];
const DAY_MS = 24 * 60 * 60 * 1000;
// Recent mail first: most lists are filled from the last week, without reading years of mail.
const RECENT_DAYS = [7, 31, 366];

class MailBridge {
  constructor(api, clock = () => Date.now()) {
    this.api = api;
    this.clock = clock;
    // The newest-first list of the folder last paged through, so each later page
    // (and End) does not read the whole folder again: {folderId, at, headers}.
    this.listCache = null;
  }

  async handle(message) {
    const id = message && message.id;
    if (!Number.isInteger(id) || typeof message.method !== 'string' || !MailBridge.METHODS.includes(message.method)) {
      return {id: Number.isInteger(id) ? id : -1, error: {code: 'invalid', message: 'Unsupported mail request.'}};
    }
    // Anything that changes mail or folders makes the kept list out of date.
    if (!MailBridge.READ_ONLY.includes(message.method)) this.listCache = null;
    try {
      const result = await this[message.method](message.params || {});
      return {id, result: result === undefined ? null : result};
    } catch (error) {
      // Error text from Thunderbird can include addresses; keep it generic.
      return {id, error: {code: error && error.code || 'failed', message: error && error.safeMessage || 'Thunderbird could not complete that request.'}};
    }
  }

  static fail(code, safeMessage) {
    const error = new Error(safeMessage);
    error.code = code; error.safeMessage = safeMessage;
    return error;
  }

  async accounts() {
    const accounts = await this.api.accounts.list(false);
    return accounts
      .filter(account => (account.identities || []).length)
      .map(account => ({
        id: account.id, name: account.name, type: account.type,
        identities: account.identities.map(identity => ({id: identity.id, email: identity.email, name: identity.name || ''}))
      }));
  }

  async folder(folderId) {
    if (typeof folderId !== 'string' || !folderId) throw MailBridge.fail('folder', 'That folder is not available.');
    const folder = await this.api.folders.get(folderId, false);
    if (!folder) throw MailBridge.fail('folder', 'That folder is not available. Say list folders to refresh.');
    return folder;
  }

  static display(folder) {
    if ((folder.specialUse || []).includes('inbox')) return 'Inbox';
    const parts = String(folder.path || folder.name || '').split('/').filter(Boolean);
    if (parts[0] === '[Gmail]' || parts[0] === '[Google Mail]') parts.shift();
    return parts.join('/') || folder.name || 'Folder';
  }

  async folders({accountId}) {
    const folders = await this.api.folders.query({accountId});
    return folders.filter(folder => !folder.isRoot).map(folder => ({
      id: folder.id, name: MailBridge.display(folder), path: folder.path || '', specialUse: folder.specialUse || []
    }));
  }

  async inbox({accountId}) {
    const [inbox] = await this.api.folders.query({accountId, specialUse: ['inbox']});
    if (!inbox) throw MailBridge.fail('folder', 'This account has no inbox in Thunderbird.');
    return {id: inbox.id, name: 'Inbox'};
  }

  async folderInfo({folderId}) {
    const info = await this.api.folders.getFolderInfo(await this.folder(folderId));
    return {messagesTotal: info.totalMessageCount, unread: info.unreadMessageCount};
  }

  // Thunderbird shows only mail it has downloaded. It checks the server on its own every few
  // minutes, but not at once after it starts in the background, and not if the check failed.
  // So before a list, ask it to check this folder now, and wait a limited time.
  // Answers 'checked', 'failed', 'offline', 'slow', or 'unavailable' (no Experiment).
  async checkMail(folder) {
    const setup = this.api.voiceCompanionSetup;
    if (!setup || typeof setup.getNewMail !== 'function') return 'unavailable';
    let timer;
    const slow = new Promise(resolve => { timer = setTimeout(() => resolve('slow'), MailBridge.MAIL_CHECK_MS); });
    const check = (async () => {
      const result = await setup.getNewMail(String(folder.accountId || ''), String(folder.path || ''),
        (folder.specialUse || []).includes('inbox'));
      if (result && result.offline) return 'offline';
      return result && result.checked ? 'checked' : 'failed';
    })().catch(() => 'failed');
    try {
      return await Promise.race([check, slow]);
    } finally {
      clearTimeout(timer);
    }
  }

  // Every message in the folder dated after fromDate (all when null), newest first.
  // Thunderbird answers in its own database order, page by page, so every page is read.
  async headers(folderId, fromDate) {
    const query = {folderId, messagesPerPage: 1000, autoPaginationTimeout: 0};
    if (fromDate) query.fromDate = fromDate;
    const newest = (a, b) => b.date - a.date || b.id - a.id;
    let found = [];
    let page = await this.api.messages.query(query);
    while (page) {
      for (const header of page.messages || []) found.push({id: header.id, date: new Date(header.date).getTime() || 0, header});
      // A huge folder: keep the newest, never the first ones read (those are the oldest).
      if (found.length >= 2 * MAX_MESSAGES) found = found.sort(newest).slice(0, MAX_MESSAGES);
      if (!page.id) break;
      page = await this.api.messages.continueList(page.id);
    }
    found.sort(newest);
    return found.slice(0, MAX_MESSAGES);
  }

  // The whole folder, newest first, kept for a short time for paging.
  async allHeaders(folderId) {
    const kept = this.listCache;
    if (kept && kept.folderId === folderId && this.clock() - kept.at < MailBridge.LIST_KEEP_MS) return kept.headers;
    const headers = await this.headers(folderId, null);
    this.listCache = {folderId, at: this.clock(), headers};
    return headers;
  }

  // One page of a folder, newest first. With last: true, the page holding the oldest
  // message, found in one step (End), never by reading every page in turn.
  async list({folderId, offset = 0, limit = 10, last = false}) {
    if (!PAGE_SIZES.includes(limit) || !Number.isInteger(offset) || offset < 0) throw MailBridge.fail('page', 'Invalid message page.');
    const folder = await this.folder(folderId);
    let headers = null, mailCheck;
    if (offset === 0 && !last) {
      // A fresh list: check the server first, and read only recent mail when that fills the page.
      this.listCache = null;
      mailCheck = await this.checkMail(folder);
      const needed = limit + 1;   // one more than the page tells whether more messages follow
      const now = this.clock();
      for (const days of RECENT_DAYS) {
        const recent = await this.headers(folderId, new Date(now - days * DAY_MS));
        // Everything newer than the cut-off is here, so the newest messages are complete.
        if (recent.length >= needed) { headers = recent; break; }
      }
    }
    if (!headers) headers = await this.allHeaders(folderId);
    if (last) offset = headers.length ? Math.floor((headers.length - 1) / limit) * limit : 0;
    const rows = headers.slice(offset, offset + limit).map(({header}) => ({
      id: header.id, from: header.author || '', subject: header.subject || '',
      date: header.date ? new Date(header.date).toISOString() : '', size: header.size, unread: header.read === false
    }));
    const answer = {rows, offset, next: headers.length > offset + limit ? offset + limit : null};
    if (headers === (this.listCache && this.listCache.headers)) answer.total = headers.length;
    if (mailCheck) answer.mailCheck = mailCheck;
    return answer;
  }

  static textOf(part, type, found = []) {
    if (!part) return found;
    if (part.contentType === type && typeof part.body === 'string') found.push(part.body);
    for (const child of part.parts || []) MailBridge.textOf(child, type, found);
    return found;
  }

  async read({messageId}) {
    if (!Number.isInteger(messageId)) throw MailBridge.fail('message', 'That message is not available.');
    let html = '', plain = '';
    if (this.api.messages.listInlineTextParts) {
      const parts = await this.api.messages.listInlineTextParts(messageId);
      html = (parts.find(p => p.contentType === 'text/html') || {}).content || '';
      plain = (parts.find(p => p.contentType === 'text/plain') || {}).content || '';
    } else {
      const full = await this.api.messages.getFull(messageId);
      html = MailBridge.textOf(full, 'text/html')[0] || '';
      plain = MailBridge.textOf(full, 'text/plain')[0] || '';
    }
    return {html, plain};
  }

  async context({messageId}) {
    if (!Number.isInteger(messageId)) throw MailBridge.fail('message', 'That message is not available.');
    const header = await this.api.messages.get(messageId);
    const full = await this.api.messages.getFull(messageId);
    const replyTo = ((full.headers || {})['reply-to'] || [])[0] || '';
    return {
      author: header.author || '', subject: header.subject || '', recipients: header.recipients || [],
      ccList: header.ccList || [], replyTo, headerMessageId: header.headerMessageId || ''
    };
  }

  static ids(messageIds) {
    if (!Array.isArray(messageIds) || !messageIds.length || !messageIds.every(Number.isInteger)) {
      throw MailBridge.fail('message', 'No messages were chosen.');
    }
    return messageIds;
  }

  async move({messageIds, folderId}) {
    await this.api.messages.move(MailBridge.ids(messageIds), await this.folder(folderId));
    return true;
  }

  async trash({messageIds}) {
    // skipTrash false: Thunderbird moves the messages to the account's Trash.
    await this.api.messages.delete(MailBridge.ids(messageIds), false);
    return true;
  }

  static folderName(name) {
    if (typeof name !== 'string' || !name.trim() || name.length > 80 || /[\r\n\/\\]/.test(name)) {
      throw MailBridge.fail('folder', 'Use a short folder name without slashes.');
    }
    return name.trim();
  }

  async createFolder({parentId, accountId, name}) {
    let parent;
    if (parentId) parent = await this.folder(parentId);
    else {
      const account = await this.api.accounts.get(accountId, false);
      if (!account || !account.rootFolder) throw MailBridge.fail('folder', 'That account is not available.');
      parent = account.rootFolder;
    }
    const folder = await this.api.folders.create(parent, MailBridge.folderName(name));
    return {id: folder.id, name: MailBridge.display(folder)};
  }

  async renameFolder({folderId, name}) {
    const folder = await this.api.folders.rename(await this.folder(folderId), MailBridge.folderName(name));
    return {id: folder.id, name: MailBridge.display(folder)};
  }

  async moveFolder({folderId, destinationId}) {
    const folder = await this.api.folders.move(await this.folder(folderId), await this.folder(destinationId));
    return {id: folder.id, name: MailBridge.display(folder)};
  }

  async deleteFolder({folderId}) {
    await this.api.folders.delete(await this.folder(folderId));
    return true;
  }

  // Thunderbird's own "add email account" page, driven by Voice Companion (experiment/).
  // Thunderbird asks for the sign-in itself; passwords pass through once and are never kept.
  setup() {
    const setup = this.api.voiceCompanionSetup;
    return setup && typeof setup.openAccountSetup === 'function' ? setup : null;
  }

  async openAccountSetup({email = '', name = '', show = false}) {
    if (typeof email !== 'string' || email.length > 254 || (email && !/^[^\s@]+@[^\s@]+$/.test(email))) {
      throw MailBridge.fail('address', 'That is not a complete email address.');
    }
    if (typeof name !== 'string' || name.length > 100 || /[\r\n]/.test(name)) throw MailBridge.fail('name', 'Use a shorter name.');
    const setup = this.setup();
    if (!setup) return {opened: false, prefilled: false};
    try {
      const result = await setup.openAccountSetup(email, name, Boolean(show));
      return {opened: Boolean(result && result.opened), prefilled: Boolean(result && result.prefilled)};
    } catch (error) {
      return {opened: false, prefilled: false};
    }
  }

  async setupPage() {
    const closed = {open: false, title: '', notes: [], button: '', canContinue: false, needsPassword: false, fields: [],
      addressFilled: false};
    const setup = this.setup();
    if (!setup) return closed;
    try {
      const page = await setup.setupPage();
      return {open: Boolean(page.open), title: String(page.title || ''), notes: (page.notes || []).map(String).slice(0, 5),
        button: String(page.button || ''), canContinue: Boolean(page.canContinue), needsPassword: Boolean(page.needsPassword),
        fields: (page.fields || []).map(String).slice(0, 12), addressFilled: Boolean(page.addressFilled)};
    } catch (error) {
      return closed;
    }
  }

  async setupContinue() {
    const setup = this.setup();
    try { return {pressed: Boolean(setup && (await setup.setupContinue()).pressed)}; } catch (error) { return {pressed: false}; }
  }

  async fillPassword({password}) {
    if (typeof password !== 'string' || !password || password.length > 1000) throw MailBridge.fail('password', 'The password box was empty.');
    const setup = this.setup();
    try { return {filled: Boolean(setup && (await setup.fillPassword(password)).filled)}; } catch (error) { return {filled: false}; }
  }

  async showSetup() {
    const setup = this.setup();
    try { return {shown: Boolean(setup && (await setup.showSetup()).shown)}; } catch (error) { return {shown: false}; }
  }

  async captureSignIn({seconds = 0}) {
    if (!Number.isInteger(seconds) || seconds < 0) throw MailBridge.fail('time', 'Invalid time.');
    const setup = this.setup();
    try { return {capturing: Boolean(setup && (await setup.captureSignIn(seconds)).capturing)}; } catch (error) { return {capturing: false}; }
  }

  async takeSignIn() {
    const setup = this.setup();
    try {
      const found = setup ? await setup.takeSignIn() : null;
      const url = found && typeof found.url === 'string' && /^https:\/\//i.test(found.url) ? found.url : '';
      return {url, where: found && ['browser', 'thunderbird'].includes(found.where) ? found.where : ''};
    } catch (error) {
      return {url: '', where: ''};
    }
  }

  // Started by Voice Companion in the background: keep Thunderbird's windows out of the way.
  async minimize() {
    if (!this.api.windows || !this.api.windows.getAll) return {minimized: 0};
    let count = 0;
    for (const win of await this.api.windows.getAll({windowTypes: ['normal']})) {
      if (win.state === 'minimized') continue;
      try { await this.api.windows.update(win.id, {state: 'minimized'}); count++; } catch (error) { /* only a courtesy */ }
    }
    return {minimized: count};
  }

  static innerBody(html) {
    const match = /<body[^>]*>([\s\S]*)<\/body>/i.exec(html || '');
    return match ? match[1] : (html || '');
  }

  async send(params) {
    const {identityId, to = [], cc = [], bcc = [], subject = '', html = '', plain = '', plainOnly = false, reply, forward} = params;
    if (typeof identityId !== 'string' || !identityId) throw MailBridge.fail('identity', 'Choose a Thunderbird account first.');
    let tab;
    if (reply) {
      tab = await this.api.compose.beginReply(reply.messageId, reply.type === 'replyToAll' ? 'replyToAll' : 'replyToSender', {identityId});
    } else if (forward) {
      tab = await this.api.compose.beginForward(forward.messageId, 'forwardInline', {identityId});
    } else {
      tab = await this.api.compose.beginNew(null, {identityId});
    }
    try {
      // The compose window would take keyboard focus from Voice Companion.
      if (this.api.windows && tab.windowId !== undefined) await this.api.windows.update(tab.windowId, {state: 'minimized'});
    } catch (error) { /* minimizing is only a courtesy */ }
    const current = await this.api.compose.getComposeDetails(tab.id);
    const details = {to, cc, bcc, subject};
    if (current.isPlainText || plainOnly) {
      details.isPlainText = true;
      details.plainTextBody = plain + (reply || forward ? '\n\n' + (current.plainTextBody || '') : '');
    } else {
      details.isPlainText = false;
      details.body = (reply || forward)
        ? '<html><body>' + MailBridge.innerBody(html) + MailBridge.innerBody(current.body) + '</body></html>'
        : html;
    }
    await this.api.compose.setComposeDetails(tab.id, details);
    let sent;
    try {
      sent = await this.api.compose.sendMessage(tab.id, {mode: 'sendNow'});
    } catch (error) {
      throw MailBridge.fail('send', 'Thunderbird could not send this message. It is still open as a draft in Thunderbird.');
    }
    return {headerMessageId: sent && sent.headerMessageId || ''};
  }
}

MailBridge.MAIL_CHECK_MS = 15000;   // longest wait for Thunderbird's check for new mail
MailBridge.LIST_KEEP_MS = 120000;   // how long a folder's list is reused for its later pages
MailBridge.READ_ONLY = ['accounts', 'folders', 'inbox', 'folderInfo', 'list', 'read', 'context', 'setupPage', 'captureSignIn', 'takeSignIn'];
MailBridge.METHODS = ['accounts', 'folders', 'inbox', 'folderInfo', 'list', 'read', 'context', 'move', 'trash',
  'createFolder', 'renameFolder', 'moveFolder', 'deleteFolder', 'send', 'openAccountSetup', 'setupPage', 'setupContinue', 'fillPassword', 'showSetup',
  'captureSignIn', 'takeSignIn', 'minimize'];
globalThis.MailBridge = MailBridge;

function startConnection(api) {
  const bridge = new MailBridge(api);
  let port = null;
  const connect = () => {
    try {
      port = api.runtime.connectNative(HOST);
    } catch (error) {
      setTimeout(connect, RETRY_MS);
      return;
    }
    port.onMessage.addListener(async message => {
      const response = await bridge.handle(message);
      try { port.postMessage(response); } catch (error) { /* reconnect below */ }
    });
    port.onDisconnect.addListener(() => { port = null; setTimeout(connect, RETRY_MS); });
    port.postMessage({protocol: PROTOCOL, command: 'mail', version: api.runtime.getManifest().version});
  };
  connect();
  return {bridge, port: () => port};
}

const thunderbird = globalThis.messenger || globalThis.browser;
if (thunderbird && thunderbird.runtime && thunderbird.runtime.connectNative && !globalThis.VOICE_COMPANION_TEST) {
  startConnection(thunderbird);
}
globalThis.startConnection = startConnection;

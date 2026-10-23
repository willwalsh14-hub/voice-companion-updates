// Voice Companion Mail Connection for Thunderbird.
// Answers Voice Companion's mail requests over native messaging. Thunderbird
// keeps the account sign-in; this add-on never sees or stores passwords.
// No message content, addresses or subjects are logged.
'use strict';

const HOST = 'com.voicecompanion.mail';
const PROTOCOL = 3;
const RETRY_MS = 30000;
const MAX_MESSAGES = 50000;
const PAGE_SIZES = [10, 20, 30, 40, 50, 100];

class MailBridge {
  constructor(api) {
    this.api = api;
  }

  async handle(message) {
    const id = message && message.id;
    if (!Number.isInteger(id) || typeof message.method !== 'string' || !MailBridge.METHODS.includes(message.method)) {
      return {id: Number.isInteger(id) ? id : -1, error: {code: 'invalid', message: 'Unsupported mail request.'}};
    }
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

  async list({folderId, offset = 0, limit = 10}) {
    if (!PAGE_SIZES.includes(limit) || !Number.isInteger(offset) || offset < 0) throw MailBridge.fail('page', 'Invalid message page.');
    await this.folder(folderId);
    const headers = [];
    let page = await this.api.messages.query({folderId, messagesPerPage: 1000, autoPaginationTimeout: 0});
    while (page) {
      for (const header of page.messages || []) {
        headers.push({id: header.id, date: new Date(header.date).getTime() || 0, header});
        if (headers.length >= MAX_MESSAGES) break;
      }
      if (!page.id || headers.length >= MAX_MESSAGES) break;
      page = await this.api.messages.continueList(page.id);
    }
    // Newest first, like Gmail and Outlook in Voice Companion.
    headers.sort((a, b) => b.date - a.date || b.id - a.id);
    const rows = headers.slice(offset, offset + limit).map(({header}) => ({
      id: header.id, from: header.author || '', subject: header.subject || '',
      date: header.date ? new Date(header.date).toISOString() : '', size: header.size, unread: header.read === false
    }));
    return {rows, next: headers.length > offset + limit ? offset + limit : null};
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

  // Thunderbird's own "add email account" page, with the address filled in where possible.
  // Thunderbird then asks for the sign-in itself; this add-on never sees a password.
  async openAccountSetup({email = ''}) {
    if (typeof email !== 'string' || email.length > 254 || (email && !/^[^\s@]+@[^\s@]+$/.test(email))) {
      throw MailBridge.fail('address', 'That is not a complete email address.');
    }
    const setup = this.api.voiceCompanionSetup;
    if (!setup || typeof setup.openAccountSetup !== 'function') return {opened: false, prefilled: false};
    try {
      const result = await setup.openAccountSetup(email);
      return {opened: Boolean(result && result.opened), prefilled: Boolean(result && result.prefilled)};
    } catch (error) {
      return {opened: false, prefilled: false};
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

MailBridge.METHODS = ['accounts', 'folders', 'inbox', 'folderInfo', 'list', 'read', 'context', 'move', 'trash',
  'createFolder', 'renameFolder', 'moveFolder', 'deleteFolder', 'send', 'openAccountSetup', 'minimize'];
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

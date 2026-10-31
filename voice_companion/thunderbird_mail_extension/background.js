// Voice Companion Mail Connection for Thunderbird.
// Answers Voice Companion's mail requests over native messaging. Thunderbird
// keeps the account sign-in; this add-on never sees or stores passwords.
// No message content, addresses or subjects are logged.
'use strict';

const HOST = 'com.voicecompanion.mail';
const PROTOCOL = 3;
// When the host cannot start (Voice Companion is being updated), try again soon, then less
// often. The host itself waits for Voice Companion, so this is rarely needed.
const RETRY_FIRST_MS = 2000;
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
    // The attachment file being saved, kept between its pieces: {key, file}.
    this.attachmentCache = null;
    // Files Voice Companion is sending, arriving in pieces: id -> {name, size, received, chunks, at}.
    this.uploads = new Map();
    this.uploadSerial = 0;
  }

  async handle(message) {
    const id = message && message.id;
    if (!Number.isInteger(id) || typeof message.method !== 'string' || !MailBridge.METHODS.includes(message.method)) {
      return {id: Number.isInteger(id) ? id : -1, error: {code: 'invalid', message: 'Unsupported mail request.'}};
    }
    // Anything that changes mail or folders makes the kept list out of date.
    // Marking changes the kept list itself (keepMarks), so it is kept.
    if (!MailBridge.READ_ONLY.includes(message.method) && !MailBridge.KEEPS_LIST.includes(message.method)) this.listCache = null;
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
    const page = headers.slice(offset, offset + limit);
    const status = await this.status(page.map(({header}) => header.id));
    const rows = page.map(({header}) => Object.assign({
      id: header.id, from: header.author || '', subject: header.subject || '',
      date: header.date ? new Date(header.date).toISOString() : '', size: header.size, unread: header.read === false,
      flagged: header.flagged === true, junk: header.junk === true
    }, status[header.id] || {}));
    const answer = {rows, offset, next: headers.length > offset + limit ? offset + limit : null};
    if (headers === (this.listCache && this.listCache.headers)) answer.total = headers.length;
    if (mailCheck) answer.mailCheck = mailCheck;
    return answer;
  }

  // Replied, forwarded and importance: Thunderbird's add-on interface does not give them, so the
  // Experiment reads them from Thunderbird's own message list. {id: {replied, forwarded, priority}};
  // empty when the Experiment is older or cannot read them (nothing is then said about them).
  async status(ids) {
    const setup = this.api.voiceCompanionSetup;
    if (!ids.length || !setup || typeof setup.messageStatus !== 'function') return {};
    try {
      const found = await setup.messageStatus(ids);
      // Gmail's Important marker, for Gmail messages only.
      let important = {};
      const marks = this.api.voiceCompanionMarks;
      if (marks && typeof marks.isImportant === 'function') {
        try { important = await marks.isImportant(ids) || {}; } catch (error) { important = {}; }
      }
      const status = {};
      for (const id of ids) {
        const item = found && found[String(id)];
        if (!item) continue;
        status[id] = {replied: item.replied === true, forwarded: item.forwarded === true,
          priority: MailBridge.PRIORITIES.includes(item.priority) ? item.priority : ''};
        if (typeof important[String(id)] === 'boolean') status[id].important = important[String(id)];
      }
      return status;
    } catch (error) {
      return {};
    }
  }

  // Mark messages read or unread, flagged or not, as Thunderbird's own Mark menu does.
  // The kept list is changed to match, so the next page says the new state.
  async mark({ids, read, flagged, conversation = true}) {
    if (!Array.isArray(ids) || !ids.length || !ids.every(Number.isInteger)) throw MailBridge.fail('message', 'Choose a message first.');
    const changes = {};
    if (typeof read === 'boolean') changes.read = read;
    if (typeof flagged === 'boolean') changes.flagged = flagged;
    if (!Object.keys(changes).length) throw MailBridge.fail('mark', 'Nothing to change.');
    // Will, October 31, 2026: a read mark stayed in Thunderbird and never reached Gmail. The
    // Experiment marks, then waits for the IMAP server's own answer (one retry), and for Gmail
    // takes in the whole conversation when marking read, as Gmail's website does.
    const marks = this.api.voiceCompanionMarks;
    if (marks && typeof marks.mark === 'function') {
      const result = await marks.mark(ids, 'read' in changes ? changes.read : null, 'flagged' in changes ? changes.flagged : null,
        conversation !== false);
      if (!result || !result.changed) {
        throw MailBridge.fail('mark', 'Thunderbird could not change that message. It may have been moved or deleted. Say list messages to refresh.');
      }
      const others = Array.isArray(result.conversationIds) ? result.conversationIds.filter(Number.isInteger) : [];
      this.keepMarks(ids, changes);
      if (others.length) this.keepMarks(others, {read: true});
      const answer = {changed: result.changed, missing: result.missing || 0, server: result.server || 'sent'};
      if (result.conversation) answer.conversation = result.conversation;
      if (others.length) answer.conversationIds = others;
      if (result.server === 'local') answer.thisComputerOnly = true;
      return answer;
    }
    const done = [];
    for (const id of ids) {
      try {
        await this.api.messages.update(id, changes);
        done.push(id);
      } catch (error) { /* a message that is gone: counted below */ }
    }
    this.keepMarks(done, changes);
    if (!done.length) throw MailBridge.fail('mark', 'Thunderbird could not change that message. It may have been moved or deleted. Say list messages to refresh.');
    const answer = {changed: done.length, missing: ids.length - done.length};
    // Thunderbird sends these marks to the mail server (IMAP and Exchange), so phones and the
    // web show them too. A POP or Local Folders account has no server copy to mark: say so.
    if (await this.onlyOnThisComputer(done[0])) answer.thisComputerOnly = true;
    return answer;
  }

  async onlyOnThisComputer(messageId) {
    try {
      const header = await this.api.messages.get(messageId);
      const accountId = header && header.folder && header.folder.accountId;
      const account = accountId && await this.api.accounts.get(accountId, false);
      return Boolean(account && MailBridge.LOCAL_ACCOUNT_TYPES.includes(account.type));
    } catch (error) {
      return false;
    }
  }

  keepMarks(ids, changes) {
    const kept = this.listCache;
    if (!kept) return;
    for (const item of kept.headers) if (ids.includes(item.id)) Object.assign(item.header, changes);
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
    // The message's files, said when it is read. null: Thunderbird could not list them.
    let attachments = null;
    try { attachments = await this.listFiles(messageId); } catch (error) { attachments = null; }
    // Opening a message marks it read, as it does in Thunderbird, and in Gmail the whole
    // conversation, as Gmail's website does when a conversation is opened.
    let read = false, server = '', others = [], result = null;
    const marks = this.api.voiceCompanionMarks;
    try {
      if (marks && typeof marks.mark === 'function') {
        result = await marks.mark([messageId], true, null, true);
        read = Boolean(result && result.changed);
        server = result && result.server || '';
      } else {
        await this.api.messages.update(messageId, {read: true});
        read = true;
      }
      if (read) this.keepMarks([messageId], {read: true});
      if (read && result && Array.isArray(result.conversationIds)) {
        others = result.conversationIds.filter(Number.isInteger);
        this.keepMarks(others, {read: true});
      }
    } catch (error) { read = false; }
    const answer = {html, plain, attachments, read};
    if (others.length) answer.conversationIds = others;
    if (server === 'failed') answer.readNotConfirmed = true;
    const receipt = await this.receipt(messageId);
    if (receipt) answer.receipt = receipt;
    return answer;
  }

  // A read receipt the message asks for, through Thunderbird's own receipt sender and its
  // settings: '' (none asked, or already handled), 'handled' (sent or not, as the settings
  // say), or 'ask' (the settings say to ask: Voice Companion asks, then calls receiptAnswer).
  async receipt(messageId) {
    const marks = this.api.voiceCompanionMarks;
    if (!marks || typeof marks.needsReceipt !== 'function') return '';
    try {
      const due = await marks.needsReceipt(messageId);
      if (due !== 'yes' && due !== 'maybe') return '';
      if (due === 'maybe') {
        // Only Thunderbird's copy of the whole message can say; a very large one is left alone.
        const header = await this.api.messages.get(messageId);
        if (header && header.size > MailBridge.RECEIPT_CHECK_BYTES) return '';
      }
      const headers = MailBridge.headerBlock(await this.api.messages.getRaw(messageId));
      if (!/^(?:disposition-notification-to|return-receipt-to)[ \t]*:/im.test(headers)) return '';
      const result = await marks.receipt(messageId, headers);
      return result && (result.state === 'ask' || result.state === 'handled') ? result.state : '';
    } catch (error) {
      return '';
    }
  }

  async receiptAnswer({messageId, send}) {
    if (!Number.isInteger(messageId)) throw MailBridge.fail('message', 'That message is not available.');
    const marks = this.api.voiceCompanionMarks;
    if (!marks || typeof marks.receiptAnswer !== 'function') return {done: false};
    const result = await marks.receiptAnswer(messageId, send === true);
    return {done: Boolean(result && result.done)};
  }

  // The header lines of a raw message (text up to the first empty line), for the receipt sender.
  static headerBlock(raw) {
    const text = typeof raw === 'string' ? raw : '';
    const end = text.search(/\r?\n\r?\n/);
    return (end < 0 ? text : text.slice(0, end)).slice(0, 256 * 1024) + '\r\n';
  }

  // Gmail's own Important marker on a message you got: on or off. Other accounts cannot change it.
  async important({messageId, on}) {
    if (!Number.isInteger(messageId)) throw MailBridge.fail('message', 'Choose a message first.');
    const marks = this.api.voiceCompanionMarks;
    if (!marks || typeof marks.important !== 'function') {
      throw MailBridge.fail('important', 'Thunderbird needs the newer Voice Companion add-on for this. Restart Thunderbird, then try again.');
    }
    const result = await marks.important(messageId, on === true) || {};
    if (result.done) {
      const kept = this.listCache;
      if (kept) for (const item of kept.headers) if (item.id === messageId) item.important = on === true;
      return {done: true, already: result.already === true};
    }
    const words = MailBridge.IMPORTANT_REASONS[result.reason] || MailBridge.IMPORTANT_REASONS.failed;
    throw MailBridge.fail('important', words);
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
    const ids = MailBridge.ids(messageIds);
    // In Trash, Thunderbird deletes for good, with no way back. Voice Companion never does that
    // (a message may have been moved to Trash after it was listed elsewhere).
    for (const id of ids) {
      const header = await this.api.messages.get(id);
      const folder = header && header.folder;
      if (folder && ((folder.specialUse || []).includes('trash') || folder.type === 'trash')) {
        throw MailBridge.fail('trash', MailBridge.ALREADY_IN_TRASH);
      }
    }
    // skipTrash false: Thunderbird moves the messages to the account's Trash.
    await this.api.messages.delete(ids, false);
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

  // ---- Attachments -----------------------------------------------------------------

  // A part the person would call an attachment. Left out: pictures that are only part of the
  // message's look (Thunderbird names an unnamed part "Part 1.2"), and attachments deleted earlier.
  static isFile(item) {
    if (!item || typeof item.partName !== 'string' || !item.partName) return false;
    const type = String(item.contentType || '').toLowerCase();
    if (type === 'text/x-moz-deleted') return false;
    const headers = item.headers || {};
    const disposition = String((headers['content-disposition'] || [''])[0] || '').toLowerCase();
    const name = String(item.name || '').trim();
    const named = name && !/^part \d+(\.\d+)*$/i.test(name);
    return Boolean(named || /^\s*attachment/.test(disposition) || type === 'message/rfc822');
  }

  static fileRows(items) {
    return (items || []).filter(MailBridge.isFile).map(item => ({
      partName: item.partName,
      name: String(item.name || '').trim() || (String(item.contentType || '').toLowerCase() === 'message/rfc822' ? 'Attached message.eml' : 'Attachment ' + item.partName),
      size: Number.isInteger(item.size) && item.size >= 0 ? item.size : null,
      contentType: String(item.contentType || '')
    }));
  }

  // The message's files, or null when this Thunderbird cannot list attachments.
  async listFiles(messageId) {
    if (!this.api.messages.listAttachments) return null;
    return MailBridge.fileRows(await this.api.messages.listAttachments(messageId));
  }

  async attachments({messageId}) {
    if (!Number.isInteger(messageId)) throw MailBridge.fail('message', 'That message is not available.');
    let rows;
    try { rows = await this.listFiles(messageId); } catch (error) {
      throw MailBridge.fail('attachment', 'Thunderbird could not list the attachments of this message.');
    }
    if (rows === null) throw MailBridge.fail('attachment', 'This version of Thunderbird cannot list attachments.');
    return rows;
  }

  static toBase64(bytes) {
    let text = '';
    for (let i = 0; i < bytes.length; i += 0x8000) text += String.fromCharCode.apply(null, bytes.subarray(i, i + 0x8000));
    return btoa(text);
  }

  static fromBase64(text) {
    if (typeof text !== 'string' || /[^A-Za-z0-9+/=]/.test(text)) throw MailBridge.fail('upload', 'The file did not reach Thunderbird whole. Try again.');
    let binary;
    try { binary = atob(text); } catch (error) { throw MailBridge.fail('upload', 'The file did not reach Thunderbird whole. Try again.'); }
    const bytes = new Uint8Array(binary.length);
    for (let i = 0; i < binary.length; i++) bytes[i] = binary.charCodeAt(i);
    return bytes;
  }

  // One piece of an attachment, so a large file never passes in one message.
  async attachmentChunk({messageId, partName, offset = 0, length = MailBridge.CHUNK}) {
    if (!Number.isInteger(messageId) || typeof partName !== 'string' || !partName) throw MailBridge.fail('message', 'That attachment is not available.');
    if (!Number.isInteger(offset) || offset < 0 || !Number.isInteger(length) || length < 1 || length > MailBridge.CHUNK) {
      throw MailBridge.fail('attachment', 'Invalid attachment piece.');
    }
    const key = messageId + ':' + partName;
    let file = this.attachmentCache && this.attachmentCache.key === key ? this.attachmentCache.file : null;
    if (!file) {
      try { file = await this.api.messages.getAttachmentFile(messageId, partName); } catch (error) { file = null; }
      if (!file || typeof file.slice !== 'function') {
        this.attachmentCache = null;
        throw MailBridge.fail('attachment', 'Thunderbird could not read this attachment. It may be damaged.');
      }
      this.attachmentCache = {key, file};
    }
    const piece = new Uint8Array(await file.slice(offset, offset + length).arrayBuffer());
    const done = offset + piece.length >= file.size;
    if (done) this.attachmentCache = null;
    return {data: MailBridge.toBase64(piece), size: file.size, done};
  }

  // A file to send arrives in pieces. Unfinished ones are dropped after a while.
  uploadStart({name, size}) {
    if (typeof name !== 'string' || !name.trim() || name.length > 255 || /[\\/\x00-\x1f]/.test(name)) throw MailBridge.fail('upload', 'That file name cannot be attached.');
    if (!Number.isInteger(size) || size < 0 || size > MailBridge.MAX_UPLOAD) throw MailBridge.fail('upload', 'That file is too large to attach.');
    const now = this.clock();
    for (const [id, upload] of this.uploads) if (now - upload.at > MailBridge.UPLOAD_KEEP_MS) this.uploads.delete(id);
    const uploadId = 'upload' + (++this.uploadSerial);
    this.uploads.set(uploadId, {name: name.trim(), size, received: 0, chunks: [], at: now});
    return {uploadId};
  }

  uploadChunk({uploadId, offset, data}) {
    const upload = this.uploads.get(uploadId);
    if (!upload) throw MailBridge.fail('upload', 'The file did not reach Thunderbird. Try again.');
    if (offset !== upload.received) throw MailBridge.fail('upload', 'The file did not reach Thunderbird whole. Try again.');
    const bytes = MailBridge.fromBase64(data);
    if (upload.received + bytes.length > upload.size) throw MailBridge.fail('upload', 'The file did not reach Thunderbird whole. Try again.');
    upload.chunks.push(bytes); upload.received += bytes.length; upload.at = this.clock();
    return {received: upload.received};
  }

  uploadCancel({uploadId}) {
    this.uploads.delete(uploadId);
    return true;
  }

  // The finished File for each upload id, in order.
  uploadedFiles(ids) {
    if (ids === undefined || ids === null) return [];
    if (!Array.isArray(ids) || !ids.every(id => typeof id === 'string')) throw MailBridge.fail('upload', 'The attachments did not reach Thunderbird. Try again.');
    return ids.map(id => {
      const upload = this.uploads.get(id);
      if (!upload || upload.received !== upload.size) throw MailBridge.fail('upload', 'The attachments did not reach Thunderbird whole. Nothing was sent. Try again.');
      return new File(upload.chunks, upload.name);
    });
  }

  // Attach the files, and on a forward, the original's attachments, less the ones the person
  // took off. Then check that every one is there before anything is sent.
  async attachFiles(tab, files, forward) {
    const compose = this.api.compose;
    let expected = files.length;
    if (forward && this.api.messages.listAttachments) {
      const originals = await this.listFiles(forward.messageId) || [];
      const drop = Array.isArray(forward.drop) ? forward.drop.map(String) : [];
      // Thunderbird usually brings the original's files along; add any it left out.
      const present = compose.listAttachments ? (await compose.listAttachments(tab.id)).map(item => item.name) : [];
      for (const original of originals) {
        const at = present.indexOf(original.name);
        if (at >= 0) { present.splice(at, 1); continue; }
        const file = await this.api.messages.getAttachmentFile(forward.messageId, original.partName);
        await compose.addAttachment(tab.id, {file, name: original.name});
      }
      let kept = originals.length;
      // dropParts names each file taken off by its part, so two files with the same name, or
      // a name Voice Companion had to make safe for Windows, still take off the right one.
      const parts = Array.isArray(forward.dropParts) ? forward.dropParts.map(String) : null;
      const unwanted = parts ? originals.filter(original => parts.includes(original.partName))
        : drop.map(name => originals.find(original => original.name === name)).filter(Boolean);
      const removed = new Set();
      for (const original of unwanted) {
        const listed = compose.listAttachments ? await compose.listAttachments(tab.id) : [];
        const free = listed.filter(item => item.name === original.name && !removed.has(item.id));
        const found = free.find(item => original.size !== null && item.size === original.size) || free[0];
        if (!found) {
          // Never send a file the person took off.
          throw MailBridge.fail('attach', 'Thunderbird could not take ' + original.name + ' off the forward, so nothing was sent.');
        }
        await compose.removeAttachment(tab.id, found.id);
        removed.add(found.id);
        kept--;
      }
      expected += kept;
    }
    for (const file of files) await compose.addAttachment(tab.id, {file, name: file.name});
    if ((files.length || forward) && compose.listAttachments) {
      const count = (await compose.listAttachments(tab.id)).length;
      if (count < expected) throw MailBridge.fail('attach', 'Thunderbird did not add every attachment, so nothing was sent.');
    }
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
      addressFilled: false, choices: [], noteShapes: []};
    const setup = this.setup();
    if (!setup) return closed;
    try {
      const page = await setup.setupPage();
      return {open: Boolean(page.open), title: String(page.title || ''), notes: (page.notes || []).map(String).slice(0, 5),
        button: String(page.button || ''), canContinue: Boolean(page.canContinue), needsPassword: Boolean(page.needsPassword),
        fields: (page.fields || []).map(String).slice(0, 12), addressFilled: Boolean(page.addressFilled),
        // How the account connects, when Thunderbird offers a choice (Microsoft Graph, Exchange, IMAP).
        choices: (page.choices || []).slice(0, 6).map(choice => ({label: String(choice && choice.label || '').slice(0, 80),
          checked: Boolean(choice && choice.checked)})),
        // How each notice is built (element kinds only), for error reports.
        noteShapes: (page.noteShapes || []).slice(0, 5).map(item => String(item).slice(0, 400))};
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

  // "Hidden from the taskbar" in Voice Companion: questions Thunderbird asks stay in reach.
  async background({on = false}) {
    const setup = this.api.voiceCompanionSetup;
    if (!setup || typeof setup.keepPromptsInView !== 'function') return {watching: false};
    try { return {watching: Boolean((await setup.keepPromptsInView(Boolean(on))).watching)}; } catch (error) { return {watching: false}; }
  }

  static innerBody(html) {
    const match = /<body[^>]*>([\s\S]*)<\/body>/i.exec(html || '');
    return match ? match[1] : (html || '');
  }

  // The identities that send as this address, the saved one first. More than one means the
  // account was added to Thunderbird twice; Voice Companion may have saved the older one
  // (Will, 0.2.163: sending kept using an old outgoing server Thunderbird itself no longer used).
  async sendingIdentities(identityId, email) {
    const accounts = await this.api.accounts.list(false);
    const all = [];
    for (const account of accounts) for (const identity of account.identities || []) all.push({account, identity});
    const saved = all.find(entry => entry.identity.id === identityId);
    const address = String(email || (saved && saved.identity.email) || '').toLowerCase();
    const same = all.filter(entry => address && String(entry.identity.email || '').toLowerCase() === address);
    if (saved && !same.includes(saved)) same.unshift(saved);
    if (!same.length && saved) same.push(saved);
    // Saved first, then the newest account (the one most recently set up), then the rest.
    const number = entry => parseInt(String(entry.account.id).replace(/\D+/g, ''), 10) || 0;
    return same.sort((a, b) => (b === saved) - (a === saved) || number(b) - number(a));
  }

  async send(params) {
    const {identityId, attachments} = params;
    if (typeof identityId !== 'string' || !identityId) throw MailBridge.fail('identity', 'Choose a Thunderbird account first.');
    try {
      // Files Voice Companion sent ahead (uploadStart, uploadChunk), attached before sending.
      return await this.sendWith(params, this.uploadedFiles(attachments));
    } finally {
      for (const id of Array.isArray(attachments) ? attachments : []) this.uploads.delete(id);
    }
  }

  async sendWith(params, files) {
    const {identityId, email = '', to = [], cc = [], bcc = [], subject = '', html = '', plain = '', plainOnly = false, reply, forward} = params;
    const priority = MailBridge.PRIORITIES.includes(params.priority) ? params.priority : '';
    const returnReceipt = params.returnReceipt === true;
    const choices = await this.sendingIdentities(identityId, email);
    if (!choices.length) {
      throw MailBridge.fail('identity', 'This address is no longer in Thunderbird. Say add Thunderbird account to set it up again.');
    }
    let failure = null, spare = null;
    for (let index = 0; index < choices.length; index++) {
      const {account, identity} = choices[index];
      const tab = await this.compose(identity.id, {to, cc, bcc, subject, html, plain, plainOnly, reply, forward, priority, returnReceipt});
      try {
        await this.attachFiles(tab, files, forward);
      } catch (error) {
        const copy = await this.saveDraftAndClose(tab);
        if (error && error.safeMessage) {
          throw MailBridge.fail(error.code, error.safeMessage + (copy ? ' Your message is saved in Drafts in Thunderbird.' : ''));
        }
        throw MailBridge.fail('attach', 'Thunderbird could not attach the files, so nothing was sent.' +
          (copy ? ' Your message is saved in Drafts in Thunderbird.' : ''));
      }
      try {
        const sent = await this.api.compose.sendMessage(tab.id, {mode: 'sendNow'});
        // Sent. Thunderbird usually closes its compose window now, but a window can stay
        // open (Will, Office 365: "Write: testing" was left behind). Close it if it is.
        await this.closeCompose(tab);
        // The safety copy kept from a failed try is not needed now.
        if (spare) { try { await this.api.messages.delete(spare, false); } catch (error) { /* stays in Drafts */ } }
        const answer = {headerMessageId: sent && sent.headerMessageId || '', identityId: identity.id, accountId: account.id,
          tried: index + 1};
        // High importance: the people it went to see it from the message itself. Gmail ignores
        // that for its own copy, so its Sent Mail copy also gets Gmail's Important marker.
        if ((priority === 'high' || priority === 'highest') && answer.headerMessageId) {
          const marked = await this.sentImportant(account.id, answer.headerMessageId);
          if (marked !== null) answer.sentImportant = marked;
        }
        return answer;
      } catch (error) {
        failure = error;
        const copy = await this.saveDraftAndClose(tab);
        // Nothing was sent when the server could not be reached, so another identity for the
        // same address may be tried once. A sign-in or other refusal is not retried.
        const reach = /timed out|timeout|refused|could not connect|unable to connect|connection|network/i.test(String(error && error.message || ''));
        if (!(reach && index + 1 < choices.length && copy)) {
          throw MailBridge.fail('send', MailBridge.sendFailure(failure, account.type) + await this.serverWords(identity.id) + ' ' + (copy
            ? 'Your message is saved in Drafts in Thunderbird.'
            : 'Your message is still open in Thunderbird.'));
        }
        spare = copy;
      }
    }
    throw MailBridge.fail('send', MailBridge.sendFailure(failure, choices.length ? choices[choices.length - 1].account.type : ''));
  }

  // 'done', 'failed' or 'pending' for a Gmail account (waits up to SENT_IMPORTANT_MS; Gmail's Sent
  // copy usually arrives within seconds, and the marking goes on after that), null for others.
  async sentImportant(accountId, headerMessageId) {
    const marks = this.api.voiceCompanionMarks;
    if (!marks || typeof marks.sentImportant !== 'function') return null;
    const work = Promise.resolve(marks.sentImportant(accountId, headerMessageId)).catch(() => ({done: false, reason: 'failed'}));
    let timer = null;
    const slow = new Promise(resolve => { timer = setTimeout(() => resolve('slow'), MailBridge.SENT_IMPORTANT_MS); });
    const result = await Promise.race([work, slow]);
    clearTimeout(timer);
    if (result === 'slow') return 'pending';   // still going: Gmail's copy was not there yet
    if (result && result.reason === 'notGmail') return null;
    return result && result.done ? 'done' : 'failed';
  }

  // A compose window for this identity, minimized and filled in.
  async compose(identityId, {to, cc, bcc, subject, html, plain, plainOnly, reply, forward, priority = '', returnReceipt = false}) {
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
    // Importance and a read receipt, as in Thunderbird's own Options menu while writing.
    // Left alone unless asked, so the account's own settings stay.
    if (priority) details.priority = priority;
    if (returnReceipt) details.returnReceipt = true;
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
    return tab;
  }

  // " It used smtp.office365.com, port 587, with STARTTLS." when Thunderbird can say.
  async serverWords(identityId) {
    const setup = this.api.voiceCompanionSetup;
    if (!setup || typeof setup.outgoingServer !== 'function') return '';
    try {
      const server = await setup.outgoingServer(identityId);
      if (!server || !server.host) return '';
      const security = {starttls: 'with STARTTLS', ssl: 'with SSL/TLS', none: 'with no encryption'}[server.security] || '';
      return ' It used ' + server.host + (server.port ? ', port ' + server.port : '') + (security ? ', ' + security : '') + '.';
    } catch (error) {
      return '';
    }
  }

  // Which Thunderbird this is: {version, channel, profile, composing}, or {} from an older Experiment.
  // Voice Companion uses it to update Thunderbird safely and to check the same profile came back.
  async about() {
    const setup = this.api.voiceCompanionSetup;
    if (!setup || typeof setup.about !== 'function') return {};
    try {
      const found = await setup.about() || {};
      return {version: String(found.version || ''), channel: String(found.channel || ''), profile: String(found.profile || ''),
        composing: Number.isInteger(found.composing) && found.composing > 0 ? found.composing : 0};
    } catch (error) {
      return {};
    }
  }

  // Voice Companion is closing and started Thunderbird: close Thunderbird too.
  async quit() {
    const setup = this.api.voiceCompanionSetup;
    if (!setup || typeof setup.quitThunderbird !== 'function') return {quit: false, reason: 'unavailable'};
    return setup.quitThunderbird();
  }

  // Save the message to Drafts and close its window. The saved message ids, or false.
  async saveDraftAndClose(tab) {
    let saved;
    try {
      saved = await this.api.compose.saveMessage(tab.id, {mode: 'draft'});
    } catch (error) {
      return false;
    }
    await this.closeCompose(tab);
    const ids = ((saved && saved.messages) || []).map(message => message.id).filter(id => id !== undefined);
    return ids.length ? ids : [];
  }

  // Why sending failed, in plain words, from Thunderbird's error text (never the text itself).
  // An account type without the "extension:" Thunderbird puts before types added by its own modules:
  // Thunderbird 159 lists a Microsoft Graph account as "extension:graph".
  static baseType(type) {
    return String(type || '').toLowerCase().replace(/^extension:/, '');
  }

  // type: the account's type. Microsoft Graph and Exchange ("ews") accounts do not send over SMTP.
  static sendFailure(error, type = '') {
    const text = String(error && error.message || '');
    const microsoft = MailBridge.MICROSOFT_TYPES.includes(MailBridge.baseType(type));
    // Microsoft 365 through Graph or Exchange: the organization must approve Thunderbird for its mailboxes.
    if (microsoft && /admin(istrator)? (approval|consent)|AADSTS65001|AADSTS90094|consent/i.test(text)) {
      return 'Your organization must approve Thunderbird for Microsoft 365. Ask your IT administrator to allow Thunderbird.';
    }
    // Microsoft 365: the organization has turned off sending from mail programs (SMTP AUTH),
    // or an administrator must approve Thunderbird. Only the organization can change this.
    if (!microsoft && /SmtpClientAuthentication|5\.7\.139|admin(istrator)? (approval|consent)|AADSTS65001|AADSTS90094/i.test(text)) {
      return 'Your organization does not allow sending from mail programs like Thunderbird. ' +
        'Ask your IT administrator to turn on Authenticated SMTP for your mailbox.';
    }
    if (/timed out|timeout/i.test(text)) {
      return 'Thunderbird could not reach your mail server to send. The connection timed out.';
    }
    if (/authenticat|password|login|log in|sign.?in|oauth|5\.7\.(?:3|57)/i.test(text)) {
      return 'Your mail server did not accept the sign-in for sending.';
    }
    if (/refused|could not connect|unable to connect|connection|offline|network/i.test(text)) {
      return 'Thunderbird could not connect to your mail server to send.';
    }
    return 'Thunderbird could not send this message.';
  }

  async closeCompose(tab) {
    if (!this.api.windows || tab.windowId === undefined) return;
    try {
      const open = await this.api.windows.get(tab.windowId);
      if (open) await this.api.windows.remove(tab.windowId);
    } catch (error) { /* already closed */ }
  }
}

MailBridge.MICROSOFT_TYPES = ['graph', 'ews'];   // Thunderbird's Microsoft Graph and Exchange accounts
MailBridge.MAIL_CHECK_MS = 15000;   // longest wait for Thunderbird's check for new mail
MailBridge.LIST_KEEP_MS = 120000;   // how long a folder's list is reused for its later pages
MailBridge.CHUNK = 1024 * 1024;              // largest piece of an attachment sent to Voice Companion
MailBridge.MAX_UPLOAD = 60 * 1024 * 1024;    // largest file Voice Companion may attach
MailBridge.UPLOAD_KEEP_MS = 10 * 60 * 1000;  // an unfinished file to send is dropped after this long
// Account types whose mail is not kept on a server that other devices read.
MailBridge.LOCAL_ACCOUNT_TYPES = ['pop3', 'none', 'rss'];
MailBridge.PRIORITIES = ['highest', 'high', 'normal', 'low', 'lowest'];
MailBridge.ALREADY_IN_TRASH = 'This is already in Trash. Deleting it there would remove it for good, so nothing was changed.';
MailBridge.SENT_IMPORTANT_MS = 12000;
MailBridge.RECEIPT_CHECK_BYTES = 5 * 1024 * 1024;   // largest message read whole to look for a receipt request
// Changes that update the kept list themselves, so it is kept.
MailBridge.KEEPS_LIST = ['mark', 'important', 'receiptAnswer'];
MailBridge.IMPORTANT_REASONS = {
  notGmail: 'Only Gmail accounts have an Important marker you can change. Others show the importance the sender chose.',
  noFolder: 'Gmail did not show its Important label to Thunderbird. In Gmail settings, under Labels, turn on Show in IMAP for Important, then try again.',
  inImportant: 'Open this message from your Inbox or another folder to change it.',
  offline: 'Thunderbird is offline, so Gmail could not be changed. Try again when you are online.',
  missing: 'Thunderbird could not find that message. It may have been moved or deleted. Say list messages to refresh.',
  failed: 'Gmail did not take the change. Check your internet connection, then try again.',
};
MailBridge.READ_ONLY = ['about', 'accounts', 'folders', 'inbox', 'folderInfo', 'list', 'read', 'context', 'setupPage', 'captureSignIn', 'takeSignIn', 'background',
  'attachments', 'attachmentChunk', 'uploadStart', 'uploadChunk', 'uploadCancel'];
MailBridge.METHODS = ['accounts', 'folders', 'inbox', 'folderInfo', 'list', 'read', 'context', 'move', 'trash',
  'createFolder', 'renameFolder', 'moveFolder', 'deleteFolder', 'mark', 'send', 'openAccountSetup', 'setupPage', 'setupContinue', 'fillPassword', 'showSetup',
  'captureSignIn', 'takeSignIn', 'minimize', 'background', 'quit', 'about', 'attachments', 'attachmentChunk', 'uploadStart', 'uploadChunk', 'uploadCancel',
  'important', 'receiptAnswer'];
globalThis.MailBridge = MailBridge;

function startConnection(api, timers = {setTimeout}) {
  const bridge = new MailBridge(api);
  let port = null;
  let delay = RETRY_FIRST_MS;
  const later = () => {
    timers.setTimeout(connect, delay);
    delay = Math.min(delay * 2, RETRY_MS);
  };
  const connect = () => {
    try {
      port = api.runtime.connectNative(HOST);
    } catch (error) {
      later();
      return;
    }
    const current = port;
    current.onMessage.addListener(async message => {
      delay = RETRY_FIRST_MS;   // Voice Companion is talking: the next break is tried again soon
      const response = await bridge.handle(message);
      try { current.postMessage(response); } catch (error) { /* reconnect below */ }
    });
    current.onDisconnect.addListener(() => { if (port === current) port = null; later(); });
    current.postMessage({protocol: PROTOCOL, command: 'mail', version: api.runtime.getManifest().version});
  };
  connect();
  return {bridge, port: () => port};
}

const thunderbird = globalThis.messenger || globalThis.browser;
if (thunderbird && thunderbird.runtime && thunderbird.runtime.connectNative && !globalThis.VOICE_COMPANION_TEST) {
  startConnection(thunderbird);
}
globalThis.startConnection = startConnection;

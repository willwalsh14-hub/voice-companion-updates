/* Voice Companion Mail Connection: marks that reach the mail server, and say so.
 *
 * Will, October 31, 2026: a read mark made in Voice Companion stayed in Thunderbird and
 * never reached Gmail. The add-on interface (messages.update) asks Thunderbird to send a
 * mark to the server but cannot tell whether the server took it. This small Experiment adds:
 *
 *  - mark: read or unread, flagged or not, then a second STORE to the IMAP server with a
 *    listener, so the server's answer is known. One retry. For Gmail, marking read can take
 *    in the whole Gmail conversation (same X-GM-THRID) in that folder, as Gmail's website does.
 *  - important / isImportant: Gmail's own "Important" marker. Gmail shows a label as an IMAP
 *    folder ([Gmail]/Important): copying a message there adds the marker, and deleting the
 *    copy there removes only the marker (the message stays in the Inbox and All Mail).
 *  - sentImportant: after sending with high importance, the Sent Mail copy gets the marker.
 *  - receipt / receiptAnswer: a read receipt (MDN) for a message that asks for one, through
 *    Thunderbird's own receipt sender, which follows the account's receipt settings.
 *
 * Keep this file thin: it can only run in real Thunderbird (tested on Windows by
 * check_thunderbird_marks_live.py). Every function catches its own errors and answers plainly.
 * Nothing is logged.
 */
'use strict';

var { ExtensionCommon } = ChromeUtils.importESModule('resource://gre/modules/ExtensionCommon.sys.mjs');
// An Experiment's scope has no window timers; Gecko's own timer module has them.
var { setTimeout, clearTimeout } = ChromeUtils.importESModule('resource://gre/modules/Timer.sys.mjs');

function mailServices() {
  return ChromeUtils.importESModule('resource:///modules/MailServices.sys.mjs').MailServices;
}

const READ = 0x0001, FLAGGED = 0x0004;              // nsMsgMessageFlags, and IMAP \Seen, \Flagged
const MDN_NEEDED = 0x00400000, MDN_SENT = 0x00800000;  // nsMsgMessageFlags.MDNReportNeeded / Sent
const SENT_FOLDER = 0x00000200;                      // nsMsgFolderFlags.SentMail
const SERVER_WAIT_MS = 30000, RETRY_WAIT_MS = 1500;
const CONVERSATION_LIMIT = 500, CONVERSATION_MS = 3000;
const IMPORTANT = /^\[(?:gmail|google mail)\]\/important$/i;

// Thunderbird reports success as 0 (NS_OK); failures have the top bit set.
const succeeded = status => (Number(status) >>> 0) < 0x80000000;
const wait = ms => new Promise(resolve => setTimeout(resolve, ms));

function imapFolder(folder) {
  try { return folder.QueryInterface(Ci.nsIMsgImapMailFolder); } catch (error) { return null; }
}

function isGmail(folder) {
  try { return Boolean(folder.server.QueryInterface(Ci.nsIImapIncomingServer).isGMailServer); } catch (error) { return false; }
}

// Resolves with the server's answer to one IMAP request, or null when it never answers.
function urlDone(start) {
  return new Promise(resolve => {
    let finished = false;
    const end = value => { if (!finished) { finished = true; resolve(value); } };
    const timer = setTimeout(() => end(null), SERVER_WAIT_MS);
    const listener = {
      QueryInterface: ChromeUtils.generateQI(['nsIUrlListener']),
      OnStartRunningUrl() {},
      OnStopRunningUrl(url, status) { clearTimeout(timer); end(succeeded(status)); },
    };
    try { start(listener); } catch (error) { clearTimeout(timer); end(false); }
  });
}

function copyDone(start) {
  return new Promise(resolve => {
    let finished = false;
    const end = value => { if (!finished) { finished = true; resolve(value); } };
    const timer = setTimeout(() => end(null), SERVER_WAIT_MS);
    const listener = {
      QueryInterface: ChromeUtils.generateQI(['nsIMsgCopyServiceListener']),
      onStartCopy() {}, onProgress() {}, setMessageKey() {}, getMessageId() { return ''; },
      onStopCopy(status) { clearTimeout(timer); end(succeeded(status)); },
      // Older spellings, for Thunderbird 128.
      OnStartCopy() {}, OnProgress() {}, SetMessageKey() {}, GetMessageId() { return ''; },
      OnStopCopy(status) { clearTimeout(timer); end(succeeded(status)); },
    };
    try { start(listener); } catch (error) { clearTimeout(timer); end(false); }
  });
}

// The other unread messages of the same Gmail conversation in this folder, newest first.
function conversation(folder, header) {
  const found = [];
  let thread = '';
  try { thread = header.getStringProperty('X-GM-THRID') || ''; } catch (error) { thread = ''; }
  if (!thread) return found;
  const until = Date.now() + CONVERSATION_MS;
  try {
    for (const other of folder.msgDatabase.reverseEnumerateMessages()) {
      if (Date.now() > until || found.length >= CONVERSATION_LIMIT) break;
      if (other.messageKey === header.messageKey || (other.flags & READ)) continue;
      if (other.getStringProperty('X-GM-THRID') === thread) found.push(other);
    }
  } catch (error) { /* the list could not be read: just this message */ }
  return found;
}

// Ask the IMAP server to store one flag, and wait for its answer. If it does not answer yes,
// try again; then drop Thunderbird's open connections to that server and try a third time on a
// fresh one. Will, October 31, 2026: a mark stayed in Thunderbird until Thunderbird was
// restarted; a fresh connection is what the restart gave.
async function storeOnServer(folder, flag, on, keys) {
  const imap = imapFolder(folder);
  if (!imap) return null;
  for (let attempt = 0; attempt < 3; attempt++) {
    const answer = await urlDone(listener => imap.storeImapFlags(flag, on, keys, listener));
    if (answer === true) return true;
    if (attempt === 1) {
      try { folder.server.closeCachedConnections(); } catch (error) { /* a new connection is made anyway */ }
    }
    if (attempt < 2) await wait(RETRY_WAIT_MS);
  }
  return false;
}

// server: "confirmed" (the IMAP server said yes), "failed" (it said no, or never answered),
// "offline" (Thunderbird is offline: it sends the mark when it is back), "local" (POP or Local
// Folders: there is no server copy), or "sent" (another kind, such as Microsoft Graph or
// Exchange: Thunderbird sends it, but cannot say whether the server took it).
async function mark(extension, ids, read, flagged, wholeConversation) {
  const answer = {changed: 0, missing: 0, conversation: 0, conversationIds: [], server: 'confirmed'};
  const groups = new Map();
  for (const id of Array.isArray(ids) ? ids : []) {
    let header = null;
    try { header = extension.messageManager.get(id); } catch (error) { header = null; }
    if (!header || !header.folder) { answer.missing++; continue; }
    const key = header.folder.URI;
    if (!groups.has(key)) groups.set(key, {folder: header.folder, headers: []});
    groups.get(key).headers.push(header);
  }
  const worst = ['confirmed', 'sent', 'local', 'offline', 'failed'];
  const note = state => { if (worst.indexOf(state) > worst.indexOf(answer.server)) answer.server = state; };
  for (const {folder, headers} of groups.values()) {
    let all = headers.slice();
    if (read === true && wholeConversation && isGmail(folder)) {
      const seen = new Set(all.map(item => item.messageKey));
      for (const header of headers) {
        for (const other of conversation(folder, header)) {
          if (!seen.has(other.messageKey)) {
            seen.add(other.messageKey); all.push(other); answer.conversation++;
            // The add-on's own number for it, so Voice Companion's list says read too.
            try { const known = extension.messageManager.convert(other); if (known && Number.isInteger(known.id)) answer.conversationIds.push(known.id); } catch (error) { /* left as it was in the list */ }
          }
        }
      }
    }
    try {
      if (typeof read === 'boolean') folder.markMessagesRead(all, read);
      if (typeof flagged === 'boolean') folder.markMessagesFlagged(headers, flagged);
    } catch (error) { answer.missing += headers.length; continue; }
    answer.changed += headers.length;
    const type = String(folder.server && folder.server.type || '');
    if (type === 'none' || type === 'pop3' || type === 'rss') { note('local'); continue; }
    if (Services.io.offline) { note('offline'); continue; }
    if (!imapFolder(folder)) {
      // Microsoft Graph and Exchange: Thunderbird sends the change itself and gives no answer for
      // it. Syncing the folder right after shows whether the server can be reached at all; if it
      // cannot, the change has not reached it, and that is said.
      note(await refresh(folder) === true ? 'sent' : 'failed');
      continue;
    }
    let ok = true;
    if (typeof read === 'boolean') ok = await storeOnServer(folder, READ, read, all.map(item => item.messageKey));
    if (ok && typeof flagged === 'boolean') ok = await storeOnServer(folder, FLAGGED, flagged, headers.map(item => item.messageKey));
    if (!ok) note('failed');
  }
  if (!answer.changed) answer.server = 'none';
  return answer;
}

// ---- Gmail's Important marker ---------------------------------------------------------

function importantFolder(server) {
  const root = server && server.rootFolder;
  if (!root) return null;
  for (const folder of root.descendants || []) {
    const imap = imapFolder(folder);
    let name = '';
    try { name = imap ? String(imap.onlineName || '') : ''; } catch (error) { name = ''; }
    if (IMPORTANT.test(name)) return folder;
  }
  return null;
}

function copyIn(folder, messageId) {
  try {
    const db = folder.msgDatabase;
    return messageId && db ? db.getMsgHdrForMessageID(messageId) : null;
  } catch (error) {
    return null;
  }
}

async function refresh(folder) {
  return urlDone(listener => folder.updateFolderWithListener(null, listener));
}

// done: true when Gmail took the change. reason: "notGmail", "noFolder" (the Important label is
// hidden from IMAP in Gmail's settings), "inImportant" (open the message from another folder),
// "offline", "failed", "missing".
async function important(extension, id, on) {
  let header = null;
  try { header = extension.messageManager.get(id); } catch (error) { header = null; }
  if (!header || !header.folder) return {done: false, reason: 'missing'};
  if (!isGmail(header.folder)) return {done: false, reason: 'notGmail'};
  if (Services.io.offline) return {done: false, reason: 'offline'};
  const target = importantFolder(header.folder.server);
  if (!target) return {done: false, reason: 'noFolder'};
  if (target.URI === header.folder.URI) return {done: false, reason: 'inImportant'};
  await refresh(target);
  const copy = copyIn(target, header.messageId);
  if (on) {
    if (copy) return {done: true, already: true};
    const ok = await copyDone(listener =>
      mailServices().copy.copyMessages(header.folder, [header], target, false, listener, null, false));
    return ok ? {done: true} : {done: false, reason: 'failed'};
  }
  if (!copy) return {done: true, already: true};
  // Only the copy in the Important folder is removed. The message itself stays where it is
  // (it was found in another folder above), so Gmail only takes the marker off.
  const imap = imapFolder(target);
  const ok = imap && await storeOnServer(target, 0x0008, true, [copy.messageKey]);   // IMAP \Deleted
  if (!ok) return {done: false, reason: 'failed'};
  await urlDone(listener => imap.expunge(listener, null));
  return {done: true};
}

async function isImportant(extension, ids) {
  const found = {};
  const folders = new Map();
  for (const id of Array.isArray(ids) ? ids.slice(0, 200) : []) {
    try {
      const header = extension.messageManager.get(id);
      if (!header || !header.folder || !isGmail(header.folder)) continue;
      const server = header.folder.server;
      if (!folders.has(server.key)) folders.set(server.key, importantFolder(server));
      const target = folders.get(server.key);
      if (target) found[String(id)] = target.URI === header.folder.URI || Boolean(copyIn(target, header.messageId));
    } catch (error) { /* left out: nothing is said about it */ }
  }
  return found;
}

// After a message sent with high importance: Gmail keeps its own copy in Sent Mail. Wait for it,
// then add the Important marker, so the phone and the Gmail website show it.
async function sentImportant(accountId, headerMessageId) {
  const account = mailServices().accounts.getAccount(String(accountId || ''));
  const server = account && account.incomingServer;
  const root = server && server.rootFolder;
  if (!root) return {done: false, reason: 'missing'};
  let gmail = false;
  try { gmail = server.QueryInterface(Ci.nsIImapIncomingServer).isGMailServer; } catch (error) { gmail = false; }
  if (!gmail) return {done: false, reason: 'notGmail'};
  const sent = root.getFolderWithFlags(SENT_FOLDER);
  const target = importantFolder(server);
  if (!sent || !target) return {done: false, reason: 'noFolder'};
  const messageId = String(headerMessageId || '').replace(/^<|>$/g, '');
  for (let attempt = 0; attempt < 6; attempt++) {
    await refresh(sent);
    const header = copyIn(sent, messageId);
    if (header) {
      await refresh(target);
      if (copyIn(target, messageId)) return {done: true, already: true};
      const ok = await copyDone(listener =>
        mailServices().copy.copyMessages(sent, [header], target, false, listener, null, false));
      return ok ? {done: true} : {done: false, reason: 'failed'};
    }
    await wait(2500);
  }
  return {done: false, reason: 'notFound'};
}

// ---- Read receipts ----------------------------------------------------------------------

const pendingReceipts = new Map();

// As Thunderbird's own message view (msgHdrView.js, HandleMDNResponse): never for junk, never
// twice. "yes" when Thunderbird saw the request in the header, "maybe" when only the full
// header can say (the add-on then reads it), "" when no receipt can be due. Changes nothing.
function receiptDue(header) {
  if (!header || !header.folder || (header.flags & MDN_SENT)) return '';
  try { if (header.folder.isSpecialFolder(Ci.nsMsgFolderFlags.Newsgroup, false)) return ''; } catch (error) { /* not a newsgroup */ }
  let junk = '';
  try { junk = header.getStringProperty('junkscore') || ''; } catch (error) { junk = ''; }
  if (!['', '0'].includes(junk)) return '';
  return (header.flags & MDN_NEEDED) ? 'yes' : 'maybe';
}

function needsReceipt(extension, id) {
  try { return receiptDue(extension.messageManager.get(id)); } catch (error) { return ''; }
}

// state: "none" (no receipt asked for, or already handled), "ask" (Thunderbird's settings say
// to ask: answer with receiptAnswer), "handled" (Thunderbird sent it, or did not, by its settings).
function receipt(extension, id, headersText) {
  let header = null;
  try { header = extension.messageManager.get(id); } catch (error) { header = null; }
  if (!receiptDue(header)) return {state: 'none'};
  try {
    const headers = Cc['@mozilla.org/messenger/mimeheaders;1'].createInstance(Ci.nsIMimeHeaders);
    headers.initialize(String(headersText || ''));
    if (!headers.extractHeader('Disposition-Notification-To', false) && !headers.extractHeader('Return-Receipt-To', false)) {
      return {state: 'none'};
    }
    const generator = Cc['@mozilla.org/messenger-mdn/generator;1'].createInstance(Ci.nsIMsgMdnGenerator);
    const win = Services.wm.getMostRecentWindow('mail:3pane');
    const msgWindow = (win && win.msgWindow) || null;
    const ask = generator.process(Ci.nsIMsgMdnGenerator.eDisplayed, msgWindow, header.folder,
      header.messageKey, headers, false);
    if (ask) {
      pendingReceipts.set(id, generator);
      return {state: 'ask'};
    }
    return {state: 'handled'};
  } catch (error) {
    return {state: 'none', error: true};
  }
}

function receiptAnswer(id, send) {
  const generator = pendingReceipts.get(id);
  if (!generator) return {done: false};
  pendingReceipts.delete(id);
  try {
    if (send) generator.userAgreed(); else generator.userDeclined();
    return {done: true};
  } catch (error) {
    return {done: false};
  }
}

var voiceCompanionMarks = class extends ExtensionCommon.ExtensionAPI {
  onShutdown() {
    pendingReceipts.clear();
  }

  getAPI(context) {
    const extension = context.extension;
    return {
      voiceCompanionMarks: {
        async mark(ids, read, flagged, wholeConversation) {
          try {
            return await mark(extension, ids, typeof read === 'boolean' ? read : null,
              typeof flagged === 'boolean' ? flagged : null, wholeConversation === true);
          } catch (error) {
            return {changed: 0, missing: Array.isArray(ids) ? ids.length : 0, conversation: 0, server: 'none'};
          }
        },
        async important(id, on) {
          try { return await important(extension, id, on === true); } catch (error) { return {done: false, reason: 'failed'}; }
        },
        async isImportant(ids) {
          try { return await isImportant(extension, ids); } catch (error) { return {}; }
        },
        async sentImportant(accountId, headerMessageId) {
          try { return await sentImportant(accountId, headerMessageId); } catch (error) { return {done: false, reason: 'failed'}; }
        },
        async needsReceipt(id) {
          return needsReceipt(extension, id);
        },
        async receipt(id, headersText) {
          return receipt(extension, id, headersText);
        },
        async receiptAnswer(id, send) {
          return receiptAnswer(id, send === true);
        },
      },
    };
  }
};

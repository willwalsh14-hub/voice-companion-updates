// Test-only stand-in for Thunderbird's add-on interface (messenger.*).
// Not packaged into the add-on. Behaves like Thunderbird 128 for the calls the
// Voice Companion Mail Connection uses, and records what would be sent.
'use strict';

function createFakeMessenger() {
  const state = {
    accounts: [
      {id: 'account1', name: 'Gmail', type: 'imap', rootFolder: {id: 'account1://', isRoot: true, path: '/'},
       identities: [{id: 'id1', email: 'me@gmail.com', name: 'Me'}]},
      {id: 'account2', name: 'Local Folders', type: 'none', identities: []},
    ],
    folders: [
      {id: 'account1://', accountId: 'account1', path: '/', name: 'Root', isRoot: true, specialUse: []},
      {id: 'account1://INBOX', accountId: 'account1', path: '/INBOX', name: 'Inbox', specialUse: ['inbox']},
      {id: 'account1://[Gmail]/Sent Mail', accountId: 'account1', path: '/[Gmail]/Sent Mail', name: 'Sent Mail', specialUse: ['sent']},
      {id: 'account1://Receipts', accountId: 'account1', path: '/Receipts', name: 'Receipts', specialUse: []},
      {id: 'account1://Trash', accountId: 'account1', path: '/Trash', name: 'Trash', specialUse: ['trash']},
    ],
    messages: [],
    composed: [], sent: [], minimized: [], moved: [], deleted: [],
    failSend: false,
    // Thunderbird's own "add email account" page (the Experiment): what it was asked,
    // and, like a person finishing that page, the account it adds a moment later.
    setupOpened: [], setupCalls: [], setupResult: {opened: true, prefilled: true}, addAfterSetupMs: null,
    setupPage: {open: true, title: 'Set up your existing email address', notes: [], button: 'Continue', canContinue: true, needsPassword: false},
    continued: 0, passwordLengths: [], shown: 0, capture: [], signIn: null,
    windows: [{id: 1, type: 'normal', state: 'normal'}, {id: 2, type: 'popup', state: 'normal'}],
  };
  let nextAccount = 3;
  state.addAccount = email => {
    const id = 'account' + nextAccount++;
    state.accounts.push({id, name: email, type: 'imap', rootFolder: {id: id + '://', isRoot: true, path: '/'},
      identities: [{id: 'id' + id, email, name: ''}]});
    state.folders.push({id: id + '://INBOX', accountId: id, path: '/INBOX', name: 'Inbox', specialUse: ['inbox']});
  };
  for (let i = 1; i <= 25; i++) {
    state.messages.push({
      id: i, folderId: 'account1://INBOX', date: new Date(Date.UTC(2026, 9, i, 9)), author: `Sender ${i} <sender${i}@example.com>`,
      subject: `Message ${i}`, read: i % 2 === 0, size: 1000 + i, recipients: ['me@gmail.com'], ccList: i === 25 ? ['other@example.com'] : [],
      headerMessageId: `msg${i}@example.com`, html: i === 25 ? '<html><head><title>x</title></head><body><p>Latest <b>news</b></p></body></html>' : '',
      plain: `Plain text of message ${i}`, replyTo: i === 25 ? 'Replies <replies@example.com>' : '',
    });
  }
  const folder = id => state.folders.find(f => f.id === id) || null;
  const header = m => ({id: m.id, date: m.date, author: m.author, subject: m.subject, read: m.read, size: m.size,
    recipients: m.recipients, ccList: m.ccList, headerMessageId: m.headerMessageId, folder: folder(m.folderId)});
  const lists = new Map();
  let nextList = 1, nextTab = 1;
  const page = (items, size) => {
    const first = items.slice(0, size), rest = items.slice(size);
    let id = null;
    if (rest.length) { id = 'list' + nextList++; lists.set(id, {rest, size}); }
    return {id, messages: first};
  };
  const tabs = new Map();
  const api = {
    accounts: {
      list: async () => state.accounts.map(a => ({...a})),
      get: async id => state.accounts.find(a => a.id === id) || null,
    },
    folders: {
      get: async id => folder(id),
      query: async ({accountId, specialUse}) => state.folders.filter(f => f.accountId === accountId &&
        (!specialUse || specialUse.every(use => f.specialUse.includes(use)))),
      getFolderInfo: async f => ({totalMessageCount: state.messages.filter(m => m.folderId === f.id).length,
        unreadMessageCount: state.messages.filter(m => m.folderId === f.id && !m.read).length}),
      create: async (parent, name) => {
        const created = {id: parent.id.replace(/\/$/, '') + '/' + name, accountId: 'account1', path: (parent.path === '/' ? '' : parent.path) + '/' + name, name, specialUse: []};
        state.folders.push(created); return created;
      },
      rename: async (f, name) => { f.name = name; f.path = f.path.replace(/[^/]+$/, name); f.id = f.id.replace(/[^/]+$/, name); return f; },
      move: async (f, destination) => { f.path = destination.path + '/' + f.name; f.id = destination.id + '/' + f.name; return f; },
      delete: async f => { state.folders = state.folders.filter(x => x !== f); },
    },
    messages: {
      // Thunderbird returns folder order (oldest first here); the add-on sorts.
      query: async ({folderId, messagesPerPage}) => page(state.messages.filter(m => m.folderId === folderId).map(header), messagesPerPage),
      continueList: async id => { const list = lists.get(id); lists.delete(id); return page(list.rest, list.size); },
      get: async id => header(state.messages.find(m => m.id === id)),
      getFull: async id => {
        const m = state.messages.find(x => x.id === id);
        return {headers: m.replyTo ? {'reply-to': [m.replyTo]} : {}, contentType: 'multipart/alternative',
          parts: [{contentType: 'text/plain', body: m.plain}, ...(m.html ? [{contentType: 'text/html', body: m.html}] : [])]};
      },
      listInlineTextParts: async id => {
        const m = state.messages.find(x => x.id === id);
        return [{contentType: 'text/plain', content: m.plain}, ...(m.html ? [{contentType: 'text/html', content: m.html}] : [])];
      },
      move: async (ids, destination) => {
        if (!destination || !destination.id) throw new Error('Invalid folder');
        for (const id of ids) state.messages.find(m => m.id === id).folderId = destination.id;
        state.moved.push({ids, folderId: destination.id});
      },
      delete: async (ids, skipTrash) => {
        state.deleted.push({ids, skipTrash});
        for (const id of ids) state.messages.find(m => m.id === id).folderId = 'account1://Trash';
      },
    },
    compose: {
      beginNew: async (messageId, details) => newTab({kind: 'new', details}),
      beginReply: async (messageId, replyType, details) => newTab({kind: 'reply', messageId, replyType, details,
        body: '<html><body><blockquote>Original message</blockquote></body></html>', plainTextBody: '> Original message'}),
      beginForward: async (messageId, forwardType, details) => newTab({kind: 'forward', messageId, forwardType, details,
        body: '<html><body><div>Forwarded original</div></body></html>', plainTextBody: 'Forwarded original'}),
      getComposeDetails: async tabId => ({...tabs.get(tabId).current}),
      setComposeDetails: async (tabId, details) => { Object.assign(tabs.get(tabId).current, details); },
      sendMessage: async (tabId, options) => {
        if (state.failSend) throw new Error('SMTP refused sam@example.com');
        const tab = tabs.get(tabId);
        state.sent.push({...tab, options});
        return {mode: 'sendNow', headerMessageId: 'sent' + tabId + '@thunderbird.test', messages: []};
      },
    },
    windows: {
      update: async (windowId, info) => {
        state.minimized.push({windowId, info});
        const win = state.windows.find(w => w.id === windowId);
        if (win && info.state) win.state = info.state;
      },
      getAll: async ({windowTypes} = {}) => state.windows.filter(w => !windowTypes || windowTypes.includes(w.type)).map(w => ({...w})),
    },
    voiceCompanionSetup: {
      openAccountSetup: async (email, name, show) => {
        state.setupOpened.push(email); state.setupCalls.push({email, name, show});
        // Google and Microsoft sign in through the web browser (Thunderbird 151 and newer).
        if (/@(gmail|outlook|hotmail)\.com$/.test(email)) {
          state.signIn = {url: 'https://accounts.google.com/o/oauth2/auth?login_hint=' + encodeURIComponent(email) +
            '&redirect_uri=http%3A%2F%2F127.0.0.1%3A49152', where: 'browser'};
        }
        if (state.addAfterSetupMs !== null && email) setTimeout(() => state.addAccount(email), state.addAfterSetupMs);
        return {...state.setupResult};
      },
      setupPage: async () => ({...state.setupPage}),
      setupContinue: async () => { state.continued++; return {pressed: state.setupPage.canContinue}; },
      // Records only that a password arrived and how long it was: tests must never print it.
      fillPassword: async secret => { state.passwordLengths.push(secret.length); return {filled: state.setupPage.needsPassword}; },
      showSetup: async () => { state.shown++; return {shown: true}; },
      captureSignIn: async seconds => { state.capture.push(seconds); return {capturing: seconds > 0}; },
      takeSignIn: async () => { const found = state.signIn || {url: '', where: ''}; state.signIn = null; return found; },
    },
    runtime: {getManifest: () => ({version: require('./manifest.json').version})},
  };
  function newTab(info) {
    const id = nextTab++;
    const tab = {id, windowId: 100 + id, ...info, current: {isPlainText: false, body: info.body || '', plainTextBody: info.plainTextBody || '', ...(info.details || {})}};
    tabs.set(id, tab); state.composed.push(tab);
    return {id, windowId: tab.windowId};
  }
  return {api, state};
}

module.exports = {createFakeMessenger};

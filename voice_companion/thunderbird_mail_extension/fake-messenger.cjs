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
    composed: [], sent: [], minimized: [], moved: [], deleted: [], openCompose: [], closedByAddon: [],
    failSend: false,
    // Thunderbird's own "add email account" page (the Experiment): what it was asked,
    // and, like a person finishing that page, the account it adds a moment later.
    setupOpened: [], setupCalls: [], setupResult: {opened: true, prefilled: true}, addAfterSetupMs: null,
    setupPage: {open: true, title: 'Set up your existing email address', notes: [], button: 'Continue', canContinue: true, needsPassword: false},
    continued: 0, passwordLengths: [], shown: 0, capture: [], signIn: null,
    windows: [{id: 1, type: 'normal', state: 'normal'}, {id: 2, type: 'popup', state: 'normal'}],
    // Mail that has reached the server but not Thunderbird yet. Thunderbird only shows it after
    // it checks the server (getNewMail in the Experiment). mailCheck: 'ok', 'fail', 'offline' or 'hang'.
    server: [], mailCheck: 'ok', mailChecks: [],
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
      attachments: [],
    });
  }
  // Message 24 has two files, an inline picture without a name (part of its look), and an
  // attachment deleted earlier. Thunderbird 128 lists all of them; only two are files.
  const bytes = (text, repeat = 1) => new Uint8Array(Buffer.from(text.repeat(repeat), 'utf8'));
  state.messages[23].attachments = [
    {partName: '1.2', name: 'report.pdf', contentType: 'application/pdf', data: bytes('%PDF-1.4 report ', 90000)},
    {partName: '1.3', name: 'Part 1.3', contentType: 'image/png', data: bytes('PNG')},
    {partName: '1.4', name: 'photo.jpg', contentType: 'image/jpeg', data: bytes('JPEGDATA')},
    {partName: '1.5', name: 'old.txt', contentType: 'text/x-moz-deleted', data: bytes('')},
  ];
  const folder = id => state.folders.find(f => f.id === id) || null;
  const header = m => ({id: m.id, date: m.date, author: m.author, subject: m.subject, read: m.read, size: m.size,
    recipients: m.recipients, ccList: m.ccList, headerMessageId: m.headerMessageId, folder: folder(m.folderId)});
  const lists = new Map();
  let nextList = 1, nextTab = 1, nextAttachment = 1;
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
      // Like Thunderbird: database order (the order mail arrived, oldest first), 100 per page unless
      // messagesPerPage says otherwise; fromDate keeps only messages dated after it.
      query: async ({folderId, messagesPerPage = 100, fromDate}) => {
        state.queries = (state.queries || 0) + 1;
        const from = fromDate === undefined ? null : new Date(fromDate).getTime();
        return page(state.messages.filter(m => m.folderId === folderId && (from === null || m.date.getTime() > from)).map(header),
          messagesPerPage);
      },
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
      // Like Thunderbird 128: every part it shows as an attachment, with its decoded size.
      listAttachments: async id => {
        const m = state.messages.find(x => x.id === id);
        if (!m) throw new Error('Message not found: ' + id);
        return m.attachments.map(a => ({contentType: a.contentType, name: a.name, partName: a.partName, size: a.data.length}));
      },
      getAttachmentFile: async (id, partName) => {
        state.attachmentReads = (state.attachmentReads || 0) + 1;
        const m = state.messages.find(x => x.id === id);
        const found = m && m.attachments.find(a => a.partName === partName);
        if (!found || found.damaged) throw new Error('Part not found: ' + partName);
        return new File([found.data], found.name, {type: found.contentType});
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
      // Forwarding inline brings the original's files (state.forwardWithoutFiles: a Thunderbird that does not).
      beginForward: async (messageId, forwardType, details) => newTab({kind: 'forward', messageId, forwardType, details,
        body: '<html><body><div>Forwarded original</div></body></html>', plainTextBody: 'Forwarded original',
        attachments: state.forwardWithoutFiles ? [] : (state.messages.find(m => m.id === messageId).attachments || [])
          .filter(a => a.contentType !== 'text/x-moz-deleted').map(a => new File([a.data], a.name, {type: a.contentType}))}),
      addAttachment: async (tabId, attachment) => {
        const tab = tabs.get(tabId);
        if (state.failAttach) throw new Error('Could not attach');
        if (!attachment || !(attachment.file instanceof Blob)) throw new Error('Type error for parameter attachment');
        const file = new File([attachment.file], attachment.name || attachment.file.name);
        const id = nextAttachment++;
        tab.attachments.push({id, file});
        return {id, name: file.name, size: file.size};
      },
      // Attachment ids stay the same while the window is open, as in Thunderbird.
      listAttachments: async tabId => tabs.get(tabId).attachments.map(({id, file}) => ({id, name: file.name, size: file.size})),
      removeAttachment: async (tabId, attachmentId) => {
        const tab = tabs.get(tabId);
        const at = tab.attachments.findIndex(item => item.id === attachmentId);
        if (at < 0) throw new Error('Invalid attachment ID: ' + attachmentId);
        tab.attachments.splice(at, 1);
      },
      getComposeDetails: async tabId => ({...tabs.get(tabId).current}),
      setComposeDetails: async (tabId, details) => { Object.assign(tabs.get(tabId).current, details); },
      saveMessage: async (tabId, options) => {
        if (state.failSave) throw new Error('Could not save');
        const id = 9000 + (state.drafts || []).length;
        state.drafts = (state.drafts || []).concat([{...tabs.get(tabId).current, options, id}]);
        return {mode: options.mode, messages: [{id}]};
      },
      sendMessage: async (tabId, options) => {
        if (state.failSend) throw new Error(state.failSend === true ? 'SMTP refused sam@example.com' : state.failSend);
        const tab = tabs.get(tabId);
        const why = state.failSendFor && state.failSendFor[tab.details && tab.details.identityId];
        if (why) throw new Error(why);
        state.sent.push({...tab, options});
        // Thunderbird closes the compose window after sending, unless something keeps it
        // open (state.composeStaysOpen, as Will saw with Office 365).
        if (!state.composeStaysOpen) state.openCompose = state.openCompose.filter(w => w !== tab.windowId);
        return {mode: 'sendNow', headerMessageId: 'sent' + tabId + '@thunderbird.test', messages: []};
      },
    },
    windows: {
      update: async (windowId, info) => {
        state.minimized.push({windowId, info});
        const win = state.windows.find(w => w.id === windowId);
        if (win && info.state) win.state = info.state;
      },
      get: async windowId => {
        if (state.openCompose.includes(windowId)) return {id: windowId, type: 'messageCompose'};
        const win = state.windows.find(w => w.id === windowId);
        if (!win) throw new Error('Invalid window ID: ' + windowId);
        return {...win};
      },
      remove: async windowId => {
        if (!state.openCompose.includes(windowId)) throw new Error('Invalid window ID: ' + windowId);
        state.openCompose = state.openCompose.filter(w => w !== windowId); state.closedByAddon.push(windowId);
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
      // Ask the server for new mail in one folder, as Thunderbird does when a folder is opened.
      getNewMail: async (accountId, path, inbox) => {
        state.mailChecks.push({accountId, path, inbox});
        if (state.mailCheck === 'hang') return new Promise(() => {});
        if (state.mailCheck === 'offline') return {checked: false, offline: true};
        if (state.mailCheck === 'fail') return {checked: false, offline: false};
        const target = state.folders.find(f => f.accountId === accountId && f.path === path);
        for (const m of state.server.filter(m => target && m.folderId === target.id)) state.messages.push(m);
        state.server = state.server.filter(m => !target || m.folderId !== target.id);
        return {checked: true, offline: false};
      },
    },
    runtime: {getManifest: () => ({version: require('./manifest.json').version})},
  };
  addContactsAndCalendars(api, state);
  function newTab(info) {
    const id = nextTab++;
    const tab = {id, windowId: 100 + id, ...info, attachments: (info.attachments || []).map(file => ({id: nextAttachment++, file})),
      current: {isPlainText: false, body: info.body || '', plainTextBody: info.plainTextBody || '', ...(info.details || {})}};
    tabs.set(id, tab); state.composed.push(tab); state.openCompose.push(tab.windowId);
    return {id, windowId: tab.windowId};
  }
  return {api, state};
}

// Address books (messenger.addressBooks, messenger.contacts) as Thunderbird 128 gives them to a
// manifest version 2 add-on: each contact has properties with the full vCard and the legacy fields.
// Calendars through the voiceCompanionCalendar Experiment (experiment/calendar.js), in its answer shapes.
function addContactsAndCalendars(api, state) {
  const card = (name, emails, phones, extra = '') => ['BEGIN:VCARD', 'VERSION:4.0', 'FN:' + name,
    ...emails.map(e => 'EMAIL:' + e), ...phones.map(p => 'TEL;TYPE=cell:' + p), extra, 'END:VCARD'].filter(Boolean).join('\r\n');
  const contact = (id, parentId, name, emails, phones, extra) => ({id, parentId, type: 'contact', readOnly: parentId === 'book-ro',
    properties: {vCard: card(name, emails, phones, extra), DisplayName: name, PrimaryEmail: emails[0] || '', SecondEmail: emails[1] || ''}});
  state.addressBooks = [
    {id: 'book-personal', type: 'addressBook', name: 'Personal Address Book', readOnly: false, remote: false},
    {id: 'book-collected', type: 'addressBook', name: 'Collected Addresses', readOnly: false, remote: false},
    {id: 'book-google', type: 'addressBook', name: 'me@gmail.com', readOnly: false, remote: false},
    {id: 'book-ro', type: 'addressBook', name: 'Company directory', readOnly: true, remote: false},
    {id: 'book-ldap', type: 'addressBook', name: 'LDAP', readOnly: true, remote: true},
  ];
  state.contacts = [
    contact('c1', 'book-google', 'John Smith', ['john@example.com'], ['555-0100']),
    contact('c2', 'book-google', 'Mary Jones', ['mary@example.com', 'mary.work@example.org'], []),
    contact('c3', 'book-personal', 'Plumber', [], ['555-0199']),
    // A family-name-first N line and no FN: the name is still found.
    {id: 'c4', parentId: 'book-collected', type: 'contact', readOnly: false,
     properties: {vCard: 'BEGIN:VCARD\r\nVERSION:4.0\r\nN:Brown;Ann;;;\r\nitem1.EMAIL;TYPE=home:ann@example.com\r\nEND:VCARD'}},
    contact('c5', 'book-ro', 'Help Desk', ['help@work.example'], ['555-0111']),
    contact('c6', 'book-ldap', 'Remote Person', ['remote@example.com'], []),
  ];
  state.contactCalls = [];
  state.failBook = null;
  let nextContact = 100;
  api.addressBooks = {
    list: async complete => state.addressBooks.map(b => ({...b})),
  };
  api.contacts = {
    list: async parentId => {
      if (state.failBook === parentId) throw new Error('Address book is not ready');
      return state.contacts.filter(c => c.parentId === parentId).map(c => JSON.parse(JSON.stringify(c)));
    },
    get: async id => { const found = state.contacts.find(c => c.id === id); return found ? JSON.parse(JSON.stringify(found)) : null; },
    // Thunderbird 128, manifest version 2: create(parentId, [id], properties). The optional id is skipped
    // when the second argument is the properties object.
    create: async (parentId, ...rest) => {
      const properties = rest.length === 2 ? rest[1] : rest[0];
      if (typeof properties !== 'object' || !properties.vCard) throw new Error('Type error for parameter properties');
      const id = 'c' + nextContact++;
      state.contactCalls.push({create: parentId, vCard: properties.vCard});
      state.contacts.push({id, parentId, type: 'contact', readOnly: false, properties: {vCard: properties.vCard}});
      return id;
    },
    delete: async id => { state.contactCalls.push({delete: id}); state.contacts = state.contacts.filter(c => c.id !== id); },
  };

  // Times are milliseconds (UTC) for timed events, YYYY-MM-DD for all-day ones.
  const at = (y, m, d, h, min = 0) => Date.UTC(y, m - 1, d, h, min);
  state.calendars = [
    {id: 'cal-home', name: 'Home', type: 'storage', readOnly: false},
    {id: 'cal-google', name: 'me@gmail.com', type: 'caldav', readOnly: false},
    {id: 'cal-holidays', name: 'Holidays', type: 'ics', readOnly: true},
  ];
  state.events = [
    {id: 'e1', calendarId: 'cal-google', calendarName: 'me@gmail.com', title: 'Doctor appointment', location: 'VA clinic',
     notes: 'Bring the card.', allDay: false, start: at(2026, 10, 26, 15, 30), end: at(2026, 10, 26, 16), recurring: false, occurrence: ''},
    {id: 'e2', calendarId: 'cal-holidays', calendarName: 'Holidays', title: 'Veterans Day', location: '', notes: '',
     allDay: true, start: '2026-11-11', end: '2026-11-12', recurring: false, occurrence: ''},
    {id: 'e3', calendarId: 'cal-google', calendarName: 'me@gmail.com', title: 'Group meeting', location: '', notes: '',
     allDay: false, start: at(2026, 10, 27, 14), end: at(2026, 10, 27, 15), recurring: true, occurrence: '20261027T140000Z'},
    {id: 'e3', calendarId: 'cal-google', calendarName: 'me@gmail.com', title: 'Group meeting', location: '', notes: '',
     allDay: false, start: at(2026, 11, 3, 14), end: at(2026, 11, 3, 15), recurring: true, occurrence: '20261103T140000Z'},
  ];
  state.calendarCalls = [];
  state.calendarBroken = false;
  let nextEvent = 100;
  const overlaps = (event, startMs, endMs) => {
    const ms = value => (typeof value === 'number' ? value : Date.parse(value + 'T00:00:00Z'));
    return ms(event.start) < endMs && ms(event.end) > startMs;
  };
  api.voiceCompanionCalendar = {
    listCalendars: async () => (state.calendarBroken ? null : state.calendars.map(c => ({...c}))),
    listEvents: async (startMs, endMs) => {
      state.calendarCalls.push({list: [startMs, endMs]});
      if (state.calendarBroken) return null;
      return {events: state.events.filter(e => overlaps(e, startMs, endMs)).map(e => ({...e})), failed: state.failedCalendars || []};
    },
    createEvent: async details => {
      state.calendarCalls.push({create: {...details}});
      const calendar = state.calendars.find(c => c.id === details.calendarId);
      if (!calendar) return {created: false, reason: 'missing'};
      if (calendar.readOnly) return {created: false, reason: 'readonly'};
      const id = 'e' + nextEvent++;
      state.events.push({id, calendarId: calendar.id, calendarName: calendar.name, title: details.title, location: details.location || '',
        notes: details.notes || '', allDay: details.allDay, start: details.allDay ? details.start : Number(details.start),
        end: details.allDay ? details.end : Number(details.end), recurring: false, occurrence: ''});
      return {created: true, id};
    },
    deleteEvent: async (calendarId, id, occurrence, all) => {
      state.calendarCalls.push({delete: {calendarId, id, occurrence, all}});
      const calendar = state.calendars.find(c => c.id === calendarId);
      if (calendar && calendar.readOnly) return {deleted: false, reason: 'readonly'};
      const matching = state.events.filter(e => e.calendarId === calendarId && e.id === id);
      if (!matching.length) return {deleted: false, reason: 'missing'};
      const removeAll = all || !occurrence;
      state.events = state.events.filter(e => !(e.calendarId === calendarId && e.id === id && (removeAll || e.occurrence === occurrence)));
      return {deleted: true, all: removeAll};
    },
  };
}

module.exports = {createFakeMessenger};

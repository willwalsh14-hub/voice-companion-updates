// Voice Companion Mail Connection: contacts and calendars.
// Loaded after background.js. It adds requests to MailBridge, so Voice Companion's
// Contacts and Calendar screens use what Thunderbird already syncs (Google and other
// CardDAV address books, Google and other CalDAV calendars). Thunderbird keeps every
// sign-in. Nothing here is logged: names, addresses and events can be private.
'use strict';

(function () {
  const Bridge = globalThis.MailBridge;
  if (!Bridge) return;
  const MAX_CONTACTS = 5000;
  const MAX_EVENTS = 1000;
  const MAX_RANGE_MS = 62 * 24 * 60 * 60 * 1000;   // the longest span one events request may ask for
  const DAY = /^\d{4}-\d{2}-\d{2}$/;

  // ---- vCard reading and writing (RFC 6350, the parts Voice Companion uses) -------

  function unescapeValue(value) {
    return String(value).replace(/\\([\\;,nN])/g, (all, ch) => (ch === 'n' || ch === 'N' ? ' ' : ch)).trim();
  }

  function escapeValue(value) {
    return String(value).replace(/\\/g, '\\\\').replace(/;/g, '\\;').replace(/,/g, '\\,').replace(/\r?\n/g, ' ');
  }

  // [{name, value}] for each line; folded lines are joined; a group prefix (item1.EMAIL) is dropped.
  function vcardLines(text) {
    const unfolded = String(text || '').replace(/\r\n[ \t]/g, '').replace(/\n[ \t]/g, '');
    const lines = [];
    for (const line of unfolded.split(/\r?\n/)) {
      const colon = line.indexOf(':');
      if (colon < 1) continue;
      const head = line.slice(0, colon).split(';')[0];
      const name = head.slice(head.lastIndexOf('.') + 1).toUpperCase();
      lines.push({name, value: line.slice(colon + 1)});
    }
    return lines;
  }

  function cleanPhone(value) {
    return unescapeValue(value).replace(/^tel:/i, '').trim();
  }

  // A contact as Voice Companion uses it, from Thunderbird's contact node (MV2 or MV3 shape).
  Bridge.contactFrom = function (node, book) {
    const properties = node.properties || {};
    const vcard = node.vCard || properties.vCard || '';
    const lines = vcardLines(vcard);
    const values = name => lines.filter(line => line.name === name).map(line => line.value);
    let name = unescapeValue(values('FN')[0] || '');
    if (!name && values('N').length) {
      const [family = '', given = ''] = values('N')[0].split(/(?<!\\);/).map(unescapeValue);
      name = (given + ' ' + family).trim();
    }
    if (!name) name = String(properties.DisplayName || ((properties.FirstName || '') + ' ' + (properties.LastName || ''))).trim();
    const emails = [];
    for (const value of values('EMAIL').concat([properties.PrimaryEmail, properties.SecondEmail])) {
      const email = unescapeValue(value || '').replace(/^mailto:/i, '').trim();
      if (email && email.length <= 254 && !emails.some(known => known.toLowerCase() === email.toLowerCase())) emails.push(email);
    }
    const phones = [];
    for (const value of values('TEL').concat([properties.CellularNumber, properties.HomePhone, properties.WorkPhone])) {
      const phone = cleanPhone(value || '');
      if (phone && phone.length <= 60 && !phones.includes(phone)) phones.push(phone);
    }
    return {id: String(node.id), bookId: String(node.parentId || (book && book.id) || ''), book: book ? String(book.name || '') : '',
      name: name.slice(0, 200), emails: emails.slice(0, 10), phones: phones.slice(0, 10), readOnly: Boolean(node.readOnly || (book && book.readOnly))};
  };

  Bridge.vcard = function (name, email, phone, uid) {
    const parts = String(name).trim().split(/\s+/);
    const family = parts.length > 1 ? parts.pop() : '';
    const lines = ['BEGIN:VCARD', 'VERSION:4.0', 'UID:' + uid, 'FN:' + escapeValue(name),
      'N:' + escapeValue(family) + ';' + escapeValue(parts.join(' ')) + ';;;'];
    if (email) lines.push('EMAIL;PREF=1:' + escapeValue(email));
    if (phone) lines.push('TEL;VALUE=TEXT:' + escapeValue(phone));
    lines.push('END:VCARD');
    return lines.join('\r\n') + '\r\n';
  };

  function newUid() {
    if (globalThis.crypto && typeof globalThis.crypto.randomUUID === 'function') return globalThis.crypto.randomUUID();
    const hex = () => Math.floor(Math.random() * 0x10000).toString(16).padStart(4, '0');
    return hex() + hex() + '-' + hex() + '-4' + hex().slice(1) + '-a' + hex().slice(1) + '-' + hex() + hex() + hex();
  }

  // Thunderbird 128 (manifest version 2) has messenger.contacts; newer versions have
  // messenger.addressBooks.contacts, which takes a vCard directly.
  Bridge.prototype.contactsApi = function () {
    const books = this.api.addressBooks;
    if (books && books.contacts && typeof books.contacts.list === 'function') return {api: books.contacts, vcardOnly: true};
    if (this.api.contacts && typeof this.api.contacts.list === 'function') return {api: this.api.contacts, vcardOnly: false};
    throw Bridge.fail('contacts', 'Thunderbird did not let Voice Companion read its address books. Close Thunderbird, then try again.');
  };

  // ---- Address books and contacts ---------------------------------------------------

  Bridge.prototype.addressBooks = async function () {
    if (!this.api.addressBooks || typeof this.api.addressBooks.list !== 'function') {
      throw Bridge.fail('contacts', 'Thunderbird did not let Voice Companion read its address books. Close Thunderbird, then try again.');
    }
    const books = await this.api.addressBooks.list(false);
    // Remote books (LDAP directories) are searched, not listed; leave them out.
    return books.filter(book => !book.remote).map(book => ({id: String(book.id), name: String(book.name || ''), readOnly: Boolean(book.readOnly)}));
  };

  Bridge.prototype.contacts = async function () {
    const books = await this.addressBooks();
    const {api} = this.contactsApi();
    const found = [];
    let failed = 0;
    for (const book of books) {
      let nodes;
      try {
        nodes = await api.list(book.id);
      } catch (error) {
        failed++;     // one broken book must not hide the others
        continue;
      }
      for (const node of nodes || []) {
        if (found.length >= MAX_CONTACTS) break;
        try { found.push(Bridge.contactFrom(node, book)); } catch (error) { /* a damaged card is left out */ }
      }
    }
    return {books, contacts: found, failed};
  };

  Bridge.prototype.addContact = async function ({bookId = '', name = '', email = '', phone = ''}) {
    if (typeof name !== 'string' || !name.trim() || name.length > 100 || /[\r\n<>]/.test(name)) {
      throw Bridge.fail('name', 'Use a shorter name without symbols.');
    }
    if (typeof email !== 'string' || email.length > 254 || (email && !/^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(email))) {
      throw Bridge.fail('address', 'That is not a complete email address.');
    }
    if (typeof phone !== 'string' || phone.length > 40 || /[\r\n]/.test(phone)) throw Bridge.fail('phone', 'Use a shorter phone number.');
    const books = (await this.addressBooks()).filter(book => !book.readOnly);
    if (!books.length) throw Bridge.fail('book', 'Thunderbird has no address book that can be changed.');
    // The book asked for, else Personal Address Book, else the first that is not Collected Addresses.
    const book = books.find(b => b.id === bookId) || books.find(b => /personal/i.test(b.name)) ||
      books.find(b => !/collected/i.test(b.name)) || books[0];
    const {api, vcardOnly} = this.contactsApi();
    const card = Bridge.vcard(name.trim(), email.trim(), phone.trim(), newUid());
    const id = vcardOnly ? await api.create(book.id, card) : await api.create(book.id, {vCard: card});
    return {id: String(id), bookId: book.id, book: book.name};
  };

  Bridge.prototype.deleteContact = async function ({id}) {
    if (typeof id !== 'string' || !id) throw Bridge.fail('contact', 'That contact is not available.');
    const {api} = this.contactsApi();
    let node = null;
    try { node = await api.get(id); } catch (error) { node = null; }
    if (!node) throw Bridge.fail('contact', 'That contact is no longer in Thunderbird.');
    if (node.readOnly) throw Bridge.fail('contact', 'This address book cannot be changed, so the contact was not deleted.');
    await api.delete(id);
    return true;
  };

  // ---- Calendars (the voiceCompanionCalendar Experiment) ---------------------------

  Bridge.prototype.calendarApi = function () {
    const calendar = this.api.voiceCompanionCalendar;
    if (!calendar || typeof calendar.listEvents !== 'function') {
      throw Bridge.fail('calendar', 'The Thunderbird calendar is not available. Close Thunderbird, then try again.');
    }
    return calendar;
  };

  Bridge.prototype.calendars = async function () {
    const list = await this.calendarApi().listCalendars();
    if (!Array.isArray(list)) throw Bridge.fail('calendar', 'Thunderbird could not list its calendars.');
    return list.map(c => ({id: String(c.id), name: String(c.name || ''), type: String(c.type || ''), readOnly: Boolean(c.readOnly)}));
  };

  Bridge.cleanEvent = function (event) {
    const text = (value, limit) => String(value || '').replace(/\s+/g, ' ').trim().slice(0, limit);
    const allDay = Boolean(event.allDay);
    const when = value => (allDay ? (DAY.test(String(value)) ? String(value) : '') : (Number.isFinite(Number(value)) ? Number(value) : null));
    return {id: text(event.id, 500), calendarId: text(event.calendarId, 500), calendar: text(event.calendarName, 200),
      title: text(event.title, 300), location: text(event.location, 300), notes: String(event.notes || '').slice(0, 4000),
      allDay, start: when(event.start), end: when(event.end), recurring: Boolean(event.recurring),
      occurrence: text(event.occurrence, 40)};
  };

  Bridge.prototype.events = async function ({start, end}) {
    if (!Number.isFinite(start) || !Number.isFinite(end) || end <= start || end - start > MAX_RANGE_MS) {
      throw Bridge.fail('range', 'Invalid calendar dates.');
    }
    const answer = await this.calendarApi().listEvents(start, end);
    if (!answer || !Array.isArray(answer.events)) throw Bridge.fail('calendar', 'Thunderbird could not read its calendars.');
    const events = [];
    for (const event of answer.events.slice(0, MAX_EVENTS)) {
      const clean = Bridge.cleanEvent(event);
      if (clean.id && clean.calendarId && clean.start !== null && clean.start !== '') events.push(clean);
    }
    return {events, failed: (answer.failed || []).map(String).slice(0, 20)};
  };

  Bridge.prototype.addEvent = async function (params) {
    const {calendarId, title = '', allDay = false, start, end, location = '', notes = ''} = params;
    if (typeof calendarId !== 'string' || !calendarId) throw Bridge.fail('calendar', 'Choose a calendar first.');
    if (typeof title !== 'string' || !title.trim() || title.length > 300) throw Bridge.fail('title', 'Give the event a shorter name.');
    if (typeof location !== 'string' || location.length > 300 || typeof notes !== 'string' || notes.length > 4000) {
      throw Bridge.fail('details', 'The place or notes are too long.');
    }
    if (allDay ? !(DAY.test(String(start)) && DAY.test(String(end)) && end > start)
      : !(Number.isFinite(start) && Number.isFinite(end) && end > start && end - start <= MAX_RANGE_MS)) {
      throw Bridge.fail('time', 'The event must end after it starts.');
    }
    const result = await this.calendarApi().createEvent({calendarId, title: title.trim(), allDay: Boolean(allDay),
      start: String(start), end: String(end), location: location.trim(), notes});
    if (!result || !result.created) {
      throw Bridge.fail('calendar', result && result.reason === 'readonly'
        ? 'That calendar cannot be changed. Choose another calendar.' : 'Thunderbird could not save the event.');
    }
    return {id: String(result.id || '')};
  };

  Bridge.prototype.deleteEvent = async function ({calendarId, id, occurrence = '', all = false}) {
    if (typeof calendarId !== 'string' || !calendarId || typeof id !== 'string' || !id || typeof occurrence !== 'string') {
      throw Bridge.fail('event', 'That event is not available.');
    }
    const result = await this.calendarApi().deleteEvent(calendarId, id, occurrence, Boolean(all));
    if (!result || !result.deleted) {
      throw Bridge.fail('event', result && result.reason === 'missing' ? 'That event is no longer in the calendar.'
        : result && result.reason === 'readonly' ? 'That calendar cannot be changed, so the event was not deleted.'
          : 'Thunderbird could not delete the event.');
    }
    return {all: Boolean(result.all)};
  };

  const reading = ['addressBooks', 'contacts', 'calendars', 'events'];
  const changing = ['addContact', 'deleteContact', 'addEvent', 'deleteEvent'];
  for (const name of reading.concat(changing)) if (!Bridge.METHODS.includes(name)) Bridge.METHODS.push(name);
  // None of these change mail, so the kept message list stays valid.
  for (const name of reading.concat(changing)) if (!Bridge.READ_ONLY.includes(name)) Bridge.READ_ONLY.push(name);
}());

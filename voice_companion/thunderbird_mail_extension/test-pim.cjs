// Tests for contacts and calendars in the Thunderbird add-on (pim.js and experiment/calendar.js).
// Run: node voice_companion/thunderbird_mail_extension/test-pim.cjs (test-bridge.cjs runs it too).
//
// pim.js runs against the simulated Thunderbird in fake-messenger.cjs. experiment/calendar.js runs
// against a small stand-in for Thunderbird's calendar manager (cal), shaped like Thunderbird 128's
// calUtils, CalEvent and calICalendar as Thunderbird's own calendar Experiment uses them. Real
// Thunderbird can only check this on Windows; see the notes in experiment/calendar.js.
'use strict';
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const {createFakeMessenger} = require('./fake-messenger.cjs');

function loadBridge(api, withPim = true) {
  const context = vm.createContext({console, setTimeout, clearTimeout, VOICE_COMPANION_TEST: true, messenger: api});
  vm.runInContext(fs.readFileSync(path.join(__dirname, 'background.js'), 'utf8'), context);
  if (withPim) vm.runInContext(fs.readFileSync(path.join(__dirname, 'pim.js'), 'utf8'), context);
  return new (vm.runInContext('MailBridge', context))(api);
}

const plain = value => JSON.parse(JSON.stringify(value));

async function contactChecks() {
  const {api, state} = createFakeMessenger();
  const bridge = loadBridge(api);
  const call = async (method, params) => {
    const answer = plain(await bridge.handle({id: 5, method, params}));
    if (answer.error) throw Object.assign(new Error(answer.error.message), {answer});
    return answer.result;
  };

  // Address books: remote (LDAP) books are left out.
  assert.deepEqual((await call('addressBooks')).map(b => b.name),
    ['Personal Address Book', 'Collected Addresses', 'me@gmail.com', 'Company directory']);

  // Every contact from every book, read from the vCard: several emails, a phone, no email, an N line only.
  const {contacts, books, failed} = await call('contacts');
  assert.equal(failed, 0);
  assert.equal(books.length, 4);
  const byName = Object.fromEntries(contacts.map(c => [c.name, c]));
  assert.deepEqual(Object.keys(byName).sort(), ['Ann Brown', 'Help Desk', 'John Smith', 'Mary Jones', 'Plumber']);
  assert.deepEqual(byName['John Smith'], {id: 'c1', bookId: 'book-google', book: 'me@gmail.com', name: 'John Smith',
    emails: ['john@example.com'], phones: ['555-0100'], readOnly: false});
  assert.deepEqual(byName['Mary Jones'].emails, ['mary@example.com', 'mary.work@example.org']);
  assert.deepEqual(byName['Plumber'].emails, []);
  assert.deepEqual(byName['Ann Brown'].emails, ['ann@example.com'], 'a grouped EMAIL line (item1.EMAIL) is read');
  assert.equal(byName['Help Desk'].readOnly, true);

  // One address book that cannot be read does not hide the others.
  state.failBook = 'book-personal';
  const partial = await call('contacts');
  assert.equal(partial.failed, 1);
  assert.ok(!partial.contacts.some(c => c.name === 'Plumber'));
  assert.ok(partial.contacts.some(c => c.name === 'John Smith'));
  state.failBook = null;

  // Escaped commas, semicolons and folded lines in a vCard.
  const MailBridge = bridge.constructor;
  const folded = plain(MailBridge.contactFrom({id: 'x', parentId: 'b', properties: {vCard:
    'BEGIN:VCARD\r\nVERSION:4.0\r\nFN:Smith\\, Jr.\\; Bob\r\nEMAIL:bob@exam\r\n ple.com\r\nTEL;VALUE=uri:tel:+1-555-0123\r\nEND:VCARD'}}, null));
  assert.equal(folded.name, 'Smith, Jr.; Bob');
  assert.deepEqual(folded.emails, ['bob@example.com']);
  assert.deepEqual(folded.phones, ['+1-555-0123']);
  // Legacy fields only (an old contact with no vCard).
  const legacy = plain(MailBridge.contactFrom({id: 'y', parentId: 'b', properties: {FirstName: 'Sue', LastName: 'Lee', PrimaryEmail: 'sue@example.com', CellularNumber: '555'}}, null));
  assert.deepEqual([legacy.name, legacy.emails, legacy.phones], ['Sue Lee', ['sue@example.com'], ['555']]);
  // Thunderbird 128 with manifest version 3 shape: the vCard is on the node itself.
  assert.equal(MailBridge.contactFrom({id: 'z', parentId: 'b', vCard: 'BEGIN:VCARD\r\nFN:Zed\r\nEND:VCARD'}, null).name, 'Zed');

  // Adding: into the book asked for; otherwise Personal Address Book. A vCard, never legacy fields.
  const added = await call('addContact', {bookId: 'book-google', name: 'Pat Doe', email: 'pat@example.com', phone: '555-0142'});
  assert.equal(added.bookId, 'book-google');
  const made = state.contactCalls.at(-1);
  assert.equal(made.create, 'book-google');
  assert.match(made.vCard, /^BEGIN:VCARD\r\nVERSION:4\.0\r\nUID:[0-9a-f-]+\r\nFN:Pat Doe\r\nN:Doe;Pat;;;\r\nEMAIL;PREF=1:pat@example\.com\r\nTEL;VALUE=TEXT:555-0142\r\nEND:VCARD\r\n$/);
  assert.ok((await call('contacts')).contacts.some(c => c.name === 'Pat Doe' && c.emails[0] === 'pat@example.com'));
  await call('addContact', {bookId: 'missing', name: 'No Email', email: '', phone: '555'});
  assert.equal(state.contactCalls.at(-1).create, 'book-personal');
  assert.doesNotMatch(state.contactCalls.at(-1).vCard, /EMAIL/);
  await call('addContact', {bookId: 'book-ro', name: 'Read Only Book', email: 'r@example.com'});
  assert.equal(state.contactCalls.at(-1).create, 'book-personal', 'a read-only book is never written to');
  await assert.rejects(call('addContact', {name: '', email: 'a@b.co'}), /shorter name/);
  await assert.rejects(call('addContact', {name: 'Bad', email: 'not an address'}), /not a complete email address/);
  await assert.rejects(call('addContact', {name: 'x<y>', email: ''}), /without symbols/);

  // Deleting: only from a book that can change.
  assert.equal(await call('deleteContact', {id: 'c3'}), true);
  assert.ok(!state.contacts.some(c => c.id === 'c3'));
  await assert.rejects(call('deleteContact', {id: 'c5'}), /cannot be changed/);
  await assert.rejects(call('deleteContact', {id: 'nope'}), /no longer in Thunderbird/);

  // Changing a contact: only the lines asked for change; the rest of the card is kept as it was.
  const card = id => state.contacts.find(c => c.id === id).properties.vCard;
  assert.equal(await call('updateContact', {id: 'c1', name: 'John Smyth', phone: '555-0200'}), true);
  assert.match(card('c1'), /\r\nFN:John Smyth\r\n/);
  assert.match(card('c1'), /\r\nEMAIL:john@example\.com\r\n/, 'the email address is kept');
  assert.match(card('c1'), /\r\nTEL;TYPE=cell:555-0200\r\n/, 'the phone keeps its type');
  assert.match(card('c1'), /^BEGIN:VCARD\r\nVERSION:4\.0\r\n[\s\S]*END:VCARD\r\n$/);
  let after = (await call('contacts')).contacts;
  assert.deepEqual(after.find(c => c.id === 'c1'), {id: 'c1', bookId: 'book-google', book: 'me@gmail.com', name: 'John Smyth',
    emails: ['john@example.com'], phones: ['555-0200'], readOnly: false});
  // The first email address changes; others stay. An empty one removes it.
  await call('updateContact', {id: 'c2', email: 'mary@new.example'});
  assert.deepEqual((await call('contacts')).contacts.find(c => c.id === 'c2').emails, ['mary@new.example', 'mary.work@example.org']);
  await call('updateContact', {id: 'c2', email: ''});
  assert.deepEqual((await call('contacts')).contacts.find(c => c.id === 'c2').emails, ['mary.work@example.org']);
  // A card with no phone gets one before END:VCARD; a grouped line (item1.EMAIL) keeps its group and type.
  await call('updateContact', {id: 'c2', phone: '555-0300'});
  assert.match(card('c2'), /\r\nTEL;VALUE=TEXT:555-0300\r\nEND:VCARD\r\n$/);
  await call('updateContact', {id: 'c4', email: 'ann@new.example'});
  assert.match(card('c4'), /\r\nitem1\.EMAIL;TYPE=home:ann@new\.example\r\n/);
  assert.match(card('c4'), /\r\nN:Brown;Ann;;;\r\n/, 'the name is not touched when it is not changed');
  // A new name changes the N line too, and adds the FN line a card must have.
  await call('updateContact', {id: 'c4', name: 'Ann Green'});
  assert.match(card('c4'), /\r\nN:Green;Ann;;;\r\n/);
  assert.match(card('c4'), /\r\nFN:Ann Green\r\nEND:VCARD\r\n$/);
  assert.equal((await call('contacts')).contacts.find(c => c.id === 'c4').name, 'Ann Green');
  // A phone saved as a tel: address stays one.
  state.contacts.push({id: 'c9', parentId: 'book-personal', type: 'contact', readOnly: false,
    properties: {vCard: 'BEGIN:VCARD\r\nVERSION:4.0\r\nFN:Tel Uri\r\nTEL;VALUE=uri:tel:+1-555-0123\r\nNOTE:Keep me\r\nEND:VCARD\r\n'}});
  await call('updateContact', {id: 'c9', phone: '+1-555-0999'});
  assert.equal(card('c9'), 'BEGIN:VCARD\r\nVERSION:4.0\r\nFN:Tel Uri\r\nTEL;VALUE=uri:tel:+1-555-0999\r\nNOTE:Keep me\r\nEND:VCARD\r\n');
  // A name with a comma is escaped; a card with no N line is not given one.
  await call('updateContact', {id: 'c9', name: 'Uri, Tel'});
  assert.equal(card('c9'), 'BEGIN:VCARD\r\nVERSION:4.0\r\nFN:Uri\\, Tel\r\nTEL;VALUE=uri:tel:+1-555-0999\r\nNOTE:Keep me\r\nEND:VCARD\r\n');
  // Checks before Thunderbird is asked, and Thunderbird's answers.
  const before = state.contactCalls.length;
  await assert.rejects(call('updateContact', {id: 'c1', name: ' '}), /shorter name/);
  await assert.rejects(call('updateContact', {id: 'c1', email: 'not an address'}), /not a complete email address/);
  await assert.rejects(call('updateContact', {id: 'c1', phone: '1'.repeat(41)}), /shorter phone number/);
  await assert.rejects(call('updateContact', {id: 'c1'}), /Nothing was changed/);
  await assert.rejects(call('updateContact', {id: ''}), /not available/);
  assert.equal(state.contactCalls.length, before);
  await assert.rejects(call('updateContact', {id: 'c5', name: 'X'}), /cannot be changed, so the contact was not changed/);
  await assert.rejects(call('updateContact', {id: 'nope', name: 'X'}), /no longer in Thunderbird/);

  // Newer Thunderbird (addressBooks.contacts): create takes the vCard itself.
  const newer = createFakeMessenger();
  const created = [];
  newer.api.addressBooks.contacts = {list: newer.api.contacts.list, get: newer.api.contacts.get, delete: newer.api.contacts.delete,
    create: async (parentId, vCard) => { assert.equal(typeof vCard, 'string'); created.push(parentId); return 'new1'; }};
  delete newer.api.contacts;
  const newerBridge = loadBridge(newer.api);
  const answer = plain(await newerBridge.handle({id: 6, method: 'addContact', params: {name: 'Ray', email: 'ray@example.com'}}));
  assert.deepEqual(answer.result, {id: 'new1', bookId: 'book-personal', book: 'Personal Address Book'});
  // ... and update takes the vCard itself too.
  const updated = [];
  newer.api.addressBooks.contacts.update = async (id, vCard) => { assert.equal(typeof vCard, 'string'); updated.push([id, vCard]); };
  assert.equal(plain(await newerBridge.handle({id: 10, method: 'updateContact', params: {id: 'c3', name: 'Joe Plumber'}})).result, true);
  assert.match(updated[0][1], /\r\nFN:Joe Plumber\r\n/);
  assert.equal(plain(await newerBridge.handle({id: 7, method: 'contacts'})).result.contacts.length, 5);

  // Without the address book permission (an older add-on copy's manifest), a plain answer.
  const without = createFakeMessenger();
  delete without.api.addressBooks; delete without.api.contacts;
  const refused = plain(await loadBridge(without.api).handle({id: 8, method: 'contacts'}));
  assert.match(refused.error.message, /did not let Voice Companion read its address books/);

  // An add-on without pim.js answers "unsupported", which Voice Companion turns into update advice.
  const old = plain(await loadBridge(createFakeMessenger().api, false).handle({id: 9, method: 'contacts'}));
  assert.equal(old.error.code, 'invalid');
}

async function calendarChecks() {
  const {api, state} = createFakeMessenger();
  const bridge = loadBridge(api);
  const call = async (method, params) => {
    const answer = plain(await bridge.handle({id: 5, method, params}));
    if (answer.error) throw Object.assign(new Error(answer.error.message), {answer});
    return answer.result;
  };
  assert.deepEqual((await call('calendars')).map(c => [c.name, c.type, c.readOnly]),
    [['Home', 'storage', false], ['me@gmail.com', 'caldav', false], ['Holidays', 'ics', true]]);

  const day = (y, m, d) => Date.UTC(y, m - 1, d);
  const {events} = await call('events', {start: day(2026, 10, 26), end: day(2026, 10, 28)});
  assert.deepEqual(events.map(e => [e.title, e.recurring, e.occurrence]),
    [['Doctor appointment', false, ''], ['Group meeting', true, '20261027T140000Z']]);
  assert.equal(events[0].location, 'VA clinic');
  assert.equal(events[0].calendar, 'me@gmail.com');
  const holiday = (await call('events', {start: day(2026, 11, 11), end: day(2026, 11, 12)})).events[0];
  assert.deepEqual([holiday.allDay, holiday.start, holiday.end], [true, '2026-11-11', '2026-11-12']);
  // Ranges are checked: backwards, empty, or longer than about two months.
  await assert.rejects(call('events', {start: day(2026, 10, 28), end: day(2026, 10, 26)}), /Invalid calendar dates/);
  await assert.rejects(call('events', {start: day(2026, 1, 1), end: day(2026, 12, 1)}), /Invalid calendar dates/);
  await assert.rejects(call('events', {start: 'today', end: 5}), /Invalid calendar dates/);

  // Damaged answers: an event with no id or start is left out; a missing answer is a plain error.
  state.events.push({id: '', calendarId: 'cal-home', title: 'Broken', allDay: false, start: day(2026, 10, 26) + 1, end: day(2026, 10, 26) + 2});
  state.events.push({id: 'nostart', calendarId: 'cal-home', title: 'Broken', allDay: true, start: 'soon', end: day(2026, 10, 26) + 2});
  assert.ok(!(await call('events', {start: day(2026, 10, 26), end: day(2026, 10, 27)})).events.some(e => e.title === 'Broken'));
  state.calendarBroken = true;
  await assert.rejects(call('events', {start: day(2026, 10, 26), end: day(2026, 10, 27)}), /could not read its calendars/);
  await assert.rejects(call('calendars'), /could not list its calendars/);
  state.calendarBroken = false;

  // Adding: timed and all-day; checks before Thunderbird is asked.
  const start = Date.UTC(2026, 9, 30, 14), end = start + 3600000;
  const added = await call('addEvent', {calendarId: 'cal-google', title: 'Lunch', start, end, allDay: false});
  assert.match(added.id, /^e\d+$/);
  assert.deepEqual(state.calendarCalls.at(-1).create, {calendarId: 'cal-google', title: 'Lunch', allDay: false,
    start: String(start), end: String(end), location: '', notes: ''});
  await call('addEvent', {calendarId: 'cal-home', title: 'Trip', start: '2026-10-30', end: '2026-10-31', allDay: true});
  assert.equal(state.calendarCalls.at(-1).create.start, '2026-10-30');
  await assert.rejects(call('addEvent', {calendarId: 'cal-holidays', title: 'X', start, end}), /cannot be changed/);
  await assert.rejects(call('addEvent', {calendarId: 'cal-home', title: ' ', start, end}), /shorter name/);
  await assert.rejects(call('addEvent', {calendarId: 'cal-home', title: 'X', start: end, end: start}), /end after it starts/);
  await assert.rejects(call('addEvent', {calendarId: 'cal-home', title: 'X', allDay: true, start: '2026-10-30', end: '2026-10-30'}), /end after/);

  // Deleting one day of a repeating event, then the whole series.
  assert.deepEqual(await call('deleteEvent', {calendarId: 'cal-google', id: 'e3', occurrence: '20261027T140000Z', all: false}), {all: false});
  assert.equal(state.events.filter(e => e.id === 'e3').length, 1);
  assert.deepEqual(await call('deleteEvent', {calendarId: 'cal-google', id: 'e3', occurrence: '20261103T140000Z', all: true}), {all: true});
  assert.equal(state.events.filter(e => e.id === 'e3').length, 0);
  await assert.rejects(call('deleteEvent', {calendarId: 'cal-google', id: 'e3'}), /no longer in the calendar/);
  await assert.rejects(call('deleteEvent', {calendarId: 'cal-holidays', id: 'e2'}), /cannot be changed/);

  // Changing an event: only the parts asked for; checked before Thunderbird is asked.
  const doctor = () => state.events.find(e => e.id === 'e1');
  assert.deepEqual(await call('modifyEvent', {calendarId: 'cal-google', id: 'e1', changes: {title: ' Dentist ', location: ''}}), {id: 'e1'});
  assert.deepEqual(state.calendarCalls.at(-1).modify, {calendarId: 'cal-google', id: 'e1', occurrence: '', all: false,
    changes: {title: 'Dentist', location: ''}});
  assert.deepEqual([doctor().title, doctor().location, doctor().notes], ['Dentist', '', 'Bring the card.']);
  const later = Date.UTC(2026, 9, 30, 19);
  await call('modifyEvent', {calendarId: 'cal-google', id: 'e1', changes: {allDay: false, start: later, end: later + 1800000}});
  assert.deepEqual(state.calendarCalls.at(-1).modify.changes, {allDay: false, start: String(later), end: String(later + 1800000)});
  await call('modifyEvent', {calendarId: 'cal-google', id: 'e1', changes: {allDay: true, start: '2026-10-30', end: '2026-10-31'}});
  assert.equal(doctor().start, '2026-10-30');
  await call('modifyEvent', {calendarId: 'cal-google', id: 'e1', changes: {calendarId: 'cal-home'}});
  assert.equal(doctor().calendarId, 'cal-home');
  // Bad requests never reach Thunderbird.
  const asked = state.calendarCalls.length;
  await assert.rejects(call('modifyEvent', {calendarId: 'cal-home', id: 'e1', changes: {}}), /Nothing was changed/);
  await assert.rejects(call('modifyEvent', {calendarId: 'cal-home', id: 'e1', changes: {title: ' '}}), /shorter name/);
  await assert.rejects(call('modifyEvent', {calendarId: 'cal-home', id: 'e1', changes: {start: later, end: later - 1, allDay: false}}), /end after it starts/);
  await assert.rejects(call('modifyEvent', {calendarId: 'cal-home', id: 'e1', changes: {start: later, allDay: false}}), /end after it starts/);
  await assert.rejects(call('modifyEvent', {calendarId: 'cal-home', id: 'e1', changes: {shift: 60, length: 60}}), /whole repeating event/);
  await assert.rejects(call('modifyEvent', {calendarId: 'cal-home', id: 'e1', changes: {notes: 'x'.repeat(4001)}}), /too long/);
  await assert.rejects(call('modifyEvent', {calendarId: 'cal-home', id: 'e1', changes: {color: 'red'}}), /Nothing was changed/);
  await assert.rejects(call('modifyEvent', {calendarId: '', id: 'e1', changes: {title: 'X'}}), /not available/);
  assert.equal(state.calendarCalls.length, asked);
  // Thunderbird's answers, said plainly.
  await assert.rejects(call('modifyEvent', {calendarId: 'cal-holidays', id: 'e2', changes: {title: 'X'}}), /cannot be changed, so the event was not changed/);
  await assert.rejects(call('modifyEvent', {calendarId: 'cal-home', id: 'gone', changes: {title: 'X'}}), /no longer in the calendar/);
  await assert.rejects(call('modifyEvent', {calendarId: 'cal-home', id: 'e1', changes: {calendarId: 'cal-holidays'}}), /cannot be changed/);
  // A repeating event: one day, or all of them with a new start time and length on the same day.
  // (The checks above deleted it; put two of its days back.)
  for (const [day, occurrence] of [[27, '20261027T140000Z'], [3, '20261103T140000Z']]) {
    const begins = Date.UTC(2026, day === 3 ? 10 : 9, day, 14);
    state.events.push({id: 'e3', calendarId: 'cal-google', calendarName: 'me@gmail.com', title: 'Group meeting', location: '', notes: '',
      allDay: false, start: begins, end: begins + 3600000, recurring: true, occurrence});
  }
  await call('modifyEvent', {calendarId: 'cal-google', id: 'e3', occurrence: '20261027T140000Z', changes: {title: 'Team'}});
  assert.deepEqual(state.events.filter(e => e.id === 'e3').map(e => e.title), ['Team', 'Group meeting']);
  await call('modifyEvent', {calendarId: 'cal-google', id: 'e3', all: true, changes: {shift: 60, length: 30}});
  assert.deepEqual(state.calendarCalls.at(-1).modify, {calendarId: 'cal-google', id: 'e3', occurrence: '', all: true, changes: {shift: 60, length: 30}});
  await assert.rejects(call('modifyEvent', {calendarId: 'cal-google', id: 'e3', all: true, changes: {shift: 1440}}), /same day/);
  await assert.rejects(call('modifyEvent', {calendarId: 'cal-google', id: 'e3', occurrence: '20261027T140000Z', changes: {calendarId: 'cal-home'}}),
    /whole repeating event/);
  // An add-on copy from before editing answers "unsupported", which Voice Companion turns into update advice.
  delete api.voiceCompanionCalendar.modifyEvent;
  await assert.rejects(call('modifyEvent', {calendarId: 'cal-google', id: 'e3', changes: {title: 'X'}}), /calendar is not available|Close Thunderbird/);

  // An add-on without the calendar Experiment says what to do.
  delete api.voiceCompanionCalendar;
  await assert.rejects(call('calendars'), /calendar is not available\. Close Thunderbird, then try again/);
}

// ---- experiment/calendar.js against a stand-in for Thunderbird's calendar manager ----

function fakeCalendarManager() {
  const UTC = {tzid: 'UTC', isUTC: true, isFloating: false}, local = {tzid: 'America/Chicago', isFloating: false};
  const paris = {tzid: 'Europe/Paris', isFloating: false};
  // calIDateTime: month counts from 0; an eight-digit value is a date (all-day). addDuration changes it in place.
  const dateTime = (ms, isDate = false, timezone = UTC) => {
    const d = new Date(ms);
    return {isDate, year: d.getUTCFullYear(), month: d.getUTCMonth(), day: d.getUTCDate(), nativeTime: ms * 1000, ms, timezone, zone: timezone,
      getInTimezone(zone) { return {icalString: new Date(ms).toISOString().replace(/[-:]/g, '').replace(/\.\d+Z$/, 'Z')}; },
      clone() { return dateTime(this.ms, this.isDate, this.timezone); },
      addDuration(duration) {
        Object.assign(this, dateTime(this.ms + (this.isDate ? duration.days * 86400000 : duration.inSeconds * 1000), this.isDate, this.timezone));
      }};
  };
  const cal = {
    createDateTime(text) {
      const m = /^(\d{4})(\d{2})(\d{2})(?:T(\d{2})(\d{2})(\d{2})Z)?$/.exec(text);
      if (!m) throw new Error('bad date ' + text);
      return dateTime(Date.UTC(+m[1], m[2] - 1, +m[3], +(m[4] || 0), +(m[5] || 0), +(m[6] || 0)), !m[4]);
    },
    // calIDuration: whole days, or seconds (negative allowed).
    createDuration: () => ({days: 0, inSeconds: 0}),
    dtz: {dateTimeToJsDate: dt => new Date(dt.ms), jsDateToDateTime: (date, zone) => dateTime(date.getTime(), false, zone),
      defaultTimezone: local},
    timezoneService: {UTC},
    getUUID: () => 'uuid-1',
    calls: [],
  };
  // calIItemBase: clone() gives a changeable copy; the original stays as Thunderbird gave it.
  const item = (props) => ({...props, properties: {...(props.properties || {})},
    getProperty(name) { return this.properties[name] || null; },
    setProperty(name, value) { this.properties[name] = value; },
    deleteProperty(name) { delete this.properties[name]; },
    clone() {
      cal.calls.push({clone: this.id});
      const copy = item({...this, startDate: this.startDate && this.startDate.clone(), endDate: this.endDate && this.endDate.clone()});
      if (this.recurrenceInfo) copy.recurrenceInfo = this.recurrenceInfo.cloneFor(copy);
      return copy;
    }});
  // calIRecurrenceInfo, for the series it belongs to.
  const recurrence = base => ({
    getOccurrenceFor(rid) {
      return item({id: base.id, title: base.title, startDate: dateTime(rid.ms), endDate: dateTime(rid.ms + 3600000),
        recurrenceId: Object.assign(dateTime(rid.ms), {found: true}), parentItem: base});
    },
    removeOccurrenceAt(rid) { cal.calls.push({removeOccurrenceAt: rid}); },
    modifyException(occurrence, owner) { cal.calls.push({modifyException: {item: occurrence, owner}}); },
    cloneFor(copy) { return recurrence(copy); },
  });
  const series = item({id: 'series', title: 'Walk', startDate: dateTime(Date.UTC(2026, 9, 26, 13)), endDate: dateTime(Date.UTC(2026, 9, 26, 14))});
  series.recurrenceInfo = recurrence(series);
  const single = item({id: 'lunch', title: 'Lunch', startDate: dateTime(Date.UTC(2026, 9, 30, 11), false, paris),
    endDate: dateTime(Date.UTC(2026, 9, 30, 12), false, paris), properties: {LOCATION: 'Cafe', DESCRIPTION: 'Old notes'}});
  const occurrence = item({id: 'series', title: 'Walk', startDate: dateTime(Date.UTC(2026, 9, 27, 13)), endDate: dateTime(Date.UTC(2026, 9, 27, 14)),
    recurrenceId: dateTime(Date.UTC(2026, 9, 27, 13)), parentItem: series});
  const allDay = item({id: 'trip', title: 'Trip', startDate: dateTime(Date.UTC(2026, 9, 27), true), endDate: dateTime(Date.UTC(2026, 9, 29), true),
    properties: {LOCATION: 'Austin', DESCRIPTION: 'Pack light'}});
  const makeCalendar = (id, name, type, readOnly, items, disabled = false) => ({
    id, name, type, readOnly, superCalendar: {id}, items,
    getProperty(key) { return key === 'disabled' ? disabled : null; },
    async getItemsAsArray(filter, limit, start, end) {
      cal.calls.push({getItemsAsArray: {filter, limit, start: start.ms, end: end.ms}});
      if (this.broken) throw new Error('offline');
      return this.items;
    },
    async getItem(wanted) { return this.items.find(i => i.id === wanted && !i.recurrenceId) || null; },
    async adoptItem(event) { cal.calls.push({adoptItem: event}); return event; },
    async deleteItem(event) { cal.calls.push({deleteItem: event.id}); },
    async modifyItem(changed, old) { cal.calls.push({modifyItem: [changed, old]}); return changed; },
  });
  const google = makeCalendar('g', 'Google', 'caldav', false, [occurrence, allDay, series, single]);
  const work = makeCalendar('w', 'Work', 'caldav', false, []);
  const off = makeCalendar('o', 'Turned off', 'storage', false, [allDay], true);
  const holidays = makeCalendar('h', 'Holidays', 'ics', true, []);
  const calendars = [google, work, off, holidays];
  cal.manager = {getCalendars: () => calendars, getCalendarById: id => calendars.find(c => c.id === id) || null};
  class CalEvent {
    constructor() { this.properties = {}; }
    setProperty(name, value) { this.properties[name] = value; }
  }
  return {cal, CalEvent, google, work, series, single};
}

function loadCalendarExperiment(fake) {
  const modules = url => (url.includes('calUtils') ? {cal: fake.cal} : url.includes('CalEvent') ? {CalEvent: fake.CalEvent}
    : {ExtensionCommon: {ExtensionAPI: class {}}});
  const context = vm.createContext({
    ChromeUtils: {importESModule: modules},
    Ci: {calICalendar: {ITEM_FILTER_TYPE_EVENT: 8, ITEM_FILTER_CLASS_OCCURRENCES: 16, ITEM_FILTER_COMPLETED_ALL: 3}},
    Object, Date, String, Number, Boolean, Math,
  });
  vm.runInContext(fs.readFileSync(path.join(__dirname, 'experiment', 'calendar.js'), 'utf8'), context);
  const Api = vm.runInContext('voiceCompanionCalendar', context);
  return new Api().getAPI({}).voiceCompanionCalendar;
}

async function experimentChecks() {
  const fake = fakeCalendarManager();
  const api = loadCalendarExperiment(fake);
  // Calendars that are turned on only.
  assert.deepEqual(plain(await api.listCalendars()), [
    {id: 'g', name: 'Google', type: 'caldav', readOnly: false}, {id: 'w', name: 'Work', type: 'caldav', readOnly: false},
    {id: 'h', name: 'Holidays', type: 'ics', readOnly: true}]);

  // Events: the range goes to Thunderbird as UTC; occurrences are asked for (expanded repeating events).
  const start = Date.UTC(2026, 9, 26, 5), end = Date.UTC(2026, 9, 28, 5);
  const answer = plain(await api.listEvents(start, end));
  assert.deepEqual(fake.cal.calls[0], {getItemsAsArray: {filter: 8 | 16 | 3, limit: 0, start, end}});
  const walk = answer.events.find(e => e.title === 'Walk' && e.occurrence);
  assert.deepEqual(walk, {id: 'series', calendarId: 'g', calendarName: 'Google', title: 'Walk', location: '', notes: '', allDay: false,
    start: Date.UTC(2026, 9, 27, 13), end: Date.UTC(2026, 9, 27, 14), recurring: true, occurrence: '20261027T130000Z'});
  const trip = answer.events.find(e => e.title === 'Trip');
  assert.deepEqual([trip.allDay, trip.start, trip.end, trip.location, trip.notes], [true, '2026-10-27', '2026-10-29', 'Austin', 'Pack light']);
  assert.deepEqual(answer.failed, []);
  // A calendar that cannot be read is named; the others still answer.
  fake.google.broken = true;
  const partial = plain(await api.listEvents(start, end));
  assert.deepEqual(partial, {events: [], failed: ['Google']});
  fake.google.broken = false;

  // Creating a timed event: in the computer's time zone, with an id, adopted by the calendar.
  const made = plain(await api.createEvent({calendarId: 'w', title: 'Lunch', allDay: false, start: String(start), end: String(start + 3600000),
    location: 'Cafe', notes: ''}));
  assert.deepEqual(made, {created: true, id: 'uuid-1'});
  const adopted = fake.cal.calls.at(-1).adoptItem;
  assert.equal(adopted.title, 'Lunch');
  assert.equal(adopted.startDate.ms, start);
  assert.equal(adopted.startDate.zone.tzid, 'America/Chicago');
  assert.equal(adopted.properties.LOCATION, 'Cafe');
  assert.equal(adopted.properties.DESCRIPTION, undefined);
  assert.deepEqual(adopted.calendar, {id: 'w'});
  // An all-day event: dates with no time.
  await api.createEvent({calendarId: 'w', title: 'Trip', allDay: true, start: '2026-10-30', end: '2026-10-31'});
  const allDay = fake.cal.calls.at(-1).adoptItem;
  assert.deepEqual([allDay.startDate.isDate, allDay.startDate.day, allDay.endDate.day], [true, 30, 31]);
  assert.deepEqual(plain(await api.createEvent({calendarId: 'h', title: 'X', allDay: true, start: '2026-10-30', end: '2026-10-31'})),
    {created: false, reason: 'readonly'});
  assert.deepEqual(plain(await api.createEvent({calendarId: 'o', title: 'X', allDay: true, start: '2026-10-30', end: '2026-10-31'})),
    {created: false, reason: 'missing'}, 'a turned-off calendar is not written to');

  // Deleting one day of a repeating event: a copy without that day replaces the series.
  assert.deepEqual(plain(await api.deleteEvent('g', 'series', '20261027T130000Z', false)), {deleted: true, all: false});
  const removed = fake.cal.calls.find(c => c.removeOccurrenceAt).removeOccurrenceAt;
  assert.equal(removed.found, true, 'the occurrence Thunderbird knows is removed, with its own time zone');
  assert.deepEqual(fake.cal.calls.at(-1).modifyItem.map(i => i.id), ['series', 'series']);
  assert.deepEqual(plain(await api.deleteEvent('g', 'series', '20261027T130000Z', true)), {deleted: true, all: true});
  assert.deepEqual(fake.cal.calls.at(-1), {deleteItem: 'series'});
  assert.deepEqual(plain(await api.deleteEvent('g', 'trip', '', false)), {deleted: true, all: true});
  assert.deepEqual(plain(await api.deleteEvent('g', 'missing', '', false)), {deleted: false, reason: 'missing'});
  assert.deepEqual(plain(await api.deleteEvent('h', 'x', '', false)), {deleted: false, reason: 'readonly'});

  // Changing a single event: a copy with the changes replaces it, in its own time zone.
  const editing = fakeCalendarManager();
  const edit = loadCalendarExperiment(editing);
  const lunch = editing.single;
  const newStart = Date.UTC(2026, 9, 30, 19);
  assert.deepEqual(plain(await edit.modifyEvent({calendarId: 'g', id: 'lunch', occurrence: '', all: false,
    changes: {title: 'Late lunch', location: '', notes: 'Bring cash', allDay: false, start: String(newStart), end: String(newStart + 1800000)}})),
  {modified: true, id: 'lunch'});
  const [changed, old] = editing.cal.calls.at(-1).modifyItem;
  assert.equal(old, lunch, 'the item Thunderbird gave is the old one');
  assert.notEqual(changed, lunch, 'Thunderbird gets a changed copy');
  assert.deepEqual([changed.title, changed.properties.LOCATION, changed.properties.DESCRIPTION], ['Late lunch', undefined, 'Bring cash']);
  assert.deepEqual([changed.startDate.ms, changed.endDate.ms, changed.startDate.zone.tzid], [newStart, newStart + 1800000, 'Europe/Paris'],
    'an event made in another time zone keeps it');
  assert.equal(lunch.title, 'Lunch', 'the old item is not changed');
  // To all day, as dates with no time.
  await edit.modifyEvent({calendarId: 'g', id: 'lunch', occurrence: '', all: false, changes: {allDay: true, start: '2026-10-30', end: '2026-11-01'}});
  const allDayed = editing.cal.calls.at(-1).modifyItem[0];
  assert.deepEqual([allDayed.startDate.isDate, allDayed.startDate.day, allDayed.endDate.day], [true, 30, 1]);

  // One day of a repeating event: an exception for that day replaces the series, like Thunderbird's "Edit only this occurrence".
  assert.deepEqual(plain(await edit.modifyEvent({calendarId: 'g', id: 'series', occurrence: '20261027T130000Z', all: false,
    changes: {title: 'Long walk'}})), {modified: true, id: 'series'});
  const exception = editing.cal.calls.find(c => c.modifyException).modifyException;
  assert.deepEqual([exception.item.title, exception.item.recurrenceId.ms, exception.owner], ['Long walk', Date.UTC(2026, 9, 27, 13), true]);
  const [newSeries, oldSeries] = editing.cal.calls.at(-1).modifyItem;
  assert.equal(oldSeries, editing.series);
  assert.equal(newSeries.title, 'Walk', 'the rest of the series keeps its name');
  // All of them: the name, and a later start on the same day with a new length, kept in the series' time zone.
  await edit.modifyEvent({calendarId: 'g', id: 'series', occurrence: '', all: true, changes: {title: 'Walk', shift: 90, length: 45}});
  const whole = editing.cal.calls.at(-1).modifyItem[0];
  assert.deepEqual([whole.startDate.ms, whole.endDate.ms], [Date.UTC(2026, 9, 26, 14, 30), Date.UTC(2026, 9, 26, 15, 15)]);
  // Moving a whole event to another calendar: added there first, then removed here, so it is never lost.
  editing.cal.calls.length = 0;
  assert.deepEqual(plain(await edit.modifyEvent({calendarId: 'g', id: 'lunch', occurrence: '', all: false, changes: {calendarId: 'w'}})),
    {modified: true, id: 'lunch'});
  assert.deepEqual(editing.cal.calls.map(c => Object.keys(c)[0]), ['clone', 'adoptItem', 'deleteItem']);
  assert.deepEqual(editing.cal.calls[1].adoptItem.calendar, {id: 'w'});
  // A failed add keeps the event where it was.
  editing.work.adoptItem = async () => { throw new Error('offline'); };
  editing.cal.calls.length = 0;
  assert.deepEqual(plain(await edit.modifyEvent({calendarId: 'g', id: 'lunch', occurrence: '', all: false, changes: {calendarId: 'w'}})),
    {modified: false, reason: 'failed'});
  assert.ok(!editing.cal.calls.some(c => c.deleteItem));
  // Refusals.
  assert.deepEqual(plain(await edit.modifyEvent({calendarId: 'h', id: 'x', occurrence: '', all: false, changes: {title: 'X'}})),
    {modified: false, reason: 'readonly'});
  assert.deepEqual(plain(await edit.modifyEvent({calendarId: 'g', id: 'nope', occurrence: '', all: false, changes: {title: 'X'}})),
    {modified: false, reason: 'missing'});
  assert.deepEqual(plain(await edit.modifyEvent({calendarId: 'g', id: 'lunch', occurrence: '', all: false, changes: {calendarId: 'h'}})),
    {modified: false, reason: 'readonly'});
  assert.deepEqual(plain(await edit.modifyEvent({calendarId: 'g', id: 'series', occurrence: '20261027T130000Z', all: false,
    changes: {calendarId: 'w'}})), {modified: false, reason: 'series'});

  // Thunderbird without its calendar modules: plain answers, never an exception.
  const broken = loadCalendarExperiment({cal: null, CalEvent: null});
  assert.equal(await broken.listCalendars(), null);
  assert.equal(await broken.listEvents(start, end), null);
  assert.deepEqual(plain(await broken.createEvent({calendarId: 'g', title: 'x', allDay: true, start: '2026-10-30', end: '2026-10-31'})),
    {created: false, reason: 'failed'});

  // The manifest declares the calendar Experiment, contacts permission and pim.js, and the schema matches.
  const manifest = JSON.parse(fs.readFileSync(path.join(__dirname, 'manifest.json'), 'utf8'));
  assert.ok(manifest.permissions.includes('addressBooks'));
  assert.deepEqual(manifest.background.scripts, ['background.js', 'pim.js']);
  const declared = manifest.experiment_apis.voiceCompanionCalendar;
  assert.deepEqual(declared.parent.paths, [['voiceCompanionCalendar']]);
  const schema = JSON.parse(fs.readFileSync(path.join(__dirname, declared.schema), 'utf8'));
  assert.equal(schema[0].namespace, 'voiceCompanionCalendar');
  assert.deepEqual(schema[0].functions.map(f => f.name).sort(), Object.keys(api).sort());
  assert.ok(schema[0].functions.every(f => f.async === true && f.type === 'function'));
  const [major, minor] = manifest.version.split('.').map(Number);
  // 1.3.0 added contacts and calendars; 1.4.0 adds changing events and contacts. A higher version replaces installed copies.
  assert.ok(major > 1 || minor >= 4, 'the add-on version was raised so installed copies are replaced');
  // Every file the add-on needs is packaged.
  const packaged = fs.readFileSync(path.join(__dirname, '..', 'build_thunderbird_addon.py'), 'utf8');
  for (const file of ['pim.js', 'experiment/calendar.js', 'experiment/calendar-schema.json']) assert.ok(packaged.includes("'" + file + "'"), file);
}

(async () => {
  await contactChecks();
  await calendarChecks();
  await experimentChecks();
  console.log('Contacts and calendar add-on tests passed.');
})().catch(error => { process.stderr.write(error.stack + '\n'); process.exitCode = 1; });

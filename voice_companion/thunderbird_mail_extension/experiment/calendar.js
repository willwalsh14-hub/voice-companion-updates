/* Voice Companion Mail Connection: Thunderbird's calendars, for Voice Companion's Calendar screen.
 *
 * Thunderbird's add-on interface has no calendar functions. This small Experiment
 * uses Thunderbird's own calendar manager (cal.manager), the same way Thunderbird's
 * official calendar Experiment does (github.com/thunderbird/webext-experiments,
 * calendar/experiments/calendar/parent/ext-calendar-items.js):
 *
 *  - listCalendars: the calendars that are turned on (Home, Google, other CalDAV).
 *  - listEvents: the events between two moments, with repeating events expanded
 *    into one entry per day they happen.
 *  - createEvent: add one event (all-day or timed) to one calendar.
 *  - deleteEvent: delete one event, or one day of a repeating event.
 *  - modifyEvent: change one event, one day of a repeating event, or all of them;
 *    or move an event to another calendar.
 *
 * Times cross this boundary as milliseconds since 1970 (UTC). All-day events
 * cross as YYYY-MM-DD dates, because they have no time zone. Thunderbird syncs
 * with Google and other servers by itself. Nothing is logged.
 *
 * Keep this file thin: it cannot be tested against real Thunderbird outside
 * Windows. Every function catches its own errors and answers plainly.
 */
'use strict';

var { ExtensionCommon } = ChromeUtils.importESModule('resource://gre/modules/ExtensionCommon.sys.mjs');

// Loaded on first use, so a Thunderbird without its calendar answers "unavailable", not an error at startup.
function modules() {
  const { cal } = ChromeUtils.importESModule('resource:///modules/calendar/calUtils.sys.mjs');
  const { CalEvent } = ChromeUtils.importESModule('resource:///modules/CalEvent.sys.mjs');
  return { cal, CalEvent };
}

const pad = (n, size = 2) => String(n).padStart(size, '0');

// 1761436800000 -> "20251026T000000Z", the iCalendar UTC form cal.createDateTime reads.
function utcText(ms) {
  const d = new Date(ms);
  return d.getUTCFullYear() + pad(d.getUTCMonth() + 1) + pad(d.getUTCDate()) + 'T' +
    pad(d.getUTCHours()) + pad(d.getUTCMinutes()) + pad(d.getUTCSeconds()) + 'Z';
}

// calIDateTime -> milliseconds (UTC). A floating time is read as this computer's local time.
function millis(cal, dt) {
  if (cal.dtz && typeof cal.dtz.dateTimeToJsDate === 'function') return cal.dtz.dateTimeToJsDate(dt).getTime();
  return Math.floor(dt.nativeTime / 1000);   // nativeTime is microseconds
}

// An all-day date -> "2026-10-26". calIDateTime months count from 0.
function dayText(dt) {
  return pad(dt.year, 4) + '-' + pad(dt.month + 1) + '-' + pad(dt.day);
}

function enabled(cal) {
  return cal.manager.getCalendars().filter(calendar => !calendar.getProperty('disabled'));
}

function describe(cal, item, calendar) {
  const parent = item.parentItem || item;
  const allDay = Boolean(item.startDate && item.startDate.isDate);
  const end = item.endDate || item.startDate;
  return {
    id: String(parent.id || item.id || ''),
    calendarId: String(calendar.id),
    calendarName: String(calendar.name || ''),
    title: String(item.title || ''),
    location: String(item.getProperty('LOCATION') || ''),
    notes: String(item.getProperty('DESCRIPTION') || ''),
    allDay,
    start: allDay ? dayText(item.startDate) : millis(cal, item.startDate),
    end: allDay ? dayText(end) : millis(cal, end),
    recurring: Boolean(item.recurrenceId || parent.recurrenceInfo),
    // Which day of a repeating event this is, in UTC iCalendar form; '' for a single event.
    occurrence: item.recurrenceId ? String(item.recurrenceId.getInTimezone(cal.timezoneService.UTC).icalString) : '',
  };
}

async function listEvents(startMs, endMs) {
  const { cal } = modules();
  const filter = Ci.calICalendar.ITEM_FILTER_TYPE_EVENT | Ci.calICalendar.ITEM_FILTER_CLASS_OCCURRENCES |
    Ci.calICalendar.ITEM_FILTER_COMPLETED_ALL;
  const start = cal.createDateTime(utcText(startMs));
  const end = cal.createDateTime(utcText(endMs));
  const events = [], failed = [];
  for (const calendar of enabled(cal)) {
    try {
      const items = await calendar.getItemsAsArray(filter, 0, start, end);
      for (const item of items || []) {
        try { events.push(describe(cal, item, calendar)); } catch (error) { /* a damaged event is left out */ }
      }
    } catch (error) {
      failed.push(String(calendar.name || ''));   // one calendar that cannot be read must not hide the others
    }
  }
  return { events, failed };
}

function calendarById(cal, id) {
  const calendar = cal.manager.getCalendarById(String(id || ''));
  return calendar && !calendar.getProperty('disabled') ? calendar : null;
}

async function createEvent(details) {
  const { cal, CalEvent } = modules();
  const calendar = calendarById(cal, details.calendarId);
  if (!calendar) return { created: false, reason: 'missing' };
  if (calendar.readOnly) return { created: false, reason: 'readonly' };
  const event = new CalEvent();
  event.id = cal.getUUID();
  event.title = details.title;
  if (details.allDay) {
    // An eight-digit iCalendar value ("20261026") is a date with no time: an all-day event.
    event.startDate = cal.createDateTime(details.start.replace(/-/g, ''));
    event.endDate = cal.createDateTime(details.end.replace(/-/g, ''));
  } else {
    // Stored in this computer's time zone, as Thunderbird does for events made in its own window.
    const zone = cal.dtz.defaultTimezone;
    event.startDate = cal.dtz.jsDateToDateTime(new Date(Number(details.start)), zone);
    event.endDate = cal.dtz.jsDateToDateTime(new Date(Number(details.end)), zone);
  }
  if (details.location) event.setProperty('LOCATION', details.location);
  if (details.notes) event.setProperty('DESCRIPTION', details.notes);
  event.calendar = calendar.superCalendar;
  const created = await calendar.adoptItem(event);
  return { created: true, id: String((created && created.id) || event.id) };
}

async function deleteEvent(calendarId, id, occurrence, all) {
  const { cal } = modules();
  const calendar = calendarById(cal, calendarId);
  if (!calendar) return { deleted: false, reason: 'missing' };
  if (calendar.readOnly) return { deleted: false, reason: 'readonly' };
  const item = await calendar.getItem(id);
  if (!item) return { deleted: false, reason: 'missing' };
  if (all || !occurrence || !item.recurrenceInfo) {
    await calendar.deleteItem(item);
    return { deleted: true, all: true };
  }
  // One day of a repeating event: what Thunderbird's "Delete only this occurrence" does.
  const wanted = cal.createDateTime(occurrence);
  const found = item.recurrenceInfo.getOccurrenceFor(wanted);
  const changed = item.clone();
  changed.recurrenceInfo.removeOccurrenceAt(found ? found.recurrenceId : wanted);
  await calendar.modifyItem(changed, item);
  return { deleted: true, all: false };
}

// A calIDuration: whole days for an all-day date, else seconds (negative moves earlier).
function duration(cal, seconds, isDate) {
  const value = cal.createDuration();
  if (isDate) value.days = Math.round(seconds / 86400);
  else value.inSeconds = seconds;
  return value;
}

// Put the changes on a changeable copy of an event (or one day of a repeating event).
function applyChanges(cal, item, changes) {
  if (typeof changes.title === 'string') item.title = changes.title;
  for (const [key, property] of [['location', 'LOCATION'], ['notes', 'DESCRIPTION']]) {
    if (typeof changes[key] !== 'string') continue;
    if (changes[key]) item.setProperty(property, changes[key]);
    else item.deleteProperty(property);
  }
  if (typeof changes.start === 'string') {
    if (changes.allDay) {
      item.startDate = cal.createDateTime(changes.start.replace(/-/g, ''));
      item.endDate = cal.createDateTime(changes.end.replace(/-/g, ''));
    } else {
      // The event keeps its own time zone; a floating or all-day one gets this computer's.
      const old = item.startDate;
      const zone = old && !old.isDate && old.timezone && !old.timezone.isFloating ? old.timezone : cal.dtz.defaultTimezone;
      item.startDate = cal.dtz.jsDateToDateTime(new Date(Number(changes.start)), zone);
      item.endDate = cal.dtz.jsDateToDateTime(new Date(Number(changes.end)), zone);
    }
  }
  if (Number.isInteger(changes.shift) || Number.isInteger(changes.length)) {
    // A whole repeating event: the first day moves by the same minutes (in its own time zone,
    // so daylight saving does not change the hour), and the rule makes every other day from it.
    const start = item.startDate.clone();
    if (changes.shift) start.addDuration(duration(cal, changes.shift * 60, start.isDate));
    const minutes = Number.isInteger(changes.length) ? changes.length
      : Math.round((millis(cal, item.endDate) - millis(cal, item.startDate)) / 60000);
    const end = start.clone();
    end.addDuration(duration(cal, minutes * 60, start.isDate));
    const oldStart = item.startDate;
    item.startDate = start;
    item.endDate = end;
    // Days already deleted or changed move too, as when Thunderbird's own window changes the time;
    // otherwise a deleted day would come back at the new time.
    if (changes.shift && item.recurrenceInfo && typeof item.recurrenceInfo.onStartDateChange === 'function') {
      item.recurrenceInfo.onStartDateChange(start, oldStart);
    }
  }
}

async function modifyEvent(details) {
  const { cal } = modules();
  const changes = details.changes || {};
  const calendar = calendarById(cal, details.calendarId);
  if (!calendar) return { modified: false, reason: 'missing' };
  if (calendar.readOnly) return { modified: false, reason: 'readonly' };
  let target = null;
  if (changes.calendarId && changes.calendarId !== details.calendarId) {
    target = calendarById(cal, changes.calendarId);
    if (!target) return { modified: false, reason: 'missing' };
    if (target.readOnly) return { modified: false, reason: 'readonly' };
  }
  const item = await calendar.getItem(details.id);
  if (!item) return { modified: false, reason: 'missing' };
  if (details.occurrence && !details.all && item.recurrenceInfo) {
    // One day of a repeating event: what Thunderbird's "Edit only this occurrence" does.
    if (target) return { modified: false, reason: 'series' };
    const occurrence = item.recurrenceInfo.getOccurrenceFor(cal.createDateTime(details.occurrence));
    if (!occurrence) return { modified: false, reason: 'missing' };
    const changed = occurrence.clone();
    applyChanges(cal, changed, changes);
    const series = item.clone();
    series.recurrenceInfo.modifyException(changed, true);
    await calendar.modifyItem(series, item);
    return { modified: true, id: String(item.id) };
  }
  const changed = item.clone();
  applyChanges(cal, changed, changes);
  if (target) {
    // Another calendar: add it there first, then remove it here, so a failure never loses it.
    changed.calendar = target.superCalendar;
    const added = await target.adoptItem(changed);
    await calendar.deleteItem(item);
    return { modified: true, id: String((added && added.id) || changed.id) };
  }
  await calendar.modifyItem(changed, item);
  return { modified: true, id: String(item.id) };
}

var voiceCompanionCalendar = class extends ExtensionCommon.ExtensionAPI {
  getAPI(context) {
    return {
      voiceCompanionCalendar: {
        async listCalendars() {
          try {
            const { cal } = modules();
            return enabled(cal).map(calendar => ({ id: String(calendar.id), name: String(calendar.name || ''),
              type: String(calendar.type || ''), readOnly: Boolean(calendar.readOnly) }));
          } catch (error) {
            return null;
          }
        },
        async listEvents(startMs, endMs) {
          try { return await listEvents(startMs, endMs); } catch (error) { return null; }
        },
        async createEvent(details) {
          try { return await createEvent(details); } catch (error) { return { created: false, reason: 'failed' }; }
        },
        async deleteEvent(calendarId, id, occurrence, all) {
          try { return await deleteEvent(calendarId, id, occurrence, all); } catch (error) { return { deleted: false, reason: 'failed' }; }
        },
        async modifyEvent(details) {
          try { return await modifyEvent(details); } catch (error) { return { modified: false, reason: 'failed' }; }
        },
      },
    };
  }
};

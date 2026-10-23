/* Voice Companion Mail Connection: Thunderbird's own "add email account" page, driven by voice.
 *
 * Thunderbird's add-on interface can list accounts but cannot add one, open the
 * page that does, or show the sign-in page anywhere but where Thunderbird puts it.
 * This small Experiment adds only that:
 *
 *  - openAccountSetup: open the page (the function Thunderbird's File > New menu
 *    uses) and fill in the name and email address.
 *  - setupPage / setupContinue / fillPassword: read the page's headings, messages
 *    and main button aloud through Voice Companion, press that button, and put a
 *    password typed privately in Voice Companion into the page's password box.
 *    Passwords are never read back, stored or logged.
 *  - captureSignIn / takeSignIn: from Thunderbird 151, Google and Microsoft sign-in
 *    runs "in the web browser": Thunderbird listens on this computer
 *    (http://127.0.0.1:port) and asks Windows to open the sign-in address. While
 *    Voice Companion is adding an account, that address is handed to Voice
 *    Companion instead, which opens it in its own browser (embedded Edge or
 *    Chrome). The sign-in still returns to Thunderbird's own listener; nothing
 *    else about Thunderbird's sign-in changes, and outside that time nothing is
 *    touched.
 *
 * It never reads or changes accounts, passwords or settings, and it logs nothing.
 */
'use strict';

var { ExtensionCommon } = ChromeUtils.importESModule('resource://gre/modules/ExtensionCommon.sys.mjs');

// Thunderbird has renamed this function over the years; the first one present is used.
const OPENERS = ['openAccountSetup', 'openAccountSetupTab', 'AddMailAccount'];
const FILL_TRIES = 30, FILL_WAIT_MS = 200;
const BUTTON_IDS = ['forward', 'continueButton', 'createButton', 'reTestButton'];
const LOOPBACK = /^https?:\/\/(?:127\.0\.0\.1|localhost|\[::1\])(?::\d+)?(?:\/|$)/i;
const MAX_CAPTURE_MS = 30 * 60 * 1000;

function descendants(root, found, depth) {
  if (!root || depth > 12) return found;
  // Account Hub's container keeps its dialog in its own shadow root.
  if (root.shadowRoot) descendants(root.shadowRoot, found, depth + 1);
  for (const element of root.querySelectorAll('*')) {
    found.push(element);
    if (element.shadowRoot) descendants(element.shadowRoot, found, depth + 1);
  }
  return found;
}

const visible = element => element.getClientRects().length > 0 && !element.hidden;
const text = element => String(element.textContent || '').replace(/\s+/g, ' ').trim();

function mailWindow() {
  return Services.wm.getMostRecentWindow('mail:3pane');
}

// The account setup page: the old page in a tab, or Account Hub inside the main window.
function setupRoots(win) {
  const roots = [];
  const tabmail = win.document.getElementById('tabmail');
  for (const info of (tabmail && tabmail.tabInfo) || []) {
    try {
      const doc = info && info.browser && info.browser.contentDocument;
      if (doc && String(doc.documentURI || '').startsWith('about:accountsetup')) roots.push(doc);
    } catch (error) { /* a web page: not ours to touch */ }
  }
  for (const element of win.document.querySelectorAll('*')) {
    if (String(element.localName || '').startsWith('account-hub') && visible(element)) { roots.push(element); break; }
  }
  return roots;
}

function inputs(win, type) {
  for (const root of setupRoots(win)) {
    const found = descendants(root, [], 0).filter(e => e.localName === 'input' &&
      String(e.getAttribute('type') || 'text') === type && visible(e) && !e.disabled);
    if (found.length) return found;
  }
  return [];
}

function setValue(box, value) {
  box.value = value;
  box.dispatchEvent(new box.ownerGlobal.Event('input', {bubbles: true}));
  box.dispatchEvent(new box.ownerGlobal.Event('change', {bubbles: true}));
}

// Fill in the address (and the name, when one is given and the box is empty).
// Never replaces what someone already typed. null: the page is not there yet.
function fill(win, email, name) {
  const [box] = inputs(win, 'email');
  if (!box) return null;
  if (box.value && box.value !== email) return false;
  if (box.value !== email) setValue(box, email);
  if (name) {
    for (const root of setupRoots(win)) {
      const names = descendants(root, [], 0).filter(e => ['realName', 'realname', 'name'].includes(e.id) &&
        e.localName === 'input' && visible(e) && !e.disabled);
      if (names.length) { if (!names[0].value) setValue(names[0], name); break; }
    }
  }
  return true;
}

async function wait(win, ms) {
  await new Promise(resolve => win.setTimeout(resolve, ms));
}

function mainButton(win) {
  for (const root of setupRoots(win)) {
    const buttons = descendants(root, [], 0).filter(e => e.localName === 'button' && visible(e));
    const found = buttons.find(b => BUTTON_IDS.includes(b.id)) ||
      buttons.find(b => /\b(?:button-)?primary\b/.test(String(b.className || '')));
    if (found) return found;
  }
  return null;
}

function page(win) {
  const roots = win ? setupRoots(win) : [];
  if (!roots.length) return {open: false, title: '', notes: [], button: '', canContinue: false, needsPassword: false};
  const elements = descendants(roots[0], [], 0).filter(visible);
  const title = (elements.find(e => /^h[1-3]$/.test(e.localName || '')) || {textContent: ''});
  const notes = [];
  for (const element of elements) {
    const kind = (element.getAttribute('role') || '') + ' ' + String(element.className || '') + ' ' + element.id;
    if (/\balert\b|error|warning|notification|status/i.test(kind)) {
      const said = text(element).slice(0, 200);
      if (said && !notes.includes(said) && notes.length < 5) notes.push(said);
    }
  }
  const button = mainButton(win);
  const passwords = inputs(win, 'password');
  return {open: true, title: text(title).slice(0, 200), notes, button: button ? text(button).slice(0, 60) : '',
    canContinue: Boolean(button && !button.disabled), needsPassword: passwords.some(p => !p.value)};
}

// ---- Sign-in in Voice Companion's browser -------------------------------------

const STORE = Symbol('voiceCompanionRequest');
let armedUntil = 0, captured = null, hooked = null;

function wrap(request) {
  try {
    if (!request || Date.now() >= armedUntil || typeof request.start !== 'function') return request;
    if (typeof request.startLoopbackRedirectListener === 'function' && LOOPBACK.test(String(request.redirectURI || ''))) {
      const start = request.start;
      request.start = function (url) {
        try {
          if (Date.now() < armedUntil && url && /^https:\/\//i.test(String(url.href))) {
            captured = {url: String(url.href), where: 'browser'};
            return true;    // Voice Companion opens it; Thunderbird's own listener still receives the answer.
          }
        } catch (error) { /* fall through to Thunderbird's own way */ }
        return start.call(this, url);
      };
    } else if (typeof request.cancelled === 'function') {
      captured = {url: '', where: 'thunderbird'};   // Thunderbird's own sign-in window: say so, nothing else
    }
  } catch (error) { /* leave Thunderbird's request untouched */ }
  return request;
}

function hook() {
  if (hooked) return true;
  let proto;
  try {
    proto = ChromeUtils.importESModule('resource:///modules/OAuth2.sys.mjs').OAuth2.prototype;
  } catch (error) {
    return false;
  }
  const original = Object.getOwnPropertyDescriptor(proto, 'request');
  if (!original || !('value' in original) || !original.configurable) return false;
  const instances = new Set();
  Object.defineProperty(proto, 'request', {
    configurable: true, enumerable: original.enumerable,
    get() { return Object.prototype.hasOwnProperty.call(this, STORE) ? this[STORE] : original.value; },
    set(value) { this[STORE] = wrap(value); instances.add(new WeakRef(this)); },
  });
  hooked = {proto, original, instances};
  return true;
}

function unhook() {
  if (!hooked) return;
  const {proto, original, instances} = hooked;
  hooked = null;
  Object.defineProperty(proto, 'request', original);
  for (const ref of instances) {
    const oauth = ref.deref();
    // Requests still running keep their own value, as Thunderbird stored them.
    if (oauth && Object.prototype.hasOwnProperty.call(oauth, STORE)) {
      Object.defineProperty(oauth, 'request', {value: oauth[STORE], writable: true, configurable: true, enumerable: true});
    }
  }
}

var voiceCompanionSetup = class extends ExtensionCommon.ExtensionAPI {
  onShutdown() {
    armedUntil = 0; captured = null;
    unhook();
  }

  getAPI(context) {
    return {
      voiceCompanionSetup: {
        async openAccountSetup(email, name, show) {
          const win = mailWindow();
          if (!win) return {opened: false, prefilled: false};
          if (show) {
            if (win.windowState === win.STATE_MINIMIZED) win.restore();
            win.focus();
          }
          let opened = false;
          for (const opener of OPENERS) {
            if (typeof win[opener] === 'function') { await win[opener](); opened = true; break; }
          }
          if (!opened && typeof win.openAccountHub === 'function') { await win.openAccountHub('EMAIL'); opened = true; }
          if (!opened) {
            const tabmail = win.document.getElementById('tabmail');
            if (tabmail) { tabmail.openTab('contentTab', {url: 'about:accountsetup'}); opened = true; }
          }
          if (!opened) return {opened: false, prefilled: false};
          let prefilled = false;
          try {
            for (let i = 0; email && i < FILL_TRIES; i++) {
              const result = fill(win, email, name);
              if (result !== null) { prefilled = result; break; }
              await wait(win, FILL_WAIT_MS);
            }
          } catch (error) { prefilled = false; }
          return {opened: true, prefilled};
        },

        async setupPage() {
          try { return page(mailWindow()); } catch (error) { return page(null); }
        },

        async setupContinue() {
          const win = mailWindow();
          const button = win && mainButton(win);
          if (!button || button.disabled) return {pressed: false};
          button.click();
          return {pressed: true};
        },

        async fillPassword(secret) {
          const win = mailWindow();
          const [box] = win ? inputs(win, 'password') : [];
          if (!box || typeof secret !== 'string' || !secret) return {filled: false};
          setValue(box, secret);
          return {filled: true};
        },

        async showSetup() {
          const win = mailWindow();
          if (!win) return {shown: false};
          if (win.windowState === win.STATE_MINIMIZED) win.restore();
          win.focus();
          return {shown: true};
        },

        async captureSignIn(seconds) {
          if (!hook()) return {capturing: false};
          armedUntil = seconds > 0 ? Date.now() + Math.min(seconds * 1000, MAX_CAPTURE_MS) : 0;
          if (!armedUntil) captured = null;
          return {capturing: armedUntil > 0};
        },

        async takeSignIn() {
          const found = captured;
          captured = null;
          return found || {url: '', where: ''};
        },
      },
    };
  }
};

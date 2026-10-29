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
 *  - keepPromptsInView: while Voice Companion keeps Thunderbird off the taskbar, bring up
 *    the window that holds any question Thunderbird asks, so it can be reached.
 *
 *  - getNewMail: ask Thunderbird to check the mail server for one folder now, the
 *    same as when a person opens that folder, so Voice Companion lists the newest
 *    mail. Thunderbird's add-on interface has no way to ask for this.
 *
 * It never reads or changes accounts, passwords or settings, and it logs nothing.
 */
'use strict';

var { ExtensionCommon } = ChromeUtils.importESModule('resource://gre/modules/ExtensionCommon.sys.mjs');

// Thunderbird has renamed this function over the years; the first one present is used.
const OPENERS = ['openAccountSetup', 'openAccountSetupTab', 'AddMailAccount'];
const FILL_TRIES = 75, FILL_WAIT_MS = 200;   // Account Hub can take several seconds to load the first time
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
  // setUserInput (Thunderbird's own code only) acts like typing, so the page checks the form
  // and turns on Continue, as it does for a person. The plain events are the fallback.
  if (typeof box.setUserInput === 'function') {
    box.setUserInput(value);
    if (box.value === value) return;
  }
  box.value = value;
  box.dispatchEvent(new box.ownerGlobal.Event('input', {bubbles: true}));
  box.dispatchEvent(new box.ownerGlobal.Event('change', {bubbles: true}));
}

// Account Hub's email step re-checks itself with checkValidEmailForm; ask it to after filling.
function recheck(box) {
  for (let node = box; node; node = node.parentNode || node.host) {
    if (typeof node.checkValidEmailForm === 'function') {
      try { node.checkValidEmailForm({showErrors: false}); } catch (error) { /* only a courtesy */ }
      return;
    }
  }
}

// Fill in the address (and the name, when one is given and the box is empty).
// Never replaces what someone already typed. null: the page is not there yet.
function fill(win, email, name) {
  const [box] = inputs(win, 'email');
  if (!box) return null;
  if (box.value && box.value !== email) return false;
  const changed = box.value !== email;
  if (changed) setValue(box, email);
  if (name) {
    for (const root of setupRoots(win)) {
      const names = descendants(root, [], 0).filter(e => ['realName', 'realname', 'name'].includes(e.id) &&
        e.localName === 'input' && visible(e) && !e.disabled);
      if (names.length) { if (!names[0].value) setValue(names[0], name); break; }
    }
  }
  if (changed) recheck(box);
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

// What Voice Companion asked to fill in. Account Hub resets its form while it loads, which can
// clear a box just filled, so it is filled again whenever it is found empty, until Continue.
let pending = null;

function fillPending(win) {
  if (!pending || !win) return false;
  try {
    const result = fill(win, pending.email, pending.name);
    if (result === false) pending = null;     // someone typed something else: leave it alone
    return result === true;
  } catch (error) {
    return false;
  }
}

function addressFilled(win) {
  const [box] = inputs(win, 'email');
  return Boolean(box && box.value);
}

function page(win) {
  const roots = win ? setupRoots(win) : [];
  if (!roots.length) return {open: false, title: '', notes: [], button: '', canContinue: false, needsPassword: false, fields: []};
  fillPending(win);
  const elements = descendants(roots[0], [], 0).filter(visible);
  // The last visible heading is the current step's; the first is Thunderbird's own banner.
  const title = elements.filter(e => /^h[1-3]$/.test(e.localName || '')).pop() || {textContent: ''};
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
  // Which boxes are showing, by kind and id only (never their contents), so a problem can be named.
  const fields = elements.filter(e => e.localName === 'input')
    .map(e => String(e.getAttribute('type') || 'text') + (e.id ? '#' + e.id : '')).slice(0, 12);
  return {open: true, title: text(title).slice(0, 200), notes, button: button ? text(button).slice(0, 60) : '',
    canContinue: Boolean(button && !button.disabled), needsPassword: passwords.some(p => !p.value), fields,
    addressFilled: addressFilled(win)};
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

// ---- Checking the server for new mail -------------------------------------------

const INBOX_FLAG = 0x00001000;   // nsMsgFolderFlags.Inbox

function mailServices() {
  return ChromeUtils.importESModule('resource:///modules/MailServices.sys.mjs').MailServices;
}

// A folder's path as Thunderbird's add-on interface gives it ("/INBOX", "/[Gmail]/Sent Mail"):
// the folder address after the account's, each part decoded.
function folderPath(root, folder) {
  const rootURI = String(root.URI || ''), uri = String(folder.URI || '');
  if (uri === rootURI) return '/';
  if (!rootURI || !uri.startsWith(rootURI + '/')) return null;
  try { return uri.slice(rootURI.length).split('/').map(decodeURIComponent).join('/'); } catch (error) { return null; }
}

function findFolder(accountId, path, inbox) {
  const account = mailServices().accounts.getAccount(accountId);
  const server = account && account.incomingServer;
  const root = server && server.rootFolder;
  if (!root) return null;
  for (const folder of root.descendants || []) {
    if (folderPath(root, folder) === path) return {server, folder};
  }
  const found = inbox && root.getFolderWithFlags(INBOX_FLAG);
  return found ? {server, folder: found} : null;
}

// Thunderbird reports success as 0 (NS_OK); failures have the top bit set.
const succeeded = status => (Number(status) >>> 0) < 0x80000000;

// Account types whose mail is kept only on this computer.
const LOCAL_TYPES = ['none', 'rss'];

function getNewMail(accountId, path, inbox) {
  if (Services.io.offline) return Promise.resolve({checked: false, offline: true});
  const found = findFolder(accountId, path, inbox);
  if (!found) return Promise.resolve({checked: false, offline: false});
  const {server, folder} = found;
  return new Promise(resolve => {
    const listener = {
      QueryInterface: ChromeUtils.generateQI(['nsIUrlListener']),
      OnStartRunningUrl() {},
      OnStopRunningUrl(url, status) { resolve({checked: succeeded(status), offline: false}); },
    };
    try {
      if (LOCAL_TYPES.includes(server.type) || (server.type === 'pop3' && !inbox)) {
        resolve({checked: true, offline: false});   // kept on this computer: nothing on a server to fetch
      } else if (server.type === 'pop3') {
        server.getNewMessages(folder, null, listener);
      } else {
        // IMAP, and Microsoft Exchange accounts (ews and newer types): what Thunderbird does
        // when a folder is opened, fetch the server's messages for it. A new account still
        // downloading its mail takes longer than Voice Companion waits, and the list says so.
        folder.updateFolderWithListener(null, listener);
      }
    } catch (error) {
      resolve({checked: false, offline: false});
    }
  });
}

// ---- The outgoing server an identity sends through ------------------------------

// nsMsgSocketType: 0 plain, 1 STARTTLS if offered, 2 STARTTLS always, 3 SSL/TLS.
function outgoingServer(identityKey) {
  const services = mailServices();
  const identity = services.accounts.getIdentity(String(identityKey || ''));
  if (!identity) return null;
  const outgoing = services.outgoingServer || services.smtp;
  const server = outgoing && outgoing.getServerByIdentity(identity);
  if (!server) return null;
  let smtp = server;
  try { smtp = server.QueryInterface(Ci.nsISmtpServer); } catch (error) { /* a newer, non-SMTP kind */ }
  const type = Number(server.socketType);
  const security = type === 3 ? 'ssl' : type === 1 || type === 2 ? 'starttls' : type === 0 ? 'none' : '';
  return {host: String(smtp.hostname || ''), port: Number(smtp.port) || 0, security};
}

// ---- Closing Thunderbird with Voice Companion ------------------------------------

// Voice Companion started Thunderbird for mail, and is closing: close Thunderbird the
// normal way. Never while a message is being written; Thunderbird then stays open.
function quitThunderbird() {
  const composing = Services.wm.getEnumerator('msgcompose');
  if (composing && composing.hasMoreElements()) return {quit: false, reason: 'composing'};
  Services.tm.dispatchToMainThread(() => Services.startup.quit(Ci.nsIAppStartup.eAttemptQuit));
  return {quit: true};
}

// ---- Questions kept in view ("Hidden from the taskbar" in Voice Companion) --------------

// Voice Companion keeps Thunderbird's minimized main window off the taskbar. A question
// Thunderbird shows inside that window (a password prompt, an add-on permission) would then
// be out of reach. While this is on, the window holding any question is brought up (never
// given the keyboard: Voice Companion does that, and says what it is). Windows writing a
// message, which the add-on opens itself to send, are left alone.
const PROMPT_TOPICS = ['common-dialog-loaded', 'tabmodal-dialog-loaded', 'webextension-permission-prompt'];
const QUIET_WINDOWS = ['msgcompose', 'alert:alert'];
let promptWatch = null;

function bringUp(win) {
  if (win && win.windowState === win.STATE_MINIMIZED && typeof win.restore === 'function') win.restore();
}

function holdingWindow(subject) {
  try {
    const top = subject && subject.browsingContext && subject.browsingContext.topChromeWindow;
    if (top) return top;
  } catch (error) { /* a closed prompt */ }
  return mailWindow();
}

function keepPromptsInView(on) {
  if (on && !promptWatch) {
    promptWatch = {
      observe(subject, topic) {
        try {
          if (topic === 'domwindowopened') {
            subject.addEventListener('load', () => {
              try {
                const type = subject.document.documentElement.getAttribute('windowtype') || '';
                if (!QUIET_WINDOWS.includes(type)) bringUp(mailWindow());
              } catch (error) { /* only a courtesy */ }
            }, {once: true});
            return;
          }
          bringUp(holdingWindow(topic === 'webextension-permission-prompt' ? null : subject));
        } catch (error) { /* never stop Thunderbird */ }
      },
    };
    for (const topic of PROMPT_TOPICS) Services.obs.addObserver(promptWatch, topic);
    Services.ww.registerNotification(promptWatch);
  } else if (!on && promptWatch) {
    for (const topic of PROMPT_TOPICS) {
      try { Services.obs.removeObserver(promptWatch, topic); } catch (error) { /* already gone */ }
    }
    try { Services.ww.unregisterNotification(promptWatch); } catch (error) { /* already gone */ }
    promptWatch = null;
  }
  return {watching: Boolean(promptWatch)};
}

var voiceCompanionSetup = class extends ExtensionCommon.ExtensionAPI {
  onShutdown() {
    armedUntil = 0; captured = null; pending = null;
    unhook();
    keepPromptsInView(false);
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
          let prefilled = false, steady = 0;
          pending = email ? {email, name} : null;
          try {
            // Filled, and still filled a moment later (after the hub's own reset).
            for (let i = 0; pending && i < FILL_TRIES && steady < 5; i++) {
              fillPending(win);
              prefilled = Boolean(pending) && addressFilled(win);
              steady = prefilled ? steady + 1 : 0;
              await wait(win, FILL_WAIT_MS);
            }
            prefilled = Boolean(pending) && addressFilled(win);
          } catch (error) { prefilled = false; }
          return {opened: true, prefilled};
        },

        async setupPage() {
          try { return page(mailWindow()); } catch (error) { return page(null); }
        },

        async setupContinue() {
          const win = mailWindow();
          fillPending(win);
          const button = win && mainButton(win);
          if (!button || button.disabled) return {pressed: false};
          pending = null;        // past the first step: nothing more to fill in
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

        async outgoingServer(identityKey) {
          try { return outgoingServer(identityKey); } catch (error) { return null; }
        },

        async keepPromptsInView(on) {
          try { return keepPromptsInView(Boolean(on)); } catch (error) { return {watching: false}; }
        },

        async quitThunderbird() {
          try { return quitThunderbird(); } catch (error) { return {quit: false, reason: 'error'}; }
        },

        async getNewMail(accountId, path, inbox) {
          try { return await getNewMail(accountId, path, Boolean(inbox)); } catch (error) { return {checked: false, offline: false}; }
        },
      },
    };
  }
};

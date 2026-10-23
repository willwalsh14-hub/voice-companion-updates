/* Voice Companion Mail Connection: open Thunderbird's own "add email account" page.
 *
 * Thunderbird's add-on interface can list accounts but cannot add one or open
 * the page that does. This small Experiment does only that, using the same
 * function Thunderbird's own File > New menu uses, and fills in the email
 * address box when it can find it. It never reads or changes accounts,
 * passwords or settings, and it logs nothing.
 */
'use strict';

var { ExtensionCommon } = ChromeUtils.importESModule('resource://gre/modules/ExtensionCommon.sys.mjs');

// Thunderbird has renamed this function over the years; the first one present is used.
const OPENERS = ['openAccountSetup', 'openAccountSetupTab', 'AddMailAccount'];
const FILL_TRIES = 30, FILL_WAIT_MS = 200;

function emailBoxes(root, found, depth) {
  if (!root || depth > 12 || found.length > 20) return;
  for (const input of root.querySelectorAll('input[type="email"]')) found.push(input);
  for (const element of root.querySelectorAll('*')) {
    if (element.shadowRoot) emailBoxes(element.shadowRoot, found, depth + 1);
  }
}

function documents(win) {
  const list = [win.document];
  const tabmail = win.document.getElementById('tabmail');
  const browser = tabmail && tabmail.currentTabInfo && tabmail.currentTabInfo.browser;
  try {
    // about:accountsetup runs in Thunderbird's own process, so its page can be reached.
    if (browser && browser.contentDocument) list.unshift(browser.contentDocument);
  } catch (error) { /* a web page: not ours to touch */ }
  return list;
}

function fill(win, email) {
  for (const doc of documents(win)) {
    const found = [];
    emailBoxes(doc, found, 0);
    const box = found.find(input => input.getClientRects().length && !input.disabled);
    if (!box) continue;
    if (box.value && box.value !== email) return false;  // never overwrite what someone typed
    if (box.value !== email) {
      box.value = email;
      box.dispatchEvent(new box.ownerGlobal.Event('input', {bubbles: true}));
      box.dispatchEvent(new box.ownerGlobal.Event('change', {bubbles: true}));
    }
    return true;
  }
  return null;  // not there yet
}

async function prefill(win, email) {
  for (let i = 0; i < FILL_TRIES; i++) {
    const result = fill(win, email);
    if (result !== null) return result;
    await new Promise(resolve => win.setTimeout(resolve, FILL_WAIT_MS));
  }
  return false;
}

var voiceCompanionSetup = class extends ExtensionCommon.ExtensionAPI {
  getAPI(context) {
    return {
      voiceCompanionSetup: {
        async openAccountSetup(email) {
          const win = Services.wm.getMostRecentWindow('mail:3pane');
          if (!win) return {opened: false, prefilled: false};
          if (win.windowState === win.STATE_MINIMIZED) win.restore();
          win.focus();
          let opened = false;
          for (const name of OPENERS) {
            if (typeof win[name] === 'function') { await win[name](); opened = true; break; }
          }
          if (!opened && typeof win.openAccountHub === 'function') { await win.openAccountHub('EMAIL'); opened = true; }
          if (!opened) {
            const tabmail = win.document.getElementById('tabmail');
            if (tabmail) { tabmail.openTab('contentTab', {url: 'about:accountsetup'}); opened = true; }
          }
          if (!opened) return {opened: false, prefilled: false};
          let prefilled = false;
          try { prefilled = email ? await prefill(win, email) : false; } catch (error) { prefilled = false; }
          return {opened: true, prefilled};
        },
      },
    };
  }
};

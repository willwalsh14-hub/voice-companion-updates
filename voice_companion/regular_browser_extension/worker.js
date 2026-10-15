// Experimental supported Chrome APIs. No browser launch flags, fingerprint
// changes, cookie access, page-to-extension messages, or network server.
let tabId = null;
let current = null;
let busy = false;
let nativePort = null;
let companionAction = false;
let spokenText = [];
const roles = new Set(['button', 'link', 'textbox', 'combobox', 'listbox', 'radio', 'checkbox', 'switch']);
const send = (method, params = {}) => chrome.debugger.sendCommand({tabId}, method, params);
const value = (node, name) => node.properties?.find(p => p.name === name)?.value?.value;

async function tree() {
  const result = await send('Accessibility.getFullAXTree');
  return result.nodes.filter(n => !n.ignored);
}

async function speak(nodes) {
  if (companionAction) { spokenText.push(...nodes.map(describe).filter(Boolean)); return; }
  // Exclude field values, URLs, descriptions and unrelated AX properties.
  const safeNodes = nodes.slice(0, 5000).map(n => ({
    ignored: n.ignored, role: n.role, name: n.name,
    properties: (n.properties || []).filter(p => ['checked', 'disabled', 'level'].includes(p.name))
  }));
  const result = await chrome.runtime.sendNativeMessage('com.voicecompanion.browser', {protocol: 1, command: 'speak', nodes: safeNodes});
  if (!result?.ok) throw new Error(result?.error || 'Voice Companion speech is unavailable.');
}

async function run(command) {
  if (command === 'connect') {
    const [tab] = await chrome.tabs.query({active: true, currentWindow: true});
    if (!tab?.id || !/^https?:\/\//i.test(tab.url || '')) throw new Error('Open a website in a regular browser tab first.');
    if (tabId !== null && tabId !== tab.id) await chrome.debugger.detach({tabId});
    if (tabId !== tab.id) await chrome.debugger.attach({tabId: tab.id}, '1.3');
    tabId = tab.id; current = null;
    startConnection();
    return 'Connected to Voice Companion. You may close this popup and return to the app.';
  }
  if (tabId === null) throw new Error('Choose Connect this tab first.');
  if (command === 'disconnect') {
    await chrome.debugger.detach({tabId}); tabId = null; current = null;
    return 'Disconnected. Your browser remains open.';
  }
  const nodes = await tree();
  if (command === 'read') { await speak(nodes); return 'Page reading finished.'; }
  if (command === 'heading' || command === 'previousHeading') {
    const headings = nodes.filter(n => n.role?.value === 'heading' && n.backendDOMNodeId);
    const index = headings.findIndex(n => n.backendDOMNodeId === current);
    const target = index < 0 ? (command === 'heading' ? 0 : headings.length - 1) : index + (command === 'heading' ? 1 : -1);
    if (target < 0 || target >= headings.length) return command === 'heading' ? 'End of headings.' : 'Start of headings.';
    current = headings[target].backendDOMNodeId;
    await speak([headings[target]]); return 'Heading read.';
  }
  const controls = nodes.filter(n => roles.has(n.role?.value) && n.backendDOMNodeId && value(n, 'disabled') !== true);
  if (['next', 'previous', 'first', 'last'].includes(command)) {
    const index = controls.findIndex(n => n.backendDOMNodeId === current);
    const target = command === 'first' ? 0 : command === 'last' ? controls.length - 1 : index < 0 ? (command === 'next' ? 0 : controls.length - 1) : index + (command === 'next' ? 1 : -1);
    if (target < 0 || target >= controls.length) return command === 'next' ? 'End of controls.' : 'Start of controls.';
    current = controls[target].backendDOMNodeId;
    await send('DOM.focus', {backendNodeId: current});
    await speak([controls[target]]);
    return (target === 0 ? 'Start of controls. ' : '') + (target === controls.length - 1 ? 'End of controls. ' : '') + 'Control read.';
  }
  if (command !== 'activate' && command !== 'space') throw new Error('Unknown browser action.');
  const node = controls.find(n => n.backendDOMNodeId === current);
  if (!node) throw new Error('Choose Next control first. The page may have changed.');
  await send('DOM.focus', {backendNodeId: current});
  // A single native key sequence, with no click retry or forced state change.
  const space = command === 'space' || ['radio', 'checkbox', 'switch'].includes(node.role?.value);
  const key = space ? ' ' : 'Enter', code = space ? 'Space' : 'Enter', keyCode = space ? 32 : 13;
  await send('Input.dispatchKeyEvent', {type: 'keyDown', key, code, windowsVirtualKeyCode: keyCode});
  await send('Input.dispatchKeyEvent', {type: 'keyUp', key, code, windowsVirtualKeyCode: keyCode});
  await new Promise(resolve => setTimeout(resolve, 100));
  const updated = (await tree()).find(n => n.backendDOMNodeId === current);
  if (updated) await speak([updated]);
  else current = null;
  return 'Action sent. Read page to hear any new content.';
}

chrome.debugger.onDetach.addListener(source => {
  if (source.tabId === tabId) { tabId = null; current = null; }
});

chrome.runtime.onMessage.addListener((message, sender, respond) => {
  // Only our own popup may request actions, never website content scripts.
  if (sender.id !== chrome.runtime.id || sender.url !== chrome.runtime.getURL('popup.html')) return false;
  if (busy) { respond({ok: false, error: 'Wait for the current reading or action to finish.'}); return false; }
  busy = true;
  run(message.command).then(message => respond({ok: true, message}), error => respond({ok: false, error: error.message || 'The browser connection failed.'})).finally(() => { busy = false; });
  return true;
});


function describe(node) {
  const role = node.role?.value, name = String(node.name?.value || '').trim();
  if (!name || node.ignored) return '';
  if (['radio','checkbox','switch','menuitemradio','menuitemcheckbox'].includes(role)) {
    const state = value(node, 'checked');
    return name + ', ' + (role === 'radio' ? 'radio button' : role) + ', ' +
      (state === true || state === 'true' ? 'checked' : state === false || state === 'false' ? 'not checked' : state === 'mixed' ? 'mixed' : 'state unavailable') + '.';
  }
  if (role === 'heading') return name + ', heading level ' + (value(node, 'level') || 'unknown') + '.';
  if (roles.has(role)) return name + ', ' + (role === 'textbox' ? 'edit field' : role) + '.';
  return ['StaticText','RootWebArea','alert','status'].includes(role) ? name : '';
}

function startConnection() {
  if (nativePort || !chrome.runtime.connectNative) return;
  const port = chrome.runtime.connectNative('com.voicecompanion.browser');
  nativePort = port;
  port.onDisconnect.addListener(() => { nativePort = null; });
  port.onMessage.addListener(async message => {
    let result = '';
    if (message.action && !busy) {
      busy = true; companionAction = true; spokenText = [];
      try {
        const status = await run(message.action);
        result = spokenText.join('\n').slice(0,60000) + '\n' + status;
      } catch (error) { result = error.message || 'Browser action failed.'; }
      finally { busy = false; companionAction = false; }
    } else if (message.action) result = 'The browser is busy. No action was sent.';
    setTimeout(() => {
      if (nativePort === port) port.postMessage({protocol:1, command:'poll', connected:tabId !== null, result});
    }, 250);
  });
  port.postMessage({protocol:1, command:'poll', connected:tabId !== null});
}

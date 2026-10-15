// Experimental supported Chrome APIs. No browser launch flags, fingerprint
// changes, cookie access, page-to-extension messages, or network server.
let tabId = null;
let current = null;
let busy = false;
const roles = new Set(['button', 'link', 'textbox', 'combobox', 'listbox', 'radio', 'checkbox', 'switch']);
const send = (method, params = {}) => chrome.debugger.sendCommand({tabId}, method, params);
const value = (node, name) => node.properties?.find(p => p.name === name)?.value?.value;

async function tree() {
  const result = await send('Accessibility.getFullAXTree');
  return result.nodes.filter(n => !n.ignored);
}

async function speak(nodes) {
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
    return 'Connected. Choose Read page or Next control.';
  }
  if (tabId === null) throw new Error('Choose Connect this tab first.');
  if (command === 'disconnect') {
    await chrome.debugger.detach({tabId}); tabId = null; current = null;
    return 'Disconnected. Your browser remains open.';
  }
  const nodes = await tree();
  if (command === 'read') { await speak(nodes); return 'Page reading finished.'; }
  const controls = nodes.filter(n => roles.has(n.role?.value) && n.backendDOMNodeId && value(n, 'disabled') !== true);
  if (command === 'next' || command === 'previous') {
    const index = controls.findIndex(n => n.backendDOMNodeId === current);
    const target = index < 0 ? (command === 'next' ? 0 : controls.length - 1) : index + (command === 'next' ? 1 : -1);
    if (target < 0 || target >= controls.length) return command === 'next' ? 'End of controls.' : 'Start of controls.';
    current = controls[target].backendDOMNodeId;
    await send('DOM.focus', {backendNodeId: current});
    await speak([controls[target]]);
    return 'Control read.';
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

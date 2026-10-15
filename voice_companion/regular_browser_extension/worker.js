importScripts('cdp.js');
let connection = null;
let model = null;
let busy = false;

async function disconnect() {
  const old = connection;connection = null;
  if (old) old.disconnect();
  if (model) await model.detachAll();model = null;
}

async function run(command) {
  if (command === 'status') return model ? 'Connected. Use the Web browser menu in Voice Companion.' : 'Choose Connect this tab to begin.';
  if (command === 'disconnect') { await disconnect();return 'Disconnected. Your browser tabs remain open.'; }
  if (command !== 'connect') throw new Error('Unknown browser connection action.');
  const [tab] = await chrome.tabs.query({active:true,currentWindow:true});
  if (!Number.isInteger(tab?.id) || !/^https?:\/\//i.test(tab.url || '')) throw new Error('Open a website tab first.');
  return connectTab(tab);
}

async function connectTab(tab) {
  await disconnect();
  await chrome.debugger.attach({tabId:tab.id},'1.3');
  const port = chrome.runtime.connectNative('com.voicecompanion.browser');connection = port;
  model = new CompanionCDP(chrome,message=>{if (connection === port)port.postMessage(message);});
  model.remember(tab,true);
  port.onMessage.addListener(message=>{if(connection === port && model)void model.command(message);});
  port.onDisconnect.addListener(()=>{
    if (connection !== port)return;
    connection = null;
    const previous = model;model = null;
    if (previous)void previous.detachAll();
  });
  port.postMessage({protocol:2,command:'cdp',browser:/Edg\//.test(navigator.userAgent)?'edge':navigator.brave?'brave':'chrome'});
  return 'Connected. Return to Voice Companion and open Web browser. All reading, forms, tables, lists and keyboard controls use its usual interface.';
}

chrome.debugger.onEvent.addListener((source,method,params)=>model?.event(source,method,params));
chrome.debugger.onDetach.addListener(source=>model?.removed(source.tabId));
chrome.tabs.onCreated.addListener(tab=>{if(model)void model.created(tab).catch(()=>{});});
chrome.tabs.onRemoved.addListener(tabId=>model?.removed(tabId));
chrome.runtime.onMessage.addListener((message,sender,respond)=>{
  if (sender.id !== chrome.runtime.id || sender.url !== chrome.runtime.getURL('popup.html'))return false;
  if (busy) {respond({ok:false,error:'Wait for the current connection action.'});return false;}
  busy = true;
  run(message.command).then(message=>respond({ok:true,message}),()=>respond({ok:false,error:'The connection could not be completed. Open Web browser in Voice Companion, then reconnect this tab.'})).finally(()=>{busy = false;});
  return true;
});

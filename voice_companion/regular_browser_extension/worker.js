importScripts('cdp.js','downloads.js');
let connection=null, model=null, busy=false, enabled=true, downloads=null;
// Tabs where the user pressed Cancel on the browser's debugging bar. Kept across reconnects;
// cleared when the user chooses Connect in the popup or the tab closes.
const declined=new Set();
const eligible=tab=>Number.isInteger(tab?.id)&&/^https?:\/\//i.test(tab.url||'');
async function disconnect() {
  const old=connection;connection=null;
  if(old)old.disconnect();
  downloads?.stop();downloads=null;
  const previous=model;model=null;if(previous)await previous.detachAll();
}
async function ensureConnection() {
  if(!enabled||connection||busy)return;
  busy=true;
  try {
    const port=chrome.runtime.connectNative('com.voicecompanion.browser');connection=port;
    const current=new CompanionCDP(chrome,message=>{if(connection===port)port.postMessage(message);});
    current.allTabs=true;current.declined=declined;model=current;
    const mirror=chrome.downloads?new CompanionDownloads(chrome,message=>{if(connection===port)port.postMessage(message);}):null;
    downloads?.stop();downloads=mirror;
    port.onMessage.addListener(message=>{
      if(connection!==port)return;
      // Download controls come from Companion, never from a page.
      if(message?.method==='VoiceCompanion.downloadControl'){void mirror?.control(message.params);return;}
      if(message?.method==='VoiceCompanion.checkForUpdate'){void checkForUpdate();return;}
      void current.command(message);
    });
    port.onDisconnect.addListener(()=>{void chrome.runtime.lastError;if(connection!==port)return;connection=null;model=null;mirror?.stop();if(downloads===mirror)downloads=null;void current.detachAll();});
    // Register every normal tab before exposing the connection to the driver.
    const tabs=await chrome.tabs.query({});
    for(const tab of tabs.filter(eligible))if(!declined.has(tab.id))current.remember(tab);
    port.postMessage({protocol:2,command:'cdp',browser:/Edg\//.test(navigator.userAgent)?'edge':navigator.brave?'brave':'chrome',version:chrome.runtime.getManifest?.().version||''});
    void mirror?.sync().catch(()=>{});
  } finally {busy=false;}
}
async function run(command) {
  if(command==='status')return !enabled?'Browser connection paused.':connection?'Browser linked. Use the usual Web browser menu. Sites and tabs connect automatically.':'Waiting for Voice Companion. The browser will reconnect automatically.';
  if(command==='disconnect'){enabled=false;await chrome.storage.local.set({enabled:false});await disconnect();return 'Browser connection paused. Your tabs stay open.';}
  if(command!=='connect')throw new Error('Unknown connection action.');
  enabled=true;declined.clear();await chrome.storage.local.set({enabled:true});await ensureConnection();
  if(model)for(const tab of (await chrome.tabs.query({})).filter(eligible))await model.created(tab).catch(()=>{});
  return run('status');
}
// Companion asks when it ships a newer version than this one. Store copies ask the store now;
// an unpacked copy has no store, and reloading it loads the installer's new files.
async function checkForUpdate(){
  if(!chrome.runtime.getManifest?.().update_url){chrome.runtime.reload();return;}
  try{await chrome.runtime.requestUpdateCheck();}catch(_){}
}
// Chrome installs an update only when the worker is idle, and the Companion link keeps it busy.
// Reloading applies it now; the link comes back on its own.
chrome.runtime.onUpdateAvailable?.addListener(()=>chrome.runtime.reload());
// Kept as a test entry point; production linking never requires a tab action.
async function connectTab(tab){await ensureConnection();if(model&&eligible(tab))await model.created(tab);}
async function startup(){const saved=await chrome.storage.local.get('enabled');enabled=saved.enabled!==false;await chrome.alarms.create('companion-reconnect',{periodInMinutes:0.5});await ensureConnection();}
chrome.alarms.onAlarm.addListener(alarm=>{if(alarm.name==='companion-reconnect')void ensureConnection();});
chrome.runtime.onStartup.addListener(()=>{void startup();});
chrome.runtime.onInstalled.addListener(()=>{void startup();});
chrome.debugger.onEvent.addListener((source,method,params)=>model?.event(source,method,params));
chrome.debugger.onDetach.addListener((source,reason)=>{if(reason==='canceled_by_user')declined.add(source.tabId);model?.removed(source.tabId);});
// Downloads report to Companion while it is linked; on linking, sync() catches up.
if(chrome.downloads){
  chrome.downloads.onCreated.addListener(item=>downloads?.created(item));
  chrome.downloads.onChanged.addListener(delta=>{void downloads?.changed(delta).catch(()=>{});});
}
chrome.tabs.onCreated.addListener(tab=>{if(model)void model.created(tab).catch(()=>{});});
chrome.tabs.onUpdated.addListener((id,change,tab)=>{if(eligible(tab)&&model&&!model.tabs.has(id))void model.created(tab).catch(()=>{});else if(model&&change.url&&/^(chrome|edge|devtools|chrome-extension):/i.test(change.url))model.removed(id);});
chrome.tabs.onRemoved.addListener(id=>{declined.delete(id);model?.removed(id);});
chrome.runtime.onMessage.addListener((message,sender,respond)=>{
  if(sender.id!==chrome.runtime.id||sender.url!==chrome.runtime.getURL('popup.html'))return false;
  run(message.command).then(message=>respond({ok:true,message}),()=>respond({ok:false,error:'Open Voice Companion. The browser will reconnect automatically.'}));return true;
});
void startup();

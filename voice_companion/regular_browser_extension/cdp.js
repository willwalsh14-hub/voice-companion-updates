// A transport adapter, not a second reading/navigation implementation.
// Only the chosen tab and its own new popup tabs are exposed to Companion.
class CompanionCDP {
  constructor(api, emit) {
    this.api = api; this.emit = emit; this.tabs = new Map(); this.children = new Map();
    this.autoAttach = false; this.serial = 0;
  }
  remember(tab, attached = false) {
    if (!Number.isInteger(tab?.id)) throw new Error('No browser tab is available.');
    const prior = this.tabs.get(tab.id);
    if (prior) return prior;
    const item = {tabId:tab.id, attached, sessionId:'vc-tab-'+(++this.serial), announced:false, info:null};
    this.tabs.set(tab.id,item); return item;
  }
  async attach(item) {
    if (!item.attached) { await this.api.debugger.attach({tabId:item.tabId},'1.3'); item.attached = true; }
    const {targetInfo} = await this.api.debugger.sendCommand({tabId:item.tabId},'Target.getTargetInfo');
    item.info = {...targetInfo, attached:true};
    // The connection is restricted to this browser's normal default context.
    delete item.info.browserContextId;
    if (!item.announced) {
      item.announced = true;
      this.emit({method:'Target.attachedToTarget',params:{sessionId:item.sessionId,targetInfo:item.info,waitingForDebugger:false}});
    }
    return item;
  }
  source(sessionId) {
    const item = [...this.tabs.values()].find(t=>t.sessionId === sessionId);
    if (item) return {tabId:item.tabId};
    const tabId = this.children.get(sessionId);
    if (tabId !== undefined) return {tabId,sessionId};
    throw new Error('The selected browser tab or frame disconnected. Reconnect it.');
  }
  event(source, method, params) {
    const item = this.tabs.get(source.tabId);
    if (!item || !item.announced) return;
    if (method === 'Target.attachedToTarget' && params?.sessionId) this.children.set(params.sessionId,source.tabId);
    if (method === 'Target.detachedFromTarget' && params?.sessionId) this.children.delete(params.sessionId);
    this.emit({sessionId:source.sessionId || item.sessionId,method,params});
  }
  removed(tabId) {
    const item = this.tabs.get(tabId);
    if (!item) return;
    this.tabs.delete(tabId);
    for (const [id,owner] of this.children) if (owner === tabId) this.children.delete(id);
    if (item.announced) this.emit({method:'Target.detachedFromTarget',params:{sessionId:item.sessionId,targetId:item.info?.targetId}});
  }
  async created(tab) {
    if (!this.tabs.has(tab.openerTabId)) return; // Never attach unrelated tabs.
    const item = this.remember(tab);
    if (this.autoAttach) await this.attach(item);
  }
  reset() {
    this.autoAttach = false;this.children.clear();
    for (const item of this.tabs.values()) item.announced = false;
  }
  async command(message) {
    const {method,params = {},sessionId} = message;
    if (method === 'VoiceCompanion.reset') { this.reset(); return; }
    if (!Number.isInteger(message.id) || typeof method !== 'string') return;
    try {
      const result = await this.dispatch(method,params,sessionId);
      this.emit({id:message.id,...(sessionId?{sessionId}:{}),result:result || {}});
    } catch (error) {
      // CDP errors can contain a submitted value. Keep error text generic.
      this.emit({id:message.id,...(sessionId?{sessionId}:{}),error:{code:-32000,message:'The browser could not complete '+method+'. The tab may have changed or disconnected.'}});
    }
  }
  async dispatch(method,params,sessionId) {
    if (!sessionId && method === 'Browser.getVersion') {
      const item = this.tabs.values().next().value;
      if (!item) throw new Error('Connect a browser tab first.');
      const reply = await this.api.debugger.sendCommand({tabId:item.tabId},'Runtime.evaluate',{expression:'navigator.userAgent',returnByValue:true});
      const agent = String(reply.result?.value || '');
      const version = /(?:Chrome|Chromium)\/([\d.]+)/.exec(agent)?.[1] || '140.0.0.0';
      return {protocolVersion:'1.3',product:'Chrome/'+version,userAgent:agent,revision:'',jsVersion:''};
    }
    if (!sessionId && method === 'Browser.setDownloadBehavior') return {};
    if (!sessionId && method === 'Target.setAutoAttach') {
      this.autoAttach = !!params.autoAttach;
      if (this.autoAttach) await Promise.all([...this.tabs.values()].map(async item=>{
        await this.api.debugger.sendCommand({tabId:item.tabId},'Target.setAutoAttach',{autoAttach:false,waitForDebuggerOnStart:false,flatten:true});
        return this.attach(item);
      }));
      return {};
    }
    if (!sessionId && method === 'Target.getTargetInfo') {
      return {targetInfo:{targetId:'voice-companion-browser',type:'browser',title:'',url:'',attached:true}};
    }
    if (!sessionId && method === 'Target.createTarget') {
      const url = params.url || 'about:blank';
      if (!/^(https?:\/\/|about:blank$)/i.test(url)) throw new Error('Unsupported tab address.');
      const tab = await this.api.tabs.create({url});
      const item = await this.attach(this.remember(tab));
      return {targetId:item.info.targetId};
    }
    if (!sessionId && method === 'Target.closeTarget') {
      const item = [...this.tabs.values()].find(t=>t.info?.targetId === params.targetId);
      if (!item) return {success:false};
      await this.api.tabs.remove(item.tabId);return {success:true};
    }
    if (!sessionId && method === 'Target.activateTarget') {
      const item = [...this.tabs.values()].find(t=>t.info?.targetId === params.targetId);
      if (!item) throw new Error('Unknown tab.');
      await this.api.tabs.update(item.tabId,{active:true});return {};
    }
    // Browser-global data and settings are not part of reading the chosen tab.
    if (!sessionId) throw new Error('Unsupported browser-level request.');
    if (!/^(Accessibility|Audits|CSS|DOM|DOMDebugger|DOMSnapshot|Debugger|Emulation|Fetch|IO|Input|Inspector|Log|Network|Overlay|Page|Performance|Profiler|Runtime|Security|Target|WebAudio)\./.test(method)) throw new Error('Unsupported page request.');
    if (/^(Network\.(getAllCookies|getCookies|setCookie|setCookies|deleteCookies|clearBrowserCookies)|Storage\.)/.test(method)) throw new Error('Browser account storage is not exposed.');
    return await this.api.debugger.sendCommand(this.source(sessionId),method,params);
  }
  async detachAll() {
    const items = [...this.tabs.values()];this.tabs.clear();this.children.clear();
    await Promise.all(items.filter(t=>t.attached).map(t=>this.api.debugger.detach({tabId:t.tabId}).catch(()=>{})));
  }
}
globalThis.CompanionCDP = CompanionCDP;

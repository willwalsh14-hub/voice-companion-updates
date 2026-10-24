const assert = require('node:assert/strict');
const vm = require('node:vm');
const fs = require('node:fs');
const path = require('node:path');
const replies=[], calls=[], attached=[], removed=[];
const api={debugger:{
  attach:async source=>attached.push(source),detach:async()=>{},
  sendCommand:async(source,method,params)=>{
    calls.push({source,method,params});
    if(method==='Target.getTargetInfo')return {targetInfo:{targetId:'target-'+source.tabId,type:'page',url:'https://fixture.test/',title:'Fixture',browserContextId:'PRIVATE-CONTEXT'}};
    if(method==='Runtime.evaluate')return {result:{value:'Mozilla/5.0 Chrome/140.0.0.0'}};
    return {};
  }},tabs:{create:async()=>({id:9}),remove:async id=>removed.push(id),update:async()=>{}}};
const context=vm.createContext({console});
vm.runInContext(fs.readFileSync(path.join(__dirname,'cdp.js'),'utf8'),context);
const Model=vm.runInContext('CompanionCDP',context);
(async()=>{
 api.tabs.query=async()=>[{id:7,active:true,url:'https://fixture.test/'}];
 const model=new Model(api,message=>replies.push(message));
 model.remember({id:7},true);
 await model.command({id:1,method:'Browser.getVersion'});
 assert.equal(replies.at(-1).result.product,'Chrome/140.0.0.0');
 await model.command({id:2,method:'Target.setAutoAttach',params:{autoAttach:true}});
 const event=replies.find(r=>r.method==='Target.attachedToTarget');
 assert.equal(event.params.targetInfo.targetId,'target-7');
 assert.equal(event.params.targetInfo.browserContextId,'PRIVATE-CONTEXT');
 const sessionId=event.params.sessionId;
 await model.command({id:3,sessionId,method:'DOM.getDocument'});
 assert.equal(calls.at(-1).source.tabId,7);
 assert.ok(!calls.at(-1).source.sessionId);
 model.event({tabId:7},'Target.attachedToTarget',{sessionId:'child-frame',targetInfo:{type:'iframe'}});
 await model.command({id:4,sessionId:'child-frame',method:'Runtime.evaluate',params:{expression:'document.title'}});
 assert.equal(calls.at(-1).source.sessionId,'child-frame');
 model.event({tabId:7,sessionId:'child-frame'},'Runtime.executionContextCreated',{context:{id:22}});
 assert.equal(replies.at(-1).sessionId,'child-frame');
 const count=replies.length;model.event({tabId:99},'Page.frameNavigated',{});assert.equal(replies.length,count);
 await model.created({id:88,openerTabId:99});assert.ok(!model.tabs.has(88));
 await model.created({id:8,openerTabId:7});assert.ok(model.tabs.has(8));assert.equal(attached.at(-1).tabId,8);
 await model.command({id:5,method:'Target.createTarget',params:{url:'https://fixture.test/new'}});
 assert.equal(replies.at(-1).result.targetId,'target-9');
 await model.command({id:6,method:'Target.closeTarget',params:{targetId:'unknown'}});assert.equal(replies.at(-1).result.success,false);
 await model.command({id:7,sessionId,method:'Network.getAllCookies'});assert.ok(replies.at(-1).error);
 await model.command({id:8,method:'Storage.getCookies'});assert.ok(replies.at(-1).error);
 await model.command({id:9,sessionId:'unrelated-tab',method:'DOM.getDocument'});assert.ok(replies.at(-1).error);
 const disableCount=calls.filter(c=>c.method==='Runtime.disable').length;
 model.reset();assert.equal(model.children.size,0);
 await model.command({id:10,method:'Target.setAutoAttach',params:{autoAttach:true}});
 assert.ok(replies.filter(r=>r.method==='Target.attachedToTarget').length>=6);
 assert.ok(calls.filter(c=>c.method==='Runtime.disable').length>disableCount);
 assert.ok(calls.some(c=>c.method==='Page.disable')); 
 await model.command({id:11,method:'Target.attachToBrowserTarget'});
 const browserSession=replies.at(-1).result.sessionId;
 await model.command({id:12,sessionId:browserSession,method:'VoiceCompanion.getActiveTarget'});
 assert.equal(replies.at(-1).result.targetId,'target-7');
 await model.command({id:13,sessionId:browserSession,method:'Target.attachToTarget',params:{targetId:'target-7'}});
 const inspectSession=replies.at(-1).result.sessionId;
 await model.command({id:14,sessionId:inspectSession,method:'Target.getTargetInfo'});
 assert.equal(replies.at(-1).result.targetInfo.targetId,'target-7');
 await model.command({id:15,sessionId:browserSession,method:'Target.detachFromTarget',params:{sessionId:inspectSession}});
 assert.ok(!model.aliases.has(inspectSession));assert.ok(model.tabs.has(7));
 model.allTabs=true;
 await model.created({id:55,url:'https://another.test/'});assert.ok(model.tabs.has(55));
 await model.created({id:56,url:'chrome://settings/'});assert.ok(!model.tabs.has(56));
 await model.detachAll();assert.equal(model.tabs.size,0);
 // Exercise the actual worker's one-time startup, saved pause and retry.
 let saved={},ports=0;
 const listenerStub=()=>({addListener:()=>{}});
 const runtime={id:'fixture',getURL:name=>'extension://fixture/'+name,onStartup:listenerStub(),onInstalled:listenerStub(),onMessage:listenerStub(),
  connectNative:()=>{ports++;return {postMessage:()=>{},disconnect:()=>{},onMessage:listenerStub(),onDisconnect:listenerStub()};}};
 const chrome={...api,runtime,storage:{local:{get:async()=>saved,set:async value=>Object.assign(saved,value)}},
  alarms:{create:async()=>{},onAlarm:listenerStub()}};
 let detached;
 chrome.debugger={...api.debugger,onEvent:listenerStub(),onDetach:{addListener:handler=>{detached=handler;}}};
 let updated;
 chrome.tabs={...api.tabs,onCreated:listenerStub(),onUpdated:{addListener:handler=>{updated=handler;}},onRemoved:listenerStub()};
 // Downloads as chrome.downloads reports them: ids are integers, sizes are bytes, totalBytes is 0 when unknown.
 const downloadItems=new Map(),downloadCalls=[],downloadListeners={};
 const listener=name=>({addListener:handler=>{downloadListeners[name]=handler;}});
 chrome.downloads={onCreated:listener('created'),onChanged:listener('changed'),
  search:async query=>query.id!==undefined?[downloadItems.get(query.id)].filter(Boolean):[...downloadItems.values()],
  pause:async id=>{downloadCalls.push(['pause',id]);if(!downloadItems.has(id))throw new Error('Invalid download id');downloadItems.get(id).paused=true;},
  resume:async id=>{downloadCalls.push(['resume',id]);const item=downloadItems.get(id);if(!item?.canResume)throw new Error('Download must be resumable');item.paused=false;},
  cancel:async id=>{downloadCalls.push(['cancel',id]);Object.assign(downloadItems.get(id),{state:'interrupted',error:'USER_CANCELED'});}};
 const sent=[];
 runtime.connectNative=()=>{ports++;return {postMessage:message=>sent.push(message),disconnect:()=>{},onMessage:{addListener:handler=>{runtime.toPort=handler;}},onDisconnect:listenerStub()};};
 const workerContext=vm.createContext({chrome,navigator:{userAgent:'Chrome/140.0.0.0'},setInterval,clearInterval});
 workerContext.importScripts=(...names)=>{for(const name of names)vm.runInContext(fs.readFileSync(path.join(__dirname,name),'utf8'),workerContext);};
 vm.runInContext(fs.readFileSync(path.join(__dirname,'worker.js'),'utf8'),workerContext);
 await vm.runInContext('startup()',workerContext);
 await new Promise(resolve=>setImmediate(resolve));
 assert.equal(ports,1);assert.equal(vm.runInContext('model.allTabs',workerContext),true);
 // Cancel on the "is debugging this browser" bar must stick until the user reconnects.
 const attachesBefore=attached.length;
 detached({tabId:7},'canceled_by_user');assert.equal(vm.runInContext('model.tabs.has(7)',workerContext),false);
 updated(7,{title:'New title'},{id:7,url:'https://fixture.test/'});await new Promise(resolve=>setImmediate(resolve));
 assert.equal(vm.runInContext('model.tabs.has(7)',workerContext),false);assert.equal(attached.length,attachesBefore);
 await vm.runInContext('run("connect")',workerContext);assert.equal(vm.runInContext('model.tabs.has(7)',workerContext),true);
 detached({tabId:7},'target_closed');updated(7,{title:'Again'},{id:7,url:'https://fixture.test/'});await new Promise(resolve=>setImmediate(resolve));
 assert.equal(vm.runInContext('model.tabs.has(7)',workerContext),true);
 updated(7,{url:'about:blank'},{id:7,url:'about:blank'});assert.equal(vm.runInContext('model.tabs.has(7)',workerContext),true);
 updated(7,{url:'chrome://settings/'},{id:7,url:'chrome://settings/'});assert.equal(vm.runInContext('model.tabs.has(7)',workerContext),false);
 await vm.runInContext('run("disconnect")',workerContext);assert.equal(saved.enabled,false);
 await vm.runInContext('startup()',workerContext);assert.equal(ports,1);
 await vm.runInContext('run("connect")',workerContext);assert.equal(saved.enabled,true);assert.equal(ports,2);
 await vm.runInContext('disconnect();ensureConnection()',workerContext);assert.equal(ports,3);
 // Downloads: each new file, its progress and its end reach Companion; Companion's controls reach Chrome.
 await new Promise(resolve=>setImmediate(resolve));
 const report=()=>sent.filter(m=>m.method==='VoiceCompanion.download').at(-1)?.params;
 const item={id:41,filename:'C:\\Users\\Will\\Downloads\\report.pdf',url:'https://files.test/report.pdf?token=1',finalUrl:'https://files.test/report.pdf',
  state:'in_progress',paused:false,canResume:true,bytesReceived:0,totalBytes:1000,danger:'safe',mime:'application/pdf'};
 downloadItems.set(41,item);downloadListeners.created({...item});
 assert.equal(report().new,true);assert.equal(report().filename,item.filename);assert.equal(report().total,1000);
 item.bytesReceived=450;await vm.runInContext('downloads.poll()',workerContext);assert.equal(report().received,450);assert.ok(!report().new);
 runtime.toPort({method:'VoiceCompanion.downloadControl',params:{id:41,action:'pause'}});await new Promise(resolve=>setTimeout(resolve,5));
 assert.deepEqual(downloadCalls.at(-1),['pause',41]);assert.equal(report().paused,true);
 // A page can never send a control: only Companion's port carries them, and bad actions are ignored.
 runtime.toPort({method:'VoiceCompanion.downloadControl',params:{id:41,action:'erase'}});await new Promise(resolve=>setTimeout(resolve,5));
 assert.deepEqual(downloadCalls.at(-1),['pause',41]);
 item.canResume=false;runtime.toPort({method:'VoiceCompanion.downloadControl',params:{id:41,action:'resume'}});await new Promise(resolve=>setTimeout(resolve,5));
 assert.equal(report().refused,true);assert.equal(report().paused,true);
 Object.assign(item,{state:'complete',paused:false,bytesReceived:1000});downloadListeners.changed({id:41,state:{current:'complete'}});await new Promise(resolve=>setTimeout(resolve,5));
 assert.equal(report().state,'complete');assert.equal(vm.runInContext('downloads.watching.size',workerContext),0);
 assert.equal(vm.runInContext('downloads.timer',workerContext),null);
 // data: addresses are never sent, and long addresses are cut short.
 const data={...item,id:42,url:'data:text/plain,'+'x'.repeat(5000),finalUrl:'',state:'in_progress'};downloadItems.set(42,data);downloadListeners.created({...data});
 assert.equal(report().url,'');
 runtime.toPort({method:'VoiceCompanion.downloadControl',params:{id:42,action:'cancel'}});await new Promise(resolve=>setTimeout(resolve,5));
 assert.equal(report().state,'interrupted');assert.equal(report().error,'USER_CANCELED');
 // On linking, Companion hears what Chrome is still doing.
 sent.length=0;await vm.runInContext('downloads.sync()',workerContext);
 assert.deepEqual(sent.filter(m=>m.method==='VoiceCompanion.download').map(m=>m.params.id).sort(),[41,42]);
 await vm.runInContext('disconnect()',workerContext);assert.equal(vm.runInContext('downloads',workerContext),null);
 downloadListeners.created({...item,id:43});assert.ok(!sent.some(m=>m.params?.id===43));
 const manifest=JSON.parse(fs.readFileSync(path.join(__dirname,'manifest.json'),'utf8'));assert.ok(manifest.permissions.includes('downloads'));
 process.stdout.write('Browser startup, persistent pause, retry, tabs, CDP routing, frame and download checks passed.\n');
})().catch(error=>{process.stderr.write(error.stack+'\n');process.exitCode=1;});

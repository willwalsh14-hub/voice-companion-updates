"""Accessible names from dom-accessibility-api 0.7.1 (MIT)."""
from pathlib import Path
import sys

_LIBRARY=(Path(getattr(sys, '_MEIPASS', Path(__file__).parent))/'accessible_names.js').read_text(encoding='utf-8')
ACCESSIBLE_LABEL='(()=>{'+_LIBRARY+r'''
const label=e=>{
  const name=VoiceCompanionAccessibility.computeAccessibleName(e);
  if(name)return name.trim().replace(/\s+/g,' ').slice(0,1000);
  const role=VoiceCompanionAccessibility.getRole(e);
  if(['list','listitem','region','main','navigation','complementary','banner','contentinfo','form','search'].includes(role))return '';
  if(e.matches('iframe,frame'))return e.getAttribute('title')||e.getAttribute('name')||'Unlabeled frame';
  if(e.matches('input,textarea')&&e.getAttribute('placeholder'))return e.getAttribute('placeholder');
  // Search-result title containers use visible content, not an invented label.
  if((!role||role==='generic')&&!e.isContentEditable&&!e.matches('input,textarea,select,svg,img'))return (e.innerText||e.textContent||'').trim().replace(/\s+/g,' ').slice(0,1000);
  if(role==='link'&&e.href){try{return 'Unlabeled link on '+new URL(e.href).hostname;}catch(_){}}
  return 'Unlabeled '+(role==='img'?'graphic':e.isContentEditable?'text field':role||'control');
};
label.role=VoiceCompanionAccessibility.getRole;
label.nativeName=VoiceCompanionAccessibility.computeAccessibleName;
label.description=VoiceCompanionAccessibility.computeAccessibleDescription;
// HTML checked state wins over a stale, conflicting ARIA attribute.
// Custom ARIA widgets expose their state through aria-checked instead.
label.checked=e=>e.matches('input[type="checkbox"],input[type="radio"]')?e.checked:e.getAttribute('aria-checked')==='true';
// The browser answers element.labels by searching the whole document, once for
// every button and field. On a page with thousands of buttons (YouTube
// comments) that alone took about a second per snapshot. While one snapshot
// runs, answer it from one pass over the page's labels. The page's own
// definitions are restored before the snapshot returns.
label.withCachedLabels=run=>{
  const kinds=['HTMLButtonElement','HTMLInputElement','HTMLMeterElement','HTMLOutputElement','HTMLProgressElement','HTMLSelectElement','HTMLTextAreaElement'];
  let found=null;const owners=()=>{if(found)return found;found=new Map();const roots=[document];
    for(let i=0;i<roots.length;i++){for(const node of roots[i].querySelectorAll('*')){if(node.shadowRoot)roots.push(node.shadowRoot);
      if(node.tagName==='LABEL'){const control=node.control;if(control){if(!found.has(control))found.set(control,[]);found.get(control).push(node);}}}}
    return found;};
  const saved=[];
  for(const name of kinds){const proto=window[name]?.prototype,original=proto&&Object.getOwnPropertyDescriptor(proto,'labels');
    if(!original||!original.configurable)continue;
    try{Object.defineProperty(proto,'labels',{configurable:true,enumerable:original.enumerable,get(){return this instanceof HTMLInputElement&&this.type==='hidden'?null:(owners().get(this)||[]);}});saved.push([proto,original]);}catch(_){}}
  try{return run();}
  finally{for(const [proto,original] of saved){try{Object.defineProperty(proto,'labels',original);}catch(_){}}}
};
return label;
})()'''

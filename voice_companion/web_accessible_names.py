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
return label;
})()'''

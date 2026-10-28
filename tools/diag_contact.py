"""Try branch only: run the real site audit for the contact page, logging each step from 'find Contact Us' on."""
import json,os,sys
from pathlib import Path
app=Path(sys.argv[1]).resolve();label=sys.argv[2]
sys.path.insert(0,str(app));os.chdir(app)
import consumer_site_audit as audit
from web_assistant import WebSession
audit.SITES=(('Control Alt Success home and contact dialog','https://controlaltsuccess.com/'),)
log=[];on=[False]
def wrap(name):
    real=getattr(WebSession,name)
    def run(self,*args,**kw):
        if name=='find' and args and 'Contact' in str(args[0]):on[0]=True
        before=self.reading.position
        result=real(self,*args,**kw)
        if on[0]:
            f=self.keyboard_field or {}
            log.append(f"{name}{args[:1]} pos {before}->{self.reading.position}/{len(self.reading.text)} -> {str(result)[:160]!r} | field={f.get('label')!r} value={f.get('value')!r} modal={bool((self.snapshot or {}).get('modal_key'))}")
        return result
    setattr(WebSession,name,run)
for n in ('keyboard','command','dictate_to_focus','find','choose','commit_keyboard_field'):wrap(n)
report=audit.audit()
site=report['sites'][0]
log.append('status='+site['status']+' failed='+json.dumps([c['action'] for c in site['checks'] if not c.get('passed')]))
text='%0A'.join(l.replace('%','%25') for l in log)
print('::error title=Audit contact steps ('+label+')::'+text[:7000])
print('\n'.join(log))

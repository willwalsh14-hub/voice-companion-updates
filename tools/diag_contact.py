"""Try branch only: step through the Control Alt Success contact dialog and report every step."""
import json,os,sys,tempfile
from pathlib import Path
app=Path(sys.argv[1]).resolve();label=sys.argv[2]
sys.path.insert(0,str(app));os.chdir(app)
from playwright.sync_api import sync_playwright
from web_assistant import BrowserBackend,WebSession
log=[]
def step(name,result,session):
    field=session.keyboard_field or {}
    log.append(f"{name} -> {str(result)[:220]!r} | field={field.get('label')!r} value={field.get('value')!r} modal={bool((session.snapshot or {}).get('modal_key'))}")
with tempfile.TemporaryDirectory() as folder,sync_playwright() as driver:
    try:browser=driver.chromium.launch(headless=True,channel='chrome')
    except Exception:browser=driver.chromium.launch(headless=True)
    context=browser.new_context();page=context.new_page();page.set_default_timeout(8000)
    backend=BrowserBackend(Path(folder)/'profile');backend.page=page;backend.context=context;backend.start=lambda:None
    s=WebSession(folder,backend);s.auto_read=False
    try:
        page.goto('https://controlaltsuccess.com/',wait_until='domcontentloaded',timeout=20000);s._refresh()
        step('find',s.find('Contact Us'),s);step('choose 1',s.choose(1),s);step('yes',s.command('yes'),s)
        step('controls',len(s._form_controls(s.snapshot)),s)
        step('Quick:e',s.keyboard('Quick:e'),s)
        for value in ('Accessibility Test','test@example.com','5550000000'):
            step('type '+value,s.dictate_to_focus(value,literal=True),s);step('Tab',s.keyboard('Tab'),s)
        for k in ('Down','Tab','Down','Tab'):step(k,s.keyboard(k),s)
        step('type message',s.dictate_to_focus('Read-only navigation test. Do not send.',literal=True),s);step('Tab',s.keyboard('Tab'),s)
        for i in range(6):step('ShiftTab',s.keyboard('ShiftTab'),s)
        s.commit_keyboard_field();s.keyboard_field=None
        step('review',s.command('review and submit form'),s)
    except Exception as exc:log.append('EXCEPTION '+type(exc).__name__+': '+str(exc)[:300])
    browser.close()
text='%0A'.join(l.replace('%','%25') for l in log)
print('::error title=Contact dialog steps ('+label+')::'+text[:6000])
print('\n'.join(log))

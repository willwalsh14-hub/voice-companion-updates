"""Read-only public-site tasks through Companion's real browser integration.

Network, certificate and verification blocks are reported separately from
navigation failures. This never signs in, enters real data or submits a form.
"""
import argparse,json,os,re,tempfile,time
from pathlib import Path
from playwright.sync_api import sync_playwright
from web_assistant import BrowserBackend,WebSession
from web_browse import kind
from companion import APP_VERSION
from media_hub import MediaHub,_json,RADIO_ROOTS,APPLE_API
from media_player import MediaPlayer,PLAYER_HTML

SITES=(('Hartgen Word training','https://hartgenconsultancy.com/word/'),
       ('Hartgen Chrome training','https://hartgenconsultancy.com/chromeplatedrevisited/'),
       ('PayPal public sign-in','https://www.paypal.com/signin'),
       ('Control Alt Success home and contact dialog','https://controlaltsuccess.com/'),
       ('Control Alt Success services','https://controlaltsuccess.com/services.html'),
       ('Control Alt Success training','https://controlaltsuccess.com/training-options.html'),
       ('Control Alt Success assessment','https://controlaltsuccess.com/assessment.html'),
       ('Control Alt Success custom training','https://controlaltsuccess.com/custom-training.html'),
       ('Control Alt Success resources','https://controlaltsuccess.com/resource-hub.html'),
       ('Control Alt Success blog','https://controlaltsuccess.com/blog.html'),
       ('Control Alt Success portal public page','https://controlaltsuccess.com/portal.html'),
       ('Blind MAST contact form','https://blindmast.com/contact-us/'),
       ('Simple page','https://example.org/'),
       ('Reference article','https://en.wikipedia.org/wiki/Assistive_technology'),
       ('Accessibility guidance','https://www.w3.org/WAI/'),
       ('JAWS enhancements','https://support.freedomscientific.com/downloads/jaws/JAWSWhatsNew'),
       ('Podcast website','https://www.npr.org/podcasts/510289/planet-money'),
       ('Brave search','https://search.brave.com/search?q=accessible+keyboard+navigation'),
       ('Brave speedtest search','https://search.brave.com/search?q=speedtest+global+broadband'),
       ('Speedtest rendered page','https://www.speedtest.net/'),
       ('Google search','https://www.google.com/search?q=accessible+keyboard+navigation'))

def audit(timeout=20000):
    report={'version':APP_VERSION,'kind':'Read-only live public-site audit','sites':[],'media':[]}
    with tempfile.TemporaryDirectory() as folder,sync_playwright() as driver:
        options={'headless':True,'firefox_user_prefs':{'media.autoplay.default':0}}
        if os.name!='nt':options['env']={**os.environ,'MOZ_DISABLE_CONTENT_SANDBOX':'1','MOZ_DISABLE_RDD_SANDBOX':'1','MOZ_DISABLE_GMP_SANDBOX':'1'}
        browser=driver.firefox.launch(**options)
        for name,url in SITES:
            entry={'name':name,'requested_url':url,'status':'blocked','checks':[]};start=time.monotonic()
            context=browser.new_context();page=context.new_page();page.set_default_timeout(8000)
            backend=BrowserBackend(Path(folder)/'profile');backend.page=page;backend.context=context;backend.start=lambda:None
            session=WebSession(folder,backend);session.auto_read=False
            try:
                response=page.goto(url,wait_until='domcontentloaded',timeout=timeout)
                entry['http_status']=response.status if response else None
                if response and response.status>=400:
                    entry['reason']='The website returned HTTP '+str(response.status)
                else:
                    session._refresh();snapshot=session.snapshot;entry['loaded_url']=snapshot.get('url');entry['title']=snapshot.get('title');entry['characters']=len(snapshot.get('text',''))
                    if snapshot.get('challenge'):entry['reason']='Website verification or CAPTCHA requires assistance.'
                    elif not entry['characters']:entry['reason']='No readable page text was returned.'
                    else:
                        entry['status']='passed';elements=snapshot.get('elements',[])
                        entry['headings']=len([e for e in elements if kind(e)=='heading']);entry['links']=len(session.link_items())
                        for key in ('Quick:h','Quick:k','Read:Down','Read:Up','PageTop','PageBottom'):
                            message=session.keyboard(key)
                            valid=0<=session.reading.position<=len(session.reading.text) and session.reading.position==session.browse.position
                            entry['checks'].append({'action':key,'passed':valid,'feedback':message[:160]})
                            if not valid:entry['status']='failed'
                        session.page_boundary()
                        for command in ('next heading','next link','next paragraph','previous paragraph'):
                            message=session.command(command)
                            valid=0<=session.reading.position<=len(session.reading.text) and session.reading.position==session.browse.position
                            entry['checks'].append({'action':command,'passed':valid,'feedback':message[:160]})
                            if not valid:entry['status']='failed'
                        if name.startswith('Hartgen ') or name=='PayPal public sign-in':
                            targets=[e for e in elements if kind(e)=='button' and ('paypal' in e.get('label','').lower() if name.startswith('Hartgen ') else e.get('label')=='Next')]
                            valid=bool(targets)
                            for control in targets:
                                session.reading.position=max(0,control['start']-1)
                                feedback=session.keyboard('Read:Down')
                                valid=valid and 'button' in feedback.lower()
                                entry['checks'].append({'action':'Arrow to native button','passed':'button' in feedback.lower(),'feedback':feedback[:160]})
                            if not valid:entry['status']='failed'
                            entry['submission']='No purchase or account sign-in submitted.'
                        if name.startswith('Control Alt Success'):
                            # Down enters text fields automatically. Use the
                            # same Leave:Down path as the app when leaving one,
                            # and still require every ordinary text block.
                            session.page_boundary();page_text=session.reading.text
                            body_lines=[]
                            interactive={'link','heading','button','edit','checkbox','radio','combobox','listbox','graphic'}
                            for line in re.finditer(r'[^\n]+',page_text):
                                if line.group().strip() and not any(kind(e) in interactive and e.get('start',0)<=line.start()<e.get('end',0) for e in session.browse.elements):
                                    body_lines.append(line.group().strip())
                            heard=[session.keyboard('Read:Home')]
                            for _ in range(page_text.count('\n')+2):
                                previous=session.reading.position
                                heard.append(session.keyboard('Leave:Down' if session.keyboard_field else 'Read:Down'))
                                if session.reading.position==previous:break
                            spoken=' '.join(heard)
                            missing=[line for line in body_lines if line not in spoken]
                            valid=bool(body_lines) and not missing and session.keyboard_field is None
                            entry['checks'].append({'action':'Arrow through ordinary page text and text fields','passed':valid,'body_lines':len(body_lines),'missing_lines':missing[:5]})
                            if not valid:entry['status']='failed'
                        if entry['headings']>=2:
                            session._refresh(read=False);session.keyboard('Quick:h');first=session.browse.current()['key']
                            session.keyboard('Quick:h');session.keyboard('Quick:Shift:h')
                            valid=session.browse.current()['key']==first
                            entry['checks'].append({'action':'H, H, Shift+H reversibility','passed':valid})
                            if not valid:entry['status']='failed'
                        if name=='Control Alt Success home and contact dialog':
                            session.find('Contact Us');session.choose(1);session.command('yes')
                            valid=bool(session.snapshot.get('modal_key')) and len(session._form_controls(session.snapshot))==6
                            entry['checks'].append({'action':'Open contact dialog and scope virtual cursor to its six fields','passed':valid})
                            if not valid:entry['status']='failed'
                            session.keyboard('Quick:e')
                            for value in ('Accessibility Test','test@example.com','5550000000'):
                                session.dictate_to_focus(value,literal=True);session.keyboard('Tab')
                            session.keyboard('Down');session.keyboard('Tab');session.keyboard('Down');session.keyboard('Tab')
                            session.dictate_to_focus('Read-only navigation test. Do not send.',literal=True);session.keyboard('Tab')
                            for _ in range(6):session.keyboard('ShiftTab')
                            valid=session.keyboard_field.get('value')=='Accessibility Test'
                            entry['checks'].append({'action':'Type three fields, choose two drop-downs, fill message, and Shift Tab back six fields','passed':valid})
                            if not valid:entry['status']='failed'
                            session.commit_keyboard_field();session.keyboard_field=None
                            message=session.command('review and submit form')
                            valid='Form review' in message
                            entry['checks'].append({'action':'Voice review is available after keyboard editing','passed':valid})
                            if not valid:entry['status']='failed'
                            if valid:
                                message=session.command('change this field');session.command('Corrected Test')
                                valid='Changing' in message and page.locator('#cf-name').input_value()=='Corrected Test'
                                entry['checks'].append({'action':'Correct first field during voice review without sending','passed':valid})
                                if not valid:entry['status']='failed'
                            entry['submission']='Not submitted; no contact message sent.'
                        if name=='Blind MAST contact form':
                            session._refresh(read=False)
                            first=next(e for e in session.browse.elements if e.get('label')=='Your Name (required)' and kind(e)=='edit')
                            session.browse.index=session.browse.elements.index(first);session.focus_current()
                            for value in ('Companion navigation test','test@example.com','Unsubmitted browser test','This is an unsubmitted navigation test.'):
                                session.dictate_to_focus(value,literal=True)
                                feedback=session.keyboard('Tab')
                                valid='changed' not in feedback.lower()
                                entry['checks'].append({'action':'Enter field and Tab','passed':valid,'feedback':feedback[:160]})
                                if not valid:entry['status']='failed'
                            for _ in range(4):
                                feedback=session.keyboard('ShiftTab');valid='changed' not in feedback.lower()
                                entry['checks'].append({'action':'Shift+Tab review','passed':valid,'feedback':feedback[:160]})
                                if not valid:entry['status']='failed'
                            valid=bool(session.keyboard_field and session.keyboard_field.get('value')=='Companion navigation test')
                            entry['checks'].append({'action':'First field retains its answer','passed':valid})
                            if not valid:entry['status']='failed'
                            entry['form_submission']='Not submitted; submit behavior tested only on isolated fixtures.'
                        if name in ('Brave speedtest search','Speedtest rendered page'):
                            entry['heading_labels']=[e.get('label','') for e in session.snapshot.get('elements',[]) if kind(e)=='heading'][:30]
                            entry['render_updates']=[]
                            for _ in range(5):
                                page.wait_for_timeout(600)
                                previous=session.reading.text;session.refresh_changed()
                                valid=session.reading.position==session.browse.position and 0<=session.reading.position<=len(session.reading.text)
                                entry['render_updates'].append({'changed':previous!=session.reading.text,'characters':len(session.reading.text),'cursor_valid':valid})
                                if not valid:entry['status']='failed'
            except Exception as exc:entry['reason']=type(exc).__name__+': '+str(exc)[:250]
            finally:context.close()
            entry['seconds']=round(time.monotonic()-start,2);report['sites'].append(entry)
            print('LIVE SITE AUDIT '+json.dumps(entry),flush=True)
        for name in ('Radio','Podcast'):
            entry={'name':name,'status':'blocked'};context=browser.new_context();page=context.new_page()
            audio_folder=Path(folder)/name;audio_folder.mkdir();(audio_folder/'audio-player.html').write_text(PLAYER_HTML)
            player=MediaPlayer(audio_folder);player.context=context;player.page=page;player.start=lambda:None
            hub=MediaHub(audio_folder,player=player)
            try:
                if name=='Radio':
                    values=_json(RADIO_ROOTS[0]+'/json/stations/search?name=WGN&limit=5&hidebroken=true')
                    choices=[{'name':v['name'],'url':v.get('url_resolved') or v.get('url')} for v in values if v.get('url_resolved') or v.get('url')]
                else:
                    values=_json(APPLE_API+'?media=podcast&entity=podcast&limit=5&term=Planet%20Money').get('results',[])
                    show=next(v for v in values if v.get('feedUrl'))
                    choices=hub._feed_episodes({'name':show['collectionName'],'feed':show['feedUrl']})
                    entry['feed']=show['feedUrl']
                entry['catalog_items']=len(choices)
                if not choices:entry['reason']='No live catalog results were available.'
                else:
                    entry['attempts']=[]
                    for item in choices[:5]:
                        selected=item.get('name') or item.get('title')
                        try:
                            player.open(item['url'],selected,name.casefold())
                            player.control('pause');player.control('play');player.control('stop')
                            entry['selected']=selected;entry['status']='passed'
                            entry['playback']='Browser started, paused, resumed and stopped the live public stream.'
                            entry['attempts'].append({'name':selected,'status':'passed'});break
                        except Exception as exc:
                            entry['attempts'].append({'name':selected,'status':'blocked','reason':type(exc).__name__+': '+str(exc)[:250]})
                    if entry['status']!='passed':entry['reason']='None of the first five returned streams started; see attempts.'
            except Exception as exc:entry['reason']=type(exc).__name__+': '+str(exc)[:250]
            finally:context.close()
            report['media'].append(entry);print('LIVE MEDIA AUDIT '+json.dumps(entry),flush=True)
        browser.close()
    report['passed']=sum(s['status']=='passed' for s in report['sites'])
    report['blocked']=sum(s['status']=='blocked' for s in report['sites'])
    report['failed']=sum(s['status']=='failed' for s in report['sites'])
    return report

if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--output',default='VoiceCompanion-Consumer-Site-Audit.txt');parser.add_argument('--timeout',type=int,default=20000);args=parser.parse_args()
    report=audit(args.timeout)
    Path(args.output).write_text(json.dumps(report,indent=2)+'\n',encoding='utf-8')
    print('LIVE SITE AUDIT SUMMARY '+json.dumps({k:report[k] for k in ('version','passed','blocked','failed')}),flush=True)
    raise SystemExit(bool(report['failed']))

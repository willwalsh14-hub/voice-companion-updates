"""Shared keyboard and voice menus for radio and podcast workflows."""
from dataclasses import dataclass
from datetime import datetime, timedelta
import re
from menu_navigation import announce_item,next_match,boundary_notice

# MediaHub.source() returns stored ids; the Station database form shows spoken labels.
SOURCE_LABELS={'all':'all','radio-browser':'radio browser','iprd':'iprd','radiosure':'radio sure'}

@dataclass(frozen=True)
class Action:
    label:str
    command:str=''
    form:str=''

RADIO=(Action('Search stations',form='search'),Action('Browse genre',form='genre'),Action('Browse country',form='country'),Action('Browse state',form='state'),Action('Browse city',form='city'),Action('Station database',form='source'),Action('List stations','list stations'),Action('List presets','list presets'),Action('Save preset',form='preset'),Action('Delete preset','delete preset'),Action('Pause audio','pause'),Action('Resume audio','resume'),Action('Stop audio','stop'),Action('Record selected station',form='record'),Action('Schedule selected station',form='schedule'),Action('Stop recording','stop recording'),Action('List scheduled recordings','list scheduled recordings'),Action('Cancel selected scheduled recording','cancel selected scheduled recording'),Action('List saved recordings','list recordings'),Action('Refresh station directories','refresh stations'),Action('Import RadioSure stations',form='import'))
RADIO_STATION=(Action('Add to favorites',form='preset'),Action('Record current station',form='record'),Action('Schedule recording',form='schedule'),Action('Pause audio','pause'),Action('Play','resume'),Action('Stop recording','stop recording'),Action('Cancel scheduled recordings','list scheduled recordings'))
PODCASTS=(Action('Search podcasts',form='search'),Action('Browse podcast category',form='category'),Action('List podcast shows','list shows'),Action('List subscriptions','list subscriptions'),Action('Open selected podcast','open selected feed'),Action('Subscribe to selected podcast','subscribe'),Action('Remove selected subscription','remove selected subscription'),Action('Download selected episode','download this episode'),Action('List downloaded episodes','list downloads'),Action('Automatic downloads',form='automatic'),Action('Manual downloads','manual downloads'),Action('Refresh subscriptions','refresh subscriptions'),Action('Pause audio','pause'),Action('Resume audio','resume'),Action('Stop audio','stop'),Action('Rewind audio',form='rewind'),Action('Forward audio',form='forward'),Action('Next chapter','next chapter'),Action('Previous chapter','previous chapter'),Action('Play next episode','next episode'),Action('Play previous episode','previous episode'),Action('Open next podcast','next podcast'),Action('Open previous podcast','previous podcast'))

class MediaNavigation:
    def __init__(self,hub,section='radio'):
        self.hub=hub;self.section=section;self.focus='actions';self.index=0;self.view='stations' if section=='radio' else 'shows';self.item_index=0;self.form=None;self.field_index=0;self.pending=None;self.confirm_choice='no';self.revision=0;self.audio_paused=False;self.keyboard_mode=False;self.full_radio_actions=False
        if hub.browsing_episodes:self.view='episodes';self.focus='items';self.item_index=hub.episode_index or 0
        elif section=='radio' and hub.preset_browsing:self.view='presets';self.focus='items';self.item_index=hub.preset_index or 0
        elif section=='radio' and hub.stations:self.focus='items';self.item_index=hub.station_index or 0
        elif section=='podcast' and hub.shows:self.focus='items';self.item_index=hub.show_index or 0
    @property
    def actions(self):
        selected=bool(getattr(self.hub,'current_station',None)) or bool(self.hub.stations and isinstance(self.hub.station_index,int) and 0<=self.hub.station_index<len(self.hub.stations)) or bool(getattr(self.hub,'preset_browsing',False))
        return RADIO_STATION if self.section=='radio' and self.keyboard_mode and selected and not self.full_radio_actions else RADIO if self.section=='radio' else PODCASTS
    def set_keyboard_mode(self,enabled):
        old=self.actions[min(self.index,len(self.actions)-1)]
        self.keyboard_mode=enabled
        self.index=next((i for i,a in enumerate(self.actions) if (a.command,a.form)==(old.command,old.form)),0)
    def enter(self,section,keyboard=False):
        self.hub.awaiting_radio_search=False;self.hub.awaiting_podcast_search=False
        self.section=section;self.keyboard_mode=keyboard;self.full_radio_actions=False;self.focus='actions';self.index=0;self.form=None;self.pending=None;self.view='stations' if section=='radio' else 'shows';self.revision+=1
        return ('Radio' if section=='radio' else 'Podcasts')+' menu. '+self.announce()+ ' Tab switches between actions and results. Enter opens the selected item.'
    def items(self):
        h=self.hub
        if self.view=='stations':return h.stations
        if self.view=='station_choices':return [h.stations[i] for i in h.station_choices]
        if self.view=='presets':return h.presets()
        if self.view=='shows':return h.shows
        if self.view=='episodes':return h.episodes
        if self.view=='downloads':return [{'title':p.stem} for p in h.downloads()]
        if self.view=='recordings':
            return [{'title':p.stem} for p in h.recordings()]
        if self.view=='scheduled':
            jobs=h._load('radio-schedule.json',[])
            # A damaged entry is listed so it can be removed; it never stops the app.
            return [{'job':j,'name':(name+' at '+j['when']) if (name:=h._scheduled_name(j)) else 'A damaged schedule entry'}
                    for j in (jobs if isinstance(jobs,list) else [])]
        return []
    def state(self):
        if self.pending:return None
        if self.form:
            fields=self.form['fields'];i=self.field_index
            if i>=len(fields):return (['OK','Cancel'],i-len(fields),lambda n:self.select_field(len(fields)+n))
            field=fields[i]
            if field['choices']:return (list(field['choices']),field['choices'].index(field['value']),lambda n:self.set_field(field['choices'][n]))
            return None
        if self.focus=='actions':return ([a.label for a in self.actions],self.index,lambda i:setattr(self,'index',i))
        names=[x.get('name') or x.get('title') or 'Unnamed item' for x in self.items()]
        return (names,self.item_index,self.select_item) if names else None
    def select_item(self,index):
        self.item_index=index;h=self.hub
        if self.view in ('stations','station_choices','presets'):
            self.full_radio_actions=False
            if self.keyboard_mode:self.index=0
        attr={'stations':'station_index','station_choices':'station_choice_index','presets':'preset_index','shows':'show_index','episodes':'episode_index'}.get(self.view)
        if attr:setattr(h,attr,index)
        h.preset_browsing=self.view=='presets'
        if self.view=='episodes':h.browsing_episodes=True
        elif self.view=='shows':h.browsing_episodes=False
    def announce(self):
        state=self.state()
        if state:
            names,i,_=state;return announce_item(names[max(0,min(i or 0,len(names)-1))],i or 0,len(names))
        if self.form:return self.field_notice()
        return 'No items in this list. Tab returns to the actions menu.'
    def keyboard(self,key,voice=False):
        if not voice and not self.form and not self.pending and self.focus=='items' and key in ('Tab','ShiftTab') and self.view in ('stations','presets') and self.items():self.select_item(self.item_index)
        self.set_keyboard_mode(not voice)
        if self.pending:
            if key.lower() in ('y','n','alt+y','alt+n'):return self.command('yes' if key.lower() in ('y','alt+y') else 'no')
            if key in ('Up','Down','Left','Right','Tab','ShiftTab'):
                choice='yes' if key in ('Up','Left','ShiftTab') else 'no'
                if choice==self.confirm_choice:return 'Start of list.' if choice=='yes' else 'End of list.'
                self.confirm_choice=choice;return choice.capitalize()+'.'
            if key in ('Home','End'):
                choice='yes' if key=='Home' else 'no'
                if choice==self.confirm_choice:return 'Start of list.' if key=='Home' else 'End of list.'
                self.confirm_choice=choice;return choice.capitalize()+'.'
            if key=='Enter':return self.command(self.confirm_choice)
            if key=='Escape':return self.command('no')
            return ''
        if self.form:
            if key=='Escape':self.form=None;self.revision+=1;return 'Canceled. '+self.announce()
            if key in ('Tab','ShiftTab'):return self.select_field((self.field_index+(-1 if key=='ShiftTab' else 1))%(len(self.form['fields'])+2))
            if key=='Enter':
                if self.field_index==len(self.form['fields'])+1:return self.keyboard('Escape')
                if self.field_index<len(self.form['fields'])-1:return self.select_field(self.field_index+1)
                return self.submit_form()
            if key=='Space' and self.field_index<len(self.form['fields']) and self.form['fields'][self.field_index]['choices']==('no','yes'):
                f=self.form['fields'][self.field_index];return self.set_field('yes' if f['value']=='no' else 'no')
        else:
            if key in ('Tab','ShiftTab'):self.focus='items' if self.focus=='actions' else 'actions';return ('Results. ' if self.focus=='items' else 'Actions. ')+self.announce()
            if key=='Escape':
                if self.focus=='items' and self.view=='episodes':return self.run('back to podcasts')
                if self.focus=='items':self.focus='actions';return ('Station actions. ' if self.section=='radio' and self.actions is RADIO_STATION else '')+self.announce()
                if self.section=='radio' and self.actions is RADIO_STATION:
                    self.full_radio_actions=True;self.index=0;return 'Radio menu. '+self.announce()
                return '__main__'
            if key=='Enter':return self.activate()
            shortcuts={'Ctrl+F':'search','Ctrl+Shift+P':'list presets' if self.section=='radio' else 'list subscriptions','F5':'refresh stations' if self.section=='radio' else 'refresh subscriptions','Space':'toggle audio', 'Ctrl+R':'resume','Ctrl+S':'stop','Ctrl+Left':'rewind 15 seconds','Ctrl+Right':'forward 15 seconds','Ctrl+D':'download this episode' if self.section=='podcast' else 'save preset','Delete':'delete preset' if self.view=='presets' else 'cancel selected scheduled recording' if self.view=='scheduled' else 'remove selected subscription' if self.section=='podcast' and self.view=='shows' else ''}
            if key in shortcuts:
                if key=='Ctrl+F':return self.open_form('search')
                return self.run(shortcuts[key]) if shortcuts[key] else 'Choose an item first.'
        state=self.state()
        if not state:return self.announce() if key in ('Home','End','Up','Down') and not self.form else ''
        names,index,select=state;index=index or 0
        if key in ('Home','End'):
            target=0 if key=='Home' else len(names)-1
            if index==target:return 'Start of list.' if key=='Home' else 'End of list.'
            index=target
        elif key in ('Up','Down','Left','Right'):
            step=-1 if key in ('Up','Left') else 1
            if not 0<=index+step<len(names):return 'Start of list.' if step<0 else 'End of list.'
            index+=step
        elif key.startswith('Letter:'):
            match=next_match(names,index,key[-1])
            if match is None:return 'No item starts with '+key[-1].upper()+'.'
            if match==index:return 'End of matching items. '+self.announce()
            index=match
        else:return ''
        select(index);return self.announce()
    def activate(self):
        if self.focus=='actions':
            action=self.actions[self.index]
            if self.section=='radio' and action.label=='Play':
                h=self.hub
                selected=h.presets()[h.preset_index] if h.preset_browsing and h.presets() and h.preset_index is not None else h.stations[h.station_index] if h.stations and h.station_index is not None else h.current_station
                same=bool(selected and h.current_station and selected.get('url')==h.current_station.get('url') and getattr(h.player,'kind',None)=='radio')
                if not same:
                    if h.preset_browsing:return self.run('play preset')
                    if h.stations and h.station_index is not None:return self.run('play station '+str(h.station_index+1))
                    if selected:
                        h.player.open(selected['url'],selected['name'],'radio');return 'Playing '+selected['name']+'.'
            return self.open_form(action.form) if action.form else self.run(action.command)
        if not self.items():return self.announce()
        self.item_index=max(0,min(self.item_index,len(self.items())-1));self.select_item(self.item_index)
        if self.view=='scheduled':return self.run('cancel selected scheduled recording')
        cmd={'stations':f'play station {self.item_index+1}','station_choices':f'choose {self.item_index+1}','presets':'play preset','shows':'open selected feed','episodes':f'play episode {self.item_index+1}','downloads':f'play download {self.item_index+1}','recordings':f'play recording {self.item_index+1}'}.get(self.view)
        return self.run(cmd) if cmd and self.items() else self.announce()
    def open_form(self,name):
        def field(label,value='',choices=()):return {'label':label,'value':value,'choices':choices}
        selected=self.items()[self.item_index] if self.focus=='items' and self.items() and self.item_index<len(self.items()) else {}
        if name=='preset' and not selected:
            selected=self.hub.stations[self.hub.station_index] if self.hub.stations and self.hub.station_index is not None else self.hub.current_station or {}
        definitions={'search':[field('Station name' if self.section=='radio' else 'Podcast name')], 'genre':[field('Genre')], 'country':[field('Country')], 'state':[field('State')], 'city':[field('City')], 'category':[field('Podcast category')], 'source':[field('Station database',SOURCE_LABELS.get(self.hub.source(),'all'),('all','radio browser','iprd','radio sure'))], 'preset':[field('Preset name',selected.get('name',''))], 'record':[field('Length in minutes','30')], 'schedule':[field('Day','tomorrow',('today','tomorrow')),field('Time','8 PM'),field('Length in minutes','30'),field('Repeat every day','no',('no','yes'))], 'automatic':[field('Automatic download limit','manual')], 'rewind':[field('Amount','15 seconds')], 'forward':[field('Amount','15 seconds')], 'import':[field('RadioSure file path')]}
        self.form={'name':name,'fields':definitions[name]};self.field_index=0;self.revision+=1
        question=('What station would you like to find? ' if self.section=='radio' else 'What podcast would you like to find? ') if name=='search' else ''
        return question+self.field_notice()+ ' Tab or Shift+Tab moves between fields, OK and Cancel. Enter continues. Escape cancels.'
    def field_notice(self):
        if self.field_index>=len(self.form['fields']):return 'OK.' if self.field_index==len(self.form['fields']) else 'Cancel.'
        f=self.form['fields'][self.field_index];return f['label']+'. '+(f['value']+'.' if f['value'] else 'Type or say a value.')
    def select_field(self,index):self.field_index=index;self.revision+=1;return self.field_notice()
    def set_field(self,value):
        f=self.form['fields'][self.field_index]
        if f['choices']:
            candidate=next((c for c in f['choices'] if c.casefold()==value.casefold()),None)
            if candidate is None:return 'Choose '+', '.join(f['choices'])+'.'
            value=candidate
        f['value']=value;self.revision+=1;return value+'.'
    def field_value(self,value,next_key='Enter'):
        if not self.form or self.field_index>=len(self.form['fields']):return ''
        f=self.form['fields'][self.field_index]
        if f['choices']:return self.keyboard(next_key)
        f['value']=value.strip()
        return self.keyboard(next_key)
    def submit_form(self):
        form=self.form;name=form['name'];v=[f['value'].strip() for f in form['fields']]
        if not all(v):return 'Enter a value for every field.'
        h=self.hub
        try:
            if name in ('record','schedule'):
                if not v[2 if name=='schedule' else 0].isdigit() or not 1<=int(v[2 if name=='schedule' else 0])<=240:return 'Choose a recording length from 1 to 240 minutes.'
                station=(h.presets()[h.preset_index] if h.preset_browsing and h.presets() and h.preset_index is not None else h.stations[h.station_index] if h.stations and h.station_index is not None else h.current_station)
                if not station:return 'Select a station first. Escape cancels this form.'
                if name=='record':result=h.record_now(station,int(v[0]))
                else:
                    match=re.fullmatch(r'(\d{1,2})(?::(\d\d))?\s*(am|pm)',v[1],re.I)
                    if not match or not 1<=int(match[1])<=12 or int(match[2] or 0)>59:return 'Enter a time such as 8 PM or 8:30 AM.'
                    when=datetime.combine(h.clock().date()+timedelta(days=v[0]=='tomorrow'),datetime.min.time()).replace(hour=int(match[1])%12+(12 if match[3].lower()=='pm' else 0),minute=int(match[2] or 0))
                    if when<=h.clock():return 'Choose a future time.'
                    result=h.schedule(station,when,int(v[2]),v[3]=='yes')
            else:
                command={'search':('search radio for ' if self.section=='radio' else 'search podcasts for ')+v[0],'genre':'browse genre '+v[0],'country':'browse country '+v[0],'state':'browse state '+v[0],'city':'browse city '+v[0],'category':'browse podcasts category '+v[0],'source':'choose station database '+v[0],'preset':'save preset as '+v[0],'automatic':'auto download '+v[0],'rewind':'rewind '+v[0],'forward':'forward '+v[0],'import':'import radiosure '+v[0]}.get(name)
                if name=='automatic':
                    from media_hub import _episode_count
                    if _episode_count(v[0]) is None:return 'Choose manual, all, or a number from 1 to 10,000.'
                    if h.shows and h.show_index is not None:command+=' for podcast '+str(h.show_index+1)
                    else:return 'Select a subscribed podcast first.'
                    if not any(x['feed']==h.shows[h.show_index].get('feed') for x in h.saved_subscriptions()):return 'Subscribe to this podcast first.'
                result=self.run(command)
        except (OSError,ValueError,IndexError) as exc:return 'Could not complete this request. '+str(exc)
        self.form=None;self.revision+=1;return result
    def run(self,text):
        if not text:return ''
        cmd=text.strip().casefold().rstrip('.!?');h=self.hub
        if cmd in ('cancel selected scheduled recording','remove selected subscription'):
            if cmd.startswith('cancel'):
                if self.view!='scheduled' or not self.items():return 'List scheduled recordings and select one first.'
                item=self.items()[min(self.item_index,len(self.items())-1)];self.pending=('schedule',item['job']);label=item['name']
            else:
                if not h.shows or h.show_index is None:return 'Select a subscribed podcast first.'
                item=h.shows[h.show_index]
                if not any(s['feed']==item.get('feed') for s in h.saved_subscriptions()):return 'This podcast is not subscribed.'
                self.pending=('subscription',item['feed']);label=item['name']
            self.confirm_choice='no';return 'Remove '+label+'? Yes or no.'
        if cmd=='delete preset':self.select_item(self.item_index) if self.view=='presets' else None
        if cmd=='manual downloads' and h.shows and h.show_index is not None:text+=' for podcast '+str(h.show_index+1)
        result=h.command(text,section=self.section)
        if cmd.startswith('pause'):self.audio_paused=True
        elif cmd.startswith(('resume','play','stop')):self.audio_paused=False
        if h.preset_delete_pending:self.pending=('preset',h.preset_delete_pending);self.confirm_choice='no'
        if cmd.startswith(('search','find','browse genre','browse country','browse state','browse city','browse podcasts category')) or cmd in ('list stations','list radio stations','list results'):
            if h.stations and self.section=='radio':self.view='stations';self.focus='items'
            elif h.shows and self.section=='podcast':self.view='shows';self.focus='items'
        elif cmd in ('list presets','radio presets','list favorites'):self.view='presets';self.focus='items'
        elif cmd in ('list subscriptions','list podcasts','list shows','list podcast shows','back to podcasts','back to feeds'):self.view='shows';self.focus='items'
        elif cmd in ('list downloads','downloaded podcasts'):self.view='downloads';self.focus='items'
        elif cmd in ('list recordings','list radio recordings'):self.view='recordings';self.focus='items'
        elif cmd in ('list scheduled recordings','what recordings are scheduled'):self.view='scheduled';self.focus='items'
        elif h.browsing_episodes and self.section=='podcast':self.view='episodes';self.focus='items'
        elif h.station_choices and self.section=='radio':self.view='station_choices';self.focus='items'
        attr={'stations':'station_index','station_choices':'station_choice_index','presets':'preset_index','shows':'show_index','episodes':'episode_index'}.get(self.view)
        self.item_index=(getattr(h,attr,None) or 0) if attr else 0;self.revision+=1
        return result
    def voice_key(self,key):return self.keyboard(key,voice=True)
    def command(self,text):
        self.set_keyboard_mode(False)
        cmd=text.strip().casefold().rstrip('.!?')
        if self.pending:
            kind,key=self.pending
            if cmd in ('no','cancel','no thanks'):self.pending=None;self.hub.preset_delete_pending=None;return 'Canceled.'
            if cmd in ('yes','yes please','ok','okay','confirm'):
                self.pending=None
                if kind=='preset':return self.hub.command('yes',section='radio')
                filename='radio-schedule.json' if kind=='schedule' else 'podcast-subscriptions.json'
                with self.hub._lock:
                    items=self.hub._load(filename,[])
                    if not isinstance(items,list):items=[]
                    if kind=='schedule':
                        # Remove only the chosen entry, damaged or not.
                        index=next((i for i,x in enumerate(items) if x==key),None)
                        if index is None:return 'That recording is no longer scheduled.'
                        items.pop(index)
                    else:items=[x for x in items if not (isinstance(x,dict) and x.get('feed')==key)]
                    self.hub._save(filename,items)
                if kind=='subscription':self.hub.subscriptions();self.view='shows'
                self.item_index=0;return 'Removed.'
            return 'Yes or no?'
        if self.form:
            if cmd in ('read field','read current field','current field'):return self.field_notice()
            target=re.fullmatch(r'(?:go to|focus) (.+)',cmd)
            if target:
                index=next((i for i,f in enumerate(self.form['fields']) if f['label'].casefold()==target[1]),None)
                if index is not None:return self.select_field(index)
            if cmd in ('next option','previous option','first option','last option'):return self.voice_key({'next option':'Down','previous option':'Up','first option':'Home','last option':'End'}[cmd])
            letter=re.fullmatch(r'(?:choose|select) (?:option )?(?:beginning|starting) with ([a-z])',cmd)
            if letter:return self.voice_key('Letter:'+letter[1])
            if cmd in ('next field','next control','next setting','next'):return self.voice_key('Tab')
            if cmd in ('previous field','previous setting','previous'):return self.voice_key('ShiftTab')
            if cmd in ('cancel','cancel form','go back','back'):return self.voice_key('Escape')
            if cmd in ('ok','okay','confirm','save','submit'):return self.submit_form()
            value=re.sub(r'^(?:type|enter|set to|choose|select) ','',text,flags=re.I)
            if self.field_index<len(self.form['fields']):value=re.sub(r'^(?:set )?'+re.escape(self.form['fields'][self.field_index]['label'])+r'(?: to)? ','',value,flags=re.I)
            if self.field_index>=len(self.form['fields']):return 'Say OK or cancel.'
            result=self.set_field(value)
            if self.form['name'] in ('search','genre','country','state','city','category'):return self.submit_form()
            return result
        if cmd in ('radio menu','podcasts menu','podcast menu','actions','media actions','back to actions'):self.focus='actions';return self.announce()
        if cmd in ('results','media results','back to results'):self.focus='items';return self.announce()
        if cmd in ('next action','previous action'):self.focus='actions';return self.voice_key('Down' if cmd.startswith('next') else 'Up')
        if cmd in ('next','previous','ok','okay','that one','confirm'):
            return self.voice_key('Enter' if cmd in ('ok','okay','that one','confirm') else 'Down' if cmd=='next' else 'Up')
        if cmd in ('first item','last item','first action','last action'):return self.voice_key('Home' if cmd.startswith('first') else 'End')
        if cmd in ('search','search stations','search radio','search podcasts','search podcast'):return self.open_form('search')
        # Station actions (the short list after choosing a station) can be spoken by name too.
        action=next((a for a in ((*RADIO,*RADIO_STATION) if self.section=='radio' else PODCASTS) if cmd==a.label.casefold()),None)
        if action:return self.open_form(action.form) if action.form else self.run(action.command)
        return self.run(text)

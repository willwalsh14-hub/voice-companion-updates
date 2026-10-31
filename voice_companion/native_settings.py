"""Self-voicing settings made from standard Windows controls, with staged edits."""
import ctypes as c
from ctypes import wintypes as w
import queue
import threading
import uuid
import copy
from settings_model import CATEGORIES
from menu_navigation import boundary_notice, next_match,menu_label

def choice_feedback(value,index,count):
    return str(value)


# What kind of control each setting is, said with it, as a screen reader would (Will, October 30, 2026):
# "Announce email header names, check box, not checked." "Speech rate, combo box, 0, 11 of 21."
ROLES={'check':'check box','choice':'combo box','combo':'edit combo box','text':'edit','password':'password edit',
       'action':'button','headers':'list box'}


def check_words(value):
    return 'checked' if value else 'not checked'


def control_words(field,value,index=None,count=None):
    """The label, the kind of control, and its value, for a setting that gets focus."""
    role=ROLES.get(field.kind,'')
    if field.kind=='check':return field.label+', check box, '+check_words(value)+'.'
    if field.kind=='action':return field.label+', button.'
    if field.kind=='password':return field.label+', password edit, hidden.'   # never says whether one is typed
    if field.kind=='choice':
        if not getattr(field,'choices',()):return field.label+', combo box, unavailable.'
        where=', '+str(index+1)+' of '+str(count) if index is not None and count and 0<=index<count else ''
        return field.label+', combo box, '+(str(value) if str(value) else 'none')+where+'.'
    if field.kind in ('combo','text'):
        return field.label+', '+role+', '+(str(value) if str(value).strip() else 'blank')+'.'
    return field.label+(', '+role if role else '')+', '+str(value)+'.'


BUTTON_WORDS={'OK':'OK, button. Saves your settings.','Cancel':'Cancel, button. Discards your changes.',
              'Close settings':'Close settings, button.'}

def settings_bottom(fields,step):
    """The lowest pixel the settings use, laid out step pixels apart (as render does)."""
    y=-17;bottom=0
    for field in fields:
        y+=step
        if field.kind=='headers':y+=90;bottom=y+49
        else:bottom=y+50
    return bottom


class NativeSettings:
    def __init__(self,session,announce,save,action,interrupt=None,preview=None,keyboard=None):
        self.last_notice=""
        def announce_notice(message):
            self.last_notice=str(message);announce(message)
        self.session=session;self.announce=announce_notice;self.interrupt=interrupt or (lambda:None);self.save_callback=save;self.action_callback=action
        self.actions=queue.Queue();self.ready=threading.Event();self.closed=threading.Event();self.hwnd=None;self.pending=False
        self.preview=preview
        # F12 says the time and, pressed twice quickly, the date, as on every other screen.
        from time_date import PressCounter
        self.keyboard=keyboard;self.clock_presses=PressCounter()
    def start(self):
        threading.Thread(target=self.run,name='Settings dialog',daemon=True).start()
        return self
    def command(self,text):self.actions.put(('voice',text))
    def accepted(self):self.actions.put(('accepted',None))
    def failed(self,message):self.actions.put(('failed',message))
    def cancel(self):self.actions.put(('cancel',None))
    def run(self):
        try:self._run()
        except Exception as exc:
            self.announce('Settings could not open: '+str(exc));self.closed.set();self.ready.set()
    def _run(self):
        u=c.WinDLL('user32',use_last_error=True);k=c.WinDLL('kernel32',use_last_error=True)
        result=c.c_ssize_t;callback=c.WINFUNCTYPE(result,w.HWND,w.UINT,w.WPARAM,w.LPARAM)
        class WC(c.Structure):
            _fields_=[('style',w.UINT),('proc',callback),('class_extra',c.c_int),('window_extra',c.c_int),('instance',w.HINSTANCE),('icon',w.HICON),('cursor',w.HANDLE),('background',w.HBRUSH),('menu',w.LPCWSTR),('name',w.LPCWSTR)]
        def api(name,restype,args):
            f=getattr(u,name);f.restype=restype;f.argtypes=args;return f
        create=api('CreateWindowExW',w.HWND,[w.DWORD,w.LPCWSTR,w.LPCWSTR,w.DWORD,c.c_int,c.c_int,c.c_int,c.c_int,w.HWND,w.HMENU,w.HINSTANCE,w.LPVOID])
        send=api('SendMessageW',result,[w.HWND,w.UINT,w.WPARAM,w.LPARAM]);destroy=api('DestroyWindow',w.BOOL,[w.HWND])
        focus=api('SetFocus',w.HWND,[w.HWND]);get_focus=api('GetFocus',w.HWND,[])
        parent=api('GetParent',w.HWND,[w.HWND])
        default=api('DefWindowProcW',result,[w.HWND,w.UINT,w.WPARAM,w.LPARAM])
        api('RegisterClassW',w.ATOM,[c.POINTER(WC)]);api('SetWindowTextW',w.BOOL,[w.HWND,w.LPCWSTR]);api('GetWindowTextW',c.c_int,[w.HWND,w.LPWSTR,c.c_int])
        api('ShowWindow',w.BOOL,[w.HWND,c.c_int]);api('SetForegroundWindow',w.BOOL,[w.HWND]);api('EnableWindow',w.BOOL,[w.HWND,w.BOOL]);api('IsWindow',w.BOOL,[w.HWND]);api('GetNextDlgTabItem',w.HWND,[w.HWND,w.HWND,w.BOOL])
        api('GetKeyState',c.c_short,[c.c_int]);api('IsWindowEnabled',w.BOOL,[w.HWND])
        api('GetMessageW',w.BOOL,[c.POINTER(w.MSG),w.HWND,w.UINT,w.UINT]);api('IsDialogMessageW',w.BOOL,[w.HWND,c.POINTER(w.MSG)]);api('TranslateMessage',w.BOOL,[c.POINTER(w.MSG)]);api('DispatchMessageW',result,[c.POINTER(w.MSG)])
        api('SetTimer',c.c_size_t,[w.HWND,c.c_size_t,w.UINT,c.c_void_p]);api('PostQuitMessage',None,[c.c_int])
        api('GetKeyState',c.c_short,[c.c_int])
        k.GetModuleHandleW.restype=w.HMODULE;k.GetModuleHandleW.argtypes=[w.LPCWSTR];instance=k.GetModuleHandleW(None)
        headers=[None];header_buttons={};controls={};children=[];last_focus=[None];category=[None];buttons={};alive=[True];editing=[False];baseline=[copy.deepcopy(self.session.values)]
        def control(cls,label,style,x,y,width,height,identifier,extended=0):
            hwnd=create(extended,cls,label,0x50000000|style,x,y,width,height,self.hwnd,identifier,instance,None)
            if not hwnd:raise c.WinError(c.get_last_error())
            return hwnd
        def text(hwnd):
            buf=c.create_unicode_buffer(4096);u.GetWindowTextW(hwnd,buf,len(buf));return buf.value
        def raw(hwnd,field):
            if field.kind=='headers':return self.session.values[field.key]
            if field.kind=='check':return bool(send(hwnd,0xF0,0,0))
            if field.kind=='choice':
                index=int(send(hwnd,0x147,0,0))
                return field.choices[index] if 0<=index<len(field.choices) else self.session.values.get(field.key,'')
            return text(hwnd)
        def collect():
            for hwnd,field in controls.items():
                if field.kind=='action' or (field.kind=='choice' and not field.choices):continue
                self.session.set(field.key,raw(hwnd,field))
        def focused_control():
            # An editable combo box (File manager options) gives keyboard focus to the edit box
            # inside it. Remember the combo itself, as the focus watcher in the timer does;
            # otherwise the watcher sees a "change" and speaks the field again, which cut off
            # "File manager options dialog" just after it began.
            hwnd=get_focus()
            return parent(hwnd) if parent(hwnd) in controls else hwnd
        def describe(hwnd):
            if hwnd not in controls and parent(hwnd) in controls:hwnd=parent(hwnd)
            if hwnd==category[0]:return menu_label(self.session.category_name())+', '+str(self.session.category+1)+' of '+str(len(CATEGORIES))+'.'
            if hwnd in buttons:return buttons[hwnd]
            field=controls.get(hwnd)
            if not field:return ''
            if field.kind=='headers':return field.label.split(';')[0]+', list box. '+headers[0].label()+'. Space enables or disables. Alt+Up or Alt+Down changes order.'
            value=raw(hwnd,field)
            if field.kind=='choice' and field.choices:
                return control_words(field,value,int(send(hwnd,0x147,0,0)),len(field.choices))
            return control_words(field,value)
        def changed_value(hwnd):
            field=controls[hwnd];value=raw(hwnd,field)
            notice=check_words(value) if field.kind=='check' else str(value)
            if field.choices:
                index=int(send(hwnd,0x147,0,0));notice=choice_feedback(notice,index,len(field.choices))
            return notice.strip()
        def announce_value(hwnd):
            field=controls[hwnd];notice=changed_value(hwnd)
            if self.preview and field.key in ('rate','pitch','volume','engine','windows_voice','espeak_voice','ai_voice'):
                self.preview(dict(self.session.values),field.label+' '+notice+'. This is a sample of your voice settings. Press Escape to cancel changes.')
            else:self.announce(notice)
        def render(focus_key=None):
            for hwnd in children:destroy(hwnd)
            children.clear();controls.clear();buttons.clear();header_buttons.clear();headers[0]=None;last_focus[0]=None
            u.ShowWindow(category[0],0 if editing[0] else 5)
            u.SetWindowTextW(self.hwnd,'Voice Companion Options - '+self.session.category_name() if editing[0] else 'Voice Companion Options (Alt+O)')
            if not editing[0]:
                close_button=control('BUTTON','Close settings',0x10000,595,530,100,32,2)
                children.append(close_button);buttons[close_button]=BUTTON_WORDS['Close settings']
                focus(category[0]);return
            # Settings sit 62 pixels apart. A long category (Email gained a Thunderbird window
            # setting in 0.2.174) packs them closer, so the last one never lands on the
            # OK and Cancel row at 530 and stays reachable.
            fields=self.session.current_fields()
            step=62
            while step>48 and settings_bottom(fields,step)>510:step-=2
            y=-17
            for index,field in enumerate(fields):
                y+=step
                if field.kind not in ('check','action'):
                    children.append(control('STATIC',field.label,0,225,y,470,22,0))
                value=self.session.values.get(field.key,False if field.kind=='check' else '')
                if field.kind=='check':
                    hwnd=control('BUTTON',field.label,0x10000|3,225,y+22,480,27,200+index);send(hwnd,0xF1,int(bool(value)),0)
                elif field.kind=='action':hwnd=control('BUTTON',field.label,0x10000,225,y+22,450,27,200+index)
                elif field.kind=='headers':
                    from email_headers import HeaderEditor
                    headers[0]=HeaderEditor(self.session)
                    hwnd=control('LISTBOX','',0x10000|0x200000|1,225,y+22,460,86,200+index)
                    for i in range(4):
                        buf=c.create_unicode_buffer(headers[0].label(i));send(hwnd,0x180,0,c.cast(buf,c.c_void_p).value)
                    send(hwnd,0x186,0,0)
                    for j,(label,action) in enumerate((('Enable / disable','toggle'),('Move up','up'),('Move down','down'))):
                        button=control('BUTTON',label,0x10000,225+j*155,y+110,145,27,300+j)
                        children.append(button);buttons[button]=label+', button.';header_buttons[button]=action
                    y+=90
                elif field.kind in ('choice','combo'):
                    hwnd=control('COMBOBOX','',0x10000|0x200000|(3 if field.kind=='choice' else 2),225,y+22,460,300,200+index)
                    for item in field.choices:
                        buf=c.create_unicode_buffer(item);send(hwnd,0x143,0,c.cast(buf,c.c_void_p).value)
                    if value in field.choices:send(hwnd,0x14E,field.choices.index(value),0)
                    elif field.kind=='combo':u.SetWindowTextW(hwnd,str(value))
                    elif field.choices:send(hwnd,0x14E,0,0)
                    else:u.EnableWindow(hwnd,False)
                else:hwnd=control('EDIT',str(value),0x10000|0x80|(0x20 if field.kind=='password' else 0),225,y+22,460,28,200+index,0x200)
                controls[hwnd]=field;children.append(hwnd)
            ok=control('BUTTON','OK',0x10000|1,485,530,100,32,1);cancel=control('BUTTON','Cancel',0x10000,595,530,100,32,2)
            buttons[ok]=BUTTON_WORDS['OK'];buttons[cancel]=BUTTON_WORDS['Cancel'];children.extend((ok,cancel))
            target=next((h for h,f in controls.items() if f.key==focus_key),next((h for h in controls if u.IsWindowEnabled(h)),ok));focus(target)
        def change_header(action):
            model=headers[0]
            if model is None:return
            hwnd=next(h for h,f in controls.items() if f.kind=='headers')
            index=int(send(hwnd,0x188,0,0));model.index=max(0,min(3,index))
            try:
                message=model.toggle() if action=='toggle' else model.move(-1 if action=='up' else 1)
            except ValueError as exc:self.announce(str(exc));return
            send(hwnd,0x184,0,0)
            for i in range(4):
                buf=c.create_unicode_buffer(model.label(i));send(hwnd,0x180,0,c.cast(buf,c.c_void_p).value)
            send(hwnd,0x186,model.index,0);self.announce(message)
        def close(saved=False):
            if not saved:self.session.cancel()
            self.closed.set();alive[0]=False;destroy(self.hwnd)
        def show_menu(discard=False):
            if discard:self.session.values=copy.deepcopy(baseline[0])
            editing[0]=False;self.pending=False;render();self.announce('Options menu. '+describe(category[0]));last_focus[0]=category[0]
        def cancel_category():
            if editing[0]:self.announce('Changes canceled.');show_menu(True)
            else:self.announce('Settings closed.');close()
        def open_category():
            baseline[0]=copy.deepcopy(self.session.values);editing[0]=True;render()
            self.announce(self.session.category_name()+' dialog. '+describe(get_focus()));last_focus[0]=focused_control()
        def save():
            if not editing[0]:open_category();return
            if self.pending:return
            try:collect()
            except ValueError as exc:self.announce(str(exc));return
            self.pending=True;self.save_callback(dict(self.session.values))
        def select_category(index):
            if not 0<=index<len(CATEGORIES):self.announce('Start of list.' if index<0 else 'End of list.');return
            if editing[0]:self.session.values=copy.deepcopy(baseline[0]);editing[0]=False
            self.session.category=max(0,min(len(CATEGORIES)-1,index));send(category[0],0x186,self.session.category,0)
            render();self.announce(describe(category[0]));last_focus[0]=category[0]
        def voice(text_command):
            cmd=text_command.lower().strip().rstrip('.!?')
            if cmd in ('toggle header','enable or disable header','move header up','move header down') and headers[0]:
                change_header('toggle' if cmd in ('toggle header','enable or disable header') else 'up' if cmd.endswith('up') else 'down');return
            if cmd in ('next header','previous header') and headers[0]:
                hwnd=next(h for h,f in controls.items() if f.kind=='headers')
                self.announce(headers[0].select(-1 if cmd.startswith('previous') else 1));send(hwnd,0x186,headers[0].index,0);focus(hwnd);last_focus[0]=hwnd;return
            if cmd in ('voice','speech','go to voice','go to speech'):cmd='voice'
            action=next((key for key,value in self.session.context.get('actions',{}).items() if cmd==value),None)
            if action:self.action_callback(action);close();return
            if cmd in ('read setting','say setting','read current setting'):
                self.announce(describe(get_focus()));return
            if cmd in ('ok','okay','confirm','confirm that','save','save settings','that one'):save();return
            if cmd in ('close settings','cancel settings'):self.announce('Settings closed.');close();return
            if cmd in ('cancel','back','settings menu','back to settings'):cancel_category();return
            cmd=cmd.replace('web browsing','web browser')
            name=cmd.removeprefix('go to ').removeprefix('settings ').removeprefix('select ')
            name={'documents':'word processor','documents options':'word processor options','notes':'notepad','notes options':'notepad options','updates':'auto-update','updates options':'auto-update options','auto update':'auto-update','auto update options':'auto-update options'}.get(name,name)
            match=next((i for i,n in enumerate(CATEGORIES) if name in (n.lower(),n.removesuffix(' options').lower())),None)
            if match is not None:select_category(match);open_category();return
            if cmd in ('next setting','previous setting'):
                focus(u.GetNextDlgTabItem(self.hwnd,get_focus(),cmd.startswith('previous')));return
            if cmd in ('first item','start of list','last item','end of list'):
                hwnd=get_focus();hwnd=parent(hwnd) if parent(hwnd) in controls else hwnd;field=controls.get(hwnd);bottom=cmd in ('last item','end of list')
                if hwnd==category[0]:select_category(len(CATEGORIES)-1 if bottom else 0)
                elif field and field.kind=='headers':
                    headers[0].index=3 if bottom else 0;send(hwnd,0x186,headers[0].index,0);self.announce(headers[0].label())
                elif field and field.choices:
                    index=len(field.choices)-1 if bottom else 0;send(hwnd,0x14E,index,0);self.session.set(field.key,field.choices[index]);announce_value(hwnd)
                return
            if cmd in ('next','previous','next category','previous category'):
                delta=-1 if cmd.startswith('previous') else 1
                hwnd=get_focus();hwnd=parent(hwnd) if parent(hwnd) in controls else hwnd;field=controls.get(hwnd)
                if hwnd==category[0] or cmd.endswith('category'):select_category(self.session.category+delta)
                elif field and field.choices:
                    index=int(send(hwnd,0x147,0,0))
                    if not 0<=index+delta<len(field.choices):self.announce('Start of list.' if delta<0 else 'End of list.');return
                    index+=delta;send(hwnd,0x14E,index,0);self.session.set(field.key,field.choices[index]);announce_value(hwnd)
                else:focus(u.GetNextDlgTabItem(self.hwnd,hwnd,delta<0))
                return
            setting=self.session.voice_setting(text_command)
            if setting:
                collect();key,value=setting
                if not editing[0]:baseline[0]=copy.deepcopy(self.session.values);editing[0]=True
                message=self.session.set(key,value)
                self.session.category=next(i for i,name in enumerate(CATEGORIES) if any(f.key==key for f in __import__('settings_model').fields(name,self.session.context)))
                send(category[0],0x186,self.session.category,0);render(key);announce_value(focused_control()) if key in ('rate','pitch','volume','engine','windows_voice','espeak_voice','ai_voice') else self.announce(message);last_focus[0]=focused_control();return
            if cmd in ('on','off','toggle'):
                hwnd=get_focus();hwnd=parent(hwnd) if parent(hwnd) in controls else hwnd;field=controls.get(hwnd)
                if field and field.kind=='check':
                    value=not raw(hwnd,field) if cmd=='toggle' else cmd=='on';send(hwnd,0xF1,int(value),0);self.session.set(field.key,value);announce_value(hwnd);return
            field=controls.get(get_focus())
            if field and field.kind=='action' and cmd in ('open','activate','click'):
                self.action_callback(field.key);close();return
            self.announce('Setting not recognized. Say a category, next setting, a setting name and value, OK, or cancel.')
        @callback
        def proc(hwnd,msg,wp,lp):
            try:
                if msg==0x10:self.announce('Settings canceled.');close();return 0
                if msg==2:u.PostQuitMessage(0);return 0
                if msg==0x400:return 0x534B0001 # Default OK button for dialog navigation.
                if msg==0x111:
                    identifier=wp&0xFFFF;notice=(wp>>16)&0xFFFF
                    if identifier==1:save();return 0
                    if identifier==2:cancel_category();return 0
                    if identifier==100 and notice==1:select_category(int(send(category[0],0x188,0,0)));return 0
                    if lp in header_buttons and notice==0:change_header(header_buttons[lp]);return 0
                    if lp in controls:
                        field=controls[lp]
                        if field.kind=='headers' and notice==1:
                            headers[0].index=max(0,min(3,int(send(lp,0x188,0,0))));self.announce(describe(lp));return 0
                        if field.kind=='action' and notice==0:self.action_callback(field.key);close();return 0
                        if (field.kind=='check' and notice==0) or (field.kind in ('choice','combo') and notice==1):
                            self.session.set(field.key,raw(lp,field));announce_value(lp);last_focus[0]=lp
                            if field.key=='podcast_feed':render(field.key)
                        return 0
                if msg==0x113:
                    u.SetWindowTextW(status_handle,self.last_notice)
                    while not self.actions.empty():
                        action,payload=self.actions.get_nowait()
                        if action=='accepted':self.session.original=copy.deepcopy(self.session.values);close(saved=True);return 0
                        if action=='cancel':close();return 0
                        if action=='failed':self.pending=False;self.announce(payload)
                        if action=='voice' and not self.pending:voice(payload)
                        if not alive[0]:return 0
                    current=get_focus()
                    if parent(current) in controls:current=parent(current)
                    if current!=last_focus[0]:
                        last_focus[0]=current;message=describe(current)
                        if message:self.announce(message)
                    return 0
            except Exception as exc:self.announce('Settings: '+str(exc));self.pending=False;return 0
            return default(hwnd,msg,wp,lp)
        class_name='VoiceCompanionSettings'+uuid.uuid4().hex
        wc=WC(0,proc,0,0,instance,None,None,6,None,class_name)
        if not u.RegisterClassW(c.byref(wc)):raise c.WinError(c.get_last_error())
        work=w.RECT(0,0,u.GetSystemMetrics(0),u.GetSystemMetrics(1))
        u.SystemParametersInfoW(0x30,0,c.byref(work),0)
        x=work.left+max(0,(work.right-work.left-750)//2)
        y=work.top+max(0,(work.bottom-work.top-680)//2)
        self.hwnd=create(0x10000,class_name,'Voice Companion Settings',0x10C80000,x,y,750,680,None,None,instance,None)
        if not self.hwnd:raise c.WinError(c.get_last_error())
        status_handle=control('STATIC','',0,20,570,695,45,400)
        control('STATIC','Categories',0,15,15,185,22,0)
        category[0]=control('LISTBOX','',0x10000|0x200000|0x800000|1,15,45,185,475,100)
        for name in CATEGORIES:
            buf=c.create_unicode_buffer(menu_label(name));send(category[0],0x180,0,c.cast(buf,c.c_void_p).value)
        send(category[0],0x186,self.session.category,0);render();last_focus[0]=category[0];self.announce('Options menu. Use arrows to choose a category, then Enter to open. '+describe(category[0]));u.ShowWindow(self.hwnd,5);u.SetForegroundWindow(self.hwnd);focus(category[0]);u.SetTimer(self.hwnd,1,100,None);self.ready.set()
        message=w.MSG()
        while u.GetMessageW(c.byref(message),None,0,0)>0:
            if message.message==0x104 and message.wParam==0x73:
                self.action_callback('exit_app');close();continue
            if message.message==0x100 and message.wParam==0x11:self.interrupt();continue
            # Keys that work on every screen: F9 turns the microphone off or on; F3 says what
            # is playing; Control+F1 goes to the main menu (the app closes Options first).
            if message.message==0x100 and message.wParam==0x78 and self.keyboard:self.keyboard('F9');continue
            if message.message==0x100 and message.wParam==0x72 and self.keyboard and not (u.GetKeyState(0x11)&0x8000 or u.GetKeyState(0x10)&0x8000):self.keyboard('F3');continue
            if message.message==0x100 and message.wParam==0x70 and u.GetKeyState(0x11)&0x8000 and self.keyboard:self.keyboard('Ctrl+F1');continue
            if message.message==0x100 and message.wParam==0x7B and self.keyboard and not (u.GetKeyState(0x11)&0x8000 or u.GetKeyState(0x10)&0x8000):
                from time_date import key_request
                self.keyboard(key_request(self.clock_presses.press()));continue
            if message.message in (0x100,0x104) and headers[0]:
                target=get_focus();field=controls.get(target)
                if field and field.kind=='headers':
                    if message.wParam==0x20:change_header('toggle');continue
                    if message.wParam in (0x26,0x28) and u.GetKeyState(0x12)&0x8000:
                        change_header('up' if message.wParam==0x26 else 'down');continue
            if message.message==0x100 and message.wParam in (0x24,0x23) and get_focus()==category[0] and not editing[0]:
                index=0 if message.wParam==0x24 else len(CATEGORIES)-1
                if index==self.session.category:self.announce('Start of list.' if message.wParam==0x24 else 'End of list.');continue
                select_category(index);continue
            if message.message==0x104 and message.wParam==ord('O'):
                if editing[0]:cancel_category()
                else:focus(category[0]);self.announce('Options menu. '+describe(category[0]))
                continue
            if message.message==0x100 and 65<=message.wParam<=90 and get_focus()==category[0] and not editing[0]:
                index=next_match(CATEGORIES,self.session.category,chr(message.wParam))
                if index==self.session.category:self.announce('End of matching items.');continue
                if index is not None:select_category(index)
                else:self.announce('No category starts with '+chr(message.wParam)+'.')
                continue
            current=get_focus();current=parent(current) if parent(current) in controls else current
            field=controls.get(current)
            if message.message==0x100 and field and field.kind=='headers' and message.wParam in (0x26,0x28,0x24,0x23):
                model=headers[0];old=max(0,int(send(current,0x188,0,0)))
                model.index=0 if message.wParam==0x24 else 3 if message.wParam==0x23 else max(0,min(3,old+(-1 if message.wParam==0x26 else 1)))
                if model.index==old and message.wParam in (0x26,0x28,0x24,0x23):self.announce('Start of list.' if message.wParam in (0x26,0x24) else 'End of list.');continue
                send(current,0x186,model.index,0);self.announce(model.label());continue
            if message.message==0x100 and field and field.kind=='choice' and field.choices and (message.wParam in (0x26,0x28,0x24,0x23) or 65<=message.wParam<=90):
                old=int(send(current,0x147,0,0))
                if message.wParam in (0x24,0x23):index=0 if message.wParam==0x24 else len(field.choices)-1
                elif message.wParam in (0x26,0x28):index=max(0,min(len(field.choices)-1,old+(-1 if message.wParam==0x26 else 1)))
                else:index=next_match(field.choices,old,chr(message.wParam))
                if index is None:self.announce('No matching option.');continue
                if index==old and message.wParam in (0x26,0x28,0x24,0x23):self.announce('Start of list.' if message.wParam in (0x26,0x24) else 'End of list.');continue
                if index==old and 65<=message.wParam<=90:self.announce('End of matching items.');continue
                send(current,0x14E,index,0);self.session.set(field.key,field.choices[index]);announce_value(current);continue
            if message.message==0x100 and message.wParam==0x1B:cancel_category();continue
            if message.message==0x100 and message.wParam in (0x26,0x28,0x25,0x27) and get_focus()==category[0] and not editing[0]:
                select_category(self.session.category+(-1 if message.wParam in (0x26,0x25) else 1));continue
            if message.message==0x100 and message.wParam==9:
                current=get_focus()
                if parent(current) in controls:current=parent(current)
                target=u.GetNextDlgTabItem(self.hwnd,current,bool(u.GetKeyState(0x10)&0x8000))
                focus(target);last_focus[0]=target
                self.announce(describe(target));continue
            if message.message==0x100 and message.wParam==0x0D:
                target=get_focus()
                if target in header_buttons:change_header(header_buttons[target])
                elif get_focus() in buttons and (buttons[get_focus()].startswith('Cancel') or not editing[0]):cancel_category()
                elif target in controls and controls[target].kind=='action':self.action_callback(controls[target].key);close()
                else:save()
                continue
            if not u.IsDialogMessageW(self.hwnd,c.byref(message)):
                u.TranslateMessage(c.byref(message));u.DispatchMessageW(c.byref(message))
            target=message.hWnd
            control_handle=target if target in controls else parent(target)
            field=controls.get(control_handle)
            if field and field.kind in ('text','password','combo'):
                if message.message==0x102 and message.wParam>=32:
                    self.announce('Hidden character.' if field.kind=='password' else chr(message.wParam))
                elif message.message==0x100 and message.wParam in (8,46):
                    self.announce('Deleted.' if field.kind=='password' else describe(control_handle))
                elif message.message==0x100 and message.wParam in (0x25,0x27,0x24,0x23):
                    if field.kind=='password':self.announce('Hidden.')
                    else:
                        start=c.c_uint();end=c.c_uint()
                        send(target,0xB0,c.addressof(start),c.addressof(end))
                        value=text(target)
                        self.announce(value[start.value:end.value] if start.value!=end.value else value[start.value:start.value+1] or 'End of field.')
        self.closed.set()



"""Self-voicing settings made from standard Windows controls, with staged edits."""
import ctypes as c
from ctypes import wintypes as w
import queue
import threading
import uuid
import copy
from settings_model import CATEGORIES

class NativeSettings:
    def __init__(self,session,announce,save,action):
        self.session=session;self.announce=announce;self.save_callback=save;self.action_callback=action
        self.actions=queue.Queue();self.ready=threading.Event();self.closed=threading.Event();self.hwnd=None;self.pending=False
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
        controls={};children=[];last_focus=[None];category=[None];buttons={};alive=[True];editing=[False];baseline=[copy.deepcopy(self.session.values)]
        def control(cls,label,style,x,y,width,height,identifier,extended=0):
            hwnd=create(extended,cls,label,0x50000000|style,x,y,width,height,self.hwnd,identifier,instance,None)
            if not hwnd:raise c.WinError(c.get_last_error())
            return hwnd
        def text(hwnd):
            buf=c.create_unicode_buffer(4096);u.GetWindowTextW(hwnd,buf,len(buf));return buf.value
        def raw(hwnd,field):
            if field.kind=='check':return bool(send(hwnd,0xF0,0,0))
            if field.kind=='choice':
                index=int(send(hwnd,0x147,0,0))
                return field.choices[index] if 0<=index<len(field.choices) else self.session.values.get(field.key,'')
            return text(hwnd)
        def collect():
            for hwnd,field in controls.items():
                if field.kind=='action' or (field.kind=='choice' and not field.choices):continue
                self.session.set(field.key,raw(hwnd,field))
        def describe(hwnd):
            if hwnd not in controls and parent(hwnd) in controls:hwnd=parent(hwnd)
            if hwnd==category[0]:return self.session.category_name()+', '+str(self.session.category+1)+' of '+str(len(CATEGORIES))
            if hwnd in buttons:return buttons[hwnd]
            field=controls.get(hwnd)
            if not field:return ''
            if field.kind=='password':return field.label+'. Hidden.'
            if field.kind=='action':return field.label+'. Button.'
            value=raw(hwnd,field)
            return field.label+', '+('on' if value is True else 'off' if value is False else value)
        def render(focus_key=None):
            for hwnd in children:destroy(hwnd)
            children.clear();controls.clear();buttons.clear();last_focus[0]=None
            u.ShowWindow(category[0],0 if editing[0] else 5)
            u.SetWindowTextW(self.hwnd,'Voice Companion Settings - '+self.session.category_name() if editing[0] else 'Voice Companion Settings')
            if not editing[0]:
                close_button=control('BUTTON','Close settings',0x10000,595,530,100,32,2)
                children.append(close_button);buttons[close_button]='Close settings.'
                focus(category[0]);return
            for index,field in enumerate(self.session.current_fields()):
                y=45+index*62
                if field.kind not in ('check','action'):
                    children.append(control('STATIC',field.label,0,225,y,470,22,0))
                value=self.session.values.get(field.key,False if field.kind=='check' else '')
                if field.kind=='check':
                    hwnd=control('BUTTON',field.label,0x10000|3,225,y+22,480,27,200+index);send(hwnd,0xF1,int(bool(value)),0)
                elif field.kind=='action':hwnd=control('BUTTON',field.label,0x10000,225,y+22,450,27,200+index)
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
            buttons[ok]='OK. Save settings.';buttons[cancel]='Cancel. Discard changes.';children.extend((ok,cancel))
            target=next((h for h,f in controls.items() if f.key==focus_key),next((h for h in controls if u.IsWindowEnabled(h)),ok));focus(target)
        def close(saved=False):
            if not saved:self.session.cancel()
            self.closed.set();alive[0]=False;destroy(self.hwnd)
        def show_menu(discard=False):
            if discard:self.session.values=copy.deepcopy(baseline[0])
            editing[0]=False;self.pending=False;render();self.announce('Settings menu. '+describe(category[0]));last_focus[0]=category[0]
        def cancel_category():
            if editing[0]:self.announce('Changes canceled.');show_menu(True)
            else:self.announce('Settings closed.');close()
        def open_category():
            baseline[0]=copy.deepcopy(self.session.values);editing[0]=True;render()
            self.announce(self.session.category_name()+'. '+describe(get_focus()));last_focus[0]=get_focus()
        def save():
            if not editing[0]:open_category();return
            if self.pending:return
            try:collect()
            except ValueError as exc:self.announce(str(exc));return
            self.pending=True;self.save_callback(dict(self.session.values))
        def select_category(index):
            if editing[0]:self.session.values=copy.deepcopy(baseline[0]);editing[0]=False
            self.session.category=max(0,min(len(CATEGORIES)-1,index));send(category[0],0x186,self.session.category,0)
            render();self.announce(describe(category[0]));last_focus[0]=category[0]
        def voice(text_command):
            cmd=text_command.lower().strip().rstrip('.!?')
            action=next((key for key,value in self.session.context.get('actions',{}).items() if cmd==value),None)
            if action:self.action_callback(action);close();return
            if cmd in ('read setting','say setting','read current setting'):
                self.announce(describe(get_focus()));return
            if cmd in ('ok','okay','confirm','confirm that','save','save settings','that one'):save();return
            if cmd in ('close settings','cancel settings'):self.announce('Settings closed.');close();return
            if cmd in ('cancel','back','settings menu','back to settings'):cancel_category();return
            name=cmd.removeprefix('go to ').removeprefix('settings ').removeprefix('select ')
            match=next((i for i,n in enumerate(CATEGORIES) if name==n.lower()),None)
            if match is not None:select_category(match);open_category();return
            if cmd in ('next setting','previous setting'):
                focus(u.GetNextDlgTabItem(self.hwnd,get_focus(),cmd.startswith('previous')));return
            if cmd in ('next','previous','next category','previous category'):
                delta=-1 if cmd.startswith('previous') else 1
                hwnd=get_focus();hwnd=parent(hwnd) if parent(hwnd) in controls else hwnd;field=controls.get(hwnd)
                if hwnd==category[0] or cmd.endswith('category'):select_category(self.session.category+delta)
                elif field and field.choices:
                    index=int(send(hwnd,0x147,0,0));index=max(0,min(len(field.choices)-1,index+delta));send(hwnd,0x14E,index,0);self.session.set(field.key,field.choices[index]);self.announce(describe(hwnd))
                else:focus(u.GetNextDlgTabItem(self.hwnd,hwnd,delta<0))
                return
            setting=self.session.voice_setting(text_command)
            if setting:
                collect();key,value=setting
                if not editing[0]:baseline[0]=copy.deepcopy(self.session.values);editing[0]=True
                message=self.session.set(key,value)
                self.session.category=next(i for i,name in enumerate(CATEGORIES) if any(f.key==key for f in __import__('settings_model').fields(name,self.session.context)))
                send(category[0],0x186,self.session.category,0);render(key);self.announce(message);last_focus[0]=get_focus();return
            if cmd in ('on','off','toggle'):
                hwnd=get_focus();hwnd=parent(hwnd) if parent(hwnd) in controls else hwnd;field=controls.get(hwnd)
                if field and field.kind=='check':
                    value=not raw(hwnd,field) if cmd=='toggle' else cmd=='on';send(hwnd,0xF1,int(value),0);self.session.set(field.key,value);self.announce(describe(hwnd));return
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
                    if lp in controls:
                        field=controls[lp]
                        if field.kind=='action' and notice==0:self.action_callback(field.key);close();return 0
                        if (field.kind=='check' and notice==0) or (field.kind in ('choice','combo') and notice==1):
                            self.session.set(field.key,raw(lp,field));self.announce(describe(lp))
                            if field.key=='podcast_feed':render(field.key)
                        return 0
                if msg==0x113:
                    while not self.actions.empty():
                        action,payload=self.actions.get_nowait()
                        if action=='accepted':self.session.original=copy.deepcopy(self.session.values);show_menu();return 0
                        if action=='cancel':close();return 0
                        if action=='failed':self.pending=False;self.announce(payload)
                        if action=='voice' and not self.pending:voice(payload)
                        if not alive[0]:return 0
                    current=get_focus()
                    if current!=last_focus[0]:
                        last_focus[0]=current;message=describe(current)
                        if message:self.announce(message)
                    return 0
            except Exception as exc:self.announce('Settings: '+str(exc));self.pending=False;return 0
            return default(hwnd,msg,wp,lp)
        class_name='VoiceCompanionSettings'+uuid.uuid4().hex
        wc=WC(0,proc,0,0,instance,None,None,6,None,class_name)
        if not u.RegisterClassW(c.byref(wc)):raise c.WinError(c.get_last_error())
        self.hwnd=create(0x10000,class_name,'Voice Companion Settings',0x10C80000,100,80,750,620,None,None,instance,None)
        if not self.hwnd:raise c.WinError(c.get_last_error())
        control('STATIC','Categories',0,15,15,185,22,0)
        category[0]=control('LISTBOX','',0x10000|0x200000|0x800000|1,15,45,185,475,100)
        for name in CATEGORIES:
            buf=c.create_unicode_buffer(name);send(category[0],0x180,0,c.cast(buf,c.c_void_p).value)
        send(category[0],0x186,self.session.category,0);render();self.announce('Settings menu. Use arrows to choose a category, then Enter to open. '+describe(category[0]));u.ShowWindow(self.hwnd,5);u.SetForegroundWindow(self.hwnd);focus(category[0]);u.SetTimer(self.hwnd,1,100,None);self.ready.set()
        message=w.MSG()
        while u.GetMessageW(c.byref(message),None,0,0)>0:
            if message.message==0x100 and message.wParam==0x1B:cancel_category();continue
            if message.message==0x100 and message.wParam in (0x26,0x28,0x25,0x27) and get_focus()==category[0] and not editing[0]:
                select_category((self.session.category+(-1 if message.wParam in (0x26,0x25) else 1))%len(CATEGORIES));continue
            if message.message==0x100 and message.wParam==9:
                current=get_focus()
                if parent(current) in controls:current=parent(current)
                target=u.GetNextDlgTabItem(self.hwnd,current,bool(u.GetKeyState(0x10)&0x8000))
                focus(target);last_focus[0]=target
                self.announce(describe(target));continue
            if message.message==0x100 and message.wParam==0x0D:
                target=get_focus()
                if get_focus() in buttons and (buttons[get_focus()].startswith('Cancel') or not editing[0]):cancel_category()
                elif target in controls and controls[target].kind=='action':self.action_callback(controls[target].key);close()
                else:save()
                continue
            # Handle Space explicitly in this custom window's message loop.
            # Keep native checkbox state, staged value, and speech in sync.
            if message.message in (0x100,0x101) and message.wParam==0x20:
                target=get_focus();field=controls.get(target)
                if field and field.kind=='check':
                    if message.message==0x100 and not (message.lParam & (1<<30)):
                        value=not raw(target,field)
                        send(target,0xF1,int(value),0)
                        self.session.set(field.key,value);self.announce(describe(target))
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



"""Small status window for the Windows voice application.

The window lives on its own UI thread. Other threads only post text to a queue.
Closing the window requests a clean shutdown of the microphone loop.
"""
import queue
import threading
import time
from field_selection import FieldSelection
from keyboard_text import caret_feedback,typing_feedback,phonetic,character as spoken_character


def foreground_window(root,control=None):
    """Return keyboard ownership after an explicit application navigation."""
    import sys
    if sys.platform!='win32':return
    import ctypes
    user=ctypes.windll.user32
    hwnd=user.GetAncestor(root.winfo_id(),2)
    current=ctypes.windll.kernel32.GetCurrentThreadId()
    other=user.GetWindowThreadProcessId(user.GetForegroundWindow(),None)
    attached=False
    try:
        if other and current!=other:attached=bool(user.AttachThreadInput(current,other,True))
        user.BringWindowToTop(hwnd);user.SetForegroundWindow(hwnd)
        if control is not None:user.SetFocus(control.winfo_id())
    finally:
        if attached:user.AttachThreadInput(current,other,False)


def entry_feedback(before, after, old_caret, caret, key, character=""):
    """Short spoken feedback for a standard editable email entry."""
    names = {'@':'at sign', '.':'dot', '-':'hyphen', '_':'underscore', ' ':'space'}
    def say(char): return names[char] if char in names else spoken_character(char)
    if key == 'Left': return say(after[caret]) if caret < len(after) else 'End of field'
    if key == 'Right': return say(after[caret]) if caret < len(after) else 'End of field'
    if key == 'Home': return 'Start of field'
    if key == 'End': return 'End of field'
    if key in ('BackSpace','Delete') and len(after) < len(before):
        if key=='Delete':return say(after[caret:caret+1])
        removed = before[caret:old_caret] if key == 'BackSpace' else before[old_caret:old_caret+1]
        return say(removed[-1]) if removed else 'Deleted'
    if character and character.isprintable(): return say(character)
    if len(after) > len(before):
        return say(after[max(0,caret-1)])
    return None


def show_context_text(old, field, seq, web_cursor_changed=False):
    """Whether the editor takes the app's text and caret from a new context.

    In a web text box, keys typed since the app's last acknowledgment are
    still on their way to it. Replacing the box then would undo those letters
    and make the next key look like a conflicting edit."""
    if old.get('source') != field.get('source'):
        return True
    if (field.get('mode') == 'web' and field.get('web_field') and not field.get('readonly')
            and field.get('ack', 0) < seq):
        return False
    return bool(web_cursor_changed or field.get('focus', False) or field.get('ack', 0) >= seq)


class CompanionWindow:
    def __init__(self, version, data_folder=None):
        self.version = version
        self.data_folder = data_folder
        self.settings_results = queue.Queue()
        self.settings_notices = queue.Queue()
        self.messages = queue.Queue()
        self.commands = queue.Queue()
        self.ui_actions = queue.Queue()
        self.key_feedback = queue.Queue()
        self.closed = threading.Event()
        self.ready = threading.Event()
        self.last_email_request = None
        self.email_selection_edit = None
        self.problem = None
        self.context = {'mode':'awake','source':None,'text':'','echo':'characters','phonetic':True,'delay':0.5,'ack':0}
        self.keyboard_output = None
        self.last_name_request = None
        self.command_entry_active=False
        self.last_navigation=0.0
        self.media_shortcut=None
        self.thread = threading.Thread(target=self._thread_main, name='Voice Companion window', daemon=True)

    def _thread_main(self):
        try:self._run()
        finally:
            if self.media_shortcut:self.media_shortcut.close()
            # Tcl interpreters and widget cycles must be released on their owner thread.
            self.closed.set()
            while True:
                try:self.ui_actions.get_nowait()
                except queue.Empty:break
            self.root=self.typed=self.editor=self.current=self.file_list=None
            self.file_toolbar=None;self.file_buttons=[];self.update_buttons=[]
            import gc
            gc.collect()

    def start(self):
        self.thread.start()
        self.ready.wait(5)
        if self.problem:
            raise RuntimeError('The Voice Companion window could not open.') from self.problem

    def show(self, message):
        if not self.closed.is_set():
            self.messages.put(str(message))

    def set_context(self,context):
        if not self.closed.is_set():self.ui_actions.put(('context',context))

    def wait_for_context(self):
        # Native browser focus calls can complete before the queued reading-view
        # update. Finish that update before the next physical key is accepted.
        if self.closed.is_set() or threading.current_thread() is self.thread:return
        done=threading.Event();self.ui_actions.put(('ui_call',done.set));done.wait(1)

    def feedback(self,text):
        if text:
            self.show(text)
            if getattr(self,'keyboard_output',None) and not self.keyboard_output.problem:self.keyboard_output.speak(text)
            else:self.key_feedback.put(text)

    def focus_email_field(self, field, value=''):
        """Give typed entry focus on the UI thread when email needs input."""
        request = (field, value)
        if not self.closed.is_set():
            if request == self.last_email_request:
                self.ui_actions.put(('email_focus', None))
            else:
                self.last_email_request = request
                self.ui_actions.put(('email_field', request))

    def select_email_text(self, command):
        self.email_selection_edit = None
        request = {'command': command, 'done': threading.Event(), 'canceled': threading.Event()}
        if self.closed.is_set(): return 'The typing window is closed.'
        self.ui_actions.put(('email_select', request))
        if not request['done'].wait(3):
            request['canceled'].set()
            return 'The typing window did not respond. Try the selection again.'
        if request.get('edited_text') is not None:
            self.email_selection_edit = (request['field_name'],request['edited_text'])
        return request['result']

    def clear_email_field(self):
        self.last_email_request = None
        if not self.closed.is_set(): self.ui_actions.put(('clear_email', None))

    def submit_entry(self, value):
        if self.context.get('account_private'):
            # Yahoo app password: sent straight to setup, never shown or spoken.
            self.commands.put(('account_secret',value));return True
        if self.context.get('media_input'):
            self.commands.put(('media_field',{'value':value,'next':'Enter'}));return True
        if self.context.get('web_private'):
            self.commands.put(('web_private_value',dict(value=value,next='Done',**({'field_source':self.context['web_field_source']} if self.context.get('web_field_source') else {}))));return True
        value = value.strip()
        if value:
            self.commands.put(('web_search_query',value) if self.context.get('mode')=='search' else 'name document '+value if self.context.get('mode')=='document_name' else value)
            self.last_email_request = None
            self.command_entry_active=False
            return True
        return False

    def report_entry_key(self, before, after, old_caret, caret, key, character=""):
        if self.context.get('account_private'):
            added=len(after)-len(before)
            if added>1:self.feedback(str(added)+' hidden characters pasted.')
            elif added==1:self.feedback('Hidden character.')
            elif added<0:self.feedback('Deleted.' if not after else 'Deleted. '+str(len(after))+' hidden characters left.')
            else:self.feedback('Private field. '+str(len(after))+' hidden characters.' if after else 'Private field. Empty.')
            return
        if self.context.get('web_private'):
            if before!=after:self.feedback('Hidden character.' if len(after)>len(before) else 'Deleted.')
            else:self.feedback('Private field.')
            return
        echo=getattr(self,'context',{}).get('echo','characters')
        notice=typing_feedback(before,after,caret,echo,key,old_caret) if before!=after else entry_feedback(before,after,old_caret,caret,key)
        self.feedback(notice)

    def setup_ai_voice(self):
        if not self.closed.is_set():
            self.ui_actions.put(('ai_voice', None))

    def _run(self):
        try:
            import tkinter as tk
            from tkinter import scrolledtext
            root = tk.Tk()
            self.root=root
            from media_shortcut import MediaShortcut
            self.media_shortcut=MediaShortcut(lambda:self.commands.put(('keyboard','Ctrl+P')))
            self.media_shortcut.register()
            root.bind('<Destroy>',lambda event:self.closed.set() if event.widget==root else None)
            root.title('Voice Companion ' + self.version)
            root.geometry('660x560')
            root.option_add('*Font',('Segoe UI',12))
            root.minsize(470, 300)
            root.protocol('WM_DELETE_WINDOW', lambda: (flush_pending_edit(), self.commands.put(('keyboard','Alt+F4'))))
            heading = tk.Label(root, text='Voice Companion', font=('Segoe UI', 20, 'bold'))
            heading.pack(anchor='w', padx=18, pady=(16, 4))
            startup_label=tk.Label(root, text='Say “Wake up” to begin. Say “What version?” to check this build.',
                     font=('Segoe UI', 11), wraplength=600, justify='left')
            startup_label.pack(anchor='w', padx=18)
            entry_label = tk.Label(root, text='Type a command or email address, then press Enter:',
                                   font=('Segoe UI', 11),wraplength=600,justify='left')
            entry_label.pack(anchor='w', padx=18, pady=(8, 0))
            typed = tk.Entry(root, font=('Segoe UI', 12))
            self.typed=typed
            typed.pack(fill='x', padx=18)
            self.email_field = None
            file_list=tk.Listbox(root,font=('Segoe UI',12),exportselection=False,selectmode='multiple')
            self.file_list=file_list
            file_toolbar=tk.Frame(root)
            self.file_toolbar=file_toolbar;self.file_buttons=[]
            for label,request in (('Search','search files'),('Search subfolders','search subfolders'),('Sort','sort files'),('Clear search','clear search'),('Drives','drive list'),('Main menu','main menu')):
                button=tk.Button(file_toolbar,text=label,command=lambda command=request:self.commands.put(command),takefocus=True)
                button.pack(side='left',padx=3);self.file_buttons.append(button)
                button.bind('<FocusIn>',lambda event,name=label:self.feedback(name+' button. Enter activates. Tab moves to the next control.'))
                button.bind('<Return>',lambda event,command=request:(self.commands.put(command),'break')[1])
            update_toolbar=tk.Frame(root)
            self.update_buttons=[]
            for label,request,hint in (('Status','update status','Alt+S or F5'),('Cancel update','cancel update','Alt+C or Escape')):
                button=tk.Button(update_toolbar,text=label,command=lambda command=request:self.commands.put(command),takefocus=True)
                button.pack(side='left',padx=3);self.update_buttons.append(button)
                button.bind('<FocusIn>',lambda event,name=label,key=hint:self.feedback(name+' button. '+key+'. Enter activates.'))
                button.bind('<Return>',lambda event,command=request:(self.commands.put(command),'break')[1])
            def update_tab(event):
                controls=self.update_buttons
                index=controls.index(event.widget) if event.widget in controls else -1
                controls[(index+(-1 if event.state&1 or event.keysym=='ISO_Left_Tab' else 1))%len(controls)].focus_force()
                return 'break'
            for button in self.update_buttons:
                for sequence in ('<Tab>','<Shift-Tab>','<ISO_Left_Tab>'):button.bind(sequence,update_tab)
            def file_tab(event):
                if self.context.get('file_name_input'):return
                controls=[file_list,*self.file_buttons]
                index=controls.index(event.widget) if event.widget in controls else 0
                backward=bool(event.state&1) or event.keysym=='ISO_Left_Tab'
                target=controls[(index+(-1 if backward else 1))%len(controls)]
                target.focus_set()
                if target==file_list:self.feedback('File list. Use arrows or type the first letters of an item.')
                return 'break'
            for widget in [file_list,*self.file_buttons]:
                widget.bind('<Tab>',file_tab);widget.bind('<Shift-Tab>',file_tab);widget.bind('<ISO_Left_Tab>',file_tab)
            activation_keys=set()
            document_modifiers=set()
            caps_modifier={'before':False,'used':False,'synthetic':0}
            def release_document_modifier(event):
                document_modifiers.discard(event.keysym)
                if event.keysym=='Caps_Lock':
                    if caps_modifier['synthetic']:caps_modifier['synthetic']-=1;return
                    if caps_modifier['used']:
                        import sys
                        if sys.platform=='win32':
                            import ctypes
                            if bool(ctypes.windll.user32.GetKeyState(0x14)&1)!=caps_modifier['before']:
                                caps_modifier['synthetic']=2
                                ctypes.windll.user32.keybd_event(0x14,0,0,0)
                                ctypes.windll.user32.keybd_event(0x14,0,2,0)
                        caps_modifier['used']=False
            root.bind_all('<KeyRelease>',release_document_modifier,add='+')
            previous = ['', 0]
            field_selection = FieldSelection()
            # Alt pressed and let go with no other key opens the screen's Alt menu
            # (screen_menus.py). Alt+O, Alt+T, Alt+F4 and other Alt keys never do.
            alt_tap={'alone':False,'when':0.0}
            def watch_alt(event):
                if event.keysym in ('Alt_L','Alt_R'):
                    if not alt_tap['alone']:alt_tap.update(alone=True,when=time.monotonic())
                else:alt_tap['alone']=False
            root.bind('<KeyPress>',watch_alt,add='+')
            root.bind('<FocusOut>',lambda event:alt_tap.update(alone=False),add='+')
            def alt_release(event):
                if event.keysym not in ('Alt_L','Alt_R'):return
                alone=alt_tap['alone'] and time.monotonic()-alt_tap['when']<2;alt_tap['alone']=False
                if not alone or event.widget.winfo_toplevel()!=root:return
                from screen_menus import alt_menu_key
                if not alt_menu_key(self.context,self.command_entry_active,typed.get()):return
                if self.keyboard_output:self.keyboard_output.interrupt()
                flush_pending_edit();cancel_phonetic()
                mode=self.context.get('mode');opening=not self.context.get('screen_menu')
                if opening and mode=='email_draft' and self.email_field:
                    # Keep what was typed in the address or subject box first.
                    self.commands.put(('email_field_tab',{'field':self.email_field,'value':typed.get(),'backward':False,'key':'Alt'}));return 'break'
                if opening and mode=='note' and typed.get().strip():
                    self.commands.put(typed.get().strip());typed.delete(0,'end')
                self.commands.put(('keyboard','Alt'));return 'break'
            root.bind_all('<KeyRelease>',alt_release,add='+')
            def shortcut(event):
                if event.keysym.lower()=='p' and event.state&4 and not event.state&(1|8|0x20000):
                    if not self.media_shortcut.registered:self.commands.put(('keyboard','Ctrl+P'))
                    return 'break'
                if event.widget.winfo_toplevel()!=root:return
                mode=self.context.get('mode');key=event.keysym
                self.last_navigation=time.monotonic()
                control=bool(event.state&4);shift=bool(event.state&1)
                watch_alt(event)
                if self.context.get('screen_menu') and not self.command_entry_active:
                    # An Alt menu is open: arrows, Enter, Escape, letters and numbers belong to it.
                    from screen_menus import overlay_key
                    menu_key=overlay_key(key,event.char,event.state)
                    if menu_key is not None:
                        if menu_key:
                            if self.keyboard_output:self.keyboard_output.interrupt()
                            flush_pending_edit();cancel_phonetic();self.commands.put(('keyboard',menu_key))
                        return 'break'
                if mode=='files' and control and key.lower()=='e':
                    self.command_entry_active=False
                    typed.delete(0,'end')
                    self.commands.put(('keyboard','Ctrl+E'));return 'break'
                if mode=='update_download':
                    if key in ('Tab','ISO_Left_Tab'):return update_tab(event)
                    update_key='Alt+'+key.upper() if event.state&(8|0x20000) else key
                    if update_key in ('F5','Alt+S','Escape','Alt+C'):
                        self.commands.put(('keyboard',update_key));return 'break'
                if mode in ('document','file_reader') and not self.command_entry_active:
                    if key in ('Insert','Caps_Lock'):
                        if key=='Caps_Lock':
                            if caps_modifier['synthetic']:caps_modifier['synthetic']-=1;return 'break'
                            if key not in document_modifiers:caps_modifier.update(before=bool(event.state&2),used=False)
                        document_modifiers.add(key);return 'break'
                    prefix='CapsLock' if 'Caps_Lock' in document_modifiers else 'Insert' if 'Insert' in document_modifiers else None
                    chord=prefix+('+' if prefix=='CapsLock' or not shift else '+Shift+')+('F' if key.lower()=='f' else key) if prefix else None
                    if chord in ('Insert+F','CapsLock+F','Insert+Shift+Down','CapsLock+Down'):
                        if prefix=='CapsLock':caps_modifier['used']=True
                        flush_pending_edit();send_text_position();cancel_phonetic()
                        self.commands.put(('keyboard',chord));return 'break'
                if mode=='file_reader' and not self.command_entry_active and not control and not event.state&(8|0x20000):
                    if key.lower() in ('h','t','k','l','b','n') or key=='Return':
                        send_text_position();self.commands.put(('keyboard',('Shift+' if shift else '')+key.upper() if key!='Return' else 'Enter'));return 'break'
                if mode=='document_menu' and not control and not event.state&(8|0x20000) and event.char and event.char.isalnum():
                    self.commands.put(('keyboard','MenuPick:'+event.char));return 'break'
                if mode=='files' and not self.command_entry_active and not self.context.get('file_name_input'):
                    file_key=('Ctrl+Shift+' if shift else 'Ctrl+')+key.upper() if control else 'Alt' if key in ('Alt_L','Alt_R') else 'Enter' if key=='Return' else 'Space' if key=='space' else key
                    if control and shift:
                        file_key='Ctrl+Shift+'+(chr(event.keycode) if 48<=event.keycode<=57 else key if key in ('Up','Down') else key.upper())
                    if event.state&(8|0x20000) and key.lower() in ('s','n','c','m','d','a','z'):file_key='Alt+'+key.upper()
                    if event.state&(8|0x20000) and key=='Home':file_key='Alt+Home'
                    if control and key=='Home':file_key='Ctrl+Home'
                    if event.widget in self.file_buttons and not control and file_key not in ('Alt+S','Alt+N','Alt+C','Alt+M','Alt+D','Alt+A','Alt+Z','Alt+Home','Escape'):return
                    if event.widget==file_list and not control and not event.state&(8|0x20000) and event.char and event.char.isprintable() and key!='space':
                        self.commands.put(('keyboard','Letter:'+event.char));return 'break'
                    if file_key in ('Up','Down','Left','Right','Home','End','Enter','Space','Escape','Alt','F1','F2','Delete','Ctrl+C','Ctrl+X','Ctrl+V','Ctrl+A','Ctrl+Shift+A','F5','Ctrl+D','Ctrl+F','Ctrl+Shift+F','Ctrl+Shift+X','Alt+S','Alt+N','Alt+C','Alt+M','Alt+D','Alt+A','Alt+Z','Alt+Home','Ctrl+Shift+1','Ctrl+Shift+2','Ctrl+Shift+3','Ctrl+Shift+4','Ctrl+Shift+Up','Ctrl+Shift+Down','BackSpace'):
                        self.commands.put(('keyboard',file_key));return 'break'
                if mode=='web' and not self.command_entry_active and not self.context.get('web_private'):
                    from web_modes import toggle_chord
                    # Insert+Space, or Control+Alt+Space when a screen reader owns Insert.
                    if key=='Insert':document_modifiers.add(key);return 'break'
                    if toggle_chord(key,event.state,'Insert' in document_modifiers):
                        if self.keyboard_output:self.keyboard_output.interrupt()
                        flush_pending_edit();cancel_phonetic()
                        self.commands.put(('keyboard','Web:ToggleAppMode'));return 'break'
                if not self.command_entry_active and mode!='task_input':
                    from input_parity import screen_key
                    direct=screen_key(mode,key,event.state,self.context,self.email_field,typed.get())
                    if direct:
                        if self.keyboard_output:self.keyboard_output.interrupt()
                        flush_pending_edit();cancel_phonetic()
                        if mode=='email_draft' and self.email_field:
                            # Keep what was typed in the address or subject box before acting.
                            self.commands.put(('email_field_tab',{'field':self.email_field,'value':typed.get(),'backward':False,'key':direct}));return 'break'
                        if mode=='note' and typed.get().strip():
                            # Add the typed words to the note first, as Enter would.
                            self.commands.put(typed.get().strip());typed.delete(0,'end')
                        self.commands.put(('keyboard',direct));return 'break'
                if mode in ('document','word_processor','document_menu','recent_documents','media_menu','local_media','task_input'):
                    from task_navigation import SHORTCUTS
                    if mode=='document' and self.context.get('document_table') and key in ('Tab','ISO_Left_Tab'):
                        flush_pending_edit();cancel_phonetic()
                        self.commands.put(('keyboard','Doc:PreviousCell' if shift or key=='ISO_Left_Tab' else 'Doc:NextCell'));return 'break'
                    chord=('Ctrl+Shift+' if shift else 'Ctrl+')+(key if key in ('Home','End','Left','Right') else 'Enter' if key=='Return' else key.upper()) if control else 'Alt' if key in ('Alt_L','Alt_R') else 'Shift+F4' if shift and key=='F4' else key
                    if control and shift and 48<=event.keycode<=57:chord='Ctrl+Shift+'+chr(event.keycode)
                    if event.state&(8|0x20000) and key.lower()=='o':chord='Alt+O'
                    elif control and event.state&(8|0x20000):chord='Ctrl+Alt+'+(key if key in ('Up','Down','Left','Right') else key.upper())
                    elif event.state&(8|0x20000) and not control and key in ('Up','Down','Left','Right'):chord='Alt+'+key
                    requests=set(SHORTCUTS)|{'Alt','Ctrl+O','Ctrl+N','Ctrl+R','Alt+O'}
                    if mode=='document':requests-= {'Ctrl+Home','Ctrl+End'}
                    if mode=='local_media':requests|={'space','Left','Right','Up','Down','Escape','Tab','ISO_Left_Tab','Return','Ctrl+I'}
                    if mode=='document_menu' and key in ('Alt_L','Alt_R'):chord='Escape'
                    if chord in requests and not (mode=='task_input' or self.command_entry_active):
                        flush_pending_edit();cancel_phonetic()
                        self.commands.put(('keyboard','Space' if chord=='space' else 'Enter' if chord=='Return' else 'ShiftTab' if chord=='ISO_Left_Tab' else chord));return 'break'
                if mode=='files' and self.context.get('file_name_input') and event.widget==typed and key!='Escape':return
                if key.lower()=='t' and not control and event.state&(8|0x20000):
                    flush_pending_edit();self.command_entry_active=True;typed.focus_set();return 'break'
                if mode in ('web','files') and self.command_entry_active and event.widget==typed and key=='Escape':
                    self.command_entry_active=False;typed.delete(0,'end');(file_list if mode=='files' else editor).focus_set();return 'break'
                request=None
                if self.context.get('confirmation') is not None and key.lower() in ('y','n') and event.state&(8|0x20000):
                    self.commands.put(('keyboard','Alt+'+key.upper()));return 'break'
                if key=='F4' and event.state&(8|0x20000):
                    flush_pending_edit();self.commands.put(('keyboard','Alt+F4'));return 'break'
                if key in ('Control_L','Control_R'):
                    cancel_phonetic()
                    if local['reading_after'] is not None:root.after_cancel(local['reading_after']);local['reading_after']=None
                    if self.keyboard_output:self.keyboard_output.interrupt()
                    self.commands.put(('keyboard','Control'));return 'break'
                if key in ('Up','Down','Left','Right','Tab','ISO_Left_Tab','Home','End','Escape') or self.context.get('menu_letters') and event.char.isalpha():
                    if self.keyboard_output:self.keyboard_output.interrupt()
                if key in ('Home','End') and not control and not self.context.get('source') and not self.email_field and not typed.get():
                    self.commands.put(('keyboard',key));return 'break'
                if key.lower()=='o' and event.state&(8|0x20000):
                    flush_pending_edit();self.commands.put(('keyboard','settings'));return 'break'
                # A name is editable text, even when empty. Its letters must
                # never select a main-menu item or become an app command.
                if (mode=='document_name' or self.context.get('browser_name_input') or self.context.get('task_input')) and event.widget==typed and key!='Escape':return
                if mode in ('media','podcast') and self.context.get('media_navigation'):
                    if self.context.get('media_input') and key in ('Tab','ISO_Left_Tab','Return'):
                        self.commands.put(('media_field',{'value':typed.get(),'next':'Enter' if key=='Return' else 'ShiftTab' if shift or key=='ISO_Left_Tab' else 'Tab'}));typed.delete(0,'end');return 'break'
                    if key=='Escape':request='Escape'
                    elif key in ('Tab','ISO_Left_Tab'):request='ShiftTab' if shift or key=='ISO_Left_Tab' else 'Tab'
                    elif not control and not self.context.get('media_input') and key in ('Up','Down','Left','Right','Home','End','Return','space','Delete','F5') and not typed.get():request='Enter' if key=='Return' else 'Space' if key=='space' else key
                    elif control and shift and key.lower()=='p':request='Ctrl+Shift+P'
                    elif control and key.lower() in ('f','p','r','s','d'):request='Ctrl+'+key.upper()
                    elif control and key in ('Left','Right') and not self.context.get('media_input'):request='Ctrl+'+key
                if mode=='web' and (self.context.get('web_app_mode') or self.context.get('web_slider')) and not self.command_entry_active and not self.context.get('web_private'):
                    # App mode sends keys to the page; a slider takes its arrow keys.
                    from web_modes import window_request
                    request=window_request(key,event.char,event.state,self.context)
                    if request=='':return 'break'
                if request:pass
                elif mode=='regular_browser' and not control and not typed.get() and key in ('Up','Down','Home','End','Return','space','Escape'):
                    request='Enter' if key=='Return' else 'Space' if key=='space' else key
                elif mode=='browser_menu' and not self.command_entry_active and not self.context.get('browser_name_input') and not control and not event.state&(8|0x20000) and event.char and event.char.isalnum():request='Letter:'+event.char
                elif mode=='note' and control and not shift and not event.state&(8|0x20000) and key.lower()=='o':request='Ctrl+O'
                elif mode=='web' and not control and event.state&(8|0x20000) and key in ('Left','Right'):request='Web:Back' if key=='Left' else 'Web:Forward'
                elif mode=='web' and key=='F5':request='Web:Refresh'
                elif mode=='web' and key in ('F6','F7'):request=key
                elif mode=='web' and control and key.lower()=='l':request='Web:Search'
                elif mode=='web' and control and not shift and not event.state&(8|0x20000) and key in ('Home','End') and not self.context.get('web_private'):request='Web:PageTop' if key=='Home' else 'Web:PageBottom'
                if self.context.get('confirmation') is not None and key=='Return' and event.widget==typed and typed.get().strip().lower() in ('yes','no'):
                    self.commands.put(typed.get().strip().lower());typed.delete(0,'end');return 'break'
                if request:pass
                elif self.context.get('confirmation') is not None and key in ('Up','Down','Left','Right','Home','End','Tab','Return','Escape','space','y','Y','n','N'):
                    request='Enter' if key in ('Return','space') else 'ShiftTab' if key=='Tab' and shift else key
                elif key=='F4' and event.state&8:request='Alt+F4'
                elif mode=='web' and self.context.get('web_name_list') and not self.command_entry_active and not control and not event.state&(8|0x20000) and (event.char and event.char.isprintable() or key=='BackSpace'):
                    request='Web:ListBackspace' if key=='BackSpace' else 'Web:ListText:'+event.char
                elif mode=='web' and (self.context.get('web_browse') or self.context.get('web_field')) and not (event.widget==typed and self.command_entry_active):
                    field=None if local.get('web_escape_pending') else self.context.get('web_field')
                    if field and self.context.get('web_private') and (key in ('Return','Tab','ISO_Left_Tab','Up','Down') or control and key in ('Home','End')):
                        if key=='Return':
                            if key in activation_keys:return 'break'
                            activation_keys.add(key)
                        self.commands.put(('web_private_value',dict(value=typed.get(),next=('PageTop' if key=='Home' else 'PageBottom') if control and key in ('Home','End') else 'Leave:'+key if key in ('Up','Down') else 'ShiftTab' if shift or key=='ISO_Left_Tab' else 'Tab' if key=='Tab' else 'Done',**({'field_source':self.context['web_field_source']} if self.context.get('web_field_source') else {}))));typed.delete(0,'end');return 'break'
                    elif key=='Escape':request='Web:Escape'
                    elif control and event.state&(8|0x20000) and key in ('Up','Down','Left','Right','Home','End','Prior','Next'):request='Web:Table:'+key
                    elif field and self.context.get('web_choice') and key in ('Up','Down','Left','Right','Home','End'):request='Web:'+key
                    elif field and self.context.get('web_choice') and not control and not event.state&(8|0x20000) and len(event.char)==1 and event.char.isalnum():request='Web:ChoiceLetter:'+event.char
                    elif field and self.context.get('web_combo_edit') and not control and not shift and not event.state&(8|0x20000) and key in ('Up','Down'):request='Web:'+key
                    elif field and not self.context.get('web_private') and not control and not shift and key in ('Up','Down','Left','Right'):
                        value=editor.get('1.0','end-1c');caret=len(editor.get('1.0','insert'))
                        multiline=field.get('tag')=='textarea' or field.get('editable')
                        if key=='Up' and (not multiline or '\n' not in value[:caret]) or key=='Down' and (not multiline or '\n' not in value[caret:]) or key=='Left' and caret==0 or key=='Right' and caret==len(value):request='Web:Leave:'+key
                    elif key=='F5':request='Web:Refresh'
                    elif key in ('Tab','ISO_Left_Tab'):request='Web:ShiftTab' if shift or key=='ISO_Left_Tab' else 'Web:Tab'
                    elif not field and not control and not event.state&(8|0x20000) and event.char.lower() in 'khfbxcetr alidgmps123456'.replace(' ','') and len(event.char)==1:request='Web:Quick:'+('Previous:' if shift else '')+event.char.lower()
                    elif key=='Return' and (not field or field.get('tag')!='textarea' and not field.get('editable') or control or self.context.get('web_combo_edit')):request='Web:Done' if field and not self.context.get('web_choice') else 'Web:Activate'
                    elif not field and not shift and key in ('Up','Down','Left','Right','Home','End','Prior','Next'):request='Web:Read:'+('Control:' if control else '')+key
                    elif not field and key=='space':request='Web:Space'
                    elif key=='Left' and event.state&(8|0x20000):request='Web:Back'

                elif self.context.get('help_reader') and not shift and not event.state&(8|0x20000) and key in ('Up','Down','Left','Right','Home','End') and not (event.widget==typed and self.command_entry_active):
                    request='Help:'+('Ctrl+' if control and key in ('Left','Right') else '')+key
                elif mode=='document' and key=='F2':request='F2'
                elif mode=='mailbox':
                    if self.context.get('source') and self.context.get('email_html') and event.widget==self.editor and not control and not event.state&(8|0x20000) and key.lower() in ('k','b'):
                        request='Email:'+('previous ' if shift else 'next ')+('link' if key.lower()=='k' else 'button')
                    elif shift and key in ('Up','Down') and not self.context.get('source') and not self.typed.get():request='Shift'+key
                    elif key=='F2':request='F2'
                    elif key=='F5':request='F5'
                    elif key=='Delete' and not self.context.get('source') and not self.typed.get():request='Delete'
                    elif key=='space' and not self.context.get('source') and not self.typed.get():request='Space'
                    elif control:
                        code=('Ctrl+Shift+' if shift else 'Ctrl+')+key.upper()
                        if code in ('Ctrl+Y','Ctrl+R','Ctrl+Shift+R','Ctrl+F','Ctrl+N','Ctrl+Shift+C','Ctrl+Shift+N','Ctrl+Shift+V','Ctrl+Shift+E','Ctrl+Shift+D') or code=='Ctrl+A' and not self.context.get('source'):request=code
                elif mode=='email_draft' and key in ('Tab','ISO_Left_Tab'):
                    flush_pending_edit();cancel_phonetic()
                    backward=shift or key=='ISO_Left_Tab'
                    if self.email_field:
                        self.commands.put(('email_field_tab',{'field':self.email_field,'value':typed.get(),'backward':backward}))
                    else:self.commands.put(('keyboard','ShiftTab' if backward else 'Tab'))
                    return 'break'
                elif mode=='email_draft' and ((control and key=='Return') or (key.lower()=='s' and event.state&(8|0x20000))):
                    if self.email_field:
                        self.commands.put(('email_field_tab',{'field':self.email_field,'value':typed.get(),'backward':False,'send':True}));return 'break'
                    request='Ctrl+Enter' if control else 'Alt+S'
                elif self.context.get('menu_letters') and not (mode in ('browser_menu','files') and self.command_entry_active) and not self.context.get('file_name_input') and not self.context.get('media_input') and not control and not event.state&(8|0x20000) and len(event.char)==1 and event.char.isalpha() and not typed.get():
                    request='Letter:'+event.char.lower()
                if mode=='web' and not request and (self.context.get('web_browse') or local.get('web_escape_pending')) and not self.command_entry_active and event.char and event.char.isprintable() and not control and not event.state&(8|0x20000):return 'break'
                if request:
                    if request in ('Web:Activate','Web:Space','Web:Done'):
                        if key in activation_keys:return 'break'
                        activation_keys.add(key)
                    if self.keyboard_output:self.keyboard_output.interrupt()
                    flush_pending_edit();cancel_phonetic()
                    # Escape in a search box with suggestions may only close them; keep typing there.
                    if request=='Web:Escape' and self.context.get('web_field') and not self.context.get('web_combo_edit'):local['web_escape_pending']=True
                    if self.context.get('web_browse') and request.startswith(('Web:Quick:','Web:Read:','Web:Table:','Web:Page')):
                        local['web_navigation_pending']=True
                        if local['reading_after'] is not None:root.after_cancel(local['reading_after']);local['reading_after']=None
                    self.commands.put(('keyboard',request));return 'break'
            root.bind_all('<Alt-F4>',shortcut)
            root.bind_all('<Alt-o>',shortcut)
            root.bind_all('<Alt-O>',shortcut)
            root.bind_all('<Alt-s>',shortcut)
            root.bind_all('<Alt-S>',shortcut)
            for sequence in ('<Alt-y>','<Alt-Y>','<Alt-n>','<Alt-N>'):root.bind_all(sequence,shortcut)
            root.bind_all('<Tab>',shortcut)
            root.bind_all('<Shift-Tab>',shortcut)
            file_list.bind('<KeyPress>',shortcut)
            root.bind_all('<KeyPress>',shortcut,add='+')
            root.bind_all('<KeyRelease>',lambda event:activation_keys.discard(event.keysym),add='+')
            root.bind('<FocusOut>',lambda event:activation_keys.clear() if event.widget==root else None,add='+')
            for sequence in ('<F2>','<F5>','<F6>','<F7>','<Delete>','<Control-y>','<Control-r>','<Control-Shift-R>','<Control-f>','<Control-n>','<Control-Shift-V>','<Control-Shift-E>','<Control-Shift-D>','<Control-Return>'):
                root.bind_all(sequence,shortcut)
            def before_key(event):
                if shortcut(event)=='break':return 'break'
                previous[:] = [typed.get(), typed.index('insert')]
                cancel_phonetic()
                if event.keysym in ('Up','Down','Left','Right') and not typed.get() and not self.email_field and self.context.get('mode') not in ('document_name','search'):return
                before=typed.get();old_caret=typed.index('insert')
                def after_entry():
                    caret=typed.index('insert');value=typed.get()
                    if event.state&4 and event.keysym in ('Left','Right'):
                        self.feedback(caret_feedback(value,caret,event.keysym,True)[0])
                    else:self.report_entry_key(before,value,old_caret,caret,event.keysym,event.char)
                    if event.keysym in ('Left','Right') and not event.state&4 and caret<len(value) and self.context.get('phonetic') and phonetic(value[caret]):
                        local['phonetic']=root.after(int(float(self.context.get('delay',.5))*1000),lambda:self.feedback(phonetic(value[caret])))
                root.after_idle(after_entry)
            def after_key(event):
                self.report_entry_key(previous[0], typed.get(), previous[1], typed.index('insert'), event.keysym, event.char)
            typed.bind('<KeyPress>', before_key)
            def command_entry(event):
                self.command_entry_active=True;typed.focus_set();return 'break'
            root.bind_all('<Alt-t>',command_entry)
            root.after_idle(lambda:typed.focus_set() if self.context.get('source') is None and self.context.get('mode')!='settings' else None)
            def submit_typed(event=None):
                if self.submit_entry(typed.get()):
                    typed.delete(0, 'end')
                return 'break'
            def navigation(event):
                if shortcut(event)=='break':return 'break'
                if event.widget.winfo_toplevel() != root: return
                if event.keysym == 'Escape':
                    flush_pending_edit()
                    self.commands.put(('keyboard','Escape')); return 'break'
                if event.keysym=='Return' and isinstance(event.widget,tk.Button):
                    event.widget.invoke(); return 'break'
                if event.widget==editor:return
                if event.widget==typed and (self.email_field or typed.get() or self.context.get('mode') in ('document_name','search') or self.context.get('browser_name_input') or self.context.get('file_name_input')):return
                if event.widget == typed or event.widget == history or event.widget == root or isinstance(event.widget,tk.Button):
                    self.commands.put(('keyboard','Enter' if event.keysym=='Return' else event.keysym)); return 'break'
            def enter(event):
                if shortcut(event)=='break':return 'break'
                if self.context.get('file_name_input') and not typed.get().strip():
                    self.feedback('Enter a new file name, including the extension.');return 'break'
                if self.context.get('mode')=='document_name':
                    if not typed.get().strip():self.feedback('Enter a document name.');return 'break'
                    return submit_typed(event)
                if self.email_field or typed.get() or self.context.get('media_input'): return submit_typed(event)
                return navigation(event)
            typed.bind('<Return>', enter)
            for key in ('Up','Down','Left','Right','Escape'):
                root.bind_all('<'+key+'>',navigation)
            root.bind_all('<Return>',navigation,add='+')
            def settings_shortcut(event):
                flush_pending_edit()
                self.commands.put(('keyboard','settings')); return 'break'
            root.bind_all('<Alt-o>',settings_shortcut)
            root.bind_all('<Alt-O>',settings_shortcut)
            tk.Button(root,text='Options (Alt+O)',command=lambda:self.commands.put(('keyboard','settings'))).pack(anchor='w',padx=18)
            tk.Button(root, text='Enter typed command', command=submit_typed).pack(anchor='w', padx=18)
            tk.Button(root, text='AI voice setup (trainer)', command=self.setup_ai_voice).pack(anchor='w', padx=18)
            self.current = tk.StringVar(value='Starting speech and microphone...')
            status_label=tk.Label(root, textvariable=self.current, font=('Segoe UI', 12),
                     wraplength=600, justify='left')
            status_label.pack(anchor='w', padx=18, pady=14)
            def resize_labels(event):
                if event.widget==root:
                    for label in (startup_label,entry_label,status_label):label.configure(wraplength=max(1,event.width-36))
            root.bind('<Configure>',resize_labels,add='+')
            history = scrolledtext.ScrolledText(root, wrap='word', font=('Segoe UI', 11), state='disabled')
            history.pack(fill='both', expand=True, padx=18, pady=(0, 18))
            editor=tk.Text(root,wrap='word',font=('Segoe UI',12),undo=True,exportselection=False)
            self.editor=editor
            from visual_accessibility import readable_color,focus_style
            for widget in (typed,editor):focus_style(widget)
            editor.tag_configure('bold',font=('Segoe UI',12,'bold'))
            editor.tag_configure('italic',font=('Segoe UI',12,'italic'))
            editor.tag_configure('underline',underline=True)
            editor.tag_configure('heading',font=('Segoe UI',16,'bold'))
            editor.tag_configure('strike',overstrike=True)
            editor.tag_configure('superscript',offset=4)
            editor.tag_configure('subscript',offset=-4)
            editor.tag_configure('highlight',background='#ffff99',foreground='#000000')
            editor.tag_configure('code',font=('Consolas',12))
            def reader_link(position):
                editor.mark_set('insert','1.0 + '+str(position)+' chars');send_text_position()
                self.commands.put(('keyboard','Enter'));return 'break'
            def open_link(url):
                from email_reader import safe_link
                if safe_link(url):
                    import webbrowser
                    webbrowser.open(url)
            def link_at_caret():
                caret=offset()
                return next((span for span in self.context.get('rich_spans',[]) if span[2] in ('link','button') and span[0]<=caret<span[1]),None)
            local={'text':'','seq':0,'applying':False,'phonetic':None,'reading_after':None,'link':None,'web_navigation_pending':False,'web_escape_pending':False}
            def offset(index='insert'):return len(editor.get('1.0',index))
            def selection():
                try:return (offset('sel.first'),offset('sel.last'))
                except tk.TclError:return None
            def cancel_phonetic():
                if local['phonetic'] is not None:
                    root.after_cancel(local['phonetic']);local['phonetic']=None
            def read_caret(key,control=False):
                if local['web_navigation_pending']:return
                local['link']=None
                text=editor.get('1.0','end-1c');caret=offset()
                notice,char=caret_feedback(text,caret,key,control,selection())
                if not selection() and key in ('Up','Down') and not control:
                    notice=editor.get('insert display linestart','insert display lineend') or 'Blank.'
                    if self.context.get('rich_spans'):
                        from email_reader import MessageBody,spoken_range
                        start=offset('insert display linestart');end=offset('insert display lineend')
                        notice=spoken_range(MessageBody(text,spans=self.context['rich_spans']),start,end)
                        if notice.lstrip().startswith('• '):notice='Bullet '+notice.lstrip()[2:]
                        if any(a<=caret<b and style=='heading' for a,b,style,url in self.context['rich_spans']):notice='Heading. '+notice
                if self.context.get('web_elements') and key in ('Up','Down','Home','End') and not selection():
                    from web_browse import kind
                    roles=[]
                    for item in self.context['web_elements']:
                        if item.get('start',0)<=caret<item.get('end',0) and kind(item) in ('link','heading','button','edit','checkbox','radio','combobox','cell'):
                            role=kind(item)+(' level '+str(item.get('level',1)) if kind(item)=='heading' else '')
                            if role not in roles:roles.append(role)
                    if 'link' in roles:notice='Link '+notice;roles.remove('link')
                    if roles:notice+=', '+', '.join(roles)+'.'
                self.feedback(notice);cancel_phonetic()
                if char and self.context.get('phonetic') and phonetic(char):
                    local['phonetic']=root.after(int(float(self.context.get('delay',.5))*1000),lambda:self.feedback(phonetic(char)))
            def send_text_position(before=None):
                if self.context.get('web_browse') and local['web_navigation_pending']:return
                text=editor.get('1.0','end-1c');local['seq']+=1
                self.commands.put(('text_edit',{'source':self.context.get('source'),'before':local['text'] if before is None else before,'after':text,'caret':offset(),'selection':selection(),'seq':local['seq'],'readonly':self.context.get('readonly',False),'web_cursor_revision':self.context.get('web_cursor_revision')}))
                local['text']=text
            def changed(event=None):
                if not editor.edit_modified():return
                editor.edit_modified(False)
                if not local['applying']:send_text_position()
            editor.bind('<<Modified>>',changed)
            def flush_pending_edit():
                if self.context.get('source') and not self.context.get('readonly') and editor.get('1.0','end-1c')!=local['text']:
                    editor.edit_modified(False);send_text_position()
            def edit_key(event):
                if shortcut(event)=='break':return 'break'
                cancel_phonetic()
                if event.keysym=='Escape':return navigation(event)
                if event.keysym=='a' and event.state&4:
                    editor.tag_add('sel','1.0','end-1c');read_caret('Right');send_text_position();return 'break'
                nav=event.keysym in ('Left','Right','Up','Down','Home','End','Prior','Next')
                if self.context.get('readonly') and event.keysym=='Return' and link_at_caret():
                    span=link_at_caret()
                    if span[3]:open_link(span[3]);self.feedback('Opening '+span[2]+'.')
                    else:self.feedback('This email button has no safe link to open.')
                    return 'break'
                if self.context.get('readonly') and event.keysym in ('Tab','ISO_Left_Tab'):
                    from email_reader import MessageBody,message_controls
                    links=message_controls(MessageBody(editor.get('1.0','end-1c'),spans=self.context.get('rich_spans',[])))
                    caret=offset();backward=bool(event.state&1) or event.keysym=='ISO_Left_Tab'
                    candidates=[span for span in links if span[0]<caret] if backward else [span for span in links if span[0]>caret or caret==0 and span[0]==0 and local['link']!=0]
                    if candidates:
                        span=candidates[-1] if backward else candidates[0];local['link']=span[0]
                        editor.mark_set('insert','1.0 + '+str(span[0])+' chars');editor.tag_remove('sel','1.0','end');editor.tag_add('sel','1.0 + '+str(span[0])+' chars','1.0 + '+str(span[1])+' chars');editor.see('insert')
                        self.feedback(editor.get('sel.first','sel.last')+', '+span[2]+'. Enter to open.');send_text_position();return 'break'
                    target=editor.tk_focusPrev() if backward else editor.tk_focusNext()
                    if target:target.focus_set()
                    return 'break'
                if self.context.get('readonly') and not nav and event.keysym not in ('Shift_L','Shift_R','Control_L','Control_R','Tab'):
                    if event.state&4 and event.keysym.lower()=='c':return
                    return 'break'
                if event.state&4 and event.keysym in ('Up','Down'):
                    from keyboard_text import paragraph_destination
                    old_index=editor.index('insert')
                    destination=paragraph_destination(editor.get('1.0','end-1c'),offset(),-1 if event.keysym=='Up' else 1)
                    target='1.0 + '+str(destination)+' chars'
                    editor.mark_set('insert',target)
                    if event.state&1:
                        anchor=editor.index('anchor') if editor.tag_ranges('sel') else old_index
                        editor.mark_set('anchor',anchor);editor.tag_remove('sel','1.0','end')
                        a,b=sorted((anchor,editor.index('insert')),key=lambda i:tuple(map(int,i.split('.'))))
                        editor.tag_add('sel',a,b)
                    else:editor.tag_remove('sel','1.0','end')
                    editor.see('insert');read_caret(event.keysym,True);send_text_position();return 'break'
                before=editor.get('1.0','end-1c');old_caret=offset()
                def after():
                    if local['applying']:return
                    if nav:read_caret(event.keysym,bool(event.state&4));send_text_position()
                    else:self.feedback(typing_feedback(before,editor.get('1.0','end-1c'),offset(),self.context.get('echo','characters'),event.keysym,old_caret))
                if nav:
                    if local['reading_after'] is not None:root.after_cancel(local['reading_after'])
                    def latest_read():
                        local['reading_after']=None;after()
                    local['reading_after']=root.after_idle(latest_read)
                else:root.after_idle(after)
            editor.bind('<KeyPress>',edit_key)
            editor.bind('<<Paste>>',lambda event:'break' if self.context.get('readonly') else None)
            editor.bind('<<Cut>>',lambda event:'break' if self.context.get('readonly') else None)
            editor.bind('<FocusOut>',lambda event:cancel_phonetic())

            settings_dialog = [None]
            def open_ai_setup():
                from ai_voice_settings import load, save, remove, VOICES
                from tkinter import ttk, messagebox
                if settings_dialog[0] is not None:
                    settings_dialog[0].lift()
                    return
                dialog = tk.Toplevel(root)
                settings_dialog[0] = dialog
                dialog.title('AI voice setup - trainer')
                dialog.transient(root)
                dialog.grab_set()
                def close():
                    settings_dialog[0] = None
                    dialog.destroy()
                    typed.focus_set()
                dialog.protocol('WM_DELETE_WINDOW', close)
                dialog.bind('<Escape>', lambda event: close())
                tk.Label(dialog, text='AI speech requires separately billed OpenAI API access.\nNarration text, including email and documents, is sent to OpenAI.',
                         justify='left', wraplength=520).pack(padx=16, pady=12)
                tk.Label(dialog, text='API key (hidden):').pack(anchor='w', padx=16)
                key_entry = tk.Entry(dialog, show='*', width=55)
                key_entry.pack(padx=16, fill='x')
                tk.Label(dialog, text='AI voice:').pack(anchor='w', padx=16, pady=(10,0))
                choice = ttk.Combobox(dialog, values=VOICES, state='readonly')
                choice.set('coral')
                choice.pack(padx=16, fill='x')
                consent = tk.BooleanVar(value=False)
                tk.Checkbutton(dialog, text='I authorize sending narration text to OpenAI.',
                               variable=consent).pack(anchor='w', padx=16, pady=10)
                try:
                    current = load(self.data_folder)
                    key_entry.insert(0, current.get('key',''))
                    if current.get('voice') in VOICES: choice.set(current['voice'])
                except Exception:
                    self.key_feedback.put('Saved AI settings could not be opened. Enter a new key or remove the saved settings.')
                def apply():
                    try:
                        save(self.data_folder, key_entry.get(), choice.get(), consent.get())
                    except ValueError as exc:
                        self.key_feedback.put(str(exc))
                        messagebox.showerror('AI voice setup', str(exc), parent=dialog)
                        return
                    except Exception:
                        self.key_feedback.put('AI voice settings could not be saved.')
                        messagebox.showerror('AI voice setup', 'Settings could not be saved.', parent=dialog)
                        return
                    self.settings_results.put('saved')
                    close()
                def delete():
                    try: remove(self.data_folder)
                    except OSError:
                        self.key_feedback.put('AI voice settings could not be removed.')
                        return
                    self.settings_results.put('removed')
                    close()
                tk.Button(dialog, text='Save AI voice settings', command=apply).pack(anchor='w', padx=16)
                tk.Button(dialog, text='Remove saved AI settings', command=delete).pack(anchor='w', padx=16)
                tk.Button(dialog, text='Cancel', command=close).pack(anchor='w', padx=16, pady=12)
                key_entry.focus_set()

            def poll():
                while True:
                    if self.closed.is_set():return
                    try: action, field = self.ui_actions.get_nowait()
                    except queue.Empty: break
                    if action=='cancel_phonetic':cancel_phonetic();continue
                    if action=='ui_call':
                        field();continue
                    if action=='context':
                        if field.get('mode')=='update_download':update_toolbar.pack(before=history,fill='x',padx=18,pady=6)
                        else:update_toolbar.pack_forget()
                        old=self.context;self.context=field
                        mode=field['mode'];source=field.get('source')
                        transition=old.get('mode')!=mode or old.get('source')!=source or old.get('browser_name_input')!=field.get('browser_name_input') or field.get('focus',False)
                        if transition and mode=='web':self.command_entry_active=False
                        try:root.attributes('-disabled',mode=='settings')
                        except tk.TclError:pass
                        if mode=='settings':continue
                        if not field.get('web_field') or field.get('source')!=old.get('source'):local['web_escape_pending']=False
                        if mode in ('media','podcast') and field.get('media_revision')!=old.get('media_revision'):
                            form=field.get('media_form')
                            if form:
                                entry_label.configure(text=form['label']+':');typed.delete(0,'end');typed.insert(0,form['value'] if field.get('media_input') else '');typed.icursor('end');typed.focus_force()
                            else:typed.delete(0,'end')
                        typed.configure(show='*' if field.get('web_private') or field.get('account_private') else '')
                        if field.get('account_entry')!=old.get('account_entry'):
                            typed.delete(0,'end')
                            if field.get('account_entry'):
                                entry_label.configure(text='Yahoo app password (private); paste or type, then press Enter. Escape cancels:' if field.get('account_private') else 'Yahoo email address; type, then press Enter. Escape cancels:')
                                typed.focus_force()
                        if field.get('web_private') and field.get('web_field')!=old.get('web_field'):
                            typed.delete(0,'end');typed.insert(0,field['web_field'].get('value',''));entry_label.configure(text=field['web_field']['label']+' (private); Enter saves, Tab moves, Escape cancels:')
                        if mode!='files':file_list.pack_forget();file_toolbar.pack_forget()
                        if mode=='files':
                            history.pack_forget();editor.pack_forget();file_toolbar.pack(side='bottom',fill='x',padx=18,pady=(0,18));file_list.pack(fill='both',expand=True,padx=18)
                            values=field.get('file_items',[])
                            if tuple(file_list.get(0,'end'))!=tuple(values):
                                file_list.delete(0,'end')
                                for value in values:file_list.insert('end',value)
                            file_list.selection_clear(0,'end')
                            index=min(field.get('file_index',0),max(0,len(values)-1))
                            for selected_index in field.get('file_selected_indices',[index]):
                                if 0<=selected_index<len(values):file_list.selection_set(selected_index)
                            if values:file_list.activate(index);file_list.see(index)
                            if field.get('focus'):
                                if field.get('file_name_input'):typed.focus_force()
                                else:file_list.focus_force()
                        text_mode=source is not None and mode in ('document','email_draft','mailbox','help','web','file_reader')
                        if transition or (field.get('ack',0)>=local['seq'] and field.get('text','')!=editor.get('1.0','end-1c')):cancel_phonetic()
                        if text_mode:
                            editor.configure(state='normal')
                            history.pack_forget();editor.pack(fill='both',expand=True,padx=18,pady=(0,18))
                            web_cursor_changed=mode=='web' and (old.get('web_cursor_revision')!=field.get('web_cursor_revision') or old.get('web_revision')!=field.get('web_revision'))
                            if web_cursor_changed or transition:local['web_navigation_pending']=False
                            if web_cursor_changed and local['reading_after'] is not None:root.after_cancel(local['reading_after']);local['reading_after']=None
                            if show_context_text(old,field,local['seq'],web_cursor_changed):
                                local['applying']=True
                                if editor.get('1.0','end-1c')!=field.get('text',''):
                                    editor.delete('1.0','end');editor.insert('1.0',field.get('text',''));editor.edit_reset()
                                editor.edit_modified(False);local['text']=field.get('text','')
                                caret=field.get('caret',len(local['text']));editor.mark_set('insert','1.0 + '+str(caret)+' chars')
                                editor.tag_remove('sel','1.0','end')
                                if field.get('selection'):editor.tag_add('sel',*('1.0 + '+str(i)+' chars' for i in field['selection']))
                                editor.see('insert');local['applying']=False
                            if mode=='web' and field.get('readonly'):editor.configure(state='disabled')
                            for tag in editor.tag_names():
                                if tag in ('bold','italic','underline','heading','strike','superscript','subscript','highlight','code') or tag.startswith(('mail_link_','docfont:')):editor.tag_remove(tag,'1.0','end')
                            for i,(start,end,style,url) in enumerate(field.get('rich_spans',[])):
                                tag='mail_link_'+str(i) if style in ('link','button') else style
                                if style.startswith('docfont:'):
                                    import json
                                    family,size,weight,slant=json.loads(style[8:])
                                    editor.tag_configure(tag,font=(family,int(size),weight,slant))
                                if style in ('link','button'):
                                    editor.tag_configure(tag,foreground=readable_color(editor),underline=True)
                                    editor.tag_bind(tag,'<Button-1>',lambda event,u=url,a=start:reader_link(a) if self.context.get('mode')=='file_reader' else open_link(u))
                                editor.tag_add(tag,'1.0 + '+str(start)+' chars','1.0 + '+str(end)+' chars')
                            self.email_field=None
                            if transition:typed.delete(0,'end');self.last_email_request=None
                            if transition:
                                root.deiconify();root.lift()
                                if mode=='web':
                                    try:
                                        foreground_window(root,editor)
                                    except (AttributeError,OSError):pass
                                editor.focus_force()
                        else:
                            editor.pack_forget();history.pack(fill='both',expand=True,padx=18,pady=(0,18)) if mode!='files' else None
                            if mode!='email_draft' or not self.email_field:
                                self.email_field=None;self.last_email_request=None
                                if transition and not field.get('media_form') and not ((mode=='document_name' or field.get('file_name_input')) and old.get('mode')=='settings'):typed.delete(0,'end')
                            request=field.get('naming_request')
                            if mode=='document_name' and request and request!=self.last_name_request:
                                typed.delete(0,'end');typed.insert(0,request[1]);typed.icursor('end');self.last_name_request=request
                            if field.get('file_name_input') and old.get('mode')!='settings' and field.get('file_rename_value')!=old.get('file_rename_value'):
                                typed.delete(0,'end');typed.insert(0,field['file_rename_value']);typed.selection_range(0,'end')
                            if mode!='email_draft' and not field.get('web_private') and not field.get('account_entry') and not field.get('media_form'):entry_label.configure(text='Web address or search; type, then press Enter. Escape cancels:' if mode=='search' else 'Document name; press Enter to save:' if mode=='document_name' else 'Favorite name; press Enter. Escape cancels:' if field.get('browser_name_input') else field.get('file_input_label','New file name, including extension')+'; press Enter. Escape cancels:' if field.get('file_name_input') else 'Type a command, then press Enter:')
                            if transition:
                                if mode in ('media','podcast'):
                                    root.deiconify();root.lift()
                                    try:
                                        import sys,ctypes
                                        if sys.platform=='win32':foreground_window(root)
                                    except (AttributeError,OSError):pass
                                if mode=='files' and not field.get('file_name_input'):file_list.focus_force()
                                else:typed.focus_force()
                        continue
                    if action == 'email_select' and not field['canceled'].is_set():
                        try:
                            if not self.email_field:
                                field['result'] = 'No email field is active.'
                            else:
                                start = typed.index('sel.first') if typed.selection_present() else typed.index('insert')
                                end = typed.index('sel.last') if typed.selection_present() else start
                                result, span = field_selection.select(typed.get(), field['command'], start, end)
                                if field_selection.edited_text is not None:
                                    field['edited_text'] = field_selection.edited_text
                                    field['field_name'] = self.email_field
                                    typed.delete(0,'end')
                                    typed.insert(0,field_selection.edited_text)
                                typed.selection_clear()
                                if span:
                                    typed.selection_range(*span)
                                    typed.icursor(span[0])
                                typed.focus_set()
                                field['result'] = result
                        except Exception:
                            field['result'] = 'I could not select text in this field. Try again.'
                        finally: field['done'].set()
                    if action == 'email_focus':
                        root.deiconify()
                        root.lift()
                        typed.focus_force()
                    if action == 'clear_email':
                        self.email_field = None
                        typed.delete(0, 'end')
                        entry_label.configure(text='Type a command, then press Enter:')
                    if action == 'ai_voice':
                        open_ai_setup()
                    if action == 'email_field':
                        field, value = field
                        self.email_field = field
                        names = {'recipient':'To address', 'cc':'CC address', 'bcc':'BCC address',
                                 'subject':'Subject', 'body':'Message body'}
                        entry_label.configure(text='Type '+names.get(field,'email text')+' here, then press Enter:')
                        typed.delete(0, 'end')
                        if value: typed.insert(0, value)
                        typed.icursor('end')
                        root.deiconify()
                        root.lift()
                        typed.focus_force()
                while True:
                    if self.closed.is_set():return
                    try: message = self.messages.get_nowait()
                    except queue.Empty: break
                    self.current.set(message)
                    history.configure(state='normal')
                    history.insert('end', message + '\n\n')
                    if int(history.index('end-1c').split('.')[0]) > 90:
                        history.delete('1.0', '30.0')
                    history.see('end')
                    history.configure(state='disabled')
                if not self.closed.is_set(): root.after(15, poll)

            self.ready.set()
            root.after(15, poll)
            root.mainloop()
        except Exception as exc:
            self.problem = exc.with_traceback(None)
            self.ready.set()
            self.closed.set()



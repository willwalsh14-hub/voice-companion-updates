"""Small status window for the Windows voice application.

The window lives on its own UI thread. Other threads only post text to a queue.
Closing the window requests a clean shutdown of the microphone loop.
"""
import user_stop
import queue
import threading
import time
from field_selection import FieldSelection
from key_state import alt_held  # Tk state bit 0x8 is Num Lock on Windows, not Alt.
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


def use_display_scaling():
    """Draw this thread's windows at the Windows display scale, so text is sharp, not stretched.

    Only the window thread changes: Options and other windows keep their own setting.
    Font sizes are in points, so they follow the Windows scale either way."""
    import sys
    if sys.platform!='win32':return
    try:
        import ctypes
        set_context=ctypes.windll.user32.SetThreadDpiAwarenessContext
        set_context.restype=ctypes.c_void_p;set_context.argtypes=[ctypes.c_void_p]
        set_context(ctypes.c_void_p(-2))  # DPI_AWARENESS_CONTEXT_SYSTEM_AWARE
    except (AttributeError,OSError):pass  # Older Windows: Windows stretches the window instead.


def saved_display(folder):
    """The saved display options, read before the app sends them, so the window opens large at once."""
    import json
    from pathlib import Path
    import display_options
    try:preferences=json.loads((Path(folder)/'preferences.json').read_text(encoding='utf-8')) if folder else {}
    except (OSError,ValueError,TypeError):preferences={}
    return display_options.settings(preferences)


def window_geometry(screen_width,screen_height):
    """A large window, centered, for when full screen is off: most of the screen."""
    width=max(470,int(screen_width*.8));height=max(300,int(screen_height*.8))
    return f'{width}x{height}+{max(0,(screen_width-width)//2)}+{max(0,(screen_height-height)//3)}'


def list_lines(items,index):
    """The display list's text: one line per item; the selected line starts with an arrow."""
    return [('\u25ba\t' if i==index else '\t')+str(item) for i,item in enumerate(items)]


def flow_positions(widths,height,width,gap=6):
    """Where each button goes so a row of buttons wraps onto more lines instead of being cut off.

    Returns ([(x, y)], total height)."""
    places=[];x=y=0
    for need in widths:
        if x and x+need>width:x=0;y+=height+gap
        places.append((x,y));x+=need+gap
    return places,(y+height if widths else 0)


def position_text(field):
    """'3 of 15' for the selected item, or ''."""
    index=field.get('display_index');count=field.get('display_count')
    if index is None or not count:return ''
    return str(index+field.get('display_offset',0)+1)+' of '+str(count)


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
        self.ui_errors = []
        # The last key events, as plain text, so a failed window test says
        # which key the window saw and why it ignored it.
        self.key_trace = __import__('collections').deque(maxlen=16)
        self.last_email_request = None
        self.email_selection_edit = None
        self.problem = None
        self.context = {'mode':'awake','source':None,'text':'','echo':'characters','phonetic':True,'delay':0.5,'ack':0}
        self.keyboard_output = None
        self.last_name_request = None
        self.command_entry_active=False
        self.last_navigation=0.0
        self.media_shortcut=None
        # Font names installed on this computer, found by the window thread (plain text, safe to read anywhere).
        self.font_families=None
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

    def debug_state(self):
        """Why the window is not answering, for test failure messages. Reads no Tk state."""
        # Call only when a test has already failed. Holding another thread's frame
        # can make Tk objects be freed on this thread ("Tcl_AsyncDelete"), so the
        # stack is turned into text at once and the frame is released here.
        import sys,traceback
        stack=None
        if self.thread.is_alive():
            frames=sys._current_frames();frame=frames.get(self.thread.ident)
            if frame is not None:stack=''.join(traceback.format_stack(frame)[-12:])
            del frames,frame
        return {'thread alive':self.thread.is_alive(),'ready':self.ready.is_set(),'problem':repr(self.problem),
                'waiting updates':self.ui_actions.qsize(),'window errors':list(self.ui_errors),
                'window thread is at':stack,'other threads':[t.name for t in __import__('threading').enumerate()],
                'recent keys':list(self.key_trace)}

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
            if getattr(self,'keyboard_output',None) and not self.keyboard_output.problem:
                # The opt-in speech transcript (speech_transcript.py); the queue below is written by the main loop.
                __import__('speech_transcript').TRANSCRIPT.said(text)
                self.keyboard_output.speak(text)
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

    def reclaim_focus(self):
        """Take the keyboard back after another program (Thunderbird started for mail) took it."""
        if not self.closed.is_set(): self.ui_actions.put(('reclaim_focus', None))

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
            import tkinter.font as tkfont
            from tkinter import scrolledtext
            import display_options
            use_display_scaling()
            root = tk.Tk()
            self.root=root
            def callback_error(kind,error,trace):
                # Tk's own handler keeps the error in sys.last_traceback. Its
                # frames hold this window's widgets, so the Tk interpreter
                # would outlive the window thread and be freed on another
                # thread ("Tcl_AsyncDelete"). Keep the text only.
                import sys,traceback
                text=''.join(traceback.format_exception(kind,error,trace))[-3000:]
                self.ui_errors.append(text)
                print('Voice Companion window error:\n'+text,file=sys.stderr,flush=True)
            root.report_callback_exception=callback_error
            from media_shortcut import MediaShortcut
            self.media_shortcut=MediaShortcut(lambda:self.commands.put(('keyboard','Ctrl+P')))
            self.media_shortcut.register()
            root.bind('<Destroy>',lambda event:self.closed.set() if event.widget==root else None)
            root.title('Voice Companion ' + self.version)
            try:self.font_families=sorted(set(str(name) for name in tkfont.families(root)))
            except tk.TclError:self.font_families=None
            # Large, high-contrast display for low vision (display_options.py). Every font is
            # a named font, so a new text size or font changes the whole window at once.
            display_values=saved_display(self.data_folder)
            fonts={role:tkfont.Font(root,font=display_options.font(display_values,role,self.font_families)) for role in ('title','message','body','hint','heading')}
            fonts['selected']=tkfont.Font(root,font=display_options.font(display_values,'body',self.font_families,weight='bold'))
            for style,weight,slant in (('bold','bold','roman'),('italic','normal','italic')):
                fonts[style]=tkfont.Font(root,font=display_options.font(display_values,'body',self.font_families,weight=weight,slant=slant))
            fonts['code']=tkfont.Font(root,family='Consolas',size=display_options.scaled(12,display_values))
            root.geometry(window_geometry(root.winfo_screenwidth(),root.winfo_screenheight()))
            root.option_add('*Font',fonts['body'])
            root.minsize(470, 300)
            root.protocol('WM_DELETE_WINDOW', lambda: (user_stop.request(), flush_pending_edit(), self.commands.put(('keyboard','Alt+F4'))))
            # The screen name, large, at the top: "Main menu", "Email: Inbox".
            heading = tk.Label(root, text='Voice Companion', font=fonts['title'], wraplength=600, justify='left', anchor='w')
            startup_label=tk.Label(root, text='Say “Wake up” to begin. Say “What version?” to check this build.',
                     font=fonts['hint'], wraplength=600, justify='left')
            entry_label = tk.Label(root, text='Type a command or email address, then press Enter:',
                                   font=fonts['hint'],wraplength=600,justify='left')
            typed = tk.Entry(root, font=fonts['body'])
            self.typed=typed
            self.email_field = None
            # The screen's list, menu or text is shown in this area; it fills the space left.
            content=tk.Frame(root)
            content.pack_propagate(False)
            file_list=tk.Listbox(root,font=fonts['body'],exportselection=False,selectmode='multiple')
            self.file_list=file_list
            file_toolbar=tk.Frame(root)
            self.file_toolbar=file_toolbar;self.file_buttons=[]
            for label,request in (('Search','search files'),('Search subfolders','search subfolders'),('Sort','sort files'),('Clear search','clear search'),('Drives','drive list'),('Main menu','main menu')):
                button=tk.Button(file_toolbar,text=label,command=lambda command=request:self.commands.put(command),takefocus=True)
                self.file_buttons.append(button)
                button.bind('<FocusIn>',lambda event,name=label:self.feedback(name+' button. Enter activates. Tab moves to the next control.'))
                button.bind('<Return>',lambda event,command=request:(self.commands.put(command),'break')[1])
            def flow(frame,buttons):
                # Large text makes a row of buttons wider than the window: wrap them onto more lines.
                def arrange(event=None):
                    width=frame.winfo_width()
                    if width<=1:width=max(1,root.winfo_width()-36)
                    height=max([button.winfo_reqheight() for button in buttons] or [0])
                    places,total=flow_positions([button.winfo_reqwidth() for button in buttons],height,width)
                    for button,(x,y) in zip(buttons,places):button.place(x=x,y=y)
                    if int(frame.cget('height'))!=total:frame.configure(height=total)
                frame.bind('<Configure>',arrange,add='+')
                arrangers.append(arrange)
            arrangers=[]
            flow(file_toolbar,self.file_buttons)
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
                self.key_trace.append(f'release reached window: {event.keysym} state={event.state:#x}')
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
            # A key let go in another window never reaches this one. A held
            # Insert must not stay remembered and turn the next Space into a
            # mode switch.
            root.bind('<FocusOut>',lambda event:document_modifiers.discard('Insert') if event.widget==root else None,add='+')
            previous = ['', 0]
            field_selection = FieldSelection()
            # Alt pressed and let go with no other key opens the screen's Alt menu
            # (screen_menus.py). Alt+O, Alt+T, Alt+F4 and other Alt keys never do.
            alt_tap={'alone':False,'when':0.0}
            def watch_alt(event):
                # Escape stops a wait on another program at once (user_stop); the key is
                # then handled as usual.
                if event.keysym=='Escape':user_stop.request()
                if event.keysym in ('Alt_L','Alt_R'):
                    if not alt_tap['alone']:alt_tap.update(alone=True,when=time.monotonic());alt_tap.pop('sent',None)
                else:alt_tap['alone']=False
            root.bind('<KeyPress>',watch_alt,add='+')
            root.bind('<FocusOut>',lambda event:alt_tap.update(alone=False),add='+')
            def alt_release(event):
                if event.keysym not in ('Alt_L','Alt_R'):return
                if alt_tap.pop('sent',False):alt_tap['alone']=False;return 'break'
                alone=alt_tap['alone'] and time.monotonic()-alt_tap['when']<2;alt_tap['alone']=False
                if not alone or event.widget.winfo_toplevel()!=root:return
                from screen_menus import alt_menu_key,alt_unavailable
                if not alt_menu_key(self.context,self.command_entry_active,typed.get()):
                    # Never hand a lone Alt to Windows: its system menu is not Companion's.
                    self.feedback(alt_unavailable(self.context,self.command_entry_active,typed.get()));return 'break'
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
            from time_date import PressCounter,clock_key,key_request
            clock_presses=PressCounter()
            def shortcut(event):
                if event.keysym.lower()=='p' and event.state&4 and not event.state&1 and not alt_held(event.state):
                    if not self.media_shortcut.registered:self.commands.put(('keyboard','Ctrl+P'))
                    return 'break'
                if event.widget.winfo_toplevel()!=root:return
                mode=self.context.get('mode');key=event.keysym
                self.key_trace.append(f'press {key} state={event.state:#x}')
                if clock_key(key,event.state):
                    # F12 or Insert+F12 says the time; twice quickly, the date. It works on every
                    # screen, in menus, lists and questions, and never reaches a web page.
                    if self.keyboard_output:self.keyboard_output.interrupt()
                    cancel_phonetic()
                    self.commands.put(('keyboard',key_request(clock_presses.press())));return 'break'
                if key=='F9' and not event.state&(1|4) and not alt_held(event.state):
                    # F9 turns the microphone fully off or on, on every screen.
                    if self.keyboard_output:self.keyboard_output.interrupt()
                    cancel_phonetic()
                    self.commands.put(('keyboard','F9'));return 'break'
                if key=='F1' and event.state&4 and not event.state&1 and not alt_held(event.state):
                    # Control+F1 goes to the main menu from every screen. (Print Screen stays
                    # with Windows, for screenshots.)
                    if self.keyboard_output:self.keyboard_output.interrupt()
                    cancel_phonetic()
                    self.commands.put(('keyboard','Ctrl+F1'));return 'break'
                display_request=display_options.display_key(key,event.state)
                if display_request:
                    # Control+Plus, Control+Minus, Control+0: text size. Control+9: colors. F11: full
                    # screen. On every screen, as in a web browser; never typed into a page or document.
                    if self.keyboard_output:self.keyboard_output.interrupt()
                    flush_pending_edit();cancel_phonetic()
                    self.commands.put(('keyboard',display_request));return 'break'
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
                    update_key='Alt+'+key.upper() if alt_held(event.state) else key
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
                if mode=='file_reader' and not self.command_entry_active and not control and not alt_held(event.state):
                    if key.lower() in ('h','t','k','l','b','n') or key=='Return':
                        send_text_position();self.commands.put(('keyboard',('Shift+' if shift else '')+key.upper() if key!='Return' else 'Enter'));return 'break'
                if mode=='document_menu' and not control and not alt_held(event.state) and event.char and event.char.isalnum():
                    self.commands.put(('keyboard','MenuPick:'+event.char));return 'break'
                if mode=='files' and not self.command_entry_active and not self.context.get('file_name_input'):
                    file_key=('Ctrl+Shift+' if shift else 'Ctrl+')+key.upper() if control else 'Alt' if key in ('Alt_L','Alt_R') else 'Enter' if key=='Return' else 'Space' if key=='space' else key
                    if control and shift:
                        file_key='Ctrl+Shift+'+(chr(event.keycode) if 48<=event.keycode<=57 else key if key in ('Up','Down') else key.upper())
                    if alt_held(event.state) and key.lower() in ('s','n','c','m','d','a','z'):file_key='Alt+'+key.upper()
                    if alt_held(event.state) and key=='Home':file_key='Alt+Home'
                    if control and key=='Home':file_key='Ctrl+Home'
                    if event.widget in self.file_buttons and not control and file_key not in ('Alt+S','Alt+N','Alt+C','Alt+M','Alt+D','Alt+A','Alt+Z','Alt+Home','Escape'):return
                    if event.widget==file_list and not control and not alt_held(event.state) and event.char and event.char.isprintable() and key!='space':
                        self.commands.put(('keyboard','Letter:'+event.char));return 'break'
                    if file_key in ('Up','Down','Left','Right','Home','End','Enter','Space','Escape','Alt','F1','F2','Delete','Ctrl+C','Ctrl+X','Ctrl+V','Ctrl+A','Ctrl+Shift+A','F5','Ctrl+D','Ctrl+F','Ctrl+Shift+F','Ctrl+Shift+X','Alt+S','Alt+N','Alt+C','Alt+M','Alt+D','Alt+A','Alt+Z','Alt+Home','Ctrl+Shift+1','Ctrl+Shift+2','Ctrl+Shift+3','Ctrl+Shift+4','Ctrl+Shift+Up','Ctrl+Shift+Down','BackSpace'):
                        self.commands.put(('keyboard',file_key));return 'break'
                if mode=='web' and not self.command_entry_active and not self.context.get('web_private'):
                    from web_modes import toggle_chord
                    # Insert+Space; Control+Shift+Space or Control+Alt+Space when a screen reader owns Insert.
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
                if mode in ('document','word_processor','document_menu','recent_documents','media_menu','local_media','notepad_menu','reader_menu','reader_recent','task_input'):
                    from task_navigation import SHORTCUTS
                    if mode=='document' and self.context.get('document_table') and key in ('Tab','ISO_Left_Tab'):
                        flush_pending_edit();cancel_phonetic()
                        self.commands.put(('keyboard','Doc:PreviousCell' if shift or key=='ISO_Left_Tab' else 'Doc:NextCell'));return 'break'
                    chord=('Ctrl+Shift+' if shift else 'Ctrl+')+(key if key in ('Home','End','Left','Right') else 'Enter' if key=='Return' else key.upper()) if control else 'Alt' if key in ('Alt_L','Alt_R') else 'Shift+F4' if shift and key=='F4' else key
                    if control and shift and 48<=event.keycode<=57:chord='Ctrl+Shift+'+chr(event.keycode)
                    if alt_held(event.state) and key.lower()=='o':chord='Alt+O'
                    elif control and alt_held(event.state):chord='Ctrl+Alt+'+(key if key in ('Up','Down','Left','Right') else key.upper())
                    elif alt_held(event.state) and not control and key in ('Up','Down','Left','Right'):chord='Alt+'+key
                    requests=set(SHORTCUTS)|{'Alt','Ctrl+O','Ctrl+N','Ctrl+R','Alt+O'}
                    if mode=='document':requests-= {'Ctrl+Home','Ctrl+End'}
                    if mode=='local_media':requests|={'space','Left','Right','Up','Down','Escape','Tab','ISO_Left_Tab','Return','Ctrl+I'}
                    if mode=='document_menu' and key in ('Alt_L','Alt_R'):chord='Escape'
                    if chord in requests and not (mode=='task_input' or self.command_entry_active):
                        flush_pending_edit();cancel_phonetic()
                        # Alt already did its job here (the player or document menu), so letting
                        # it go must not also say "This screen has no Alt menu".
                        if chord=='Alt' and mode in ('local_media','document','document_menu'):alt_tap['sent']=True
                        self.commands.put(('keyboard','Space' if chord=='space' else 'Enter' if chord=='Return' else 'ShiftTab' if chord=='ISO_Left_Tab' else chord));return 'break'
                if mode=='files' and self.context.get('file_name_input') and event.widget==typed and key!='Escape':return
                if key.lower()=='t' and not control and alt_held(event.state):
                    flush_pending_edit();self.command_entry_active=True;typed.focus_set();return 'break'
                if mode in ('web','files') and self.command_entry_active and event.widget==typed and key=='Escape':
                    self.command_entry_active=False;typed.delete(0,'end');(file_list if mode=='files' else editor).focus_set();return 'break'
                request=None
                if self.context.get('confirmation') is not None and key.lower() in ('y','n') and alt_held(event.state):
                    self.commands.put(('keyboard','Alt+'+key.upper()));return 'break'
                if key=='F4' and alt_held(event.state):
                    user_stop.request();flush_pending_edit();self.commands.put(('keyboard','Alt+F4'));return 'break'
                if key in ('Control_L','Control_R'):
                    cancel_phonetic()
                    if local['reading_after'] is not None:root.after_cancel(local['reading_after']);local['reading_after']=None
                    if self.keyboard_output:self.keyboard_output.interrupt()
                    self.commands.put(('keyboard','Control'));return 'break'
                if key in ('Up','Down','Left','Right','Tab','ISO_Left_Tab','Home','End','Escape') or self.context.get('menu_letters') and event.char.isalpha():
                    if self.keyboard_output:self.keyboard_output.interrupt()
                if key in ('Home','End') and not control and not self.context.get('source') and not self.email_field and not typed.get():
                    self.commands.put(('keyboard',key));return 'break'
                if key.lower()=='o' and alt_held(event.state):
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
                elif mode=='browser_menu' and not self.command_entry_active and not self.context.get('browser_name_input') and not control and not alt_held(event.state) and event.char and event.char.isalnum():request='Letter:'+event.char
                elif mode=='note' and control and not shift and not alt_held(event.state) and key.lower()=='o':request='Ctrl+O'
                elif mode=='web' and not control and alt_held(event.state) and key in ('Left','Right'):request='Web:Back' if key=='Left' else 'Web:Forward'
                elif mode=='web' and key=='F5':request='Web:Refresh'
                elif mode=='web' and key in ('F6','F7'):request=key
                elif mode=='web' and control and key.lower()=='l':request='Web:Search'
                elif mode=='web' and control and not shift and not alt_held(event.state) and key in ('Home','End') and not self.context.get('web_private'):request='Web:PageTop' if key=='Home' else 'Web:PageBottom'
                if self.context.get('confirmation') is not None and key=='Return' and event.widget==typed and typed.get().strip().lower() in ('yes','no'):
                    self.commands.put(typed.get().strip().lower());typed.delete(0,'end');return 'break'
                if request:pass
                elif self.context.get('confirmation') is not None and key in ('Up','Down','Left','Right','Home','End','Tab','Return','Escape','space','y','Y','n','N'):
                    request='Enter' if key in ('Return','space') else 'ShiftTab' if key=='Tab' and shift else key
                elif key=='F4' and alt_held(event.state):request='Alt+F4'
                elif mode=='web' and self.context.get('web_name_list') and not self.command_entry_active and not control and not alt_held(event.state) and (event.char and event.char.isprintable() or key=='BackSpace'):
                    request='Web:ListBackspace' if key=='BackSpace' else 'Web:ListText:'+event.char
                elif mode=='web' and (self.context.get('web_browse') or self.context.get('web_field')) and not (event.widget==typed and self.command_entry_active):
                    field=None if local.get('web_escape_pending') else self.context.get('web_field')
                    if field and self.context.get('web_private') and (key in ('Return','Tab','ISO_Left_Tab','Up','Down') or control and key in ('Home','End')):
                        if key=='Return':
                            if key in activation_keys:return 'break'
                            activation_keys.add(key)
                        self.commands.put(('web_private_value',dict(value=typed.get(),next=('PageTop' if key=='Home' else 'PageBottom') if control and key in ('Home','End') else 'Leave:'+key if key in ('Up','Down') else 'ShiftTab' if shift or key=='ISO_Left_Tab' else 'Tab' if key=='Tab' else 'Done',**({'field_source':self.context['web_field_source']} if self.context.get('web_field_source') else {}))));typed.delete(0,'end');return 'break'
                    elif key=='Escape':request='Web:Escape'
                    elif control and alt_held(event.state) and key in ('Up','Down','Left','Right','Home','End','Prior','Next'):request='Web:Table:'+key
                    elif field and self.context.get('web_choice') and key in ('Up','Down','Left','Right','Home','End'):request='Web:'+key
                    elif field and self.context.get('web_choice') and not control and not alt_held(event.state) and len(event.char)==1 and event.char.isalnum():request='Web:ChoiceLetter:'+event.char
                    elif field and self.context.get('web_combo_edit') and not control and not shift and not alt_held(event.state) and key in ('Up','Down'):request='Web:'+key
                    elif field and not self.context.get('web_private') and not control and not shift and key in ('Up','Down','Left','Right'):
                        value=editor.get('1.0','end-1c');caret=len(editor.get('1.0','insert'))
                        multiline=field.get('tag')=='textarea' or field.get('editable')
                        if key=='Up' and (not multiline or '\n' not in value[:caret]) or key=='Down' and (not multiline or '\n' not in value[caret:]) or key=='Left' and caret==0 or key=='Right' and caret==len(value):request='Web:Leave:'+key
                    elif key=='F5':request='Web:Refresh'
                    elif key in ('Tab','ISO_Left_Tab'):request='Web:ShiftTab' if shift or key=='ISO_Left_Tab' else 'Web:Tab'
                    elif not field and not control and not alt_held(event.state) and event.char.lower() in 'khfbxcetr alidgmps123456'.replace(' ','') and len(event.char)==1:request='Web:Quick:'+('Previous:' if shift else '')+event.char.lower()
                    elif key=='Return' and (not field or field.get('tag')!='textarea' and not field.get('editable') or control or self.context.get('web_combo_edit')):request='Web:Done' if field and not self.context.get('web_choice') else 'Web:Activate'
                    elif not field and not shift and key in ('Up','Down','Left','Right','Home','End','Prior','Next'):request='Web:Read:'+('Control:' if control else '')+key
                    elif not field and key=='space':request='Web:Space'
                    elif key=='Left' and alt_held(event.state):request='Web:Back'

                elif self.context.get('help_reader') and not shift and not alt_held(event.state) and key in ('Up','Down','Left','Right','Home','End') and not (event.widget==typed and self.command_entry_active):
                    request='Help:'+('Ctrl+' if control and key in ('Left','Right') else '')+key
                elif mode=='document' and key=='F2':request='F2'
                elif mode=='mailbox':
                    if self.context.get('source') and self.context.get('email_html') and event.widget==self.editor and not control and not alt_held(event.state) and key.lower() in ('k','b'):
                        request='Email:'+('previous ' if shift else 'next ')+('link' if key.lower()=='k' else 'button')
                    elif shift and key in ('Up','Down') and not self.context.get('source') and not self.typed.get():request='Shift'+key
                    elif key=='F2':request='F2'
                    elif key=='F5':request='F5'
                    # Page Down and Page Up: the next and previous page of messages. Control+Home
                    # and Control+End: the newest and oldest message in the folder.
                    elif key in ('Next','Prior') and not control and not shift and not self.context.get('source') and not self.typed.get():request=key
                    elif control and not shift and key in ('Home','End') and not self.context.get('source') and not self.typed.get():request='Ctrl+'+key
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
                elif mode=='email_draft' and ((control and key=='Return') or (key.lower()=='s' and alt_held(event.state))):
                    if self.email_field:
                        self.commands.put(('email_field_tab',{'field':self.email_field,'value':typed.get(),'backward':False,'send':True}));return 'break'
                    request='Ctrl+Enter' if control else 'Alt+S'
                elif self.context.get('menu_letters') and not (mode in ('browser_menu','files') and self.command_entry_active) and not self.context.get('file_name_input') and not self.context.get('media_input') and not control and not alt_held(event.state) and len(event.char)==1 and event.char.isalpha() and not typed.get():
                    request='Letter:'+event.char.lower()
                if mode=='web' and not request and (self.context.get('web_browse') or local.get('web_escape_pending')) and not self.command_entry_active and event.char and event.char.isprintable() and not control and not alt_held(event.state):return 'break'
                if request:
                    # A held key repeats. Activating, or going back or forward,
                    # happens once per press.
                    if request in ('Web:Activate','Web:Space','Web:Done','Web:Back','Web:Forward'):
                        if key in activation_keys:
                            self.key_trace.append(f'held, ignored: {request} {key} state={event.state:#x}');return 'break'
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
            def release_activation_key(event):
                self.key_trace.append(f'release {event.keysym} state={event.state:#x} held={sorted(activation_keys)}')
                activation_keys.discard(event.keysym)
            root.bind_all('<KeyRelease>',release_activation_key,add='+')
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
            route_alt_keys(root)
            button_row=tk.Frame(root)
            flow(button_row,[tk.Button(button_row,text='Options (Alt+O)',command=lambda:self.commands.put(('keyboard','settings'))),
                             tk.Button(button_row, text='Enter typed command', command=submit_typed),
                             tk.Button(button_row, text='AI voice setup (trainer)', command=self.setup_ai_voice)])
            self.current = tk.StringVar(root, value='Starting speech and microphone...')
            # The last thing spoken, in large text under the screen name. A few lines at most,
            # so a long message never pushes the list or text off the screen.
            message_view=tk.Text(root,wrap='word',font=fonts['message'],height=2,takefocus=0,borderwidth=0,cursor='arrow')
            message_view.insert('1.0',self.current.get());message_view.configure(state='disabled')
            history = scrolledtext.ScrolledText(root, wrap='word', font=fonts['body'], state='disabled', height=4)
            editor=tk.Text(root,wrap='word',font=fonts['body'],undo=True,exportselection=False,height=4)
            self.editor=editor
            # Lists and menus as lines, the selected one marked with an arrow, bold and highlighted;
            # never by color alone. It never takes the keyboard: keys stay where they are.
            display_view=scrolledtext.ScrolledText(root,wrap='word',font=fonts['body'],takefocus=0,height=4,cursor='arrow')
            display_view.vbar.configure(takefocus=0)
            display_view.configure(state='disabled')
            heading.pack(fill='x', padx=18, pady=(12, 2))
            message_view.pack(fill='x', padx=18, pady=(0, 8))
            content.pack(fill='both', expand=True)
            entry_label.pack(anchor='w', padx=18, pady=(6, 0))
            typed.pack(fill='x', padx=18)
            button_row.pack(fill='x', padx=18, pady=(6, 0))
            startup_label.pack(anchor='w', padx=18, pady=(2, 10))
            history.pack(in_=content, fill='both', expand=True, padx=18, pady=(0, 8))
            def resize_labels(event):
                if event.widget==root:
                    for label in (heading,startup_label,entry_label):label.configure(wraplength=max(1,event.width-36))
            root.bind('<Configure>',resize_labels,add='+')
            from visual_accessibility import readable_color,focus_style
            for widget in (typed,editor):focus_style(widget)
            editor.tag_configure('bold',font=fonts['bold'])
            editor.tag_configure('italic',font=fonts['italic'])
            editor.tag_configure('underline',underline=True)
            editor.tag_configure('heading',font=fonts['heading'])
            editor.tag_configure('strike',overstrike=True)
            editor.tag_configure('superscript',offset=4)
            editor.tag_configure('subscript',offset=-4)
            editor.tag_configure('highlight',background='#ffff99',foreground='#000000')
            editor.tag_configure('code',font=fonts['code'])
            # The line with the caret or reading place; under every other mark, so selected text shows.
            editor.tag_configure('reading_line');editor.tag_lower('reading_line')
            display_view.tag_configure('selected',font=fonts['selected'])
            display_view.tag_configure('reading_line')
            def reader_link(position):
                editor.mark_set('insert','1.0 + '+str(position)+' chars');send_text_position()
                self.commands.put(('keyboard','Enter'));return 'break'
            def open_link(url):
                # Companion opens it in the guided browser from Web browser options.
                from email_reader import safe_link
                if safe_link(url):self.commands.put(('open_link',url))
            def link_at_caret():
                caret=offset()
                return next((span for span in self.context.get('rich_spans',[]) if span[2] in ('link','button') and span[0]<=caret<span[1]),None)
            local={'text':'','seq':0,'applying':False,'phonetic':None,'reading_after':None,'link':None,'web_navigation_pending':False,'web_escape_pending':False,
                   'display_values':None,'display_shown':None,'palette':None,'caret_target':None,'caret_after':None,'editor_place':None,'file_current':None}
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
                    if span[3]:open_link(span[3]);self.feedback('Opening '+' '.join(editor.get('1.0 + '+str(span[0])+' chars','1.0 + '+str(span[1])+' chars').split())+'.')
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
                        # Said as the web browser says it (Will, October 2026).
                        name=' '.join(editor.get('sel.first','sel.last').split()) or 'Unlabeled'
                        self.feedback(('Link '+name+'.') if span[2]=='link' else name+', button.');send_text_position();return 'break'
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
                consent = tk.BooleanVar(dialog, value=False)
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

            def tk_color(name):
                # Windows color names (the Windows colors scheme) exist only on Windows.
                try:root.winfo_rgb(name);return name
                except tk.TclError:
                    return {'SystemWindowText':'#000000','SystemWindow':'#FFFFFF','SystemHighlight':'#0078D7','SystemHighlightText':'#FFFFFF'}.get(name,'#000000')
            def paint(widget,palette,ring):
                if isinstance(widget,tk.Toplevel):return
                try:names=set(widget.keys())
                except tk.TclError:names=set()
                values={'background':palette['bg'],'foreground':palette['fg'],'activebackground':palette['select_bg'],'activeforeground':palette['select_fg'],
                        'selectbackground':palette['select_bg'],'selectforeground':palette['select_fg'],'inactiveselectbackground':palette['select_bg'],
                        'insertbackground':palette['caret'],'highlightbackground':palette['bg'],'highlightcolor':palette['focus'],
                        'disabledforeground':palette['fg'],'disabledbackground':palette['bg'],'readonlybackground':palette['bg'],'troughcolor':palette['bg']}
                change={name:value for name,value in values.items() if name in names}
                if widget.winfo_class() in ('Entry','Text','Listbox','Button') and str(widget.cget('takefocus'))!='0':change['highlightthickness']=ring
                # A wide, steady caret: nothing on the screen flashes.
                if 'insertwidth' in names:change.update(insertwidth=max(2,ring),insertofftime=0)
                try:widget.configure(**change)
                except tk.TclError:pass
                for child in widget.winfo_children():paint(child,palette,ring)
            def set_full_screen(on):
                if not on:
                    try:root.state('normal')
                    except tk.TclError:pass
                    root.geometry(window_geometry(root.winfo_screenwidth(),root.winfo_screenheight()))
                try:root.state('zoomed' if on else 'normal')
                except tk.TclError:
                    try:root.attributes('-zoomed',bool(on))  # Linux test computers
                    except tk.TclError:pass
            def apply_display(values,force=False):
                """Colors, fonts, text size, spacing and full screen from the display options."""
                old=local['display_values']
                if not values or values==old and not force:return
                values=display_options.settings(values);local['display_values']=dict(values)
                family=display_options.font_family(values,self.font_families)
                for role in ('title','message','body','hint','heading'):
                    name,size,weight,slant=display_options.font(values,role,self.font_families)
                    fonts[role].configure(family=name,size=size,weight=weight,slant=slant)
                body_size=display_options.scaled(display_options.BASE_SIZES['body'],values)
                for role,weight,slant in (('selected','bold','roman'),('bold','bold','roman'),('italic','bold' if values['display_bold'] else 'normal','italic')):
                    fonts[role].configure(family=family,size=body_size,weight=weight,slant=slant)
                fonts['code'].configure(size=body_size)
                palette={name:(tk_color(color) if color else None) for name,color in display_options.colors(values).items()}
                local['palette']=palette
                ring=max(2,round(3*display_options.percent(values)/100))
                root.configure(background=palette['bg'])
                paint(root,palette,ring)
                above,below=display_options.line_gap(values,fonts['body'].metrics('linespace'))
                for view in (editor,history,display_view,message_view):view.configure(spacing1=above,spacing3=below,spacing2=above+below)
                tab=max(fonts['selected'].measure('\u25ba '),fonts['selected'].measure('WW'))+max(4,ring)
                display_view.configure(tabs=(tab,))
                display_view.tag_configure('item',lmargin2=tab)
                display_view.tag_configure('selected',background=palette['select_bg'],foreground=palette['select_fg'])
                for view in (editor,display_view):view.tag_configure('reading_line',background=palette['line'] or '')
                # Marked files use the line color; the current file uses the highlight (mark_current_file).
                file_list.configure(activestyle='underline',selectbackground=palette['line'] or palette['select_bg'],selectforeground=palette['fg'] if palette['line'] else palette['select_fg'])
                local['file_current']=None;local['display_shown']=None;local['editor_place']=None
                for arrange in arrangers:root.after_idle(arrange)
                if old is None or bool(old.get('display_full_screen'))!=bool(values['display_full_screen']):set_full_screen(values['display_full_screen'])
            def place_caret():
                # Windows Magnifier, ZoomText and Fusion follow the system caret: put it on the
                # selected item or the reading place.
                local['caret_after']=None
                target=local['caret_target']
                if not target:return
                widget,index=target
                try:
                    if not widget.winfo_ismapped():return
                    box=widget.bbox(index)
                    if box:root.tk.call('tk','caret',widget,'-x',box[0],'-y',box[1],'-height',box[3])
                except tk.TclError:pass
            def schedule_caret(widget,index):
                local['caret_target']=(widget,index)
                if local['caret_after'] is None:local['caret_after']=root.after(60,place_caret)
            def mark_current_file(index):
                palette=local['palette']
                if not palette or not 0<=index<file_list.size():return
                old=local['file_current']
                if old is not None and old!=index and old<file_list.size():
                    file_list.itemconfigure(old,background='',foreground='',selectbackground='',selectforeground='')
                file_list.itemconfigure(index,background=palette['select_bg'],foreground=palette['select_fg'],selectbackground=palette['select_bg'],selectforeground=palette['select_fg'])
                local['file_current']=index;schedule_caret(file_list,index)
            def show_display(field,text_mode):
                """Show the screen name, and the list, menu or note. Returns the view to show, or None."""
                position=position_text(field)
                heading.configure(text=(field.get('display_title') or 'Voice Companion')+('   '+position if position else ''))
                if text_mode or field.get('mode')=='files':return None
                items=field.get('display_items');text=field.get('display_text')
                if items is None and text is None:return history
                key=(tuple(items) if items is not None else None,field.get('display_index'),text,field.get('display_caret'))
                if key!=local['display_shown']:
                    local['display_shown']=key
                    display_view.configure(state='normal');display_view.delete('1.0','end')
                    if items is not None:
                        index=field.get('display_index')
                        for i,line in enumerate(list_lines(items,index) if items else ['\tThe list is empty.']):
                            display_view.insert('end',line+'\n',('item','selected') if i==index else ('item',))
                        target=str(index+1)+'.0' if index is not None else '1.0'
                    else:
                        display_view.insert('1.0',text)
                        target='1.0 + '+str(min(len(text),max(0,int(field.get('display_caret') or 0))))+' chars'
                        display_view.tag_add('reading_line',target+' linestart',target+' lineend')
                    display_view.configure(state='disabled')
                    if items is not None and index is not None:
                        # Keep an item above and below the selected one in view, when there is room.
                        display_view.see(str(index+3)+'.0');display_view.see(str(max(1,index))+'.0')
                    display_view.see(target)
                    local['display_target']=target
                schedule_caret(display_view,local.get('display_target','1.0'))
                return display_view
            def track_editor():
                # The reading line follows the caret as keys move it or the app reads on.
                if not editor.winfo_ismapped():return
                place=(editor.index('insert'),editor.index('end'))
                if place==local['editor_place']:return
                local['editor_place']=place
                editor.tag_remove('reading_line','1.0','end')
                editor.tag_add('reading_line','insert display linestart','insert display lineend')
                schedule_caret(editor,'insert')
            def show_message(message):
                message_view.configure(state='normal');message_view.delete('1.0','end');message_view.insert('1.0',message)
                message_view.configure(state='disabled')
                try:lines=int(root.tk.call(message_view,'count','-update','-displaylines','1.0','end'))
                except (tk.TclError,ValueError):lines=2
                message_view.configure(height=max(1,min(3,lines)))
            def show_body(view):
                for other in (history,display_view):
                    if other is not view:other.pack_forget()
                view.pack(in_=content,fill='both',expand=True,padx=18,pady=(0,8))
            def poll_once():
                while True:
                    if self.closed.is_set():return
                    try: action, field = self.ui_actions.get_nowait()
                    except queue.Empty: break
                    if action=='cancel_phonetic':cancel_phonetic();continue
                    if action=='ui_call':
                        field();continue
                    if action=='context':
                        if field.get('mode')=='update_download':
                            first=next(iter(content.pack_slaves()),None)
                            update_toolbar.pack(in_=content,fill='x',padx=18,pady=6,**({'before':first} if first is not None and first!=update_toolbar else {}))
                        else:update_toolbar.pack_forget()
                        old=self.context;self.context=field
                        if field.get('display'):apply_display(field['display'])
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
                                entry_label.configure(text=field.get('account_label') or ('Yahoo app password (private); paste or type, then press Enter. Escape cancels:' if field.get('account_private') else 'Yahoo email address; type, then press Enter. Escape cancels:'))
                                typed.focus_force()
                        if field.get('web_private') and field.get('web_field')!=old.get('web_field'):
                            typed.delete(0,'end');typed.insert(0,field['web_field'].get('value',''));entry_label.configure(text=field['web_field']['label']+' (private); Enter saves, Tab moves, Escape cancels:')
                        if mode!='files':file_list.pack_forget();file_toolbar.pack_forget()
                        if mode=='files':
                            history.pack_forget();editor.pack_forget();display_view.pack_forget()
                            file_toolbar.pack(in_=content,side='bottom',fill='x',padx=18,pady=(0,8));file_list.pack(in_=content,fill='both',expand=True,padx=18)
                            values=field.get('file_items',[])
                            if tuple(file_list.get(0,'end'))!=tuple(values):
                                file_list.delete(0,'end')
                                for value in values:file_list.insert('end',value)
                            file_list.selection_clear(0,'end')
                            index=min(field.get('file_index',0),max(0,len(values)-1))
                            for selected_index in field.get('file_selected_indices',[index]):
                                if 0<=selected_index<len(values):file_list.selection_set(selected_index)
                            if values:file_list.activate(index);file_list.see(index);mark_current_file(index)
                            if field.get('focus'):
                                if field.get('file_name_input'):typed.focus_force()
                                else:file_list.focus_force()
                        text_mode=source is not None and mode in ('document','email_draft','mailbox','help','web','file_reader')
                        body=show_display(field,text_mode)
                        if transition or (field.get('ack',0)>=local['seq'] and field.get('text','')!=editor.get('1.0','end-1c')):cancel_phonetic()
                        if text_mode:
                            editor.configure(state='normal')
                            history.pack_forget();display_view.pack_forget();editor.pack(in_=content,fill='both',expand=True,padx=18,pady=(0,8))
                            local['editor_place']=None
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
                                    # The document's own size, made larger by the text size in Display options.
                                    editor.tag_configure(tag,font=(family,display_options.scaled(int(size),local['display_values'] or {}),weight,slant))
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
                            editor.pack_forget()
                            if mode!='files':show_body(body or history)
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
                    if action == 'reclaim_focus':
                        root.deiconify()
                        root.lift()
                        # focus_force alone cannot take the keyboard back from another program
                        # (Thunderbird starting, or the installer); Windows needs the foreground call.
                        try:foreground_window(root)
                        except (AttributeError,OSError):pass
                        root.focus_force()
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
                latest=None
                while True:
                    if self.closed.is_set():return
                    try: message = self.messages.get_nowait()
                    except queue.Empty: break
                    self.current.set(message);latest=message
                    history.configure(state='normal')
                    history.insert('end', message + '\n\n')
                    if int(history.index('end-1c').split('.')[0]) > 90:
                        history.delete('1.0', '30.0')
                    history.see('end')
                    history.configure(state='disabled')
                if latest is not None:show_message(latest)
                track_editor()
            def poll():
                # One failed update must never stop the window: without the
                # next check it would stop speaking and answering keys for good.
                try:poll_once()
                except Exception as exc:
                    import sys,traceback
                    self.ui_errors.append(''.join(traceback.format_exception(type(exc),exc,exc.__traceback__))[-3000:])
                    print('Voice Companion window error:\n'+self.ui_errors[-1],file=sys.stderr,flush=True)
                finally:
                    if not self.closed.is_set(): root.after(15, poll)

            apply_display(display_values,force=True)
            self.ready.set()
            root.after(15, poll)
            root.mainloop()
        except Exception as exc:
            self.problem = exc.with_traceback(None)
            self.ready.set()
            self.closed.set()


def route_alt_keys(root):
    """Give Alt keys to Companion, not to the Windows system menu.

    On Windows, Tk binds Alt on the "all" tag (<KeyRelease-Alt_L>,
    <Alt-KeyPress> and others) and hands those keys to Windows. Those patterns
    are more specific than Companion's own <KeyPress> and <KeyRelease>, so a
    lone Alt opened the system menu instead of the screen's Alt menu, and Alt
    with a letter never reached Companion. Here the Alt patterns run
    Companion's handlers instead. Alt+Space still opens the system menu.
    Call this after every <KeyPress> and <KeyRelease> binding on "all" is made."""
    call=root.tk.call
    press=call('bind','all','<KeyPress>');release=call('bind','all','<KeyRelease>')
    system_menu='if {[info commands ::tk::WinMenuKey] ne ""} {::tk::WinMenuKey %W %N}'
    for sequence in ('<KeyRelease-Alt_L>','<KeyRelease-Alt_R>','<Alt-KeyRelease>'):
        call('bind','all',sequence,release)
    call('bind','all','<Alt-KeyPress>',press)
    call('bind','all','<Alt-KeyPress-space>',press+'\n'+system_menu)
    call('bind','all','<Alt-KeyRelease-space>',release+'\n'+system_menu)

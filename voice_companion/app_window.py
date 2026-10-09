"""Small status window for the Windows voice application.

The window lives on its own UI thread. Other threads only post text to a queue.
Closing the window requests a clean shutdown of the microphone loop.
"""
import queue
import threading
from field_selection import FieldSelection
from keyboard_text import caret_feedback,typing_feedback,phonetic,character as spoken_character


def entry_feedback(before, after, old_caret, caret, key, character=""):
    """Short spoken feedback for a standard editable email entry."""
    names = {'@':'at sign', '.':'dot', '-':'hyphen', '_':'underscore', ' ':'space'}
    def say(char): return names[char] if char in names else spoken_character(char)
    if key == 'Left': return say(after[caret]) if caret < len(after) else 'End of field'
    if key == 'Right': return say(after[caret]) if caret < len(after) else 'End of field'
    if key == 'Home': return 'Start of field'
    if key == 'End': return 'End of field'
    if key in ('BackSpace','Delete') and len(after) < len(before):
        removed = before[caret:old_caret] if key == 'BackSpace' else before[old_caret:old_caret+1]
        return 'Deleted ' + say(removed[-1]) if removed else 'Deleted'
    if character and character.isprintable(): return say(character)
    if len(after) > len(before):
        return say(after[max(0,caret-1)])
    return None


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
        self.thread = threading.Thread(target=self._thread_main, name='Voice Companion window', daemon=True)

    def _thread_main(self):
        try:self._run()
        finally:
            # Tcl interpreters and widget cycles must be released on their owner thread.
            self.root=self.typed=self.editor=self.current=None
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

    def feedback(self,text):
        if text:
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
        value = value.strip()
        if value:
            self.commands.put(value)
            self.last_email_request = None
            return True
        return False

    def report_entry_key(self, before, after, old_caret, caret, key, character=""):
        echo=getattr(self,'context',{}).get('echo','characters')
        notice=typing_feedback(before,after,caret,echo) if before!=after else entry_feedback(before,after,old_caret,caret,key)
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
            root.title('Voice Companion ' + self.version)
            root.geometry('660x430')
            root.minsize(470, 300)
            root.protocol('WM_DELETE_WINDOW', lambda: (self.closed.set(), root.destroy()))
            heading = tk.Label(root, text='Voice Companion', font=('Segoe UI', 20, 'bold'))
            heading.pack(anchor='w', padx=18, pady=(16, 4))
            tk.Label(root, text='Say “Wake up” to begin. Say “What version?” to check this build.',
                     font=('Segoe UI', 11), wraplength=600, justify='left').pack(anchor='w', padx=18)
            entry_label = tk.Label(root, text='Type a command or email address, then press Enter:',
                                   font=('Segoe UI', 11))
            entry_label.pack(anchor='w', padx=18, pady=(8, 0))
            typed = tk.Entry(root, font=('Segoe UI', 12))
            self.typed=typed
            typed.pack(fill='x', padx=18)
            self.email_field = None
            previous = ['', 0]
            field_selection = FieldSelection()
            def before_key(event):
                previous[:] = [typed.get(), typed.index('insert')]
                cancel_phonetic()
                if event.keysym in ('Up','Down','Left','Right') and not typed.get() and not self.email_field and self.context.get('mode')!='document_name':return
                before=typed.get();old_caret=typed.index('insert')
                def after_entry():
                    caret=typed.index('insert');value=typed.get()
                    self.report_entry_key(before,value,old_caret,caret,event.keysym,event.char)
                    if event.keysym in ('Left','Right') and not event.state&4 and caret<len(value) and self.context.get('phonetic') and phonetic(value[caret]):
                        local['phonetic']=root.after(int(float(self.context.get('delay',.5))*1000),lambda:self.feedback(phonetic(value[caret])))
                root.after_idle(after_entry)
            def after_key(event):
                self.report_entry_key(previous[0], typed.get(), previous[1], typed.index('insert'), event.keysym, event.char)
            typed.bind('<KeyPress>', before_key)
            root.bind_all('<Alt-t>', lambda event: typed.focus_set())
            root.after_idle(lambda:typed.focus_set() if self.context.get('source') is None and self.context.get('mode')!='settings' else None)
            def submit_typed(event=None):
                if self.submit_entry(typed.get()):
                    typed.delete(0, 'end')
                return 'break'
            def navigation(event):
                if event.widget.winfo_toplevel() != root: return
                if event.keysym == 'Escape':
                    self.commands.put(('keyboard','Escape')); return 'break'
                if event.keysym=='Return' and isinstance(event.widget,tk.Button):
                    event.widget.invoke(); return 'break'
                if event.widget==editor:return
                if event.widget==typed and (self.email_field or typed.get() or self.context.get('mode')=='document_name'):return
                if event.widget == typed or event.widget == history or event.widget == root or isinstance(event.widget,tk.Button):
                    self.commands.put(('keyboard','Enter' if event.keysym=='Return' else event.keysym)); return 'break'
            def enter(event):
                if self.context.get('mode')=='document_name':
                    if not typed.get().strip():self.feedback('Enter a document name.');return 'break'
                    return submit_typed(event)
                if self.email_field or typed.get(): return submit_typed(event)
                return navigation(event)
            typed.bind('<Return>', enter)
            for key in ('Up','Down','Left','Right','Escape'):
                root.bind_all('<'+key+'>',navigation)
            root.bind_all('<Return>',navigation,add='+')
            def settings_shortcut(event):
                self.commands.put(('keyboard','settings')); return 'break'
            root.bind_all('<Control-comma>',settings_shortcut)
            tk.Button(root,text='Settings (Ctrl+Comma)',command=lambda:self.commands.put(('keyboard','settings'))).pack(anchor='w',padx=18)
            tk.Button(root, text='Enter typed command', command=submit_typed).pack(anchor='w', padx=18)
            tk.Button(root, text='AI voice setup (trainer)', command=self.setup_ai_voice).pack(anchor='w', padx=18)
            self.current = tk.StringVar(value='Starting speech and microphone...')
            tk.Label(root, textvariable=self.current, font=('Segoe UI', 12),
                     wraplength=600, justify='left').pack(anchor='w', padx=18, pady=14)
            history = scrolledtext.ScrolledText(root, wrap='word', font=('Segoe UI', 11), state='disabled')
            history.pack(fill='both', expand=True, padx=18, pady=(0, 18))
            editor=tk.Text(root,wrap='word',font=('Segoe UI',12),undo=True,exportselection=False)
            self.editor=editor
            local={'text':'','seq':0,'applying':False,'phonetic':None}
            def offset(index='insert'):return len(editor.get('1.0',index))
            def selection():
                try:return (offset('sel.first'),offset('sel.last'))
                except tk.TclError:return None
            def cancel_phonetic():
                if local['phonetic'] is not None:
                    root.after_cancel(local['phonetic']);local['phonetic']=None
            def read_caret(key,control=False):
                text=editor.get('1.0','end-1c');caret=offset()
                notice,char=caret_feedback(text,caret,key,control,selection())
                if not selection() and key in ('Up','Down') and not control:
                    notice=editor.get('insert display linestart','insert display lineend') or 'Blank line.'
                self.feedback(notice);cancel_phonetic()
                if char and self.context.get('phonetic') and phonetic(char):
                    local['phonetic']=root.after(int(float(self.context.get('delay',.5))*1000),lambda:self.feedback(phonetic(char)))
            def send_text_position(before=None):
                text=editor.get('1.0','end-1c');local['seq']+=1
                self.commands.put(('text_edit',{'source':self.context.get('source'),'before':local['text'] if before is None else before,'after':text,'caret':offset(),'selection':selection(),'seq':local['seq'],'readonly':self.context.get('readonly',False)}))
                local['text']=text
            def changed(event=None):
                if not editor.edit_modified():return
                editor.edit_modified(False)
                if not local['applying']:send_text_position()
            editor.bind('<<Modified>>',changed)
            def edit_key(event):
                cancel_phonetic()
                if event.keysym=='Escape':return navigation(event)
                if event.keysym=='comma' and event.state&4:return settings_shortcut(event)
                if event.keysym=='a' and event.state&4:
                    editor.tag_add('sel','1.0','end-1c');read_caret('Right');send_text_position();return 'break'
                nav=event.keysym in ('Left','Right','Up','Down','Home','End','Prior','Next')
                if self.context.get('readonly') and event.keysym=='Tab':
                    target=editor.tk_focusPrev() if event.state&1 else editor.tk_focusNext()
                    if target:target.focus_set()
                    return 'break'
                if self.context.get('readonly') and not nav and event.keysym not in ('Shift_L','Shift_R','Control_L','Control_R','Tab'):
                    if event.state&4 and event.keysym.lower()=='c':return
                    return 'break'
                if event.state&4 and event.keysym in ('Up','Down'):
                    old_index=editor.index('insert');row=int(old_index.split('.')[0])
                    target=f'{row}.0' if event.keysym=='Up' and int(old_index.split('.')[1]) else f'{max(1,row-1) if event.keysym=="Up" else row+1}.0'
                    editor.mark_set('insert',target)
                    if event.state&1:
                        anchor=editor.index('anchor') if editor.tag_ranges('sel') else old_index
                        editor.mark_set('anchor',anchor);editor.tag_remove('sel','1.0','end')
                        a,b=sorted((anchor,editor.index('insert')),key=lambda i:tuple(map(int,i.split('.'))))
                        editor.tag_add('sel',a,b)
                    else:editor.tag_remove('sel','1.0','end')
                    editor.see('insert');read_caret(event.keysym,True);send_text_position();return 'break'
                before=editor.get('1.0','end-1c')
                def after():
                    if local['applying']:return
                    if nav:read_caret(event.keysym,bool(event.state&4));send_text_position()
                    else:self.feedback(typing_feedback(before,editor.get('1.0','end-1c'),offset(),self.context.get('echo','characters')))
                root.after_idle(after)
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
                    try: action, field = self.ui_actions.get_nowait()
                    except queue.Empty: break
                    if action=='cancel_phonetic':cancel_phonetic();continue
                    if action=='ui_call':
                        field();continue
                    if action=='context':
                        old=self.context;self.context=field
                        mode=field['mode'];source=field.get('source')
                        transition=old.get('mode')!=mode or old.get('source')!=source or field.get('focus',False)
                        try:root.attributes('-disabled',mode=='settings')
                        except tk.TclError:pass
                        if mode=='settings':continue
                        text_mode=source is not None and mode in ('document','email_draft','mailbox')
                        if transition or (field.get('ack',0)>=local['seq'] and field.get('text','')!=editor.get('1.0','end-1c')):cancel_phonetic()
                        if text_mode:
                            history.pack_forget();editor.pack(fill='both',expand=True,padx=18,pady=(0,18))
                            if old.get('source')!=source or field.get('ack',0)>=local['seq']:
                                local['applying']=True
                                if editor.get('1.0','end-1c')!=field.get('text',''):
                                    editor.delete('1.0','end');editor.insert('1.0',field.get('text',''));editor.edit_reset()
                                editor.edit_modified(False);local['text']=field.get('text','')
                                caret=field.get('caret',len(local['text']));editor.mark_set('insert','1.0 + '+str(caret)+' chars')
                                editor.tag_remove('sel','1.0','end')
                                if field.get('selection'):editor.tag_add('sel',*('1.0 + '+str(i)+' chars' for i in field['selection']))
                                editor.see('insert');local['applying']=False
                            self.email_field=None
                            if transition:typed.delete(0,'end');self.last_email_request=None
                            if transition:editor.focus_force()
                        else:
                            editor.pack_forget();history.pack(fill='both',expand=True,padx=18,pady=(0,18))
                            if mode!='email_draft' or not self.email_field:
                                self.email_field=None;self.last_email_request=None
                                if transition and not (mode=='document_name' and old.get('mode')=='settings'):typed.delete(0,'end')
                            request=field.get('naming_request')
                            if mode=='document_name' and request and request!=self.last_name_request:
                                typed.delete(0,'end');typed.insert(0,request[1]);typed.icursor('end');self.last_name_request=request
                            if mode!='email_draft':entry_label.configure(text='Document name; press Enter to save:' if mode=='document_name' else 'Type a command, then press Enter:')
                            if transition:typed.focus_force()
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
            self.problem = exc
            self.ready.set()
            self.closed.set()


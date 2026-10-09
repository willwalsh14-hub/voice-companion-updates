"""Small status window for the Windows voice application.

The window lives on its own UI thread. Other threads only post text to a queue.
Closing the window requests a clean shutdown of the microphone loop.
"""
import queue
import threading
from field_selection import FieldSelection


def entry_feedback(before, after, old_caret, caret, key, character=""):
    """Short spoken feedback for a standard editable email entry."""
    names = {'@':'at sign', '.':'dot', '-':'hyphen', '_':'underscore', ' ':'space'}
    def say(char): return names.get(char, char)
    if key == 'Left': return say(after[caret]) if caret < len(after) else 'End of field'
    if key == 'Right': return say(after[caret-1]) if caret else 'Start of field'
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
        self.messages = queue.Queue()
        self.commands = queue.Queue()
        self.ui_actions = queue.Queue()
        self.key_feedback = queue.Queue()
        self.closed = threading.Event()
        self.ready = threading.Event()
        self.last_email_request = None
        self.email_selection_edit = None
        self.problem = None
        self.thread = threading.Thread(target=self._run, name='Voice Companion window', daemon=True)

    def start(self):
        self.thread.start()
        self.ready.wait(5)
        if self.problem:
            raise RuntimeError('The Voice Companion window could not open.') from self.problem

    def show(self, message):
        if not self.closed.is_set():
            self.messages.put(str(message))

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
        if getattr(self, 'email_field', None):
            notice = entry_feedback(before, after, old_caret, caret, key, character)
            if notice: self.key_feedback.put(notice)

    def setup_ai_voice(self):
        if not self.closed.is_set():
            self.ui_actions.put(('ai_voice', None))

    def _run(self):
        try:
            import tkinter as tk
            from tkinter import scrolledtext
            root = tk.Tk()
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
            typed.pack(fill='x', padx=18)
            self.email_field = None
            previous = ['', 0]
            field_selection = FieldSelection()
            def before_key(event):
                previous[:] = [typed.get(), typed.index('insert')]
            def after_key(event):
                self.report_entry_key(previous[0], typed.get(), previous[1], typed.index('insert'), event.keysym, event.char)
            typed.bind('<KeyPress>', before_key)
            typed.bind('<KeyRelease>', after_key)
            root.bind_all('<Alt-t>', lambda event: typed.focus_set())
            root.after(100, typed.focus_set)
            def submit_typed(event=None):
                if self.submit_entry(typed.get()):
                    typed.delete(0, 'end')
                return 'break'
            typed.bind('<Return>', submit_typed)
            tk.Button(root, text='Enter typed command', command=submit_typed).pack(anchor='w', padx=18)
            tk.Button(root, text='AI voice setup (trainer)', command=self.setup_ai_voice).pack(anchor='w', padx=18)
            self.current = tk.StringVar(value='Starting speech and microphone...')
            tk.Label(root, textvariable=self.current, font=('Segoe UI', 12),
                     wraplength=600, justify='left').pack(anchor='w', padx=18, pady=14)
            history = scrolledtext.ScrolledText(root, wrap='word', font=('Segoe UI', 11), state='disabled')
            history.pack(fill='both', expand=True, padx=18, pady=(0, 18))

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
                if not self.closed.is_set(): root.after(100, poll)

            self.ready.set()
            root.after(100, poll)
            root.mainloop()
        except Exception as exc:
            self.problem = exc
            self.ready.set()
            self.closed.set()

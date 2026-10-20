"""Spoken file lists backed by the Windows Shell, with asynchronous Shell work."""
from dataclasses import dataclass
from pathlib import Path
import os
import ntpath
import queue
import re
import threading

@dataclass(frozen=True)
class Item:
    name:str
    path:str
    folder:bool=False
    drive:bool=False
    filesystem:bool=True
    created:float|None=None
    modified:float|None=None
    added:float|None=None
    def label(self):return self.name+', '+('drive' if self.drive else 'folder' if self.folder else 'file')

def location_name(path):
    if path is None:return 'Drive list'
    clean=str(path).rstrip('\\/')
    drive,tail=ntpath.splitdrive(clean)
    if drive and not tail:return drive.rstrip(':')+' drive' if len(drive)==2 else ntpath.basename(drive)
    return ntpath.basename(clean) or 'Root folder'

def valid_name(name):
    name=name.strip()
    if not name or name in ('.','..') or re.search(r'[<>:"/\\|?*\x00-\x1f]',name) or name.endswith(('.', ' ')) or name.split('.')[0].upper() in {'CON','PRN','AUX','NUL',*(f'COM{i}' for i in range(1,10)),*(f'LPT{i}' for i in range(1,10))}:
        raise ValueError('Use a file name without slashes or special Windows characters.')
    return name

def shell_thread(function):
    def call(*args,**kwargs):
        import pythoncom
        pythoncom.CoInitialize()
        try:return function(*args,**kwargs)
        finally:pythoncom.CoUninitialize()
    return call

class ShellFiles:
    """Use the same Shell namespace, file operation engine, and clipboard as Explorer."""
    def _shell(self):
        import pythoncom
        from win32com.client import Dispatch
        return Dispatch('Shell.Application')
    def _item(self,e,drive=False):
        def date(name):
            try:
                value=e.ExtendedProperty(name)
                return value.timestamp() if hasattr(value,'timestamp') else None
            except Exception:return None
        return Item(Path(str(e.Path)).name if e.IsFileSystem and not drive else str(e.Name),str(e.Path),bool(e.IsFolder),drive,bool(e.IsFileSystem),date('System.DateCreated') if not drive else None,date('System.DateModified') if not drive else None)
    @shell_thread
    def roots(self):
        shell=self._shell();result=[]
        folder=shell.NameSpace(17)
        if folder:
            # This PC also contains known folders; only volume/UNC roots are drives.
            result=[self._item(e,True) for e in folder.Items()
                    if e.IsFolder and str(e.Path).rstrip('\\/').casefold()==ntpath.splitdrive(str(e.Path))[0].casefold()
                    and ntpath.splitdrive(str(e.Path))[0]]
        # Explorer cloud providers register per-user roots with the Shell.
        import winreg
        import win32api,win32security,win32con
        token=win32security.OpenProcessToken(win32api.GetCurrentProcess(),win32con.TOKEN_QUERY)
        try:sid=win32security.ConvertSidToStringSid(win32security.GetTokenInformation(token,win32security.TokenUser)[0])
        finally:token.Close()
        base=r'Software\Microsoft\Windows\CurrentVersion\Explorer\SyncRootManager'
        for hive in (winreg.HKEY_CURRENT_USER,winreg.HKEY_LOCAL_MACHINE):
            try:
                with winreg.OpenKey(hive,base) as key:
                    for i in range(winreg.QueryInfoKey(key)[0]):
                        provider=winreg.EnumKey(key,i)
                        try:
                            with winreg.OpenKey(key,provider+r'\UserSyncRoots') as user:
                                path=winreg.QueryValueEx(user,sid)[0]
                            namespace=shell.NameSpace(path)
                            if namespace:result.append(Item(str(namespace.Title),str(path),True,True))
                        except OSError:continue
            except OSError:pass
        unique={}
        for item in result:unique.setdefault(item.path.casefold(),item)
        return list(unique.values())
    @shell_thread
    def list(self,path):
        shell=self._shell();folder=shell.NameSpace(path)
        if folder is None:raise ValueError('Windows could not open this location. It may be disconnected or unavailable.')
        items=[self._item(e) for e in folder.Items()]
        return sorted(items,key=lambda x:(not x.folder,x.name.casefold()))
    @shell_thread
    def parent(self,path):
        shell=self._shell();folder=shell.NameSpace(path)
        if not folder or not folder.ParentFolder:return None
        parent=str(folder.ParentFolder.Self.Path)
        return None if parent.startswith('::') else parent
    def clipboard(self,paths,cut):
        import struct,win32clipboard
        # DROPFILES: native Explorer CF_HDROP, followed by UTF-16 file names.
        data=struct.pack('<IiiII',20,0,0,0,1)+('\0'.join(paths)+'\0\0').encode('utf-16le')
        win32clipboard.OpenClipboard()
        try:
            win32clipboard.EmptyClipboard();win32clipboard.SetClipboardData(15,data)
            win32clipboard.SetClipboardData(win32clipboard.RegisterClipboardFormat('Preferred DropEffect'),struct.pack('<I',2 if cut else 1))
        finally:win32clipboard.CloseClipboard()
    def get_clipboard(self):
        import win32clipboard,struct
        win32clipboard.OpenClipboard()
        try:
            if not win32clipboard.IsClipboardFormatAvailable(15):raise ValueError('The clipboard has no files to paste.')
            paths=list(win32clipboard.GetClipboardData(15));effect=win32clipboard.RegisterClipboardFormat('Preferred DropEffect')
            cut=win32clipboard.IsClipboardFormatAvailable(effect) and struct.unpack('<I',win32clipboard.GetClipboardData(effect)[:4])[0]==2
            return paths,cut
        finally:win32clipboard.CloseClipboard()
    def finish_cut(self,paths):
        import win32clipboard,struct
        try:
            win32clipboard.OpenClipboard()
            try:
                effect=win32clipboard.RegisterClipboardFormat('Preferred DropEffect')
                if win32clipboard.IsClipboardFormatAvailable(15) and win32clipboard.IsClipboardFormatAvailable(effect) and list(win32clipboard.GetClipboardData(15))==paths and struct.unpack('<I',win32clipboard.GetClipboardData(effect)[:4])[0]==2:
                    win32clipboard.EmptyClipboard()
            finally:win32clipboard.CloseClipboard()
        except Exception:pass # The move succeeded; a busy/new clipboard must be left alone.
    @shell_thread
    def operate(self,action,paths,destination=None,name=None):
        import pythoncom
        from win32com.shell import shell,shellcon
        paths=[str(Path(p).absolute()) for p in paths]
        if not paths:raise ValueError('Select a file or folder first.')
        for p in paths:
            if not Path(p).exists():raise ValueError('An item moved or was removed. Refresh the folder and try again.')
            if Path(p).parent==Path(p):raise ValueError('Drives cannot be changed here.')
        if action=='rename':
            name=valid_name(name)
            if len(paths)!=1:raise ValueError('Rename one item at a time.')
            if Path(paths[0]).with_name(name).exists() and str(Path(paths[0]).with_name(name)).casefold()!=paths[0].casefold():raise ValueError('That name already exists. Choose another name.')
        if action in ('copy','cut'):
            if not destination or not Path(destination).is_dir():raise ValueError('Open a writable folder before pasting.')
            for p in paths:
                target=Path(destination)/Path(p).name
                if target.exists():raise ValueError('An item named '+target.name+' already exists. Nothing was replaced. Rename it or choose another folder.')
                if Path(p).is_dir() and (Path(destination)==Path(p) or Path(p) in Path(destination).parents):raise ValueError('A folder cannot be pasted inside itself.')
        operation=pythoncom.CoCreateInstance(shell.CLSID_FileOperation,None,pythoncom.CLSCTX_INPROC_SERVER,shell.IID_IFileOperation)
        # Keep Explorer undo records; recycling is required rather than asking
        # the user to control a second application's progress dialogs.
        flags=0x4|0x10|0x400|0x100000|0x20000000|0x8
        if action=='delete':flags|=0x80000
        operation.SetOperationFlags(flags)
        target=shell.SHCreateItemFromParsingName(destination,None,shell.IID_IShellItem) if destination else None
        for path in paths:
            item=shell.SHCreateItemFromParsingName(path,None,shell.IID_IShellItem)
            if action=='delete':operation.DeleteItem(item,None)
            elif action=='rename':operation.RenameItem(item,name,None)
            elif action=='copy':operation.CopyItem(item,target,None,None)
            elif action=='cut':operation.MoveItem(item,target,None,None)
            else:raise ValueError('Unknown file action.')
        operation.PerformOperations()
        if operation.GetAnyOperationsAborted():raise ValueError('Windows did not finish every item. Refresh and check the folder before trying again.')

MENU=(('Open','Enter','open'),('Select or unselect current item','Space','toggle selection'),('Select all','Ctrl+A','select all'),('Clear selection','Ctrl+Shift+A','clear selection'),('Rename','F2','rename'),('Delete','Delete','delete'),('Copy','Ctrl+C','copy'),('Cut','Ctrl+X','cut'),('Paste','Ctrl+V','paste'),('Refresh','F5','refresh'),('Previous folder','Escape','go back'),('Drive list','Ctrl+D','drive list'),('Read file manager help','F1','file manager help'),('Main menu','Say Main menu','main menu'))
SORT_MENU=(('Name','Alt+N','sort by name'),('Date created','Alt+C','sort by date created'),('Date modified','Alt+M','sort by date modified'),('Date added (first seen here)','Alt+D','sort by date added'),('Ascending','Alt+A','sort ascending'),('Descending','Alt+Z','sort descending'))
MENU=MENU[:-1]+(('Search current list','Ctrl+E','search files'),('Search subfolders','Ctrl+Shift+F','search subfolders'),('Clear search','Ctrl+Shift+X','clear search'),('Sort files','Alt+S','sort files'),*SORT_MENU,('Main menu','Alt+Home','main menu'))

HELP='File manager. Up and Down move through items. Home and End reach the list boundaries. Enter opens. Escape goes up one folder, then back to the drive list. Say an item name, or open followed by its name. Space toggles selection. Say select, unselect, mark, unmark, check, or uncheck, with an item name if needed. Ctrl A selects all; Ctrl Shift A clears selections. F2 renames. Delete asks you to confirm removal. Ctrl C copies; Ctrl X cuts; Ctrl V pastes in the current folder. F5 refreshes. Alt opens the file manager menu with all keyboard and voice commands. Ctrl D returns to drives. Type the first few letters of an item to jump to it. Repeated letters cycle matches. Backspace removes a letter. Ctrl F searches names in this list; Ctrl Shift F searches subfolders. Ctrl Shift X clears search. Alt S opens Sort. Ctrl Shift 1 through 4 sorts by name, date created, date modified, or date added. Ctrl Shift Up and Down choose ascending and descending. Date added means first seen here. Tab and Shift Tab reach Search, Sort, Clear search and Drives buttons. Alt Home returns to the main menu. Say Main menu to leave File manager.'

class FileManager:
    def __init__(self,backend=None):
        self.backend=backend or ShellFiles();self.items=[];self.index=0;self.location=None;self.history=[];self.selected=set();self.menu=False;self.menu_index=0;self.pending=None;self.busy=False;self.events=queue.Queue();self.opened=None;self.revision=0;self.job_id=0;self.mutating=False
        from file_explorer import FileView
        from regular_browser import data_folder
        self.view=FileView(data_folder()/'file-dates' if backend is None else None)
    def start_job(self,work,done,notice,mutating=False):
        if self.busy:return 'File manager is working. Please wait.'
        self.busy=True;self.mutating=mutating;self.job_id+=1;job_id=self.job_id
        def run():
            try:self.events.put((job_id,done,work(),None))
            except Exception as exc:self.events.put((job_id,None,None,str(exc) if isinstance(exc,(ValueError,OSError)) else 'Windows could not complete that file action. Check the location and permissions, then refresh.'))
        threading.Thread(target=run,daemon=True,name='File manager Shell').start()
        return notice
    def poll(self):
        while True:
            try:job_id,done,value,error=self.events.get_nowait()
            except queue.Empty:return None
            if job_id==self.job_id:break
        self.busy=False
        if error:return error
        try:return done(value)
        except (ValueError,OSError) as exc:return str(exc)
    def menu_items(self):return SORT_MENU if self.menu=='sort' else MENU
    def labels(self):
        if self.busy:return ['File manager is working']
        if self.pending:return ['Yes','No'] if self.pending[0]=='delete' else []
        if self.menu:return [name+'. '+key+'. Say '+voice+'.' for name,key,voice in self.menu_items()]
        return [i.label()+(', selected' if i.path in self.selected else '') for i in self.items]
    def current(self):return self.items[self.index] if self.items and 0<=self.index<len(self.items) else None
    def say_current(self):
        labels=self.labels();i=self.menu_index if self.menu else self.index
        return labels[i]+'. '+str(i+1)+' of '+str(len(labels))+'.' if labels else 'This folder is empty.'
    def refresh(self,path=None,push=False,preserve=None,restore_index=None):
        def done(items):
            extensions=getattr(self,'pick_extensions',None)
            if path is not None and extensions:items=[item for item in items if item.folder or Path(item.path).suffix.lower() in extensions]
            if push:self.history.append((self.location,self.index))
            if path!=self.location:self.view.filter=''
            self.view.prefix='';self.view.searching=False
            self.view.all_items=self.view.dated(items,path,prune=True)
            self.location=path;self.items=self.view.shown();self.selected.clear();items=self.items
            found=next((n for n,i in enumerate(items) if i.path==preserve),None)
            self.index=found if found is not None else min(restore_index,max(0,len(items)-1)) if restore_index is not None else 0
            self.revision+=1
            return location_name(path)+'. '+self.say_current()
        return self.start_job(lambda:self.backend.roots() if path is None else self.backend.list(path),done,'Loading drive list.' if path is None else 'Opening folder.')
    def refresh_here(self):return self.refresh(self.location,preserve=self.current().path if self.current() else None)
    def targets(self):
        targets=[i for i in self.items if i.path in self.selected] or ([self.current()] if self.current() else [])
        if not targets:raise ValueError('No item is selected.')
        if self.location is None or any(i.drive or not i.filesystem for i in targets):raise ValueError('Choose files or folders inside a drive. Drives and virtual locations cannot be renamed, deleted, copied, or cut.')
        return targets
    def choose(self,name):
        name=name.strip().casefold()
        matches=[(n,i) for n,i in enumerate(self.items) if i.name.casefold()==name or i.path.casefold()==name or Path(i.name).stem.casefold()==name or (i.drive and name in (ntpath.splitdrive(i.path)[0].rstrip(':').casefold()+' drive','drive '+ntpath.splitdrive(i.path)[0].rstrip(':').casefold()))]
        if len(matches)!=1:raise ValueError('More than one item matches. Use arrows or say its full path.' if matches else 'No item has that name in this list.')
        self.index=matches[0][0]
    def command(self,text):
        text=text.strip();cmd=text.casefold().rstrip('.!?')
        cmd={'first item':'home','last item':'end','start of list':'home','end of list':'end','activate current item':'open','open current item':'open','select current item':'select','unselect current item':'unselect'}.get(cmd,cmd)
        if cmd in ('file manager help','help with file manager','help for file manager','help','commands'):return HELP
        if self.busy:
            if cmd in ('escape','go back','cancel') and not self.mutating:
                self.view.cancel.set();self.job_id+=1;self.busy=False;return 'Opening canceled. '+self.say_current()
            return 'File manager is working. Please wait.'
        if self.pending:
            action,paths=self.pending
            if cmd in ('cancel','escape','go back','no'):
                self.pending=None;return 'File action canceled. '+self.say_current()
            if action=='delete' and cmd in ('yes','okay','ok','confirm','enter'):
                self.pending=None
                return self.mutate('delete',paths)
            if action=='rename':
                name=re.sub(r'^(?:rename to|name|call it)\s+','',text,flags=re.I)
                valid_name(name);self.pending=None;return self.mutate('rename',paths,name=name)
            if action in ('search','search all'):
                if not text.strip():raise ValueError('Enter part of a name, or press Escape to cancel.')
                self.pending=None
                return self.command(('search subfolders for ' if action=='search all' else 'search files for ')+text)
            return 'Delete the selected items? Say Yes or No. Escape cancels.'
        if cmd in ('file manager menu','alt','menu'):
            self.menu=not self.menu;self.menu_index=0;return ('File manager menu. ' if self.menu else 'Files. ')+self.say_current()
        if self.menu:
            if cmd in ('escape','go back','cancel'):self.menu=False;return self.say_current()
            if cmd in ('enter','okay','ok','open','activate','confirm'):
                command=self.menu_items()[self.menu_index][2];self.menu=False;return self.command(command)
            if cmd not in ('next','previous','up','down','home','end','first','last','file backspace') and not cmd.startswith('file letters '):
                self.menu=False;return self.command(text)
        if cmd in ('next','previous','up','down','home','end','first','last'):
            attr='menu_index' if self.menu else 'index';old=getattr(self,attr);length=len(self.menu_items()) if self.menu else len(self.items)
            new=0 if cmd in ('home','first') else max(0,length-1) if cmd in ('end','last') else max(0,min(max(0,length-1),old+(-1 if cmd in ('previous','up') else 1)))
            setattr(self,attr,new);return ('Start of list. ' if old==new==0 else 'End of list. ' if old==new and length else '')+self.say_current()
        from file_explorer import command as view_command
        result=view_command(self,text)
        if result is not None:return result
        relative=re.fullmatch(r'(next|previous) (item|drive|folder|file)',cmd)
        if relative:
            direction=1 if relative[1]=='next' else -1
            indices=range(self.index+direction,len(self.items),direction) if direction>0 else range(self.index-1,-1,-1)
            target=next((i for i in indices if relative[2]=='item' or relative[2]=='drive' and self.items[i].drive or relative[2]=='folder' and self.items[i].folder and not self.items[i].drive or relative[2]=='file' and not self.items[i].folder),None)
            if target is None:return ('End' if direction>0 else 'Start')+' of list. '+self.say_current()
            self.index=target;return self.say_current()
        if cmd in ('drive list','drives'):self.history.clear();return self.refresh()
        if cmd in ('refresh','refresh folder'):return self.refresh_here()
        if cmd in ('go back','back','escape','previous folder','parent folder'):
            if self.location is None:return '__main__'
            if self.history:
                path,index=self.history.pop()
                return self.refresh(path,preserve=self.location,restore_index=index)
            return self.start_job(lambda:self.backend.parent(self.location),lambda path:self.refresh(path),'Opening parent folder.')
        if cmd in ('select all','mark all','check all'):
            self.targets();self.selected={i.path for i in self.items if not i.drive and i.filesystem};return str(len(self.selected))+' items selected.'
        if cmd in ('clear selection','unselect all','unmark all','uncheck all','deselect all'):
            self.selected.clear();return 'Selection cleared.'
        match=re.fullmatch(r'(select|unselect|deselect|mark|unmark|check|uncheck)(?:\s+(.+))?',text,re.I)
        if cmd=='toggle selection' or match:
            if match and match[2]:self.choose(match[2])
            item=self.current()
            if self.location is None or not item or item.drive or not item.filesystem:raise ValueError('Choose a file or folder inside a drive before selecting.')
            selected=item.path not in self.selected if cmd=='toggle selection' else match[1].lower() in ('select','mark','check')
            if selected:self.selected.add(item.path)
            else:self.selected.discard(item.path)
            return ('' if selected else 'Unselected. ')+self.say_current()
        if cmd in ('copy','copy selected','cut','cut selected'):
            paths=[i.path for i in self.targets()];cut=cmd.startswith('cut')
            return self.start_job(lambda:self.backend.clipboard(paths,cut),lambda _:str(len(paths))+' items '+('cut. Open the destination and paste to move them.' if cut else 'copied.'),'Preparing file clipboard.',mutating=True)
        if cmd in ('paste','paste items'):
            if self.location is None:raise ValueError('Open a folder before pasting.')
            destination=self.location
            def work():
                paths,cut=self.backend.get_clipboard();self.backend.operate('cut' if cut else 'copy',paths,destination)
                if cut and hasattr(self.backend,'finish_cut'):self.backend.finish_cut(paths)
                return len(paths)
            return self.start_job(work,lambda count:self.after_change(str(count)+' items pasted.'),'Pasting files. You can say status.',mutating=True)
        if cmd in ('delete','delete selected','remove','remove selected'):
            targets=self.targets();self.pending=('delete',[i.path for i in targets]);return 'Delete '+', '.join(i.name for i in targets)+('? Windows will use the Recycle Bin where supported. Say Yes or No. Escape cancels.')
        match=re.fullmatch(r'rename(?: (?:to )?(.+))?',text,re.I)
        if match:
            targets=self.targets()
            if len(targets)!=1:raise ValueError('Select one item to rename.')
            if match[1]:return self.mutate('rename',[targets[0].path],name=valid_name(match[1]))
            self.pending=('rename',[targets[0].path]);return 'Rename '+targets[0].name+'. Type or say the new name, including the extension, then press Enter.'
        if cmd in ('status','where am i','current item','read current item'):return location_name(self.location)+'. '+self.say_current()
        if cmd in ('open','enter','okay','ok','activate','confirm','that one'):
            item=self.current()
            if not item:return 'This folder is empty.'
            if item.folder:return self.refresh(item.path,push=True)
            if not item.filesystem:raise ValueError('Windows has not exposed this file for reading.')
            if getattr(self,'pick_extensions',None):
                if Path(item.path).suffix.lower() not in self.pick_extensions:raise ValueError('Choose a supported '+getattr(self,'pick_label','document')+' file.')
                if getattr(self,'pick_label',None)=='document' and Path(item.path).suffix.lower()!='.docx':
                    from document_reader import open_document
                    return self.start_job(lambda:open_document(item.path),self.finish_open,'Opening '+item.name+'. Scanned pages are read with offline OCR.')
                return self.start_job(lambda:Path(item.path),self.finish_pick,'Opening '+item.name+'.')
            from task_navigation import MEDIA_TYPES
            if Path(item.path).suffix.lower() in MEDIA_TYPES:
                return self.start_job(lambda:Path(item.path),self.finish_pick,'Opening '+item.name+'.')
            from document_reader import open_document
            return self.start_job(lambda:open_document(item.path),self.finish_open,'Opening '+item.name+'.')
        name=re.sub(r'^open(?: file| folder| drive)?\s+','',text,flags=re.I);self.choose(name)
        return self.command('open')
    def mutate(self,action,paths,**kwargs):
        return self.start_job(lambda:self.backend.operate(action,paths,**kwargs),lambda _:self.after_change('File action complete.'),'Working on '+str(len(paths))+' items.',mutating=True)
    def after_change(self,message):
        self.selected.clear();self.refresh_here();return message+' Refreshing folder.'
    def finish_open(self,reader):self.opened=reader;return 'Opened '+reader.title+'. Read-only document. '+(' '.join(reader.warnings[:3])+'. ' if reader.warnings else '')+reader.reading.read(True)
    def finish_pick(self,path):self.opened=path;return 'Selected '+path.name+'.'

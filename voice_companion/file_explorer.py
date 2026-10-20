"""File-list view operations shared by voice, keyboard and toolbar controls."""
from dataclasses import replace
from pathlib import Path
import json
import os
import time
import threading

SORTS=('name','date created','date modified','date added')


class FileView:
    def __init__(self,folder=None):
        self.all_items=[];self.filter='';self.prefix='';self.last_key=0
        self.sort='name';self.descending=False;self.searching=False
        self.cancel=threading.Event()
        self.store=Path(folder) if folder else None

    def dated(self,items,location,prune=False):
        if location is None:return items
        observed={};target=None
        if self.store:
            import hashlib
            target=self.store/(hashlib.sha256(str(location).casefold().encode()).hexdigest()+'.json')
            try:observed=json.loads(target.read_text(encoding='utf-8'))
            except (OSError,ValueError):pass
            if not isinstance(observed,dict):observed={}
        now=time.time();result=[];fresh={} if prune else dict(observed)
        for item in items:
            key=item.path.casefold();date=observed.get(key,now)
            if not isinstance(date,(int,float)):date=now
            fresh[key]=date
            result.append(replace(item,added=date))
        if target:
            try:
                target.parent.mkdir(parents=True,exist_ok=True)
                temporary=target.with_suffix('.tmp');temporary.write_text(json.dumps(fresh),encoding='utf-8');temporary.replace(target)
            except OSError:pass
        return result

    def shown(self):
        items=[item for item in self.all_items if self.filter.casefold() in item.name.casefold()]
        if self.sort=='name':
            result=sorted(items,key=lambda item:(item.name.casefold(),item.path.casefold()),reverse=self.descending)
            return sorted(result,key=lambda item:not item.folder)
        attr={'date created':'created','date modified':'modified','date added':'added'}[self.sort]
        known=[item for item in items if getattr(item,attr) is not None]
        unknown=[item for item in items if getattr(item,attr) is None]
        result=sorted(known,key=lambda item:(getattr(item,attr),item.name.casefold()),reverse=self.descending)+sorted(unknown,key=lambda item:item.name.casefold())
        return sorted(result,key=lambda item:not item.folder)

    def letter(self,items,index,value,backspace=False,now=None):
        now=time.monotonic() if now is None else now
        if now-self.last_key>1.2:self.prefix=''
        if backspace:self.prefix=self.prefix[:-1]
        else:self.prefix=(self.prefix+value.casefold()) if self.prefix!=value.casefold() else value.casefold()
        self.last_key=now
        if not self.prefix:return index,'Type the first letters of an item.'
        matches=[i for i,item in enumerate(items) if item.name.casefold().startswith(self.prefix) or item.drive and (item.path[:1].casefold()==self.prefix)]
        if not matches:return index,'No item starts with '+self.prefix+'.'
        target=next((i for i in matches if i>index),matches[0]) if len(self.prefix)==1 else matches[0]
        return target,''

    def recursive(self,root,query,make_item,extensions=None,cancel=None):
        """Bounded name search; never follows junctions, links or file contents."""
        cancel=cancel or self.cancel
        pending=[str(root)];found=[];visited=0;skipped=0;limited=False
        while pending and not cancel.is_set():
            path=pending.pop()
            try:
                with os.scandir(path) as entries:
                    for entry in entries:
                        if cancel.is_set():break
                        visited+=1
                        if visited>100000 or len(found)>=5000:limited=True;pending.clear();break
                        directory=entry.is_dir(follow_symlinks=False)
                        junction=getattr(entry,'is_junction',lambda:getattr(os.path,'isjunction',lambda path:False)(entry.path))()
                        if directory and not entry.is_symlink() and not junction:pending.append(entry.path)
                        if query.casefold() not in entry.name.casefold():continue
                        if extensions and not directory and Path(entry.name).suffix.casefold() not in extensions:continue
                        try:
                            info=entry.stat(follow_symlinks=False)
                            found.append(make_item(entry.name,entry.path,directory,False,True,getattr(info,'st_birthtime',None),info.st_mtime))
                        except OSError:skipped+=1
            except OSError:skipped+=1
        return found,skipped,limited


def command(manager,text):
    import re
    from file_manager import Item
    cmd=text.strip().casefold().rstrip('.!?');view=manager.view
    if text.casefold().startswith('file letters ') or cmd=='file backspace':
        value=text[len('file letters '):] if cmd!='file backspace' else ''
        items=[Item(name,'') for name,_,_ in manager.menu_items()] if manager.menu else view.shown()
        current=manager.current()
        index=manager.menu_index if manager.menu else next((i for i,item in enumerate(items) if current and item.path==current.path),manager.index)
        index,notice=view.letter(items,index,value,cmd=='file backspace')
        if manager.menu:manager.menu_index=index
        elif not notice or not view.prefix:
            chosen=items[index].path if items and 0<=index<len(items) else None
            manager.items=[item for item in items if item.name.casefold().startswith(view.prefix)] if len(view.prefix)>1 else items
            manager.index=next((i for i,item in enumerate(manager.items) if item.path==chosen),0);manager.revision+=1
        return notice or manager.say_current()
    if cmd in ('escape','cancel') and view.prefix:
        path=manager.current().path if manager.current() else None
        view.prefix='';manager.items=view.shown();manager.index=next((i for i,item in enumerate(manager.items) if item.path==path),0);manager.revision+=1
        return 'Name filter cleared. '+manager.say_current()
    if cmd in ('search files','find files','search subfolders'):
        view.prefix='';view.last_key=0
        manager.pending=('search all' if cmd=='search subfolders' else 'search',[])
        return 'Search '+('this folder and subfolders' if cmd=='search subfolders' else 'the current list')+'. Type or say part of a name, then Enter. Escape cancels.'
    match=re.fullmatch(r'(?:search files for|filter files by|find files named|search subfolders for)\s+(.+)',text,re.I)
    if match:
        query=match[1].strip()
        view.prefix='';view.last_key=0
        if text.casefold().startswith('search subfolders for'):
            if manager.location is None:raise ValueError('Open a drive or folder before searching subfolders.')
            # A new event is captured by each worker, so canceled workers cannot
            # be reactivated when a new search starts.
            view.cancel=threading.Event();cancel=view.cancel;view.searching=True
            def done(result):
                items,skipped,limited=result
                view.all_items=view.dated(items,manager.location);view.filter='';manager.items=view.shown();manager.index=0;manager.selected.clear();manager.revision+=1
                return str(len(manager.items))+' search results. '+('Search limit reached. Narrow the search. ' if limited else '')+(str(skipped)+' locations were unavailable. ' if skipped else '')+manager.say_current()
            root=manager.location
            return manager.start_job(lambda:view.recursive(root,query,Item,getattr(manager,'pick_extensions',None),cancel),done,'Searching this folder and subfolders. Escape cancels.')
        view.filter=query;manager.items=view.shown();manager.index=0;manager.selected.intersection_update(i.path for i in manager.items);manager.revision+=1
        return str(len(manager.items))+' matches. '+manager.say_current()
    if cmd in ('clear search','clear filter','show all files'):
        view.filter='';view.prefix=''
        if view.searching:return manager.refresh_here()
        manager.items=view.shown();manager.index=0;manager.revision+=1;return 'All items. '+manager.say_current()
    if cmd in ('sort files','sort','sort menu'):
        manager.menu='sort';manager.menu_index=0;return 'Sort menu. '+manager.say_current()
    match=re.fullmatch(r'(?:sort(?: files)?(?: by)? )?(name|date created|date modified|date added|ascending|descending)(?: (ascending|descending))?',cmd)
    if match:
        current=manager.current().path if manager.current() else None
        if match[1] in ('ascending','descending'):view.descending=match[1]=='descending'
        else:view.sort=match[1]
        if match[2]:view.descending=match[2]=='descending'
        manager.items=view.shown();manager.index=next((i for i,item in enumerate(manager.items) if item.path==current),0);manager.revision+=1
        return 'Sorted by '+view.sort+', '+('descending' if view.descending else 'ascending')+'. '+('Date added means first seen in this folder. ' if view.sort=='date added' else '')+manager.say_current()
    return None

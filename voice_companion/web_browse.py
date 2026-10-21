"""Screen-reader style browse cursor and logical table coordinates."""
QUICK_KEYS={'k':'link','h':'heading','f':'form','b':'button','x':'checkbox','c':'combobox','e':'edit','t':'table','r':'radio','a':'radio','l':'list','i':'listitem','d':'landmark','g':'graphic','m':'frame','p':'paragraph','s':'listbox'}

def voice_browse_key(command):
    """Translate spoken browsing actions to the same actions used by keys."""
    import re
    command=command.lower().strip().rstrip('.!?')
    command=re.sub(r'^(?:go to|move to|go|move) ', '', command)
    names={'link':'k','heading':'h','form control':'f','form field':'f',
           'button':'b','checkbox':'x','check box':'x','combo box':'c','combobox':'c',
           'list box':'s','listbox':'s','dropdown':'c','drop down':'c','edit field':'e','text field':'e','table':'t',
           'radio button':'r','list':'l','list item':'i','landmark':'d','graphic':'g',
           'image':'g','region':'d','frame':'m','iframe':'m','paragraph':'p'}
    match=re.fullmatch(r'(next|previous|prior) (.+)',command)
    if match:
        direction,name=match.groups()
        numbers={'one':'1','two':'2','three':'3','four':'4','five':'5','six':'6'}
        name=re.sub(r'\b(one|two|three|four|five|six)\b',lambda m:numbers[m[0]],name)
        level=re.fullmatch(r'heading (?:level )?([1-6])',name)
        key=level[1] if level else names.get(name)
        if key:return 'Quick:'+('Previous:' if direction!='next' else '')+key
    tables={'next row':'Down','previous row':'Up','prior row':'Up',
            'next column':'Right','previous column':'Left','prior column':'Left',
            'first column':'Home','last column':'End','first row':'Prior','last row':'Next',
            'cell above':'Up','cell below':'Down','cell left':'Left','cell right':'Right'}
    table_command=command.removeprefix('table ')
    if table_command in tables:return 'Table:'+tables[table_command]
    return {'activate current element':'Activate','activate element':'Activate',
            'activate current link':'Activate','activate current button':'Activate',
            'edit current field':'Activate','enter forms mode':'Activate',
            'toggle checkbox':'Space','select radio button':'Space',
            'next option':'Down','previous option':'Up','first option':'Home','last option':'End',
            'next control':'Tab','previous control':'ShiftTab',
            'cancel field editing':'Escape','cancel forms mode':'Escape',
            'save field':'Done','finish field editing':'Done'}.get(command)
def kind(item):
    role=item.get('role','');tag=item.get('tag','');type=item.get('type','')
    if role=='button':return role
    if role in ('slider','spinbutton','progressbar','meter','scrollbar','switch','tab','menuitem','menuitemcheckbox','menuitemradio','treeitem','option'):return role
    if type in ('checkbox','radio'):return type
    if role=='listbox' or tag=='select' and (item.get('multiple') or item.get('size',0)>1):return 'listbox'
    if tag=='select' or role=='combobox':return 'combobox'
    if tag in ('input','textarea') or role=='textbox' or item.get('editable'):return 'edit'
    return role

def describe(item):
    name=item.get('label') or 'Unlabeled';role=kind(item)
    if role=='landmark':return (name+', ' if name not in ('Unnamed','Unlabeled') else '')+{'contentinfo':'content information'}.get(item.get('landmark','region'),item.get('landmark','region'))+' region.'
    if role=='list':return (name+', ' if name not in ('List','Unlabeled') else '')+('Ordered list' if item.get('ordered') else 'List')+', '+str(item.get('item_count',0))+' items.'
    if role=='listitem':return name+', list item'+(' '+str(item['position'])+' of '+str(item['set_size']) if item.get('position') and item.get('set_size',0)>0 else '')+'.'
    if role=='heading':role+=' level '+str(item.get('level',1))
    state=''
    if role in ('checkbox','radio'):state=', '+('checked' if item.get('checked') else 'not checked')
    if role in ('switch','menuitemcheckbox','menuitemradio'):state=', '+('on' if item.get('checked') else 'off')
    if role in ('slider','spinbutton','progressbar','meter','scrollbar'):
        state=', '+str(item.get('value_text') or item.get('value_now') or 'value not provided')
    if item.get('expanded') is not None:state+=', '+('expanded' if item['expanded'] else 'collapsed')
    if item.get('selected') is not None:state+=', '+('selected' if item['selected'] else 'not selected')
    if item.get('pressed') is not None:state+=', '+('pressed' if item['pressed'] else 'not pressed')
    if role in ('combobox','listbox'):state=', '+(item.get('selected_label') or 'no selection')
    if item.get('disabled'):state+=', unavailable'
    if item.get('readonly'):state+=', read-only'
    if item.get('required'):state+=', required'
    return ('Link '+name+state+'.') if role=='link' else name+', '+role+state+'.'

def focus_order(items,elements=None):
    """Keep frame contents inside their owner while honoring HTML tabindex."""
    owners={e['child_frame_index']:e for e in (elements or items) if 'child_frame_index' in e}
    def rank(item):
        tab=item.get('tabindex',0)
        return (0 if tab>0 else 2 if tab<0 else 1,tab if tab>0 else 0,item.get('start',0))
    def scope_path(scope,seen=()):
        owner=owners.get(scope)
        if owner is None or scope in seen:return ()
        return scope_path(owner.get('frame_index'),seen+(scope,))+(rank(owner),)
    return sorted(items,key=lambda e:scope_path(e.get('frame_index'))+(rank(e),))

class BrowseCursor:
    def __init__(self,snapshot):
        self.elements=snapshot.get('elements',snapshot.get('controls',[]));self.index=-1;self.position=0;self.table=None;self.row=self.column=0
    def set_position(self,position):
        # GUI caret acknowledgments must retain the semantic heading/link selected
        # by a quick key, even when several nested elements start at this offset.
        current=self.current()
        if current and position==self.position==current.get('start',0):return
        self.position=position
        matches=[(i,e) for i,e in enumerate(self.elements) if e.get('start',0)<=position<e.get('end',0)]
        if matches:self.index=min(matches,key=lambda x:x[1].get('end',0)-x[1].get('start',0))[0]
        else:self.index=max((i for i,e in enumerate(self.elements) if e.get('start',0)<=position),default=-1)
    def current(self):return self.elements[self.index] if 0<=self.index<len(self.elements) else None
    def move(self,key,backward=False,tab=False):
        wanted='tab' if tab else QUICK_KEYS.get(key,key)
        level=int(key) if key in '123456' and len(key)==1 else None
        def match(e):
            role=kind(e)
            if level:return role=='heading' and e.get('level')==level
            if wanted=='button' and role in ('menuitem','menuitemradio','menuitemcheckbox'):return True
            if wanted=='form':return role in ('edit','checkbox','radio','combobox','listbox','button','menuitem','menuitemradio','menuitemcheckbox')
            if wanted=='tab':return role in ('link','edit','checkbox','radio','combobox','listbox','button','menuitem','menuitemradio','menuitemcheckbox') and not e.get('disabled') and e.get('tabindex',0)>=0
            return role==wanted
        if tab:
            ordered=focus_order([e for e in self.elements if match(e)],self.elements)
            current=self.current()
            if current in ordered:
                offset=ordered.index(current)+(-1 if backward else 1)
                target=ordered[offset] if 0<=offset<len(ordered) else None
            else:
                candidates=[e for e in ordered if e.get('start',0)<self.position] if backward else [e for e in ordered if self.index<0 or e.get('start',0)>self.position]
                target=(candidates[-1] if backward else candidates[0]) if candidates else None
            index=next((i for i,e in enumerate(self.elements) if e is target),None)
        else:
            candidates=range(self.index-1,-1,-1) if backward else range(self.index+1,len(self.elements))
            index=next((i for i in candidates if match(self.elements[i])),None)
        if index is None:return ('Start of list. ' if backward else 'End of list. ')+'No '+('previous ' if backward else 'next ')+('heading level '+str(level) if level else wanted)+'.'
        self.index=index;item=self.current();self.position=item.get('start',0)
        if kind(item)=='table':self.table=item['key'];self.row=self.column=0
        elif kind(item)=='cell':self.table=item.get('table');self.row=item.get('row',0);self.column=item.get('column',0)
        return describe(item)
    def cells(self):return [e for e in self.elements if kind(e)=='cell' and e.get('table')==self.table]
    def cell_at(self,row,column):
        return next((e for e in self.cells() if e['row']<=row<e['row']+e.get('rowspan',1) and e['column']<=column<e['column']+e.get('colspan',1)),None)
    def table_move(self,key):
        current=self.current()
        if current and kind(current)=='cell' and (self.table!=current['table'] or self.cell_at(self.row,self.column) is not current):self.table=current['table'];self.row=current['row'];self.column=current['column']
        if current and kind(current)=='table':self.table=current['key']
        cells=self.cells()
        if not cells:return 'Move to a table with T first.'
        row,column=self.row,self.column
        maxrow=max(e['row']+e.get('rowspan',1)-1 for e in cells);maxcol=max(e['column']+e.get('colspan',1)-1 for e in cells)
        cell=self.cell_at(row,column)
        if key=='Right':column=(cell['column']+cell.get('colspan',1)) if cell else column+1
        elif key=='Left':column=(cell['column'] if cell else column)-1
        elif key=='Down':row=(cell['row']+cell.get('rowspan',1)) if cell else row+1
        elif key=='Up':row=(cell['row'] if cell else row)-1
        elif key=='Home':column=0
        elif key=='End':column=maxcol
        elif key=='Prior':row=0
        elif key=='Next':row=maxrow
        if not 0<=row<=maxrow or not 0<=column<=maxcol:return 'Start of table.' if row<0 or column<0 else 'End of table.'
        target=self.cell_at(row,column)
        if target is None:return 'No cell at row '+str(row+1)+', column '+str(column+1)+'.'
        self.row,self.column=row,column;self.index=self.elements.index(target);self.position=target.get('start',0)
        headers=[]
        for e in cells:
            if e.get('header') and e is not target and ((e.get('scope')=='row' and e['row']==row) or (e.get('scope') in ('col','') and e['row']<row and e['column']<=column<e['column']+e.get('colspan',1))):headers.append(e['label'])
        return ('. '.join(dict.fromkeys(headers))+'. ' if headers else '')+(target.get('label') or 'Blank')+'. Row '+str(row+1)+', column '+str(column+1)+'.'

# Runs in the page's DOM; scripts, hidden subtrees and closed roots are not read.
SEMANTICS = r'''
          const elements=[],anchors=[];let output='';let serial=1+all(document).reduce((maximum,e)=>Math.max(maximum,/^n\d+$/.test(e.getAttribute('data-voice-companion-key')||'')?Number(e.getAttribute('data-voice-companion-key').slice(1)):0),0);
          const block=new Set(['P','DIV','SECTION','ARTICLE','HEADER','FOOTER','MAIN','NAV','ASIDE','H1','H2','H3','H4','H5','H6','LI','UL','OL','MENU','TR','TABLE']);
          const newline=()=>{output=output.replace(/ +$/,'');if(output && !output.endsWith('\n'))output+='\n';};
          const append=s=>{s=String(s||'').replace(/\s+/g,' ');if(!output || /[ \n]$/.test(output))s=s.trimStart();output+=s;};
          const keyOwners=new Map();const key=e=>{const existing=e.getAttribute('data-voice-companion-key');if(!/^n\d+$/.test(existing||'')||keyOwners.has(existing)&&keyOwners.get(existing)!==e)e.setAttribute('data-voice-companion-key','n'+serial++);const value=e.getAttribute('data-voice-companion-key');keyOwners.set(value,e);return value;};
          const searchHost=['search.brave.com','www.google.com','google.com','www.bing.com','bing.com','html.duckduckgo.com','duckduckgo.com','www.duckduckgo.com'].includes(location.hostname);
          const titleSelector='.search-snippet-title,.snippet-title,.snippet-title-text,[data-testid="result-title"],.title';
          const braveResult=e=>location.hostname==='search.brave.com'&&e.matches(titleSelector)&&e.closest('a[href]');
          const controlsByKey=new Map(controls.map(c=>[c.key,c]));const tableMaps=new Map();
          for(const table of all(document).filter(e=>e.matches('table,[role="table"],[role="grid"]'))){
            const grid=[];const rows=all(table).filter(e=>e.matches('tr,[role="row"]') && visible(e) && !e.closest('[aria-hidden="true"],[inert]') && e.closest('table,[role="table"],[role="grid"]')===table);
            rows.forEach((row,r)=>{grid[r]??=[];let c=0;const cells=[...row.querySelectorAll('th,td,[role="cell"],[role="gridcell"],[role="columnheader"],[role="rowheader"]')].filter(e=>visible(e) && !e.closest('[aria-hidden="true"],[inert]') && e.closest('tr,[role="row"]')===row && e.closest('table,[role="table"],[role="grid"]')===table);
              cells.forEach(cell=>{while(grid[r][c])c++;const rs=Math.min(100,Math.max(1,parseInt(cell.getAttribute('rowspan')||cell.getAttribute('aria-rowspan')||1)||1));const cs=Math.min(100,Math.max(1,parseInt(cell.getAttribute('colspan')||cell.getAttribute('aria-colspan')||1)||1));tableMaps.set(cell,{table:key(table),row:r,column:c,rowspan:rs,colspan:cs,header:cell.tagName==='TH'||/header$/.test(cell.getAttribute('role')||''),scope:cell.getAttribute('scope')||(cell.getAttribute('role')==='rowheader'?'row':'')});for(let a=r;a<r+rs;a++){grid[a]??=[];for(let b=c;b<c+cs;b++)grid[a][b]=true;}c+=cs;});});
          }
          const urlOnly=s=>/^(?:https?:\/\/|www\.)\S+(?:\s*[›>]\s*\S+)*$|^[\w-]+(?:\.[\w-]+)+(?:[\/?]\S*)?(?:\s*[›>]\s*\S+)*$/i.test(String(s||'').trim());
          const contentTitle=e=>{
            const titles=[...e.querySelectorAll(titleSelector+',h1,h2,h3,h4,h5,h6,[role="heading"]')];
            const lines=[...titles.flatMap(t=>String(t.innerText||'').split(/\n+/)),...String(e.innerText||'').split(/\n+/)].map(s=>String(s||'').trim()).filter(s=>s&&!urlOnly(s));
            return lines[0]||'';
          };
          const ownedItems=new WeakMap(),ownerOf=new WeakMap();
          for(const owner of all(document).filter(n=>n.matches('[role~="list"]'))){
            const root=owner.getRootNode(),items=(owner.getAttribute('aria-owns')||'').split(/\s+/).filter(Boolean).map(id=>root.querySelector('#'+CSS.escape(id))).filter(n=>n&&n!==owner);
            ownedItems.set(owner,items);for(const item of items)ownerOf.set(item,owner);
          }
          const ownList=e=>ownerOf.get(e)||(e.assignedSlot||e.parentElement||e.getRootNode()?.host)?.closest('ul,ol,menu,[role~="list"]');
          const itemsFor=list=>[...new Set([...all(list.shadowRoot||list),...(ownedItems.get(list)||[])])].filter(n=>accessibleLabel.role(n)==='listitem'&&visible(n)&&ownList(n)===list);
          const controlRoles=['button','textbox','searchbox','checkbox','radio','combobox','listbox','switch','slider','spinbutton'];
          const walk=e=>{
            if(e.nodeType===3){append(e.textContent);return;}
            if(e.nodeType!==1 || !visible(e,true) || e.closest('[aria-hidden="true"],[inert]') || ['SCRIPT','STYLE','NOSCRIPT','HEAD'].includes(e.tagName))return;
            if(!visible(e)){for(const child of (e.shadowRoot?e.shadowRoot.children:e.children))walk(child);return;}
            if(block.has(e.tagName))newline();if(e.tagName==='BR'){newline();return;}
            let role=accessibleLabel.role(e)||'';const tag=e.tagName.toLowerCase(),type=(e.type||'').toLowerCase(),editable=e.isContentEditable&&!e.parentElement?.isContentEditable;
            const explicit=e.getAttribute('role')||'', presentation=['none','presentation'].includes(role);
            if(presentation)role='';
            if(!presentation){
              if(tableMaps.has(e))role='cell';else if(role==='grid')role='table';
              else if(['img','graphics-document','graphics-symbol','graphics-object'].includes(role)||tag==='svg'&&(e.hasAttribute('aria-label')||e.hasAttribute('aria-labelledby')||e.querySelector('title')))role='graphic';
              else if(['input','textarea','select'].includes(tag)||editable)role=role||'field';
              else if(tag==='p'&&!role)role='paragraph';
              else if(['iframe','frame'].includes(tag))role='frame';
            }
            const landmarkRoles=['main','navigation','complementary','banner','contentinfo','search','region','form'];
            let landmark='';
            if(landmarkRoles.includes(role)){
              const named=!!accessibleLabel.nativeName(e);
              const nestedHeader=['header','footer'].includes(tag)&&!explicit&&e.parentElement?.closest('article,aside,main,nav,section,[role="article"]');
              if(nestedHeader||(['section','form'].includes(tag)&&!explicit&&!named))role='';
              else{landmark=role;role='landmark';}
            }
            if(role==='generic'||role==='none'||role==='presentation')role='';
            if(tag==='li'&&!explicit&&['none','presentation'].includes(accessibleLabel.role(e.closest('ul,ol,menu')||e)))role='';
            if(braveResult(e))role='heading';
            if(tag==='label'){for(const child of all(e).filter(c=>c.matches(selector)))walk(child);return;}
            if(['input','select','textarea','a','button'].includes(tag)||controlRoles.includes(role)||editable)newline();
            const control=controlsByKey.get(e.getAttribute('data-voice-companion-key'));
            const resultHeading=e.querySelector('h1,h2,h3,h4,h5,h6,[role="heading"]')||e.closest('h1,h2,h3,h4,h5,h6,[role="heading"]');
            const braveTitle=location.hostname==='search.brave.com'?e.querySelector(titleSelector):null;
            const resultTitle=role==='link'?(braveTitle||e.querySelector('[class*="title"],[class*="heading"]')||resultHeading?.querySelector('[class*="title"],[class*="heading"]')||resultHeading):null;
            let label=role ? role==='link'&&resultTitle && !e.getAttribute('aria-label') && !e.getAttribute('aria-labelledby')?accessibleLabel(resultTitle):accessibleLabel(e) : '';
            if(role==='heading'&&(!e.hasAttribute('aria-label')&&!e.hasAttribute('aria-labelledby')||urlOnly(label))){label=contentTitle(e)||contentTitle(e.closest('a')||e)||label;}
            if(role==='link'&&(resultHeading||braveTitle)&&!e.hasAttribute('aria-label')&&!e.hasAttribute('aria-labelledby')){label=contentTitle(resultTitle||e)||contentTitle(e)||label;}
            const listItems=role==='list'?itemsFor(e):[];
            const parentList=role==='listitem'?ownList(e):null;
            const siblings=parentList?itemsFor(parentList):[];
            if(role==='listitem'){const pieces=[];const own=n=>{if(n.nodeType===3){pieces.push(n.textContent);return;}if(n.nodeType!==1||n!==e&&n.matches('ul,ol,menu,[role~="list"]')||!visible(n)||n.closest('[aria-hidden="true"],[inert]'))return;for(const c of (n.shadowRoot?n.shadowRoot.childNodes:n.tagName==='SLOT'&&n.assignedNodes({flatten:true}).length?n.assignedNodes({flatten:true}):n.childNodes))own(c);};own(e);label=pieces.join(' ').trim().replace(/\s+/g,' ')||'Empty list item';}
            if(role==='list'&&!label)label='List';
            if(role==='landmark'&&!label)label='Unnamed';
            if(['list','listitem','frame','landmark'].includes(role))newline();
            const bookmark=e.id?{id:e.id,key:key(e),start:output.length,end:output.length}:null;if(bookmark)anchors.push(bookmark);
            let item=null;if(role && elements.length<5000){item={...(control||{}),key:key(e),role,tag,type,label,identity_label:control?.label||label,id:e.id||'',search_result:searchHost&&role==='link'&&!!(braveTitle||resultHeading),activation_key:role==='heading'?key(e.closest('a[href]')||e.querySelector('a[href]')||e):null,href:tag==='a'?e.href:(control?.href||''),level:braveResult(e)?2:parseInt(e.getAttribute('aria-level')||tag.slice(1))||1,start:output.length,end:output.length,disabled:!!e.disabled||e.getAttribute('aria-disabled')==='true',editable:!!editable,...(tableMaps.get(e)||{})};elements.push(item);}
            if(item){item.landmark=landmark;if(role==='list'){item.item_count=Math.max(listItems.length,...listItems.map(n=>Number(n.getAttribute('aria-setsize'))||0));item.ordered=tag==='ol';}if(role==='listitem'){item.position=Number(e.getAttribute('aria-posinset'))||siblings.indexOf(e)+1;item.set_size=Number(e.getAttribute('aria-setsize'))||siblings.length;}}
            if(item){item.tabindex=e.tabIndex;item.checked=accessibleLabel.checked(e);item.value_text=e.getAttribute('aria-valuetext')||'';item.value_now=e.getAttribute('aria-valuenow')??(['range','number'].includes(type)?e.value:null);for(const attr of ['expanded','selected','pressed'])if(e.hasAttribute('aria-'+attr))item[attr]=e.getAttribute('aria-'+attr)==='true';}
            if(controlRoles.includes(role)||['input','select','textarea'].includes(tag)||editable){
              const spokenRole=type==='password'?'password field':({button:'button',textbox:'edit field',searchbox:'search field',checkbox:'checkbox',radio:'radio button',combobox:'combo box',listbox:'list box',switch:'switch',slider:'slider',spinbutton:'spin button'}[role]||'edit field');
              append(label+', '+spokenRole);
              if(['checkbox','radio','switch'].includes(role))append(accessibleLabel.checked(e)?', checked':', not checked');
              if(e.disabled||e.getAttribute('aria-disabled')==='true')append(', unavailable');
              if(e.readOnly||e.getAttribute('aria-readonly')==='true')append(', read-only');
              if(e.required||e.getAttribute('aria-required')==='true')append(', required');
              if(type!=='password'&&role!=='button'&&e.value&&!['checkbox','radio','image','submit','button'].includes(type))append(', '+(tag==='select'?e.selectedOptions[0]?.text:e.value));
              if(editable)append(', '+(e.innerText||''));
            }
            else if(role==='graphic'||role==='frame')append((role==='frame'?'Frame ':'')+label);
            else if(tag==='img'){}
            else for(const child of (e.shadowRoot?e.shadowRoot.childNodes:e.tagName==='SLOT'&&e.assignedNodes({flatten:true}).length?e.assignedNodes({flatten:true}):e.childNodes))walk(child);
            if(item){if(output.length===item.start && label)append(label);item.end=output.length;if(!item.label)item.label=output.slice(item.start,item.end).trim().slice(0,180);if(['heading','link'].includes(item.role)&&item.label&&!urlOnly(item.label)){const range=output.slice(item.start,item.end);const offset=range.indexOf(item.label);if(offset>=0){item.start+=offset;if(item.role==='heading')item.end=item.start+item.label.length;}}}
            if(bookmark)bookmark.end=output.length;
            if(['input','select','textarea','a','button'].includes(tag)||controlRoles.includes(role)||editable)newline();if(['TD','TH'].includes(e.tagName))append(' ');if(block.has(e.tagName)||['list','listitem','frame','landmark'].includes(role))newline();
          };
          walk(readingRoot);
          // A result may put its URL in an H tag and its human title in a
          // sibling div. The heading's label and caret must target that title.
          for(const item of elements){if(item.role!=='heading'||!item.label||urlOnly(item.label))continue;
            if(output.slice(item.start,item.end).includes(item.label))continue;
            const link=elements.find(e=>e.role==='link'&&e.key===item.activation_key);
            if(link){const offset=output.slice(link.start,link.end).indexOf(item.label);if(offset>=0){item.start=link.start+offset;item.end=item.start+item.label.length;}}
          }
          // URL/breadcrumb-only headings are metadata, not useful heading destinations.
          for(let i=elements.length-1;i>=0;i--)if(elements[i].role==='heading'&&urlOnly(elements[i].label))elements.splice(i,1);
          if(searchHost){const seen=new Set();for(let i=0;i<elements.length;i++){const item=elements[i];if(item.role!=='heading')continue;const link=elements.find(e=>e.key===item.activation_key&&e.role==='link');if(!link)continue;const identity=link.href+'|'+item.label.toLocaleLowerCase().replace(/\s+/g,' ').trim();if(seen.has(identity)){elements.splice(i,1);i--;}else seen.add(identity);}}
          if(searchHost){const seenLinks=new Set(),removed=new Set(),resultUrls=new Set(elements.filter(e=>e.search_result).map(e=>e.href));for(let i=0;i<elements.length;i++){const item=elements[i];if(item.role!=='link')continue;const identity=item.href+'|'+item.label.toLocaleLowerCase().replace(/\s+/g,' ').trim();if(item.search_result&&seenLinks.has(identity)||!item.search_result&&urlOnly(item.label)&&resultUrls.has(item.href)){removed.add(item.key);elements.splice(i,1);i--;}else if(item.search_result)seenLinks.add(identity);}for(let i=controls.length-1;i>=0;i--)if(removed.has(controls[i].key))controls.splice(i,1);}
          for(const item of elements){const control=controlsByKey.get(item.key);if(control&&item.role==='link'){control.identity_label=control.label;control.label=item.label;}}
          // JavaScript offsets count UTF-16 units; Companion reading uses code points.
          const boundaries=new Set([...elements,...anchors].flatMap(e=>[e.start,e.end]));const offsets=new Map();let units=0,points=0;
          for(const char of output){if(boundaries.has(units))offsets.set(units,points);units+=char.length;points++;}
          offsets.set(units,points);for(const item of [...elements,...anchors]){item.start=offsets.get(item.start);item.end=offsets.get(item.end);}
'''

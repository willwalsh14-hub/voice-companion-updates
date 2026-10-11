"""Screen-reader style browse cursor and logical table coordinates."""
QUICK_KEYS={'k':'link','h':'heading','f':'form','b':'button','x':'checkbox','c':'combobox','e':'edit','t':'table','r':'radio','a':'radio','l':'list','i':'listitem','d':'landmark','g':'graphic','p':'paragraph'}

def voice_browse_key(command):
    """Translate spoken browsing actions to the same actions used by keys."""
    import re
    command=command.lower().strip().rstrip('.!?')
    command=re.sub(r'^(?:go to|move to|go|move) ', '', command)
    names={'link':'k','heading':'h','form control':'f','form field':'f','control':'f',
           'button':'b','checkbox':'x','check box':'x','combo box':'c','combobox':'c',
           'dropdown':'c','drop down':'c','edit field':'e','text field':'e','table':'t',
           'radio button':'r','list':'l','list item':'i','landmark':'d','graphic':'g',
           'image':'g','paragraph':'p'}
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
            'next option':'Down','previous option':'Up',
            'next control':'Tab','previous control':'ShiftTab',
            'cancel field editing':'Escape','cancel forms mode':'Escape',
            'save field':'Done','finish field editing':'Done'}.get(command)
def kind(item):
    role=item.get('role','');tag=item.get('tag','');type=item.get('type','')
    if role=='button':return role
    if type in ('checkbox','radio'):return type
    if tag=='select' or role=='combobox':return 'combobox'
    if tag in ('input','textarea') or role=='textbox' or item.get('editable'):return 'edit'
    return role

def describe(item):
    name=item.get('label') or 'Unlabelled';role=kind(item)
    if role=='heading':role+=' level '+str(item.get('level',1))
    state=''
    if role in ('checkbox','radio'):state=', '+('checked' if item.get('checked') else 'not checked')
    if role=='combobox':state=', '+(item.get('selected_label') or 'no selection')
    if item.get('disabled'):state+=', unavailable'
    if item.get('required'):state+=', required'
    return name+', '+role+state+'.'

class BrowseCursor:
    def __init__(self,snapshot):
        self.elements=snapshot.get('elements',snapshot.get('controls',[]));self.index=-1;self.position=0;self.table=None;self.row=self.column=0
    def set_position(self,position):
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
            if wanted=='form':return role in ('edit','checkbox','radio','combobox','button')
            if wanted=='tab':return role in ('link','edit','checkbox','radio','combobox','button') and not e.get('disabled')
            return role==wanted
        candidates=range(self.index-1,-1,-1) if backward else range(self.index+1,len(self.elements))
        index=next((i for i in candidates if match(self.elements[i])),None)
        if index is None:return 'No '+('previous ' if backward else 'next ')+('heading level '+str(level) if level else wanted)+'.'
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
        elif key=='Left':column-=1
        elif key=='Down':row=(cell['row']+cell.get('rowspan',1)) if cell else row+1
        elif key=='Up':row-=1
        elif key=='Home':column=0
        elif key=='End':column=maxcol
        elif key=='Prior':row=0
        elif key=='Next':row=maxrow
        if not 0<=row<=maxrow or not 0<=column<=maxcol:return 'Table boundary.'
        target=self.cell_at(row,column)
        if target is None:return 'No cell at row '+str(row+1)+', column '+str(column+1)+'.'
        self.row,self.column=row,column;self.index=self.elements.index(target);self.position=target.get('start',0)
        headers=[]
        for e in cells:
            if e.get('header') and e is not target and ((e.get('scope')=='row' and e['row']==row) or (e.get('scope') in ('col','') and e['row']<row and e['column']<=column<e['column']+e.get('colspan',1))):headers.append(e['label'])
        return ('. '.join(dict.fromkeys(headers))+'. ' if headers else '')+(target.get('label') or 'Blank')+'. Row '+str(row+1)+', column '+str(column+1)+'.'

# Runs in the page's DOM; scripts, hidden subtrees and closed roots are not read.
SEMANTICS = r'''
          const elements=[];let output='';let serial=1+Math.max(0,...all(document).map(e=>/^n\d+$/.test(e.getAttribute('data-voice-companion-key')||'')?Number(e.getAttribute('data-voice-companion-key').slice(1)):0));
          const block=new Set(['P','DIV','SECTION','ARTICLE','HEADER','FOOTER','MAIN','NAV','ASIDE','H1','H2','H3','H4','H5','H6','LI','UL','OL','TR','TABLE']);
          const newline=()=>{output=output.replace(/ +$/,'');if(output && !output.endsWith('\n'))output+='\n';};
          const append=s=>{s=String(s||'').replace(/\s+/g,' ');if(!output || /[ \n]$/.test(output))s=s.trimStart();output+=s;};
          const key=e=>{if(!e.hasAttribute('data-voice-companion-key'))e.setAttribute('data-voice-companion-key','n'+serial++);return e.getAttribute('data-voice-companion-key');};
          const tableMaps=new Map();
          for(const table of all(document).filter(e=>e.matches('table,[role="table"],[role="grid"]'))){
            const grid=[];const rows=all(table).filter(e=>e.matches('tr,[role="row"]') && visible(e) && !e.closest('[aria-hidden="true"],[inert]') && e.closest('table,[role="table"],[role="grid"]')===table);
            rows.forEach((row,r)=>{grid[r]??=[];let c=0;const cells=[...row.querySelectorAll('th,td,[role="cell"],[role="gridcell"],[role="columnheader"],[role="rowheader"]')].filter(e=>visible(e) && !e.closest('[aria-hidden="true"],[inert]') && e.closest('tr,[role="row"]')===row && e.closest('table,[role="table"],[role="grid"]')===table);
              cells.forEach(cell=>{while(grid[r][c])c++;const rs=Math.min(100,Math.max(1,parseInt(cell.getAttribute('rowspan')||cell.getAttribute('aria-rowspan')||1)||1));const cs=Math.min(100,Math.max(1,parseInt(cell.getAttribute('colspan')||cell.getAttribute('aria-colspan')||1)||1));tableMaps.set(cell,{table:key(table),row:r,column:c,rowspan:rs,colspan:cs,header:cell.tagName==='TH'||/header$/.test(cell.getAttribute('role')||''),scope:cell.getAttribute('scope')||(cell.getAttribute('role')==='rowheader'?'row':'')});for(let a=r;a<r+rs;a++){grid[a]??=[];for(let b=c;b<c+cs;b++)grid[a][b]=true;}c+=cs;});});
          }
          const walk=e=>{
            if(e.nodeType===3){append(e.textContent);return;}
            if(e.nodeType!==1 || !visible(e) || e.closest('[aria-hidden="true"],[inert]') || ['SCRIPT','STYLE','NOSCRIPT','HEAD'].includes(e.tagName))return;
            if(block.has(e.tagName))newline();if(e.tagName==='BR'){newline();return;}
            let role=e.getAttribute('role')||'';const tag=e.tagName.toLowerCase(),type=(e.type||'').toLowerCase(),editable=e.isContentEditable&&!e.parentElement?.isContentEditable;
            if(/^h[1-6]$/.test(tag))role='heading';else if(tag==='a' && e.href)role='link';else if(tag==='button'||tag==='input'&&['submit','reset','button','image'].includes(type))role='button';else if(['input','textarea','select'].includes(tag)||editable)role='field';else if(tag==='table'||role==='grid')role='table';else if(tableMaps.has(e))role='cell';else if(['ul','ol'].includes(tag))role='list';else if(tag==='li')role='listitem';else if(tag==='p')role='paragraph';else if(['main','nav','aside','header','footer'].includes(tag))role='landmark';else if(tag==='img')role='graphic';
            if(tag==='label'){for(const child of all(e).filter(c=>c.matches(selector)))walk(child);return;}
            if(['input','select','textarea'].includes(tag))newline();
            const control=controls.find(c=>c.key===e.getAttribute('data-voice-companion-key'));
            const label=accessibleLabel(e);
            let item=null;if(role && elements.length<5000){item={...(control||{}),key:key(e),role,tag,type,label,level:parseInt(e.getAttribute('aria-level')||tag.slice(1))||1,start:output.length,end:output.length,disabled:!!e.disabled||e.getAttribute('aria-disabled')==='true',editable:!!editable,...(tableMaps.get(e)||{})};elements.push(item);}
            if(['input','select','textarea'].includes(tag)){append(label);if(type!=='password' && e.value && !['checkbox','radio','submit','button'].includes(type))append(' '+(tag==='select'?e.selectedOptions[0]?.text:e.value));}
            else if(tag==='img')append(label);
            else for(const child of (e.shadowRoot?e.shadowRoot.childNodes:e.childNodes))walk(child);
            if(item){if(output.length===item.start && label)append(label);item.end=output.length;if(!item.label)item.label=output.slice(item.start,item.end).trim().slice(0,180);}
            if(['input','select','textarea'].includes(tag))newline();if(['TD','TH'].includes(e.tagName))append(' ');if(block.has(e.tagName))newline();
          };
          walk(document.body);
'''

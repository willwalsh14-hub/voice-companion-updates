"""Passive document structure. Text offsets are shared by speech and the reader UI."""
from dataclasses import dataclass, field
from html import escape
from html.parser import HTMLParser
from urllib.parse import urlsplit
import re
import json
import xml.etree.ElementTree as ET


@dataclass
class Structure:
    start: int
    end: int
    kind: str
    label: str = ''
    attributes: dict = field(default_factory=dict)


class RichText(HTMLParser):
    blocks = {'p','div','section','article','blockquote','pre','h1','h2','h3','h4','h5','h6','table','tr','ul','ol','li'}
    styles = {'b':'bold','strong':'bold','i':'italic','em':'italic','u':'underline','s':'strike','del':'strike','sup':'superscript','sub':'subscript','mark':'highlight','code':'code'}

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.text='';self.spans=[];self.structure=[];self.active=[];self.hidden=[];self.lists=[]

    def newline(self):
        if self.text and not self.text.endswith('\n'):self.text+='\n'

    def handle_starttag(self, tag, attrs):
        attrs=dict(attrs)
        if tag in ('script','style','head','iframe','object','template') or self.hidden:
            if tag not in ('img','br','meta','link','input','hr'):self.hidden.append(tag)
            return
        if 'hidden' in attrs or attrs.get('aria-hidden')=='true':self.hidden.append(tag);return
        if tag in self.blocks:self.newline()
        if tag in ('td','th') and self.text and not self.text.endswith('\n'):self.text+='\t'
        if tag in ('ul','ol'):self.lists.append([tag,int(attrs.get('start','1'))-1 if attrs.get('start','1').lstrip('-').isdigit() else 0])
        if tag=='li':
            if self.lists:self.lists[-1][1]+=1
            depth=int(attrs.get('data-level','0')) if attrs.get('data-level','0').isdigit() else 0
            self.text+=('  '*max(depth,len(self.lists)-1))+((attrs.get('data-label') or str(self.lists[-1][1])+'.')+' ' if self.lists and self.lists[-1][0]=='ol' else '• ')
        if tag=='br':self.text+='\n'
        if tag=='img':self.text+=attrs.get('alt','')
        start=len(self.text)
        bookmark=attrs.get('id') or (attrs.get('name') if tag=='a' else None)
        if bookmark:self.structure.append(Structure(start,start,'bookmark',bookmark))
        kinds={'table':'table','tr':'row','td':'cell','th':'cell','li':'list item','ul':'list','ol':'list'}
        kind='heading' if re.fullmatch('h[1-6]',tag) else 'footnote' if attrs.get('role') in ('doc-footnote','doc-endnote') or attrs.get('epub:type') in ('footnote','endnote') else kinds.get(tag)
        style='heading' if kind=='heading' else self.styles.get(tag)
        url=attrs.get('href','')
        try:scheme=urlsplit(url).scheme.lower()
        except ValueError:scheme=''
        if tag=='a' and (url.startswith('#') or scheme in ('http','https','mailto')):style='link';kind='link'
        extra=dict(attrs)
        if attrs.get('data-font') or attrs.get('data-size'):
            kind='format'
            try:size=float(attrs.get('data-size','12'))
            except ValueError:size=12
            style='docfont:'+json.dumps([attrs.get('data-font','Segoe UI'),max(6,min(72,size)),attrs.get('data-weight','normal'),attrs.get('data-slant','roman')])
        if kind=='heading':extra['level']=int(tag[1])
        if kind in ('list','list item'):extra['level']=int(attrs.get('data-level','0'))+1 if attrs.get('data-level','0').isdigit() and 'data-level' in attrs else len(self.lists);extra['ordered']=bool(self.lists and self.lists[-1][0]=='ol')
        if tag not in ('img','br','hr','meta','link','input'):self.active.append((tag,start,kind,style,url,extra))

    def handle_startendtag(self,tag,attrs):
        self.handle_starttag(tag,attrs)
        if tag not in ('img','br','hr','meta','link','input'):self.handle_endtag(tag)

    def handle_endtag(self,tag):
        if self.hidden:
            if tag==self.hidden[-1]:self.hidden.pop()
            return
        for i in range(len(self.active)-1,-1,-1):
            name,start,kind,style,url,attrs=self.active[i]
            if name!=tag:continue
            end=len(self.text)
            if style and end>start:self.spans.append((start,end,style,url if style=='link' else ''))
            if kind:self.structure.append(Structure(start,end,kind,self.text[start:end].strip(),attrs))
            self.active.pop(i);break
        if tag in self.blocks:self.newline()
        if tag in ('ul','ol') and self.lists:self.lists.pop()

    def handle_data(self,data):
        if self.hidden:return
        if any(a[0]=='pre' for a in self.active):self.text+=data;return
        value=re.sub(r'\s+',' ',data)
        if not self.text or self.text.endswith((' ','\n','\t')):value=value.lstrip()
        self.text+=value

    def finish(self):
        for tag,*_ in list(reversed(self.active)):self.handle_endtag(tag)
        self.text=self.text.rstrip()
        self.spans=[(a,min(b,len(self.text)),s,u) for a,b,s,u in self.spans if a<min(b,len(self.text))]
        for node in self.structure:node.end=min(node.end,len(self.text));node.start=min(node.start,node.end)
        self.structure.sort(key=lambda n:(n.start,-n.end,n.kind))
        return self


def html_document(source):
    result=RichText();result.feed(source);result.close();return result.finish()


W='{http://schemas.openxmlformats.org/wordprocessingml/2006/main}'
R='{http://schemas.openxmlformats.org/officeDocument/2006/relationships}'


def word_document(archive):
    """Convert Word body order, styles, numbering, tables, notes and anchors to safe structure."""
    def xml(name):return ET.fromstring(archive.read(name)) if name in archive.namelist() else ET.Element('empty')
    relationships={e.get('Id'):e.get('Target','') for e in xml('word/_rels/document.xml.rels') if e.get('Type','').endswith('/hyperlink')}
    style_root=xml('word/styles.xml')
    styles={e.get(W+'styleId'):e for e in style_root.findall(W+'style')}
    def properties(style,seen=None):
        seen=set() if seen is None else seen
        if style in seen or style not in styles:return []
        seen.add(style);node=styles[style];base=node.find(W+'basedOn')
        return (properties(base.get(W+'val'),seen) if base is not None else [])+[node]
    numbering=xml('word/numbering.xml');abstract={n.get(W+'abstractNumId'):n for n in numbering.findall(W+'abstractNum')}
    nums={n.get(W+'numId'):n for n in numbering.findall(W+'num')};counters={}
    def value(node,path,default=''):
        found=node.find(path);return found.get(W+'val',default) if found is not None else default
    def runs(node,inherited=()):
        if node.tag==W+'p':
            defaults=style_root.find(W+'docDefaults/'+W+'rPrDefault')
            inherited=([defaults] if defaults is not None else [])+properties('Normal')+properties(value(node,W+'pPr/'+W+'pStyle'))
        out=[]
        for child in node:
            tag=child.tag.removeprefix(W)
            if tag=='r':
                content=''.join(escape(c.text or '') if c.tag==W+'t' else '<br>' if c.tag==W+'br' else '\t' if c.tag==W+'tab' else ('<a href="#footnote-'+c.get(W+'id','')+'">[Footnote '+c.get(W+'id','')+']</a>') if c.tag==W+'footnoteReference' else ('<a href="#endnote-'+c.get(W+'id','')+'">[Endnote '+c.get(W+'id','')+']</a>') if c.tag==W+'endnoteReference' else '' for c in child)
                style=value(child,W+'rPr/'+W+'rStyle');props=list(inherited)+properties(style)+[child]
                flags={};font='';size=''
                for p in props:
                    fonts=p.find(W+'rPr/'+W+'rFonts')
                    if fonts is not None:font=fonts.get(W+'ascii') or fonts.get(W+'hAnsi') or font
                    amount=value(p,W+'rPr/'+W+'sz')
                    if amount.isdigit():size=str(int(amount)/2)
                for prop,html in (('b','strong'),('i','em'),('u','u'),('strike','s')):
                    state=None
                    for p in props:
                        found=p.find(W+'rPr/'+W+prop)
                        if found is not None:state=found.get(W+'val','true') not in ('0','false','off','none')
                    if state:content='<'+html+'>'+content+'</'+html+'>'
                    flags[prop]=bool(state)
                if font or size:
                    content='<span data-font="'+escape(font or 'Segoe UI',quote=True)+'" data-size="'+(size or '12')+'" data-weight="'+('bold' if flags['b'] else 'normal')+'" data-slant="'+('italic' if flags['i'] else 'roman')+'">'+content+'</span>'
                out.append(content)
            elif tag=='hyperlink':
                target='#'+child.get(W+'anchor') if child.get(W+'anchor') else relationships.get(child.get(R+'id'),'')
                out.append('<a href="'+escape(target,quote=True)+'">'+runs(child,inherited)+'</a>')
            elif tag=='bookmarkStart':out.append('<a id="'+escape(child.get(W+'name',''),quote=True)+'"></a>')
            elif tag not in ('pPr','rPr','del'):out.append(runs(child,inherited))
        return ''.join(out)
    def paragraph(node):
        style=value(node,W+'pPr/'+W+'pStyle');chain=properties(style)+[node];level=None
        for p in chain:
            found=p.find(W+'pPr/'+W+'outlineLvl')
            if found is not None:level=int(found.get(W+'val','9'))+1
        match=re.search(r'heading\s*([1-6])',style,re.I)
        if level is None and match:level=int(match[1])
        tag='h'+str(level) if level and level<=6 else 'p';content=runs(node)
        num=None
        for p in chain:
            candidate=p.find(W+'pPr/'+W+'numPr')
            if candidate is not None:num=candidate
        if num is not None:
            numid=value(num,W+'numId');ilvl=value(num,W+'ilvl','0');definition=nums.get(numid)
            base=abstract.get(value(definition,W+'abstractNumId')) if definition is not None else None
            lvl=base.find(W+"lvl[@"+W+"ilvl='"+ilvl+"']") if base is not None else None
            bullet=lvl is not None and value(lvl,W+'numFmt')=='bullet'
            start=int(value(lvl,W+'start','1')) if lvl is not None else 1
            if definition is not None:
                override=next((entry for entry in definition.findall(W+'lvlOverride') if entry.get(W+'ilvl')==ilvl),None)
                if override is not None:start=int(value(override,W+'startOverride',str(start)))
            key=(numid,ilvl);counters[key]=counters.get(key,start-1)+1
            for other in list(counters):
                if other[0]==numid and int(other[1])>int(ilvl):counters.pop(other)
            listtag='ul' if bullet else 'ol'
            fmt=value(lvl,W+'numFmt','decimal') if lvl is not None else 'decimal'
            number=counters[key]
            if fmt in ('lowerLetter','upperLetter'):
                letters='';n=number
                while n>0:n,remainder=divmod(n-1,26);letters=chr(97+remainder)+letters
                label=letters.upper() if fmt=='upperLetter' else letters
            elif fmt in ('lowerRoman','upperRoman'):
                label='';n=number
                for amount,symbol in ((1000,'M'),(900,'CM'),(500,'D'),(400,'CD'),(100,'C'),(90,'XC'),(50,'L'),(40,'XL'),(10,'X'),(9,'IX'),(5,'V'),(4,'IV'),(1,'I')):
                    count,n=divmod(n,amount);label+=symbol*min(count,100)
                if fmt=='lowerRoman':label=label.lower()
            else:label=str(number)
            default_label='%'+str(int(ilvl)+1)+'.'
            punctuation=value(lvl,W+'lvlText',default_label) if lvl is not None else default_label
            label=re.sub(r'%[1-9]',lambda match:label if int(match[0][1:])-1==int(ilvl) else str(counters.get((numid,str(int(match[0][1:])-1)),1)),punctuation)
            # Each item retains its level and explicit continuation number.
            return '<'+listtag+' start="'+str(counters[key])+'" data-numid="'+numid+'"><li data-label="'+escape(label,quote=True)+'" data-level="'+ilvl+'">'+content+'</li></'+listtag+'>'
        return '<'+tag+'>'+content+'</'+tag+'>'
    def blocks(parent):
        result=[]
        for child in parent:
            if child.tag==W+'p':result.append(paragraph(child))
            elif child.tag==W+'tbl':
                rows=[]
                for row in child.findall(W+'tr'):
                    cells=[]
                    for cell in row.findall(W+'tc'):
                        span=value(cell,W+'tcPr/'+W+'gridSpan','1')
                        cells.append('<td colspan="'+escape(span)+'">'+blocks(cell)+'</td>')
                    rows.append('<tr>'+''.join(cells)+'</tr>')
                result.append('<table>'+''.join(rows)+'</table>')
            elif child.tag not in (W+'sectPr',W+'pPr',W+'tcPr',W+'tblPr',W+'tblGrid'):result.append(blocks(child))
        return ''.join(result)
    body=xml('word/document.xml').find(W+'body');source=blocks(body) if body is not None else ''
    for name in archive.namelist():
        if re.fullmatch(r'word/(?:header|footer)\d+\.xml',name):source+='<section><p>'+('Header' if '/header' in name else 'Footer')+'</p>'+blocks(xml(name))+'</section>'
    for part,kind in (('footnotes','footnote'),('endnotes','endnote')):
        notes=[]
        for note in xml('word/'+part+'.xml'):
            ident=note.get(W+'id','')
            if ident.lstrip('-').isdigit() and int(ident)>0:notes.append('<section role="doc-'+kind+'" id="'+kind+'-'+ident+'"><p>'+kind.title()+' '+ident+'</p>'+blocks(note)+'</section>')
        source+=''.join(notes)
    return html_document(source)


def odt_document(archive):
    root=ET.fromstring(archive.read('content.xml'))
    def local(tag):return tag.rsplit('}',1)[-1]
    definitions={}
    for name in ('styles.xml','content.xml'):
        if name not in archive.namelist():continue
        for element in ET.fromstring(archive.read(name)).iter():
            if local(element.tag) in ('style','list-style'):
                key=next((v for k,v in element.attrib.items() if local(k)=='name'),None)
                if key:definitions[key]=element
    def convert(node):
        name=local(node.tag);attrs={local(k):v for k,v in node.attrib.items()}
        content=escape(node.text or '')+''.join(convert(c)+escape(c.tail or '') for c in node)
        tag={'p':'p','h':'h'+attrs.get('outline-level','1'),'table':'table','table-row':'tr','table-cell':'td','list':'ul','list-item':'li','span':'span','a':'a','note':'section','line-break':'br','tab':'span'}.get(name,'div' if name=='text' else 'span')
        style=definitions.get(attrs.get('style-name'))
        if style is not None:
            if name=='list' and any(local(c.tag)=='list-level-style-number' for c in style):tag='ol'
            for prop in style.iter():
                values={local(k):v for k,v in prop.attrib.items()}
                for active,wrapper in ((values.get('font-weight')=='bold','strong'),(values.get('font-style')=='italic','em'),(values.get('text-underline-style') not in (None,'none'),'u')):
                    if active:content='<'+wrapper+'>'+content+'</'+wrapper+'>'
        if name in ('bookmark','bookmark-start'):return '<a id="'+escape(attrs.get('name',''),quote=True)+'"></a>'
        attributes=' href="'+escape(attrs.get('href',''),quote=True)+'"' if name=='a' else ' role="doc-footnote"' if name=='note' else ''
        if name=='tab':content='\t'
        if name=='s':content=' '*min(100,int(attrs.get('c','1')))
        return '<'+tag+attributes+'>'+content+('' if tag=='br' else '</'+tag+'>')
    return html_document(convert(root))

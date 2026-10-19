"""Passive local document reading. Never launch file associations or run content."""
from dataclasses import dataclass,field
from pathlib import Path
from reading_navigation import ReadingCursor
from email_reader import render_html
import zipfile
import xml.etree.ElementTree as ET

MAX_BYTES=50*1024*1024
MAX_TEXT=5*1024*1024
SUPPORTED=('.docx','.txt','.text','.md','.log','.csv','.tsv','.html','.htm','.xhtml','.pdf','.odt','.epub')

@dataclass
class DocumentReader:
    path:str
    text:str
    reading:ReadingCursor=field(default_factory=ReadingCursor)
    spans:list=field(default_factory=list)
    structure:list=field(default_factory=list)
    warnings:list=field(default_factory=list)
    selection:tuple|None=None
    def __post_init__(self):self.title=Path(self.path).name;self.reading.set_text(self.text,True)

    def describe_formatting(self):
        position=self.reading.position
        nodes=[n for n in self.structure if n.start<=position<max(n.end,n.start+1)]
        styles=[s for a,b,s,_ in self.spans if a<=position<b and not s.startswith('docfont:')]
        details=[n.kind+(' level '+str(n.attributes['level']) if n.kind=='heading' else '') for n in nodes if n.kind!='format']
        for n in nodes:
            if n.kind=='format':details.extend([n.attributes.get('data-font',''),n.attributes.get('data-size','')+' point' if n.attributes.get('data-size') else ''])
        return ', '.join(dict.fromkeys(item for item in details+styles if item)) or 'Plain text.'

    def navigate(self,kind,direction=1):
        items=[n for n in self.structure if n.kind==kind]
        position=self.reading.position
        candidates=[n for n in items if n.start>position] if direction>0 else [n for n in items if n.start<position]
        if not candidates:return 'No '+('next ' if direction>0 else 'previous ')+kind+'.'
        item=min(candidates,key=lambda n:n.start) if direction>0 else max(candidates,key=lambda n:n.start)
        self.reading.position=self.reading.continuation=item.start
        return item.kind+(' level '+str(item.attributes['level']) if item.kind=='heading' else '')+'. '+(item.label or self.reading.move('line',0))

    def follow_link(self):
        links=[n for n in self.structure if n.kind=='link' and n.start<=self.reading.position<n.end]
        if not links:return 'No link at the reading position.'
        target=links[0].attributes.get('href','')
        if target.startswith('#'):
            bookmark=next((n for n in self.structure if n.kind=='bookmark' and n.label==target[1:]),None)
            if bookmark:
                self.reading.position=self.reading.continuation=bookmark.start
                return self.reading.move('line',0)
            return 'The link target is not in this document.'
        return 'Link: '+target+'. Say open website followed by the address to browse.'

def xml_text(data):
    root=ET.fromstring(data)
    paragraphs=[]
    for element in root.iter():
        tag=element.tag.rsplit('}',1)[-1]
        if tag in ('p','h'):
            words=[]
            for node in element.iter():
                name=node.tag.rsplit('}',1)[-1]
                if name in ('tab',):words.append('\t')
                elif name in ('br','line-break'):words.append('\n')
                elif name=='t' and node.text:words.append(node.text)
            paragraphs.append(''.join(words) if words else ''.join(element.itertext()))
    return '\n'.join(paragraphs)

def decode(data):
    if data.startswith((b'\xff\xfe',b'\xfe\xff')):return data.decode('utf-16')
    try:return data.decode('utf-8-sig')
    except UnicodeDecodeError:return data.decode('cp1252',errors='replace')

def open_document(path):
    path=Path(path)
    if path.suffix.casefold() not in SUPPORTED:raise ValueError('This file type is not supported in Companion yet. Supported types include Word DOCX, text, HTML, PDF, ODT, and EPUB. The file was not launched in another app.')
    if path.stat().st_size>MAX_BYTES:raise ValueError('This file is over the 50 MB reading limit.')
    extension=path.suffix.casefold()
    rich=None;warnings=[]
    if extension=='.pdf':
        from pypdf import PdfReader,Configuration
        Configuration.zlib_maximum_output_length=MAX_BYTES
        pdf=PdfReader(path)
        if pdf.is_encrypted and not pdf.decrypt(''):raise ValueError('This PDF needs a password. It was not opened.')
        parts=[];length=0;has_text=False;pdf_spans=[];pdf_nodes=[];page_offsets=[]
        for number,page in enumerate(pdf.pages,1):
            content=page.extract_text() or ''
            if len(content.strip())<80 and page.images:
                from offline_ocr import recognize_pdf_page
                recognized,notice=recognize_pdf_page(path,number-1)
                if len(recognized.strip())>len(content.strip()):content=recognized
                warnings.extend(notice)
            has_text=has_text or bool(content.strip())
            length+=len(content)
            if length>MAX_TEXT:raise ValueError('This document has too much text for the reader.')
            parts.append('Page '+str(number)+'\n'+(content.strip() or 'No readable text on this page.'))
            offset=sum(len(part)+2 for part in parts[:-1])+len('Page '+str(number)+'\n')
            page_offsets.append(offset)
            from rich_documents import Structure
            pdf_nodes.append(Structure(offset,offset+len(content.strip()),'page',str(number)))
            fragments=[]
            def visitor(value,cm,tm,font,size):
                if value.strip():fragments.append((value.strip(),font or {},size))
            page.extract_text(visitor_text=visitor)
            sizes=sorted(size for _,_,size in fragments if size>0)
            normal=sizes[len(sizes)//2] if sizes else 12
            cursor=0
            for value,font,size in fragments:
                start=content.strip().find(value,cursor)
                if start<0:continue
                end=start+len(value);cursor=end;face=str(font.get('/BaseFont','')).lower()
                for token,style in (('bold','bold'),('italic','italic'),('oblique','italic')):
                    if token in face:pdf_spans.append((offset+start,offset+end,style,''))
                if size>normal*1.25:
                    pdf_nodes.append(Structure(offset+start,offset+end,'heading',value,{'level':1,'inferred':True}))
                    pdf_spans.append((offset+start,offset+end,'heading',''))
            from urllib.parse import urlsplit
            for annotation in page.get('/Annots',[]):
                annotation=annotation.get_object();action=annotation.get('/A',{})
                if hasattr(action,'get_object'):action=action.get_object()
                target=str(action.get('/URI',''))
                from email_reader import safe_link
                if not safe_link(target):continue
                at=sum(len(part)+2 for part in parts[:-1])+len(parts[-1])+1
                parts[-1]+='\n'+target
                pdf_spans.append((at,at+len(target),'link',target))
                pdf_nodes.append(Structure(at,at+len(target),'link',target,{'href':target}))
        text='\n\n'.join(parts)
        if not has_text:text+='\nThis PDF has no readable text.'
        def outline(items):
            from rich_documents import Structure
            for item in items:
                if isinstance(item,list):outline(item);continue
                try:page_number=pdf.get_destination_page_number(item)
                except (ValueError,KeyError):continue
                if page_number is not None and 0<=page_number<len(page_offsets):
                    at=page_offsets[page_number];pdf_nodes.append(Structure(at,at,'bookmark',str(item.title)))
        outline(pdf.outline)
        from types import SimpleNamespace
        rich=SimpleNamespace(spans=pdf_spans,structure=sorted(pdf_nodes,key=lambda n:n.start))
    elif extension in ('.docx','.odt','.epub'):
        with zipfile.ZipFile(path) as archive:
            if sum(i.file_size for i in archive.infolist())>MAX_BYTES:raise ValueError('This document expands beyond the reading limit.')
            if extension=='.docx':
                from rich_documents import word_document
                rich=word_document(archive);text=rich.text
            elif extension=='.odt':
                from rich_documents import odt_document
                rich=odt_document(archive);text=rich.text
            else:
                container=ET.fromstring(archive.read('META-INF/container.xml'))
                package=next(e.attrib['full-path'] for e in container.iter() if e.tag.endswith('rootfile'))
                root=ET.fromstring(archive.read(package));manifest={e.attrib['id']:e.attrib['href'] for e in root.iter() if e.tag.endswith('}item')}
                import posixpath
                names=[posixpath.normpath(posixpath.join(posixpath.dirname(package),manifest[e.attrib['idref']])) for e in root.iter() if e.tag.endswith('}itemref')]
                from rich_documents import html_document
                import re
                from html import escape,unescape
                from urllib.parse import urlsplit,unquote
                chapters=[];prefixes={name:'chapter-'+str(i)+'-' for i,name in enumerate(names)}
                for name in names:
                    source=decode(archive.read(name));prefix=prefixes[name]
                    source=re.sub(r'\bid\s*=\s*([\'"])(.*?)\1',lambda match:'id="'+escape(prefix+unescape(match[2]),quote=True)+'"',source,flags=re.I)
                    def link(match):
                        target=unescape(match[2]);parts=urlsplit(target)
                        resolved=posixpath.normpath(posixpath.join(posixpath.dirname(name),unquote(parts.path))) if parts.path else name
                        if not parts.scheme and resolved in prefixes:target='#'+prefixes[resolved]+parts.fragment
                        return 'href="'+escape(target,quote=True)+'"'
                    source=re.sub(r'\bhref\s*=\s*([\'"])(.*?)\1',link,source,flags=re.I)
                    chapters.append('<a id="'+prefix+'"></a>'+source)
                rich=html_document('\n'.join(chapters));text=rich.text
    else:
        data=path.read_bytes();text=decode(data)
        if extension in ('.html','.htm','.xhtml','.md','.csv','.tsv'):
            from rich_documents import html_document
            if extension=='.md':
                from markdown_it import MarkdownIt
                text=MarkdownIt('commonmark',{'html':False}).render(text)
            elif extension in ('.csv','.tsv'):
                import csv,io
                from html import escape
                text='<table>'+''.join('<tr>'+''.join('<td>'+escape(cell)+'</td>' for cell in row)+'</tr>' for row in csv.reader(io.StringIO(text),delimiter='\t' if extension=='.tsv' else ','))+'</table>'
            rich=html_document(text);text=rich.text
    if len(text)>MAX_TEXT:raise ValueError('This document has too much text for the reader.')
    return DocumentReader(str(path),text or 'This document has no readable text.',spans=rich.spans if rich else [],structure=rich.structure if rich else [],warnings=warnings)

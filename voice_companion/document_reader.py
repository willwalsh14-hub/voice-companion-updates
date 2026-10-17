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
    def __post_init__(self):self.title=Path(self.path).name;self.reading.set_text(self.text,True)

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
    if extension=='.pdf':
        from pypdf import PdfReader,Configuration
        Configuration.zlib_maximum_output_length=MAX_BYTES
        pdf=PdfReader(path)
        if pdf.is_encrypted and not pdf.decrypt(''):raise ValueError('This PDF needs a password. It was not opened.')
        parts=[];length=0;has_text=False
        for number,page in enumerate(pdf.pages,1):
            content=page.extract_text() or ''
            has_text=has_text or bool(content.strip())
            length+=len(content)
            if length>MAX_TEXT:raise ValueError('This document has too much text for the reader.')
            parts.append('Page '+str(number)+'\n'+(content.strip() or 'No readable text on this page.'))
        text='\n\n'.join(parts)
        if not has_text:text+='\nThis PDF may be scanned. OCR is not included.'
    elif extension in ('.docx','.odt','.epub'):
        with zipfile.ZipFile(path) as archive:
            if sum(i.file_size for i in archive.infolist())>MAX_BYTES:raise ValueError('This document expands beyond the reading limit.')
            if extension=='.docx':text=xml_text(archive.read('word/document.xml'))
            elif extension=='.odt':text=xml_text(archive.read('content.xml'))
            else:
                container=ET.fromstring(archive.read('META-INF/container.xml'))
                package=next(e.attrib['full-path'] for e in container.iter() if e.tag.endswith('rootfile'))
                root=ET.fromstring(archive.read(package));manifest={e.attrib['id']:e.attrib['href'] for e in root.iter() if e.tag.endswith('}item')}
                import posixpath
                names=[posixpath.normpath(posixpath.join(posixpath.dirname(package),manifest[e.attrib['idref']])) for e in root.iter() if e.tag.endswith('}itemref')]
                text='\n\n'.join(str(render_html(decode(archive.read(name)))) for name in names)
    else:
        data=path.read_bytes();text=decode(data)
        if extension in ('.html','.htm','.xhtml'):text=str(render_html(text))
    if len(text)>MAX_TEXT:raise ValueError('This document has too much text for the reader.')
    return DocumentReader(str(path),text or 'This document has no readable text.')

"""Build and check all manual formats from the same numbered text guide."""
from pathlib import Path
from datetime import datetime, timezone
from xml.etree import ElementTree as ET
from zipfile import ZipFile, ZIP_STORED, ZIP_DEFLATED
import html
import re
from docx import Document
from docx.shared import Inches, Pt, RGBColor
from guide_text import guide_blocks, PART, STEP

ROOT = Path(__file__).resolve().parent
SOURCE = ROOT / 'START HERE - Veteran.txt'
STEM = 'START HERE - Veteran'
UID = 'urn:uuid:340725b7-7e86-4b50-bf97-21c4b4270019'


def sections():
    return guide_blocks(SOURCE)


def subtitle(value):
    match = PART.fullmatch(value)
    return match[3] if match else None


def content_markup(blocks):
    output = []
    for i, (heading, paragraphs) in enumerate(blocks):
        if heading:
            output.append(f'<section aria-labelledby="section-{i}"><h2 id="section-{i}">{html.escape(heading.title())}</h2>')
        ordered = False; bulleted = False
        for j, value in enumerate(paragraphs):
            step = STEP.fullmatch(value)
            bullet = value.startswith('- ')
            if bulleted and not bullet:output.append('</ul>');bulleted=False
            if ordered and (not step or step[1]=='1'):
                output.append('</ol>'); ordered = False
            if subtitle(value):
                output.append(f'<h3 id="part-{i}-{j}">{html.escape(value)}</h3>')
            elif bullet:
                if not bulleted:output.append('<ul>');bulleted=True
                output.append('<li>'+html.escape(value[2:])+'</li>')
            elif step:
                if not ordered:
                    output.append('<ol>'); ordered = True
                output.append('<li>'+html.escape(step[2])+'</li>')
            else:
                output.append('<p>'+html.escape(value)+'</p>')
        if ordered: output.append('</ol>')
        if bulleted: output.append('</ul>')
        if heading: output.append('</section>')
    return '\n'.join(output)


def navigation(blocks, prefix=''):
    output=[]
    for i,(heading,paragraphs) in enumerate(blocks):
        if not heading:continue
        children=''.join(f'<li><a href="{prefix}#part-{i}-{j}">{html.escape(value)}</a></li>'
                         for j,value in enumerate(paragraphs) if subtitle(value))
        output.append(f'<li><a href="{prefix}#section-{i}">{html.escape(heading.title())}</a>'+ (f'<ul>{children}</ul>' if children else '')+'</li>')
    return ''.join(output)


def build_html(title, blocks):
    page = f'''<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>{html.escape(title.title())}</title>
<style>body{{font:1.125rem/1.6 Arial,sans-serif;max-width:48rem;margin:auto;padding:1rem;color:#111;background:#fff}}h1,h2,h3{{line-height:1.3}}h2{{margin-top:2.5rem}}h3{{margin-top:1.5rem}}a{{color:#0645ad}}li{{margin-block:.4rem}}:focus-visible{{outline:3px solid #0645ad}}@media print{{nav{{break-after:page}}h2,h3{{break-after:avoid}}}}</style></head><body>
<a href="#guide">Skip to the guide</a><header><h1>{html.escape(title.title())}</h1></header>
<nav aria-label="Contents"><h2>Contents</h2><ul>{navigation(blocks)}</ul></nav>
<main id="guide">{content_markup(blocks)}</main></body></html>'''
    (ROOT / f'{STEM}.html').write_text(page, encoding='utf-8')


def word_bookmark(paragraph, name, number):
    from docx.oxml import OxmlElement
    from docx.oxml.ns import qn
    first=OxmlElement('w:bookmarkStart');first.set(qn('w:id'),str(number));first.set(qn('w:name'),name)
    last=OxmlElement('w:bookmarkEnd');last.set(qn('w:id'),str(number))
    paragraph._p.insert(0,first);paragraph._p.append(last)


def word_link(paragraph, text, anchor):
    from docx.oxml import OxmlElement
    from docx.oxml.ns import qn
    link=OxmlElement('w:hyperlink');link.set(qn('w:anchor'),anchor)
    run=OxmlElement('w:r');props=OxmlElement('w:rPr')
    color=OxmlElement('w:color');color.set(qn('w:val'),'0645AD');props.append(color)
    underline=OxmlElement('w:u');underline.set(qn('w:val'),'single');props.append(underline)
    run.append(props);node=OxmlElement('w:t');node.text=text;run.append(node);link.append(run);paragraph._p.append(link)


def number_list(doc):
    from docx.oxml import OxmlElement
    from docx.oxml.ns import qn
    root=doc.part.numbering_part.element
    abstract_id=max([int(n.get(qn('w:abstractNumId'))) for n in root.findall(qn('w:abstractNum'))]+[-1])+1
    abstract=OxmlElement('w:abstractNum');abstract.set(qn('w:abstractNumId'),str(abstract_id))
    kind=OxmlElement('w:multiLevelType');kind.set(qn('w:val'),'singleLevel');abstract.append(kind)
    level=OxmlElement('w:lvl');level.set(qn('w:ilvl'),'0')
    for tag,value in [('start','1'),('numFmt','decimal'),('lvlText','%1.'),('lvlJc','left')]:
        node=OxmlElement('w:'+tag);node.set(qn('w:val'),value);level.append(node)
    abstract.append(level);root.append(abstract)
    numid=max([int(n.get(qn('w:numId'))) for n in root.findall(qn('w:num'))]+[0])+1
    num=OxmlElement('w:num');num.set(qn('w:numId'),str(numid))
    ref=OxmlElement('w:abstractNumId');ref.set(qn('w:val'),str(abstract_id));num.append(ref)
    override=OxmlElement('w:lvlOverride');override.set(qn('w:ilvl'),'0')
    start=OxmlElement('w:startOverride');start.set(qn('w:val'),'1');override.append(start);num.append(override);root.append(num)
    return numid


def apply_number(paragraph, number):
    from docx.oxml import OxmlElement
    from docx.oxml.ns import qn
    prop=OxmlElement('w:numPr')
    for tag,value in [('ilvl','0'),('numId',str(number))]:
        node=OxmlElement('w:'+tag);node.set(qn('w:val'),value);prop.append(node)
    paragraph._p.get_or_add_pPr().append(prop)


def build_word(title, blocks):
    doc=Document();page=doc.sections[0]
    page.page_width=Inches(8.5);page.page_height=Inches(11)
    page.top_margin=page.bottom_margin=Inches(.75);page.left_margin=page.right_margin=Inches(.8)
    for name,size in [('Title',24),('Normal',12),('Heading 1',18),('Heading 2',14)]:
        style=doc.styles[name];style.font.name='Arial';style.font.size=Pt(size);style.font.color.rgb=RGBColor(0,0,0)
    normal=doc.styles['Normal'].paragraph_format;normal.space_after=Pt(5);normal.line_spacing=1.08
    for name in ('Heading 1','Heading 2'):
        pf=doc.styles[name].paragraph_format;pf.keep_with_next=True;pf.space_before=Pt(14);pf.space_after=Pt(6)
    doc.add_paragraph(title.title(),style='Title')
    for value in blocks[0][1]:doc.add_paragraph(value)
    doc.add_paragraph('Contents',style='Heading 1')
    for i,(heading,paragraphs) in enumerate(blocks):
        if not heading:continue
        chapter_link=doc.add_paragraph();chapter_link.paragraph_format.keep_with_next=True
        word_link(chapter_link,heading.title(),'chapter_'+str(i))
        for j,value in enumerate(paragraphs):
            if subtitle(value):
                para=doc.add_paragraph();para.paragraph_format.left_indent=Inches(.25)
                para.paragraph_format.space_after=Pt(2)
                word_link(para,value,f'part_{i}_{j}')
    doc.add_page_break()
    for i,(heading,paragraphs) in enumerate(blocks):
        if not heading:continue
        para=doc.add_paragraph(heading.title(),style='Heading 1');word_bookmark(para,'chapter_'+str(i),i)
        number=None
        for j,value in enumerate(paragraphs):
            step=STEP.fullmatch(value)
            if subtitle(value):
                para=doc.add_paragraph(value,style='Heading 2')
                word_bookmark(para,f'part_{i}_{j}',1000+i*1000+j);number=None
            elif value.startswith('- '):
                doc.add_paragraph(value[2:],style='List Bullet');number=None
            elif step:
                if number is None or step[1]=='1':number=number_list(doc)
                para=doc.add_paragraph(step[2],style='List Number');apply_number(para,number)
                para.paragraph_format.keep_with_next=False
                para.paragraph_format.left_indent=Inches(.35)
                para.paragraph_format.first_line_indent=Inches(-.35)
                para.paragraph_format.tab_stops.add_tab_stop(Inches(.35))
            else:
                doc.add_paragraph(value);number=None
    from docx.oxml import OxmlElement
    from docx.oxml.ns import qn
    footer=page.footer.paragraphs[0];footer.alignment=2
    field=OxmlElement('w:fldSimple');field.set(qn('w:instr'),'PAGE');footer._p.append(field)
    for style_tree in (doc.element,doc.styles.element):
        for border in style_tree.xpath('.//w:pBdr'):border.getparent().remove(border)
    doc.core_properties.title=title.title();doc.core_properties.language='en-US'
    doc.save(ROOT / f'{STEM}.docx')


def build_epub(title, blocks):
    esc=html.escape
    xhtml=f'''<?xml version="1.0" encoding="utf-8"?><html xmlns="http://www.w3.org/1999/xhtml" lang="en" xml:lang="en"><head><title>{esc(title.title())}</title></head><body><h1>{esc(title.title())}</h1>{content_markup(blocks)}</body></html>'''
    nav=f'''<?xml version="1.0" encoding="utf-8"?><html xmlns="http://www.w3.org/1999/xhtml" xmlns:epub="http://www.idpf.org/2007/ops" lang="en" xml:lang="en"><head><title>Contents</title></head><body><nav epub:type="toc" id="toc"><h1>Contents</h1><ul>{navigation(blocks,'content.xhtml')}</ul></nav></body></html>'''
    modified=datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')
    opf=f'''<?xml version="1.0" encoding="utf-8"?><package xmlns="http://www.idpf.org/2007/opf" version="3.0" unique-identifier="pub-id" xml:lang="en"><metadata xmlns:dc="http://purl.org/dc/elements/1.1/"><dc:identifier id="pub-id">{UID}</dc:identifier><dc:title>{esc(title.title())}</dc:title><dc:language>en</dc:language><meta property="dcterms:modified">{modified}</meta></metadata><manifest><item id="nav" href="nav.xhtml" media-type="application/xhtml+xml" properties="nav"/><item id="content" href="content.xhtml" media-type="application/xhtml+xml"/></manifest><spine><itemref idref="content"/></spine></package>'''
    container='<?xml version="1.0"?><container version="1.0" xmlns="urn:oasis:names:tc:opendocument:xmlns:container"><rootfiles><rootfile full-path="OEBPS/package.opf" media-type="application/oebps-package+xml"/></rootfiles></container>'
    with ZipFile(ROOT / f'{STEM}.epub','w') as z:
        z.writestr('mimetype','application/epub+zip',compress_type=ZIP_STORED)
        for name,data in [('META-INF/container.xml',container),('OEBPS/package.opf',opf),('OEBPS/nav.xhtml',nav),('OEBPS/content.xhtml',xhtml)]:
            z.writestr(name,data.encode('utf-8'),compress_type=ZIP_DEFLATED)


def build_daisy(title, blocks):
    esc=html.escape
    nodes=[('title',title.title())];body=[f'<level1 id="guide"><h1 id="title" smilref="guide.smil#text-title">{esc(title.title())}</h1>']
    points=[];order=2
    for i,(heading,paragraphs) in enumerate(blocks):
        if heading:
            name='heading-'+str(i);nodes.append((name,heading.title()))
            body.append(f'<level2 id="section-{i}"><h2 id="{name}" smilref="guide.smil#text-{name}">{esc(heading.title())}</h2>')
            points.append(f'<navPoint id="nav-{i}" playOrder="{order}"><navLabel><text>{esc(heading.title())}</text></navLabel><content src="guide.smil#par-{name}"/>');order+=1
        part_open=False
        for j,value in enumerate(paragraphs):
            name=f'text-{i}-{j}';nodes.append((name,value))
            if subtitle(value):
                if part_open:body.append('</level3>')
                body.append(f'<level3><h3 id="{name}" smilref="guide.smil#text-{name}">{esc(value)}</h3>');part_open=True
                points.append(f'<navPoint id="nav-{i}-{j}" playOrder="{order}"><navLabel><text>{esc(value)}</text></navLabel><content src="guide.smil#par-{name}"/></navPoint>');order+=1
            else:body.append(f'<p id="{name}" smilref="guide.smil#text-{name}">{esc(value)}</p>')
        if part_open:body.append('</level3>')
        if heading:body.append('</level2>');points.append('</navPoint>')
    body.append('</level1>')
    dtbook=f'''<?xml version="1.0" encoding="utf-8"?><dtbook xmlns="http://www.daisy.org/z3986/2005/dtbook/" version="2005-3" xml:lang="en"><head><meta name="dc:Identifier" content="{UID}"/><meta name="dc:Title" content="{esc(title.title())}"/></head><book><frontmatter><doctitle>{esc(title.title())}</doctitle></frontmatter><bodymatter>{''.join(body)}</bodymatter></book></dtbook>'''
    pars=''.join(f'<par id="par-{name}"><text id="text-{name}" src="guide.xml#{name}"/></par>' for name,_ in nodes)
    smil=f'''<?xml version="1.0"?><smil xmlns="http://www.w3.org/2001/SMIL20/"><head><meta name="dtb:uid" content="{UID}"/></head><body><seq id="all">{pars}</seq></body></smil>'''
    ncx=f'''<?xml version="1.0"?><ncx xmlns="http://www.daisy.org/z3986/2005/ncx/" version="2005-1" xml:lang="en"><head><meta name="dtb:uid" content="{UID}"/><meta name="dtb:depth" content="3"/><meta name="dtb:totalPageCount" content="0"/><meta name="dtb:maxPageNumber" content="0"/></head><docTitle><text>{esc(title.title())}</text></docTitle><navMap><navPoint id="nav-title" playOrder="1"><navLabel><text>{esc(title.title())}</text></navLabel><content src="guide.smil#par-title"/>{''.join(points)}</navPoint></navMap></ncx>'''
    opf=f'''<?xml version="1.0"?><package xmlns="http://openebook.org/namespaces/oeb-package/1.0/" unique-identifier="uid"><metadata><dc-metadata xmlns:dc="http://purl.org/dc/elements/1.1/"><dc:Title>{esc(title.title())}</dc:Title><dc:Identifier id="uid">{UID}</dc:Identifier><dc:Language>en</dc:Language></dc-metadata><x-metadata><meta name="dtb:multimediaType" content="textNCX"/><meta name="dtb:multimediaContent" content="text"/></x-metadata></metadata><manifest><item id="ncx" href="guide.ncx" media-type="application/x-dtbncx+xml"/><item id="dtbook" href="guide.xml" media-type="application/x-dtbook+xml"/><item id="smil" href="guide.smil" media-type="application/smil"/></manifest><spine toc="ncx"><itemref idref="smil"/></spine></package>'''
    with ZipFile(ROOT / f'{STEM} - DAISY 3.zip','w',ZIP_DEFLATED) as z:
        for name,data in [('guide.opf',opf),('guide.xml',dtbook),('guide.ncx',ncx),('guide.smil',smil),('READ ME.txt','This DAISY guide contains text, not recorded audio. Unzip all files together. Open guide.opf in a DAISY 3 reader that can speak text.\n')]:z.writestr(name,data.encode('utf-8'))


def validate(blocks):
    chapters=[h.title() for h,_ in blocks if h]
    parts=[p for _,paragraphs in blocks for p in paragraphs if subtitle(p)]
    page=(ROOT / f'{STEM}.html').read_text(encoding='utf-8')
    assert re.findall(r'<h2 id="section-\d+">([^<]+)</h2>',page)==chapters
    assert re.findall(r'<h3 id="part-\d+-\d+">([^<]+)</h3>',page)==[html.escape(p) for p in parts]
    doc=Document(ROOT / f'{STEM}.docx')
    assert [p.text for p in doc.paragraphs if p.style.name=='Title']==['Voice Companion User Guide']
    assert [p.text for p in doc.paragraphs if p.style.name=='Heading 1' and p.text!='Contents']==chapters
    assert [p.text for p in doc.paragraphs if p.style.name=='Heading 2']==parts
    from docx.oxml.ns import qn
    numbering=doc.part.numbering_part.element
    for paragraph in doc.paragraphs:
        if paragraph.style.name!='List Number':continue
        numid=paragraph._p.find(qn('w:pPr')).find(qn('w:numPr')).find(qn('w:numId')).get(qn('w:val'))
        number=next(n for n in numbering.findall(qn('w:num')) if n.get(qn('w:numId'))==numid)
        assert number.find(qn('w:lvlOverride')).find(qn('w:startOverride')).get(qn('w:val'))=='1', 'Step list must restart at 1'
    with ZipFile(ROOT / f'{STEM}.epub') as z:
        assert z.namelist()[0]=='mimetype' and z.getinfo('mimetype').compress_type==ZIP_STORED
        ns={'x':'http://www.w3.org/1999/xhtml'};content=ET.fromstring(z.read('OEBPS/content.xhtml'));nav=ET.fromstring(z.read('OEBPS/nav.xhtml'))
        assert [h.text for h in content.findall('.//x:h2',ns)]==chapters
        assert [h.text for h in content.findall('.//x:h3',ns)]==parts
        assert [a.text for a in nav.findall('.//x:a',ns)]==[text for h,paragraphs in blocks if h for text in [h.title()]+[v for v in paragraphs if subtitle(v)]]
        for name in ('META-INF/container.xml','OEBPS/package.opf'):ET.fromstring(z.read(name))
    with ZipFile(ROOT / f'{STEM} - DAISY 3.zip') as z:
        ns={'d':'http://www.daisy.org/z3986/2005/dtbook/','n':'http://www.daisy.org/z3986/2005/ncx/'}
        content=ET.fromstring(z.read('guide.xml'));nav=ET.fromstring(z.read('guide.ncx'))
        assert [h.text for h in content.findall('.//d:h2',ns)]==chapters
        assert [h.text for h in content.findall('.//d:h3',ns)]==parts
        labels=[p.text for p in nav.findall('.//n:navLabel/n:text',ns)]
        assert all(h in labels for h in chapters+parts)
        for name in ('guide.smil','guide.opf'):ET.fromstring(z.read(name))


def main():
    from release_documents import load_notes,build
    version=load_notes(ROOT)['version']
    text=SOURCE.read_text(encoding='utf-8-sig')
    text=re.sub(r'Guide version: [^ ]+\.', 'Guide version: '+version+'.',text,count=1)
    SOURCE.write_text(text,encoding='utf-8')
    title,blocks=sections();build_html(title,blocks);build_word(title,blocks);build_epub(title,blocks);build_daisy(title,blocks);validate(blocks)
    from braille_documents import build_brf
    build_brf(SOURCE,ROOT / f'{STEM}.brf');build(ROOT)
    from guide_files import write_manifest
    write_manifest(ROOT,version)
    print('Validated six manual formats and six release-note formats, including UEB BRF.')


if __name__=='__main__':main()

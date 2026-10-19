from pathlib import Path
import tempfile
import unittest
import zipfile
from unittest.mock import patch
from document_reader import open_document
from rich_documents import html_document,word_document


class RichDocumentTests(unittest.TestCase):
    def test_html_structure_safe_links_nested_lists_and_exact_offsets(self):
        doc=html_document('<h2 id="intro">Title</h2><p><b>Bold</b> and <i>italic</i> <a href="#intro">Back</a></p><ol start="3"><li>Third<ul><li>Nested</li></ul></li></ol><table><tr><th>Name</th><td>Value</td></tr></table><script>bad()</script><iframe>hidden</iframe><a href="javascript:bad()">Unsafe</a>')
        self.assertNotIn('bad()',doc.text);self.assertNotIn('hidden',doc.text)
        self.assertIn('3. Third',doc.text);self.assertIn('• Nested',doc.text)
        self.assertEqual([n.attributes['level'] for n in doc.structure if n.kind=='heading'],[2])
        self.assertEqual(len([n for n in doc.structure if n.kind=='cell']),2)
        for a,b,style,url in doc.spans:
            self.assertTrue(0<=a<b<=len(doc.text))
            if style=='bold':self.assertEqual(doc.text[a:b],'Bold')
        self.assertEqual([url for _,_,style,url in doc.spans if style=='link'],['#intro'])

    def test_word_styles_numbering_notes_bookmarks_and_tables_without_source_changes(self):
        ns='http://schemas.openxmlformats.org/wordprocessingml/2006/main'
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/'complex.docx'
            with zipfile.ZipFile(path,'w') as z:
                z.writestr('word/document.xml',f'<w:document xmlns:w="{ns}" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"><w:body><w:p><w:pPr><w:pStyle w:val="CustomTitle"/></w:pPr><w:bookmarkStart w:name="top" w:id="1"/><w:r><w:t>Title</w:t></w:r></w:p><w:p><w:pPr><w:numPr><w:ilvl w:val="0"/><w:numId w:val="42"/></w:numPr></w:pPr><w:r><w:rPr><w:b/></w:rPr><w:t>List text</w:t><w:footnoteReference w:id="1"/></w:r></w:p><w:p><w:hyperlink r:id="r1"><w:r><w:t>Website</w:t></w:r></w:hyperlink></w:p><w:tbl><w:tr><w:tc><w:p><w:r><w:t>Cell</w:t></w:r></w:p></w:tc></w:tr></w:tbl></w:body></w:document>')
                z.writestr('word/styles.xml',f'<w:styles xmlns:w="{ns}"><w:style w:styleId="CustomTitle"><w:pPr><w:outlineLvl w:val="1"/></w:pPr></w:style></w:styles>')
                z.writestr('word/numbering.xml',f'<w:numbering xmlns:w="{ns}"><w:abstractNum w:abstractNumId="9"><w:lvl w:ilvl="0"><w:start w:val="4"/><w:numFmt w:val="decimal"/></w:lvl></w:abstractNum><w:num w:numId="42"><w:abstractNumId w:val="9"/></w:num></w:numbering>')
                z.writestr('word/_rels/document.xml.rels','<Relationships><Relationship Id="r1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/hyperlink" Target="https://example.com"/></Relationships>')
                z.writestr('word/footnotes.xml',f'<w:footnotes xmlns:w="{ns}"><w:footnote w:id="1"><w:p><w:r><w:t>Note body</w:t></w:r></w:p></w:footnote></w:footnotes>')
            before=path.read_bytes();doc=open_document(path)
            self.assertEqual(before,path.read_bytes());self.assertIn('4. List text',doc.text)
            self.assertTrue({'heading','list item','table','cell','footnote','bookmark','link'}<={n.kind for n in doc.structure})
            heading=next(n for n in doc.structure if n.kind=='heading');self.assertEqual(heading.attributes['level'],2)
            reference=next(n for n in doc.structure if n.kind=='link' and n.attributes['href']=='#footnote-1')
            doc.reading.position=reference.start;self.assertIn('Footnote 1',doc.follow_link())
            self.assertEqual(doc.text[doc.reading.position:][:10],'Footnote 1')

    def test_markdown_csv_and_reader_structure_navigation(self):
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/'example.md';path.write_text('# Title\n\n**Bold** and [Link](https://example.com)\n\n1. Item\n')
            doc=open_document(path);self.assertTrue(doc.spans);self.assertIn('list item',[n.kind for n in doc.structure])
            doc.reading.position=0;self.assertIn('link',doc.navigate('link'));self.assertIn('https://example.com',doc.follow_link())
            csv=Path(folder)/'example.csv';csv.write_text('Name,Value\n"Two, words",42')
            result=open_document(csv);self.assertEqual(len([n for n in result.structure if n.kind=='cell']),4)
            self.assertIn('Two, words',result.text)

    def test_pdf_scan_neural_ocr_runs_with_network_blocked(self):
        from PIL import Image,ImageDraw,ImageFont
        import socket
        with tempfile.TemporaryDirectory() as folder:
            image=Image.new('RGB',(1654,2339),'white');draw=ImageDraw.Draw(image)
            font=ImageFont.truetype('C:/Windows/Fonts/arial.ttf',42)
            lines=['Offline document reader','This scanned page stays on this computer.','Invoice number 4826. Total amount 125.50 dollars.']
            for i,line in enumerate(lines):draw.text((100,150+i*90),line,font=font,fill='black')
            path=Path(folder)/'scan.pdf';image.save(path,'PDF',resolution=150)
            with patch.object(socket,'create_connection',side_effect=AssertionError('OCR must stay offline')),patch.object(socket.socket,'connect',side_effect=AssertionError('OCR must stay offline')):
                doc=open_document(path)
            for word in ('Offline','scanned','computer','4826','125.50'):self.assertIn(word,doc.text)
            self.assertTrue(any('offline OCR' in warning for warning in doc.warnings))

    def test_ocr_upside_down_and_low_contrast_scans_keep_numbers(self):
        from PIL import Image,ImageDraw,ImageFont,ImageFilter
        import socket
        with tempfile.TemporaryDirectory() as folder:
            image=Image.new('RGB',(1654,2339),'white');draw=ImageDraw.Draw(image)
            font=ImageFont.truetype('C:/Windows/Fonts/arial.ttf',46)
            for index,line in enumerate(('Document reading test','Reference 73915','Amount 246.80 dollars')):
                draw.text((110,180+index*90),line,font=font,fill=(130,130,130))
            image=image.filter(ImageFilter.GaussianBlur(.4))
            pages=[image,image.rotate(180)]
            path=Path(folder)/'degraded.pdf';pages[0].save(path,'PDF',resolution=150,save_all=True,append_images=pages[1:])
            with patch.object(socket.socket,'connect',side_effect=AssertionError('OCR must stay offline')):
                reader=open_document(path)
            self.assertEqual(reader.text.count('73915'),2)
            self.assertEqual(reader.text.count('246.80'),2)


if __name__=='__main__':unittest.main()

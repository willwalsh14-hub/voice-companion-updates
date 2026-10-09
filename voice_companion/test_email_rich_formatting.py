import base64,json,tempfile,unittest
from pathlib import Path
from email import policy
from email.parser import BytesParser
from document_editor import Paragraph,TextRun
from email_draft import VoiceEmail
from email_delivery import gmail_request,microsoft_request,yahoo_submit
from email_formatting import formatted_body

class EmailRichFormattingTests(unittest.TestCase):
    def test_save_reopen_and_provider_html_match(self):
        with tempfile.TemporaryDirectory() as folder:
            draft=VoiceEmail(Path(folder),recipient='a@example.com',subject='Formatting',provider='gmail',compose_step='body')
            draft.paragraphs=[Paragraph('<Title>',kind='Heading 3',alignment='center'),Paragraph('Rich\ntext',runs=[TextRun('Rich',True,True,True,'Arial',18,'yellow'),TextRun('\ntext')],alignment='both',line_spacing_twips=480),Paragraph('Bullet',kind='Bulleted list'),Paragraph('Number',kind='Numbered list'),Paragraph('Cell A',table_id=1,row=1,column=1),Paragraph('Cell B',table_id=1,row=1,column=2),Paragraph('End',line_spacing_twips=360,line_spacing_rule='exact')]
            expected=formatted_body(draft)
            for token in ('<h3','&lt;Title&gt;','font-family:Arial','font-size:18pt','<u>','<mark>','text-align:justify','line-height:2','<ul>','<ol>','<table','Cell A','Cell B','line-height:18pt','<br>'):self.assertIn(token,expected)
            draft.save();self.assertEqual(formatted_body(VoiceEmail.open_existing(Path(folder),draft.title)),expected)
            saved=BytesParser(policy=policy.default).parsebytes(draft.path.read_bytes());self.assertEqual(saved.get_body(preferencelist=('html',)).get_content().strip(),expected)
            gmail=BytesParser(policy=policy.default).parsebytes(base64.urlsafe_b64decode(json.loads(gmail_request(draft,'token').data)['raw']));self.assertEqual(gmail.get_body(preferencelist=('html',)).get_content().strip(),expected)
            self.assertEqual(json.loads(microsoft_request(draft,'token').data)['message']['body']['content'],expected)
            messages=[]
            class SMTP:
                def __init__(self,*a,**k):pass
                def __enter__(self):return self
                def __exit__(self,*a):pass
                def login(self,*a):pass
                def send_message(self,message):messages.append(message);return {}
            yahoo_submit(draft,'me@yahoo.com','secret',SMTP);self.assertEqual(messages[0].get_body(preferencelist=('html',)).get_content().strip(),expected)

    def test_outlook_responses_use_html_without_comment_conflict(self):
        with tempfile.TemporaryDirectory() as folder:
            for action in ('reply','reply_all','forward'):
                draft=VoiceEmail(Path(folder),recipient='a@example.com',subject='Response',provider='outlook');draft.paragraphs=[Paragraph('Rich reply',kind='Heading 2',bold=True)]
                draft.response_context={'action':action,'source_id':'original','recipient_override':True};draft.cc=['cc@example.com'];draft.bcc=['bcc@example.com']
                payload=json.loads(microsoft_request(draft,'token').data);self.assertNotIn('comment',payload);self.assertEqual(payload['message']['body']['contentType'],'HTML');self.assertIn('<h2',payload['message']['body']['content']);self.assertEqual(payload['message']['toRecipients'][0]['emailAddress']['address'],'a@example.com')

    def test_email_dictation_formatting_and_spacing_prompt(self):
        from unittest.mock import patch
        import companion
        with tempfile.TemporaryDirectory() as folder:
            draft=VoiceEmail(Path(folder),compose_step='body');draft.paragraphs=[Paragraph('Body text')]
            with patch.object(companion,'email_draft',draft),patch.object(companion,'INPUT_MODE','dictation'),patch.object(companion,'APP_WINDOW',None),patch.object(companion,'speak'):
                for command in ('set font to Arial','set font size to 18','align center','heading level three','bulleted list','numbered list','double space','custom spacing','one point five','yes'):companion.handle(command,'email_draft')
                p=draft.paragraphs[0];self.assertEqual((p.text,p.font,p.size,p.alignment,p.kind,p.line_spacing_twips),('Body text','Arial',18,'center','Numbered list',360))
                companion.handle('normal text','email_draft');self.assertEqual(p.kind,'Normal')
                companion.handle('line spacing 1.25','email_draft');self.assertEqual(p.line_spacing_twips,300)
                companion.handle('create table with two rows and two columns','email_draft');self.assertEqual(len([p for p in draft.paragraphs if p.table_id]),4)
                companion.handle('next cell','email_draft');self.assertEqual(draft.table_cell().column,2)

    def test_selected_font_and_paragraph_formatting(self):
        with tempfile.TemporaryDirectory() as folder:
            draft=VoiceEmail(Path(folder),compose_step='body');draft.paragraphs=[Paragraph('One two'),Paragraph('Three four')]
            draft.process('select first word');draft.process('set font to Arial');draft.process('set font size to 20')
            self.assertEqual([(r.text,r.font,r.size) for r in draft.paragraphs[0].runs],[('One','Arial',20),(' two','',0)])
            draft.process('select all');draft.process('align right');draft.process('heading level two');draft.process('double space')
            self.assertTrue(all(p.alignment=='right' and p.kind=='Heading 2' and p.line_spacing_twips==480 for p in draft.paragraphs))

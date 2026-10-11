import tempfile,unittest
from pathlib import Path
from unittest.mock import Mock,patch
from web_browse import BrowseCursor,describe,voice_browse_key,QUICK_KEYS
from web_assistant import WebSession

def cell(key,row,column,label,**values):return dict(key=key,role='cell',tag='td',type='',label=label,row=row,column=column,table='t',start=row*20+column*4,end=row*20+column*4+3,**values)
class WebBrowseTests(unittest.TestCase):
    def test_voice_covers_every_quick_key_and_table_direction(self):
        names={'form':'form control','combobox':'combo box','edit':'edit field','radio':'radio button','listitem':'list item'}
        for key,name in QUICK_KEYS.items():
            spoken=names.get(name,name)
            expected='r' if key=='a' else key
            self.assertEqual(voice_browse_key('next '+spoken),'Quick:'+expected)
            self.assertEqual(voice_browse_key('move to previous '+spoken),'Quick:Previous:'+expected)
        for number,word in enumerate(('one','two','three','four','five','six'),1):
            self.assertEqual(voice_browse_key('next heading level '+word),'Quick:'+str(number))
            self.assertEqual(voice_browse_key('previous heading '+word),'Quick:Previous:'+str(number))
        for level in range(1,7):self.assertEqual(voice_browse_key('next heading level '+str(level)),'Quick:'+str(level))
        for phrase,key in [('next row','Down'),('previous row','Up'),('next column','Right'),('previous column','Left'),('first column','Home'),('last column','End'),('first row','Prior'),('last row','Next')]:
            self.assertEqual(voice_browse_key(phrase),'Table:'+key)
    def test_voice_navigation_cannot_erase_an_uncommitted_private_field(self):
        with tempfile.TemporaryDirectory() as folder:
            backend=Mock();session=WebSession(folder,backend)
            session.snapshot={'text':'Page','elements':[]};session.keyboard_field={'type':'password','label':'Password','value':''}
            self.assertIn('keyboard',session.command('next form control'));backend.fill.assert_not_called()
            self.assertIsNotNone(session.keyboard_field)
            self.assertIn('canceled',session.command('cancel field editing'));self.assertIsNone(session.keyboard_field)
    def test_failed_live_value_read_never_leaves_a_partial_editor(self):
        with tempfile.TemporaryDirectory() as folder:
            backend=Mock();backend.field_value.side_effect=RuntimeError('Page changed')
            session=WebSession(folder,backend)
            item=dict(key='a',role='field',tag='input',type='text',label='Name',value='Partial',start=0,end=8)
            session.snapshot={'text':'Name','elements':[item]};session.browse=BrowseCursor(session.snapshot);session.browse.move('e')
            session.keyboard('Activate');self.assertIsNone(session.keyboard_field);backend.fill.assert_not_called()
    def test_paragraph_reading_keeps_the_full_text(self):
        with tempfile.TemporaryDirectory() as folder:
            session=WebSession(folder,Mock());text='First paragraph.\n\n'+('Second paragraph text. '*30)
            session.snapshot={'text':text,'elements':[]};session.reading.set_text(text);session.browse=BrowseCursor(session.snapshot)
            result=session.command('next paragraph')
            if 'Second' not in result:result=session.command('next paragraph')
            self.assertIn('Second paragraph text.',result);self.assertGreater(len(result),300)
    def test_voice_actions_reach_the_application_web_dispatcher(self):
        import companion as app
        session=Mock();session.command.return_value='Page action.'
        with patch.object(app,'web_session',session),patch.object(app,'pending_website',None),patch.object(app,'APP_WINDOW',None),patch.object(app,'speak'),patch.object(app,'sync_app_context'),patch.object(app,'interrupt_speech'),patch.object(app,'flush_keyboard_edits'):
            for phrase in ('next heading','previous checkbox','next form control','next row','previous column','first row','last column','activate current element','edit current field','toggle checkbox','select radio button','select option Two','save field','cancel field editing'):
                session.command.reset_mock()
                self.assertEqual(app.handle(phrase,'web'),'web')
                session.command.assert_called_once_with(phrase)
    def test_voice_edit_and_keyboard_share_field_state(self):
        with tempfile.TemporaryDirectory() as folder:
            backend=Mock();session=WebSession(folder,backend)
            item=dict(key='a',role='field',tag='input',type='text',label='Name',value='',start=0,end=8,href='')
            session.snapshot={'text':'Name','elements':[item]};session.browse=BrowseCursor(session.snapshot)
            backend.snapshot.return_value=session.snapshot
            self.assertIn('Name',session.command('next edit field'))
            self.assertIn('Forms mode',session.command('edit current field'))
            session.command('type Test name');session.command('save field')
            self.assertEqual(backend.fill.call_args.args[1],'Test name');self.assertFalse(backend.fill.call_args.kwargs['private'])
    def test_quick_keys_reverse_level_and_boundaries(self):
        elements=[dict(key='h',role='heading',level=2,label='Title',start=0,end=5),dict(key='l',role='link',label='Link',start=6,end=10),dict(key='c',role='field',tag='input',type='checkbox',label='Agree',checked=True,start=11,end=16),dict(key='b',role='button',tag='input',type='submit',label='Send',start=17,end=21)]
        cursor=BrowseCursor({'elements':elements})
        self.assertIn('heading level 2',cursor.move('2'));self.assertIn('Link',cursor.move('k'))
        self.assertIn('checked',cursor.move('x'));self.assertIn('heading',cursor.move('h',True))
        self.assertIn('Link',cursor.move('',tab=True));self.assertIn('Agree',cursor.move('f'));self.assertIn('button',cursor.move('b'))
        self.assertEqual(cursor.move('b'),'No next button.')
    def test_table_rows_columns_headers_spans_and_boundaries(self):
        elements=[dict(key='t',role='table',label='Prices',start=0,end=100),cell('h',0,0,'Product',header=True,scope='col'),cell('h2',0,1,'Cost',header=True,scope='col'),cell('a',1,0,'Apple'),cell('b',1,1,'One dollar'),cell('c',2,0,'Summary',colspan=2)]
        cursor=BrowseCursor({'elements':elements});cursor.move('t')
        self.assertIn('Product',cursor.table_move('Home'));self.assertIn('Apple',cursor.table_move('Down'))
        self.assertIn('Cost. One dollar',cursor.table_move('Right'));self.assertIn('Summary',cursor.table_move('Down'))
        self.assertIn('One dollar',cursor.table_move('Up'));self.assertEqual(cursor.table_move('Right'),'Table boundary.')
        self.assertIn('Cost',cursor.table_move('Prior'));self.assertIn('Summary',cursor.table_move('Next'))
    def test_keyboard_edit_does_not_submit_and_esc_cancels(self):
        with tempfile.TemporaryDirectory() as folder:
            backend=Mock();session=WebSession(folder,backend)
            item=dict(key='a',role='field',tag='input',type='text',label='Name',value='Original',start=0,end=8,href='')
            session.snapshot={'text':'Name','elements':[item]};session.browse=BrowseCursor(session.snapshot);session.browse.move('e')
            self.assertIn('Forms mode',session.keyboard('Activate'));backend.fill.assert_not_called()
            session.keyboard_field['value']='Changed';session.keyboard('Escape');backend.fill.assert_not_called();self.assertIsNone(session.keyboard_field)
    def test_password_echo_is_hidden_and_values_are_not_command_text(self):
        from app_window import CompanionWindow
        window=CompanionWindow('test');window.context={'web_private':True};window.feedback=Mock()
        window.report_entry_key('','secret',0,6,'t','t');window.feedback.assert_called_once_with('Hidden character.')
        window.submit_entry(' secret ');action,payload=window.commands.get_nowait()
        self.assertEqual(action,'web_private_value');self.assertEqual(payload['value'],' secret ')
    def test_keyboard_page_context_and_caret_are_shared(self):
        import companion as app
        with tempfile.TemporaryDirectory() as folder:
            session=WebSession(folder,Mock());session.snapshot={'text':'Page text','elements':[]};session.reading.set_text('Page text');session.browse=BrowseCursor(session.snapshot)
            with patch.object(app,'web_session',session),patch.object(app,'APP_WINDOW',None):
                ctx=app.app_context('web');self.assertTrue(ctx['readonly']);self.assertEqual(ctx['text'],'Page text')
                app.apply_keyboard_edit(dict(source=ctx['source'],before='Page text',after='Page text',caret=4,readonly=True,seq=1),'web')
                self.assertEqual(session.reading.position,4);self.assertEqual(session.browse.position,4)
if __name__=='__main__':unittest.main()

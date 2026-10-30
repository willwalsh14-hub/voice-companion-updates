import tempfile,unittest
from pathlib import Path
from unittest.mock import Mock,patch
from web_browse import BrowseCursor,describe,voice_browse_key,QUICK_KEYS
from web_assistant import WebSession

def cell(key,row,column,label,**values):return dict(key=key,role='cell',tag='td',type='',label=label,row=row,column=column,table='t',start=row*20+column*4,end=row*20+column*4+3,**values)
class WebBrowseTests(unittest.TestCase):
    def test_voice_covers_every_quick_key_and_table_direction(self):
        self.assertEqual(voice_browse_key('next control'),'Tab')
        self.assertEqual(voice_browse_key('previous control'),'ShiftTab')
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
    def test_password_can_be_dictated_saved_or_canceled_without_readback(self):
        with tempfile.TemporaryDirectory() as folder:
            backend=Mock(document_stamp=Mock(return_value=None));session=WebSession(folder,backend)
            session.snapshot={'text':'Page','elements':[]};session.keyboard_field={'key':'secret','type':'password','tag':'input','label':'Password','value':''}
            self.assertIn('Text entered',session.dictate_to_focus('FixtureSecret'));backend.fill.assert_not_called()
            item=dict(session.keyboard_field)
            session._refresh_at=Mock(return_value='Password field.')
            session.commit_keyboard_field();backend.fill.assert_called_once_with(item,'FixtureSecret',private=True)
            self.assertIsNone(session.keyboard_field)
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
            self.assertIn('edit',session.command('edit current field'))
            session.command('type Test name');session.command('save field')
            self.assertEqual(backend.fill.call_args.args[1],'Test name');self.assertFalse(backend.fill.call_args.kwargs['private'])
    def test_quick_keys_reverse_level_and_boundaries(self):
        elements=[dict(key='h',role='heading',level=2,label='Title',start=0,end=5),dict(key='l',role='link',label='Link',start=6,end=10),dict(key='c',role='field',tag='input',type='checkbox',label='Agree',checked=True,start=11,end=16),dict(key='b',role='button',tag='input',type='submit',label='Send',start=17,end=21)]
        cursor=BrowseCursor({'elements':elements})
        self.assertIn('heading level 2',cursor.move('2'));self.assertIn('Link',cursor.move('k'))
        self.assertIn('checked',cursor.move('x'));self.assertIn('heading',cursor.move('h',True))
        self.assertIn('Link',cursor.move('',tab=True));self.assertIn('Agree',cursor.move('f'));self.assertIn('button',cursor.move('b'))
        self.assertEqual(cursor.move('b'),'End of list. No next button.')
    def test_table_rows_columns_headers_spans_and_boundaries(self):
        elements=[dict(key='t',role='table',label='Prices',start=0,end=100),cell('h',0,0,'Product',header=True,scope='col'),cell('h2',0,1,'Cost',header=True,scope='col'),cell('a',1,0,'Apple'),cell('b',1,1,'One dollar'),cell('c',2,0,'Summary',colspan=2)]
        cursor=BrowseCursor({'elements':elements});cursor.move('t')
        self.assertIn('Product',cursor.table_move('Home'));self.assertIn('Apple',cursor.table_move('Down'))
        self.assertIn('Cost. One dollar',cursor.table_move('Right'));self.assertIn('Summary',cursor.table_move('Down'))
        self.assertIn('One dollar',cursor.table_move('Up'));self.assertEqual(cursor.table_move('Right'),'End of table.')
        self.assertIn('Cost',cursor.table_move('Prior'));self.assertIn('Summary',cursor.table_move('Next'))
    def test_keyboard_edit_does_not_submit_and_esc_cancels(self):
        with tempfile.TemporaryDirectory() as folder:
            backend=Mock();session=WebSession(folder,backend)
            item=dict(key='a',role='field',tag='input',type='text',label='Name',value='Original',start=0,end=8,href='')
            session.snapshot={'text':'Name','elements':[item]};session.browse=BrowseCursor(session.snapshot);session.browse.move('e')
            self.assertIn('edit',session.keyboard('Activate'));backend.fill.assert_not_called()
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

class SentencePunctuationTests(unittest.TestCase):
    def test_a_sentence_name_does_not_say_period_comma(self):
        """A paragraph or list item is a sentence: "Just one line of text., paragraph." was said,
        and with punctuation on that is heard as "period comma"."""
        self.assertEqual(describe({'role':'paragraph','label':'Just one line of text.'}),'Just one line of text, paragraph.')
        self.assertEqual(describe({'role':'listitem','label':'Buy milk.','position':1,'set_size':2}),'Buy milk, list item 1 of 2.')
        self.assertEqual(describe({'role':'button','label':'Next:'}),'Next, button.')
        self.assertEqual(describe({'role':'link','label':'Read more.'}),'Link Read more.')
        self.assertEqual(describe({'role':'edit','label':'Add a comment...'}),'Add a comment..., edit.')
        self.assertEqual(describe({'role':'heading','level':2,'label':'Are you a veteran?'}),'Are you a veteran?, heading level 2.')


class EmptyListTests(unittest.TestCase):
    def test_a_page_with_no_links_says_so_plainly(self):
        """On a page with no links, List links said the links "may be inaccessible to this
        browser helper" and to ask a helper, unlike List headings ("No headings on this page.")."""
        with tempfile.TemporaryDirectory() as folder:
            backend=Mock();backend.snapshot.return_value={'url':'https://plain.example/','title':'Plain','text':'Just text.','controls':[]}
            session=WebSession(folder,backend)
            session.snapshot={'url':'https://plain.example/','title':'Plain','text':'Just text.','controls':[],'elements':[]}
            said=session.list_links()
        self.assertTrue(said.startswith('No links on this page.'),said)
        self.assertNotIn('helper',said)


class EditableComboTests(unittest.TestCase):
    """Search boxes such as YouTube's are combo boxes you type into, with suggestions."""
    SEARCH=dict(key='s',role='combobox',tag='input',type='text',label='Search',value='',start=0,end=20,href='')
    def test_text_input_combo_boxes_are_editable_and_choices_are_not(self):
        from web_browse import editable_combo,kind
        editable=[self.SEARCH,
                  dict(key='g',role='combobox',tag='textarea',type='textarea',label='Search'),
                  dict(key='d',role='combobox',tag='input',type='text',label='Color',datalist=True),
                  dict(key='c',role='combobox',tag='div',type='',label='City',combo_edit=True,combo_input_key='i'),
                  dict(key='e',role='combobox',tag='div',type='',label='Note',editable=True)]
        choices=[dict(key='x',role='field',tag='select',type='select-one',label='Choice',selected_label='One'),
                 dict(key='b',role='combobox',tag='button',type='button',label='Custom choice',selected_label='Alpha'),
                 dict(key='v',role='combobox',tag='div',type='',label='Estado',selected_label='Texas'),
                 dict(key='r',role='combobox',tag='input',type='checkbox',label='Odd'),
                 dict(key='l',role='listbox',tag='div',type='',label='List')]
        for item in editable:
            self.assertTrue(editable_combo(item),item);self.assertEqual(kind(item),'combobox')
        for item in choices:self.assertFalse(editable_combo(item),item)
        # The page records what it found; that wins over the tag guess.
        self.assertFalse(editable_combo(dict(self.SEARCH,combo_edit=False)))
        self.assertEqual(describe(self.SEARCH),'Search, edit combo box.')
        self.assertEqual(describe(dict(self.SEARCH,value='cats',required=True)),'Search, edit combo box, cats, required.')
        self.assertEqual(describe(choices[0]),'Choice, combobox, One.')
        self.assertEqual(describe(choices[1]),'Custom choice, combobox, Alpha.')
    def test_quick_keys_find_editable_combo_as_edit_field_and_combo_box(self):
        elements=[dict(key='x',role='field',tag='select',type='select-one',label='Choice',selected_label='One',start=0,end=5),dict(self.SEARCH,start=6,end=20)]
        cursor=BrowseCursor({'elements':elements})
        self.assertEqual(cursor.move('e'),'Search, edit combo box.')
        cursor=BrowseCursor({'elements':elements})
        self.assertIn('Choice',cursor.move('c'));self.assertIn('Search, edit combo box',cursor.move('c'))
        self.assertIn('Search',cursor.move('f',True) and cursor.move('f'))
    def test_suggestion_voice_commands(self):
        self.assertEqual(voice_browse_key('next suggestion'),'Down')
        self.assertEqual(voice_browse_key('Previous suggestion.'),'Up')
    def session(self,folder,item=None):
        backend=Mock();backend.document_stamp.return_value=None;backend.field_value.return_value=''
        backend.combo_move.return_value={'options':['cats','cat videos'],'active':0,'value':'cats','expanded':True}
        session=WebSession(folder,backend);item=dict(item or self.SEARCH)
        session.snapshot={'text':'Search, edit combo box','elements':[item],'url':'https://video.test/'};session.browse=BrowseCursor(session.snapshot);session.browse.move('c')
        return session,backend
    def test_editable_combo_is_typed_into_and_arrows_read_suggestions(self):
        with tempfile.TemporaryDirectory() as folder:
            session,backend=self.session(folder)
            self.assertIn('Search, edit combo box',session.keyboard('Activate'))
            backend.focus.assert_called_once();self.assertEqual(session.keyboard_field['label'],'Search')
            # Letters are text here, never option shortcuts.
            session.keyboard_field['value']='cat';session.combo_typed()
            backend.type_combo.assert_called_with(session.keyboard_field,'cat')
            self.assertEqual(session.keyboard('Down'),'cats, 1 of 2.')
            backend.choose_form_option.assert_not_called()
            # The site wrote the suggestion into the box; the editor follows it.
            self.assertEqual(session.keyboard_field['value'],'cats');self.assertEqual(session.web_edit_caret,4)
            backend.combo_move.return_value={'options':['cats','cat videos'],'active':None,'value':'cat','expanded':True}
            self.assertEqual(session.keyboard('Up'),'No suggestion selected. Field text: cat.')
            backend.combo_move.return_value={'options':[],'active':None,'value':'zzz','expanded':False}
            self.assertEqual(session.keyboard('Down'),'No suggestions. Keep typing, or press Tab to move on.')
    def test_voice_typing_in_editable_combo_reaches_the_site(self):
        with tempfile.TemporaryDirectory() as folder:
            session,backend=self.session(folder)
            backend.combo_state.return_value={'options':['cats','cat videos'],'active':None,'value':'cat','expanded':True}
            result=session.command('type cat')
            backend.type_combo.assert_called_with(session.keyboard_field,'cat')
            self.assertIn('Text entered. 2 suggestions.',result);self.assertIn('Down',result)
            backend.type_combo.reset_mock()
            session.dictate_to_focus('dogs');backend.type_combo.assert_called()
    def test_escape_closes_suggestions_first_then_cancels_and_restores(self):
        with tempfile.TemporaryDirectory() as folder:
            session,backend=self.session(folder,dict(self.SEARCH,value='old'))
            backend.field_value.return_value='old';session.keyboard('Activate')
            session.keyboard_field['value']='new'
            backend.combo_close.return_value={'closed':True,'value':'new'}
            self.assertEqual(session.keyboard('Escape'),'Suggestions closed.')
            self.assertIsNotNone(session.keyboard_field);backend.blur_field.assert_not_called()
            backend.combo_close.return_value={'closed':False,'value':'new'}
            self.assertIn('Field editing canceled',session.keyboard('Escape'))
            self.assertIsNone(session.keyboard_field)
            backend.type_combo.assert_called_with(unittest.mock.ANY,'old');backend.blur_field.assert_called_once()
    def test_choice_combo_boxes_keep_option_keys(self):
        with tempfile.TemporaryDirectory() as folder:
            item=dict(key='b',role='combobox',tag='button',type='button',label='Custom choice',selected_label='Alpha',start=0,end=10,
                      options=[{'label':'Alpha','selected':True},{'label':'Beta'}])
            session,backend=self.session(folder,item)
            backend.snapshot.return_value=dict(session.snapshot)
            session.keyboard('Activate');session.keyboard('Down')
            backend.choose_form_option.assert_called_once();backend.combo_move.assert_not_called();backend.type_combo.assert_not_called()
    def test_application_context_and_keyboard_edits_for_editable_combo(self):
        import companion as app
        with tempfile.TemporaryDirectory() as folder:
            session,backend=self.session(folder);session.keyboard('Activate')
            with patch.object(app,'web_session',session),patch.object(app,'APP_WINDOW',None):
                ctx=app.app_context('web')
                self.assertFalse(ctx['web_choice']);self.assertTrue(ctx['web_combo_edit']);self.assertFalse(ctx['readonly'])
                self.assertTrue(app.apply_keyboard_edit(dict(source=ctx['source'],before='',after='c',caret=1,seq=1),'web'))
                self.assertEqual(session.keyboard_field['value'],'c');backend.type_combo.assert_called_with(session.keyboard_field,'c')
                # A site error is spoken, not raised into the keyboard loop.
                from web_assistant import WebError
                backend.type_combo.side_effect=WebError('The page changed. Refresh it before continuing.')
                with patch.object(app,'speak') as spoken:
                    ctx=app.app_context('web')
                    app.apply_keyboard_edit(dict(source=ctx['source'],before='c',after='ca',caret=2,seq=2),'web')
                spoken.assert_called_once_with('The page changed. Refresh it before continuing.')
if __name__=='__main__':unittest.main()


class PageEdgeAndHeadingLineTests(unittest.TestCase):
    """Arrowing through a page, as a screen reader user does (sweep, October 30, 2026)."""
    TEXT='Intro text.\nResults\nLast line'
    def session(self,folder):
        session=WebSession(folder,Mock())
        snapshot={'url':'https://example.com/','title':'Example','text':self.TEXT,
                  'elements':[dict(key='h',role='heading',tag='h2',type='',label='Results',level=2,start=12,end=19)]}
        session.snapshot=snapshot;session.reading.set_text(self.TEXT);session.browse=BrowseCursor(snapshot)
        return session

    def test_a_heading_line_says_its_level_as_the_quick_key_does(self):
        with tempfile.TemporaryDirectory() as folder:
            session=self.session(folder)
            self.assertEqual(session.keyboard('Read:Down'),'Results, heading level 2.')
            session.keyboard('PageTop')
            self.assertEqual(session.keyboard('Quick:h'),'Results, heading level 2.')

    def test_down_on_the_last_line_and_up_on_the_first_say_so(self):
        """Down on the last line read it again with no sign the page had ended."""
        with tempfile.TemporaryDirectory() as folder:
            session=self.session(folder)
            session.keyboard('PageBottom')
            self.assertEqual(session.keyboard('Read:Down'),'Bottom of page. Last line')
            self.assertEqual(session.keyboard('Read:Next'),'Bottom of page. Last line')
            self.assertEqual(session.keyboard('Read:Up'),'Results, heading level 2.')
            self.assertEqual(session.keyboard('Read:Up'),'Intro text.')
            self.assertEqual(session.keyboard('Read:Up'),'Top of page. Intro text.')
            self.assertEqual(session.command('previous line'),'Top of page. Intro text.')
            session.keyboard('PageBottom')
            self.assertEqual(session.command('next line'),'Bottom of page. Last line')


class UnlabeledControlTests(unittest.TestCase):
    def test_an_unlabeled_control_says_its_type_once(self):
        """"Unlabeled combobox, combobox, Red." said the type twice."""
        self.assertEqual(describe({'role':'combobox','tag':'select','label':'Unlabeled combobox','selected_label':'Red'}),'Unlabeled, combobox, Red.')
        self.assertEqual(describe({'role':'button','tag':'button','label':'Unlabeled button'}),'Unlabeled, button.')
        self.assertEqual(describe({'role':'field','tag':'input','type':'text','label':'Unlabeled textbox'}),'Unlabeled, edit.')
        # A real name that happens to start with the word is kept.
        self.assertEqual(describe({'role':'button','tag':'button','label':'Unlabeled items'}),'Unlabeled items, button.')


class FieldListWordsTests(unittest.TestCase):
    def test_everyday_names_for_the_fields_list_open_it(self):
        """"List form fields" searched the page and said the site may have hidden its controls."""
        from test_web_assistant import FakeBackend
        with tempfile.TemporaryDirectory() as folder:
            session=WebSession(folder,FakeBackend());session._refresh()
            for words in ('List form fields.','list form controls','show form fields','show fields'):
                self.assertTrue(session.command(words).startswith('Found '),words)

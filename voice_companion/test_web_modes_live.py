"""App mode, sliders and suggestions in a real browser, on pages modelled on YouTube."""
import time
import unittest
import test_web_live as live

# YouTube's player: the seek bar and the volume panel are divs with role
# slider. The page's own script handles their keys. The page also has
# single-letter shortcuts on the document (K, J, L, M and F), and a status
# area that announces changes.
YOUTUBE_PLAYER = '''<title>Video - YouTube</title><h1>Video title</h1>
<div id="movie_player" class="html5-video-player" tabindex="-1" aria-label="YouTube Video Player">
<div class="ytp-progress-bar-container"><div class="ytp-progress-bar" tabindex="0" role="slider" aria-label="Seek slider"
 aria-valuemin="0" aria-valuemax="245" aria-valuenow="83" aria-valuetext="1:23 of 4:05"></div></div>
<div class="ytp-chrome-bottom"><button class="ytp-play-button ytp-button" aria-keyshortcuts="k" aria-label="Pause (k)">||</button>
<span class="ytp-volume-area"><button class="ytp-mute-button ytp-button" aria-keyshortcuts="m" aria-label="Mute (m)">M</button>
<div class="ytp-volume-panel" role="slider" aria-valuemin="0" aria-valuemax="100" tabindex="0" aria-valuenow="100" aria-valuetext="100% volume" aria-label="Volume"></div></span>
<div class="ytp-time-display"><span class="ytp-time-current">1:23</span> / <span class="ytp-time-duration">4:05</span></div></div>
<div class="ytp-bezel" role="status" aria-live="polite"></div></div>
<label>Comment <input id="comment"></label><p>After the player</p>
<script>
window.log=[];let time=83,volume=100,playing=true;
const seek=document.querySelector('.ytp-progress-bar'),vol=document.querySelector('.ytp-volume-panel'),play=document.querySelector('.ytp-play-button'),bezel=document.querySelector('.ytp-bezel');
const clock=s=>Math.floor(s/60)+':'+String(s%60).padStart(2,'0');
const show=()=>{seek.setAttribute('aria-valuenow',time);seek.setAttribute('aria-valuetext',clock(time)+' of 4:05');document.querySelector('.ytp-time-current').textContent=clock(time);
  vol.setAttribute('aria-valuenow',volume);vol.setAttribute('aria-valuetext',volume+'% volume');play.setAttribute('aria-label',(playing?'Pause':'Play')+' (k)');};
const say=t=>{bezel.textContent=t;};
seek.addEventListener('keydown',e=>{const step={ArrowLeft:-5,ArrowRight:5,ArrowDown:-5,ArrowUp:5,PageDown:-24,PageUp:24}[e.key];
  if(step!==undefined)time=Math.max(0,Math.min(245,time+step));else if(e.key==='Home')time=0;else if(e.key==='End')time=245;else return;
  e.preventDefault();e.stopPropagation();show();});
vol.addEventListener('keydown',e=>{const step={ArrowLeft:-5,ArrowDown:-5,ArrowRight:5,ArrowUp:5}[e.key];
  if(step!==undefined)volume=Math.max(0,Math.min(100,volume+step));else if(e.key==='Home')volume=0;else if(e.key==='End')volume=100;else return;
  e.preventDefault();e.stopPropagation();show();});
document.addEventListener('keydown',e=>{window.log.push(e.key);
  if(e.target.matches('input,textarea'))return;
  if(e.key==='k'||e.key===' '){e.preventDefault();playing=!playing;show();say(playing?'Play':'Pause');}
  else if(e.key==='j'){time=Math.max(0,time-10);show();}
  else if(e.key==='l'){time=Math.min(245,time+10);show();}
  else if(e.key==='m'){say('Muted');}
  else if(e.key==='f'){window.fullscreen=!window.fullscreen;}
  else if(e.key==='ArrowLeft'){e.preventDefault();time=Math.max(0,time-5);show();}});
</script>'''


class WebModeLiveTests(unittest.TestCase):
    setUpClass = classmethod(live.LiveWebTests.setUpClass.__func__)
    tearDownClass = classmethod(live.LiveWebTests.tearDownClass.__func__)
    setUp = live.LiveWebTests.setUp
    element = live.LiveWebTests.element

    def load(self, markup):
        self.page.set_content(markup)
        self.session._refresh(read=False)

    def attribute(self, selector, name):
        return self.page.locator(selector).get_attribute(name)

    def test_tab_and_enter_reach_youtube_seek_and_volume_sliders(self):
        self.load(YOUTUBE_PLAYER)
        self.assertIn('Seek slider, 1:23 of 4:05', self.session.reading.text)
        # Tab onto a slider lets its keys work at once.
        self.assertEqual(self.session.keyboard('Tab'), 'Seek slider, 1:23 of 4:05.')
        self.assertIsNotNone(self.session.slider_field)
        self.assertEqual(self.page.evaluate("document.activeElement.className"), 'ytp-progress-bar')
        self.assertEqual(self.session.keyboard('Slider:Right'), '1:28 of 4:05.')
        self.assertEqual(self.attribute('.ytp-progress-bar', 'aria-valuenow'), '88')
        self.assertEqual(self.session.keyboard('Slider:Left'), '1:23 of 4:05.')
        self.assertEqual(self.session.keyboard('Slider:Prior'), '1:47 of 4:05.')
        self.assertEqual(self.session.keyboard('Slider:Home'), '0:00 of 4:05.')
        self.assertEqual(self.session.keyboard('Slider:End'), '4:05 of 4:05.')
        # Escape goes back to browse mode, on the same slider.
        self.assertEqual(self.session.keyboard('Escape'), 'Browse mode.')
        self.assertIsNone(self.session.slider_field)
        self.assertEqual(self.session.browse.current()['label'], 'Seek slider')
        # The next Escape opens the web browser menu, as it does in page reading.
        import companion as app
        from unittest.mock import patch
        with patch.object(app, 'web_session', self.session):
            self.assertEqual(app.navigation_key('Web:Escape', 'web'), 'web browser menu')
        # Enter on a slider starts using it.
        self.assertIn('Volume, slider, 100% volume', self.session.command('next slider'))
        self.assertIsNone(self.session.slider_field)
        self.assertEqual(self.session.keyboard('Activate'), 'Volume, slider, 100% volume.')
        self.assertEqual(self.session.keyboard('Slider:Down'), '95% volume.')
        self.assertEqual(self.session.keyboard('Slider:Down'), '90% volume.')
        self.assertEqual(self.attribute('.ytp-volume-panel', 'aria-valuenow'), '90')
        with patch.object(app, 'web_session', self.session):
            self.assertEqual(app.navigation_key('Web:Escape', 'web'), 'web key Escape')
        # Tab moves on and leaves the slider.
        self.assertIn('Comment', self.session.keyboard('Tab'))
        self.assertIsNone(self.session.slider_field)
        self.assertEqual(self.session.keyboard_field['label'], 'Comment')

    def test_html_range_input_is_a_slider_too(self):
        self.load('<label>Speed<input type="range" min="0" max="10" value="5"></label><p>End</p>')
        self.assertIn('Speed, slider, 5', self.session.command('next slider'))
        self.assertEqual(self.session.keyboard('Activate'), 'Speed, slider, 5.')
        self.assertEqual(self.session.keyboard('Slider:Right'), '6.')
        self.assertEqual(self.session.keyboard('Slider:End'), '10.')
        self.assertEqual(self.page.locator('input').input_value(), '10')

    def test_app_mode_sends_keys_to_the_page_and_speaks_what_changes(self):
        self.load(YOUTUBE_PLAYER)
        self.session.keyboard('Quick:h')
        # Browse mode keeps letters for page navigation.
        self.session.keyboard('Quick:k');self.assertEqual(self.page.evaluate('window.log'), [])
        self.assertEqual(self.session.keyboard('ToggleAppMode'), 'App mode.')
        self.assertTrue(self.session.app_mode)
        self.assertEqual(self.session.keyboard('Pass:k'), 'Pause.')
        self.assertEqual(self.attribute('.ytp-play-button', 'aria-label'), 'Play (k)')
        self.session.keyboard('Pass:j');self.session.keyboard('Pass:j')
        self.assertEqual(self.attribute('.ytp-progress-bar', 'aria-valuenow'), '63')
        self.session.keyboard('Pass:l');self.session.keyboard('Pass:ArrowLeft')
        self.assertEqual(self.attribute('.ytp-progress-bar', 'aria-valuenow'), '68')
        self.assertEqual(self.session.keyboard('Pass:m'), 'Muted.')
        self.session.keyboard('Pass:f');self.assertTrue(self.page.evaluate('window.fullscreen'))
        self.assertEqual(self.session.keyboard('Pass:Space'), 'Play.')
        # Escape belongs to the page in app mode.
        self.session.keyboard('Pass:Escape')
        self.assertEqual(self.page.evaluate('window.log'), ['k', 'j', 'j', 'l', 'ArrowLeft', 'm', 'f', ' ', 'Escape'])
        # Tab moves the page's own focus, which is spoken.
        self.assertEqual(self.session.keyboard('Pass:Tab'), 'Seek slider, 1:08 of 4:05.')
        self.assertEqual(self.session.keyboard('Pass:ArrowRight'), '1:13 of 4:05.')
        self.assertEqual(self.session.keyboard('Pass:Tab'), 'Pause (k), button.')
        self.assertEqual(self.session.keyboard('Pass:Shift+Tab'), 'Seek slider, 1:13 of 4:05.')
        self.session.keyboard('Pass:Tab')
        for _ in range(3):self.session.keyboard('Pass:Tab')
        self.assertEqual(self.page.evaluate('document.activeElement.id'), 'comment')
        self.assertEqual(self.session.keyboard('Pass:h'), 'h')
        self.assertEqual(self.session.keyboard('Pass:i'), 'i')
        self.assertEqual(self.page.locator('#comment').input_value(), 'hi')
        self.assertEqual(self.session.keyboard('Pass:Backspace'), 'i')
        # Back to browse mode: the cursor is where the keys left off.
        result = self.session.keyboard('ToggleAppMode')
        self.assertTrue(result.startswith('Browse mode. Comment'), result)
        self.assertFalse(self.session.app_mode)
        self.assertEqual(self.session.browse.current()['label'], 'Comment')
        before = list(self.page.evaluate('window.log'))
        self.session.keyboard('Quick:h');self.assertEqual(self.page.evaluate('window.log'), before)
        # A key typed just after leaving app mode does not reach the page.
        self.assertEqual(self.session.keyboard('Pass:k'), '');self.assertEqual(self.page.evaluate('window.log'), before)

    def test_app_mode_starts_on_the_browse_cursor_element_and_voice_commands_work(self):
        self.load(YOUTUBE_PLAYER)
        self.session.command('next slider')
        self.assertEqual(self.session.command('Virtual cursor off.'), 'App mode.')
        self.assertEqual(self.page.evaluate("document.activeElement.className"), 'ytp-progress-bar')
        self.assertEqual(self.session.keyboard('Pass:End'), '4:05 of 4:05.')
        self.assertTrue(self.session.command('Browse mode.').startswith('Browse mode. Seek slider'))
        self.assertEqual(self.session.command('focus mode'), 'App mode.')
        self.assertTrue(self.session.command('virtual cursor on').startswith('Browse mode.'))

    def test_each_site_keeps_its_mode_and_leaving_resets_it(self):
        self.context.route('https://other.test/**', lambda route: route.fulfill(content_type='text/html', body='<title>Other</title><h1>Other site</h1><p>Text</p>'))
        self.load(YOUTUBE_PLAYER)
        self.assertEqual(self.session.keyboard('ToggleAppMode'), 'App mode.')
        self.page.goto('https://fixture.test/watch?v=2');result = self.session._refresh()
        self.assertTrue(self.session.app_mode);self.assertNotIn('Browse mode', result)
        # In app mode a new page is announced but not read aloud.
        self.assertNotIn('Readable text', result)
        self.page.goto('https://other.test/');result = self.session._refresh()
        self.assertTrue(result.startswith('Browse mode. Page loaded: Other'), result);self.assertFalse(self.session.app_mode)
        self.page.goto('https://fixture.test/');result = self.session._refresh()
        self.assertTrue(result.startswith('App mode. Page loaded'), result);self.assertTrue(self.session.app_mode)
        self.session.leave(keep_open=True)
        self.assertFalse(self.session.app_mode_hosts)

    def test_enter_and_tab_start_using_combo_boxes_and_text_boxes(self):
        self.load('<label>Size<select><option>Small</option><option>Large</option></select></label>'
                  '<div role="combobox" aria-label="Color" tabindex="0" aria-expanded="false" aria-controls="colors">Red</div>'
                  '<div id="colors" role="listbox" hidden><div role="option">Red</div></div><label>Name<input></label>')
        self.session.keyboard('Tab');self.assertEqual(self.session.keyboard_field['label'], 'Size')
        self.assertEqual(self.session.keyboard('Down'), 'Large.')
        self.session.keyboard('Tab');self.assertEqual(self.session.keyboard_field['label'], 'Color')
        self.session.keyboard('Tab');self.assertEqual(self.session.keyboard_field['label'], 'Name')
        self.session.keyboard('Escape');self.session.keyboard('PageTop')
        self.session.browse.index = self.session.browse.elements.index(self.element('Size'));self.assertIsNone(self.session.keyboard_field)
        self.session.keyboard('Activate');self.assertEqual(self.session.keyboard_field['label'], 'Size')
        # Insert+Space in a field returns to browse mode and keeps the choice.
        self.assertEqual(self.session.keyboard('ToggleAppMode'), 'Browse mode.')
        self.assertIsNone(self.session.keyboard_field);self.assertFalse(self.session.app_mode)

    def test_youtube_search_says_new_suggestions_once_when_typing_pauses(self):
        self.load(live.YOUTUBE_SEARCH)
        self.session.keyboard('Quick:e');self.assertEqual(self.session.keyboard_field['label'], 'Search')
        for text in ('c', 'ca', 'cat'):
            self.session.keyboard_field['value'] = text;self.assertEqual(self.session.combo_typed(), '')
            self.assertEqual(self.session.suggestions_notice(), '')  # Typing: no notice for each letter.
        self.page.wait_for_function("document.querySelectorAll('[role=option]').length===3")
        later = time.monotonic() + 1
        self.assertEqual(self.session.suggestions_notice(later), 'New suggestions available.')
        self.assertEqual(self.session.suggestions_notice(later), '')
        self.assertEqual(self.session.keyboard('Down'), 'cats, 1 of 3.')
        self.assertEqual(self.session.suggestions_notice(later), '')
        self.session.keyboard_field['value'] = 'cat v';self.session.combo_typed()
        self.page.wait_for_function("document.querySelectorAll('[role=option]').length===1")
        self.assertEqual(self.session.suggestions_notice(time.monotonic() + 1), 'New suggestions available.')


if __name__ == '__main__':
    unittest.main()

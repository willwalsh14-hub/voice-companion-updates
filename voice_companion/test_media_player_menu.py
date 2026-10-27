"""Media player: what is said when a file opens, the player menu, and seeking."""
import tempfile
import unittest
from contextlib import ExitStack
from pathlib import Path
from unittest.mock import patch

import companion as app
from media_hub import MediaHub


class PlayerLikeMediaPlayer:
    """Behaves like media_player.MediaPlayer without a browser.

    Like the real player: nothing works until a file is open, seeking clamps to
    the seekable range and returns the new position (None when the media cannot
    seek), status() reports a None duration for streams, and stop rewinds.
    """
    def __init__(self, duration=245.0, seekable=True, fail_open=None):
        self.kind=None;self.position=0.0;self.duration=duration;self.paused=True
        self.seekable=seekable;self.fail_open=fail_open;self.calls=[]
    def open(self,url,title,kind):
        self.calls.append(('open',title,kind))
        if self.fail_open is not None:
            self.kind=None;raise self.fail_open
        self.kind=kind;self.position=0.0;self.paused=False
    def status(self):
        if not self.kind:raise ValueError('Open a media file first.')
        return {'paused':self.paused,'position':self.position,'duration':self.duration}
    def control(self,action,seconds=15,position=None):
        self.calls.append((action,seconds,position))
        if not self.kind:return None
        if action=='toggle':
            if not self.paused:self.paused=True;return True
            self.paused=False;return False
        if action=='stop':self.paused=True;self.position=0.0;return 0
        if action=='pause':self.paused=True;return self.position
        if action=='play':self.paused=False;return self.position
        if action=='seek':
            if not self.seekable:return None
            end=self.duration if self.duration is not None else self.position+600
            target=self.position+seconds if position is None else position
            self.position=max(0.0,min(end,target));return self.position
        return self.position
    def adjust(self,setting,value):return value
    def set_ducked(self,ducked,factor=0.18):pass
    def close(self):self.kind=None


class MediaPlayerMenuTests(unittest.TestCase):
    def setUp(self):
        self.stack=ExitStack();self.addCleanup(self.stack.close)
        self.root=Path(self.stack.enter_context(tempfile.TemporaryDirectory()))
        for name,value in {'APP':self.root,'APP_WINDOW':None,'TASK_NAV':None,'document':None,'FILE_MANAGER':None,
                           'FILE_READER':None,'MEDIA_HUB':None,'SETTINGS_PANEL':None,'NAMED_LIST':None,
                           'KEYBOARD_DIRTY':{},'EDIT_ACK':{},'INPUT_MODE':'mixed','PREFERENCES':dict(app.PREFERENCES)}.items():
            self.stack.enter_context(patch.object(app,name,value))
        app.PREFERENCES['keep_audio_playing']=False
        self.said=[]
        self.stack.enter_context(patch.object(app,'speak',side_effect=lambda text,*a,**k:self.said.append(text)))
        self.stack.enter_context(patch.object(app,'sync_app_context'))
        self.player=PlayerLikeMediaPlayer()
        app.MEDIA_HUB=MediaHub(self.root/'media',player=self.player)
        self.song=self.root/'Taps.mp3';self.song.write_bytes(b'audio')

    def key(self,key,mode):
        """Press a key the way the app window does: key, then its request."""
        request=app.navigation_key(key,mode)
        return mode if request is None else app.handle(request,mode)

    def open_from_file_manager(self):
        mode=app.task_navigation().selected_file(self.song)
        self.assertEqual(mode,'local_media')
        return mode

    def test_opening_a_file_says_what_plays_and_how_to_reach_the_menu(self):
        self.open_from_file_manager()
        self.assertEqual(self.said[-1],'Playing Taps.mp3. Press Alt for the player menu.')
        self.assertNotIn('Open a media file',self.said[-1])
        self.assertEqual(self.player.kind,'local')

    def test_arrows_control_playback_until_the_menu_is_opened(self):
        mode=self.open_from_file_manager()
        mode=self.key('Right',mode)
        self.assertEqual(self.player.position,15)
        self.assertEqual(self.said[-1],'Moved forward 15 seconds. 15 seconds of 4 minutes 5 seconds.')
        self.assertEqual(mode,'local_media')

    def test_only_alt_opens_the_menu_while_playing(self):
        # Will, October 2026: the menu appeared at random while a file played. Only Alt opens it;
        # Enter plays or pauses, Home goes to the start, and other keys never show the menu.
        mode=self.open_from_file_manager()
        for key in ('Enter','Tab','Home','End','Letter:p','Down','Up'):
            mode=self.key(key,mode)
            self.assertEqual(mode,'local_media')
            self.assertFalse(any(text.startswith(('Player menu.','Media player menu.')) for text in self.said),(key,self.said[-1]))
        self.assertFalse(app.task_navigation().player_menu)
        self.assertIsNone(app.menu_state('local_media'))
        self.assertTrue(self.player.paused)              # Enter paused it
        self.player.position=60
        self.key('Home',mode);self.assertEqual(self.player.position,0);self.assertEqual(self.said[-1],'Start of file.')
        self.assertEqual(app.handle('next',mode),mode);self.assertEqual(self.said[-1],'Press Alt for the player menu.')

    def test_escape_while_playing_leaves_the_player_in_one_press(self):
        mode=self.key('Escape',self.open_from_file_manager())
        self.assertEqual(mode,'media_menu')
        self.assertIn(('stop',15,None),self.player.calls)

    def test_alt_opens_the_menu_without_stopping_playback(self):
        for opener in ('Alt',):
            with self.subTest(opener=opener):
                mode=self.open_from_file_manager()
                mode=self.key(opener,mode)
                self.assertEqual(mode,'local_media')
                self.assertEqual(self.said[-1],'Media player menu. Open a media file (O), 1 of 10. Ctrl+O.')
                self.assertFalse(self.player.paused)
                self.assertNotIn(('stop',15,None),self.player.calls)
                # Arrows now move through the menu instead of changing the volume.
                mode=self.key('Down',mode)
                self.assertTrue(self.said[-1].startswith('Play or pause (P), 2 of 10'),self.said[-1])
                mode=self.key('Up',mode)
                self.assertTrue(self.said[-1].startswith('Open a media file (O), 1 of 10'),self.said[-1])
                self.assertEqual(self.player.position,0)

    def test_alt_closes_the_menu_and_escape_in_the_menu_returns_to_media(self):
        mode=self.key('Alt',self.open_from_file_manager())
        mode=self.key('Alt',mode)
        self.assertEqual(mode,'local_media')
        self.assertEqual(self.said[-1],'Menu closed. Taps.mp3 is playing. Press Alt for the menu.')
        self.key('Right',mode);self.assertEqual(self.player.position,15)
        mode=self.key('Alt',mode);self.assertEqual(mode,'local_media')
        mode=self.key('Escape',mode)
        self.assertEqual(mode,'media_menu')
        self.assertTrue(self.said[-1].startswith('Media menu.'))
        self.assertIn(('stop',15,None),self.player.calls)

    def test_enter_on_menu_item_runs_it_and_keeps_menu(self):
        mode=self.key('Alt',self.open_from_file_manager())
        mode=self.key('Down',mode);mode=self.key('Enter',mode)
        self.assertEqual(mode,'local_media');self.assertTrue(self.player.paused)
        self.assertEqual(self.said[-1],'Paused.')
        mode=self.key('Down',mode);self.assertTrue(self.said[-1].startswith('Stop (S), 3 of 10'),self.said[-1])

    def test_media_player_from_media_menu_opens_with_menu_and_working_arrows(self):
        mode=app.handle('media player','media_menu')
        self.assertEqual(mode,'local_media')
        self.assertEqual(self.said[-1],'Media player. Open a media file (O), 1 of 10. Ctrl+O.')
        self.key('Down',mode)
        self.assertTrue(self.said[-1].startswith('Play or pause (P), 2 of 10'),self.said[-1])
        self.assertEqual(self.key('Escape',mode),'media_menu')

    def test_voice_can_open_and_close_the_player_menu(self):
        mode=self.open_from_file_manager()
        self.assertEqual(app.handle('Player menu.',mode),'local_media')
        self.assertTrue(self.said[-1].startswith('Media player menu. Open a media file'))
        self.assertEqual(app.handle('close menu',mode),'local_media')
        self.assertTrue(self.said[-1].startswith('Menu closed.'))

    def test_seek_names_the_jump_and_the_new_place_and_clamps_at_each_end(self):
        mode=self.open_from_file_manager()
        self.player.position=83
        self.key('Right',mode)
        self.assertEqual(self.said[-1],'Moved forward 15 seconds. 1 minute 38 seconds of 4 minutes 5 seconds.')
        self.key('Left',mode)
        self.assertEqual(self.said[-1],'Moved back 15 seconds. 1 minute 23 seconds of 4 minutes 5 seconds.')
        self.player.position=5
        self.key('Left',mode)
        self.assertEqual(self.player.position,0)
        self.assertEqual(self.said[-1],'Moved back to the start of the track.')
        self.key('Left',mode)
        self.assertEqual(self.said[-1],'Moved back to the start of the track.')
        self.player.position=240
        self.key('Right',mode)
        self.assertEqual(self.player.position,245)
        self.assertEqual(self.said[-1],'Moved forward to the end of the track.')

    def test_spoken_seek_in_player_uses_its_own_interval(self):
        mode=self.open_from_file_manager()
        self.assertEqual(app.handle('Forward 2 minutes.',mode),mode)
        self.assertEqual(self.player.position,120)
        self.assertEqual(self.said[-1],'Moved forward 2 minutes. 2 minutes of 4 minutes 5 seconds.')
        app.handle('back 30 seconds',mode)
        self.assertEqual(self.said[-1],'Moved back 30 seconds. 1 minute 30 seconds of 4 minutes 5 seconds.')
        app.handle('rewind',mode)
        self.assertEqual(self.said[-1],'Moved back 15 seconds. 1 minute 15 seconds of 4 minutes 5 seconds.')

    def test_stream_without_length_and_media_that_cannot_seek(self):
        self.player.duration=None
        mode=self.open_from_file_manager()
        self.key('Right',mode)
        self.assertEqual(self.said[-1],'Moved forward 15 seconds. Now at 15 seconds.')
        self.player.seekable=False
        self.key('Right',mode)
        self.assertEqual(self.said[-1],'This file cannot seek.')

    def test_zero_length_file_reports_end_without_error(self):
        self.player.duration=0.0
        mode=self.open_from_file_manager()
        self.key('Right',mode)
        self.assertEqual(self.said[-1],'Moved forward to the end of the track.')
        self.key('Left',mode)
        self.assertEqual(self.said[-1],'Moved back to the start of the track.')
        app.handle('media status',mode)
        self.assertEqual(self.said[-1],'Taps.mp3. Playing. Position 0 seconds.')

    def test_seek_without_a_file_says_how_to_open_one(self):
        mode=app.handle('media player','media_menu')
        app.handle('forward 15 seconds',mode)
        self.assertEqual(self.said[-1],'Open a media file first.')

    def test_status_uses_minutes_and_length(self):
        mode=self.open_from_file_manager();self.player.position=83.4
        app.handle('media status',mode)
        self.assertEqual(self.said[-1],'Taps.mp3. Playing. Position 1 minute 23 seconds of 4 minutes 5 seconds.')

    def test_file_that_fails_to_open_is_reported_and_stays_in_file_manager(self):
        for error in (OSError('This audio format could not start in the player.'),RuntimeError('Browser closed')):
            with self.subTest(error=type(error).__name__):
                self.player.fail_open=error
                with self.assertRaises(OSError) as raised:
                    app.task_navigation().selected_file(self.song)
                self.assertTrue(str(raised.exception).startswith('This'),str(raised.exception))
                self.assertNotIn('Browser closed',str(raised.exception))

    def test_file_manager_poll_reports_a_failed_media_file(self):
        from file_manager import FileManager
        from test_file_manager import FakeFiles
        app.FILE_MANAGER=FileManager(FakeFiles(self.root))
        app.FILE_MANAGER.opened=self.song
        self.player.fail_open=RuntimeError('Browser closed')
        self.assertEqual(app.poll_file_manager('files'),'files')
        self.assertEqual(self.said[-1],'This media file could not play. Choose another file.')

    def browse_to_song(self):
        """Open the song the way a user does: File manager, arrows, Enter."""
        from file_manager import FileManager
        from test_file_manager import FakeFiles,settle
        music=self.root/'Music';music.mkdir()
        for name in ('Anthem.mp3','Reveille.mp3','Taps.mp3','Zulu.mp3'):(music/name).write_bytes(b'audio')
        self.song=music/'Taps.mp3'
        app.FILE_MANAGER=manager=FileManager(FakeFiles(self.root))
        manager.refresh(str(music));settle(manager)
        mode='files'
        for _ in range(2):mode=self.key('Down',mode)
        self.assertTrue(self.said[-1].startswith('Taps.mp3'),self.said[-1])
        mode=self.key('Enter',mode);settle(manager)
        mode=app.poll_file_manager(mode)
        self.assertEqual(mode,'local_media')
        self.assertEqual(self.said[-1],'Playing Taps.mp3. Press Alt for the player menu.')
        return manager,mode

    def test_escape_in_menu_returns_to_file_manager_on_the_same_file(self):
        manager,mode=self.browse_to_song()
        mode=self.key('Escape',mode)
        self.assertEqual(mode,'files')
        self.assertIs(app.FILE_MANAGER,manager)
        self.assertEqual(manager.location,str(self.song.parent))
        self.assertEqual(self.said[-1],'File manager. '+manager.say_current())
        self.assertTrue(self.said[-1].startswith('File manager. Taps.mp3'),self.said[-1])
        self.assertIn('3 of 4.',self.said[-1])
        self.assertIn(('stop',15,None),self.player.calls)
        # Down Arrow now moves on in that folder.
        self.key('Down',mode);self.assertTrue(self.said[-1].startswith('Zulu.mp3'),self.said[-1])

    def test_keep_audio_playing_returns_to_file_manager_without_stopping(self):
        app.PREFERENCES['keep_audio_playing']=True
        manager,mode=self.browse_to_song()
        mode=self.key('Escape',mode)
        self.assertEqual(mode,'files')
        self.assertNotIn(('stop',15,None),self.player.calls)
        self.assertFalse(self.player.paused)

    def test_escape_climbs_from_the_song_folder_to_the_media_menu(self):
        # Will, October 2026: Escape returns to the folder the song came from. Each press
        # goes up one level, out of the drive list to the Media menu, then the main menu.
        from file_manager import FileManager
        from test_file_manager import FakeFiles,settle
        music=self.root/'Music';music.mkdir();(music/'Anthem.mp3').write_bytes(b'audio');(music/'Taps.mp3').write_bytes(b'audio')
        mode=app.handle('media player','media_menu')
        with patch('file_manager.FileManager',side_effect=lambda:FileManager(FakeFiles(self.root))):
            mode=app.handle(app.navigation_key('Ctrl+O',mode),mode)
        self.assertEqual(mode,'files')
        manager=app.FILE_MANAGER
        def step(key):
            nonlocal mode
            mode=self.key(key,mode);settle(manager);mode=app.poll_file_manager(mode)
        settle(manager);step('Enter')
        self.assertEqual(manager.location,str(self.root))
        manager.command('Music');settle(manager)
        manager.command('Taps.mp3');step('Enter')
        self.assertEqual(mode,'local_media')
        step('Escape')
        self.assertEqual((mode,manager.location),('files',str(music)))
        self.assertTrue(self.said[-1].startswith('Back to Music. Taps.mp3'),self.said[-1])
        # Another song from the same folder still plays.
        step('Up');step('Enter')
        self.assertEqual((mode,self.said[-1]),('local_media','Playing Anthem.mp3. Press Alt for the player menu.'))
        step('Escape');self.assertEqual((mode,manager.location),('files',str(music)))
        step('Escape');self.assertEqual((mode,manager.location),('files',str(self.root)))
        step('Escape');self.assertEqual((mode,manager.location),('files',None))
        step('Escape');self.assertEqual(mode,'media_menu')
        self.said.clear();step('Escape');self.assertEqual(mode,'awake')
        self.assertEqual(self.said.count(app.MAIN_MENU_PROMPT),1,self.said)

    def test_player_after_media_menu_forgets_file_manager_origin(self):
        manager,mode=self.browse_to_song()
        mode=self.key('Escape',mode)
        self.assertEqual(mode,'files')
        mode=app.handle('media menu',mode);mode=app.handle('media player',mode)
        self.assertEqual(self.key('Escape',mode),'media_menu')

    def test_podcast_seek_names_the_jump_and_place(self):
        self.player.kind='podcast';self.player.duration=3600.0;self.player.position=600
        self.assertEqual(app.MEDIA_HUB.command('fast forward one hour and 20 minutes'),'Moved forward to the end of the episode.')
        self.player.position=600
        self.assertEqual(app.MEDIA_HUB.command('forward 30 seconds'),'Moved forward 30 seconds. 10 minutes 30 seconds of 1 hour.')
        self.assertEqual(app.MEDIA_HUB.command('rewind one hour'),'Moved back to the start of the episode.')
        self.assertEqual(self.player.position,0)

    def test_spoken_time(self):
        from media_player import spoken_time
        for seconds,words in ((0,'0 seconds'),(1,'1 second'),(59.6,'1 minute'),(61,'1 minute 1 second'),
                              (245,'4 minutes 5 seconds'),(3600,'1 hour'),(3725,'1 hour 2 minutes 5 seconds'),
                              (7200,'2 hours'),(-3,'0 seconds'),(None,'0 seconds'),(float('nan'),'0 seconds')):
            with self.subTest(seconds=seconds):self.assertEqual(spoken_time(seconds),words)


if __name__=='__main__':unittest.main()

import json
import tempfile
import threading
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock,patch
import companion
from speech_controls import request

class SpeechControlTests(unittest.TestCase):
    def setUp(self):
        self.windows=Mock();self.windows.Voice.GetDescription.return_value='Windows voice'
        self.espeak=Mock(enabled=False);self.ai=Mock(enabled=False)
        for name,value in dict(voice=self.windows,ESPEAK_SPEECH=self.espeak,AI_SPEECH=self.ai,
            TEXT_MODE=False,APP_WINDOW=None,MEDIA_HUB=None,SYNTH_PICK_INDEX=None,VOICE_PICK_INDEX=None,
            VOICE_PICK_ORIGINAL=None,VOICE_PICK_ENGINE=None,SPEECH_RATE=0,SPEECH_VOLUME=50,INPUT_MODE='mixed').items():
            p=patch.object(companion,name,value,create=True);p.start();self.addCleanup(p.stop)
        p=patch.object(companion,'save_speech_settings');self.saved=p.start();self.addCleanup(p.stop)

    def test_relative_commands_reach_every_synthesizer_in_every_area(self):
        for engine in ('windows','espeak','ai'):
            self.espeak.enabled=engine=='espeak';self.ai.enabled=engine=='ai'
            for mode in ('awake','document','email_draft','mailbox','web','note','media','help','tutorial'):
                for phrase,rate,volume in (('faster',1,50),('slower',-1,50),('louder',0,60),('quieter',0,40),('softer',0,40)):
                    companion.SPEECH_RATE=0;companion.SPEECH_VOLUME=50
                    self.assertEqual(companion.handle(phrase,mode),mode)
                    self.assertEqual((companion.SPEECH_RATE,companion.SPEECH_VOLUME),(rate,volume))
                    if engine!='windows':
                        selected=self.espeak if engine=='espeak' else self.ai
                        self.assertEqual(selected.speak.call_args.args[1:],(rate,volume))
                    else:
                        self.assertEqual(self.windows.Rate if rate else companion.SPEECH_RATE,rate)
                        if volume!=50:self.assertEqual(self.windows.Volume,volume)

    def test_direct_commands_keep_current_synthesizer_and_unrelated_setting(self):
        for engine in (self.espeak,self.ai):
            self.espeak.enabled=engine is self.espeak;self.ai.enabled=engine is self.ai
            for phrase,value in (('speed 1',1),('speed two',2),('rate 1',1),('rate two',2),
                                 ('set speech rate to minus two',-2),('set speed to 3',3),('rate -1',-1)):
                companion.SPEECH_VOLUME=50
                companion.handle(phrase,'tutorial')
                self.assertEqual(companion.SPEECH_RATE,value)
                self.assertEqual(engine.speak.call_args.args[1:],(value,50))
                self.assertTrue(engine.enabled)
            for phrase,value in (('volume 5',5),('volume twenty five',25),('set volume to 60',60),
                                 ('speech volume 75 percent',75),('volume one hundred',100),('volume 0',0)):
                companion.handle(phrase,'help')
                self.assertEqual(companion.SPEECH_VOLUME,value)
                self.assertEqual(companion.SPEECH_RATE,-1)
                self.assertEqual(engine.speak.call_args.args[1:],(-1,value))

    def test_invalid_direct_values_do_not_change_or_save_settings(self):
        for phrase in ('rate 11','speed minus eleven','volume 101','volume minus one','rate 1.5','volume banana'):
            self.saved.reset_mock();companion.handle(phrase,'document')
            self.assertEqual((companion.SPEECH_RATE,companion.SPEECH_VOLUME),(0,50))
            self.saved.assert_not_called()

    def test_relative_bounds_and_aliases(self):
        companion.SPEECH_RATE=10;companion.handle('talk faster','help');self.assertEqual(companion.SPEECH_RATE,10)
        companion.SPEECH_RATE=-10;companion.handle('talk slower','tutorial');self.assertEqual(companion.SPEECH_RATE,-10)
        for alias in ('softer','speak softer','talk softer','quieter','speak quieter','volume down'):
            companion.SPEECH_VOLUME=5;companion.handle(alias,'awake');self.assertEqual(companion.SPEECH_VOLUME,0)
        companion.handle('volume up','awake');self.assertEqual(companion.SPEECH_VOLUME,10)

    def test_speech_settings_fast_routing_keeps_font_commands_separate(self):
        for phrase in ('speed two','rate minus two','volume five','softer','set speech volume to fifty'):
            self.assertTrue(companion.fast_command_request(phrase,'document'))
        for phrase in ('set font size 16','set size 16','line spacing 2','type speed two'):
            self.assertIsNone(request(phrase))

    def test_settings_do_not_replace_tutorial_or_help_session(self):
        tutorial=Mock();help_session=Mock()
        with patch.object(companion,'tutorial_session',tutorial),patch.object(companion,'help_session',help_session):
            companion.handle('speed two','tutorial');companion.handle('softer','help')
            tutorial.process.assert_not_called();help_session.process.assert_not_called()
            self.assertIs(companion.tutorial_session,tutorial);self.assertIs(companion.help_session,help_session)

    def test_ai_request_speed_and_pcm_volume_are_applied(self):
        import ai_speech
        response=Mock();response.__enter__=Mock(return_value=response);response.__exit__=Mock(return_value=False)
        response.read.return_value=b'\x00\x00'
        with patch('ai_speech.urllib.request.urlopen',return_value=response) as call:
            ai_speech.synthesize('Hello',2,key='test-key',voice='coral')
        self.assertAlmostEqual(json.loads(call.call_args.args[0].data)['speed'],1.15)
        blocks=[]
        class Output:
            def __init__(self,**options): pass
            def __enter__(self):return self
            def __exit__(self,*args):pass
            def write(self,data):blocks.append(data)
        engine=ai_speech.AISpeech(Mock(),output_factory=Output)
        try:
            import array
            engine._play(array.array('h',[1000,-1000]).tobytes(),engine.generation,5)
            self.assertEqual(list(array.array('h',b''.join(blocks))),[50,-50])
        finally:engine.close()

    def test_espeak_direct_speed_is_passed_to_local_generator(self):
        import io,wave,espeak_speech
        buffer=io.BytesIO()
        with wave.open(buffer,'wb') as wav:
            wav.setnchannels(1);wav.setsampwidth(2);wav.setframerate(24000);wav.writeframes(b'\x00\x00'*4)
        with patch('espeak_speech.subprocess.run',return_value=SimpleNamespace(stdout=buffer.getvalue())) as run:
            espeak_speech.synthesize('Hello',2,program='engine.exe')
        argv=run.call_args.args[0]
        self.assertEqual(argv[argv.index('-s')+1],str(round(175*1.15)))

    def test_four_and_common_recognition_spellings_are_numeric_only_in_commands(self):
        for word,value in (('four',4),('for',4),('fore',4),('to',2),('too',2),('won',1),('ate',8),('oh',0)):
            for name in ('rate','speed','volume'):
                self.assertEqual(request(name+' '+word),(('volume' if name=='volume' else 'rate'),'set',value))
                companion.handle(name+' '+word,'awake')
                self.assertEqual(companion.SPEECH_VOLUME if name=='volume' else companion.SPEECH_RATE,value)
        self.assertIsNone(request('type for'))
        self.assertIsNone(request('set font size for'))
        self.assertEqual(request('rate minus for'),('rate','set',-4))
        self.assertEqual(request('volume forty for percent'),('volume','set',44))
        self.assertEqual(request('volume hundred'),('volume','set',100))

    def test_all_valid_rate_and_volume_numbers_digits_and_words(self):
        small='zero one two three four five six seven eight nine ten eleven twelve thirteen fourteen fifteen sixteen seventeen eighteen nineteen'.split()
        tens={20:'twenty',30:'thirty',40:'forty',50:'fifty',60:'sixty',70:'seventy',80:'eighty',90:'ninety'}
        def words(n):
            if n<0:return 'minus '+words(-n)
            if n<20:return small[n]
            if n==100:return 'one hundred'
            return tens[n//10*10]+(' '+small[n%10] if n%10 else '')
        with patch.object(companion,'speak'):
            for n in range(-10,11):
                for name in ('rate','speed'):
                    for value in (str(n),words(n)):
                        with self.subTest(name=name,value=value):
                            companion.handle(name+' '+value,'document')
                            self.assertEqual(companion.SPEECH_RATE,n)
            for n in range(101):
                for value in (str(n),words(n),str(n)+'%',words(n)+' percent'):
                    with self.subTest(value=value):
                        companion.handle('volume '+value,'email_draft')
                        self.assertEqual(companion.SPEECH_VOLUME,n)

    def test_whole_decimal_transcripts_punctuation_and_fraction_rejection(self):
        for phrase,expected in (('Rate, four.',('rate','set',4)),('speed 4.0',('rate','set',4)),
             ('rate four point zero',('rate','set',4)),('volume 50.0%',('volume','set',50)),
             ('rate \u22124',('rate','set',-4)),('volume twenty-four per cent',('volume','set',24))):
            self.assertEqual(request(phrase),expected)
        for phrase in ('rate 4.5','volume fifty point five','rate point four','volume infinity','volume for apples'):
            self.assertIsNone(request(phrase)[2])
        self.assertFalse(companion.fast_command_request('rate apples','document'))
        self.assertTrue(companion.fast_command_request('rate for','document'))

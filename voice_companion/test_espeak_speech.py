import io
import tempfile
import unittest
import wave
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch
import companion
from espeak_speech import synthesize

class ESpeakTests(unittest.TestCase):
    def test_local_unicode_speech_uses_stdin_and_fixed_english_voice(self):
        data=io.BytesIO()
        with wave.open(data,'wb') as wav:
            wav.setnchannels(1); wav.setsampwidth(2); wav.setframerate(24000)
            wav.writeframes(b'\x01\x00'*240)
        with patch('espeak_speech.subprocess.run',return_value=SimpleNamespace(stdout=data.getvalue())) as run:
            self.assertEqual(synthesize('Hello José; & goodbye',0,'en-gb',program='engine.exe'),b'\x01\x00'*240)
        args=run.call_args.args[0]
        self.assertIn('en-gb',args); self.assertNotIn('Hello', ' '.join(args))
        self.assertEqual(run.call_args.kwargs['input'],'Hello José; & goodbye'.encode())
        self.assertEqual(run.call_args.kwargs['timeout'],8)
        with self.assertRaises(ValueError): synthesize('Bonjour',voice='fr',program='engine.exe')

    def test_bad_audio_fails_to_allow_windows_fallback(self):
        with patch('espeak_speech.subprocess.run',return_value=SimpleNamespace(stdout=b'bad')):
            with self.assertRaises((wave.Error,EOFError)): synthesize('Hello',program='engine.exe')

    def setUp(self):
        self.voice=Mock(); self.token=Mock(); self.token.GetDescription.return_value='Windows voice'
        self.voice.Voice=self.token; self.voice.GetVoices.return_value=Mock(Count=1,Item=lambda i:self.token)
        self.espeak=Mock(enabled=False); self.ai=Mock(enabled=False)
        for key,value in dict(voice=self.voice,ESPEAK_SPEECH=self.espeak,AI_SPEECH=self.ai,
                              TEXT_MODE=False,SYNTH_PICK_INDEX=None,VOICE_PICK_ENGINE=None,
                              VOICE_PICK_INDEX=None,VOICE_PICK_ORIGINAL=None,VOICE_PICK_CONFIRM=False,
                              ESPEAK_VOICE_NAME='en-us',AI_VOICE_NAME='coral',APP_WINDOW=None).items():
            p=patch.object(companion,key,value,create=True);p.start();self.addCleanup(p.stop)
        self.save_real=companion.save_speech_settings
        p=patch.object(companion,'save_speech_settings');self.saved=p.start();self.addCleanup(p.stop)

    def test_two_layer_menu_english_only_confirm_and_cancel(self):
        self.assertEqual(companion.handle('voice list','email_draft'),'email_draft')
        self.assertFalse(self.espeak.enabled)
        companion.handle('next','email_draft')
        self.assertIn('eSpeak NG',self.voice.Speak.call_args.args[0])
        companion.handle('confirm that','email_draft')
        self.assertTrue(self.espeak.enabled)
        self.assertEqual(len(companion.voice_choices('espeak')),6)
        companion.handle('next','email_draft')
        self.assertEqual(companion.ESPEAK_VOICE_NAME,'en-gb')
        self.saved.assert_not_called()
        companion.handle('that one','email_draft');self.saved.assert_called_once()
        companion.handle('voice list','email_draft');companion.handle('okay','email_draft')
        self.assertFalse(self.espeak.enabled)
        companion.handle('cancel','email_draft')
        self.assertTrue(self.espeak.enabled);self.assertEqual(companion.ESPEAK_VOICE_NAME,'en-gb')

    def test_controls_pause_resume_and_purge_share_local_player(self):
        self.espeak.enabled=True
        companion.speak('Your document')
        self.espeak.speak.assert_called_once()
        companion.handle('shut the fuck up','document')
        self.espeak.pause.assert_called_once()
        self.espeak.interrupt.assert_not_called()
        companion.handle('resume speaking','document')
        self.espeak.resume.assert_called_once()
        companion.interrupt_speech();self.espeak.interrupt.assert_called_once()

    def test_cancel_at_synthesizer_layer_never_changes_engine(self):
        self.espeak.enabled=True
        companion.handle('list voices','web');companion.handle('previous','web')
        companion.handle('cancel synthesizer','web')
        self.assertTrue(self.espeak.enabled);self.saved.assert_not_called()

    def test_direct_voice_names_and_keyboard_echo(self):
        companion.handle('use voice eSpeak UK English','awake')
        self.assertTrue(self.espeak.enabled);self.assertFalse(self.ai.enabled)
        self.assertEqual(companion.ESPEAK_VOICE_NAME,'en-gb')
        self.espeak.speak.reset_mock();companion.speak_keyboard_feedback('a')
        self.espeak.speak.assert_called_once_with('a',companion.SPEECH_RATE,companion.SPEECH_VOLUME)

    def test_picker_available_inside_help_and_tutorial(self):
        for mode in ('help','tutorial'):
            companion.handle('voice list',mode);companion.handle('next',mode)
            companion.handle('okay',mode);companion.handle('okay',mode)
            self.assertTrue(self.espeak.enabled)

    def test_settings_persist_local_engine_and_voice(self):
        import json
        self.espeak.enabled=True
        companion.ESPEAK_VOICE_NAME='en-gb'
        with tempfile.TemporaryDirectory() as folder, patch.object(companion,'APP',Path(folder)):
            self.save_real()
            saved=json.loads(Path(folder,'speech-settings.json').read_text())
            self.assertTrue(saved['espeak_enabled']);self.assertEqual(saved['espeak_voice'],'en-gb')
            self.assertFalse(saved['ai_enabled'])

    def test_startup_waits_for_selected_player_not_only_sapi(self):
        with patch.object(companion,'speech_busy',side_effect=[True,True,False]),patch('companion.time.sleep') as pause:
            self.assertTrue(companion.wait_for_speech(15000))
            self.assertEqual(pause.call_count,2)


class ESpeakSetupTests(unittest.TestCase):
    def test_download_integrity_failure_never_executes_installer(self):
        from prepare_espeak import prepare
        with tempfile.TemporaryDirectory() as folder, patch('prepare_espeak.shutil.copy2', side_effect=lambda src,dst: Path(dst).write_bytes(b'wrong download')), patch('prepare_espeak.urllib.request.urlretrieve') as download, patch('prepare_espeak.subprocess.run') as run:
            download.side_effect=lambda url,path: Path(path).write_bytes(b'wrong download')
            with self.assertRaises(ValueError): prepare(Path(folder,'espeak'))
            run.assert_not_called()

    def test_private_runtime_extraction_handles_paths_with_spaces(self):
        import prepare_espeak
        with tempfile.TemporaryDirectory(prefix='User name ') as folder:
            target=Path(folder,'Local eSpeak')
            def run(argv,**options):
                if argv[0]=='msiexec.exe':
                    extracted=Path(argv[-1].split('=',1)[1])/'Program Files'/'eSpeak NG'
                    extracted.mkdir(parents=True)
                    (extracted/'espeak-ng.exe').write_bytes(b'exe')
                    (extracted/'espeak-ng-data').mkdir()
                    (extracted/'espeak-ng-data'/'phontab').write_bytes(b'data')
                data=io.BytesIO()
                with wave.open(data,'wb') as wav:
                    wav.setnchannels(1); wav.setsampwidth(2); wav.setframerate(24000)
                    wav.writeframes(b'\x01\x00'*240)
                return SimpleNamespace(returncode=0,stdout=data.getvalue())
            with patch.object(prepare_espeak,'os',SimpleNamespace(name='posix')), patch('prepare_espeak.urllib.request.urlretrieve',side_effect=lambda url,path: Path(path).write_bytes(b'msi')), patch('prepare_espeak.hashlib.sha256') as digest, patch('prepare_espeak.subprocess.run',side_effect=run):
                digest.return_value.hexdigest.return_value=prepare_espeak.SHA256
                prepare_espeak.prepare(target)
            self.assertTrue((target/'espeak-ng.exe').is_file())
            self.assertTrue((target/'espeak-ng-data'/'phontab').is_file())
            self.assertIn('1.52.0',(target/'SOURCE.txt').read_text())


class ESpeakRuntimeRegressionTests(unittest.TestCase):
    def test_bundled_variants_remain_valid_without_flooding_the_picker(self):
        from espeak_speech import VOICES, ALL_VOICES
        self.assertEqual(VOICES,(('US English','en-us'),('UK English','en-gb'),('US English Male','en-us+m1'),('UK English Male','en-gb+m1'),('US English Female','en-us+f1'),('UK English Female','en-gb+f1')))
        codes={code for _,code in ALL_VOICES}
        folder=Path(__file__).parent/'espeak/espeak-ng-data/voices/!v'
        for variant in folder.iterdir():
            if variant.is_file():
                for language in ('en-us','en-gb'):
                    self.assertIn(language+'+'+variant.name,codes)
        self.assertEqual(len(codes),len(ALL_VOICES))
        self.assertTrue(all(code.split('+')[0] in ('en-us','en-gb') for code in codes))

    def test_named_variant_uses_absolute_private_data_path(self):
        data=io.BytesIO()
        with wave.open(data,'wb') as wav:
            wav.setnchannels(1); wav.setsampwidth(2); wav.setframerate(24000)
            wav.writeframes(b'\x01\x00'*240)
        with patch('espeak_speech.subprocess.run',return_value=SimpleNamespace(stdout=data.getvalue())) as run:
            synthesize('Check.',voice='en-us+Alex',program='folder with spaces/engine.exe')
        argv=run.call_args.args[0]
        self.assertIn('--path='+str(Path('folder with spaces').resolve()),argv)
        self.assertIn('en-us+Alex',argv)
        with self.assertRaises(ValueError): synthesize('Check.',voice='en-us+unknown',program='engine.exe')

    def test_windows_dependency_copy_covers_actual_bundled_cpp_imports(self):
        import re,prepare_espeak
        library=Path(__file__).parent/'espeak/libespeak-ng.dll'
        imports={x.decode().lower() for x in re.findall(rb'(?:MSVCP|VCRUNTIME)[A-Z0-9_]*\.dll',library.read_bytes(),re.I)}
        self.assertIn('msvcp140.dll',imports)
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder); system=root/'Windows'/'System32'; system.mkdir(parents=True)
            target=root/'espeak';target.mkdir()
            for name in imports: (system/name).write_bytes(b'Windows runtime')
            fake_os=SimpleNamespace(name='nt',environ={'SystemRoot':str(system.parent)})
            with patch.object(prepare_espeak,'os',fake_os),patch.object(prepare_espeak.sys,'base_prefix',str(root/'python')),patch.object(prepare_espeak.sys,'executable',str(root/'python/python.exe')):
                prepare_espeak.runtime_dependencies(target)
            self.assertTrue(all((target/name).is_file() for name in imports))

    def test_setup_checks_real_speech_not_only_version(self):
        import prepare_espeak
        # This test exercises speech validation, not Windows dependency setup.
        # Patching the copy helper alone leaves verify's installer branch live.
        with patch.object(prepare_espeak, 'os', SimpleNamespace(name='posix')), \
             patch('prepare_espeak.subprocess.run', side_effect=AssertionError('Unit test attempted to launch a real process')):
            with tempfile.TemporaryDirectory() as folder,patch('espeak_speech.synthesize',return_value=b'pcm') as generate,patch('prepare_espeak.runtime_dependencies'):
                prepare_espeak.verify(Path(folder))
                self.assertEqual([c.kwargs['voice'] for c in generate.call_args_list],['en-us','en-gb'])
            with patch('espeak_speech.synthesize',side_effect=wave.Error('bad audio')):
                with self.assertRaises(wave.Error): prepare_espeak.verify(Path('.'))

    def test_missing_windows_runtime_prepares_signed_microsoft_installer(self):
        import prepare_espeak
        with tempfile.TemporaryDirectory() as folder:
            target=Path(folder)
            def installed(*args,**kwargs):
                for name in ('msvcp140.dll','vcruntime140.dll','vcruntime140_1.dll'):
                    (target/name).write_bytes(b'runtime')
            fake_os=SimpleNamespace(name='nt')
            with patch.object(prepare_espeak,'os',fake_os),patch('prepare_espeak.runtime_dependencies'),patch('prepare_espeak.subprocess.run',side_effect=installed) as run,patch('espeak_speech.synthesize',return_value=b'pcm'):
                prepare_espeak.verify(target)
                self.assertEqual(Path(run.call_args.args[0][-1]).name,'prepare-msvc-runtime.ps1')

    def test_failure_log_does_not_record_dictated_text(self):
        import subprocess
        with tempfile.TemporaryDirectory() as folder,patch.object(companion,'APP',Path(folder)):
            error=subprocess.CalledProcessError(3221225781,['engine','PRIVATE MESSAGE'],stderr=b'PRIVATE MESSAGE')
            companion.record_espeak_error(error)
            log=Path(folder,'espeak-error.txt').read_text()
            self.assertIn('3221225781',log);self.assertNotIn('PRIVATE MESSAGE',log)


class WindowsSetupIsolationTests(unittest.TestCase):
    def test_setup_unit_tests_under_windows_branch_never_download_or_launch_real_processes(self):
        import os, prepare_espeak
        suite = unittest.TestSuite()
        for case in (ESpeakSetupTests, ESpeakRuntimeRegressionTests):
            suite.addTests(unittest.defaultTestLoader.loadTestsFromTestCase(case))
        result = unittest.TestResult()
        # Select Windows branches independently of the host OS. Nested test
        # doubles permit intentional simulated operations; anything real fails.
        with patch.object(prepare_espeak,'os',SimpleNamespace(name='nt',environ=dict(os.environ))), \
             patch('prepare_espeak.subprocess.run',side_effect=AssertionError('Unexpected real process in setup unit tests')), \
             patch('prepare_espeak.urllib.request.urlretrieve',side_effect=AssertionError('Unexpected download in setup unit tests')):
            suite.run(result)
        self.assertEqual(result.errors, [])
        self.assertEqual(result.failures, [])


class CompactVoiceListTests(unittest.TestCase):
    setUp = ESpeakTests.setUp
    def test_picker_cycles_six_choices_and_cancel_restores_saved_variant(self):
        self.espeak.enabled=True;companion.ESPEAK_VOICE_NAME='en-us+Alex'
        companion.handle('select voice','awake')
        seen=[]
        for _ in range(6):
            seen.append(companion.ESPEAK_VOICE_NAME)
            companion.handle('next voice','awake')
        self.assertEqual(set(seen),{'en-us','en-gb','en-us+m1','en-gb+m1','en-us+f1','en-gb+f1'})
        self.assertEqual(companion.ESPEAK_VOICE_NAME,seen[0])
        companion.handle('cancel voice','awake')
        self.assertEqual(companion.ESPEAK_VOICE_NAME,'en-us+Alex')
        companion.handle('use espeak','awake')
        self.assertTrue(self.espeak.enabled)
        self.assertEqual(companion.ESPEAK_VOICE_NAME,'en-us+Alex')

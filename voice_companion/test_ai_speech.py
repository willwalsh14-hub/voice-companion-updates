import json
import threading
import unittest
from unittest.mock import Mock, patch
from ai_speech import AISpeech, synthesize

class AISpeechTests(unittest.TestCase):
    def test_request_uses_pcm_and_does_not_include_key_in_body(self):
        response = Mock()
        response.__enter__ = Mock(return_value=response)
        response.__exit__ = Mock(return_value=False)
        response.read.return_value = b'\x00\x00'
        with patch.dict('os.environ', {'VOICE_COMPANION_OPENAI_KEY':'test-secret'}), \
             patch('urllib.request.urlopen', return_value=response) as request:
            self.assertEqual(synthesize('Hello', -1), b'\x00\x00')
        sent = request.call_args.args[0]
        body = json.loads(sent.data)
        self.assertEqual(body['response_format'], 'pcm')
        self.assertNotIn('test-secret', sent.data.decode())

    def test_canceled_network_result_is_never_played(self):
        started, release, finished = threading.Event(), threading.Event(), threading.Event()
        def generate(*args):
            started.set(); release.wait(2); finished.set(); return b'\x00\x00'
        engine = AISpeech(Mock(), generate)
        engine._play = Mock()
        engine.speak('Old reply')
        self.assertTrue(started.wait(1))
        engine.interrupt(); release.set()
        self.assertTrue(finished.wait(1))
        # A second job provides an ordering barrier after cancellation.
        delivered = threading.Event()
        engine.synthesizer = lambda *args: b'\x00\x00'
        played = []
        def record(audio, generation, volume):
            played.append(generation)
            delivered.set()
        engine._play = record
        engine.speak('New reply')
        self.assertTrue(delivered.wait(1))
        self.assertEqual(played, [engine.generation])

    def test_failure_reads_original_text_with_windows_fallback(self):
        delivered = threading.Event()
        voice = Mock()
        voice.Speak.side_effect = lambda *args: delivered.set()
        voice.WaitUntilDone.return_value = True
        engine = AISpeech(lambda: voice, Mock(side_effect=OSError('offline')))
        engine.speak('Your inbox is empty', -2, 80)
        self.assertTrue(delivered.wait(1))
        self.assertIn('Your inbox is empty', voice.Speak.call_args.args[0])
        self.assertEqual((voice.Rate, voice.Volume), (-2,80))

    def test_pause_before_generation_and_cancel(self):
        engine = AISpeech(Mock(), Mock())
        engine.pause()
        self.assertFalse(engine._wait(engine.generation + 1))
        engine.interrupt()
        self.assertFalse(engine.paused)
        self.assertTrue(engine._wait(engine.generation))

    def test_every_app_message_and_voice_command_use_shared_engine(self):
        import companion
        engine = Mock(enabled=True)
        with patch.object(companion,'AI_SPEECH',engine), patch.object(companion,'TEXT_MODE',False), \
             patch.object(companion,'voice',create=True), patch.object(companion,'save_speech_settings'):
            companion.speak('Tutorial practice')
            engine.speak.assert_called_once()
            self.assertEqual(companion.handle('use windows voice','tutorial'),'tutorial')
            self.assertFalse(engine.enabled)

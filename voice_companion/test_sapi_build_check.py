import sys
import unittest
import wave
from types import ModuleType
from unittest.mock import Mock, patch
from sapi_build_check import check_file_speech


class BuildSpeechTests(unittest.TestCase):
    def run_check(self, samples):
        stream = Mock()
        def close():
            with wave.open(stream.Open.call_args.args[0], 'wb') as output:
                output.setnchannels(1)
                output.setsampwidth(2)
                output.setframerate(22050)
                output.writeframes(samples)
        stream.Close.side_effect = close
        speaker = Mock()
        speaker.WaitUntilDone.return_value = True
        client = ModuleType('win32com.client')
        client.Dispatch = Mock(side_effect=[speaker, stream])
        package = ModuleType('win32com'); package.client = client
        with patch.dict(sys.modules, {'win32com': package, 'win32com.client': client}):
            result = check_file_speech()
        return result, speaker, stream

    def test_validates_audio_and_exercises_controls(self):
        result, speaker, stream = self.run_check(b'\x01\x00' * 100)
        self.assertEqual(result, 0)
        speaker.Pause.assert_called_once()
        speaker.Resume.assert_called_once()
        self.assertIn((('', 3), {}), [(c.args, c.kwargs) for c in speaker.Speak.call_args_list])
        stream.Close.assert_called_once()

    def test_empty_or_silent_output_fails(self):
        for samples in (b'', b'\x00\x00' * 100):
            with self.assertRaisesRegex(RuntimeError, 'no audio samples'):
                self.run_check(samples)

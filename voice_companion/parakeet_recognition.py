"""Offline utterance recognition. Vosk remains the lightweight wake detector."""
from pathlib import Path


class ParakeetRecognition:
    def __init__(self, model_folder: Path):
        import onnx_asr
        if not model_folder.is_dir() or not any(model_folder.iterdir()):
            raise FileNotFoundError('Bundled Parakeet model is missing')
        self.model = onnx_asr.load_model('nemo-parakeet-tdt-0.6b-v3',
                                         str(model_folder), quantization='int8')

    def recognize(self, pcm: bytes) -> str:
        import numpy as np
        samples = np.frombuffer(pcm, dtype='<i2').astype(np.float32) / 32768.0
        if len(samples) < 1600:
            return ''
        return str(self.model.recognize(samples, sample_rate=16000)).strip()

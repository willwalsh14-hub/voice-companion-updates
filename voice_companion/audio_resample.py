"""Convert mono signed 16-bit microphone PCM to 16 kHz without extra packages."""
from array import array
from math import floor


def stereo_to_mono(pcm):
    samples = array('h')
    samples.frombytes(pcm)
    if len(samples) % 2:
        raise ValueError('Incomplete stereo microphone frame')
    mono = array('h', ((samples[i] + samples[i + 1]) // 2
                       for i in range(0, len(samples), 2)))
    return mono.tobytes()


class PCM16Resampler:
    def __init__(self, input_rate, output_rate=16000):
        self.input_rate = int(round(input_rate))
        self.output_rate = output_rate
        if self.input_rate < 8000 or self.input_rate > 192000:
            raise ValueError('Unsupported microphone sample rate')
        self.step = self.input_rate / output_rate
        self.position = 0.0
        self.previous = None

    def reset(self):
        self.position = 0.0
        self.previous = None

    def convert(self, pcm):
        if self.input_rate == self.output_rate:
            return pcm
        samples = array('h')
        samples.frombytes(pcm)
        if not samples:
            return b''
        result = array('h')
        position = self.position
        count = len(samples)
        while position <= count - 1:
            left = floor(position)
            right = left + 1
            if left < 0:
                if self.previous is None:
                    position = 0.0
                    continue
                a = self.previous
            else:
                a = samples[left]
            if right >= count:
                break
            b = samples[right]
            result.append(int(round(a + (b - a) * (position - left))))
            position += self.step
        self.position = position - count
        self.previous = samples[-1]
        return result.tobytes()

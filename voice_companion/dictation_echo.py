"""Nonblocking readback of inserted phrases, independent of keyboard typing echo."""
import re
import time
from collections import deque


class DictationEcho:
    def __init__(self, clock=time.monotonic):
        self.clock=clock
        self.clear()

    def clear(self):
        self.pending=deque()
        self.sentence=''
        self.ready_at=0
        self.choice=None

    def add(self, text, choice):
        if choice!=self.choice:
            self.clear()
            self.choice=choice
        text=text.strip()
        if not text or choice=='do not echo dictated text':return
        if choice=='at the end of a sentence':
            self.sentence=(self.sentence+' '+text).strip()
            # Only complete sentences are spoken; keep the unfinished tail.
            end=list(re.finditer(r'[.!?](?:["\u201d\u2019])?(?=\s|$)',self.sentence))
            if not end:return
            stop=end[-1].end()
            text=self.sentence[:stop]
            self.sentence=self.sentence[stop:].strip()
        self.pending.append(text)
        self.ready_at=self.clock()+.35

    def take(self, busy=False):
        if busy or not self.pending or self.clock()<self.ready_at:return None
        return self.pending.popleft()

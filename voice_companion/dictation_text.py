"""Conservative cleanup for text a speech recognizer puts into writing fields."""
import re


def clean_dictation(raw):
    text = str(raw or '').strip()
    # Parakeet sometimes emits a filler as a separate last fragment.
    text = re.sub(r'(?i)^(?:(?:uh|um)[,.;:!?]?\s+)+', '', text)
    text = re.sub(r'(?i)(?:\s+|[,;]\s*)(?:uh|um)[,.;:!?]?$', '', text).strip()
    # Explicitly spoken punctuation can coexist with the recognizer's own
    # punctuation: "Bob, period. Uh" should produce "Bob.".
    marks = {'period': '.', 'full stop': '.', 'question mark': '?',
             'exclamation point': '!', 'exclamation mark': '!', 'comma': ','}
    # Protect ordinary references; literal dictation bypasses this function.
    protected = {}
    def protect(match):
        token = '\x00' + str(len(protected)) + '\x00'
        protected[token] = match.group(0)
        return token
    text = re.sub(r'(?i)\b(?:the period of|(?:the )?word (?:comma|period)|(?:a|the) comma)\b', protect, text)
    pattern = r'(?i)\s*[,.;:!?]?\s*\b(' + '|'.join(marks) + r')\b[.,!?]?\s*'
    text = re.sub(pattern, lambda m: marks[m.group(1).lower()] + ' ', text).strip()
    for token, original in protected.items(): text = text.replace(token, original)
    text = re.sub(r'\s+([,.;!?])', r'\1', text)
    text = text.strip()
    # Line-break phrases are controls even inside a longer dictated utterance.
    text = re.sub(r'(?i)[ \t]*\b(?:new line|carriage return)\b[.!?]?[ \t]*', '\n', text)
    return text

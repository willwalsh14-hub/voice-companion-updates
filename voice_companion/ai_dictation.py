"""Local disfluency cleanup after offline AI transcription, at prose insertion only.

Punctuation and casing come from the bundled Parakeet model. This module
does not guess missing words, rewrite grammar, or send text to a service.
"""
from contextlib import contextmanager
from contextvars import ContextVar
import re

_style = ContextVar('dictation_type', default='old school')


@contextmanager
def dictation_scope(style='old school', typed=False):
    token = _style.set('old school' if typed else style)
    try:
        yield
    finally:
        _style.reset(token)


def clean_disfluencies(text):
    # Quoted examples, identifiers, and codes must retain their exact wording.
    if re.search(r'[@/\\]|https?:|["“”]|\b\d', text):
        return text
    # "Um" can be a unit or part of an identifier. Restrict filler matches to
    # isolated words; never discard discourse words such as like or you know.
    text = re.sub(r'(?i)(?<![\w-])(?:um|uh|erm)(?![\w-])', '\x00FILLER\x00', text)
    text = re.sub(r'[ \t]*[,;:]?[ \t]*\x00FILLER\x00[,;:]?[ \t]*', ' ', text)
    text = re.sub(r'[ \t]+([,.;!?])', r'\1', text)
    text = re.sub(r'([,;:])[ \t]*[,;:]+', r'\1', text)
    text = re.sub(r'^[,;:.!? \t]+', '', text)
    text = re.sub(r'[ \t]+', ' ', text).strip()
    # Collapse partial-word stammers, e.g. "w-w-want" or "th-th-this".
    text = re.sub(r'(?i)\b([a-z]{1,3})-(?:\1-)*([a-z]+)\b',
                  lambda m: m[2] if m[2].lower().startswith(m[1].lower()) else m[0], text)
    # Only a narrow set of repeated starts is unambiguous enough to remove.
    # Preserve "had had", "that that", emphasis, negation, names and numbers.
    text = re.sub(r'(?i)\b(I|we|you|he|she|they|please)(?:[ \t]+|,[ \t]+)\1\b', r'\1', text)
    text = re.sub(r'(?i)\b(I|we|you|he|she|they|please)(?:[ \t]+|,[ \t]+)\1\b', r'\1', text)
    return text


def prepare_dictation(raw, old_school_cleanup=True):
    from dictation_text import clean_dictation
    text = str(raw or '').strip()
    if _style.get() == 'ai-based':
        text = clean_disfluencies(text)
    elif not old_school_cleanup:
        return text
    return clean_dictation(text)

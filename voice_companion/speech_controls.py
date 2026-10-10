"""Shared speech-setting commands, independent of app mode or synthesizer."""
import re
from decimal import Decimal, InvalidOperation
from document_editor import spoken_number

FASTER = frozenset(('faster', 'speak faster', 'talk faster'))
SLOWER = frozenset(('slower', 'speak slower', 'talk slower'))
LOUDER = frozenset(('louder', 'speak louder', 'talk louder', 'volume up'))
QUIETER = frozenset(('quieter', 'speak quieter', 'talk quieter', 'softer', 'speak softer',
                     'talk softer', 'volume down'))

def integer(value):
    value=value.strip().replace('\u2212', '-')
    sign=1
    if value.startswith(('minus ', 'negative ')):
        value=value.split(' ',1)[1];sign=-1
    elif value.startswith('-'):
        value=value[1:];sign=-1
    elif value.startswith(('plus ', 'positive ')):
        value=value.split(' ',1)[1]
    elif value.startswith('+'):
        value=value[1:]
    # Speech recognizers commonly spell four as "for" and two as "to".
    # Apply these only inside an explicit speech-setting number, never dictation.
    aliases = {'for': 'four', 'fore': 'four', 'to': 'two', 'too': 'two',
               'won': 'one', 'ate': 'eight', 'oh': 'zero', 'o': 'zero'}
    value = ' '.join(aliases.get(word, word) for word in value.split())
    if value == 'hundred': value = 'one hundred'
    number=spoken_number(value)
    if number is None: return None
    try:
        numeric = Decimal(number)
        return sign * int(numeric) if numeric.is_finite() and numeric == numeric.to_integral_value() else None
    except (InvalidOperation, ValueError):
        return None

def request(command):
    command=re.sub(r'\s+', ' ',command.strip().lower().rstrip('.!?').replace(',', ' ')).strip()
    if command in ('higher pitch','raise pitch','pitch up','speak higher'):return 'pitch','delta',1
    if command in ('lower pitch','reduce pitch','pitch down','speak lower'):return 'pitch','delta',-1
    if command in FASTER: return 'rate', 'delta', 1
    if command in SLOWER: return 'rate', 'delta', -1
    if command in LOUDER: return 'volume', 'delta', 10
    if command in QUIETER: return 'volume', 'delta', -10
    match=re.fullmatch(r'(?:(?:set|change) )?(?:(?:speech|voice) )?(speed|rate|volume|pitch)(?: to)? (.+)',command)
    if not match: return None
    name, value=match.groups()
    if name=='volume': value=re.sub(r'\s*(?:percent|per cent|%)$', '',value)
    return (name if name in ('volume','pitch') else 'rate'), 'set', integer(value)

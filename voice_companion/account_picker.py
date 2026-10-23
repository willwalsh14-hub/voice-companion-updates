"""The spoken list of connected email accounts.

Used before the folder list when several accounts are connected (Control+Y,
"go to folder"), by "switch account", and by "remove email account". This
module only builds the words and understands the answers; companion.py keeps
the list open and acts on the choice.
"""
import re

PROVIDER_NAMES = {'gmail': 'Gmail', 'outlook': 'Outlook', 'yahoo': 'Yahoo', 'thunderbird': 'Thunderbird'}
# What people call each kind of account.
PROVIDER_WORDS = {'gmail': ('gmail', 'google', 'google mail'),
                  'outlook': ('outlook', 'microsoft', 'microsoft 365', 'exchange', 'hotmail'),
                  'yahoo': ('yahoo', 'yahoo mail'),
                  'thunderbird': ('thunderbird', 'mozilla thunderbird')}
NUMBERS = {'one': 1, 'two': 2, 'three': 3, 'four': 4, 'five': 5, 'six': 6, 'seven': 7, 'eight': 8, 'nine': 9, 'ten': 10,
           'first': 1, 'second': 2, 'third': 3, 'fourth': 4, 'fifth': 5, 'sixth': 6, 'seventh': 7, 'eighth': 8,
           'ninth': 9, 'tenth': 10}

NEXT = ('next', 'next account', 'next one', 'down', 'move down')
PREVIOUS = ('previous', 'previous account', 'previous one', 'up', 'move up')
FIRST = ('first item', 'start of list', 'beginning of list', 'top of list', 'first account')
LAST = ('last item', 'end of list', 'bottom of list', 'last account')
CHOOSE = ('that one', 'this one', 'okay', 'ok', 'confirm', 'confirm that', 'select', 'select that', 'choose', 'choose that',
          'choose it', 'open', 'open it', 'enter', 'use that', 'use that one', 'select account', 'choose account')
CANCEL = ('cancel', 'go back', 'back', 'escape', 'close list', 'close', 'stop', 'never mind', 'exit')
REPEAT = ('repeat', 'say that again', 'current account', 'which account', 'where am i', 'what')
YES = ('yes', 'yes please', 'y', 'yeah', 'yep', 'yes remove it', 'remove it', 'confirm', 'confirm remove',
       'yes remove', 'yes delete it', 'delete it')
NO = ('no', 'no thanks', 'n', 'nope', 'cancel', 'keep it', 'go back', 'back', 'escape', 'stop', 'never mind', 'do not remove')

LIST_HINT = 'Say next or previous, then that one. Or say the account number or name. Say cancel to close the list.'
KEY_HINT = 'Arrows move, Enter chooses, and Escape cancels. You can also say the account number or name.'
OPENING = {'folders': 'Choose an account.', 'inbox': 'Choose an account to open.', 'remove': 'Choose the account to remove.'}


def name(provider):
    return PROVIDER_NAMES.get(provider, provider.capitalize())


def ordered(accounts, current):
    """The current account first, then the others in their saved order."""
    accounts = list(accounts)
    return ([current] if current in accounts else []) + [account for account in accounts if account != current]


def label(account, current=None):
    provider, address = account
    return name(provider) + ', ' + address + (', current' if account == current else '')


def item(picker, index=None):
    """One spoken list item, such as "Gmail, will@gmail.com, current, 1 of 3."."""
    index = picker['index'] if index is None else index
    accounts = picker['accounts']
    return label(accounts[index], picker.get('current')) + ', ' + str(index + 1) + ' of ' + str(len(accounts)) + '.'


def opening(picker):
    return OPENING[picker['purpose']] + ' ' + item(picker) + ' ' + KEY_HINT


def choice(text, accounts):
    """What a spoken or typed answer chose: an index, ('several', provider), or None.

    Speech adds capitals and punctuation ("Sam at Gmail, dot com."), so both are ignored."""
    plain = re.sub(r'\s+', ' ', re.sub(r'[^\w\s@.+-]', ' ', text.casefold())).strip().rstrip('.').strip()
    words = re.sub(r'\s+', ' ', re.sub(r'[^\w\s]', ' ', plain)).strip()
    words = re.sub(r'^(?:(?:the|use|choose|select|open|switch to|go to|remove|delete|number|account)\s+)+', '', words)
    words = re.sub(r'\s+(?:one|account|email|mail)$', '', words) if words not in NUMBERS else words
    number = int(words) if words.isdigit() else NUMBERS.get(words)
    if number is not None:
        return number - 1 if 1 <= number <= len(accounts) else None
    for provider, names in PROVIDER_WORDS.items():
        if words in names:
            matches = [index for index, (p, _) in enumerate(accounts) if p == provider]
            if len(matches) == 1:
                return matches[0]
            return ('several', provider) if matches else None
    from email_draft import normalize_recipient
    try:
        address = normalize_recipient(plain)
    except ValueError:
        address = None
    if address:
        matches = [index for index, (_, a) in enumerate(accounts) if a.casefold() == address]
        if len(matches) == 1:
            return matches[0]
        return ('several', '') if matches else None
    # The name before the @ alone ("sam") when only one account has it.
    matches = [index for index, (_, a) in enumerate(accounts)
               if re.sub(r'[^\w]+', ' ', a.split('@')[0].casefold()).strip() == words]
    return matches[0] if len(matches) == 1 else None


def several(provider, accounts):
    count = len([a for p, a in accounts if p == provider]) if provider else 0
    if provider and count > 1:
        return 'There are ' + str(count) + ' ' + name(provider) + ' accounts. Say the account number or the address.'
    return 'More than one account has that address. Say the account number.'


def removal_question(account):
    provider, address = account
    keeps = 'Your email stays in Thunderbird.' if provider == 'thunderbird' else 'Your email stays with ' + name(provider) + '.'
    return 'Remove the ' + name(provider) + ' account ' + address + ' from Voice Companion? ' + keeps + ' Say yes or no.'


def removal_note(provider):
    """What else the user may want to know after removing an account of this kind."""
    if provider == 'gmail':
        return ('Your email is still with Gmail. To also take back its permission, open your Google Account, '
                'choose Security, and remove Voice Companion from the apps that can use your account.')
    if provider == 'outlook':
        return ('Your email is still with Outlook. To also take back its permission, open your Microsoft account, '
                'choose Privacy or Security, and remove Voice Companion from the apps that can use your account.')
    if provider == 'yahoo':
        return 'Your email is still with Yahoo. The app password still works until you delete it in Yahoo Account security.'
    return 'Thunderbird and its email are not changed. Voice Companion just stops using this account.'

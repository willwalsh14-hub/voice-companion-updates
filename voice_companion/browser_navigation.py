"""Validate spoken HTTP and HTTPS addresses before guided browser navigation."""
import re
import urllib.parse


def spoken_address(spoken):
    """Turn a dictated address such as "VA dot gov." into "VA.gov".

    Speech recognizers add sentence punctuation, and people say "forward slash",
    "underscore", "dash", "please", or a trailing "dot"; none of those words
    belong in the address as words."""
    address = str(spoken).strip()
    # "Amazon dot com, please." The polite word is not part of the address.
    address = re.sub(r'^please[\s,]+|[\s,]+please[.!?]*$', '', address, flags=re.I)
    # "V.A. dot gov": an abbreviation written with periods is one name.
    address = re.sub(r'\b(?:[A-Za-z]\.){2,}(?=\s)', lambda m: m[0].replace('.', ''), address)
    # "v a dot gov", "w w w dot ...": offline speech spells short names letter by letter.
    address = re.sub(r'\b[A-Za-z](?:\s+[A-Za-z]\b)+', lambda m: re.sub(r'\s+', '', m[0]), address)
    address = re.sub(r'\s*\b(?:forward\s+)?slash\b\s*', '/', address, flags=re.I)
    address = re.sub(r'\s*\bunderscore\b\s*', '_', address, flags=re.I)
    address = re.sub(r'\s*\bdot\b\s*', '.', address, flags=re.I)
    address = re.sub(r'(?<=\w)\s+(?:dash|hyphen)\s+(?=\w)', '-', address, flags=re.I)
    return address.strip().rstrip('.!?,;').strip()


def prepare_address(spoken):
    """Use HTTPS for bare addresses and preserve an explicit HTTP address."""
    address = re.sub(r'\s+', '', spoken_address(spoken))
    if not address or '\\' in address or any(ord(c)<32 for c in spoken):
        raise ValueError('Type a website address, such as example.com.')
    if '://' in address:
        parsed = urllib.parse.urlsplit(address)
        if parsed.scheme not in ('http','https'):
            raise ValueError('Use an HTTP or HTTPS website address.')
    else:
        parsed = urllib.parse.urlsplit('https://' + address)
    host = parsed.hostname or ''
    try:
        port = parsed.port
    except ValueError as exc:
        raise ValueError('I could not understand that website address.') from exc
    if (not re.fullmatch(r'[a-z0-9-]+(?:\.[a-z0-9-]+)+', host)
            or any(not label or label.startswith('-') or label.endswith('-') for label in host.split('.'))
            or len(host) > 253 or port is not None and not 1<=port<=65535 or parsed.username or parsed.password):
        raise ValueError('I could not understand that website address.')
    if not re.fullmatch(r'/[A-Za-z0-9._~!$&\x27()*+,;=:@%/-]*|', parsed.path):
        raise ValueError('Please type a simpler website address.')
    return urllib.parse.urlunsplit((parsed.scheme, host+(':'+str(port) if port is not None else ''), parsed.path, parsed.query, parsed.fragment)), host

def looks_like_address(text):
    value=str(text).strip().rstrip('.!?,;')
    if re.search(r'\s',value):return False
    if re.match(r'^https?://\S+',value,re.I):return True
    match=re.match(r'^((?:[A-Za-z0-9-]+)(?:\.[A-Za-z0-9-]+)+)(?:[/?:#]|$)',value)
    if not match:return False
    # A plain number such as 98.6 or 3.14 is a search, not a website; keep IPv4 addresses.
    labels=match[1].split('.')
    return bool(re.search(r'[A-Za-z]',labels[-1])) or len(labels)==4 and all(label.isdigit() for label in labels)

def contains_address(text):
    return bool(re.search(r'@|https?://|www\.|\b[A-Za-z0-9-]+\.(?:[A-Za-z]{2,})(?:\b|/)|\b(?:at|dot)\b.+\b(?:dot|com|org|net|co|edu)\b',str(text),re.I))

def private_address_field(item):
    return item.get('type') in ('password','email','url') or bool(re.search(r'\b(?:password|email|e-mail|web(?:site)? address|url)\b',str(item.get('label','')),re.I))


def spoken_address_request(spoken):
    """The address in something said in the address box, or None for a search.

    "VA dot gov.", "Open website VA dot gov" and "go to VA dot gov" are
    addresses; "what is a dot com company" is a search."""
    value=re.sub(r'^(?:(?:open|go to|visit)(?: the)? (?:website|web site|site|page)|open|go to|visit)[\s,]+','',str(spoken).strip(),flags=re.I)
    address=spoken_address(value)
    if not looks_like_address(address):return None
    try:return prepare_address(address)[0]
    except ValueError:return None

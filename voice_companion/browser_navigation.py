"""Validate spoken HTTPS addresses before guided browser navigation."""
import re
import urllib.parse


def prepare_address(spoken):
    """Return (https URL, host) or raise ValueError for unsafe/ambiguous input."""
    address = re.sub(' dot ','.',spoken.strip(),flags=re.I)
    address = re.sub(' slash ','/',address,flags=re.I)
    address = re.sub(r'\s+', '', address)
    if not address or '\\' in address or any(ord(c)<32 for c in spoken):
        raise ValueError('Type a website address, such as example.com.')
    if '://' in address:
        parsed = urllib.parse.urlsplit(address)
        if parsed.scheme not in ('http','https'):
            raise ValueError('Only secure HTTPS website addresses are supported.')
    else:
        parsed = urllib.parse.urlsplit('https://' + address)
    host = parsed.hostname or ''
    try:
        port = parsed.port
    except ValueError as exc:
        raise ValueError('I could not understand that website address.') from exc
    if (not re.fullmatch(r'[a-z0-9-]+(?:\.[a-z0-9-]+)+', host)
            or any(not label or label.startswith('-') or label.endswith('-') for label in host.split('.'))
            or len(host) > 253 or port is not None or parsed.username or parsed.password):
        raise ValueError('I could not understand that website address.')
    if not re.fullmatch(r'/[A-Za-z0-9._~!$&\x27()*+,;=:@%/-]*|', parsed.path):
        raise ValueError('Please type a simpler website address.')
    return urllib.parse.urlunsplit(('https', host, parsed.path, parsed.query, parsed.fragment)), host

def looks_like_address(text):
    value=str(text).strip()
    return bool(re.match(r'^https?://\S+|^(?:[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)+)(?:[/?:#]|$)',value,re.I)) and not re.search(r'\s',value)

def contains_address(text):
    return bool(re.search(r'@|https?://|www\.|\b[A-Za-z0-9-]+\.(?:[A-Za-z]{2,})(?:\b|/)|\b(?:at|dot)\b.+\b(?:dot|com|org|net|co|edu)\b',str(text),re.I))

def private_address_field(item):
    return item.get('type') in ('password','email','url') or bool(re.search(r'\b(?:password|email|e-mail|web(?:site)? address|url)\b',str(item.get('label','')),re.I))

"""Validate spoken HTTPS addresses before guided browser navigation."""
import re
import urllib.parse


def prepare_address(spoken):
    """Return (https URL, host) or raise ValueError for unsafe/ambiguous input."""
    address = spoken.strip().lower().replace(' dot ', '.').replace(' slash ', '/')
    address = re.sub(r'\s+', '', address)
    if not address or any(c in address for c in '@\\?#'):
        raise ValueError('Say a website address, such as example dot com.')
    if '://' in address:
        parsed = urllib.parse.urlsplit(address)
        if parsed.scheme != 'https':
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
    if not re.fullmatch(r'/[a-z0-9._~!$&\x27()*+,;=:@%/-]*|', parsed.path):
        raise ValueError('Please say a simpler website address.')
    return urllib.parse.urlunsplit(('https', host, parsed.path, '', '')), host

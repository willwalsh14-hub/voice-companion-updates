"""Read an IMAP BODYSTRUCTURE: the parts of a message, without downloading it.

Yahoo describes each message's parts (RFC 3501, section 7.4.2). From that,
Voice Companion lists attachments with their names and sizes, fetches only the
text parts of a large message to read it, and fetches one attachment when it is
saved. Part numbers ("1", "2.1") are the IMAP section numbers used to fetch.
"""
import base64
import binascii
import quopri
import re
from email.header import decode_header, make_header

from mail_attachments import safe_filename


class StructureError(ValueError):
    """The server's description of the message could not be read."""


# Reading the server's reply -------------------------------------------------------

def _segments(data):
    """imaplib gives a reply with literals as (text, literal) pairs and plain bytes."""
    for item in data or []:
        if isinstance(item, tuple):
            yield item[0] or b'', item[1] if len(item) > 1 else b''
        elif isinstance(item, bytes):
            yield item, None


def tokens(data):
    """Nested lists of the reply: bytes for strings, None for NIL, int for numbers."""
    stack = [[]]
    for text, literal in _segments(data):
        i = 0
        while i < len(text):
            c = text[i:i + 1]
            if c in (b' ', b'\r', b'\n'):
                i += 1
            elif c == b'(':
                stack.append([]); i += 1
            elif c == b')':
                if len(stack) < 2:
                    raise StructureError('Unbalanced reply.')
                done = stack.pop(); stack[-1].append(done); i += 1
            elif c == b'"':
                j, value = i + 1, bytearray()
                while j < len(text) and text[j:j + 1] != b'"':
                    if text[j:j + 1] == b'\\':
                        j += 1
                    value += text[j:j + 1]; j += 1
                if j >= len(text):
                    raise StructureError('Unfinished string.')
                stack[-1].append(bytes(value)); i = j + 1
            elif c == b'{':
                end = text.index(b'}', i)
                if text[end + 1:].strip():
                    raise StructureError('Literal in the wrong place.')
                stack[-1].append(literal if literal is not None else b''); i = len(text)
            else:
                j = i
                while j < len(text) and text[j:j + 1] not in (b' ', b'(', b')', b'\r', b'\n'):
                    # Section names such as BODY[1.2]<0> keep their brackets.
                    if text[j:j + 1] == b'[':
                        j = text.index(b']', j)
                    j += 1
                atom = text[i:j]
                stack[-1].append(None if atom.upper() == b'NIL' else int(atom) if atom.isdigit() else atom)
                i = j
    if len(stack) != 1:
        raise StructureError('Unbalanced reply.')
    return stack[0]


def find_structure(data):
    """The BODYSTRUCTURE list in a FETCH reply."""
    def search(items):
        for index, item in enumerate(items):
            if isinstance(item, bytes) and item.upper() == b'BODYSTRUCTURE' and index + 1 < len(items) \
                    and isinstance(items[index + 1], list):
                return items[index + 1]
            if isinstance(item, list):
                found = search(item)
                if found is not None:
                    return found
        return None
    found = search(tokens(data))
    if found is None:
        raise StructureError('No message structure in the reply.')
    return found


# Understanding it ----------------------------------------------------------------

def _text(value):
    if value is None:
        return ''
    if isinstance(value, bytes):
        value = value.decode('utf-8', 'replace')
    return str(value)


def _words(value):
    """Undo =?UTF-8?B?...?= encoding in a name."""
    value = _text(value)
    if '=?' not in value:
        return value
    try:
        return str(make_header(decode_header(value)))
    except (ValueError, LookupError, UnicodeError):
        return value


def _extended(value):
    """An RFC 2231 value such as utf-8''r%C3%A9sum%C3%A9.pdf."""
    from urllib.parse import unquote_to_bytes
    charset, _, rest = value.partition("'")
    _, _, text = rest.partition("'") if "'" in rest else ('', '', value)
    if not rest:
        charset, text = 'utf-8', value
    try:
        return unquote_to_bytes(text).decode(charset or 'utf-8', 'replace')
    except LookupError:
        return unquote_to_bytes(text).decode('utf-8', 'replace')


def params(items):
    """A parameter list as a dict with lower-case names, RFC 2231 names put back together."""
    found, pieces = {}, {}
    items = items if isinstance(items, list) else []
    for key, value in zip(items[0::2], items[1::2]):
        key = _text(key).lower()
        match = re.fullmatch(r'([^*]+)\*(\d+)(\*?)', key)
        if match:
            pieces.setdefault(match[1], []).append((int(match[2]), bool(match[3]), _text(value)))
            continue
        if key.endswith('*'):
            found[key[:-1]] = _extended(_text(value))
            continue
        found[key] = _words(value)
    for key, parts in pieces.items():
        parts.sort()
        encoded = parts[0][1]
        joined = ''.join(text for _, _, text in parts)
        found.setdefault(key, _extended(joined) if encoded else joined)
    return found


class Part:
    """One leaf part of a message."""

    def __init__(self, number, maintype, subtype, parameters, encoding, octets, disposition, disposition_params):
        self.number = number
        self.maintype, self.subtype = maintype.lower(), subtype.lower()
        self.params = parameters
        self.encoding = encoding.lower()
        self.octets = octets if isinstance(octets, int) else 0
        self.disposition = disposition.lower()
        self.disposition_params = disposition_params

    @property
    def content_type(self):
        return self.maintype + '/' + self.subtype

    @property
    def filename(self):
        return self.disposition_params.get('filename') or self.params.get('name') or ''

    @property
    def size(self):
        """The file's size: base64 takes about 4 bytes for every 3 (and line breaks)."""
        if self.encoding == 'base64':
            return self.octets * 57 // 78 if self.octets > 78 else self.octets * 3 // 4
        return self.octets

    def is_attachment(self):
        if self.content_type == 'message/rfc822':
            return True
        if self.disposition == 'attachment':
            return True
        if self.maintype == 'multipart':
            return False
        # Inline pictures without a file name belong to the message's look, not its files.
        if not self.filename:
            return False
        if self.disposition == 'inline' and self.maintype == 'text':
            return False
        return True

    def name(self):
        if self.filename:
            return safe_filename(self.filename)
        if self.content_type == 'message/rfc822':
            return 'Attached message.eml'
        return 'Attachment ' + self.number

    def record(self):
        return {'id': self.number, 'name': self.name(), 'size': self.size, 'type': self.content_type,
                'encoding': self.encoding}


def parts(structure, prefix=''):
    """Every leaf part of a BODYSTRUCTURE, in order, with its IMAP section number."""
    found = []
    if not isinstance(structure, list) or not structure:
        raise StructureError('Empty message structure.')
    if isinstance(structure[0], list):
        children = []
        for item in structure:
            if not isinstance(item, list):
                break
            children.append(item)
        for index, child in enumerate(children, 1):
            found.extend(parts(child, (prefix + '.' if prefix else '') + str(index)))
        return found
    number = prefix or '1'
    if len(structure) < 7:
        raise StructureError('Short part description.')
    maintype, subtype = _text(structure[0]) or 'application', _text(structure[1]) or 'octet-stream'
    kind = (maintype.lower(), subtype.lower())
    extension = 10 if kind == ('message', 'rfc822') else 8 if maintype.lower() == 'text' else 7
    disposition, disposition_params = '', {}
    if len(structure) > extension + 1 and isinstance(structure[extension + 1], list) and structure[extension + 1]:
        disposition = _text(structure[extension + 1][0])
        if len(structure[extension + 1]) > 1:
            disposition_params = params(structure[extension + 1][1])
    found.append(Part(number, maintype, subtype, params(structure[2]), _text(structure[5]) or '7bit',
                      structure[6], disposition, disposition_params))
    return found


def attachments(structure):
    return [part.record() for part in parts(structure) if part.is_attachment()]


def text_parts(structure):
    """(plain part or None, html part or None): the message's own text, not attached files."""
    plain = html = None
    for part in parts(structure):
        if part.is_attachment():
            continue
        if part.content_type == 'text/plain' and plain is None:
            plain = part
        elif part.content_type == 'text/html' and html is None:
            html = part
    return plain, html


# Decoding fetched parts ------------------------------------------------------------

class Base64Stream:
    """Decodes base64 that arrives in pieces of any length."""

    def __init__(self):
        self.rest = b''

    def feed(self, data):
        data = self.rest + re.sub(rb'\s+', b'', data)
        usable = len(data) - len(data) % 4
        self.rest = data[usable:]
        return self._decode(data[:usable])

    def finish(self):
        rest, self.rest = self.rest, b''
        if not rest:
            return b''
        return self._decode(rest + b'=' * (-len(rest) % 4))

    @staticmethod
    def _decode(data):
        if not data:
            return b''
        try:
            return base64.b64decode(data, validate=True)
        except (binascii.Error, ValueError) as exc:
            raise StructureError('Damaged attachment data.') from exc


def decode(data, encoding):
    """The bytes of a whole fetched part."""
    encoding = (encoding or '').lower()
    if encoding == 'base64':
        stream = Base64Stream()
        return stream.feed(data) + stream.finish()
    if encoding == 'quoted-printable':
        return quopri.decodestring(data)
    return data

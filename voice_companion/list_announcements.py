"""Presentation for named picker items; never applied to dictated or stored text."""
import re


def name_first(text):
    pattern = r'(?P<prefix>^|(?<=\. ))(?:(?:Voice|Preset|Choice|Station|Episode|Podcast|Favorite|Field|Topic|Subtopic|Folder|Link|Match|Message|Contact|Account|Document|Note) )?(?P<index>\d+) of (?P<count>\d+)(?:: |\. )(?P<item>.+)'
    match = re.search(pattern, text)
    if not match: return text
    item = match['item']
    split = re.search(r'\. Say |\? Say |\. Open link ', item)
    if split:
        name = item[:split.start()].rstrip('. ')
        tail = item[split.start():]
        # Keep opening instructions after the name and position.
        return text[:match.start()] + name + ', ' + match['index'] + ' of ' + match['count'] + tail
    return text[:match.start()] + item.rstrip('. ') + ', ' + match['index'] + ' of ' + match['count'] + '.'


def list_item(name, index, count):
    return f'{name}, {index + 1} of {count}.'

"""Presentation for named picker items; never applied to dictated or stored text."""
import re
from menu_navigation import menu_label, boundary_notice


def name_first(text):
    pattern = r'(?P<prefix>^|(?<=\. ))(?:(?:Voice|Preset|Choice|Station|Episode|Podcast|Favorite|Field|Topic|Subtopic|Folder|Link|Match|Message|Contact|Account|Document|Note) )?(?P<index>\d+) of (?P<count>\d+)(?:: |\. )(?P<item>.+)'
    match = re.search(pattern, text)
    if not match: return text
    item = match['item']
    boundary=boundary_notice(int(match['index'])-1,int(match['count']))
    boundary=(' '+boundary) if boundary else ''
    split = re.search(r'\. Say |\? Say |\. Open link ', item)
    if split:
        name = item[:split.start()].rstrip('. ')
        tail = item[split.start():]
        # Keep opening instructions after the name and position.
        return text[:match.start()] + menu_label(name) + ', ' + match['index'] + ' of ' + match['count'] + tail+boundary
    return text[:match.start()] + menu_label(item.rstrip('. ')) + ', ' + match['index'] + ' of ' + match['count'] + '.'+boundary


def list_item(name, index, count):
    return f'{menu_label(name)}, {index + 1} of {count}.'+(' '+boundary_notice(index,count) if boundary_notice(index,count) else '')

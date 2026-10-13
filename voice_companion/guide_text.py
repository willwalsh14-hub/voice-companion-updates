"""Read the numbered manual outline used by every guide format and Help."""
import re
from pathlib import Path

CHAPTER = re.compile(r'^(\d+)\. ([A-Z][A-Z0-9 ,.-]+)$')
PART = re.compile(r'^(\d+)\.(\d+) (.+)$')
STEP = re.compile(r'^(\d+)\. (.+)$')


def read_guide(path):
    lines = Path(path).read_text(encoding='utf-8-sig').splitlines()
    title = lines[0].strip()
    front = []
    chapters = []
    current = None
    part = None
    contents = False
    for line in lines[1:]:
        value = line.strip()
        if not value:
            continue
        chapter = CHAPTER.fullmatch(value)
        section = PART.fullmatch(value)
        if chapter:
            current = {'number': int(chapter[1]), 'key': chapter[2], 'name': chapter[2].title(),
                       'heading': value, 'paragraphs': [], 'parts': []}
            chapters.append(current)
            part = None
        elif section:
            if current is None or int(section[1]) != current['number']:
                raise ValueError('A guide subsection belongs to the wrong chapter.')
            part = {'heading': value, 'name': section[3], 'paragraphs': []}
            current['parts'].append(part)
        elif current is None:
            if value=='Contents':contents=True
            elif not contents:front.append(value)
        else:
            current['paragraphs'].append(value)
            if part is not None:
                part['paragraphs'].append(value)
    if not chapters or [c['number'] for c in chapters] != list(range(1, len(chapters)+1)):
        raise ValueError('The guide needs chapters in numbered order.')
    for chapter in chapters:
        if not chapter['parts'] or any(not p['paragraphs'] for p in chapter['parts']):
            raise ValueError('Each guide subsection needs instructions.')
    return title, front, chapters


def guide_blocks(path):
    title, front, chapters = read_guide(path)
    blocks = [(None, front)]
    for chapter in chapters:
        paragraphs = []
        for part in chapter['parts']:
            paragraphs.append(part['heading'])
            paragraphs.extend(part['paragraphs'])
        blocks.append((chapter['heading'], paragraphs))
    return title, blocks

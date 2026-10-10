"""Literal keyboard edits and concise spoken caret/typing feedback."""
import copy
import re
import unicodedata
from document_editor import Paragraph,TextRun,TextSelection

PHONETICS=dict(zip('abcdefghijklmnopqrstuvwxyz',('Alpha','Bravo','Charlie','Delta','Echo','Foxtrot','Golf','Hotel','India','Juliett','Kilo','Lima','Mike','November','Oscar','Papa','Quebec','Romeo','Sierra','Tango','Uniform','Victor','Whiskey','X-ray','Yankee','Zulu')))
NAMES={' ':'space','\n':'new line','\t':'tab','@':'at sign','.':'period',',':'comma','!':'exclamation mark','?':'question mark','-':'hyphen','_':'underscore',':':'colon',';':'semicolon','(':'left parenthesis',')':'right parenthesis'}

NAMES.update({'#':'hash sign','$':'dollar sign','%':'percent sign','&':'ampersand','*':'asterisk','+':'plus','=':'equals','/':'forward slash','\\':'backslash',"'":'apostrophe','"':'quotation mark','[':'left bracket',']':'right bracket','{':'left brace','}':'right brace','<':'less than','>':'greater than','^':'caret','|':'vertical bar','~':'tilde','`':'grave accent','\r':'carriage return'})

def character(value):
    if not value:return 'End of text.'
    if value in NAMES:return NAMES[value]
    if value.isalnum() or len(value)!=1:return value
    return unicodedata.name(value,'character '+str(ord(value))).lower()
def phonetic(value):return PHONETICS.get(value.lower(),'')
def document_text(doc):return '\n'.join(p.text for p in doc.paragraphs)
def absolute(doc,position):return sum(len(p.text)+1 for p in doc.paragraphs[:position[0]])+position[1]

def replace_keyboard_text(doc,before,after,caret,selection=None):
    """Splice only the changed range, preserving surrounding runs and paragraph styles."""
    if document_text(doc)!=before:raise ValueError('Text changed before that keyboard edit could be applied. Your latest saved text is still available.')
    if not doc.paragraphs:doc.paragraphs=[Paragraph('')]
    if before!=after:
        start=0
        while start<min(len(before),len(after)) and before[start]==after[start]:start+=1
        end_old=len(before);end_new=len(after)
        while end_old>start and end_new>start and before[end_old-1]==after[end_new-1]:end_old-=1;end_new-=1
        i,a=doc._location(start);j,b=doc._location(end_old)
        first,last=doc.paragraphs[i],doc.paragraphs[j]
        prefix,suffix=first.text[:a],last.text[b:]
        prefix_styles=doc._character_styles(first)[:a];suffix_styles=doc._character_styles(last)[b:]
        inherited=copy.copy(prefix_styles[-1]) if prefix_styles else TextRun('',first.bold,first.italic,first.underline,first.font,first.size)
        pieces=after[start:end_new].split('\n');doc.checkpoint();created=[]
        for index,piece in enumerate(pieces):
            p=copy.deepcopy(first if index==0 or index<len(pieces)-1 else last)
            p.text=(prefix if index==0 else '')+piece+(suffix if index==len(pieces)-1 else '')
            styles=(prefix_styles if index==0 else [])+[inherited]*len(piece)+(suffix_styles if index==len(pieces)-1 else [])
            p.runs=doc.compress_runs(p.text,styles);created.append(p)
        doc.paragraphs[i:j+1]=created
        doc.last_change_type='edit'
    doc.reading.set_text(after);doc.reading.position=max(0,min(len(after),caret));doc.reading.continuation=doc.reading.position
    pos=doc._location(max(0,min(len(after),caret)));doc.cursor=pos[0];doc.insertion_position=pos;doc.navigation_position=None
    doc.selection_candidates=[];doc.replacement_candidates=[]
    if selection and selection[0]!=selection[1]:
        a=doc._location(selection[0]);b=doc._location(selection[1]);doc.selection=TextSelection(a[0],a[1],b[0],b[1])
    else:doc.selection=None

def paragraph_destination(text,caret,direction):
    """Ctrl+arrows skip separator-only lines and retain paragraph-start behavior."""
    starts=[m.start() for m in re.finditer(r'[^\n]+',text) if m.group().strip()]
    if not starts:return 0
    from bisect import bisect_right
    index=max(0,bisect_right(starts,caret)-1)
    if direction<0 and caret>starts[index]:return starts[index]
    return starts[max(0,min(len(starts)-1,index+direction))]

def caret_feedback(text,caret,key,control=False,selection=None):
    if selection and selection[0]!=selection[1]:return 'Selected '+text[selection[0]:selection[1]],''
    if key not in ('Left','Right','Up','Down','Home','End','Prior','Next'):return None,''
    caret=max(0,min(len(text),caret))
    if control and key in ('Left','Right'):
        match=re.search(r'\S+',text[caret:]);return (match.group() if match else 'End of text.'),''
    if key in ('Up','Down','Prior','Next') or (control and key in ('Home','End')):
        start=text.rfind('\n',0,caret)+1;end=text.find('\n',caret);end=len(text) if end<0 else end
        return text[start:end] or 'Blank line.',''
    char=text[caret:caret+1];return character(char),char

def typing_feedback(before,after,caret,echo):
    if echo=='none' or before==after:return None
    if len(after)<len(before):return 'Deleted.'
    start=0
    while start<min(len(before),len(after)) and before[start]==after[start]:start+=1
    end_before=len(before);end_after=len(after)
    while end_before>start and end_after>start and before[end_before-1]==after[end_after-1]:end_before-=1;end_after-=1
    inserted=after[start:end_after]
    parts=[]
    if echo in ('characters','characters and words') and inserted:parts.append(' '.join(character(c) for c in inserted))
    if echo in ('words','characters and words') and inserted and inserted[-1].isspace():
        match=re.search(r'(\S+)\s+$',after[:caret])
        if match:parts.append(match[1])
    return '. '.join(parts) or None

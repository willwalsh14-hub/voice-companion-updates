"""Literal keyboard edits and concise spoken caret/typing feedback."""
import copy
import re
import unicodedata
from capital_feedback import CharacterSpeech,preserve_character
from document_editor import Paragraph,TextRun,TextSelection

PHONETICS=dict(zip('abcdefghijklmnopqrstuvwxyz',('Alpha','Bravo','Charlie','Delta','Echo','Foxtrot','Golf','Hotel','India','Juliett','Kilo','Lima','Mike','November','Oscar','Papa','Quebec','Romeo','Sierra','Tango','Uniform','Victor','Whiskey','X-ray','Yankee','Zulu')))
NAMES={' ':'space','\n':'new line','\t':'tab','@':'at sign','.':'period',',':'comma','!':'exclamation mark','?':'question mark','-':'hyphen','_':'underscore',':':'colon',';':'semicolon','(':'left parenthesis',')':'right parenthesis'}

NAMES.update({'#':'hash sign','$':'dollar sign','%':'percent sign','&':'ampersand','*':'asterisk','+':'plus','=':'equals','/':'forward slash','\\':'backslash',"'":'apostrophe','"':'quotation mark','[':'left bracket',']':'right bracket','{':'left brace','}':'right brace','<':'less than','>':'greater than','^':'caret','|':'vertical bar','~':'tilde','`':'grave accent','\r':'carriage return'})

def character(value):
    if not value:return 'End of text.'
    if value in NAMES:return NAMES[value]
    if value.isalnum() or len(value)!=1:return CharacterSpeech(value,True) if len(value)==1 and value.isalpha() and value.isupper() else value
    return unicodedata.name(value,'character '+str(ord(value))).lower()
def phonetic(value):return PHONETICS.get(value.lower(),'')
def document_text(doc):return '\n'.join(p.text for p in doc.paragraphs)
def absolute(doc,position):return sum(len(p.text)+1 for p in doc.paragraphs[:position[0]])+position[1]

def keyboard_undo_step(doc,start,removed,inserted):
    """One Undo step for a run of typing, not one per key.

    Letters typed one after another join the step before them, until a sentence ends,
    Enter is pressed, or anything else happens (a voice command, a jump with the arrows).
    Backspace and Delete pressed one after another join in the same way."""
    from document_editor import undo_words
    def words(text):
        if undo_words(text):return undo_words(text)
        if text.strip():return ' '.join(str(character(c)) for c in text.strip()[:5])
        return 'a line break' if '\n' in text else 'a space'
    step=doc.undo_steps[-1] if doc.undo_steps and doc.last_change_type=='keyboard' else None
    run=step.run if step is not None else None
    if inserted and not removed and '\n' not in inserted:
        if run and run[0]=='typing' and run[2]==start and not re.search(r'[.!?]\s$|\n',run[3]):
            text=run[3]+inserted;step.run=('typing',run[1],start+len(inserted),text);step.label='typing: '+words(text)
            return
        doc.checkpoint('typing: '+words(inserted));doc.undo_steps[-1].run=('typing',start,start+len(inserted),inserted);return
    if removed and not inserted:
        if run and run[0]=='deleting' and start+len(removed)==run[1]:      # Backspace again
            text=removed+run[3];step.run=('deleting',start,start,text);step.label='deleting: '+words(text);return
        if run and run[0]=='deleting' and start==run[1]:                   # Delete again
            text=run[3]+removed;step.run=('deleting',start,start,text);step.label='deleting: '+words(text);return
        doc.checkpoint('deleting: '+words(removed));doc.undo_steps[-1].run=('deleting',start,start,removed);return
    doc.checkpoint('typing: '+words(inserted) if inserted.strip() else 'the change');return

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
        pieces=after[start:end_new].split('\n');keyboard_undo_step(doc,start,before[start:end_old],after[start:end_new]);created=[]
        for index,piece in enumerate(pieces):
            p=copy.deepcopy(first if index==0 or index<len(pieces)-1 else last)
            p.text=(prefix if index==0 else '')+piece+(suffix if index==len(pieces)-1 else '')
            styles=(prefix_styles if index==0 else [])+[inherited]*len(piece)+(suffix_styles if index==len(pieces)-1 else [])
            p.runs=doc.compress_runs(p.text,styles);created.append(p)
        doc.paragraphs[i:j+1]=created
        doc.last_change_type='keyboard'
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
    index=bisect_right(starts,caret)-1
    # A caret on blank lines before the first paragraph moves forward to that paragraph.
    if index<0:return starts[0]
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
        return text[start:end] or 'Blank.',''
    char=text[caret:caret+1];return character(char),char

def typing_feedback(before,after,caret,echo,key=None,old_caret=None):
    if echo=='none' or before==after:return None
    if len(after)<len(before):
        if key=='Delete':return character(after[caret:caret+1])
        if key=='BackSpace' and old_caret is not None:
            removed=before[caret:old_caret]
            return character(removed[-1:]) if removed else 'Deleted.'
        return 'Deleted.'
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
    result='. '.join(parts) or None
    return preserve_character(inserted,result) if result else None

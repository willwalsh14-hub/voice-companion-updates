"""Repeatable English reading-level estimates for user-facing guide text.

Flesch-Kincaid is an estimate, not a WCAG conformance certificate. Exact
product names and commands stay visible in the measured text. Short sections
are also reviewed by an editor; a score alone cannot establish clarity.
"""
import json
import re
from pathlib import Path
from guide_text import read_guide

SYLLABLES = {'companion':3,'voice':1,'email':2,'emails':2,'microphone':3,'area':3,'read':1,
             'reading':2,'line':1,'lines':1,'file':1,'files':1,'guide':1,'guides':1,
             'saved':1,'saves':1,'named':1,'names':1,'typed':1,'typing':2,'every':2,
             'usually':3,'available':4,'different':3,'selected':3,'selection':3,
             'changes':2,'changed':1,'choices':2,'choose':1,'chosen':2,'value':2,
             'checkbox':2,'checkboxes':3,'paragraph':3,'paragraphs':3,'current':2,
             'sapi':2,'espeak':2,'html':4,'epub':2,'docx':1,'brf':3,'daisy':2,
             'okay':2,'menu':2,'menus':2,'controls':2,'control':2,'quiet':2,
             'required':2,'requires':2,'recognized':3,'password':2,'passwords':2,
             'internet':3,'windows':2,'documents':3,'document':3,'radio':3,'audio':3}


def syllables(word):
    word=word.lower()
    if word in SYLLABLES:return SYLLABLES[word]
    word=re.sub(r"[^a-z]",'',word)
    if not word:return 1
    count=len(re.findall(r'[aeiouy]+',word))
    if word.endswith('e') and not word.endswith(('le','ye')):count-=1
    if word.endswith('ed') and not word.endswith(('ted','ded')):count-=1
    return max(1,count)


def reading_level(text):
    # A numbered step is one instruction; its leading number is not a sentence.
    text=re.sub(r'(?m)^\d+\.\s+','',text)
    text=re.sub(r'(?m)^- (.+)$',r'\1.',text)
    # A file extension and an address are names, not multiple prose sentences.
    text=re.sub(r'\b[\w.+-]+@[\w.-]+\b','email address',text)
    text=re.sub(r'\b\w+\.(?:com|org|net|exe|docx|txt|opf|epub)\b','file',text,flags=re.I)
    words=re.findall(r"[A-Za-z]+(?:'[A-Za-z]+)?|\d+",text)
    if not words:return 0.0
    sentences=[x for x in re.split(r'[.!?]["”]?\s+|[.!?]["”]?$',text) if re.search(r'\w',x)]
    count=max(1,len(sentences))
    return round(max(0,.39*len(words)/count+11.8*sum(syllables(w) for w in words)/len(words)-15.59),2)


def report(root):
    root=Path(root)
    _,front,chapters=read_guide(root/'START HERE - Veteran.txt')
    sections=[]
    for chapter in chapters:
        for part in chapter['parts']:
            text='\n'.join(part['paragraphs'])
            sections.append({'part':part['heading'],'words':len(text.split()),'estimated_grade':reading_level(text)})
    notes=json.loads((root/'release-notes.json').read_text())
    note_text=notes['scope']+' '+' '.join(detail for section in notes['sections'] for topic in section['topics'] for detail in topic['details'])
    note_parts=[{'part':topic['title']+' detail '+str(index),'estimated_grade':reading_level(detail)}
                for section in notes['sections'] for topic in section['topics']
                for index,detail in enumerate(topic['details'],1)]
    from tutorial_curriculum import COURSES,option_explanation
    from settings_model import CATEGORIES,fields
    lessons=[{'part':name+' '+title,'estimated_grade':reading_level(text)} for _,name,_,intro,_ in COURSES for title,text in intro]
    lessons.extend({'part':category+' '+field.label,'estimated_grade':reading_level(option_explanation(field))}
                   for category in CATEGORIES for field in fields(category,{}))
    all_text=' '.join(front+[p for c in chapters for p in c['paragraphs']])
    setup_parts=[]
    for name in ('README.txt','AI VOICE SETUP - Trainer.txt','EMAIL SETUP - Helper.txt',
                 'UPDATES - Trainer.txt','TRAINER TEST CHECKLIST.txt'):
        parts=re.split(r'(?:\A|\n\n)\d+\. ([^\n]+)\n',(root/name).read_text())
        for index in range(1,len(parts),2):
            setup_parts.append({'part':name+' '+parts[index],
                                'estimated_grade':reading_level(parts[index+1])})
    return {'method':'Flesch-Kincaid estimate with a documented English syllable heuristic',
            'manual_estimated_grade':reading_level(all_text),
            'release_notes_estimated_grade':reading_level(note_text),
            'manual_parts':sections,'tutorial_parts':lessons,'release_note_parts':note_parts,
            'setup_parts':setup_parts,
            'over_target':[x for x in sections+lessons+note_parts+setup_parts if x['estimated_grade']>8]}


if __name__=='__main__':
    print(json.dumps(report(Path(__file__).parent),indent=2))

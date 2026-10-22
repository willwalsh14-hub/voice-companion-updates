"""Passive HTML email rendering: readable text and safe links, no remote content."""
import re
from html.parser import HTMLParser
from urllib.parse import urlsplit

class MessageBody(str):
    def __new__(cls, text, source='', spans=()):
        obj=super().__new__(cls,text);obj.html=source;obj.spans=list(spans);obj.plain=text;return obj

def clean_plain(text):
    text=str(text).replace('\r\n','\n').replace('\r','\n')
    text='\n'.join(line.rstrip() for line in text.split('\n'))
    return re.sub(r'\n{3,}','\n\n',text).strip('\n')

def safe_link(url):
    try:return urlsplit(url).scheme.lower() in ('http','https','mailto')
    except ValueError:return False

def message_controls(body,role=None):
    """Semantic stops, including passive buttons which have no safe URL."""
    items=sorted((span for span in getattr(body,'spans',[]) if span[2] in ('link','button')),key=lambda s:(s[0],s[1]-s[0],s[2]!='button'))
    result=[]
    for item in items:
        if role=='button' and item[2]!='button':continue
        if role=='link' and not item[3]:continue
        if any(item[:2]==other[:2] and item[3]==other[3] for other in result):continue
        if item[2]=='link' and any(a>=item[0] and b<=item[1] and style=='button' and url==item[3] for a,b,style,url in items):continue
        result.append(item)
    return result

def spoken_range(body,start,end):
    """Report roles while reading without inserting them into selectable text."""
    text=str(body)[start:end]
    for a,b,role,url in reversed(message_controls(body)):
        if a<end and b>start:
            at=min(b,end)-start
            text=text[:at]+', '+role+(' ' if text[at:at+1] and not text[at:at+1].isspace() else '')+text[at:]
    return text.strip() or 'Blank.'

class Renderer(HTMLParser):
    blocks={'p','div','section','article','blockquote','h1','h2','h3','h4','h5','h6','tr','ul','ol'}
    styles={'b':'bold','strong':'bold','i':'italic','em':'italic','u':'underline','h1':'heading','h2':'heading','h3':'heading'}
    head_tags={'title','meta','link','base','style','script','noscript','template','xml'}
    def __init__(self):
        super().__init__(convert_charrefs=True);self.text='';self.spans=[];self.active=[];self.hidden=0;self.lists=[];self.control_labels={}
        self.in_head=False
    def newline(self):
        self.text=self.text.rstrip(' ')
        if self.text and not self.text.endswith('\n'):self.text+='\n'
    def handle_starttag(self,tag,attrs):
        attrs=dict(attrs)
        # Browsers end <head> at the first body tag even when </head> is missing.
        if tag=='head':self.in_head=True;return
        if self.in_head and tag not in self.head_tags:self.in_head=False
        # Outlook stores page settings in <xml> blocks; they are never message text.
        if tag in ('script','style','xml') or tag=='title' and self.in_head:self.hidden+=1;return
        if self.hidden or self.in_head:return
        if tag in self.blocks or tag=='li':self.newline()
        if tag in ('ul','ol'):self.lists.append([tag,0])
        if tag=='li':
            if self.lists:self.lists[-1][1]+=1
            self.text+=(str(self.lists[-1][1])+'. ' if self.lists and self.lists[-1][0]=='ol' else '• ')
        if tag=='br' and not self.text.endswith('\n\n'):self.text+='\n'
        if tag in ('td','th') and self.text and not self.text.endswith(('\n',' ')):self.text+=' | '
        style=self.styles.get(tag)
        if tag in ('a','v:roundrect','v:rect') and safe_link(attrs.get('href','')):
            style='button' if attrs.get('role','').lower()=='button' or tag.startswith('v:') else 'link'
            self.active.append((tag,len(self.text),style,attrs['href']))
        elif tag=='button' or attrs.get('role','').lower()=='button':
            url=next((url for name,start,style,url in reversed(self.active) if style in ('link','button') and url),'')
            self.active.append((tag,len(self.text),'button',url))
        elif style:self.active.append((tag,len(self.text),style,''))
        if tag=='img':self.text+=attrs.get('alt') or attrs.get('aria-label') or attrs.get('title') or ''
        if style in ('link','button') or tag=='button' or attrs.get('role','').lower()=='button':
            label=attrs.get('aria-label') or attrs.get('title')
            if label:self.control_labels[(tag,len(self.text))]=label
    def handle_endtag(self,tag):
        if tag=='head':self.in_head=False;return
        if tag in ('script','style','xml') or tag=='title' and self.in_head:
            self.hidden=max(0,self.hidden-1);return
        if self.hidden or self.in_head:return
        for i in range(len(self.active)-1,-1,-1):
            name,start,style,url=self.active[i]
            if name==tag:
                if not self.text[start:].strip() and style in ('link','button'):self.text+=self.control_labels.get((tag,start)) or 'Unlabelled '+style
                self.spans.append((start,len(self.text),style,url));self.active.pop(i);break
        if tag in self.blocks or tag=='li':self.newline()
        if tag in ('ol','ul') and self.lists:self.lists.pop()
    def handle_data(self,data):
        if self.hidden:return
        if self.in_head:
            if not data.strip():return
            self.in_head=False
        value=re.sub(r'\s+',' ',data.replace('\xa0',' '))
        if self.text.endswith((' ','\n')) or not self.text:value=value.lstrip()
        self.text+=value

def render_html(source):
    parser=Renderer();parser.feed(source);parser.close()
    # Leading source whitespace is removed during parsing; trailing layout is irrelevant.
    text=parser.text.rstrip()
    return MessageBody(text,source,[(a,min(b,len(text)),s,u) for a,b,s,u in parser.spans if a<min(b,len(text))])

def plain_body(text):return MessageBody(clean_plain(text))

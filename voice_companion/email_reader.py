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

class Renderer(HTMLParser):
    blocks={'p','div','section','article','blockquote','h1','h2','h3','h4','h5','h6','tr','ul','ol'}
    styles={'b':'bold','strong':'bold','i':'italic','em':'italic','u':'underline','h1':'heading','h2':'heading','h3':'heading'}
    def __init__(self):
        super().__init__(convert_charrefs=True);self.text='';self.spans=[];self.active=[];self.hidden=0;self.lists=[]
    def newline(self):
        self.text=self.text.rstrip(' ')
        if self.text and not self.text.endswith('\n'):self.text+='\n'
    def handle_starttag(self,tag,attrs):
        attrs=dict(attrs)
        if tag in ('script','style','head'):self.hidden+=1;return
        if self.hidden:return
        if tag in self.blocks or tag=='li':self.newline()
        if tag in ('ul','ol'):self.lists.append([tag,0])
        if tag=='li':
            if self.lists:self.lists[-1][1]+=1
            self.text+=(str(self.lists[-1][1])+'. ' if self.lists and self.lists[-1][0]=='ol' else '• ')
        if tag=='br' and not self.text.endswith('\n\n'):self.text+='\n'
        if tag in ('td','th') and self.text and not self.text.endswith(('\n',' ')):self.text+=' | '
        style=self.styles.get(tag)
        if tag=='a' and safe_link(attrs.get('href','')):style='link';self.active.append((tag,len(self.text),style,attrs['href']))
        elif style:self.active.append((tag,len(self.text),style,''))
        if tag=='img' and attrs.get('alt'):self.text+=attrs['alt']
    def handle_endtag(self,tag):
        if tag in ('script','style','head'):
            self.hidden=max(0,self.hidden-1);return
        if self.hidden:return
        for i in range(len(self.active)-1,-1,-1):
            name,start,style,url=self.active[i]
            if name==tag:
                self.spans.append((start,len(self.text),style,url));self.active.pop(i);break
        if tag in self.blocks or tag=='li':self.newline()
        if tag in ('ol','ul') and self.lists:self.lists.pop()
    def handle_data(self,data):
        if self.hidden:return
        value=re.sub(r'\s+',' ',data.replace('\xa0',' '))
        if self.text.endswith((' ','\n')) or not self.text:value=value.lstrip()
        self.text+=value

def render_html(source):
    parser=Renderer();parser.feed(source);parser.close()
    # Leading source whitespace is removed during parsing; trailing layout is irrelevant.
    text=parser.text.rstrip()
    return MessageBody(text,source,[(a,min(b,len(text)),s,u) for a,b,s,u in parser.spans if a<min(b,len(text))])

def plain_body(text):return MessageBody(clean_plain(text))

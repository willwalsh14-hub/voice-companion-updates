"""Render the same rich message in saved drafts and provider payloads."""
import html
import re
from document_editor import TextRun, VoiceDocument


def formatted_body(draft):
    def paragraph(p, tag='p'):
        VoiceDocument.reconcile_runs(p)
        content = ''
        for run in p.runs or [TextRun(p.text,p.bold,p.italic,p.underline,p.font,p.size)]:
            text = html.escape(run.text).replace('\n','<br>')
            if run.bold: text = '<strong>'+text+'</strong>'
            if run.italic: text = '<em>'+text+'</em>'
            if run.underline: text = '<u>'+text+'</u>'
            if run.highlight: text = '<mark>'+text+'</mark>'
            styles = []
            if run.font:
                font = re.sub(r'[^\w ,.-]', '', run.font)
                styles.append('font-family:'+font)
            if run.size: styles.append('font-size:'+str(int(run.size))+'pt')
            if run.highlight: styles.append('background-color:'+({'yellow':'#ffff00','green':'#008000','cyan':'#00ffff','magenta':'#ff00ff','blue':'#0000ff','red':'#ff0000'}.get(run.highlight,'#ffff00')))
            if styles: text = '<span style="'+html.escape(';'.join(styles),quote=True)+'">'+text+'</span>'
            content += text
        styles = ['white-space:pre-wrap','text-align:'+({'both':'justify','left':'left','right':'right','center':'center'}.get(p.alignment,'left'))]
        if p.line_spacing_twips:
            if p.line_spacing_rule == 'auto': styles.append('line-height:'+format(p.line_spacing_twips/240,'.4g'))
            else:
                styles.append('line-height:'+format(p.line_spacing_twips/20,'.4g')+'pt')
                if p.line_spacing_rule == 'exact': styles.append('mso-line-height-rule:exactly')
        return '<'+tag+' style="'+';'.join(styles)+'">'+content+'</'+tag+'>'
    pieces=[];index=0
    while index<len(draft.paragraphs):
        p=draft.paragraphs[index]
        if p.table_id:
            cells=[];table_id=p.table_id
            while index<len(draft.paragraphs) and draft.paragraphs[index].table_id==table_id:
                cells.append(draft.paragraphs[index]);index+=1
            rows=[]
            for row in sorted({c.row for c in cells}):
                rows.append('<tr>'+''.join('<td style="border:1px solid #777;padding:6px;vertical-align:top">'+paragraph(c)+'</td>' for c in sorted((c for c in cells if c.row==row),key=lambda c:c.column))+'</tr>')
            pieces.append('<table border="1" cellpadding="6" cellspacing="0" style="border-collapse:collapse">'+''.join(rows)+'</table>')
        elif p.kind in ('Bulleted list','Numbered list'):
            kind=p.kind;tag='ul' if kind=='Bulleted list' else 'ol';items=[]
            while index<len(draft.paragraphs) and not draft.paragraphs[index].table_id and draft.paragraphs[index].kind==kind:
                items.append(paragraph(draft.paragraphs[index],'li'));index+=1
            pieces.append('<'+tag+'>'+''.join(items)+'</'+tag+'>')
        else:
            match=re.fullmatch(r'Heading ([1-6])',p.kind)
            pieces.append(paragraph(p,'h'+match[1] if match else 'p'));index+=1
    if draft.response_context.get('action')=='forward':
        context=draft.response_context
        pieces.append('<p>Forwarded message:<br>From: '+html.escape(context.get('original_from',''))+'<br>Subject: '+html.escape(context.get('original_subject',''))+'</p><p>'+html.escape(context.get('original_body','')).replace('\n','<br>')+'</p>')
    return '<html><body>'+''.join(pieces)+'</body></html>'

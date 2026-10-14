"""Build-time source checks. Never include detected secret values in output."""
import ast,json,re,sys
from pathlib import Path

SECRET=re.compile(r'(?:sk-[A-Za-z0-9_-]{24,}|gh[pousr]_[A-Za-z0-9]{30,}|-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----)')

def scan(root):
    findings=[]
    for path in sorted(Path(root).glob('*.py')):
        if path.name.startswith('test_') or path.name=='check_security.py':continue
        source=path.read_text(encoding='utf-8-sig')
        for number,line in enumerate(source.splitlines(),1):
            if SECRET.search(line):findings.append({'file':path.name,'line':number,'rule':'Possible embedded private credential'})
        tree=ast.parse(source)
        for node in ast.walk(tree):
            if isinstance(node,ast.Call):
                name=node.func.id if isinstance(node.func,ast.Name) else node.func.attr if isinstance(node.func,ast.Attribute) else ''
                if name in ('eval','exec','_create_unverified_context'):
                    findings.append({'file':path.name,'line':node.lineno,'rule':'Unsafe dynamic execution or TLS bypass'})
                for keyword in node.keywords:
                    if isinstance(keyword.value,ast.Constant) and ((keyword.arg=='shell' and keyword.value.value is True) or (keyword.arg=='verify' and keyword.value.value is False)):
                        findings.append({'file':path.name,'line':node.lineno,'rule':'Shell execution or TLS verification disabled'})
    return findings

if __name__=='__main__':
    found=scan(Path(__file__).parent)
    print(json.dumps({'scope':'Application Python source; excludes test fixtures','findings':found},indent=2))
    raise SystemExit(1 if found else 0)

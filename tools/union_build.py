"""Resolve build-windows.ps1 merge conflicts by taking the union of test names and hidden imports."""
import re
import sys
from pathlib import Path

path = Path(sys.argv[1])
text = path.read_text(encoding='utf-8')
PATTERN = r"<<<<<<< [^\n]*\n(.*?)=======\n(.*?)>>>>>>> [^\n]*\n"


def merge(match):
    ours, theirs = match[1], match[2]
    if "'test_" in ours:
        names = re.findall(r"'(test_[A-Za-z0-9_]+)'", ours)
        extra = [n for n in re.findall(r"'(test_[A-Za-z0-9_]+)'", theirs) if n not in names]
        if extra:
            last = names[-1]
            ours = ours.replace("'" + last + "')", "'" + last + "'," + ','.join("'" + n + "'" for n in extra) + ")", 1)
        return ours
    if "'--hidden-import'" in ours:
        mods = re.findall(r"'--hidden-import', '([^']+)'", ours)
        extra = [m for m in re.findall(r"'--hidden-import', '([^']+)'", theirs) if m not in mods]
        line = ours.rstrip('\n')
        if extra:
            line = line + ' ' + ' '.join("'--hidden-import', '" + m + "'," for m in extra)
        return line + '\n'
    raise SystemExit('Unknown conflict:\n' + ours + '\n----\n' + theirs)


text = re.sub(PATTERN, merge, text, flags=re.S)
assert '<<<<<<<' not in text
path.write_text(text, encoding='utf-8')
print('resolved', path)

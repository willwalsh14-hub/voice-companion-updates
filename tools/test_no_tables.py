"""Keep tables out of the repository's documents.

Will reads everything with a screen reader. Tables are slow and confusing to
read that way, so every report, guide and note uses headings and plain bulleted
lists instead. This check fails if any tracked Markdown or HTML file has a
Markdown table row or an HTML <table>.

  python3 -m unittest discover -s tools -p 'test_*.py'

tools/run_checks.py runs it on every check.

Program code that builds tables from a user's own content (a Word table read
aloud, a table in a rich email) is not a document and is not checked here.
"""
import re
import subprocess
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DOCUMENT_TYPES = ('*.md', '*.markdown', '*.html', '*.htm')
# A Markdown table row starts and ends with a pipe: "| a | b |".
TABLE_ROW = re.compile(r'^\s*\|.*\|\s*$')
# The line under a table's header: "--- | ---", "|:--|--:|".
DIVIDER_ROW = re.compile(r'^\s*\|?\s*:?-{3,}:?\s*(\|\s*:?-{3,}:?\s*)+\|?\s*$')
HTML_TABLE = re.compile(r'<\s*table\b', re.I)


def tracked_documents(root=ROOT):
    names = subprocess.run(['git', 'ls-files', '-z', '--', *DOCUMENT_TYPES], cwd=root,
                           capture_output=True, check=True).stdout.decode('utf-8').split('\0')
    return [root / name for name in names if name]


def table_lines(text):
    """Line numbers and text of every table line found in one document."""
    found = []
    for number, line in enumerate(text.splitlines(), 1):
        if TABLE_ROW.match(line) or DIVIDER_ROW.match(line) or HTML_TABLE.search(line):
            found.append((number, line.strip()))
    return found


def report(root=ROOT):
    """Every table line in every tracked document, as 'path:line: text'."""
    problems = []
    for path in tracked_documents(root):
        if not path.is_file():
            continue
        text = path.read_text(encoding='utf-8', errors='replace')
        for number, line in table_lines(text):
            problems.append(f'{path.relative_to(root).as_posix()}:{number}: {line[:100]}')
    return problems


class NoTablesTests(unittest.TestCase):
    def test_finds_markdown_and_html_tables(self):
        self.assertTrue(table_lines('| Command | Key |\n| --- | --- |\n| Wake up | F12 |'))
        self.assertTrue(table_lines('Command | Key\n--- | ---\n'))
        self.assertTrue(table_lines('|:--|--:|'))
        self.assertTrue(table_lines('<p>Keys</p><TABLE border="1"><tr><td>F12</td></tr></TABLE>'))

    def test_headings_lists_and_ordinary_pipes_are_fine(self):
        text = ('# Keys\n\n## Wake up\n\n- Voice: "Wake up"\n- Keyboard: F12\n\n---\n\n'
                'Run `git log | head` to see it.\n1. Press Alt.\n')
        self.assertEqual(table_lines(text), [])

    def test_no_tracked_document_has_a_table(self):
        problems = report()
        self.assertEqual(problems, [], 'Tables found. Use a heading or bullet for each item instead:\n'
                         + '\n'.join(problems))


if __name__ == '__main__':
    unittest.main()

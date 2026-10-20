"""Reviewable command inventory from the same menus used by the application."""
from pathlib import Path


def catalog():
    import companion as app
    from file_manager import MENU
    from task_navigation import DOCUMENT_ACTIONS
    from settings_model import CATEGORIES,fields
    rows=[]
    def add(area,name,voice,key):rows.append({'area':area,'action':name,'voice':voice,'keyboard':key})
    for name,voice in app.MAIN_MENU_CHOICES:add('Main menu',name,voice,'First letter, arrows, Enter')
    for name,key,voice in MENU:add('File manager',name,voice,key)
    for group,items in DOCUMENT_ACTIONS.items():
        for name,key,voice in items:add('Document: '+group,name,voice,key or 'Alt, group number, item key')
    nav=app.task_navigation()
    for mode in ('word_processor','media_menu','local_media'):
        for name,key,voice in nav.items(mode):add(mode.replace('_',' ').title(),name,name if voice.startswith('@') else voice,key or 'First letter, arrows, Enter')
    for category in CATEGORIES:
        for field in fields(category,{}):add(category,field.label,'Choose '+field.label,'Arrows to field; Space, text or arrows; Enter to save')
    from browser_menu import BrowserMenu
    from types import SimpleNamespace
    # Bypass history loading: choices depends only on the session's page state.
    menu=object.__new__(BrowserMenu)
    for name,voice in menu.choices(SimpleNamespace(snapshot=None,backend=None),False):add('Browser menu',name,name,'First letters, arrows, Enter')
    add('All command views','Enter a spoken command with the keyboard','Say the desired command','Alt+T, type the same command, Enter')
    return rows


def render():
    lines=['Voice Companion voice and keyboard command inventory','',
           'This inventory covers declared menu actions. It is not a claim of exhaustive testing of every website or microphone phrase.',
           'Dynamic items such as files, emails, stations and favorites use their spoken names or list arrows and Enter.','']
    area=None
    for row in catalog():
        if row['area']!=area:area=row['area'];lines.extend(['',area])
        lines.append(row['action']+' | Say: '+row['voice']+' | Keys: '+row['keyboard'])
    return '\n'.join(lines)+'\n'


if __name__=='__main__':print(render())

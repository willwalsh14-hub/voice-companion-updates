"""Trainer GUI for a GitHub release feed and checksum manifest. No credentials needed."""
import hashlib
import json
from pathlib import Path
import re
from app_updates import MAX_INSTALLER, https_url, version_key

ROOT = Path(__file__).parent


def release_manifest(setup, version, installer_url):
    version_key(version); https_url(installer_url)
    setup = Path(setup)
    if not setup.is_file() or setup.suffix.lower() != '.exe': raise ValueError('Choose the finished Setup EXE, not a source ZIP.')
    if not 1 <= setup.stat().st_size <= MAX_INSTALLER: raise ValueError('The installer must be smaller than 2 GiB for GitHub Releases.')
    with setup.open('rb') as source:
        if source.read(2) != b'MZ': raise ValueError('Choose a Windows Setup EXE.')
        source.seek(0); digest = hashlib.file_digest(source,'sha256').hexdigest()
    return {'schema':1,'version':version,'installer_url':installer_url,'sha256':digest,'size':setup.stat().st_size}


def main():
    import tkinter as tk
    from tkinter import filedialog,messagebox
    window=tk.Tk();window.title('Voice Companion Update Publishing')
    tk.Label(window,text='GitHub repository (owner/repository)').pack(anchor='w')
    repository=tk.Entry(window,width=70);repository.pack()
    try:
        feed=json.loads((ROOT/'update-settings.json').read_text()).get('manifest_url','')
        configured=re.fullmatch(r'https://github\.com/([^/]+/[^/]+)/releases/latest/download/voice-companion-update\.json',feed)
        if configured: repository.insert(0,configured[1])
    except (OSError,ValueError): pass
    tk.Label(window,text='Finished Setup EXE (only needed to prepare a release)').pack(anchor='w')
    setup=tk.Entry(window,width=70);setup.pack()
    def choose():
        path=filedialog.askopenfilename(filetypes=[('Windows Setup','*.exe')])
        if path: setup.delete(0,'end');setup.insert(0,path)
    tk.Button(window,text='Choose Setup file',command=choose).pack()
    version=re.search(r"APP_VERSION = '([^']+)'",(ROOT/'companion.py').read_text())[1]
    def addresses():
        value=repository.get().strip()
        if not re.fullmatch(r'[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+',value): raise ValueError('Enter owner/repository, for example yourname/voice-companion-updates.')
        return ('https://github.com/'+value+'/releases/latest/download/voice-companion-update.json',
                'https://github.com/'+value+'/releases/download/v'+version+'/VoiceCompanion-Setup.exe')
    def configure():
        try:
            feed,_=addresses()
            (ROOT/'update-settings.json').write_text(json.dumps({'manifest_url':feed},indent=2)+'\n')
            messagebox.showinfo('Saved','Update server configured for future builds. Now build the installer. Existing installations need this first update-enabled installer once.')
        except (OSError,ValueError) as exc: messagebox.showerror('Could not save',str(exc))
    def prepare():
        try:
            _,url=addresses();manifest=release_manifest(setup.get(),version,url)
            output=ROOT/'release-upload';output.mkdir(exist_ok=True)
            # This name is fixed so the version-specific manifest URL matches its asset.
            import shutil
            shutil.copy2(setup.get(),output/'VoiceCompanion-Setup.exe')
            (output/'voice-companion-update.json').write_text(json.dumps(manifest,indent=2)+'\n')
            messagebox.showinfo('Release files ready','Upload BOTH files from release-upload to a public GitHub Release with tag v'+version+'. Publish it as the latest release, not a draft or prerelease. This tool creates files; it does not upload them.')
        except (OSError,ValueError) as exc: messagebox.showerror('Could not prepare',str(exc))
    tk.Button(window,text='1. Save update server before building',command=configure).pack()
    tk.Button(window,text='2. Prepare finished installer for upload',command=prepare).pack()
    tk.Button(window,text='Close',command=window.destroy).pack()
    window.mainloop()

if __name__=='__main__': main()

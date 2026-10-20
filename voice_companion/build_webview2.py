"""Build the owned WebView2 host from the pinned Microsoft SDK on Windows."""
from pathlib import Path
import hashlib
import os
import subprocess
import urllib.request
import zipfile

VERSION='1.0.4258.31'
SHA256='56f7f4b8bf9aee4b8efefbbdd4f67d5f74ebd1b100ed0806da71bf76af481aa9'


def build(root=None):
    root=Path(root or Path(__file__).parent).resolve();out=root/'webview2';out.mkdir(exist_ok=True)
    cache=Path(os.getenv('LOCALAPPDATA',str(root)))/'VCBuild'/'webview2-sdk';cache.mkdir(parents=True,exist_ok=True)
    package=cache/('webview2-'+VERSION+'.nupkg')
    if not package.is_file() or hashlib.sha256(package.read_bytes()).hexdigest()!=SHA256:
        data=urllib.request.urlopen('https://api.nuget.org/v3-flatcontainer/microsoft.web.webview2/'+VERSION+'/microsoft.web.webview2.'+VERSION+'.nupkg',timeout=60).read()
        if hashlib.sha256(data).hexdigest()!=SHA256:raise ValueError('Microsoft WebView2 SDK checksum differs from the verified package.')
        package.write_bytes(data)
    with zipfile.ZipFile(package) as archive:
        for name in ('lib/net462/Microsoft.Web.WebView2.Core.dll','lib/net462/Microsoft.Web.WebView2.WinForms.dll','runtimes/win-x64/native/WebView2Loader.dll'):
            (out/Path(name).name).write_bytes(archive.read(name))
        for name in archive.namelist():
            if Path(name).name.lower() in ('license.txt','license','thirdpartynotices.txt','thirdpartynotices'):(out/Path(name).name).write_bytes(archive.read(name))
    compiler=Path(os.getenv('WINDIR','C:/Windows'))/'Microsoft.NET'/'Framework64'/'v4.0.30319'/'csc.exe'
    subprocess.run([str(compiler),'/nologo','/target:exe','/platform:x64','/out:VoiceCompanion-WebView2.exe',
                    '/reference:System.Windows.Forms.dll','/reference:System.Drawing.dll','/reference:System.Web.Extensions.dll',
                    '/reference:Microsoft.Web.WebView2.Core.dll','/reference:Microsoft.Web.WebView2.WinForms.dll',str(root/'webview2_host.cs')],cwd=out,check=True)
    return out/'VoiceCompanion-WebView2.exe'


if __name__=='__main__':print(build())

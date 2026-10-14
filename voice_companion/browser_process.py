"""Keep frozen application DLLs away from the browser's native processes."""
import contextlib
import ctypes
import os
import sys
import threading
from pathlib import Path

LOCK=threading.RLock()

@contextlib.contextmanager
def browser_process_environment(folder):
    if os.name!='nt' or not getattr(sys,'frozen',False):
        yield;return
    with LOCK:
        kernel=ctypes.windll.kernel32
        length=kernel.GetDllDirectoryW(0,None)
        previous=ctypes.create_unicode_buffer(length+1)
        if length:kernel.GetDllDirectoryW(len(previous),previous)
        if not kernel.SetDllDirectoryW(None):raise OSError('Browser DLL environment could not be prepared.')
        old_path=os.environ.get('PATH');old_cwd=Path.cwd()
        roots=[Path(sys.executable).parent.resolve(),Path(getattr(sys,'_MEIPASS',Path(sys.executable).parent)).resolve()]
        def bundled(value):
            try:return any(Path(value.strip('"')).resolve().is_relative_to(root) for root in roots)
            except (OSError,ValueError):return False
        try:
            os.environ['PATH']=';'.join(p for p in (old_path or '').split(';') if p and not bundled(p))
            os.chdir(folder)
            yield
        finally:
            os.chdir(old_cwd)
            if old_path is None:os.environ.pop('PATH',None)
            else:os.environ['PATH']=old_path
            if not kernel.SetDllDirectoryW(previous.value if length else None):raise OSError('Application DLL environment could not be restored.')

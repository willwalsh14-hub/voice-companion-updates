import json, os, subprocess, sys, time, traceback
from pathlib import Path
HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import check_thunderbird_upgrade_live as live
def note(t, x):
    print('::warning title=' + t + '::' + str(x).encode('ascii','replace').decode().replace('\n','%0A')[:1500], flush=True)
work = Path(sys.argv[1]); work.mkdir(parents=True, exist_ok=True)
os.environ['VOICE_COMPANION_DATA_DIR'] = str(work / 'data')
import thunderbird_bridge, thunderbird_setup
from build_thunderbird_addon import build
system = thunderbird_setup.WindowsSystem()
try:
    installer = work / 'tb.exe'
    system.download(thunderbird_setup.INSTALLER_URL, installer)
    live.run([str(installer), '/S'], timeout=600)
    program, version = thunderbird_setup.find_program(system)
    note('installed', '%s %s' % (program, version))
    system.launch_background(program)
    live.wait(lambda: any((p / 'prefs.js').is_file() for p in thunderbird_setup.profile_candidates(system)), 180)
    system.close_program(); live.wait(lambda: not system.running(), 60)
    if system.running(): live.run(['taskkill', '/F', '/IM', 'thunderbird.exe']); live.wait(lambda: not system.running(), 30)
    profile = thunderbird_setup.profile_candidates(system)[0]
    with open(profile / 'prefs.js', 'a', encoding='utf-8') as prefs:
        prefs.write('\n' + '\n'.join(live.ACCOUNTS) + '\n')
    package = build(work / 'addon.xpi')
    thunderbird_setup.install_addon(profile, package)
    live.register_host(work)
    relay = thunderbird_bridge.prepare(work / 'data')
    # First start registers the add-on; let it connect once with a normal window.
    system.launch_background(program)
    note('first start connected', relay.connected.wait(180))
    import tkinter as tk, window_hiding
    desktop = window_hiding.Win32Desktop()
    root = tk.Tk(); root.title('VC stand-in'); root.geometry('300x100+10+10'); root.update(); root.focus_force(); root.update()
    time.sleep(3); root.update()
    mains = [w for w in desktop.windows() if w.kind == window_hiding.MAIN_CLASS and not w.owned]
    for w in mains: desktop.minimize(w.handle)
    time.sleep(1); root.update(); root.focus_force(); root.update()
    me = desktop.foreground()
    note('before', 'fg=%s windows=%r' % (me, desktop.windows()))
    c1 = system.wake_windows(1); time.sleep(0.5); root.update()
    note('wake 1', 'changed=%r fg_same=%s windows=%r' % (c1, desktop.foreground() == me, desktop.windows()))
    for w in mains: desktop.minimize(w.handle)
    time.sleep(0.5); root.update()
    c2 = system.wake_windows(2); time.sleep(0.5); root.update()
    note('wake 2', 'changed=%r fg_back=%s fg=%s me=%s windows=%r' % (c2, desktop.foreground() == me, desktop.foreground(), me, desktop.windows()))
    system.settle_windows(c1 + c2); time.sleep(0.5)
    note('settled', '%r' % desktop.windows())
    root.destroy()
except Exception:
    note('crash', traceback.format_exc())

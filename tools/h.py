import gui, time, sys
from pathlib import Path
import os
S = os.environ.get("CLIPVEC_SCRATCHPAD", "shots")  # screenshot output dir
def c(x, y, **k): gui.click(x*2, y*2, **k); time.sleep(0.3)
def shot(name="s.png", region=(0,0,1440,900)):
    if region: region = tuple(v*2 for v in region)
    gui.screenshot(S + "/" + name, region=region, scale=0.5)
SAMPLES = str((Path(__file__).resolve().parent.parent / "samples"))  # repo samples dir
def focus_csp():
    ws=[w for w in gui.list_windows('CLIP STUDIO PAINT') if 'PRO' in w[1] or '.clip' in w[1] or 'px' in w[1]]
    h=(ws or gui.list_windows('CLIP STUDIO PAINT'))[0][0]
    gui.user32.SetForegroundWindow(h); gui.focus(h)
    fg=gui.user32.GetForegroundWindow()
    if fg!=h: raise RuntimeError('CSP not in foreground; aborting to avoid typing elsewhere')

def _pid(h):
    import ctypes
    pid = ctypes.c_ulong()
    gui.user32.GetWindowThreadProcessId(h, ctypes.byref(pid))
    return pid.value

def assert_csp_foreground():
    h = gui.list_windows('CLIP STUDIO PAINT')[0][0]
    fg = gui.user32.GetForegroundWindow()
    if _pid(fg) != _pid(h):
        raise RuntimeError('foreground window is not CLIP STUDIO PAINT; refusing to type')

def save_as(name):
    focus_csp()
    import os
    p = SAMPLES + "\\" + name
    if os.path.exists(p): os.remove(p)
    gui.key('ctrl+shift+s'); time.sleep(1.5)
    assert_csp_foreground()
    gui.key('ctrl+a'); gui.type_text(p); time.sleep(0.2); gui.key('enter')
    for _ in range(40):
        time.sleep(0.25)
        if os.path.exists(p): break
    time.sleep(1.0)
    return os.path.exists(p)
OX, OY = 972, 430  # physical screen px of canvas (0,0) at 100% zoom
def pen(pts, **k):
    gui.pen_stroke([(OX + x, OY + y, p) for x, y, p in pts], **k)
def line(x0, y0, x1, y1, p, n=60, **k):
    if callable(p):
        f = p
    else:
        f = lambda t: p
    pen([(x0 + (x1 - x0) * i / n, y0 + (y1 - y0) * i / n, f(i / n)) for i in range(n + 1)], **k)
def open_file(path):
    focus_csp()
    gui.key('ctrl+o'); time.sleep(1.5)
    assert_csp_foreground()
    gui.key('ctrl+a'); gui.type_text(path); time.sleep(0.2); gui.key('enter')
    time.sleep(3)
def clear():
    gui.key('ctrl+a'); gui.key('del'); gui.key('ctrl+d'); time.sleep(0.3)
def new_doc_with_vector_layer():
    gui.key('ctrl+n'); time.sleep(2); c(998,238); time.sleep(2)
    gui.key('ctrl+alt+0'); time.sleep(0.5)
    c(261,35); time.sleep(0.6); c(300,79); time.sleep(0.6); c(593,101); time.sleep(1); gui.key('enter'); time.sleep(1)

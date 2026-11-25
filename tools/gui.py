"""Minimal Win32 GUI automation helpers used to drive CLIP STUDIO PAINT
for generating reverse-engineering samples.

Supports: window lookup/focus, screenshots, mouse, keyboard, and synthetic
*pen* input with pressure via InjectSyntheticPointerInput (Windows 10 1809+).
"""
import ctypes
import ctypes.wintypes as wt
import sys
import time

user32 = ctypes.WinDLL("user32", use_last_error=True)
try:
    ctypes.windll.shcore.SetProcessDpiAwareness(2)
except Exception:
    user32.SetProcessDPIAware()

# ---------------------------------------------------------------- windows
EnumWindowsProc = ctypes.WINFUNCTYPE(wt.BOOL, wt.HWND, wt.LPARAM)


def list_windows(substr=""):
    out = []

    def cb(hwnd, _):
        if user32.IsWindowVisible(hwnd):
            n = user32.GetWindowTextLengthW(hwnd)
            buf = ctypes.create_unicode_buffer(n + 1)
            user32.GetWindowTextW(hwnd, buf, n + 1)
            if substr.lower() in buf.value.lower() and buf.value:
                r = wt.RECT()
                user32.GetWindowRect(hwnd, ctypes.byref(r))
                out.append((hwnd, buf.value, (r.left, r.top, r.right, r.bottom)))
        return True

    user32.EnumWindows(EnumWindowsProc(cb), 0)
    return out


def focus(hwnd):
    if user32.IsIconic(hwnd):
        user32.ShowWindow(hwnd, 9)  # SW_RESTORE only when minimised
    # Alt trick to allow SetForegroundWindow
    user32.keybd_event(0x12, 0, 0, 0)
    user32.SetForegroundWindow(hwnd)
    user32.keybd_event(0x12, 0, 2, 0)
    time.sleep(0.3)


def maximize(hwnd):
    user32.ShowWindow(hwnd, 3)


# ---------------------------------------------------------------- screen
def screenshot(path, region=None, scale=None):
    from PIL import ImageGrab
    img = ImageGrab.grab(bbox=region, all_screens=True)
    if scale:
        img = img.resize((int(img.width * scale), int(img.height * scale)))
    img.save(path)
    return img.size


# ---------------------------------------------------------------- mouse / keys
MOUSEEVENTF_LEFTDOWN, MOUSEEVENTF_LEFTUP = 0x2, 0x4
MOUSEEVENTF_RIGHTDOWN, MOUSEEVENTF_RIGHTUP = 0x8, 0x10


def move(x, y):
    user32.SetCursorPos(int(x), int(y))


def click(x, y, button="left", double=False):
    move(x, y)
    time.sleep(0.05)
    d, u = (MOUSEEVENTF_LEFTDOWN, MOUSEEVENTF_LEFTUP) if button == "left" else (MOUSEEVENTF_RIGHTDOWN, MOUSEEVENTF_RIGHTUP)
    for _ in range(2 if double else 1):
        user32.mouse_event(d, 0, 0, 0, 0)
        time.sleep(0.03)
        user32.mouse_event(u, 0, 0, 0, 0)
        time.sleep(0.05)


def drag(points, delay=0.01):
    move(*points[0])
    time.sleep(0.05)
    user32.mouse_event(MOUSEEVENTF_LEFTDOWN, 0, 0, 0, 0)
    for p in points[1:]:
        time.sleep(delay)
        move(*p)
    time.sleep(0.05)
    user32.mouse_event(MOUSEEVENTF_LEFTUP, 0, 0, 0, 0)


VK = {
    "ctrl": 0x11, "shift": 0x10, "alt": 0x12, "enter": 0x0D, "esc": 0x1B, "tab": 0x09,
    "del": 0x2E, "back": 0x08, "space": 0x20, "left": 0x25, "up": 0x26, "right": 0x27, "down": 0x28,
    "f2": 0x71, "home": 0x24, "end": 0x23,
}


def _vk(k):
    if k in VK:
        return VK[k]
    if len(k) == 1:
        return user32.VkKeyScanW(ord(k)) & 0xFF
    raise ValueError(k)


def key(combo):
    """e.g. key('ctrl+s'), key('enter')"""
    parts = combo.lower().split("+")
    codes = [_vk(p) for p in parts]
    for c in codes:
        user32.keybd_event(c, 0, 0, 0)
        time.sleep(0.02)
    for c in reversed(codes):
        user32.keybd_event(c, 0, 2, 0)
        time.sleep(0.02)
    time.sleep(0.1)


# SendInput unicode typing
class KEYBDINPUT(ctypes.Structure):
    _fields_ = [("wVk", wt.WORD), ("wScan", wt.WORD), ("dwFlags", wt.DWORD), ("time", wt.DWORD), ("dwExtraInfo", ctypes.c_size_t)]


class _INPUTunion(ctypes.Union):
    _fields_ = [("ki", KEYBDINPUT), ("pad", ctypes.c_byte * 32)]


class INPUT(ctypes.Structure):
    _fields_ = [("type", wt.DWORD), ("u", _INPUTunion)]


def type_text(s):
    for ch in s:
        for flags in (0x4, 0x4 | 0x2):  # KEYEVENTF_UNICODE, | KEYUP
            inp = INPUT(type=1)
            inp.u.ki = KEYBDINPUT(0, ord(ch), flags, 0, 0)
            user32.SendInput(1, ctypes.byref(inp), ctypes.sizeof(INPUT))
        time.sleep(0.01)


# ---------------------------------------------------------------- pen injection
PT_PEN = 3
POINTER_FLAG_NEW, POINTER_FLAG_INRANGE, POINTER_FLAG_INCONTACT = 0x1, 0x2, 0x4
POINTER_FLAG_DOWN, POINTER_FLAG_UPDATE, POINTER_FLAG_UP = 0x10000, 0x20000, 0x40000
PEN_MASK_PRESSURE, PEN_MASK_ROTATION, PEN_MASK_TILT_X, PEN_MASK_TILT_Y = 1, 2, 4, 8


class POINTER_INFO(ctypes.Structure):
    _fields_ = [
        ("pointerType", wt.DWORD), ("pointerId", wt.UINT), ("frameId", wt.UINT), ("pointerFlags", wt.DWORD),
        ("sourceDevice", wt.HANDLE), ("hwndTarget", wt.HWND), ("ptPixelLocation", wt.POINT),
        ("ptHimetricLocation", wt.POINT), ("ptPixelLocationRaw", wt.POINT), ("ptHimetricLocationRaw", wt.POINT),
        ("dwTime", wt.DWORD), ("historyCount", wt.UINT), ("InputData", ctypes.c_int32),
        ("dwKeyStates", wt.DWORD), ("PerformanceCount", ctypes.c_uint64), ("ButtonChangeType", ctypes.c_int),
    ]


class POINTER_PEN_INFO(ctypes.Structure):
    _fields_ = [("pointerInfo", POINTER_INFO), ("penFlags", wt.DWORD), ("penMask", wt.DWORD),
                ("pressure", wt.UINT), ("rotation", wt.UINT), ("tiltX", ctypes.c_int32), ("tiltY", ctypes.c_int32)]


class POINTER_TYPE_INFO(ctypes.Structure):
    class _U(ctypes.Union):
        _fields_ = [("penInfo", POINTER_PEN_INFO), ("pad", ctypes.c_byte * 200)]
    _fields_ = [("type", wt.DWORD), ("u", _U)]


_pen_device = None


def _pen():
    global _pen_device
    if _pen_device is None:
        user32.CreateSyntheticPointerDevice.restype = wt.HANDLE
        _pen_device = user32.CreateSyntheticPointerDevice(PT_PEN, 1, 1)  # POINTER_FEEDBACK_DEFAULT
        if not _pen_device:
            raise OSError(ctypes.get_last_error())
    return _pen_device


def _inject(x, y, flags, pressure, tilt=(0, 0), rotation=None):
    info = POINTER_TYPE_INFO(type=PT_PEN)
    pi = info.u.penInfo
    pi.pointerInfo.pointerType = PT_PEN
    pi.pointerInfo.pointerId = 0
    pi.pointerInfo.pointerFlags = flags
    pi.pointerInfo.ptPixelLocation = wt.POINT(int(x), int(y))
    pi.penMask = PEN_MASK_PRESSURE | PEN_MASK_TILT_X | PEN_MASK_TILT_Y | (PEN_MASK_ROTATION if rotation is not None else 0)
    pi.tiltX, pi.tiltY = int(tilt[0]), int(tilt[1])
    if rotation is not None:
        pi.rotation = int(rotation)
    pi.pressure = int(max(0, min(1024, pressure * 1024)))
    ok = user32.InjectSyntheticPointerInput(_pen(), ctypes.byref(info), 1)
    if not ok:
        raise OSError("inject failed %d" % ctypes.get_last_error())


def pen_stroke(points, delay=0.008, tilt=(0, 0), rotation=None):
    """points: list of (x, y, pressure 0..1) in screen pixels."""
    x, y, p = points[0]
    _inject(x, y, POINTER_FLAG_INRANGE | POINTER_FLAG_UPDATE, 0, tilt, rotation)
    time.sleep(0.05)
    _inject(x, y, POINTER_FLAG_INRANGE | POINTER_FLAG_INCONTACT | POINTER_FLAG_DOWN, p, tilt, rotation)
    for x, y, p in points[1:]:
        time.sleep(delay)
        _inject(x, y, POINTER_FLAG_INRANGE | POINTER_FLAG_INCONTACT | POINTER_FLAG_UPDATE, p, tilt, rotation)
    time.sleep(delay)
    _inject(x, y, POINTER_FLAG_INRANGE | POINTER_FLAG_UP, 0)
    time.sleep(0.03)
    _inject(x, y, POINTER_FLAG_UPDATE, 0)
    time.sleep(0.05)


if __name__ == "__main__":
    cmd = sys.argv[1]
    if cmd == "windows":
        for w in list_windows(sys.argv[2] if len(sys.argv) > 2 else ""):
            print(w)
    elif cmd == "shot":
        print(screenshot(sys.argv[2], scale=float(sys.argv[3]) if len(sys.argv) > 3 else None))

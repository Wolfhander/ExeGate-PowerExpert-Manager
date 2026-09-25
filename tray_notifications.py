"""Native Windows balloons with an explicit NIIF_NOSOUND flag."""
import ctypes
from ctypes import wintypes
import sys

NOTIFICATION_ID = 0xE17E

class GUID(ctypes.Structure):
    _fields_ = [("Data1", wintypes.DWORD), ("Data2", wintypes.WORD),
                ("Data3", wintypes.WORD), ("Data4", ctypes.c_ubyte * 8)]

class NOTIFYICONDATA(ctypes.Structure):
    _fields_ = [("cbSize", wintypes.DWORD), ("hWnd", wintypes.HWND),
                ("uID", wintypes.UINT), ("uFlags", wintypes.UINT),
                ("uCallbackMessage", wintypes.UINT), ("hIcon", wintypes.HICON),
                ("szTip", wintypes.WCHAR * 128), ("dwState", wintypes.DWORD),
                ("dwStateMask", wintypes.DWORD), ("szInfo", wintypes.WCHAR * 256),
                ("uTimeoutOrVersion", wintypes.UINT), ("szInfoTitle", wintypes.WCHAR * 64),
                ("dwInfoFlags", wintypes.DWORD), ("guidItem", GUID), ("hBalloonIcon", wintypes.HICON)]


def _data(hwnd):
    data = NOTIFYICONDATA()
    data.cbSize = ctypes.sizeof(data)
    data.hWnd, data.uID = hwnd, NOTIFICATION_ID
    return data


def _notify(action, data):
    api = ctypes.windll.shell32.Shell_NotifyIconW
    api.argtypes = [wintypes.DWORD, ctypes.POINTER(NOTIFYICONDATA)]
    api.restype = wintypes.BOOL
    return bool(api(action, ctypes.byref(data)))


def _truncate(text, units):
    return text.encode("utf-16-le")[:units * 2].decode("utf-16-le", errors="ignore")


def show_notification(hwnd, title, message, critical=False, sound=True):
    if sys.platform != "win32":
        return False
    data = _data(hwnd)
    data.uFlags = 0x02 | 0x04 | 0x10  # NIF_ICON | NIF_TIP | NIF_INFO
    load_icon = ctypes.windll.user32.LoadIconW
    load_icon.argtypes = [wintypes.HINSTANCE, ctypes.c_void_p]
    load_icon.restype = wintypes.HICON
    data.hIcon = load_icon(None, 32513 if critical else 32515)
    data.szTip = _truncate(title, 127)
    data.szInfoTitle = _truncate(title, 63)
    data.szInfo = _truncate(message, 255)
    data.dwInfoFlags = (3 if critical else 2) | (0 if sound else 0x10)
    data.uTimeoutOrVersion = 8000
    return _notify(1, data) or _notify(0, data)  # modify or add our own icon


def remove_notification(hwnd):
    if sys.platform == "win32":
        _notify(2, _data(hwnd))

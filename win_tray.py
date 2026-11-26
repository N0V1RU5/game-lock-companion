"""Ikona u hodin pro Windows přímo přes Win32 API (ctypes), bez pystray a Pillow.

Šetří ~5 MB RAM a ~5 MB v buildu. Ikona se kreslí přes GDI: zaoblený čtverec s číslem.
"""
from __future__ import annotations

import ctypes
import threading
from ctypes import wintypes

user32 = ctypes.WinDLL("user32", use_last_error=True)
gdi32 = ctypes.WinDLL("gdi32")
shell32 = ctypes.WinDLL("shell32")
kernel32 = ctypes.WinDLL("kernel32")

LRESULT = ctypes.c_ssize_t
WNDPROC = ctypes.WINFUNCTYPE(LRESULT, wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM)

WM_DESTROY, WM_CLOSE, WM_NULL, WM_COMMAND = 0x0002, 0x0010, 0x0000, 0x0111
WM_LBUTTONUP, WM_RBUTTONUP = 0x0202, 0x0205
WM_TRAY = 0x8000 + 1  # WM_APP + 1
WM_HOTKEY_ON, WM_HOTKEY_OFF, WM_HOTKEY = 0x8000 + 2, 0x8000 + 3, 0x0312
MOD_CONTROL, MOD_SHIFT, MOD_NOREPEAT = 0x2, 0x4, 0x4000
HOTKEY_ID = 1
NIM_ADD, NIM_MODIFY, NIM_DELETE = 0, 1, 2
NIF_MESSAGE, NIF_ICON, NIF_TIP = 1, 2, 4
MF_STRING, MF_SEPARATOR, MF_CHECKED = 0x0, 0x800, 0x8
TPM_RIGHTBUTTON, TPM_RETURNCMD = 0x2, 0x100
SIZE = 64


class NOTIFYICONDATAW(ctypes.Structure):
    _fields_ = [("cbSize", wintypes.DWORD), ("hWnd", wintypes.HWND), ("uID", wintypes.UINT),
                ("uFlags", wintypes.UINT), ("uCallbackMessage", wintypes.UINT), ("hIcon", wintypes.HICON),
                ("szTip", wintypes.WCHAR * 128), ("dwState", wintypes.DWORD), ("dwStateMask", wintypes.DWORD),
                ("szInfo", wintypes.WCHAR * 256), ("uVersion", wintypes.UINT),
                ("szInfoTitle", wintypes.WCHAR * 64), ("dwInfoFlags", wintypes.DWORD),
                ("guidItem", ctypes.c_byte * 16), ("hBalloonIcon", wintypes.HICON)]


class WNDCLASSW(ctypes.Structure):
    _fields_ = [("style", wintypes.UINT), ("lpfnWndProc", WNDPROC), ("cbClsExtra", ctypes.c_int),
                ("cbWndExtra", ctypes.c_int), ("hInstance", wintypes.HINSTANCE), ("hIcon", wintypes.HICON),
                ("hCursor", wintypes.HANDLE), ("hbrBackground", wintypes.HBRUSH),
                ("lpszMenuName", wintypes.LPCWSTR), ("lpszClassName", wintypes.LPCWSTR)]


class BITMAPINFOHEADER(ctypes.Structure):
    _fields_ = [("biSize", wintypes.DWORD), ("biWidth", wintypes.LONG), ("biHeight", wintypes.LONG),
                ("biPlanes", wintypes.WORD), ("biBitCount", wintypes.WORD), ("biCompression", wintypes.DWORD),
                ("biSizeImage", wintypes.DWORD), ("biXPelsPerMeter", wintypes.LONG),
                ("biYPelsPerMeter", wintypes.LONG), ("biClrUsed", wintypes.DWORD), ("biClrImportant", wintypes.DWORD)]


class ICONINFO(ctypes.Structure):
    _fields_ = [("fIcon", wintypes.BOOL), ("xHotspot", wintypes.DWORD), ("yHotspot", wintypes.DWORD),
                ("hbmMask", wintypes.HBITMAP), ("hbmColor", wintypes.HBITMAP)]


def _sig(fn, res, *args):
    fn.restype, fn.argtypes = res, list(args)


H, P = wintypes.HANDLE, ctypes.c_void_p
_sig(user32.DefWindowProcW, LRESULT, wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM)
_sig(user32.RegisterClassW, wintypes.ATOM, P)
_sig(user32.CreateWindowExW, wintypes.HWND, wintypes.DWORD, wintypes.LPCWSTR, wintypes.LPCWSTR, wintypes.DWORD,
     ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int, wintypes.HWND, wintypes.HMENU, wintypes.HINSTANCE, P)
_sig(user32.GetMessageW, wintypes.BOOL, P, wintypes.HWND, wintypes.UINT, wintypes.UINT)
_sig(user32.TranslateMessage, wintypes.BOOL, P)
_sig(user32.DispatchMessageW, LRESULT, P)
_sig(user32.PostMessageW, wintypes.BOOL, wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM)
_sig(user32.PostQuitMessage, None, ctypes.c_int)
_sig(user32.DestroyWindow, wintypes.BOOL, wintypes.HWND)
_sig(user32.RegisterWindowMessageW, wintypes.UINT, wintypes.LPCWSTR)
_sig(user32.CreatePopupMenu, wintypes.HMENU)
_sig(user32.AppendMenuW, wintypes.BOOL, wintypes.HMENU, wintypes.UINT, ctypes.c_size_t, wintypes.LPCWSTR)
_sig(user32.SetMenuDefaultItem, wintypes.BOOL, wintypes.HMENU, wintypes.UINT, wintypes.UINT)
_sig(user32.TrackPopupMenu, wintypes.BOOL, wintypes.HMENU, wintypes.UINT, ctypes.c_int, ctypes.c_int,
     ctypes.c_int, wintypes.HWND, P)
_sig(user32.DestroyMenu, wintypes.BOOL, wintypes.HMENU)
_sig(user32.GetCursorPos, wintypes.BOOL, P)
_sig(user32.SetForegroundWindow, wintypes.BOOL, wintypes.HWND)
_sig(user32.GetDC, wintypes.HDC, wintypes.HWND)
_sig(user32.ReleaseDC, ctypes.c_int, wintypes.HWND, wintypes.HDC)
_sig(user32.CreateIconIndirect, wintypes.HICON, P)
_sig(user32.DestroyIcon, wintypes.BOOL, wintypes.HICON)
_sig(user32.DrawTextW, ctypes.c_int, wintypes.HDC, wintypes.LPCWSTR, ctypes.c_int, P, wintypes.UINT)
_sig(gdi32.CreateCompatibleDC, wintypes.HDC, wintypes.HDC)
_sig(gdi32.DeleteDC, wintypes.BOOL, wintypes.HDC)
_sig(gdi32.CreateDIBSection, wintypes.HBITMAP, wintypes.HDC, P, wintypes.UINT, P, H, wintypes.DWORD)
_sig(gdi32.CreateBitmap, wintypes.HBITMAP, ctypes.c_int, ctypes.c_int, wintypes.UINT, wintypes.UINT, P)
_sig(gdi32.SelectObject, H, wintypes.HDC, H)
_sig(gdi32.DeleteObject, wintypes.BOOL, H)
_sig(gdi32.CreateSolidBrush, wintypes.HBRUSH, wintypes.COLORREF)
_sig(gdi32.GetStockObject, H, ctypes.c_int)
_sig(gdi32.RoundRect, wintypes.BOOL, wintypes.HDC, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int,
     ctypes.c_int, ctypes.c_int)
_sig(gdi32.CreateFontW, H, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int, wintypes.DWORD,
     wintypes.DWORD, wintypes.DWORD, wintypes.DWORD, wintypes.DWORD, wintypes.DWORD, wintypes.DWORD,
     wintypes.DWORD, wintypes.LPCWSTR)
_sig(gdi32.SetBkMode, ctypes.c_int, wintypes.HDC, ctypes.c_int)
_sig(gdi32.SetTextColor, wintypes.COLORREF, wintypes.HDC, wintypes.COLORREF)
_sig(shell32.Shell_NotifyIconW, wintypes.BOOL, wintypes.DWORD, P)
_sig(kernel32.GetModuleHandleW, wintypes.HMODULE, wintypes.LPCWSTR)
_sig(user32.RegisterHotKey, wintypes.BOOL, wintypes.HWND, ctypes.c_int, wintypes.UINT, wintypes.UINT)
_sig(user32.UnregisterHotKey, wintypes.BOOL, wintypes.HWND, ctypes.c_int)


def make_icon(text: str, color: str, out: list | None = None) -> int:
    """HICON 64×64: zaoblený čtverec v barvě color, uprostřed bílý tučný text."""
    rgb = int(color.lstrip("#"), 16)
    colorref = ((rgb & 0xFF) << 16) | (rgb & 0xFF00) | (rgb >> 16)
    key = 0x00FF00FF  # purpurová = průhledné (rohy)
    screen = user32.GetDC(None)
    dc = gdi32.CreateCompatibleDC(screen)
    bmi = BITMAPINFOHEADER(ctypes.sizeof(BITMAPINFOHEADER), SIZE, -SIZE, 1, 32, 0, 0, 0, 0, 0, 0)
    bits = ctypes.c_void_p()
    color_bmp = gdi32.CreateDIBSection(dc, ctypes.byref(bmi), 0, ctypes.byref(bits), None, 0)
    px = (ctypes.c_uint32 * (SIZE * SIZE)).from_address(bits.value)
    for i in range(SIZE * SIZE):
        px[i] = key
    old_bmp = gdi32.SelectObject(dc, color_bmp)
    brush = gdi32.CreateSolidBrush(colorref)
    old_brush = gdi32.SelectObject(dc, brush)
    old_pen = gdi32.SelectObject(dc, gdi32.GetStockObject(8))  # NULL_PEN
    gdi32.RoundRect(dc, 0, 0, SIZE + 1, SIZE + 1, 28, 28)
    fsize = {1: 46, 2: 40, 3: 30}.get(len(text), 24)
    font = gdi32.CreateFontW(-fsize, 0, 0, 0, 700, 0, 0, 0, 1, 0, 0, 4, 0, "Segoe UI")  # ANTIALIASED_QUALITY
    old_font = gdi32.SelectObject(dc, font)
    gdi32.SetBkMode(dc, 1)  # TRANSPARENT
    gdi32.SetTextColor(dc, 0x00FFFFFF)
    rect = wintypes.RECT(0, 0, SIZE, SIZE)
    user32.DrawTextW(dc, text, -1, ctypes.byref(rect), 0x1 | 0x4 | 0x20)  # CENTER | VCENTER | SINGLELINE
    for obj in (old_font, old_pen, old_brush, old_bmp):
        gdi32.SelectObject(dc, obj)
    for i in range(SIZE * SIZE):  # GDI nezapisuje alfa kanál: nakreslené = neprůhledné, klíč = průhledné
        v = px[i] & 0x00FFFFFF
        px[i] = 0 if v == key else v | 0xFF000000
    if out is not None:
        out.extend(px)
    mask = gdi32.CreateBitmap(SIZE, SIZE, 1, 1, None)
    info = ICONINFO(True, 0, 0, mask, color_bmp)
    hicon = user32.CreateIconIndirect(ctypes.byref(info))
    for obj in (font, brush, mask, color_bmp):
        gdi32.DeleteObject(obj)
    gdi32.DeleteDC(dc)
    user32.ReleaseDC(None, screen)
    return hicon


class WinTray:
    """Ikona u hodin. items = [(text, akce) | None (oddělovač) | (text, akce, zaškrtnuto_fn)]; první = výchozí."""

    def __init__(self, title: str, items: list, text: str, color: str, on_hotkey=None):
        self.items = items
        self.on_hotkey = on_hotkey
        self.visible = False
        self.hwnd = None
        self.hicon = make_icon(text, color)
        self.tip = title
        self._wndproc = WNDPROC(self._proc)  # reference musí žít, jinak spadne
        self._ready = threading.Event()
        threading.Thread(target=self._run, daemon=True, name="tray").start()
        self._ready.wait(5)

    def _nid(self, flags: int) -> NOTIFYICONDATAW:
        nid = NOTIFYICONDATAW()
        nid.cbSize = ctypes.sizeof(NOTIFYICONDATAW)
        nid.hWnd, nid.uID, nid.uFlags = self.hwnd, 1, flags
        nid.uCallbackMessage, nid.hIcon, nid.szTip = WM_TRAY, self.hicon, self.tip[:127]
        return nid

    def _add(self):
        self.visible = bool(shell32.Shell_NotifyIconW(NIM_ADD, ctypes.byref(self._nid(NIF_MESSAGE | NIF_ICON | NIF_TIP))))

    def _run(self):
        hinst = kernel32.GetModuleHandleW(None)
        wc = WNDCLASSW()
        wc.lpfnWndProc, wc.hInstance, wc.lpszClassName = self._wndproc, hinst, "GameLockTray"
        user32.RegisterClassW(ctypes.byref(wc))
        self.hwnd = user32.CreateWindowExW(0, "GameLockTray", "Game Lock", 0, 0, 0, 0, 0, None, None, hinst, None)
        self._taskbar_created = user32.RegisterWindowMessageW("TaskbarCreated")
        self._add()
        self._ready.set()
        msg = wintypes.MSG()
        while user32.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
            user32.TranslateMessage(ctypes.byref(msg))
            user32.DispatchMessageW(ctypes.byref(msg))

    def _proc(self, hwnd, msg, wparam, lparam):
        try:
            if msg == WM_TRAY:
                if lparam == WM_LBUTTONUP:
                    self.items[0][1]()
                elif lparam == WM_RBUTTONUP:
                    self._menu()
                return 0
            if msg == WM_HOTKEY_ON:  # zkratka se registruje ve vlákně, které vlastní okno
                user32.RegisterHotKey(hwnd, HOTKEY_ID, MOD_CONTROL | MOD_SHIFT | MOD_NOREPEAT, wparam)
                return 0
            if msg == WM_HOTKEY_OFF:
                user32.UnregisterHotKey(hwnd, HOTKEY_ID)
                return 0
            if msg == WM_HOTKEY:
                if self.on_hotkey:
                    self.on_hotkey()
                return 0
            if msg == self._taskbar_created:  # Průzkumník se restartoval -> ikonu přidat znovu
                self._add()
                return 0
            if msg == WM_CLOSE:
                user32.DestroyWindow(hwnd)
                return 0
            if msg == WM_DESTROY:
                shell32.Shell_NotifyIconW(NIM_DELETE, ctypes.byref(self._nid(0)))
                self.visible = False
                user32.PostQuitMessage(0)
                return 0
        except Exception:
            pass
        return user32.DefWindowProcW(hwnd, msg, wparam, lparam)

    def _menu(self):
        menu = user32.CreatePopupMenu()
        for i, item in enumerate(self.items, 1):
            if item is None:
                user32.AppendMenuW(menu, MF_SEPARATOR, 0, None)
            else:
                checked = len(item) > 2 and item[2]()
                user32.AppendMenuW(menu, MF_STRING | (MF_CHECKED if checked else 0), i, item[0])
        user32.SetMenuDefaultItem(menu, 1, 0)
        pt = wintypes.POINT()
        user32.GetCursorPos(ctypes.byref(pt))
        user32.SetForegroundWindow(self.hwnd)
        cmd = user32.TrackPopupMenu(menu, TPM_RIGHTBUTTON | TPM_RETURNCMD, pt.x, pt.y, 0, self.hwnd, None)
        user32.PostMessageW(self.hwnd, WM_NULL, 0, 0)
        user32.DestroyMenu(menu)
        if cmd and self.items[cmd - 1] is not None:
            self.items[cmd - 1][1]()

    def set(self, text: str, color: str, tooltip: str):
        old = self.hicon
        self.hicon, self.tip = make_icon(text, color), tooltip
        if self.hwnd:
            shell32.Shell_NotifyIconW(NIM_MODIFY, ctypes.byref(self._nid(NIF_ICON | NIF_TIP)))
        user32.DestroyIcon(old)

    def hotkey(self, on: bool, key: str = "D"):
        """Globální zkratka Ctrl+Shift+<key> jen na dobu, kdy je potřeba (jinak by blokovala jiné appky)."""
        if self.hwnd:
            user32.PostMessageW(self.hwnd, WM_HOTKEY_ON if on else WM_HOTKEY_OFF, ord(key.upper()), 0)

    def stop(self):
        if self.hwnd:
            user32.PostMessageW(self.hwnd, WM_CLOSE, 0, 0)

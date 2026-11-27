"""Herní zámek / Game Lock – nejdřív učení, pak hry. Hlídá Miku.

Spuštění:  pythonw herni_zamek.pyw
Test:      python herni_zamek.pyw --test --fake-time 22:40   (zrychlený čas, testovací data)
"""
from __future__ import annotations

import argparse
import atexit
import os
import queue
import random
import socket
import subprocess
import sys
import time
import traceback
import threading
import tkinter as tk
from datetime import datetime
from tkinter import filedialog, messagebox

from i18n import LANGS, T, get_lang, set_lang
from zamek_core import (
    HERE, IS_MAC, IS_WIN, MODE_OTHER, MODE_PLAY, MODE_PROJECT, MODE_STUDY, VERSION, WORK_MODES,
    Clock, GameDetector, Gate, fmt_min, kill_processes, load_config, mode_label, psutil, save_config, load_json,
)

# Ikona u hodin (pystray + Pillow) se načítá až při startu ikony, ať appka bez ní šetří RAM.
# Na macOS se pystray nesnese s Tk (obojí chce hlavní vlákno), tam appka běží v Docku.
TRAY_OK = not IS_MAC

# ------------------------------------------------------------------ vzhled (Miku)
THEMES = {
    "dark": {
        "BG": "#0F1417", "CARD": "#182026", "CARD2": "#212C33", "HOVER2": "#2B3842",
        "FG": "#EAF6F5", "MUTED": "#8DA3A8", "TEAL": "#39C5BB", "TEAL_D": "#2A9D95",
        "PINK": "#E12885", "PINK_D": "#B81F6C", "GREEN": "#4CAF7A", "RED": "#E0605A",
        "YELLOW": "#F2C14E", "PURPLE": "#7A5AE0", "ACCENT": "#39C5BB", "DISABLED": "#5D6B70",
    },
    "light": {
        "BG": "#F3F6F6", "CARD": "#FFFFFF", "CARD2": "#E2EAEA", "HOVER2": "#D2DEDE",
        "FG": "#15211F", "MUTED": "#5D7175", "TEAL": "#39C5BB", "TEAL_D": "#1F8F87",
        "PINK": "#E12885", "PINK_D": "#C21F73", "GREEN": "#2E8B57", "RED": "#C9443D",
        "YELLOW": "#A87A06", "PURPLE": "#6A4BD0", "ACCENT": "#1F8F87", "DISABLED": "#9AA9AB",
    },
}
BG = CARD = CARD2 = HOVER2 = FG = MUTED = TEAL = TEAL_D = PINK = PINK_D = ""
GREEN = RED = YELLOW = PURPLE = ACCENT = DISABLED = ""
WARN_BG = "#F2C14E"
THEME = "dark"


def system_theme() -> str:
    """Světlý/tmavý vzhled systému (Windows registr, macOS defaults, Linux GNOME gsettings)."""
    try:
        if IS_WIN:
            import winreg
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER,
                                r"Software\Microsoft\Windows\CurrentVersion\Themes\Personalize") as k:
                return "light" if winreg.QueryValueEx(k, "AppsUseLightTheme")[0] else "dark"
        if IS_MAC:
            r = subprocess.run(["defaults", "read", "-g", "AppleInterfaceStyle"], capture_output=True, text=True)
            return "dark" if "dark" in r.stdout.lower() else "light"
        r = subprocess.run(["gsettings", "get", "org.gnome.desktop.interface", "color-scheme"],
                           capture_output=True, text=True, timeout=2)
        if r.returncode == 0:
            return "dark" if "dark" in r.stdout else "light"
    except Exception:
        pass
    return "dark"


def apply_theme(name: str | None):
    """Nastaví barvy (globální konstanty) před stavbou oken. "system" = podle Windows."""
    global THEME
    THEME = name if name in THEMES else system_theme()
    globals().update(THEMES[THEME])


apply_theme("dark")

if IS_WIN:
    _F, _FB, _W = "Segoe UI", "Segoe UI Semibold", "normal"
else:  # macOS / Linux: systémová písma, tučnost přes "bold"
    _F = _FB = "Helvetica Neue" if IS_MAC else "DejaVu Sans"
    _W = "bold"
FONT = (_F, 11)
FONT_S = (_F, 9)
FONT_B = (_FB, 11, _W)
FONT_BIG = (_FB, 20, _W)
FONT_MID = (_FB, 13, _W)
FONT_TIMER = (_FB, 34, _W)
FONT_TEXT = (_F, 13)

FALLBACK_PERSONA = {"name": "Miku", "emoji": "🎵", "color": "#39C5BB", "lines": {}}

IMG_DIR = HERE / "obrazky"
MOODS = ("happy", "study", "angry", "sleepy", "alert")
# která nálada patří ke které hlášce
MOOD_FOR = {
    "greet": "happy", "play_start": "happy", "time_up": "happy", "override_ready": "happy",
    "study_start": "study", "presence": "study", "project_start": "study", "kill_project": "angry", "switched": "alert",
    "deny_balance": "angry", "kill_balance": "angry", "kill_study": "angry",
    "deny_curfew": "sleepy", "kill_curfew": "sleepy", "curfew_warn": "sleepy",
    "warn": "alert", "deadline": "alert", "finish_done": "happy",
}


def log_error(text: str):
    try:
        with open(HERE / "errors.log", "a", encoding="utf-8") as f:
            f.write(f"\n[{datetime.now():%Y-%m-%d %H:%M:%S}]\n{text}\n")
    except OSError:
        pass


# ------------------------------------------------------------------ hlášky
class Persona:
    def __init__(self, data: dict):
        self.p = next(iter(data.values())) if data else FALLBACK_PERSONA
        self.name = self.p.get("name", "Miku")
        self.emoji = self.p.get("emoji", "🎵")
        self.color = self.p.get("color", "#39C5BB")

    def lines(self) -> dict:
        lines = self.p.get("lines", {})
        if any(k in lines for k in LANGS):  # {"cs": {...}, "en": {...}}
            return lines.get(get_lang()) or lines.get("cs") or {}
        return lines  # starý formát: jen jeden jazyk

    def say(self, key: str, fallback: str, **fmt) -> str:
        lines = self.lines().get(key) or [fallback]
        try:
            return random.choice(lines).format(**fmt)
        except (KeyError, IndexError, ValueError):
            return fallback


# ------------------------------------------------------------------ obrázky
class Sprites:
    """Načte obrazky/<nálada>.gif|.png (snímky animace). Chybějící obrázek = bez obrázku."""

    MAX_SIDE = 110

    def __init__(self):
        self.cache: dict[str, tuple[list, int]] = {}

    def get(self, mood: str) -> tuple[list, int]:
        if mood in self.cache:
            return self.cache[mood]
        frames, delay = [], 500
        for ext in (".gif", ".png"):
            path = IMG_DIR / f"{mood}{ext}"
            if not path.exists():
                continue
            try:
                if ext == ".gif":
                    i = 0
                    while i < 200:
                        try:
                            frames.append(tk.PhotoImage(file=str(path), format=f"gif -index {i}"))
                        except tk.TclError:
                            break
                        i += 1
                    delay = gif_delay(path)
                else:
                    frames.append(tk.PhotoImage(file=str(path)))
                frames = [self._fit(f) for f in frames]
            except Exception:
                log_error(traceback.format_exc())
                frames = []
            break
        self.cache[mood] = (frames, delay)
        return frames, delay

    def _fit(self, img: tk.PhotoImage) -> tk.PhotoImage:
        """Velké vlastní obrázky zmenší (celočíselně), ať se vejdou."""
        side = max(img.width(), img.height())
        if side > self.MAX_SIDE:
            return img.subsample((side + self.MAX_SIDE - 1) // self.MAX_SIDE)
        return img


def gif_delay(path) -> int:
    """Délka prvního snímku GIFu v ms (Graphic Control Extension), bez načítání Pillow."""
    try:
        data = path.read_bytes()
        i = data.find(b"\x21\xf9\x04")
        if i >= 0:
            return max(60, int.from_bytes(data[i + 4:i + 6], "little") * 10 or 500)
    except OSError:
        pass
    return 500


class SpriteLabel(tk.Label):
    """Label s animovaným obrázkem. Když okno není vidět, neanimuje (šetří CPU)."""

    def __init__(self, parent, sprites: Sprites, **kw):
        super().__init__(parent, bd=0, **kw)
        self.sprites = sprites
        self.frames: list = []
        self.delay = 500
        self.i = 0
        self._job = None
        self.mood = None

    def set_mood(self, mood: str):
        if mood == self.mood:
            return
        self.mood = mood
        self.frames, self.delay = self.sprites.get(mood)
        self.i = 0
        if self.frames:
            self.config(image=self.frames[0])
        else:
            self.config(image="")
        if self._job is None and len(self.frames) > 1:
            self._job = self.after(self.delay, self._tick)

    def _tick(self):
        self._job = None
        if not self.winfo_exists() or len(self.frames) < 2:
            return
        if self.winfo_viewable():
            self.i = (self.i + 1) % len(self.frames)
            self.config(image=self.frames[self.i])
            self._job = self.after(self.delay, self._tick)
        else:
            self._job = self.after(1500, self._tick)


# ------------------------------------------------------------------ ikona u hodin
_fonts: dict = {}


def _tray_font(size: int):
    """Tučné písmo pro číslo v ikoně (načte se jednou pro každou velikost)."""
    from PIL import ImageFont
    if size not in _fonts:
        for name in ("segoeuib.ttf", "DejaVuSans-Bold.ttf", "Arial Bold.ttf", "Helvetica.ttc"):
            try:
                _fonts[size] = ImageFont.truetype(name, size)
                break
            except OSError:
                continue
        else:
            try:
                _fonts[size] = ImageFont.load_default(size)
            except TypeError:
                _fonts[size] = ImageFont.load_default()
    return _fonts[size]


def tray_image(text: str, color: str):
    from PIL import Image, ImageDraw
    size = 64
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    d.rounded_rectangle((0, 0, size - 1, size - 1), radius=14, fill=color)
    font = _tray_font({1: 46, 2: 40, 3: 30}.get(len(text), 24))
    box = d.textbbox((0, 0), text, font=font)
    w, h = box[2] - box[0], box[3] - box[1]
    d.text(((size - w) / 2 - box[0], (size - h) / 2 - box[1]), text, font=font, fill="white")
    return img


RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
RUN_NAME = "HerniZamek"


FROZEN = getattr(sys, "frozen", False)
# Windows ze zdrojáků se spouští zástupcem v shell:startup (README), přepínač je jen pro .exe.
AUTOSTART_OK = FROZEN or not IS_WIN


def launch_cmd() -> list[str]:
    return [sys.executable] if FROZEN else [sys.executable, os.path.abspath(__file__)]


def _autostart_file():
    from pathlib import Path
    if IS_MAC:
        return Path.home() / "Library" / "LaunchAgents" / "com.n0v1ru5.gamelockcompanion.plist"
    return Path.home() / ".config" / "autostart" / "game-lock-companion.desktop"


def autostart_enabled() -> bool:
    if not IS_WIN:
        return _autostart_file().exists()
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY) as k:
            winreg.QueryValueEx(k, RUN_NAME)
            return True
    except OSError:
        return False


def set_autostart(on: bool):
    """Spouštění po přihlášení: Windows HKCU\\...\\Run, macOS LaunchAgent, Linux ~/.config/autostart."""
    if IS_WIN:
        import winreg
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_SET_VALUE) as k:
            if on:
                winreg.SetValueEx(k, RUN_NAME, 0, winreg.REG_SZ, " ".join(f'"{c}"' for c in launch_cmd()))
            else:
                try:
                    winreg.DeleteValue(k, RUN_NAME)
                except OSError:
                    pass
        return
    f = _autostart_file()
    if not on:
        f.unlink(missing_ok=True)
        return
    f.parent.mkdir(parents=True, exist_ok=True)
    cmd = launch_cmd()
    if IS_MAC:
        args = "".join(f"<string>{c}</string>" for c in cmd)
        f.write_text('<?xml version="1.0" encoding="UTF-8"?>\n<plist version="1.0"><dict>'
                     '<key>Label</key><string>com.n0v1ru5.gamelockcompanion</string>'
                     f'<key>ProgramArguments</key><array>{args}</array>'
                     '<key>RunAtLoad</key><true/></dict></plist>\n', encoding="utf-8")
    else:
        f.write_text("[Desktop Entry]\nType=Application\nName=Game Lock Companion\n"
                     f"Exec={' '.join(chr(34) + c + chr(34) for c in cmd)}\nX-GNOME-Autostart-enabled=true\n",
                     encoding="utf-8")


def open_path(path):
    """Otevře soubor ve výchozí aplikaci (Excel, editor…)."""
    if IS_WIN:
        os.startfile(str(path))
    else:
        subprocess.Popen(["open" if IS_MAC else "xdg-open", str(path)])


def trim_memory():
    """Windows: vrátí nepoužívanou paměť systému, když je okno schované (načte se zpět, až je potřeba)."""
    if IS_WIN:
        try:
            import ctypes
            k32 = ctypes.windll.kernel32
            k32.SetProcessWorkingSetSize(k32.GetCurrentProcess(), ctypes.c_size_t(-1), ctypes.c_size_t(-1))
        except Exception:
            pass


class Tray:
    """Ikona vedle hodin. Běží ve vlastním vlákně, příkazy posílá do fronty pro Tk."""

    def __init__(self, cmd_q: queue.Queue):
        self.q = cmd_q
        self._state = None
        self._last_push = 0.0

        def toggle(*_):
            try:
                set_autostart(not autostart_enabled())
            except Exception:
                log_error(traceback.format_exc())

        entries = [(T(k), lambda c=c: self.q.put(c)) for k, c in (
            ("tray_open", "open"), ("tray_study", "study"), ("tray_project", "project"), ("tray_play", "play"),
            ("tray_stats", "stats"), ("tray_settings", "settings"))]
        if AUTOSTART_OK:
            entries += [None, (T("tray_autostart" if IS_WIN else "set_autostart"), toggle, autostart_enabled)]

        if IS_WIN:  # vlastní ikona přes Win32 API: bez pystray a Pillow (méně RAM i místa)
            from win_tray import WinTray
            self.icon = WinTray(T("app_title"), entries, "0", RED, on_hotkey=lambda: self.q.put("finish"))
            return
        import pystray  # Linux
        items = []
        for e in entries:
            if e is None:
                items.append(pystray.Menu.SEPARATOR)
            elif len(e) > 2:
                items.append(pystray.MenuItem(e[0], e[1], checked=lambda item, f=e[2]: f()))
            else:
                items.append(pystray.MenuItem(e[0], lambda icon, item, f=e[1]: f(), default=not items))
        self.icon = pystray.Icon("herni_zamek", tray_image("0", RED), T("app_title"), pystray.Menu(*items))
        self.icon.run_detached()

    def update(self, text: str, color: str, tooltip: str):
        state = (text, color, tooltip)
        # Dokud ikona není opravdu zobrazená, změny se ztrácí – proto se stav pamatuje
        # až po zobrazení a navíc se jednou za 30 s pošle znovu.
        if state == self._state and time.monotonic() - self._last_push < 30:
            return
        try:
            if not self.icon.visible:
                return
            if IS_WIN:
                self.icon.set(text, color, tooltip[:120])
            else:
                self.icon.icon = tray_image(text, color)
                self.icon.title = tooltip[:120]
            self._state = state
            self._last_push = time.monotonic()
        except Exception:
            log_error(traceback.format_exc())

    def hotkey(self, on: bool):
        """Ctrl+Shift+D během odpočtu (jen Windows)."""
        if IS_WIN:
            try:
                self.icon.hotkey(on)
            except Exception:
                log_error(traceback.format_exc())

    def stop(self):
        try:
            self.icon.stop()
        except Exception:
            pass


def single_instance(port: int) -> socket.socket | None:
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        s.bind(("127.0.0.1", port))
        s.listen(1)
        return s
    except OSError:
        return None


EXE_TYPES = [("exe", "*.exe")] if IS_WIN else [("*", "*")]


def flat_button(parent, text, bg, hover, command, font=FONT_B, fg=None, **kw):
    if fg is None:  # barevná tlačítka mají bílý text, šedá text podle vzhledu
        fg = FG if bg in (CARD, CARD2, BG) else "#FFFFFF"
    b = tk.Button(parent, text=text, font=font, bg=bg, fg=fg, activebackground=hover,
                  activeforeground=fg, relief="flat", bd=0, cursor="hand2", command=command,
                  disabledforeground=DISABLED, **kw)
    b._base_bg = bg
    b.bind("<Enter>", lambda e: b.config(bg=hover) if str(b["state"]) != "disabled" else None)
    b.bind("<Leave>", lambda e: b.config(bg=b._base_bg))
    return b


def set_icon(win):
    if THEME == "dark" and IS_WIN:  # tmavý titulek okna (Windows 10 20H1+ / 11)
        try:
            win.update_idletasks()
            hwnd = int(win.wm_frame(), 16)
            on = __import__("ctypes").c_int(1)
            __import__("ctypes").windll.dwmapi.DwmSetWindowAttribute(hwnd, 20, __import__("ctypes").byref(on), 4)
        except Exception:
            pass
    ico = HERE / "icon.ico"
    if ico.exists():
        try:
            win.iconbitmap(default=str(ico))
        except tk.TclError:
            pass


# ------------------------------------------------------------------ appka
class App:
    def __init__(self, root: tk.Tk, gate: Gate, detector: GameDetector, scan_ms: int, persona: Persona):
        self.root = root
        self.gate = gate
        self.detector = detector
        self.scan_ms = scan_ms
        self.persona = persona
        self.restart = False
        self.sprites = Sprites()
        self.notice_win: tk.Toplevel | None = None
        self.presence_win: tk.Toplevel | None = None
        self.override_win: tk.Toplevel | None = None
        self.finish_win: tk.Toplevel | None = None
        self.toast_win: tk.Toplevel | None = None
        self.update_win: tk.Toplevel | None = None
        self.pending_update: dict | None = None
        self._update_asked: set = set()
        self.settings_win: tk.Toplevel | None = None
        self._last_kill_notice = 0.0
        self._said_at = 0.0
        self._saves = 0
        self.cmd_q: queue.Queue = queue.Queue()
        self.tray = None
        if TRAY_OK:
            try:
                self.tray = Tray(self.cmd_q)
                atexit.register(self.tray.stop)
            except ImportError:
                pass  # bez pystray/Pillow appka funguje, jen nemá ikonu u hodin
            except Exception:
                log_error(traceback.format_exc())

        root.title(T("app_title"))
        root.configure(bg=BG)
        h = min(740, root.winfo_screenheight() - 90)
        x = max(0, root.winfo_screenwidth() // 2 - 235)
        root.geometry(f"470x{h}+{x}+10")
        root.minsize(430, 600)
        root.protocol("WM_DELETE_WINDOW", self.hide)
        root.report_callback_exception = self._tk_error
        set_icon(root)

        tk.Frame(root, bg=TEAL, height=4).pack(fill="x")
        pad = {"padx": 18}

        # bublina: obrázek + hláška
        self.bubble = tk.Frame(root, bg=CARD, highlightthickness=2, highlightbackground=persona.color)
        self.bubble.pack(fill="x", pady=(14, 0), **pad)
        self.sprite = SpriteLabel(self.bubble, self.sprites, bg=CARD)
        self.sprite.pack(side="left", padx=(8, 0), pady=6)
        txt = tk.Frame(self.bubble, bg=CARD)
        txt.pack(side="left", fill="both", expand=True)
        tk.Label(txt, text=f"{persona.emoji} {persona.name}", font=FONT_MID, bg=CARD, fg=self.accent(),
                 anchor="w").pack(fill="x", padx=12, pady=(12, 0))
        self.lbl_said = tk.Label(txt, font=FONT, bg=CARD, fg=FG, anchor="w", justify="left", wraplength=290)
        self.lbl_said.pack(fill="x", padx=12, pady=(2, 12))

        tk.Label(root, text=T("what_now"), font=FONT_BIG, bg=BG, fg=FG).pack(anchor="w", pady=(14, 8), **pad)

        btns = tk.Frame(root, bg=BG)
        btns.pack(fill="x", **pad)
        for i, (mode, text, color, hover) in enumerate([
            (MODE_STUDY, T("btn_study"), TEAL_D, TEAL),
            (MODE_PLAY, T("btn_play"), PINK_D, PINK),
            (MODE_PROJECT, T("btn_project"), CARD2, HOVER2),
            (MODE_OTHER, T("btn_other"), CARD2, HOVER2),
        ]):
            b = flat_button(btns, text, color, hover, lambda m=mode: self.choose(m), pady=11)
            b.grid(row=i // 2, column=i % 2, sticky="ew", padx=4, pady=4)
        btns.columnconfigure(0, weight=1)
        btns.columnconfigure(1, weight=1)

        self.card = tk.Frame(root, bg=CARD)
        self.card.pack(fill="x", pady=(12, 0), **pad)
        self.lbl_mode = tk.Label(self.card, font=FONT_B, bg=CARD, fg=MUTED, anchor="w")
        self.lbl_mode.pack(fill="x", padx=14, pady=(12, 0))
        self.lbl_balance = tk.Label(self.card, font=FONT_BIG, bg=CARD, fg=GREEN, anchor="w")
        self.lbl_balance.pack(fill="x", padx=14)
        self.lbl_stats = tk.Label(self.card, font=FONT, bg=CARD, fg=MUTED, anchor="w", justify="left")
        self.lbl_stats.pack(fill="x", padx=14, pady=(2, 12))

        self.pause_frame = tk.Frame(root, bg=WARN_BG)
        tk.Label(self.pause_frame, text=T("paused_bar"), font=FONT_B, bg=WARN_BG, fg="#222",
                 wraplength=300, justify="left").pack(side="left", padx=10, pady=8)
        tk.Button(self.pause_frame, text=T("btn_continue"), font=FONT_B, relief="flat", bg="#fff6dc",
                  command=self.confirm_presence).pack(side="right", padx=8, pady=6)

        ov = tk.Frame(root, bg=BG)
        ov.pack(fill="x", pady=(10, 0), **pad)
        self.lbl_curfew = tk.Label(ov, font=FONT, bg=BG, fg=MUTED, anchor="w")
        self.lbl_curfew.pack(side="left")
        self.btn_override = flat_button(ov, T("btn_override"), CARD2, HOVER2, self.open_override,
                                        font=FONT_S, padx=10, pady=3)
        self.btn_override.pack(side="right")

        hist = tk.Frame(root, bg=BG)
        hist.pack(fill="x", pady=(12, 0), **pad)
        self.lbl_hist = tk.Label(hist, font=FONT, bg=BG, fg=MUTED, anchor="w", justify="left")
        self.lbl_hist.pack(anchor="w")
        side = tk.Frame(hist, bg=BG)
        side.pack(anchor="e", pady=(8, 0))
        flat_button(side, T("btn_stats"), CARD2, HOVER2, self.open_stats, font=FONT_S,
                    padx=10, pady=3).pack(side="left")
        flat_button(side, T("btn_settings"), CARD2, HOVER2, self.open_settings, font=FONT_S,
                    padx=10, pady=3).pack(side="left", padx=(6, 0))

        tk.Label(root, text=T("footer"), font=FONT_S, bg=BG, fg=MUTED, wraplength=420, justify="left").pack(
            side="bottom", anchor="w", pady=10, **pad)

        if psutil is None:
            self.notice(T("no_psutil"), "alert")

        self.say("greet", T("what_now"))
        self.bring_to_front()
        self.refresh()
        self.root.after(500, self.scan_loop)
        self.root.after(15000, self.autosave)
        self.root.after(200, self.poll_tray)
        self.root.after(60 * 1000, self._schedule_update_check)

    def accent(self) -> str:
        """Barva jména postavy: ve světlém vzhledu tmavší, ať je čitelná."""
        return self.persona.color if THEME == "dark" else ACCENT

    # ------------------------------------------------------------------ hlášky
    def say(self, key: str, fallback: str, **fmt) -> tuple[str, str]:
        text = self.persona.say(key, fallback, **fmt)
        mood = MOOD_FOR.get(key, "happy")
        self.lbl_said.config(text=text)
        self.sprite.set_mood(mood)
        self._said_at = time.monotonic()
        return text, mood

    def idle_mood(self) -> str:
        g = self.gate
        if g.in_curfew():
            return "sleepy"
        if g.mode in WORK_MODES:
            return "study"
        return "happy"

    # ------------------------------------------------------------------ okno
    def hide(self):
        if self.tray is not None:
            self.root.withdraw()
        else:
            self.root.iconify()
        self.root.after(3000, trim_memory)

    def visible(self) -> bool:
        return self.root.state() == "normal"

    def gaming(self) -> bool:
        return self.gate.game_running

    def bring_to_front(self, user: bool = False):
        """Ukáže hlavní okno. Když běží hra a nechtěl to uživatel, nedělá nic (nebere hře focus)."""
        if self.gaming() and not user:
            return
        self.root.deiconify()
        self.refresh()
        self.root.lift()
        self.root.attributes("-topmost", True)
        self.root.after(1500, lambda: self.root.attributes("-topmost", False))
        self.root.focus_force()

    def poll_tray(self):
        try:
            while True:
                cmd = self.cmd_q.get_nowait()
                if isinstance(cmd, tuple) and cmd[0] == "update":
                    self.on_update_info(cmd[1], cmd[2])
                elif cmd == "open":
                    self.bring_to_front(user=True)
                elif cmd == "study":
                    self.bring_to_front(user=True)
                    self.choose(MODE_STUDY)
                elif cmd == "project":
                    self.bring_to_front(user=True)
                    self.choose(MODE_PROJECT)
                elif cmd == "play":
                    self.bring_to_front(user=True)
                    self.choose(MODE_PLAY)
                elif cmd == "stats":
                    self.open_stats()
                elif cmd == "settings":
                    self.bring_to_front(user=True)
                    self.open_settings()
                elif cmd == "finish":
                    self.finish_match()
        except queue.Empty:
            pass
        self.root.after(200, self.poll_tray)

    def open_stats(self):
        try:
            self.gate.save()
            path = self.gate.stats_path()
            open_path(path)
        except Exception:
            log_error(traceback.format_exc())
            self.notice(T("stats_fail"), "alert")

    # ------------------------------------------------------------------ akce
    def choose(self, mode: str):
        err = self.gate.set_mode(mode)
        if err:
            key = "deny_curfew" if self.gate.in_curfew() else "deny_balance"
            text, mood = self.say(key, err)
            self.notice(text, mood, extra=err)
        elif mode == MODE_STUDY:
            text, mood = self.say("study_start", T("study_started"))
            self.notice(text, mood, extra=T("study_started_x", min=self.gate.cfg.get("presence_check_every_min", 20)),
                        auto_close=7)
            self.root.after(1500, self.hide)
        elif mode == MODE_PROJECT:
            text, mood = self.say("project_start", T("project_started"))
            self.notice(text, mood, auto_close=7,
                        extra=T("project_started_x", min=self.gate.cfg.get("presence_check_every_min", 20)))
            self.root.after(1500, self.hide)
        elif mode == MODE_PLAY:
            m = int(self.gate.balance_sec() // 60)
            text, mood = self.say("play_start", T("play_started", min=m), min=m)
            self.notice(text, mood, extra=T("play_started_x"), auto_close=7)
            self.root.after(1500, self.hide)
        else:
            self.root.after(800, self.hide)
        self.refresh()

    def confirm_presence(self):
        self.gate.confirm_presence()
        if self.presence_win is not None:
            self.presence_win.destroy()
            self.presence_win = None
        self.refresh()

    # ------------------------------------------------------------------ výjimka
    def open_override(self):
        if self.override_win is not None:
            self.override_win.deiconify()
            self.override_win.lift()
            return
        g = self.gate
        cfg = g.cfg
        w = tk.Toplevel(self.root, bg=BG)
        set_icon(w)
        self.override_win = w
        w.title(T("ov_title"))
        w.geometry("440x360")
        w.resizable(False, False)
        w.protocol("WM_DELETE_WINDOW", self._close_override)
        tk.Frame(w, bg=WARN_BG, height=4).pack(fill="x")
        body = tk.Frame(w, bg=BG)
        body.pack(fill="both", expand=True)

        def page_reason():
            for c in body.winfo_children():
                c.destroy()
            tk.Label(body, text=T("ov_why"), font=FONT_MID, bg=BG, fg=FG).pack(anchor="w", padx=16, pady=(14, 4))
            tk.Label(body, text=T("ov_info", wait=cfg.get("override_wait_min", 15),
                                  bonus=cfg.get("override_bonus_min", 60), left=g.overrides_left()),
                     font=FONT_S, bg=BG, fg=MUTED, wraplength=405, justify="left").pack(anchor="w", padx=16)
            txt = tk.Text(body, height=5, font=FONT, bg=CARD, fg=FG, insertbackground=FG, relief="flat",
                          wrap="word", highlightthickness=1, highlightbackground=CARD2)
            txt.pack(fill="both", expand=True, padx=16, pady=10)
            txt.focus_set()
            err = tk.Label(body, font=FONT_S, bg=BG, fg=RED, wraplength=405, justify="left")
            err.pack(anchor="w", padx=16)

            def send():
                e = g.request_override(txt.get("1.0", "end"))
                if e:
                    err.config(text=e)
                else:
                    page_wait()
                    self.refresh()

            flat_button(body, T("ov_send"), TEAL_D, TEAL, send, padx=12, pady=6).pack(
                anchor="e", padx=16, pady=(4, 14))

        def page_wait():
            for c in body.winfo_children():
                c.destroy()
            tk.Label(body, text=T("ov_wait"), font=FONT_MID, bg=BG, fg=FG).pack(anchor="w", padx=16, pady=(14, 0))
            tk.Label(body, text=f"„{g.override_reason}“", font=FONT_S, bg=BG, fg=MUTED, wraplength=405,
                     justify="left").pack(anchor="w", padx=16)
            timer = tk.Label(body, font=FONT_TIMER, bg=BG, fg=YELLOW)
            timer.pack(pady=(18, 4))
            tk.Label(body, text=T("ov_keep_open"), font=FONT_S, bg=BG, fg=MUTED, wraplength=405).pack(padx=16)
            btn = flat_button(body, T("ov_confirm"), CARD2, CARD2, None, padx=14, pady=8, state="disabled")
            btn.pack(pady=16)

            def confirm():
                m = g.confirm_override()
                self._close_override(cancel=False)
                if m:
                    text, mood = self.say("override_ready", T("ov_granted", min=m), min=m)
                    self.notice(text, mood, extra=T("ov_granted_x", min=m))
                self.refresh()

            def tick():
                if not w.winfo_exists():
                    return
                left = g.override_wait_left_sec()
                if left is None:  # zrušeno (třeba večerkou)
                    self._close_override(cancel=False)
                    return
                timer.config(text=f"{int(left // 60)}:{int(left % 60):02d}")
                if g.override_confirmable():
                    timer.config(text="0:00", fg=GREEN)
                    btn.config(state="normal", bg=GREEN, fg="#FFFFFF", activebackground=GREEN,
                               activeforeground="#FFFFFF", command=confirm)
                    btn._base_bg = GREEN
                    return
                w.after(500, tick)

            tick()

        if g.override_wait_left_sec() is not None:
            page_wait()
        else:
            page_reason()
        w.update_idletasks()
        w.geometry(f"+{self.root.winfo_rootx() + 15}+{self.root.winfo_rooty() + 60}")

    def _close_override(self, cancel: bool = True):
        if cancel and self.gate.override_wait_left_sec() is not None:
            self.gate.cancel_override()
        if self.override_win is not None:
            self.override_win.destroy()
            self.override_win = None
        self.refresh()

    # ------------------------------------------------------------------ nastavení
    def open_settings(self):
        if self.settings_win is not None:
            self.settings_win.deiconify()
            self.settings_win.lift()
            return
        cfg = self.gate.cfg
        w = tk.Toplevel(self.root, bg=BG)
        set_icon(w)
        self.settings_win = w
        w.title(T("set_title"))
        w.resizable(False, False)

        def close():
            self.settings_win = None
            w.destroy()

        w.protocol("WM_DELETE_WINDOW", close)
        tk.Frame(w, bg=TEAL, height=4).pack(fill="x")
        body = tk.Frame(w, bg=BG)
        body.pack(fill="both", expand=True, padx=16, pady=(8, 0))

        def heading(text, info=None):
            tk.Label(body, text=text, font=FONT_B, bg=BG, fg=FG).pack(anchor="w", pady=(10, 0))
            if info:
                tk.Label(body, text=info, font=FONT_S, bg=BG, fg=MUTED, wraplength=430,
                         justify="left").pack(anchor="w")

        def radios(var, options):
            row = tk.Frame(body, bg=BG)
            row.pack(anchor="w", pady=(2, 0))
            for value, label in options:
                tk.Radiobutton(row, text=label, value=value, variable=var, font=FONT, bg=BG, fg=FG,
                               selectcolor=CARD, activebackground=BG, activeforeground=FG,
                               highlightthickness=0).pack(side="left", padx=(0, 12))

        lang = tk.StringVar(value=get_lang())
        theme = tk.StringVar(value=cfg.get("theme") or "system")
        heading(T("set_lang"))
        radios(lang, list(LANGS.items()))
        heading(T("set_theme"))
        radios(theme, [("system", T("theme_system")), ("dark", T("theme_dark")), ("light", T("theme_light"))])

        def listbox(height=5):
            lb = tk.Listbox(body, height=height, width=56, font=FONT_S, bg=CARD, fg=FG, relief="flat",
                            selectbackground=TEAL_D, selectforeground="#FFFFFF", highlightthickness=1,
                            highlightbackground=CARD2, activestyle="none")
            lb.pack(fill="x", pady=(4, 0))
            return lb

        games = [("exe", e) for e in cfg.get("game_exe", [])] + [("dir", d) for d in cfg.get("game_roots", [])]
        white = list(cfg.get("whitelist_exe", []))
        steam = tk.BooleanVar(value=bool(cfg.get("steam_auto", True)))

        heading(T("set_games"), T("set_games_info"))
        tk.Checkbutton(body, text=T("set_steam"), variable=steam, font=FONT, bg=BG, fg=FG, selectcolor=CARD,
                       activebackground=BG, activeforeground=FG, highlightthickness=0).pack(anchor="w", pady=(4, 0))
        lb_games = listbox()

        def show_games():
            lb_games.delete(0, "end")
            for kind, val in games:
                icon = "🎮" if kind == "exe" else "📁"
                lb_games.insert("end", f"{icon} {T('item_' + kind)}: {val}")

        def add_exe():
            path = filedialog.askopenfilename(parent=w, title=T("pick_exe"), filetypes=EXE_TYPES)
            if path:
                name = os.path.basename(path.rstrip("/"))
                if ("exe", name) not in games:
                    games.append(("exe", name))
                show_games()

        def add_dir():
            path = filedialog.askdirectory(parent=w, title=T("pick_dir"))
            if path:
                path = os.path.normpath(path)
                if ("dir", path) not in games:
                    games.append(("dir", path))
                show_games()

        def remove_game():
            for i in reversed(lb_games.curselection()):
                del games[i]
            show_games()

        row = tk.Frame(body, bg=BG)
        row.pack(anchor="w", pady=(6, 0))
        for text, cmd in ((T("set_add_exe" if IS_WIN else "set_add_app"), add_exe), (T("set_add_dir"), add_dir), (T("set_remove"), remove_game)):
            flat_button(row, text, CARD2, HOVER2, cmd, font=FONT_S, padx=10, pady=3).pack(side="left", padx=(0, 6))
        show_games()

        heading(T("set_white"), T("set_white_info"))
        lb_white = listbox(3)

        def show_white():
            lb_white.delete(0, "end")
            for name in white:
                lb_white.insert("end", f"✅ {name}")

        def add_white():
            path = filedialog.askopenfilename(parent=w, title=T("pick_white"), filetypes=EXE_TYPES)
            name = os.path.basename(path.rstrip("/")) if path else ""
            if name and name not in white:
                white.append(name)
                show_white()

        def remove_white():
            for i in reversed(lb_white.curselection()):
                del white[i]
            show_white()

        row = tk.Frame(body, bg=BG)
        row.pack(anchor="w", pady=(6, 0))
        for text, cmd in ((T("set_add_white" if IS_WIN else "set_add_app_white"), add_white), (T("set_remove"), remove_white)):
            flat_button(row, text, CARD2, HOVER2, cmd, font=FONT_S, padx=10, pady=3).pack(side="left", padx=(0, 6))
        show_white()

        heading(T("set_toast"), T("set_toast_info"))
        corner = tk.StringVar(value=cfg.get("toast_corner", "top-right"))
        radios(corner, [(c, T("corner_" + c)) for c in ("top-right", "top-left", "bottom-right")])

        autostart = tk.BooleanVar(value=AUTOSTART_OK and autostart_enabled())
        if AUTOSTART_OK:
            tk.Checkbutton(body, text=T("set_autostart"), variable=autostart, font=FONT, bg=BG, fg=FG,
                           selectcolor=CARD, activebackground=BG, activeforeground=FG,
                           highlightthickness=0).pack(anchor="w", pady=(12, 0))
        updates = tk.BooleanVar(value=bool(cfg.get("check_updates", True)))
        row = tk.Frame(body, bg=BG)
        row.pack(fill="x", pady=(6, 0))
        tk.Checkbutton(row, text=T("set_updates"), variable=updates, font=FONT, bg=BG, fg=FG, selectcolor=CARD,
                       activebackground=BG, activeforeground=FG, highlightthickness=0).pack(side="left")
        flat_button(row, T("upd_check"), CARD2, HOVER2, lambda: self.check_updates(manual=True), font=FONT_S,
                    padx=10, pady=3).pack(side="right")
        tk.Label(body, text=T("set_version", v=VERSION), font=FONT_S, bg=BG, fg=MUTED).pack(anchor="w")

        row = tk.Frame(body, bg=BG)
        row.pack(fill="x", pady=(14, 0))
        tk.Label(row, text=T("set_rules_hint"), font=FONT_S, bg=BG, fg=MUTED, wraplength=280,
                 justify="left").pack(side="left")
        flat_button(row, T("set_open_config"), CARD2, HOVER2, lambda: open_path(HERE / "config.json"),
                    font=FONT_S, padx=10, pady=3).pack(side="right")

        def save():
            changes = {
                "language": lang.get(), "theme": theme.get(), "steam_auto": steam.get(),
                "game_exe": [v for k, v in games if k == "exe"],
                "game_roots": [v for k, v in games if k == "dir"],
                "whitelist_exe": white,
                "toast_corner": corner.get(), "check_updates": updates.get(),
            }
            need_restart = (lang.get() != get_lang()) or (theme.get() != (cfg.get("theme") or "system"))
            try:
                save_config(changes)
            except OSError:
                log_error(traceback.format_exc())
            cfg.update(changes)
            self.detector = GameDetector.from_config(cfg)
            if AUTOSTART_OK and autostart.get() != autostart_enabled():
                try:
                    set_autostart(autostart.get())
                except Exception:
                    log_error(traceback.format_exc())
            close()
            if need_restart and messagebox.askyesno(T("set_title"), T("set_restart"), parent=self.root):
                self.restart = True
                self.root.destroy()

        btns = tk.Frame(w, bg=BG)
        btns.pack(fill="x", padx=16, pady=14)
        flat_button(btns, T("set_save"), TEAL_D, TEAL, save, padx=16, pady=5).pack(side="right")
        flat_button(btns, T("set_cancel"), CARD2, HOVER2, close, padx=12, pady=5).pack(side="right", padx=8)
        w.update_idletasks()
        w.geometry(f"+{self.root.winfo_rootx() + 10}+{self.root.winfo_rooty() + 20}")

    # ------------------------------------------------------------------ popupy
    def _center(self, w: tk.Toplevel, y_frac: float = 1 / 3):
        w.update_idletasks()
        x = self.root.winfo_screenwidth() // 2 - w.winfo_width() // 2
        y = int(self.root.winfo_screenheight() * y_frac) - w.winfo_height() // 2
        w.geometry(f"+{x}+{max(20, y)}")

    def _popup_head(self, w: tk.Toplevel, mood: str) -> tk.Frame:
        """Společná hlavička popupu: barevný pruh, obrázek vlevo, vrací rámeček na text."""
        tk.Frame(w, bg=self.persona.color, height=6).pack(fill="x")
        row = tk.Frame(w, bg=BG)
        row.pack(fill="x")
        spr = SpriteLabel(row, self.sprites, bg=BG)
        spr.pack(side="left", padx=(14, 0), pady=10, anchor="n")
        spr.set_mood(mood)
        col = tk.Frame(row, bg=BG)
        col.pack(side="left", fill="both", expand=True)
        tk.Label(col, text=f"{self.persona.emoji} {self.persona.name}", font=FONT_MID,
                 bg=BG, fg=self.accent()).pack(anchor="w", padx=14, pady=(16, 2))
        return col

    def notice(self, text: str, mood: str, extra: str | None = None, auto_close: int | None = None):
        if self.gaming():  # během hry nikdy okno uprostřed ani focus – jen lišta v rohu
            self.toast(text, mood, extra)
            return
        if self.notice_win is not None:
            try:
                self.notice_win.destroy()
            except tk.TclError:
                pass
        w = tk.Toplevel(self.root, bg=BG)
        set_icon(w)
        self.notice_win = w
        w.title(self.persona.name)
        w.attributes("-topmost", True)
        w.resizable(False, False)
        col = self._popup_head(w, mood)
        tk.Label(col, text=text, font=FONT_TEXT, bg=BG, fg=FG, wraplength=330,
                 justify="left").pack(anchor="w", padx=14)
        if extra:
            tk.Label(col, text=extra, font=FONT_S, bg=BG, fg=MUTED, wraplength=330,
                     justify="left").pack(anchor="w", padx=14, pady=(8, 0))
        flat_button(w, T("ok"), TEAL_D, TEAL, w.destroy, width=10, pady=4).pack(
            anchor="e", padx=18, pady=(4, 14))
        self._center(w)
        w.lift()
        if not self.gaming():
            w.focus_force()
        if auto_close:
            w.after(auto_close * 1000, lambda: w.winfo_exists() and w.destroy())

    def ask_presence(self, text: str):
        if self.presence_win is not None:
            return
        w = tk.Toplevel(self.root, bg=BG)
        set_icon(w)
        self.presence_win = w
        w.title(T("presence_title"))
        w.attributes("-topmost", True)
        w.resizable(False, False)
        w.protocol("WM_DELETE_WINDOW", self.confirm_presence)
        col = self._popup_head(w, "study")
        tk.Label(col, text=text, font=(_F, 14), bg=BG, fg=FG, wraplength=320,
                 justify="left").pack(anchor="w", padx=14)
        tk.Label(col, text=T("presence_info", min=self.gate.cfg.get("presence_answer_within_min", 2)),
                 font=FONT_S, bg=BG, fg=MUTED, wraplength=320, justify="left").pack(anchor="w", padx=14, pady=(6, 0))
        flat_button(w, T("presence_btn"), TEAL_D, TEAL, self.confirm_presence, width=14, pady=6).pack(pady=(4, 14))
        self._center(w, 0.18)

    def _no_activate(self, w):
        """Windows: okno, které se po kliknutí neaktivuje a nebere hře myš ani klávesnici."""
        if not IS_WIN:
            return
        try:
            import ctypes
            hwnd = int(w.wm_frame(), 16)
            u32 = ctypes.windll.user32
            u32.GetWindowLongPtrW.restype = ctypes.c_ssize_t
            u32.SetWindowLongPtrW.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_ssize_t]
            style = u32.GetWindowLongPtrW(ctypes.c_void_p(hwnd), -20)  # GWL_EXSTYLE
            u32.SetWindowLongPtrW(hwnd, -20, style | 0x08000000 | 0x80)  # NOACTIVATE | TOOLWINDOW
        except Exception:
            pass

    def _beep(self):
        try:
            if IS_WIN:
                import winsound
                winsound.MessageBeep(winsound.MB_ICONASTERISK)
            else:
                self.root.bell()
        except Exception:
            pass

    def toast(self, text: str, mood: str, extra: str | None = None, seconds: int | None = 8,
              build=None) -> tk.Toplevel:
        """Malá lišta v rohu obrazovky: nebere focus, sama zmizí, kliknutím se zavře."""
        if self.toast_win is not None:
            try:
                self.toast_win.destroy()
            except tk.TclError:
                pass
        w = tk.Toplevel(self.root, bg=self.persona.color)
        self.toast_win = w
        w.withdraw()
        w.overrideredirect(True)
        w.attributes("-topmost", True)
        box = tk.Frame(w, bg=BG)
        box.pack(fill="both", expand=True, padx=(4, 0))
        spr = SpriteLabel(box, self.sprites, bg=BG)
        spr.pack(side="left", padx=(6, 0), pady=6, anchor="n")
        spr.set_mood(mood)
        col = tk.Frame(box, bg=BG)
        col.pack(side="left", fill="both", expand=True, padx=(8, 10), pady=8)
        tk.Label(col, text=text, font=FONT_B, bg=BG, fg=FG, wraplength=240, justify="left").pack(anchor="w")
        if extra:
            tk.Label(col, text=extra, font=FONT_S, bg=BG, fg=MUTED, wraplength=240, justify="left").pack(anchor="w")
        if build:
            build(col)
        else:
            for wid in (w, box, spr, col, *col.winfo_children()):
                wid.bind("<Button-1>", lambda e: w.winfo_exists() and w.destroy())
        w.update_idletasks()
        width, height = max(330, w.winfo_reqwidth()), max(84, w.winfo_reqheight())
        sw, sh, m = self.root.winfo_screenwidth(), self.root.winfo_screenheight(), 16
        corner = self.gate.cfg.get("toast_corner", "top-right")
        x = m if corner == "top-left" else sw - width - m
        y = sh - height - 64 if corner == "bottom-right" else m + 24
        w.geometry(f"{width}x{height}+{x}+{y}")
        self._no_activate(w)
        w.deiconify()
        self._beep()
        if seconds:
            w.after(seconds * 1000, lambda: w.winfo_exists() and w.destroy())
        return w

    def ask_finish(self, ev):
        """Odpočet před zavřením hry: lišta v rohu s tlačítky + na Windows zkratka Ctrl+Shift+D."""
        self._close_finish()
        text, _ = self.say("deadline", ev.text, min=ev.minutes)
        hotkey = IS_WIN and self.tray is not None

        def build(col):
            lbl = tk.Label(col, font=FONT_B, bg=BG, fg=YELLOW, wraplength=240, justify="left")
            lbl.pack(anchor="w", pady=(4, 0))
            if hotkey:
                tk.Label(col, text=T("toast_finish_hk", min=ev.minutes), font=FONT_S, bg=BG, fg=FG,
                         wraplength=240, justify="left").pack(anchor="w")
            row = tk.Frame(col, bg=BG)
            row.pack(anchor="w", pady=(6, 0))
            flat_button(row, T("toast_finish_btn"), PINK_D, PINK, self.finish_match, font=FONT_S,
                        padx=10, pady=2).pack(side="left")
            flat_button(row, T("toast_close_btn"), CARD2, HOVER2, self._close_game_now, font=FONT_S,
                        padx=10, pady=2).pack(side="left", padx=6)

            def tick():
                if not lbl.winfo_exists():
                    return
                left = self.gate.deadline_left_sec()
                if left is None:
                    self._close_finish()
                    return
                lbl.config(text=T("toast_finish", s=int(left)))
                lbl.after(500, tick)

            tick()

        self.finish_win = self.toast(text, "alert", ev.text, seconds=None, build=build)
        if hotkey:
            self.tray.hotkey(True)

    def finish_match(self):
        m = self.gate.start_finish()
        self._close_finish()
        if m:
            self.notice(T("finish_ok", min=m), "alert", extra=T("finish_ok_x"), auto_close=6)

    def _close_game_now(self):
        if self.gate.deadline_at is not None:
            self.gate.deadline_at = self.gate.clock.now()
        self._close_finish()

    def _close_finish(self):
        if self.tray is not None:
            self.tray.hotkey(False)
        if self.finish_win is not None:
            try:
                self.finish_win.destroy()
            except tk.TclError:
                pass
            self.finish_win = None

    # ------------------------------------------------------------------ smyčka
    def scan_loop(self):
        try:
            games = self.detector.find_running()
            for ev in self.gate.tick(games):
                self.handle(ev)
            if self.pending_update and not self.gaming() and self.pending_update["version"] not in self._update_asked:
                self.show_update()
        except Exception:
            log_error(traceback.format_exc())
        self.refresh()
        self.root.after(self.scan_ms, self.scan_loop)

    def handle(self, ev):
        ct = self.gate.curfew_times()
        if ev.kind == "kill":
            failed = kill_processes(ev.procs)
            if time.monotonic() - self._last_kill_notice > 20:
                self._last_kill_notice = time.monotonic()
                text, mood = self.say(ev.key or "kill_balance", ev.text)
                closed = sorted({n for _, n in ev.procs} - set(failed))
                extra = ev.text
                if closed:
                    extra += "\n" + T("closed", names=", ".join(closed))
                if failed:
                    extra += "\n" + T("not_closed", names=", ".join(sorted(set(failed))))
                if ev.minutes:
                    extra += "\n" + T("debt", min=ev.minutes)
                extra += "\n" + T("left_today", t=fmt_min(self.gate.balance_sec()))
                self.notice(text, mood, extra=extra)
                self.bring_to_front()  # během hry (jiná povolená hra běží) nic neudělá
        elif ev.kind == "deadline":
            self.ask_finish(ev)
        elif ev.kind == "finish_done":
            text, mood = self.say("finish_done", ev.text)
            self.notice(text, mood, extra=T("debt", min=ev.minutes) if ev.minutes else None)
        elif ev.kind == "warn":
            text, mood = self.say("warn", ev.text, min=ev.minutes)
            self.notice(text, mood, auto_close=10)
        elif ev.kind == "switched":
            text, mood = self.say("switched", ev.text, min=ev.minutes)
            self.notice(text, mood, extra=ev.text, auto_close=10)
        elif ev.kind == "match_warn":
            text, mood = self.say("match_warn", ev.text, min=ev.minutes)
            self.notice(text, mood, extra=ev.text, auto_close=10)
        elif ev.kind == "curfew_warn":
            text, mood = self.say("curfew_warn", ev.text, min=ev.minutes)
            self.notice(text, mood, extra=T("curfew_warn_x", **ct))
        elif ev.kind == "presence":
            text, _ = self.say("presence", ev.text)
            self.ask_presence(text)

    def refresh(self):
        g = self.gate
        bal = g.balance_sec()
        ct = g.curfew_times()
        if self.visible():  # schované okno nepřekresluje (šetří CPU), ikona u hodin se obnovuje dál
            self._refresh_window(bal, ct)

        if self.tray is not None:
            m = int(max(0, bal) // 60)
            text = str(m) if m < 100 else f"{m // 60}h"
            if g.in_curfew():
                color, text, tip = PURPLE, "Zz", T("tip_curfew", **ct)
            elif g.mode in WORK_MODES and not g.paused:
                color = TEAL_D
                tip = T("tip_project" if g.mode == MODE_PROJECT else "tip_study", t=fmt_min(bal))
            elif bal > 0:
                color, tip = GREEN, T("tip_play", t=fmt_min(bal))
            else:
                color, tip = RED, T("tip_zero")
            self.tray.update(text, color, f"{T('app_title')} – {tip}")

    def _refresh_window(self, bal: float, ct: dict):
        g = self.gate
        d = g.today()
        mode_text = mode_label(g.mode)
        if g.mode in WORK_MODES and g.paused:
            mode_text += T("paused_suffix")
        self.lbl_mode.config(text=T("now", mode=mode_text))
        self.lbl_balance.config(text=T("can_play", t=fmt_min(bal)), fg=GREEN if bal > 0 else RED)
        extra = T("today_bonus", t=fmt_min(d["bonus_sec"])) if d["bonus_sec"] else ""
        if d.get("project_sec"):
            extra = T("today_project", t=fmt_min(d["project_sec"])) + extra
        self.lbl_stats.config(text=T("today_stats", s=fmt_min(d["study_sec"]), p=fmt_min(d["play_sec"])) + extra)
        if g.mode in WORK_MODES and g.paused:
            if not self.pause_frame.winfo_ismapped():
                self.pause_frame.pack(fill="x", padx=18, pady=(10, 0), after=self.card)
        else:
            self.pause_frame.pack_forget()

        if g.in_curfew():
            self.lbl_curfew.config(text=T("curfew_now", **ct), fg=PURPLE)
        else:
            m = g.minutes_to_curfew()
            self.lbl_curfew.config(text=T("curfew_in", t=fmt_min(m * 60), **ct), fg=YELLOW if m <= 60 else MUTED)

        wait = g.override_wait_left_sec()
        if wait is not None:
            self.btn_override.config(text=T("btn_override_wait"), state="normal")
        elif g.in_curfew() or g.overrides_left() <= 0:
            self.btn_override.config(text=T("btn_override_none") if g.overrides_left() <= 0 else T("btn_override"),
                                     state="disabled")
        else:
            self.btn_override.config(text=T("btn_override"), state="normal")

        hist = g.history(7)
        s7 = sum(h[1] for h in hist) * 60
        p7 = sum(h[2] for h in hist) * 60
        j7 = sum(h[3] for h in hist) * 60
        ts, tp, tj = g.totals()
        self.lbl_hist.config(text=T("hist", s7=fmt_min(s7), p7=fmt_min(p7), j7=fmt_min(j7),
                                    ts=fmt_min(ts), tp=fmt_min(tp), tj=fmt_min(tj)))

        # obrázek se po minutě od poslední hlášky vrátí k náladě podle stavu
        if time.monotonic() - self._said_at > 60:
            self.sprite.set_mood(self.idle_mood())

    # ------------------------------------------------------------------ aktualizace
    def check_updates(self, manual: bool = False):
        """Zeptá se GitHubu na novou verzi (ve vlákně, ať appka nezamrzne)."""
        import updater

        def work():
            self.cmd_q.put(("update", updater.latest_release(), manual))

        threading.Thread(target=work, daemon=True).start()

    def _schedule_update_check(self):
        if self.gate.cfg.get("check_updates", True):
            self.check_updates()
        self.root.after(24 * 3600 * 1000, self._schedule_update_check)

    def on_update_info(self, info: dict | None, manual: bool):
        import updater
        if info is None:
            if manual:
                self.notice(T("upd_offline"), "alert")
            return
        if not updater.is_newer(info["version"], VERSION):
            if manual:
                self.notice(T("upd_none", v=VERSION), "happy")
            return
        self.pending_update = info
        if manual or info["version"] not in self._update_asked:
            self.show_update()

    def show_update(self):
        """Nabídka aktualizace. Během hry počká, až hra skončí (viz scan_loop)."""
        info = self.pending_update
        if info is None or self.gaming() or self.update_win is not None:
            return
        self._update_asked.add(info["version"])
        w = tk.Toplevel(self.root, bg=BG)
        set_icon(w)
        self.update_win = w
        w.title(T("upd_title"))
        w.resizable(False, False)

        def close():
            self.update_win = None
            w.destroy()

        w.protocol("WM_DELETE_WINDOW", close)
        col = self._popup_head(w, "happy")
        tk.Label(col, text=T("upd_available", v=info["version"].lstrip("v")), font=FONT_TEXT, bg=BG, fg=FG,
                 wraplength=330, justify="left").pack(anchor="w", padx=14)
        frozen = getattr(sys, "frozen", False)
        status = tk.Label(col, text=T("upd_info") if frozen and info["url"] else T("upd_source"), font=FONT_S,
                          bg=BG, fg=MUTED, wraplength=330, justify="left")
        status.pack(anchor="w", padx=14, pady=(8, 0))
        row = tk.Frame(w, bg=BG)
        row.pack(anchor="e", padx=18, pady=(8, 14))

        def update_now():
            import updater
            status.config(text=T("upd_working"), fg=YELLOW)
            for b in row.winfo_children():
                b.config(state="disabled")

            def work():
                try:
                    exe = updater.download(info["url"])
                    self.root.after(0, lambda: self._apply_update(exe))
                except Exception as e:
                    log_error(traceback.format_exc())
                    self.root.after(0, lambda: (status.config(text=T("upd_failed", e=e), fg=RED),
                                                [b.config(state="normal") for b in row.winfo_children()]))

            threading.Thread(target=work, daemon=True).start()

        if frozen and info["url"]:
            flat_button(row, T("upd_btn"), TEAL_D, TEAL, update_now, padx=14, pady=5).pack(side="right")
        flat_button(row, T("upd_page"), CARD2, HOVER2, lambda: open_path(info["page"]), padx=10,
                    pady=5).pack(side="right", padx=6)
        flat_button(row, T("upd_later"), CARD2, HOVER2, close, padx=10, pady=5).pack(side="right")
        self._center(w, 0.3)

    def _apply_update(self, new_exe):
        import updater
        self.gate.save()
        updater.start_apply(new_exe, sys.executable, os.getpid())
        self.root.destroy()  # nová verze počká, až se tahle zavře, přepíše ji a spustí

    def autosave(self):
        try:
            self.gate.save()
        except Exception:
            log_error(traceback.format_exc())
        self._saves += 1
        if self._saves % 20 == 0 and not self.visible():  # každých 5 min, když je okno schované
            trim_memory()
        self.root.after(15000, self.autosave)

    def _tk_error(self, *args):
        log_error("".join(traceback.format_exception(*args)))


# ------------------------------------------------------------------ první spuštění
def choose_language(root: tk.Tk) -> str | None:
    """Při prvním spuštění: výběr jazyka. Vrátí "cs"/"en", nebo None (okno zavřeno)."""
    chosen: list[str] = []
    root.title("Game Lock")
    root.configure(bg=BG)
    set_icon(root)
    frame = tk.Frame(root, bg=BG)
    frame.pack(padx=30, pady=24)
    tk.Label(frame, text="🎵", font=FONT_BIG, bg=BG, fg=TEAL).pack()
    tk.Label(frame, text=T("first_pick"), font=FONT_MID, bg=BG, fg=FG).pack(pady=(4, 14))

    def pick(code):
        chosen.append(code)
        frame.destroy()
        root.quit()

    row = tk.Frame(frame, bg=BG)
    row.pack()
    for code, name in LANGS.items():
        flat_button(row, name, TEAL_D, TEAL, lambda c=code: pick(c), width=12, pady=8).pack(side="left", padx=6)
    root.protocol("WM_DELETE_WINDOW", root.quit)
    root.update_idletasks()
    x = root.winfo_screenwidth() // 2 - root.winfo_width() // 2
    root.geometry(f"+{x}+{root.winfo_screenheight() // 3}")
    root.mainloop()
    return chosen[0] if chosen else None


def selftest() -> int:
    """Postaví appku s dočasnými daty a bez hlídání her, projde okna a skončí. 0 = v pořádku."""
    import tempfile
    from pathlib import Path
    cfg = load_config()
    set_lang(cfg.get("language") or "en")
    apply_theme(cfg.get("theme"))
    errors: list[str] = []

    class NoGames:
        @staticmethod
        def find_running():
            return []

    root = tk.Tk()
    gate = Gate(cfg, Path(tempfile.mkdtemp()) / "data.json", Clock())
    app = App(root, gate, NoGames(), scan_ms=200, persona=Persona(load_json(HERE / "personas.json", {})))
    root.report_callback_exception = lambda *a: errors.append("".join(traceback.format_exception(*a)))

    def run():
        try:
            for mood in MOODS:
                app.sprites.get(mood)
            if app.tray is not None:
                app.tray.update("12", GREEN, "selftest")
            gate.save()
            assert gate.stats_path().exists(), "stats.xlsx"
            app.open_settings()
            app.settings_win.destroy()
            app.settings_win = None
            app.open_override()
            app._close_override()
            app.toast("selftest", "alert", "toast")
            app.pending_update = {"version": "v99.0.0", "url": None, "page": ""}
            app.show_update()
            assert app.update_win is not None, "update window"
            app.update_win.destroy()
            app.update_win = None
            app.notice("selftest", "happy")
            app.hide()
            app.bring_to_front()
        except Exception:
            errors.append(traceback.format_exc())
        root.after(800, root.destroy)

    root.after(800, run)
    root.mainloop()
    if app.tray is not None:
        app.tray.stop()
    print("\n".join(errors) if errors else "selftest ok")
    return 1 if errors else 0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--test", action="store_true", help="zrychlený čas (1 min = 1 s), testovací data")
    ap.add_argument("--speed", type=float, default=None)
    ap.add_argument("--fake-time", default=None, help="HH:MM – předstíraný čas startu")
    ap.add_argument("--selftest", action="store_true", help="otevře okna na zkoušku a skončí (CI, build)")
    ap.add_argument("--apply-update", nargs=2, metavar=("OLD_EXE", "PID"), help=argparse.SUPPRESS)
    args = ap.parse_args()
    if args.apply_update:
        from pathlib import Path
        import updater
        old_exe = Path(args.apply_update[0])
        try:
            updater.apply_update(HERE, old_exe, int(args.apply_update[1]))
        except Exception:
            with open(old_exe.parent / "errors.log", "a", encoding="utf-8") as f:
                f.write(f"\n[{datetime.now():%Y-%m-%d %H:%M:%S}] update\n{traceback.format_exc()}\n")
            subprocess.Popen([str(old_exe)], cwd=str(old_exe.parent))
        return
    if args.selftest:
        sys.exit(selftest())

    lock = single_instance(47614 if args.test else 47613)
    if lock is None:
        return  # už běží

    cfg = load_config()
    set_lang(cfg.get("language") or "cs")
    apply_theme(cfg.get("theme"))
    try:
        import updater
        threading.Thread(target=updater.cleanup, daemon=True).start()
    except Exception:
        pass
    speed = args.speed if args.speed is not None else (60.0 if args.test else 1.0)
    fake = None
    if args.fake_time:
        h, m = map(int, args.fake_time.split(":"))
        fake = datetime.now().replace(hour=h, minute=m, second=0, microsecond=0)
    data_path = HERE / ("data_test.json" if args.test else "data.json")

    root = tk.Tk()
    if not cfg.get("language"):
        lang = choose_language(root)
        if lang is None:
            return
        set_lang(lang)
        cfg["language"] = lang
        save_config({"language": lang})
        first_run = True
    else:
        first_run = False

    gate = Gate(cfg, data_path, Clock(speed=speed, fake_start=fake))
    settings = gate.data.setdefault("settings", {})
    settings.pop("persona", None)
    persona = Persona(load_json(HERE / "personas.json", {}))
    if getattr(sys, "frozen", False) and not settings.get("autostart_set"):
        try:  # .exe se při prvním spuštění sám přidá do spouštění s Windows
            set_autostart(True)
            settings["autostart_set"] = True
            gate.save()
        except Exception:
            log_error(traceback.format_exc())
    atexit.register(gate.save)

    app = App(root, gate, GameDetector.from_config(cfg), scan_ms=1000 if args.test else 2000, persona=persona)
    if first_run:
        app.notice(T("first_games"), "happy")
    root.mainloop()
    gate.save()
    if app.restart:
        if app.tray is not None:
            app.tray.stop()
        lock.close()
        cmd = [sys.executable] if getattr(sys, "frozen", False) else [sys.executable, os.path.abspath(__file__)]
        subprocess.Popen(cmd + sys.argv[1:], cwd=str(HERE))


if __name__ == "__main__":
    try:
        main()
    except Exception:
        log_error(traceback.format_exc())
        raise

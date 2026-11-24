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
import tkinter as tk
from datetime import datetime
from tkinter import filedialog, messagebox

from i18n import LANGS, T, get_lang, set_lang
from zamek_core import (
    HERE, MODE_OTHER, MODE_PLAY, MODE_SERIES, MODE_STUDY,
    Clock, GameDetector, Gate, fmt_min, kill_processes, load_config, mode_label, psutil, save_config, load_json,
)

try:
    import pystray
    from PIL import Image, ImageDraw, ImageFont
except ImportError:  # bez toho appka funguje, jen nemá ikonu u hodin
    pystray = None
    Image = None

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
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER,
                            r"Software\Microsoft\Windows\CurrentVersion\Themes\Personalize") as k:
            return "light" if winreg.QueryValueEx(k, "AppsUseLightTheme")[0] else "dark"
    except OSError:
        return "dark"


def apply_theme(name: str | None):
    """Nastaví barvy (globální konstanty) před stavbou oken. "system" = podle Windows."""
    global THEME
    THEME = name if name in THEMES else system_theme()
    globals().update(THEMES[THEME])


apply_theme("dark")

FONT = ("Segoe UI", 11)
FONT_S = ("Segoe UI", 9)
FONT_B = ("Segoe UI Semibold", 11)
FONT_BIG = ("Segoe UI Semibold", 20)
FONT_MID = ("Segoe UI Semibold", 13)
FONT_TIMER = ("Segoe UI Semibold", 34)

FALLBACK_PERSONA = {"name": "Miku", "emoji": "🎵", "color": "#39C5BB", "lines": {}}

IMG_DIR = HERE / "obrazky"
MOODS = ("happy", "study", "angry", "sleepy", "alert")
# která nálada patří ke které hlášce
MOOD_FOR = {
    "greet": "happy", "play_start": "happy", "time_up": "happy", "override_ready": "happy",
    "study_start": "study", "presence": "study",
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
                    if Image is not None:
                        with Image.open(path) as im:
                            delay = max(60, int(im.info.get("duration", 500)))
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
def tray_image(text: str, color: str):
    size = 64
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    d.rounded_rectangle((0, 0, size - 1, size - 1), radius=14, fill=color)
    fsize = {1: 46, 2: 40, 3: 30}.get(len(text), 24)
    try:
        font = ImageFont.truetype("segoeuib.ttf", fsize)
    except OSError:
        font = ImageFont.load_default()
    box = d.textbbox((0, 0), text, font=font)
    w, h = box[2] - box[0], box[3] - box[1]
    d.text(((size - w) / 2 - box[0], (size - h) / 2 - box[1]), text, font=font, fill="white")
    return img


RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
RUN_NAME = "HerniZamek"


def autostart_enabled() -> bool:
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY) as k:
            winreg.QueryValueEx(k, RUN_NAME)
            return True
    except OSError:
        return False


def set_autostart(on: bool):
    """Spouštění se startem Windows (jen pro .exe; zápis do HKCU\\...\\Run)."""
    import winreg
    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_SET_VALUE) as k:
        if on:
            winreg.SetValueEx(k, RUN_NAME, 0, winreg.REG_SZ, f'"{sys.executable}"')
        else:
            try:
                winreg.DeleteValue(k, RUN_NAME)
            except OSError:
                pass


class Tray:
    """Ikona vedle hodin. Běží ve vlastním vlákně, příkazy posílá do fronty pro Tk."""

    def __init__(self, cmd_q: queue.Queue):
        self.q = cmd_q
        self._state = None
        self._last_push = 0.0
        items = [
            pystray.MenuItem(T("tray_open"), lambda: self.q.put("open"), default=True),
            pystray.MenuItem(T("tray_study"), lambda: self.q.put("study")),
            pystray.MenuItem(T("tray_play"), lambda: self.q.put("play")),
            pystray.MenuItem(T("tray_stats"), lambda: self.q.put("stats")),
            pystray.MenuItem(T("tray_settings"), lambda: self.q.put("settings")),
        ]
        if getattr(sys, "frozen", False):
            def toggle(icon, item):
                try:
                    set_autostart(not autostart_enabled())
                except Exception:
                    log_error(traceback.format_exc())
            items += [pystray.Menu.SEPARATOR,
                      pystray.MenuItem(T("tray_autostart"), toggle, checked=lambda item: autostart_enabled())]
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
            self.icon.icon = tray_image(text, color)
            self.icon.title = tooltip[:120]
            self._state = state
            self._last_push = time.monotonic()
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
    if THEME == "dark":  # tmavý titulek okna (Windows 10 20H1+ / 11)
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
        self.settings_win: tk.Toplevel | None = None
        self._last_kill_notice = 0.0
        self._said_at = 0.0
        self.cmd_q: queue.Queue = queue.Queue()
        self.tray = None
        if pystray is not None:
            try:
                self.tray = Tray(self.cmd_q)
                atexit.register(self.tray.stop)
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
            (MODE_SERIES, T("btn_series"), CARD2, HOVER2),
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
        self.lbl_hist.pack(side="left")
        side = tk.Frame(hist, bg=BG)
        side.pack(side="right", anchor="n")
        flat_button(side, T("btn_stats"), CARD2, HOVER2, self.open_stats, font=FONT_S,
                    padx=10, pady=3).pack(fill="x")
        flat_button(side, T("btn_settings"), CARD2, HOVER2, self.open_settings, font=FONT_S,
                    padx=10, pady=3).pack(fill="x", pady=(4, 0))

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
        if g.mode == MODE_STUDY:
            return "study"
        return "happy"

    # ------------------------------------------------------------------ okno
    def hide(self):
        if self.tray is not None:
            self.root.withdraw()
        else:
            self.root.iconify()

    def bring_to_front(self):
        self.root.deiconify()
        self.root.lift()
        self.root.attributes("-topmost", True)
        self.root.after(1500, lambda: self.root.attributes("-topmost", False))
        self.root.focus_force()

    def poll_tray(self):
        try:
            while True:
                cmd = self.cmd_q.get_nowait()
                if cmd == "open":
                    self.bring_to_front()
                elif cmd == "study":
                    self.bring_to_front()
                    self.choose(MODE_STUDY)
                elif cmd == "play":
                    self.bring_to_front()
                    self.choose(MODE_PLAY)
                elif cmd == "stats":
                    self.open_stats()
                elif cmd == "settings":
                    self.bring_to_front()
                    self.open_settings()
        except queue.Empty:
            pass
        self.root.after(200, self.poll_tray)

    def open_stats(self):
        try:
            self.gate.save()
            path = self.gate.stats_path()
            os.startfile(str(path))
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
            path = filedialog.askopenfilename(parent=w, title=T("pick_exe"), filetypes=[("exe", "*.exe")])
            if path:
                name = os.path.basename(path)
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
        for text, cmd in ((T("set_add_exe"), add_exe), (T("set_add_dir"), add_dir), (T("set_remove"), remove_game)):
            flat_button(row, text, CARD2, HOVER2, cmd, font=FONT_S, padx=10, pady=3).pack(side="left", padx=(0, 6))
        show_games()

        heading(T("set_white"), T("set_white_info"))
        lb_white = listbox(3)

        def show_white():
            lb_white.delete(0, "end")
            for name in white:
                lb_white.insert("end", f"✅ {name}")

        def add_white():
            path = filedialog.askopenfilename(parent=w, title=T("pick_white"), filetypes=[("exe", "*.exe")])
            if path and os.path.basename(path) not in white:
                white.append(os.path.basename(path))
                show_white()

        def remove_white():
            for i in reversed(lb_white.curselection()):
                del white[i]
            show_white()

        row = tk.Frame(body, bg=BG)
        row.pack(anchor="w", pady=(6, 0))
        for text, cmd in ((T("set_add_white"), add_white), (T("set_remove"), remove_white)):
            flat_button(row, text, CARD2, HOVER2, cmd, font=FONT_S, padx=10, pady=3).pack(side="left", padx=(0, 6))
        show_white()

        row = tk.Frame(body, bg=BG)
        row.pack(fill="x", pady=(14, 0))
        tk.Label(row, text=T("set_rules_hint"), font=FONT_S, bg=BG, fg=MUTED, wraplength=280,
                 justify="left").pack(side="left")
        flat_button(row, T("set_open_config"), CARD2, HOVER2, lambda: os.startfile(str(HERE / "config.json")),
                    font=FONT_S, padx=10, pady=3).pack(side="right")

        def save():
            changes = {
                "language": lang.get(), "theme": theme.get(), "steam_auto": steam.get(),
                "game_exe": [v for k, v in games if k == "exe"],
                "game_roots": [v for k, v in games if k == "dir"],
                "whitelist_exe": white,
            }
            need_restart = (lang.get() != get_lang()) or (theme.get() != (cfg.get("theme") or "system"))
            try:
                save_config(changes)
            except OSError:
                log_error(traceback.format_exc())
            cfg.update(changes)
            self.detector = GameDetector.from_config(cfg)
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
        tk.Label(col, text=text, font=("Segoe UI", 13), bg=BG, fg=FG, wraplength=330,
                 justify="left").pack(anchor="w", padx=14)
        if extra:
            tk.Label(col, text=extra, font=FONT_S, bg=BG, fg=MUTED, wraplength=330,
                     justify="left").pack(anchor="w", padx=14, pady=(8, 0))
        flat_button(w, T("ok"), TEAL_D, TEAL, w.destroy, width=10, pady=4).pack(
            anchor="e", padx=18, pady=(4, 14))
        self._center(w)
        w.lift()
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
        tk.Label(col, text=text, font=("Segoe UI", 14), bg=BG, fg=FG, wraplength=320,
                 justify="left").pack(anchor="w", padx=14)
        tk.Label(col, text=T("presence_info", min=self.gate.cfg.get("presence_answer_within_min", 2)),
                 font=FONT_S, bg=BG, fg=MUTED, wraplength=320, justify="left").pack(anchor="w", padx=14, pady=(6, 0))
        flat_button(w, T("presence_btn"), TEAL_D, TEAL, self.confirm_presence, width=14, pady=6).pack(pady=(4, 14))
        self._center(w, 0.18)

    def ask_finish(self, ev):
        """Odpočet před zavřením hry s nabídkou Dohrát zápas."""
        self._close_finish()
        try:
            import winsound
            winsound.MessageBeep(winsound.MB_ICONEXCLAMATION)
        except Exception:
            pass
        w = tk.Toplevel(self.root, bg=BG)
        set_icon(w)
        self.finish_win = w
        w.title(T("finish_title"))
        w.attributes("-topmost", True)
        w.resizable(False, False)
        w.protocol("WM_DELETE_WINDOW", self._close_finish)
        text, _ = self.say("deadline", ev.text, min=ev.minutes)
        col = self._popup_head(w, "alert")
        tk.Label(col, text=text, font=("Segoe UI", 13), bg=BG, fg=FG, wraplength=330,
                 justify="left").pack(anchor="w", padx=14)
        info = T("finish_info", min=ev.minutes, per=self.gate.cfg.get("finish_match_per_day", 1))
        tk.Label(col, text=f"{ev.text}\n{info}", font=FONT_S, bg=BG, fg=MUTED, wraplength=330,
                 justify="left").pack(anchor="w", padx=14, pady=(8, 0))
        lbl = tk.Label(col, font=FONT_B, bg=BG, fg=YELLOW)
        lbl.pack(anchor="w", padx=14, pady=(8, 0))
        btns = tk.Frame(w, bg=BG)
        btns.pack(pady=(6, 14))

        def finish():
            m = self.gate.start_finish()
            self._close_finish()
            if m:
                self.notice(T("finish_ok", min=m), "alert", extra=T("finish_ok_x"), auto_close=6)
                self.root.after(1500, self.hide)

        def close_now():
            if self.gate.deadline_at is not None:
                self.gate.deadline_at = self.gate.clock.now()
            self._close_finish()

        flat_button(btns, T("finish_btn", min=ev.minutes), PINK_D, PINK, finish, pady=6).pack(side="left", padx=6)
        flat_button(btns, T("finish_close"), CARD2, HOVER2, close_now, pady=6).pack(side="left", padx=6)

        def tick():
            if not w.winfo_exists():
                return
            left = self.gate.deadline_left_sec()
            if left is None:
                self._close_finish()
                return
            lbl.config(text=T("finish_count", s=int(left)))
            w.after(500, tick)

        tick()
        self._center(w, 0.25)
        w.lift()
        w.focus_force()

    def _close_finish(self):
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
                self.bring_to_front()
        elif ev.kind == "deadline":
            self.ask_finish(ev)
        elif ev.kind == "finish_done":
            text, mood = self.say("finish_done", ev.text)
            self.notice(text, mood, extra=T("debt", min=ev.minutes) if ev.minutes else None)
        elif ev.kind == "warn":
            text, mood = self.say("warn", ev.text, min=ev.minutes)
            self.notice(text, mood, auto_close=10)
        elif ev.kind == "curfew_warn":
            text, mood = self.say("curfew_warn", ev.text, min=ev.minutes)
            self.notice(text, mood, extra=T("curfew_warn_x", **ct))
        elif ev.kind == "presence":
            text, _ = self.say("presence", ev.text)
            self.ask_presence(text)

    def refresh(self):
        g = self.gate
        d = g.today()
        bal = g.balance_sec()
        ct = g.curfew_times()
        mode_text = mode_label(g.mode)
        if g.mode == MODE_STUDY and g.paused:
            mode_text += T("paused_suffix")
        self.lbl_mode.config(text=T("now", mode=mode_text))
        self.lbl_balance.config(text=T("can_play", t=fmt_min(bal)), fg=GREEN if bal > 0 else RED)
        extra = T("today_bonus", t=fmt_min(d["bonus_sec"])) if d["bonus_sec"] else ""
        self.lbl_stats.config(text=T("today_stats", s=fmt_min(d["study_sec"]), p=fmt_min(d["play_sec"])) + extra)
        if g.mode == MODE_STUDY and g.paused:
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
        s7 = sum(s for _, s, _ in hist) * 60
        p7 = sum(p for _, _, p in hist) * 60
        ts, tp = g.totals()
        self.lbl_hist.config(text=T("hist", s7=fmt_min(s7), p7=fmt_min(p7), ts=fmt_min(ts), tp=fmt_min(tp)))

        # obrázek se po minutě od poslední hlášky vrátí k náladě podle stavu
        if time.monotonic() - self._said_at > 60:
            self.sprite.set_mood(self.idle_mood())

        if self.tray is not None:
            m = int(max(0, bal) // 60)
            text = str(m) if m < 100 else f"{m // 60}h"
            if g.in_curfew():
                color, text, tip = PURPLE, "Zz", T("tip_curfew", **ct)
            elif g.mode == MODE_STUDY and not g.paused:
                color, tip = TEAL_D, T("tip_study", t=fmt_min(bal))
            elif bal > 0:
                color, tip = GREEN, T("tip_play", t=fmt_min(bal))
            else:
                color, tip = RED, T("tip_zero")
            self.tray.update(text, color, f"{T('app_title')} – {tip}")

    def autosave(self):
        try:
            self.gate.save()
        except Exception:
            log_error(traceback.format_exc())
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


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--test", action="store_true", help="zrychlený čas (1 min = 1 s), testovací data")
    ap.add_argument("--speed", type=float, default=None)
    ap.add_argument("--fake-time", default=None, help="HH:MM – předstíraný čas startu")
    args = ap.parse_args()

    lock = single_instance(47614 if args.test else 47613)
    if lock is None:
        return  # už běží

    cfg = load_config()
    set_lang(cfg.get("language") or "cs")
    apply_theme(cfg.get("theme"))
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

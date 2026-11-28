"""Screenshoty do README (docs/screenshots/). Fotí jen okna appky, ne celou obrazovku.

Spuštění: python tools/screenshots.py   (okna na pár sekund problikne na obrazovce)
Každý snímek běží ve vlastním procesu: python tools/screenshots.py <jazyk> <vzhled> <co>
"""
import ctypes
import importlib.machinery
import importlib.util
import subprocess
import sys
import tempfile
from ctypes import wintypes
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "docs" / "screenshots"
SHOTS = [
    ("en", "dark", "main"), ("en", "light", "main"), ("cs", "dark", "main"), ("cs", "light", "main"),
    ("en", "dark", "finish"), ("en", "light", "settings"), ("en", "dark", "first"),
    ("cs", "dark", "main", "plesoun"), ("en", "light", "main", "plesoun"),
]


def window_png(win, name):
    from PIL import ImageGrab
    win.update()
    hwnd = int(win.wm_frame(), 16)
    r = wintypes.RECT()
    ctypes.windll.dwmapi.DwmGetWindowAttribute(hwnd, 9, ctypes.byref(r), ctypes.sizeof(r))  # bez stínu
    ImageGrab.grab((r.left, r.top, r.right, r.bottom)).save(OUT / name)


def one(lang, theme, what, persona="miku"):
    ctypes.windll.shcore.SetProcessDpiAwareness(1)
    sys.path.insert(0, str(ROOT))
    import gamelock.app as hz
    from gamelock import i18n
    from gamelock.core import MODE_PLAY, Clock, Gate, load_json

    i18n.set_lang(lang)
    personas = load_json(ROOT / "assets" / "personas.json", {})
    hz.apply_theme(theme, personas.get(persona, {}).get("accent", "teal"))
    hz.TRAY_OK = False  # bez ikony u hodin
    root = hz.tk.Tk()
    name = f"{what}_{lang}_{theme}.png" if persona == "miku" else f"{what}_{persona}_{lang}_{theme}.png"
    if what == "first":
        root.after(800, lambda: (window_png(root, name), root.quit()))
        hz.choose_language(root)
        return

    cfg = load_json(ROOT / "assets" / "config.default.json", {})
    cfg.update(language=lang, theme=theme)
    gate = Gate(cfg, Path(tempfile.mkdtemp()) / "data.json",
                Clock(fake_start=datetime.now().replace(hour=21, minute=12, second=0)))
    d = gate.today()
    d["study_sec"], d["play_sec"] = 95 * 60, 40 * 60
    gate.data["days"]["2000-01-01"] = {"study_sec": 600 * 60, "play_sec": 420 * 60}

    class NoGames:
        def find_running(self):
            return []

    app = hz.App(root, gate, NoGames(), scan_ms=1000, persona=hz.Persona(personas, persona))
    app.say("greet", "")
    root.attributes("-topmost", True)

    def shoot():
        if what == "main":
            gate.mode = MODE_PLAY
            app.refresh()
            window_png(root, name)
        elif what == "finish":
            from gamelock.core import Event
            gate.deadline_at = gate.clock.now().replace(hour=23, minute=59)
            ev = Event("deadline", i18n.T("kill_curfew", **gate.curfew_times()), [(1, "game.exe")],
                       key="kill_curfew", minutes=20)
            gate.clock = type("C", (), {"now": staticmethod(lambda: gate.deadline_at.replace(minute=58, second=36))})()
            gate.tick = lambda games: []  # odpočet nemá zmizet
            app.ask_finish(ev)
            root.withdraw()
            root.after(1500, lambda: (window_png(app.finish_win, name), root.destroy()))
            return
        elif what == "settings":
            gate.cfg["game_exe"] = ["VALORANT-Win64-Shipping.exe", "FortniteClient-Win64-Shipping.exe"]
            gate.cfg["game_roots"] = [r"D:\Games"]
            gate.cfg["whitelist_exe"] = ["obs64.exe"]
            app.open_settings()
            app.settings_win.attributes("-topmost", True)
            root.withdraw()
            root.after(1500, lambda: (window_png(app.settings_win, name), root.destroy()))
            return
        root.destroy()

    root.after(1500, shoot)
    root.mainloop()


if __name__ == "__main__":
    if len(sys.argv) >= 4:
        one(*sys.argv[1:])
    else:
        OUT.mkdir(parents=True, exist_ok=True)
        for args in SHOTS:
            subprocess.run([sys.executable, __file__, *args], check=True)
            print("ok", *args)

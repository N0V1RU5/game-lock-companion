"""Logika herního zámku: čas, zůstatek, večerka, výjimky, hledání her.

Bez GUI, aby šla testovat samostatně (viz test_zamek.py).
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path

from i18n import T

try:
    import psutil
except ImportError:  # appka bez psutil hry nehlídá, ale nespadne
    psutil = None

import sys

VERSION = "1.4.1"  # musí odpovídat tagu releasu, podle něj se hledají aktualizace

IS_WIN = sys.platform == "win32"
IS_MAC = sys.platform == "darwin"

# U .exe (PyInstaller) leží config a data vedle exe, ne v dočasné složce.
HERE = Path(sys.executable).resolve().parent if getattr(sys, "frozen", False) else Path(__file__).resolve().parent

MODE_STUDY = "uceni"
MODE_PLAY = "hrani"
MODE_SERIES = "serial"   # starý režim (už není v menu), jen kvůli starým datům
MODE_PROJECT = "projekt"
WORK_MODES = ("uceni", "projekt")  # režimy, kdy se vydělává čas a hry se zavírají
MODE_OTHER = "jine"
MODE_NONE = "nic"

_MODE_KEYS = {MODE_STUDY: "mode_study", MODE_PLAY: "mode_play", MODE_SERIES: "mode_series",
              MODE_PROJECT: "mode_project",
              MODE_OTHER: "mode_other", MODE_NONE: "mode_none"}


def mode_label(mode: str) -> str:
    return T(_MODE_KEYS.get(mode, "mode_none"))


# --------------------------------------------------------------------------- čas
class Clock:
    """Skutečný čas, nebo v testu zrychlený / posunutý."""

    def __init__(self, speed: float = 1.0, fake_start: datetime | None = None):
        self.speed = speed
        self._real0 = time.monotonic()
        self._virt0 = fake_start or datetime.now()

    def now(self) -> datetime:
        return self._virt0 + timedelta(seconds=(time.monotonic() - self._real0) * self.speed)


class ManualClock:
    """Pro testy: čas se posouvá ručně."""

    def __init__(self, start: datetime):
        self.t = start

    def now(self) -> datetime:
        return self.t

    def advance(self, seconds: float):
        self.t += timedelta(seconds=seconds)


def parse_hhmm(s: str) -> tuple[int, int]:
    h, m = s.split(":")
    return int(h), int(m)


# ----------------------------------------------------------------------- config
def load_json(path: Path, default):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return default


def save_json(path: Path, data):
    tmp = path.with_suffix(path.suffix + ".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    os.replace(tmp, path)


def load_config() -> dict:
    """config.default.json (součást appky) + config.json (tvoje úpravy). Chybějící config.json se vytvoří."""
    default = load_json(HERE / "config.default.json", {})
    user_path = HERE / "config.json"
    if not user_path.exists() and default:
        save_json(user_path, default)
    user = load_json(user_path, {})
    return {**default, **user}


def save_config(changes: dict):
    """Zapíše změněné klíče do config.json (ostatní nechá, jak jsou)."""
    path = HERE / "config.json"
    cfg = load_json(path, {})
    cfg.update(changes)
    save_json(path, cfg)


def steam_install_paths() -> list[str]:
    """Kde je nainstalovaný Steam: Windows z registru, macOS a Linux ze známých složek."""
    if IS_WIN:
        try:
            import winreg
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Software\Valve\Steam") as k:
                return [os.path.normpath(winreg.QueryValueEx(k, "SteamPath")[0])]
        except OSError:
            return []
    home = Path.home()
    if IS_MAC:
        cands = [home / "Library" / "Application Support" / "Steam"]
    else:  # Linux: klasický, Flatpak a Snap Steam
        cands = [home / ".local" / "share" / "Steam", home / ".steam" / "steam",
                 home / ".var" / "app" / "com.valvesoftware.Steam" / ".local" / "share" / "Steam",
                 home / "snap" / "steam" / "common" / ".local" / "share" / "Steam"]
    out = []
    for c in cands:
        if c.is_dir():
            real = os.path.realpath(c)
            if real not in out:
                out.append(real)
    return out


def steam_install_path() -> str | None:
    paths = steam_install_paths()
    return paths[0] if paths else None


def steam_vdf_candidates(cfg_path: str) -> list[str]:
    out = [cfg_path] if cfg_path else []
    for sp in steam_install_paths():
        out.append(os.path.join(sp, "steamapps", "libraryfolders.vdf"))
    return out


def steam_library_roots(vdf_path: str) -> list[str]:
    """Najde všechny Steam knihovny (C:\\...\\Steam, D:\\SteamLibrary, ...)."""
    roots = []
    try:
        text = Path(vdf_path).read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return roots
    for m in re.finditer(r'"path"\s+"([^"]+)"', text):
        lib = m.group(1).replace("\\\\", "\\")
        roots.append(os.path.join(lib, "steamapps", "common"))
    return roots


# ---------------------------------------------------------------- hledání her
@dataclass
class GameDetector:
    roots: list[str]
    game_exe: set[str]
    java_keywords: list[str]
    whitelist_exe: set[str]
    whitelist_contains: list[str]
    whitelist_dirs: set[str] = field(default_factory=set)

    _cache: dict = field(default_factory=dict, repr=False)  # (pid, jméno) -> je to hra?

    @classmethod
    def from_config(cls, cfg: dict) -> "GameDetector":
        roots = list(cfg.get("game_roots", []))
        if cfg.get("steam_auto", True):
            for vdf in steam_vdf_candidates(cfg.get("steam_libraryfolders", "")):
                roots += steam_library_roots(vdf)
            for sp in steam_install_paths():
                roots.append(os.path.join(sp, "steamapps", "common"))
        roots = list(dict.fromkeys(norm_path(r) + "/" for r in roots if r))
        return cls(
            roots=roots,
            game_exe={e.lower() for e in cfg.get("game_exe", [])},
            java_keywords=[k.lower() for k in cfg.get("java_keywords", [])],
            whitelist_exe={e.lower() for e in cfg.get("whitelist_exe", [])},
            whitelist_contains=[k.lower() for k in cfg.get("whitelist_name_contains", [])],
            whitelist_dirs={d.lower() for d in cfg.get("whitelist_dirs", [])},
        )

    @staticmethod
    def _listed(names: set, lname: str, path: str) -> bool:
        """Jméno procesu / .exe ze seznamu, nebo macOS aplikace (Hra.app) v cestě."""
        if lname in names or (path and path.rsplit("/", 1)[-1] in names):
            return True
        return bool(path) and any(n.endswith(".app") and f"/{n}/" in path for n in names)

    def _in_roots(self, path: str) -> bool | None:
        for r in self.roots:
            if path.startswith(r):
                return path[len(r):].split("/", 1)[0] not in self.whitelist_dirs
        return None

    def is_game(self, name: str, exe: str | None, cmdline_fn=None) -> bool:
        lname = (name or "").lower()
        if not lname:
            return False
        path = norm_path(exe) if exe else ""
        if self._listed(self.whitelist_exe, lname, path) or any(k in lname for k in self.whitelist_contains):
            return False
        if self._listed(self.game_exe, lname, path):
            return True
        if lname in ("javaw.exe", "java.exe", "java") and cmdline_fn is not None:
            try:
                cmd = " ".join(cmdline_fn() or []).lower()
            except Exception:
                return False
            return any(k in cmd for k in self.java_keywords)
        if path:
            verdict = self._in_roots(path)
            if verdict is not None:
                return verdict
        # Linux: Windows hry přes Proton/Wine mají v exe cestu k Wine, hra je v příkazové řádce
        if not IS_WIN and lname.endswith(".exe") and cmdline_fn is not None:
            try:
                arg0 = (cmdline_fn() or [""])[0]
            except Exception:
                return False
            arg0 = norm_path(arg0)
            if re.match(r"^[a-z]:/", arg0):
                arg0 = arg0[2:]
            return bool(self._in_roots(arg0))
        return False

    def find_running(self) -> list:
        """Vrátí seznam (pid, name) běžících her. Cestu k exe zjišťuje jen u nových procesů."""
        if psutil is None:
            return []
        found, seen = [], {}
        for p in psutil.process_iter(["name"]):
            try:
                name = p.info.get("name") or ""
                key = (p.pid, name)
                verdict = self._cache.get(key)
                if verdict is None:
                    try:
                        exe = p.exe()
                    except (psutil.AccessDenied, OSError):
                        exe = None
                    verdict = self.is_game(name, exe, p.cmdline)
                seen[key] = verdict
                if verdict:
                    found.append(key)
            except psutil.NoSuchProcess:
                continue
        self._cache = seen
        return found


def norm_path(p: str) -> str:
    """Cesta pro porovnání: lomítka "/", malá písmena (Windows i macOS ignorují velikost)."""
    return os.path.normpath(p).replace("\\", "/").lower().rstrip("/")


def close_windows(pid: int) -> bool:
    """Pošle oknům procesu WM_CLOSE (jako Alt+F4). Funguje i na hry s anti-cheatem,
    které nejdou zabít (R6 / BattlEye, hry s EAC). Vrátí True, když nějaké okno našel."""
    if os.name != "nt":
        return False
    import ctypes
    from ctypes import wintypes
    user32 = ctypes.windll.user32
    found = []

    @ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    def cb(hwnd, _):
        owner = wintypes.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(owner))
        if owner.value == pid and user32.IsWindowVisible(hwnd):
            found.append(hwnd)
        return True

    user32.EnumWindows(cb, 0)
    for hwnd in found:
        user32.PostMessageW(hwnd, 0x0010, 0, 0)  # WM_CLOSE
    return bool(found)


def kill_processes(procs: list) -> list[str]:
    """Ukončí hry. Vrátí jména těch, co se nepodařilo zavřít."""
    failed = []
    for pid, name in procs:
        ok = False
        if psutil is not None:
            try:
                psutil.Process(pid).kill()
                ok = True
            except psutil.NoSuchProcess:
                ok = True
            except Exception:
                ok = False
        if not ok and IS_WIN:
            r = subprocess.run(
                ["taskkill", "/F", "/T", "/PID", str(pid)],
                capture_output=True,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
            ok = r.returncode == 0
        if not ok:
            ok = close_windows(pid)
        if not ok:
            failed.append(name)
    return failed


# ----------------------------------------------------------------------- stav
@dataclass
class Event:
    kind: str          # "kill", "warn", "presence", "override_ready", "curfew_warn"
    text: str
    procs: list = field(default_factory=list)
    key: str = ""      # klíč do personas.json (kill_curfew, warn, ...)
    minutes: int = 0


class Gate:
    """Celá pravidla. GUI jen volá tick() a zobrazuje vrácené události."""

    def __init__(self, cfg: dict, data_path: Path, clock):
        self.cfg = cfg
        self.data_path = data_path
        self.clock = clock
        self.data = load_json(data_path, {"days": {}})
        self.data.setdefault("days", {})
        self.mode = MODE_NONE
        self.paused = False             # učení pozastavené (neodpověděl na "jsi tu?")
        self.presence_pending_since: datetime | None = None
        self.next_presence: datetime | None = None
        self.override_ready_at: datetime | None = None
        self.override_reason = ""
        self._warned = set()
        self._curfew_warned_day = None
        self._match_warned_day = None
        self._last_tick = clock.now()
        self._saved_text = ""
        self._stats_rows: list | None = None
        self._seen_game_at: datetime | None = None   # kdy naposled běžela povolená hra
        self._prev_pids: set = set()                  # hry povolené v minulém ticku
        self.deadline_at: datetime | None = None      # odpočet "zavřu hru" (nabídka dohrání)
        self.deadline_pids: set = set()
        self.finish_until: datetime | None = None     # běží dohrávka zápasu
        self.finish_pids: set = set()
        self.finish_started: datetime | None = None

    # ---- den
    def day_key(self, t: datetime | None = None) -> str:
        return (t or self.clock.now()).strftime("%Y-%m-%d")

    def today(self) -> dict:
        d = self.data["days"].setdefault(self.day_key(), {})
        d.setdefault("study_sec", 0.0)
        d.setdefault("project_sec", 0.0)
        d.setdefault("play_sec", 0.0)
        d.setdefault("bonus_sec", 0.0)
        d.setdefault("overrides", [])
        return d

    def earned_sec(self, study_sec: float) -> float:
        """Kolik hraní dá učení. Prvních X min za DEN (součet, ne za sezení) se násobí."""
        ks = float(self.cfg.get("kickstart_min", 0)) * 60
        mult = float(self.cfg.get("kickstart_multiplier", 1.0))
        ratio = float(self.cfg.get("play_per_study", 1.0))
        first = min(study_sec, ks)
        return first * mult + (study_sec - first) * ratio

    def project_earned_sec(self, project_sec: float) -> float:
        """Projekty: mezi učením a hraním, výchozí 3 min projektu = 1 min hraní (bez rozjezdu)."""
        return project_sec * float(self.cfg.get("play_per_project", 1 / 3))

    def day_earned(self, d: dict) -> float:
        return self.earned_sec(d.get("study_sec", 0.0)) + self.project_earned_sec(d.get("project_sec", 0.0))

    def day_balance(self, d: dict) -> float:
        return (self.day_earned(d) + d.get("bonus_sec", 0.0)
                + d.get("adjust_sec", 0.0) - d.get("play_sec", 0.0))

    def balance_sec(self) -> float:
        return self.day_balance(self.today())

    # ---- večerka
    def in_curfew(self, t: datetime | None = None) -> bool:
        t = t or self.clock.now()
        sh, sm = parse_hhmm(self.cfg.get("curfew_start", "23:00"))
        eh, em = parse_hhmm(self.cfg.get("curfew_end", "06:00"))
        cur = t.hour * 60 + t.minute
        start, end = sh * 60 + sm, eh * 60 + em
        if start == end:
            return False
        if start < end:
            return start <= cur < end
        return cur >= start or cur < end

    def curfew_times(self) -> dict:
        return {"start": self.cfg.get("curfew_start", "23:00"), "end": self.cfg.get("curfew_end", "06:00")}

    def minutes_to_curfew(self) -> float:
        t = self.clock.now()
        sh, sm = parse_hhmm(self.cfg.get("curfew_start", "23:00"))
        c = t.replace(hour=sh, minute=sm, second=0, microsecond=0)
        if c <= t:
            c += timedelta(days=1)
        return (c - t).total_seconds() / 60

    # ---- režimy
    def set_mode(self, mode: str) -> str | None:
        """Vrátí chybovou hlášku, pokud režim nejde zapnout."""
        if mode == MODE_PLAY:
            if self.in_curfew():
                return T("deny_curfew", **self.curfew_times())
            if self.balance_sec() <= 0:
                return T("deny_balance")
        self.mode = mode
        self.paused = False
        self.presence_pending_since = None
        if mode in WORK_MODES:
            self.next_presence = self.clock.now() + timedelta(
                minutes=float(self.cfg.get("presence_check_every_min", 20)))
        self.save()
        return None

    def confirm_presence(self):
        self.paused = False
        self.presence_pending_since = None
        self.next_presence = self.clock.now() + timedelta(
            minutes=float(self.cfg.get("presence_check_every_min", 20)))

    # ---- výjimka
    def overrides_left(self) -> int:
        return int(self.cfg.get("override_max_per_day", 2)) - len(self.today()["overrides"])

    def request_override(self, reason: str) -> str | None:
        reason = reason.strip()
        if self.in_curfew():
            return T("ov_curfew")
        if self.override_ready_at is not None:
            return T("ov_waiting")
        if self.overrides_left() <= 0:
            return T("ov_none_left")
        need = int(self.cfg.get("override_min_reason_chars", 20))
        if len(reason) < need:
            return T("ov_short", need=need, have=len(reason))
        self.override_reason = reason
        self.override_ready_at = self.clock.now() + timedelta(
            minutes=float(self.cfg.get("override_wait_min", 15)))
        return None

    def override_confirmable(self) -> bool:
        return self.override_ready_at is not None and self.clock.now() >= self.override_ready_at

    def confirm_override(self) -> int | None:
        """Po odpočtu potvrdí výjimku. Vrátí počet minut, nebo None."""
        if not self.override_confirmable() or self.in_curfew():
            return None
        bonus = float(self.cfg.get("override_bonus_min", 60)) * 60
        d = self.today()
        d["bonus_sec"] += bonus
        d["overrides"].append({"cas": self.clock.now().strftime("%H:%M"), "duvod": self.override_reason})
        self.cancel_override()
        self._warned.clear()
        self.save()
        return int(bonus // 60)

    def cancel_override(self):
        self.override_ready_at = None
        self.override_reason = ""

    def override_wait_left_sec(self) -> float | None:
        if self.override_ready_at is None:
            return None
        return max(0.0, (self.override_ready_at - self.clock.now()).total_seconds())

    @property
    def game_running(self) -> bool:
        """Běží teď povolená hra (nebo dohrávka)? Pak appka nesmí brát hře focus."""
        return bool(self._prev_pids)

    # ---- dohrání zápasu
    def finishes_left(self) -> int:
        return int(self.cfg.get("finish_match_per_day", 1)) - int(self.today().get("finishes", 0))

    def deadline_left_sec(self) -> float | None:
        if self.deadline_at is None:
            return None
        return max(0.0, (self.deadline_at - self.clock.now()).total_seconds())

    def start_finish(self) -> int | None:
        """Tlačítko "Dohrát zápas" v odpočtu. Vrátí počet minut, nebo None."""
        if self.deadline_at is None or self.finishes_left() <= 0:
            return None
        now = self.clock.now()
        m = float(self.cfg.get("finish_match_min", 20))
        self.finish_until = now + timedelta(minutes=m)
        self.finish_pids = set(self.deadline_pids)
        self.finish_started = now
        self.deadline_at = None
        d = self.today()
        d["finishes"] = int(d.get("finishes", 0)) + 1
        self.save()
        return int(m)

    def _end_finish(self) -> int:
        """Konec dohrávky. Minuty hrané na dluh se odečtou z dalšího dne. Vrátí dluh v minutách."""
        start = self.finish_started or self.clock.now()
        self.finish_until = None
        self.finish_pids = set()
        self.finish_started = None
        eh, em = parse_hhmm(self.cfg.get("curfew_end", "06:00"))
        if start.hour * 60 + start.minute < eh * 60 + em:
            return 0  # po půlnoci: dluh zůstane v dnešku (zůstatek je záporný)
        d = self.data["days"].setdefault(self.day_key(start), {})
        debt = max(0.0, -self.day_balance(d))
        if debt > 0:
            nxt = self.data["days"].setdefault(self.day_key(start + timedelta(days=1)), {})
            d["adjust_sec"] = d.get("adjust_sec", 0.0) + debt
            nxt["adjust_sec"] = nxt.get("adjust_sec", 0.0) - debt
        self.save()
        return int(round(debt / 60))

    def _split_blocked(self, games: list, key: str, reason: str, now: datetime, events: list):
        """Hry by se měly zavřít. Vrátí (povolené, zavřít) podle dohrávky / odpočtu."""
        if key in ("kill_study", "kill_project"):
            return [], games
        if self.finish_until is not None:
            if now < self.finish_until:
                return ([g for g in games if g[0] in self.finish_pids],
                        [g for g in games if g[0] not in self.finish_pids])
            debt = self._end_finish()
            events.append(Event("kill", T("finish_over"), list(games), key=key, minutes=debt))
            return [], []
        if self.deadline_at is None:
            legit = [g for g in games if g[0] in self._prev_pids]
            if not legit or self.finishes_left() <= 0:
                return [], games
            self.deadline_at = now + timedelta(seconds=float(self.cfg.get("finish_prompt_sec", 90)))
            self.deadline_pids = {pid for pid, _ in legit}
            events.append(Event("deadline", reason, legit, key=key,
                                minutes=int(self.cfg.get("finish_match_min", 20))))
        if now < self.deadline_at:
            return ([g for g in games if g[0] in self.deadline_pids],
                    [g for g in games if g[0] not in self.deadline_pids])
        self.deadline_at = None
        self.deadline_pids = set()
        return [], games

    # ---- hlavní smyčka
    def tick(self, running_games: list) -> list[Event]:
        """running_games = seznam (pid, name). Vrací události pro GUI."""
        now = self.clock.now()
        dt = max(0.0, (now - self._last_tick).total_seconds())
        self._last_tick = now
        events: list[Event] = []
        d = self.today()

        # učení / projekt
        if self.mode in WORK_MODES:
            if self.presence_pending_since is not None:
                limit = float(self.cfg.get("presence_answer_within_min", 2)) * 60
                if (now - self.presence_pending_since).total_seconds() > limit:
                    self.paused = True
            elif self.next_presence and now >= self.next_presence:
                self.presence_pending_since = now
                events.append(Event("presence", T("presence"), key="presence"))
            if not self.paused:
                d["project_sec" if self.mode == MODE_PROJECT else "study_sec"] += dt

        # výjimka: ve večerku se čekání ruší
        if self.override_ready_at is not None and self.in_curfew():
            self.cancel_override()

        # "nový zápas už nezačínej" – dřív než varování před večerkou, ať se dlouhý zápas vejde
        match_min = float(self.cfg.get("match_warning_min", 30))
        if (match_min > 0 and not self.in_curfew() and self.minutes_to_curfew() <= match_min
                and self._match_warned_day != self.day_key()):
            self._match_warned_day = self.day_key()
            m = int(round(self.minutes_to_curfew()))
            events.append(Event("match_warn", T("match_warn", min=m), key="match_warn", minutes=m))

        # varování před večerkou
        warn_min = float(self.cfg.get("curfew_warning_min", 15))
        if (not self.in_curfew() and self.minutes_to_curfew() <= warn_min
                and self._curfew_warned_day != self.day_key()):
            self._curfew_warned_day = self.day_key()
            m = int(round(self.minutes_to_curfew()))
            events.append(Event("curfew_warn", T("curfew_warn", min=m),
                                key="curfew_warn", minutes=m))

        # hry
        allowed: list = []
        if running_games:
            reason = key = None
            if self.in_curfew():
                reason, key = T("kill_curfew", **self.curfew_times()), "kill_curfew"
            elif self.balance_sec() <= 0:
                if any(pid in self._prev_pids for pid, _ in running_games):
                    reason, key = T("time_up"), "time_up"
                else:
                    reason, key = T("kill_balance"), "kill_balance"
            if reason:
                allowed, blocked = self._split_blocked(list(running_games), key, reason, now, events)
                if blocked:
                    events.append(Event("kill", reason, blocked, key=key))
            else:
                allowed = list(running_games)
            if allowed:
                if self.mode != MODE_PLAY:
                    if self.mode in WORK_MODES:  # hra při učení/projektu: nezavírat, přepnout na Hraní
                        m = int(max(0, self.balance_sec()) // 60)
                        events.append(Event("switched", T("switched", mode=mode_label(self.mode), min=m),
                                            key="switched", minutes=m))
                        self.paused = False
                        self.presence_pending_since = None
                    self.mode = MODE_PLAY
                self._seen_game_at = now
                d["play_sec"] += dt
                if not reason:
                    left_min = self.balance_sec() / 60
                    for w in sorted(self.cfg.get("warn_before_end_min", [10, 2]), reverse=True):
                        if left_min <= w and w not in self._warned:
                            self._warned.add(w)
                            lm = max(0, int(round(left_min)))
                            events.append(Event("warn", T("warn", min=lm), key="warn", minutes=lm))
        else:
            if self.mode == MODE_PLAY:
                # resetuj varování, až zase bude dost času
                self._warned = {w for w in self._warned if self.balance_sec() / 60 <= w}
        self._prev_pids = {pid for pid, _ in allowed}

        # hra vypnutá během odpočtu / dohrávky
        if self.deadline_at is not None and not (self.deadline_pids & self._prev_pids):
            self.deadline_at = None
            self.deadline_pids = set()
        if self.finish_until is not None and not (self.finish_pids & self._prev_pids):
            debt = self._end_finish()
            events.append(Event("finish_done", T("finish_done"), key="finish_done",
                                minutes=debt))

        # hra vypnutá -> po chvilce zpátky z "Hraju" (krátká mezera = restart hry / launcher)
        if (self.mode == MODE_PLAY and not allowed and self._seen_game_at is not None
                and (now - self._seen_game_at).total_seconds() >= float(self.cfg.get("game_gone_sec", 15))):
            self.mode = MODE_OTHER
            self._seen_game_at = None
            self.save()

        return events

    # ---- přehled
    def history(self, days: int = 7) -> list[tuple[str, int, int, int]]:
        """(den, učení, hraní, projekty) v minutách za posledních N dní."""
        out = []
        base = self.clock.now()
        for i in range(days):
            k = (base - timedelta(days=i)).strftime("%Y-%m-%d")
            d = self.data["days"].get(k, {})
            out.append((k, int(d.get("study_sec", 0) // 60), int(d.get("play_sec", 0) // 60),
                        int(d.get("project_sec", 0) // 60)))
        return out

    def save(self):
        """Zapíše data a statistiky, jen když se od minula něco změnilo (šetří disk)."""
        self.today()
        text = json.dumps(self.data, ensure_ascii=False, indent=2)
        if text != self._saved_text:
            tmp = self.data_path.with_suffix(self.data_path.suffix + ".tmp")
            tmp.write_text(text, encoding="utf-8")
            os.replace(tmp, self.data_path)
            self._saved_text = text
        try:
            self.write_stats()
        except OSError:
            pass

    def day_stats(self, key: str) -> dict:
        d = self.data["days"].get(key, {})
        study = d.get("study_sec", 0.0)
        project = d.get("project_sec", 0.0)
        play = d.get("play_sec", 0.0)
        earned = self.day_earned(d) + d.get("adjust_sec", 0.0)
        bonus = d.get("bonus_sec", 0.0)
        return {"study": study, "project": project, "play": play, "earned": earned, "bonus": bonus,
                "unused": max(0.0, earned + bonus - play), "overrides": len(d.get("overrides", []))}

    def stats_path(self) -> Path:
        return self.data_path.with_name(self.data_path.stem.replace("data", "stats") + ".xlsx")

    def stats_rows(self) -> list[list]:
        """Řádky statistik: hlavička, dny, CELKEM. Čísla jsou čísla (minuty), poměr desetinný."""
        rows = [T("stats_head").split("|")]
        tot = {"study": 0.0, "project": 0.0, "play": 0.0, "earned": 0.0, "bonus": 0.0, "unused": 0.0,
               "overrides": 0}

        def row(label, s):
            work = s["study"] + s["project"]
            ratio = round(s["play"] / work, 2) if work >= 60 else None
            return [label, round(s["study"] / 60), round(s["project"] / 60), round(s["play"] / 60),
                    round(s["earned"] / 60), round(s["bonus"] / 60), round(s["unused"] / 60), ratio,
                    s["overrides"]]

        for key in sorted(self.data["days"]):
            s = self.day_stats(key)
            for k in tot:
                tot[k] += s[k]
            rows.append(row(key, s))
        rows.append(row(T("stats_total"), tot))
        return rows

    def write_stats(self):
        """stats.xlsx vedle dat (Excel ho otevře správně v každém jazyce Windows). Jen při změně."""
        path = self.stats_path()
        rows = self.stats_rows()
        if rows == self._stats_rows and path.exists():
            return
        tmp = path.with_suffix(".tmp")
        write_xlsx(tmp, rows)
        os.replace(tmp, path)  # když je soubor otevřený v Excelu, selže (OSError) a zapíše se příště
        self._stats_rows = rows

    def totals(self) -> tuple[float, float, float]:
        """Učení, hraní a projekty za celou dobu (sekundy)."""
        days = self.data["days"].values()
        return (sum(d.get("study_sec", 0) for d in days), sum(d.get("play_sec", 0) for d in days),
                sum(d.get("project_sec", 0) for d in days))



def _xml(t: str) -> str:
    return t.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def write_xlsx(path: Path, rows: list[list], widths=(12, 12, 14, 12, 15, 14, 16, 22, 14)):
    """Minimální .xlsx bez knihoven: tučná hlavička, ukotvený první řádek, tučný poslední řádek."""
    import zipfile

    def col(i):
        return "ABCDEFGHIJKLMNOPQRSTUVWXYZ"[i]

    body = []
    for r, row in enumerate(rows, 1):
        bold = r == 1 or r == len(rows)
        cells = []
        for c, v in enumerate(row):
            ref = f"{col(c)}{r}"
            if v is None:
                continue
            if isinstance(v, str):
                st = ' s="1"' if bold else ""
                cells.append(f'<c r="{ref}" t="inlineStr"{st}><is><t>{_xml(v)}</t></is></c>')
            else:
                dec = isinstance(v, float)  # poměr: 2 desetinná místa
                st = ' s="3"' if (dec and bold) else ' s="2"' if dec else ' s="4"' if bold else ""
                cells.append(f'<c r="{ref}"{st}><v>{v}</v></c>')
        body.append(f'<row r="{r}">{"".join(cells)}</row>')
    cols = "".join(f'<col min="{i + 1}" max="{i + 1}" width="{w}" customWidth="1"/>' for i, w in enumerate(widths))
    sheet = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
             '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
             '<sheetViews><sheetView workbookViewId="0"><pane ySplit="1" topLeftCell="A2" activePane="bottomLeft" state="frozen"/>'
             '</sheetView></sheetViews>'
             f'<cols>{cols}</cols><sheetData>{"".join(body)}</sheetData></worksheet>')
    styles = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
              '<styleSheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
              '<fonts count="2"><font><sz val="11"/><name val="Calibri"/></font>'
              '<font><b/><sz val="11"/><name val="Calibri"/></font></fonts>'
              '<fills count="2"><fill><patternFill patternType="none"/></fill><fill><patternFill patternType="gray125"/></fill></fills>'
              '<borders count="1"><border/></borders>'
              '<cellStyleXfs count="1"><xf/></cellStyleXfs>'
              '<cellXfs count="5"><xf/>'
              '<xf fontId="1" applyFont="1"/>'
              '<xf numFmtId="2" applyNumberFormat="1"/>'
              '<xf fontId="1" numFmtId="2" applyFont="1" applyNumberFormat="1"/>'
              '<xf fontId="1" applyFont="1"/></cellXfs></styleSheet>')
    files = {
        "[Content_Types].xml": '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
            '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
            '<Default Extension="xml" ContentType="application/xml"/>'
            '<Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>'
            '<Override PartName="/xl/worksheets/sheet1.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>'
            '<Override PartName="/xl/styles.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.styles+xml"/>'
            '</Types>',
        "_rels/.rels": '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
            '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/>'
            '</Relationships>',
        "xl/workbook.xml": '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" '
            'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">'
            '<sheets><sheet name="' + _xml(T("stats_sheet")) + '" sheetId="1" r:id="rId1"/></sheets></workbook>',
        "xl/_rels/workbook.xml.rels": '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
            '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet1.xml"/>'
            '<Relationship Id="rId2" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/styles" Target="styles.xml"/>'
            '</Relationships>',
        "xl/worksheets/sheet1.xml": sheet,
        "xl/styles.xml": styles,
    }
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as z:
        for name, data in files.items():
            z.writestr(name, data)


def fmt_min(sec: float) -> str:
    m = int(max(0, sec) // 60)
    if m >= 60:
        return f"{m // 60} h {m % 60} min"
    return f"{m} min"

"""Aktualizace z GitHub Releases: najde novější verzi, stáhne zip a přepíše program.

Nastavení (config.json), data a statistiky zůstanou.
Stahuje přes curl (je ve Windows 10/11, macOS i Linuxu), takže build nemusí obsahovat OpenSSL.

Postup: stažená nová verze se spustí z dočasné složky s --apply-update <stará exe> <pid>,
počká, až se stará appka zavře, zkopíruje se přes ni a spustí ji znovu.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import zipfile
from pathlib import Path

REPO = "N0V1RU5/game-lock-companion"
API = f"https://api.github.com/repos/{REPO}/releases/latest"
PLATFORM = {"win32": "windows", "darwin": "macos"}.get(sys.platform, "linux")
ASSET = f"GameLockCompanion-{PLATFORM}.zip"
# co se při aktualizaci nikdy nepřepíše (tvoje věci)
KEEP = {"config.json", "data.json", "data_test.json", "stats.xlsx", "stats_test.xlsx", "errors.log",
        "obrazky"}  # obrazky/ = vlastní obrázky ze starších verzí
_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)


def parse_version(v: str) -> tuple:
    parts = []
    for p in v.strip().lstrip("vV").split(".")[:3]:
        digits = "".join(ch for ch in p if ch.isdigit())
        parts.append(int(digits or 0))
    return tuple(parts + [0] * (3 - len(parts)))


def is_newer(tag: str, current: str) -> bool:
    return parse_version(tag) > parse_version(current)


def _curl(args: list[str], timeout: int) -> subprocess.CompletedProcess:
    return subprocess.run(["curl", "-sSfL", "--max-time", str(timeout), *args],
                          capture_output=True, timeout=timeout + 5, creationflags=_NO_WINDOW)


def latest_release(timeout: int = 15) -> dict | None:
    """{"version", "url" (zip pro tento systém nebo None), "page"}; None když GitHub nejde."""
    try:
        r = _curl(["-H", "Accept: application/vnd.github+json", API], timeout)
        if r.returncode != 0:
            return None
        data = json.loads(r.stdout.decode("utf-8"))
    except (OSError, ValueError, subprocess.SubprocessError):
        return None
    url = next((a.get("browser_download_url") for a in data.get("assets", []) if a.get("name") == ASSET), None)
    return {"version": data.get("tag_name", ""), "url": url, "page": data.get("html_url", "")}


def _extract(zip_path: Path, dest: Path):
    """Rozbalí zip a zachová spustitelnost souborů (macOS, Linux)."""
    with zipfile.ZipFile(zip_path) as z:
        for info in z.infolist():
            out = z.extract(info, dest)
            mode = (info.external_attr >> 16) & 0o777
            if mode and os.name != "nt":
                os.chmod(out, mode)


def download(url: str, timeout: int = 300) -> Path:
    """Stáhne a rozbalí novou verzi do dočasné složky, vrátí cestu k její exe."""
    tmp = Path(tempfile.mkdtemp(prefix="gamelock-update-"))
    zip_path = tmp / ASSET
    r = _curl(["-o", str(zip_path), url], timeout)
    if r.returncode != 0:
        raise OSError(r.stderr.decode("utf-8", "replace").strip() or "download failed")
    _extract(zip_path, tmp / "new")
    exe_name = "GameLockCompanion.exe" if PLATFORM == "windows" else "GameLockCompanion"
    found = list((tmp / "new").rglob(exe_name))
    if not found:
        raise OSError("the downloaded zip has no app inside")
    return found[0]


def start_apply(new_exe: Path, old_exe: str, pid: int):
    """Spustí novou verzi, která přepíše starou, až se stará zavře."""
    kw = {"creationflags": _NO_WINDOW | getattr(subprocess, "DETACHED_PROCESS", 0)} if os.name == "nt" \
        else {"start_new_session": True}
    subprocess.Popen([str(new_exe), "--apply-update", old_exe, str(pid)], cwd=str(new_exe.parent), **kw)


def _wait_for_exit(pid: int, timeout: float = 60):
    try:
        import psutil
    except ImportError:
        time.sleep(5)
        return
    end = time.time() + timeout
    while time.time() < end and psutil.pid_exists(pid):
        time.sleep(0.3)


def apply_update(new_dir: Path, old_exe: Path, pid: int):
    """Běží v nové verzi: nakopíruje se přes starou (kromě KEEP) a spustí ji."""
    _wait_for_exit(pid)
    install = old_exe.parent
    new_exe_name = "GameLockCompanion.exe" if PLATFORM == "windows" else "GameLockCompanion"
    for item in new_dir.iterdir():
        dst = install / item.name
        if item.name in KEEP and dst.exists():
            continue
        if item.name == new_exe_name:
            dst = old_exe  # zachová název staré exe (i "HerniZamek.exe") kvůli spouštění po startu
        if item.is_dir():
            staging = install / (item.name + ".new")
            shutil.rmtree(staging, ignore_errors=True)
            shutil.copytree(item, staging, symlinks=True)
            old = install / (item.name + ".old")
            shutil.rmtree(old, ignore_errors=True)
            if dst.exists():
                dst.rename(old)
            staging.rename(dst)
            shutil.rmtree(old, ignore_errors=True)
        else:
            shutil.copy2(item, dst)
    kw = {"creationflags": getattr(subprocess, "DETACHED_PROCESS", 0)} if os.name == "nt" \
        else {"start_new_session": True}
    subprocess.Popen([str(old_exe)], cwd=str(install), **kw)


def cleanup():
    """Smaže stažené aktualizace z dočasné složky (po úspěšné aktualizaci už nejsou potřeba)."""
    for d in Path(tempfile.gettempdir()).glob("gamelock-update-*"):
        if time.time() - d.stat().st_mtime > 3600:
            shutil.rmtree(d, ignore_errors=True)

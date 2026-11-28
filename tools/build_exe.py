"""Builds the app (PyInstaller, one folder) and a zip to share. Works on Windows, macOS and Linux.

  python tools/build_exe.py
      -> dist/GameLockCompanion/ + dist/GameLockCompanion-<windows|macos|linux>.zip (clean default config)
  python tools/build_exe.py --out kamos/HerniZamek --name HerniZamek --my-config
      -> a copy for a friend with YOUR rules (config.json), never your data

Needs: python -m pip install pyinstaller psutil   (+ pystray pillow on Linux for the tray icon)
"""
import argparse
import shutil
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SKIP = {"data.json", "data_test.json", "stats.xlsx", "stats_test.xlsx", "errors.log"}
FILES = ["LICENSE", "NOTICE.md", "README.md", "README.cs.md"]
PLATFORM = {"win32": "windows", "darwin": "macos"}.get(sys.platform, "linux")

# Modules the app never uses. Leaving them out saves ~20 MB (OpenSSL alone is ~7 MB).
# Keep urllib: pathlib imports it on Python 3.12. CI self-tests every built app before upload.
EXCLUDE = ["_ssl", "ssl", "_hashlib", "_decimal", "decimal", "_lzma", "lzma", "_bz2", "bz2",
           "_zstd", "compression.zstd", "pyexpat", "xml", "_elementtree", "unicodedata",
           "unittest", "pydoc", "doctest", "email", "http", "html", "sqlite3",
           "asyncio", "multiprocessing", "concurrent", "_ctypes_test", "lib2to3", "tkinter.test"]
# Windows and macOS don't need Pillow or pystray at all (own tray icon / no tray, GIFs via Tk).
EXCLUDE += ["PIL", "pystray"] if PLATFORM != "linux" else [
    "PIL._avif", "PIL.AvifImagePlugin", "PIL._webp", "PIL.WebPImagePlugin", "PIL._imagingcms",
    "PIL.ImageCms", "PIL._imagingtk", "PIL.ImageTk", "PIL.ImageQt", "PIL.ImageShow", "numpy"]
HIDDEN = ["pystray._appindicator", "pystray._gtk", "pystray._xorg"] if PLATFORM == "linux" else []


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="dist/GameLockCompanion")
    ap.add_argument("--name", default="GameLockCompanion")
    ap.add_argument("--my-config", action="store_true", help="include your config.json")
    a = ap.parse_args()
    target = (ROOT / a.out).resolve()
    zip_path = target.with_name(target.name + (f"-{PLATFORM}.zip" if not a.my_config else ".zip"))
    exe = a.name + (".exe" if PLATFORM == "windows" else "")

    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        cmd = [sys.executable, "-m", "PyInstaller", "--noconfirm", "--onedir", "--windowed", "--optimize", "2",
               "--name", a.name, "--distpath", str(tmp / "dist"), "--workpath", str(tmp / "build"),
               "--specpath", str(tmp)]
        if PLATFORM != "linux":
            cmd += ["--icon", str(ROOT / "assets" / "icon.ico")]
        for m in EXCLUDE:
            cmd += ["--exclude-module", m]
        for m in HIDDEN:
            cmd += ["--hidden-import", m]
        subprocess.run(cmd + [str(ROOT / "herni_zamek.pyw")], check=True)
        built = tmp / "dist" / a.name
        target.mkdir(parents=True, exist_ok=True)
        shutil.rmtree(target / "_internal", ignore_errors=True)
        shutil.copytree(built / "_internal", target / "_internal", symlinks=True)
        shutil.copy2(built / exe, target / exe)
    for name in FILES + (["config.json"] if a.my_config else []):
        if (ROOT / name).exists():
            shutil.copy2(ROOT / name, target / name)
    shutil.rmtree(target / "assets", ignore_errors=True)  # nastavení, hlášky, ikona, maskoti
    shutil.copytree(ROOT / "assets", target / "assets", ignore=shutil.ignore_patterns("_zaloha"))

    tmpzip = zip_path.with_suffix(".tmp")
    with zipfile.ZipFile(tmpzip, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as z:
        for p in sorted(target.rglob("*")):
            if p.is_file() and p.name not in SKIP and (a.my_config or p.name != "config.json"):
                z.write(p, Path(target.name) / p.relative_to(target))  # keeps the executable bit
    tmpzip.replace(zip_path)
    size = sum(p.stat().st_size for p in target.rglob("*") if p.is_file()) / 1e6
    print(f"Done: {target} ({size:.1f} MB)\n      {zip_path} ({zip_path.stat().st_size / 1e6:.1f} MB)")


if __name__ == "__main__":
    main()

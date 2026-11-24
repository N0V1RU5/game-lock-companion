"""Builds the Windows .exe (PyInstaller) and a zip to share.

  python tools/build_exe.py
      -> dist/GameLockCompanion/ + dist/GameLockCompanion-windows.zip (clean default config)
  python tools/build_exe.py --out kamos/HerniZamek --name HerniZamek --my-config
      -> a copy for a friend with YOUR rules (config.json), never your data

Needs: python -m pip install pyinstaller psutil pystray pillow
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
FILES = ["config.default.json", "personas.json", "icon.ico", "LICENSE", "NOTICE.md", "README.md", "README.cs.md"]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="dist/GameLockCompanion")
    ap.add_argument("--name", default="GameLockCompanion")
    ap.add_argument("--my-config", action="store_true", help="include your config.json")
    a = ap.parse_args()
    target = (ROOT / a.out).resolve()
    zip_path = target.with_name(target.name + ("-windows.zip" if not a.my_config else ".zip"))

    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        subprocess.run([sys.executable, "-m", "PyInstaller", "--noconfirm", "--onedir", "--windowed",
                        "--name", a.name, "--icon", str(ROOT / "icon.ico"),
                        "--hidden-import", "pystray._win32",
                        "--distpath", str(tmp / "dist"), "--workpath", str(tmp / "build"),
                        "--specpath", str(tmp), str(ROOT / "herni_zamek.pyw")], check=True)
        built = tmp / "dist" / a.name
        target.mkdir(parents=True, exist_ok=True)
        shutil.rmtree(target / "_internal", ignore_errors=True)
        shutil.copytree(built / "_internal", target / "_internal")
        shutil.copy2(built / f"{a.name}.exe", target / f"{a.name}.exe")
    for name in FILES + (["config.json"] if a.my_config else []):
        if (ROOT / name).exists():
            shutil.copy2(ROOT / name, target / name)
    shutil.rmtree(target / "obrazky", ignore_errors=True)
    shutil.copytree(ROOT / "obrazky", target / "obrazky", ignore=shutil.ignore_patterns("_zaloha"))

    tmpzip = zip_path.with_suffix(".tmp")
    with zipfile.ZipFile(tmpzip, "w", zipfile.ZIP_DEFLATED) as z:
        for p in sorted(target.rglob("*")):
            if p.is_file() and p.name not in SKIP and (a.my_config or p.name != "config.json"):
                z.write(p, Path(target.name) / p.relative_to(target))
    tmpzip.replace(zip_path)
    print(f"Done: {target}\n      {zip_path} ({zip_path.stat().st_size / 1e6:.1f} MB)")


if __name__ == "__main__":
    main()

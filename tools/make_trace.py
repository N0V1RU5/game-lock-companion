"""Obkreslovací šablona z tvých referencí.

1) tools/kresleni/1..5.png -> zmenší do 64×64 (NEAREST, bez rozmazání, na střed) -> tools/kresleni/ref_64/ref_64_<n>.png
2) Aseprite soubor tools/kresleni/obkresleni.aseprite:
   - spodní vrstva "Reference" (40 % krytí, zamčená) s ref_64_<n>.png
   - prázdná vrstva "Pixel Art" nad ní, do té kreslíš
   - snímek 1..5 = happy, study, angry, sleepy, alert (tagy)
Spuštění: python tools/make_trace.py   (existující obkresleni.aseprite nepřepíše, jen s --force)
"""
import subprocess
import sys
import tempfile
from pathlib import Path

from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parent))
from import_obrazky import find_aseprite  # noqa: E402

HERE = Path(__file__).resolve().parent / "kresleni"
N = 64
MOODS = ["happy", "study", "angry", "sleepy", "alert"]
OUT = HERE / "obkresleni.aseprite"


def fit_64(src: Path, dst: Path) -> tuple[int, int]:
    with Image.open(src) as im:
        im = im.convert("RGBA")
        k = N / max(im.size)
        w, h = max(1, round(im.width * k)), max(1, round(im.height * k))
        small = im.resize((w, h), Image.NEAREST)
    canvas = Image.new("RGBA", (N, N), (0, 0, 0, 0))
    canvas.paste(small, ((N - w) // 2, (N - h) // 2))
    canvas.save(dst)
    return w, h


def main():
    refs = []
    for i, mood in enumerate(MOODS, 1):
        src = HERE / f"{i}.png"
        if not src.exists():
            sys.exit(f"Chybí {src}")
        (HERE / "ref_64").mkdir(exist_ok=True)
        dst = HERE / "ref_64" / f"ref_64_{i}.png"
        w, h = fit_64(src, dst)
        refs.append(dst)
        print(f"  {src.name} -> {dst.name} ({w}×{h} v plátně 64×64) = {mood}")

    if OUT.exists() and "--force" not in sys.argv:
        print(f"{OUT} už existuje, nepřepisuju (tvoje kresba). Nový vytvoříš s --force.")
        return
    exe = find_aseprite()
    if not exe:
        sys.exit("Aseprite nenalezen.")
    frames = ", ".join(f'"{p.as_posix()}"' for p in refs)
    tags = ", ".join(f'"{m}"' for m in MOODS)
    lua = f"""
local refs = {{ {frames} }}
local tags = {{ {tags} }}
local spr = Sprite({N}, {N}, ColorMode.RGB)
local ref = spr.layers[1]
ref.name = "Reference"
ref.opacity = 102  -- 40 %
local art = spr:newLayer()
art.name = "Pixel Art"
for k = 2, #refs do spr:newEmptyFrame() end
for k, path in ipairs(refs) do
  spr:newCel(ref, k, Image{{ fromFile = path }}, Point(0, 0))
  spr.frames[k].duration = 0.5
  local t = spr:newTag(k, k); t.name = tags[k]
end
ref.isEditable = false
spr:saveAs("{OUT.as_posix()}")
print("ok")
"""
    with tempfile.TemporaryDirectory() as tmp:
        script = Path(tmp) / "trace.lua"
        script.write_text(lua, encoding="utf-8")
        subprocess.run([str(exe), "-b", "--script", str(script)], check=True)
    print(f"Hotovo: {OUT}")


if __name__ == "__main__":
    main()

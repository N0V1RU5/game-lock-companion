"""Převede tvoje obrázky nálad do formátu appky (obrazky/<nálada>.gif).

Použití (z kořene projektu):
  python tools/import_obrazky.py moje.aseprite            # soubor ze šablony (tagy happy, study…)
  python tools/import_obrazky.py slozka_s_obrazky         # happy.png / happy.gif / happy_1.png, happy_2.png…
  python tools/import_obrazky.py sheet.png                # sprite sheet: 5 řádků (happy, study, angry, sleepy, alert),
                                                          # čtvercová políčka, snímky zleva doprava
  python tools/import_obrazky.py obrazek.png --rozdel --px 64 --barev 24
                                                          # jeden obrázek s 5 postavami v pořadí čtení (jako výš);
                                                          # pozadí a popisky zmizí, zmenší na 64 px a appka zvětší
  python tools/import_obrazky.py tools/kresleni/Screenshots --mrizka --tol 6 --dry-run
                                                          # složka 5 screenshotů zvětšeného pixel-artu (podle abecedy
                                                          # = happy, study, angry, sleepy, alert); smaže tmavé pozadí
Přepínače:
  --kamos      zkopíruje výsledek i do verze pro kámoše a přebalí kamos/HerniZamek.zip
  --dry-run    jen zkontroluje a uloží náhled, nic nepřepíše
  --ms 400     délka snímku, když ji vstup neobsahuje (PNG)

Co dělá: průhledné pozadí (když chybí, vezme barvu z rohu), stejný ořez pro všechny nálady
(ať mají stejné proporce), zvětšení bez rozmazání na max. 104 px, animovaný GIF.
Původní obrázky zálohuje do obrazky/_zaloha/ (jen poprvé).
"""
import argparse
import json
import shutil
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path

from PIL import Image

ROOT = Path(__file__).resolve().parent.parent
IMG = ROOT / "obrazky"
KAMOS_DIR = ROOT / "kamos" / "HerniZamek"
KAMOS_ZIP = ROOT / "kamos" / "HerniZamek.zip"
MOODS = ["happy", "study", "angry", "sleepy", "alert"]
MAX_SIDE = 104  # appka zmenšuje až nad 110 px
DEFAULT_MS = {"happy": 450, "study": 400, "angry": 120, "sleepy": 700, "alert": 300}
GUIDE_LAYERS = ["voditka", "figurina", "Reference"]  # pomocné vrstvy, do výsledku nejdou


def find_aseprite() -> Path | None:
    for p in (Path(r"D:\SteamLibrary\steamapps\common\Aseprite\Aseprite.exe"),
              Path(r"C:\Program Files (x86)\Steam\steamapps\common\Aseprite\Aseprite.exe"),
              Path(r"C:\Program Files\Aseprite\Aseprite.exe")):
        if p.exists():
            return p
    found = shutil.which("aseprite")
    return Path(found) if found else None


# ---------- načtení vstupu -> {nálada: [(RGBA snímek, ms), ...]} ----------

def load_aseprite(path: Path) -> dict:
    exe = find_aseprite()
    if not exe:
        sys.exit("Nenašel jsem Aseprite.exe. Exportuj z Aseprite sprite sheet (PNG) a dej ho sem.")
    with tempfile.TemporaryDirectory() as tmp:
        sheet, data = Path(tmp) / "sheet.png", Path(tmp) / "data.json"
        subprocess.run([str(exe), "-b", *[x for l in GUIDE_LAYERS for x in ("--ignore-layer", l)], str(path),
                        "--sheet", str(sheet), "--sheet-type", "horizontal",
                        "--data", str(data), "--format", "json-array", "--list-tags"],
                       check=True, capture_output=True)
        meta = json.loads(data.read_text(encoding="utf-8"))
        with Image.open(sheet) as im:
            im = im.convert("RGBA")
            frames = []
            for fr in meta["frames"]:
                r = fr["frame"]
                frames.append((im.crop((r["x"], r["y"], r["x"] + r["w"], r["y"] + r["h"])), fr["duration"]))
    tags = {t["name"].lower(): (t["from"], t["to"]) for t in meta["meta"].get("frameTags", [])}
    if not tags:
        sys.exit("Soubor nemá tagy. Pojmenuj tagy happy, study, angry, sleepy, alert (nebo použij šablonu).")
    return {m: frames[a:b + 1] for m, (a, b) in tags.items() if m in MOODS}


def load_file_frames(path: Path, ms: int) -> list:
    out = []
    with Image.open(path) as im:
        n = getattr(im, "n_frames", 1)
        for i in range(n):
            im.seek(i)
            out.append((im.convert("RGBA"), int(im.info.get("duration", ms)) if n > 1 else ms))
    return out


def load_folder(folder: Path, ms: dict) -> dict:
    res = {}
    for m in MOODS:
        single = [folder / f"{m}{e}" for e in (".gif", ".png", ".webp")]
        single = [p for p in single if p.exists()]
        numbered = sorted(folder.glob(f"{m}_*.png"), key=lambda p: int(p.stem.split("_")[-1]) if p.stem.split("_")[-1].isdigit() else 0)
        if numbered:
            res[m] = [fr for p in numbered for fr in load_file_frames(p, ms[m])]
        elif single:
            res[m] = load_file_frames(single[0], ms[m])
    if not res:  # žádné happy.png…: vezmi obrázky podle abecedy v pořadí nálad
        files = sorted(p for p in folder.iterdir() if p.suffix.lower() in (".png", ".gif", ".webp"))
        for m, p in zip(MOODS, files):
            res[m] = load_file_frames(p, ms[m])
            print(f"  {p.name} -> {m}")
    return res


def load_sheet(path: Path, ms: dict) -> dict:
    with Image.open(path) as im:
        im = im.convert("RGBA")
    cell = im.height // len(MOODS)
    if cell == 0 or im.height % len(MOODS):
        sys.exit(f"Sheet má výšku {im.height} px, ta musí jít dělit 5 (jeden řádek na náladu).")
    bg = im.getpixel((0, 0))
    res = {}
    for row, m in enumerate(MOODS):
        frames = []
        for col in range(im.width // cell):
            c = im.crop((col * cell, row * cell, (col + 1) * cell, (row + 1) * cell))
            if not has_content(c, bg):
                break
            frames.append((c, ms[m]))
        if frames:
            res[m] = frames
    return res



# ---------- celý obrázek s 5 postavami (--rozdel) ----------

def _near(a, b, tol):
    return max(abs(a[0] - b[0]), abs(a[1] - b[1]), abs(a[2] - b[2])) <= tol


def _components(mask: list, w: int, h: int) -> list:
    """Souvislé oblasti v masce (seznam bool, w*h). Vrací [(počet, minx, miny, maxx, maxy, seed)]."""
    seen = bytearray(w * h)
    out = []
    for start in range(w * h):
        if not mask[start] or seen[start]:
            continue
        seen[start] = 1
        stack = [start]
        n, x0, y0, x1, y1 = 0, w, h, 0, 0
        while stack:
            i = stack.pop()
            x, y = i % w, i // w
            n += 1
            x0, y0, x1, y1 = min(x0, x), min(y0, y), max(x1, x), max(y1, y)
            for j in (i - 1 if x else -1, i + 1 if x < w - 1 else -1, i - w if y else -1, i + w if y < h - 1 else -1):
                if j >= 0 and mask[j] and not seen[j]:
                    seen[j] = 1
                    stack.append(j)
        out.append((n, x0, y0, x1, y1, start))
    return out


TOL = 40  # tolerance barvy pozadí (--tol)


def TOL_SET(v: int):
    global TOL
    TOL = v


def remove_background(img: Image.Image, tol: int | None = None) -> Image.Image:
    """Pozadí = barva rohu. Smaže ho od okrajů i ve větších uzavřených mezerách (mezi vlasy a tělem)."""
    tol = TOL if tol is None else tol
    from PIL import ImageDraw
    if img.getchannel("A").getextrema()[0] < 255:
        opaque = [p[:3] for p in pixels(img) if p[3] == 255]
        if not opaque:
            return img
        from collections import Counter
        bg, n = Counter(opaque).most_common(1)[0]
        if n < len(opaque) * 0.2:
            return img  # čistá průhlednost, žádná výplň pozadí
        flat = Image.new("RGBA", img.size, bg + (255,))
        flat.alpha_composite(img)
        img = flat
    else:
        bg = img.getpixel((0, 0))[:3]
    rgb = img.convert("RGB")
    key = (255, 0, 254)
    w, h = rgb.size
    for x, y in ((0, 0), (w - 1, 0), (0, h - 1), (w - 1, h - 1)):
        if _near(rgb.getpixel((x, y)), bg, tol):
            ImageDraw.floodfill(rgb, (x, y), key, thresh=tol)
    f = max(1, max(w, h) // 400)
    sw, sh = max(1, w // f), max(1, h // f)
    small = rgb.resize((sw, sh), Image.NEAREST)
    mask = [_near(p, bg, tol) for p in pixels(small)]
    min_area = max(4, sw * sh // 2500)
    for n, *_, seed in _components(mask, sw, sh):
        if n >= min_area:
            x, y = (seed % sw) * f, (seed // sw) * f
            if _near(rgb.getpixel((x, y)), bg, tol):
                ImageDraw.floodfill(rgb, (x, y), key, thresh=tol)
    alpha = Image.new("L", (w, h))
    alpha.putdata([0 if p == key else 255 for p in pixels(rgb)])
    out = img.convert("RGBA")
    out.putalpha(alpha)
    return out


def load_split(path: Path, ms: dict) -> dict:
    """Jeden obrázek s 5 postavami v pořadí čtení: happy, study, angry, sleepy, alert.
    Vezme 5 největších kusů; drobné ozdoby u postavy přidá, popisky nad ní zahodí."""
    with Image.open(path) as im:
        img = remove_background(im.convert("RGBA"))
    w, h = img.size
    f = max(1, max(w, h) // 400)
    sw, sh = max(1, w // f), max(1, h // f)
    small = img.getchannel("A").resize((sw, sh), Image.NEAREST)
    comps = sorted(_components([a > 0 for a in pixels(small)], sw, sh), key=lambda c: -c[0])
    if len(comps) < 5:
        sys.exit(f"Našel jsem jen {len(comps)} postav, potřebuju 5 oddělených pozadím.")
    boxes = [list(c[1:5]) for c in comps[:5]]
    for n, x0, y0, x1, y1, _ in comps[5:]:
        for b in boxes:
            bw, bh = b[2] - b[0], b[3] - b[1]
            if b[0] - bw * 0.6 <= x0 and x1 <= b[2] + bw * 0.6 and b[1] <= y0 and y1 <= b[3] + bh * 0.1:
                b[0], b[1], b[2], b[3] = min(b[0], x0), min(b[1], y0), max(b[2], x1), max(b[3], y1)
                break
    boxes.sort(key=lambda b: (b[1] + b[3]) / 2)
    rows, cur = [], [boxes[0]]
    for b in boxes[1:]:
        if abs((b[1] + b[3]) / 2 - (cur[-1][1] + cur[-1][3]) / 2) < (cur[-1][3] - cur[-1][1]) / 2:
            cur.append(b)
        else:
            rows.append(cur)
            cur = [b]
    rows.append(cur)
    ordered = [b for r in rows for b in sorted(r, key=lambda b: b[0])]
    res = {}
    for m, (x0, y0, x1, y1) in zip(MOODS, ordered):
        crop = img.crop((x0 * f, y0 * f, min(w, (x1 + 1) * f), min(h, (y1 + 1) * f)))
        res[m] = [(crop, ms[m])]
    return res

# ---------- úpravy ----------

def pixels(img):
    return img.get_flattened_data() if hasattr(img, "get_flattened_data") else img.getdata()


def has_content(img: Image.Image, bg) -> bool:
    if img.getchannel("A").getextrema()[1] == 0:
        return False
    return any(p != bg and p[3] > 0 for p in pixels(img))


def binary_alpha(img: Image.Image) -> Image.Image:
    a = img.getchannel("A").point(lambda v: 255 if v >= 128 else 0)
    img = img.copy()
    img.putalpha(a)
    return img


def normalize(moods: dict, target: int = MAX_SIDE, colors: int = 0, grid: bool = False) -> tuple[dict, dict]:
    """Stejný ořez + stejné měřítko pro všechny nálady.
    target = strana postavy v px, colors = omezit barvy při zmenšení (pixel-art vzhled)."""
    moods = {m: [(binary_alpha(remove_background(fr)), d) for fr, d in fs] for m, fs in moods.items()}
    if grid:
        moods = {m: [(snap_grid(fr), d) for fr, d in fs] for m, fs in moods.items()}
    sizes = {fr.size for fs in moods.values() for fr, _ in fs}
    if len(sizes) > 1:  # různé výřezy: stejné plátno, na střed, nohy dole
        W, H = max(z[0] for z in sizes), max(z[1] for z in sizes)
        moods = {m: [(_pad(fr, W, H), d) for fr, d in fs] for m, fs in moods.items()}
    boxes = [fr.getbbox() for fs in moods.values() for fr, _ in fs if fr.getbbox()]
    if not boxes:
        sys.exit("Všechny snímky jsou prázdné.")
    box = (min(b[0] for b in boxes), min(b[1] for b in boxes), max(b[2] for b in boxes), max(b[3] for b in boxes))
    w, h = box[2] - box[0], box[3] - box[1]
    side = max(w, h)
    info = {"ořez": f"{w}×{h} px"}
    out = {}
    for m, fs in moods.items():
        res = []
        for fr, d in fs:
            sq = Image.new("RGBA", (side, side), (0, 0, 0, 0))
            sq.paste(fr.crop(box), ((side - w) // 2, side - h))
            if side > target:
                sq = binary_alpha(sq.resize((target, target), Image.LANCZOS))
                if colors:
                    a = sq.getchannel("A")
                    sq = sq.convert("RGB").quantize(colors=colors, method=Image.MEDIANCUT).convert("RGBA")
                    sq.putalpha(a)
                info["zmenšení"] = f"na {target} px" + (f", {colors} barev" if colors else "")
            k = MAX_SIDE // sq.width
            if k > 1:
                sq = sq.resize((sq.width * k, sq.height * k), Image.NEAREST)
                info["zvětšení"] = f"×{k}"
            res.append((sq, max(60, int(d))))
        out[m] = res
    info["výsledek"] = f"{out[next(iter(out))][0][0].width} px"
    return out, info



def snap_grid(img: Image.Image) -> Image.Image:
    """Screenshot zvětšeného pixel-artu (každý pixel = blok k×k) zmenší zpět na 1 blok = 1 pixel.
    Velikost bloku odhadne z délek úseků stejné barvy, vzorkuje střed každého bloku."""
    from collections import Counter
    box = img.getbbox()
    if not box:
        return img
    runs = Counter()
    for y in range(box[1], box[3], 3):
        prev, n = None, 0
        for x in range(box[0], box[2]):
            p = img.getpixel((x, y))
            if p == prev:
                n += 1
            else:
                if prev is not None and prev[3] and 2 <= n <= 60:
                    runs[n] += 1
                prev, n = p, 1
    if not runs:
        return img
    base = min(r for r, c in runs.items() if c >= max(3, runs.most_common(1)[0][1] * 0.25))
    near = [(r, c) for r, c in runs.items() if base - 1 <= r <= base + 1]
    k = sum(r * c for r, c in near) / sum(c for _, c in near)
    if k < 2:
        return img
    w, h = round((box[2] - box[0]) / k), round((box[3] - box[1]) / k)
    out = Image.new("RGBA", (w, h))
    for yy in range(h):
        for xx in range(w):
            x = min(box[2] - 1, int(box[0] + (xx + 0.5) * k))
            y = min(box[3] - 1, int(box[1] + (yy + 0.5) * k))
            out.putpixel((xx, yy), img.getpixel((x, y)))
    print(f"  mřížka: blok {k:.2f} px -> {w}×{h}")
    return out


def _pad(fr: Image.Image, W: int, H: int) -> Image.Image:
    out = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    out.paste(fr, ((W - fr.width) // 2, H - fr.height))
    return out


def save_gif(frames: list, path: Path):
    pal = []
    for f, _ in frames:
        alpha = f.getchannel("A")
        q = f.convert("RGB").quantize(colors=255, method=Image.MEDIANCUT)
        q.paste(255, mask=alpha.point(lambda a: 255 if a == 0 else 0))
        q.info["transparency"] = 255
        pal.append(q)
    pal[0].save(path, save_all=True, append_images=pal[1:], duration=[d for _, d in frames],
                loop=0, transparency=255, disposal=2, optimize=False)


def preview(moods: dict, path: Path):
    side = moods[next(iter(moods))][0][0].width
    cols = max(len(fs) for fs in moods.values())
    sheet = Image.new("RGBA", (side * cols, side * len(moods)), (15, 20, 23, 255))
    for r, fs in enumerate(moods.values()):
        for c, (f, _) in enumerate(fs):
            sheet.alpha_composite(f, (c * side, r * side))
    sheet.save(path)


def install(moods: dict, target: Path):
    target.mkdir(exist_ok=True)
    backup = target / "_zaloha"
    if not backup.exists():
        backup.mkdir()
        for p in target.iterdir():
            if p.is_file() and p.suffix.lower() in (".gif", ".png"):
                shutil.copy2(p, backup / p.name)
    for m, fs in moods.items():
        for ext in (".gif", ".png"):
            (target / f"{m}{ext}").unlink(missing_ok=True)
        save_gif(fs, target / f"{m}.gif")


def rebuild_zip():
    tmp = KAMOS_ZIP.with_suffix(".tmp")
    with zipfile.ZipFile(tmp, "w", zipfile.ZIP_DEFLATED) as z:
        for p in sorted(KAMOS_DIR.rglob("*")):
            if p.is_file() and "_zaloha" not in p.parts and p.name not in ("data.json", "data_test.json", "stats.csv", "errors.log"):
                z.write(p, Path("HerniZamek") / p.relative_to(KAMOS_DIR))
    tmp.replace(KAMOS_ZIP)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("vstup", type=Path)
    ap.add_argument("--kamos", action="store_true")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--ms", type=int)
    ap.add_argument("--rozdel", action="store_true", help="jeden obrázek s 5 postavami vedle sebe")
    ap.add_argument("--px", type=int, default=MAX_SIDE, help="velikost postavy v px, např. 64 (appka zvětší)")
    ap.add_argument("--barev", type=int, default=0, help="omezit počet barev při zmenšení, např. 24")
    ap.add_argument("--mrizka", action="store_true", help="vstup je zvětšený screenshot pixel-artu: vrátí ho na skutečné pixely")
    ap.add_argument("--tol", type=int, default=40, help="tolerance barvy pozadí (u tmavého pozadí a černých obrysů dej menší, např. 6)")
    a = ap.parse_args()
    ms = {m: a.ms or DEFAULT_MS[m] for m in MOODS}
    src = a.vstup.resolve()
    if not src.exists():
        sys.exit(f"Nenašel jsem {src}")
    if src.is_dir():
        moods = load_folder(src, ms)
    elif a.rozdel:
        moods = load_split(src, ms)
    elif src.suffix.lower() in (".aseprite", ".ase"):
        moods = load_aseprite(src)
    else:
        moods = load_sheet(src, ms)
    moods = {m: fs for m, fs in moods.items() if fs}
    if not moods:
        sys.exit("Nenašel jsem žádnou náladu (happy, study, angry, sleepy, alert).")
    TOL_SET(a.tol)
    moods, info = normalize(moods, min(a.px, MAX_SIDE), a.barev, a.mrizka)
    missing = [m for m in MOODS if m not in moods]
    for m in MOODS:
        if m in moods:
            print(f"  {m:7} {len(moods[m])} snímků")
    print("  " + ", ".join(f"{k}: {v}" for k, v in info.items()))
    if missing:
        print(f"  Chybí: {', '.join(missing)} (zůstanou dosavadní obrázky)")
    prev = ROOT / "tools" / "kresleni" / "nahled.png"
    prev.parent.mkdir(exist_ok=True)
    preview(moods, prev)
    print(f"  Náhled: {prev}")
    if a.dry_run:
        print("Dry run, nic jsem nepřepsal.")
        return
    install(moods, IMG)
    print(f"Hotovo: {IMG}")
    if a.kamos:
        install(moods, KAMOS_DIR / "obrazky")
        rebuild_zip()
        print(f"Kámoš: {KAMOS_DIR / 'obrazky'} + přebalený {KAMOS_ZIP}")
    print("Restartuj appku, ať se obrázky načtou.")


if __name__ == "__main__":
    main()

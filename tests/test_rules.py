"""Testy pravidel. Spuštění: python tests/test_rules.py"""
import subprocess
import sys
import tempfile
import time
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if hasattr(sys.stdout, "reconfigure"):  # české výpisy i v konzoli bez UTF-8 (CI na Windows)
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.path.insert(0, str(ROOT))
from gamelock.core import (MODE_PLAY, MODE_STUDY, GameDetector, Gate, ManualClock,
                        kill_processes, load_json)

CFG = load_json(ROOT / "assets" / "config.default.json", {})
# pevná pravidla pro testy (nezávisle na tom, co si nastavíš v configu)
CFG.update({"play_per_study": 1.0, "kickstart_min": 30, "kickstart_multiplier": 2.0,
            "override_wait_min": 15, "override_bonus_min": 60, "override_max_per_day": 2,
            "override_min_reason_chars": 20})
GAME = [(1, "PUBG.exe")]
ok = 0


def check(cond, msg):
    global ok
    if not cond:
        print("FAIL:", msg)
        sys.exit(1)
    ok += 1
    print("ok  ", msg)


def new_gate(hhmm="15:00"):
    h, m = map(int, hhmm.split(":"))
    clock = ManualClock(datetime(2026, 11, 20, h, m))
    path = Path(tempfile.mkdtemp()) / "data.json"
    g = Gate(CFG, path, clock)
    return g, clock


def run(g, clock, minutes, games=()):
    evs = []
    for _ in range(int(minutes * 60 / 2)):
        clock.advance(2)
        evs += g.tick(list(games))
    return evs


# --- bez učení se hrát nedá
g, c = new_gate()
check(g.set_mode(MODE_PLAY) is not None, "Hrát bez učení je zamčené")
evs = g.tick(GAME)
check(any(e.kind == "kill" for e in evs), "spuštěná hra bez zůstatku se zavře")

# --- učení 30 min -> 30 min hraní
g.set_mode(MODE_STUDY)
evs = run(g, c, 15)
evs = run(g, c, 0.1, GAME)
check(any(e.kind == "switched" for e in evs) and g.mode == MODE_PLAY and not any(e.kind == "kill" for e in evs),
      "hra během Učení se nezavře, appka přepne na Hraní")
s0 = g.today()["study_sec"]
run(g, c, 1, GAME)
check(g.today()["study_sec"] == s0, "během hraní se učení nepočítá")
g.today()["play_sec"] = 0  # zbytek testu počítá s nehraným dnem
g.set_mode(MODE_STUDY)
g.confirm_presence()
run(g, c, 15)
g.confirm_presence()
check(abs(g.today()["study_sec"] - 30 * 60) < 30, "30 min učení se započítá")
check(g.set_mode(MODE_PLAY) is None, "po učení jde zapnout Hrát")
evs = run(g, c, 55, GAME)
check(not any(e.kind == "kill" for e in evs), "55 min hraní projde (30 min učení s rozjezdem = 60 min)")
check(any("10" in e.text for e in evs if e.kind == "warn"), "varování 10 min před koncem")
evs = run(g, c, 6, GAME)
check(any(e.kind == "warn" for e in evs), "varování 2 min před koncem")
check(any(e.kind == "deadline" for e in evs), "po vyčerpání nejdřív nabídka Dohrát zápas")
evs = run(g, c, 2, GAME)
check(any(e.kind == "kill" and e.key == "time_up" for e in evs), "bez kliknutí se hra po odpočtu zavře")

# --- po vypnutí hry se režim sám přepne z Hraju
g, c = new_gate()
g.today()["study_sec"] = 60 * 60
run(g, c, 5, GAME)
check(g.mode == MODE_PLAY, "běžící hra = režim Hraju")
run(g, c, 0.1)
check(g.mode == MODE_PLAY, "krátká mezera (restart hry) režim nezmění")
run(g, c, 0.5)
check(g.mode == "jine", "po vypnutí hry se přepne na Něco jiného")
g.set_mode(MODE_PLAY)
run(g, c, 2)
check(g.mode == MODE_PLAY, "ručně zapnuté Hraju bez hry zůstane (čeká na spuštění hry)")

# --- dohrání zápasu ve večerku
g, c = new_gate("22:30")
g.today()["study_sec"] = 3 * 3600
evs = run(g, c, 31, GAME)
check(any(e.kind == "deadline" for e in evs) and not any(e.kind == "kill" for e in evs),
      "ve 23:00 běžící hra dostane odpočet, ne hned zavření")
check(g.start_finish() == 20, "Dohrát zápas = 20 min")
NEW = [(2, "CS2.exe")]
evs = run(g, c, 1, GAME + NEW)
check(any(e.kind == "kill" and e.procs == NEW for e in evs), "nově spuštěná hra se při dohrávce zavře")
check(not any(e.kind == "kill" and (1, "PUBG.exe") in e.procs for e in evs), "rozehraná hra běží dál")
evs = run(g, c, 10)
check(any(e.kind == "finish_done" for e in evs) and g.finish_until is None, "vypnutím hry dohrávka skončí")
check(g.deadline_at is None and g.finishes_left() == 0, "dohrání jen 1x denně")
evs = run(g, c, 0.1, GAME)
check(any(e.kind == "kill" for e in evs) and not any(e.kind == "deadline" for e in evs),
      "znovu spuštěná hra ve večerku se zavře hned")

# --- dohrávka vyprší + dluh na zítřek
g, c = new_gate("16:00")
g.today()["study_sec"] = 20 * 60   # 20 min hraní (rozjezd 2x = 40 min)
g.cfg = dict(CFG, kickstart_multiplier=1.0)
run(g, c, 20, GAME)
run(g, c, 0.5, GAME)
check(g.deadline_at is not None, "došel čas -> odpočet")
g.start_finish()
evs = run(g, c, 21, GAME)
check(any(e.kind == "kill" and "dohrání" in e.text for e in evs), "po 20 min dohrávky se hra zavře")
nxt = g.data["days"]["2026-11-21"].get("adjust_sec", 0)
check(-22 * 60 < nxt < -19 * 60 and abs(g.balance_sec()) < 1, "minuty na dluh se odečtou zítra, dnešek je na nule")

# --- rozjezd: prvních 30 min za den 2x, pak 1:1
g, c = new_gate()
g.today()["study_sec"] = 30 * 60
check(abs(g.balance_sec() - 60 * 60) < 1, "30 min učení = 60 min hraní (rozjezd)")
g.today()["study_sec"] = 60 * 60
check(abs(g.balance_sec() - 90 * 60) < 1, "60 min učení = 90 min hraní")
g, c = new_gate()
for _ in range(2):  # dvakrát 30 min s přestávkou = pořád jen jeden rozjezd
    g.set_mode(MODE_STUDY)
    run(g, c, 15); g.confirm_presence(); run(g, c, 15); g.confirm_presence()
    g.set_mode("jine")
    run(g, c, 5)
check(abs(g.balance_sec() - 90 * 60) < 60, "2x30 min s pauzou = 90 min, ne 120 (rozjezd jen jednou denně)")

# --- kontrola přítomnosti
g, c = new_gate()
g.set_mode(MODE_STUDY)
evs = run(g, c, 21)
check(any(e.kind == "presence" for e in evs), "po 20 min se ptá 'jsi tu?'")
run(g, c, 10)
check(g.paused, "bez odpovědi se učení pozastaví")
s = g.today()["study_sec"]
run(g, c, 10)
check(g.today()["study_sec"] == s, "pozastavené učení se nepočítá")
g.confirm_presence()
run(g, c, 1)
check(g.today()["study_sec"] > s, "po potvrzení se zase počítá")

# --- večerka
g, c = new_gate("22:40")
g.today()["study_sec"] = 3 * 3600
check(not any(e.kind == "curfew_warn" for e in run(g, c, 1)), "ve 22:41 ještě nevaruje")
evs = run(g, c, 5)
check(any(e.kind == "curfew_warn" for e in evs), "ve 22:45 varování před večerkou")
evs = run(g, c, 21, GAME)
check(any(e.kind == "kill" and "večerka" in e.text.lower() for e in evs), "po 23:00 se hra zavře i se zůstatkem")
check(g.set_mode(MODE_PLAY) is not None, "ve večerku nejde zapnout Hrát")
check(g.request_override("potřebuju hrát s kámošema dneska") is not None, "ve večerku výjimka neplatí")
g2, c2 = new_gate("05:59")
check(g2.in_curfew(), "5:59 je večerka")
c2.advance(60)
check(not g2.in_curfew(), "6:00 už není večerka")

# --- výjimka
g, c = new_gate()
check(g.request_override("krátké") is not None, "krátký důvod neprojde")
check(g.request_override("domluvenej squad s kámošema na 20:00") is None, "dlouhý důvod projde")
run(g, c, 14)
check(g.balance_sec() <= 0 and g.confirm_override() is None, "během čekání nejde potvrdit ani hrát")
run(g, c, 1.5)
check(g.balance_sec() <= 0, "po odpočtu se výjimka nepřidá sama")
check(g.confirm_override() == 60, "po odpočtu jde potvrdit a dá 60 min")
check(g.balance_sec() >= 59 * 60, "zůstatek po potvrzení je 60 min")
g.request_override("druhá výjimka, kámoši chtěj ještě jednu hru")
g.cancel_override()
check(g.overrides_left() == 1, "zrušená výjimka se nepočítá")
g.request_override("druhá výjimka, kámoši chtěj ještě jednu hru")
run(g, c, 16)
g.confirm_override()
check(g.request_override("třetí výjimka, ještě jednou prosím fakt") is not None, "max 2 výjimky denně")

# --- výjimka se ve večerku zruší
g, c = new_gate("22:50")
g.request_override("domluvenej squad s kámošema na 22:55")
run(g, c, 16)
check(g.override_wait_left_sec() is None and g.confirm_override() is None, "večerka zruší čekající výjimku")

# --- vyrovnání a statistiky
g, c = new_gate()
g.today()["study_sec"] = 60 * 60
g.today()["adjust_sec"] = 10 * 60
check(abs(g.balance_sec() - 100 * 60) < 1, "adjust_sec se přičte k zůstatku")
g.today()["play_sec"] = 40 * 60
g.save()
import zipfile
rows = g.stats_rows()
check(["2026-11-20", 60, 0, 40, 100, 0, 60, 0.67, 0] in rows and rows[-1][0] == "CELKEM", "statistiky mají správný řádek a součet")
with zipfile.ZipFile(g.stats_path()) as z:
    sheet = z.read("xl/worksheets/sheet1.xml").decode()
check(g.stats_path().suffix == ".xlsx" and "<v>0.67</v>" in sheet and "CELKEM" in sheet, "stats.xlsx se zapsal s čísly")

# --- detekce her
# pevné hodnoty, ať testy nezávisí na tom, co je na počítači nainstalované
DET_CFG = dict(CFG, steam_auto=False,
               game_roots=[r"D:\SteamLibrary\steamapps\common", r"C:\Program Files (x86)\Steam\steamapps\common"],
               game_exe=["RobloxPlayerBeta.exe"], whitelist_exe=["RobloxStudioBeta.exe"], java_keywords=["minecraft"])
det = GameDetector.from_config(DET_CFG)
check(any("steamlibrary" in r for r in det.roots), "složka s hrami z configu se hlídá")
check(det.is_game("TslGame.exe", r"D:\SteamLibrary\steamapps\common\PUBG\TslGame\Binaries\Win64\TslGame.exe"),
      "PUBG ve Steam knihovně = hra")
check(det.is_game("RobloxPlayerBeta.exe", None), "Roblox Player = hra")
check(not det.is_game("RobloxStudioBeta.exe", None), "Roblox Studio není hra")
check(det.is_game("javaw.exe", None, lambda: ["javaw", "-cp", "net.minecraft.client.main.Main"]), "Minecraft (javaw) = hra")
check(not det.is_game("javaw.exe", None, lambda: ["javaw", "-jar", "databaze.jar"]), "jiná Java není hra")
check(not det.is_game("DayZServer_x64.exe", r"C:\Program Files (x86)\Steam\steamapps\common\DayZServer\DayZServer_x64.exe"),
      "servery nejsou hra")
check(not det.is_game("Aseprite.exe", r"D:\SteamLibrary\steamapps\common\Aseprite\Aseprite.exe"),
      "Aseprite ze Steamu není hra")
check(not det.is_game("wallpaper64.exe", r"D:\SteamLibrary\steamapps\common\wallpaper_engine\wallpaper64.exe"),
      "Wallpaper Engine není hra")
check(not det.is_game("WINWORD.EXE", r"C:\Program Files\Microsoft Office\root\Office16\WINWORD.EXE"), "Word není hra")
check(not det.is_game("PacketTracer.exe", r"C:\Program Files\Cisco Packet Tracer 8.2.2\bin\PacketTracer.exe"),
      "Packet Tracer není hra")
check(not det.is_game("code.exe", r"C:\Users\x\AppData\Local\Programs\Microsoft VS Code\Code.exe"), "VS Code není hra")


# --- jazyky: každý text je česky i anglicky, hlášky Miku mají v obou jazycích stejné klíče
import json
from gamelock import i18n
check(all(set(v) >= {"cs", "en"} for v in i18n.STR.values()), "všechny texty appky jsou CZ i EN")
personas = json.load(open(ROOT / "assets" / "personas.json", encoding="utf-8"))
lines = personas["miku"]["lines"]
check(set(lines["cs"]) == set(lines["en"]), "hlášky Miku mají CZ i EN")
check(all(set(p["lines"]["cs"]) == set(p["lines"]["en"]) == set(lines["cs"]) for p in personas.values()),
      "všichni maskoti mají všechny hlášky v CZ i EN")
check(all((ROOT / p["images"] / f"{m}.png").exists() or (ROOT / p["images"] / f"{m}.gif").exists()
          for p in personas.values() for m in ("happy", "study", "angry", "sleepy", "alert")),
      "všichni maskoti mají obrázky všech 5 nálad")
i18n.set_lang("en")
g, c = new_gate("23:30")
evs = g.tick(GAME)
check(any(e.kind == "kill" and "curfew" in e.text for e in evs), "anglicky: hra ve večerku má anglický text")
check(g.stats_rows()[0][0] == "Date", "anglicky: statistiky mají anglickou hlavičku")
i18n.set_lang("cs")

# --- macOS a Linux (logika jde testovat na každém systému)
import gamelock.core as zamek_core
check(det.is_game("Celeste", "/Users/x/Library/Application Support/Steam/steamapps/common/Celeste/Celeste.app/Contents/MacOS/Celeste")
      is False, "macOS: cesta mimo hlídané složky není hra")
mac = GameDetector.from_config(dict(DET_CFG, game_roots=["/Users/x/Library/Application Support/Steam/steamapps/common"],
                                    game_exe=["Minecraft.app"], whitelist_exe=["OBS.app"]))
check(mac.is_game("Celeste", "/Users/x/Library/Application Support/Steam/steamapps/common/Celeste/Celeste.app/Contents/MacOS/Celeste"),
      "macOS: hra ve Steam knihovně")
check(mac.is_game("launcher", "/Applications/Minecraft.app/Contents/MacOS/launcher"), "macOS: přidaná Hra.app")
check(not mac.is_game("obs", "/Applications/OBS.app/Contents/MacOS/obs"), "macOS: Program.app v Není hra")
lin = GameDetector.from_config(dict(DET_CFG, game_roots=["/home/x/.local/share/Steam/steamapps/common"]))
check(lin.is_game("hl2_linux", "/home/x/.local/share/Steam/steamapps/common/Half-Life 2/hl2_linux"), "Linux: nativní hra")
was_win = zamek_core.IS_WIN
zamek_core.IS_WIN = False
proton = lambda: [r"Z:\home\x\.local\share\Steam\steamapps\common\Celeste\Celeste.exe"]
check(lin.is_game("Celeste.exe", "/home/x/.steam/compat/proton/files/bin/wine64-preloader", proton),
      "Linux: Windows hra přes Proton (cesta v příkazové řádce)")
check(not lin.is_game("notepad.exe", "/usr/bin/wine64-preloader", lambda: [r"C:\windows\notepad.exe"]),
      "Linux: jiný Wine program není hra")
zamek_core.IS_WIN = was_win
check(lin.is_game("java", None, lambda: ["java", "-cp", "minecraft.jar"]), "Linux/macOS: Minecraft (java) = hra")

# --- mezipaměť: u známého procesu se cesta k exe nezjišťuje znovu
calls = []
class FakeProc:
    def __init__(self, pid, name): self.pid, self.info = pid, {"name": name}
    def exe(self): calls.append(self.pid); return r"C:\Windows\notepad.exe"
    def cmdline(self): return []
real_iter = zamek_core.psutil.process_iter
zamek_core.psutil.process_iter = lambda attrs=None: [FakeProc(10, "notepad.exe"), FakeProc(11, "PUBG.exe")]
cdet = GameDetector.from_config(dict(DET_CFG, game_exe=["PUBG.exe"]))
r1, r2 = cdet.find_running(), cdet.find_running()
zamek_core.psutil.process_iter = real_iter
check(r1 == r2 == [(11, "PUBG.exe")] and calls.count(10) == 1, "hledání her si pamatuje známé procesy")

# --- ukládání jen při změně
g, c = new_gate()
g.save()
t0 = g.data_path.stat().st_mtime_ns
time.sleep(0.05)
g.save()
check(g.data_path.stat().st_mtime_ns == t0, "beze změny se data znovu nezapisují")
g.today()["study_sec"] += 120
g.save()
check(g.data_path.stat().st_mtime_ns != t0, "po změně se data zapíšou")

# --- projekty: 3 min projektu = 1 min hraní, hry zavřené, kontrola přítomnosti, statistiky
from gamelock.core import MODE_PROJECT
g, c = new_gate()
g.cfg = dict(CFG, play_per_project=1 / 3)
check(g.set_mode(MODE_PROJECT) is None, "režim Projekty jde zapnout i bez času na hraní")
evs = run(g, c, 15)
g.confirm_presence()
run(g, c, 15)
check(abs(g.today()["project_sec"] - 30 * 60) < 30 and g.today()["study_sec"] == 0, "30 min projektu se počítá zvlášť")
check(abs(g.balance_sec() - 10 * 60) < 30, "30 min projektu = 10 min hraní (3:1)")
evs = run(g, c, 0.1, GAME)
check(any(e.kind == "switched" for e in evs) and g.mode == MODE_PLAY, "hra během projektu přepne na Hraní")
g4, c4 = new_gate()
g4.set_mode(MODE_STUDY)
evs = g4.tick(GAME)
check(any(e.kind == "kill" and e.key == "kill_balance" for e in evs) and g4.mode == MODE_STUDY,
      "bez vydělaného času se hra při učení zavře a učení běží dál")
g2, c2 = new_gate()
g2.set_mode(MODE_PROJECT)
check(any(e.kind == "presence" for e in run(g2, c2, 21)), "projekt: po 20 min 'jsi tu?'")
run(g2, c2, 10)
check(g2.paused, "projekt: bez odpovědi se časovač zastaví")
row = [r for r in g.stats_rows() if r[0] == "2026-11-20"][0]
check(row[1] == 0 and row[2] == 30 and row[4] == 10, "statistiky mají sloupec Projekty")
g3, c3 = new_gate()
g3.today()["study_sec"] = 30 * 60
g3.today()["project_sec"] = 30 * 60
check(abs(g3.balance_sec() - (60 + 10) * 60) < 1, "učení (rozjezd 2:1 v testu) a projekty se sčítají")

# --- oblíbení maskoti (střídají se); starý config z 1.4.x s "nahac"
from gamelock.app import favorite_keys
check(favorite_keys({"persona": "nahac"}, personas) == ["plesoun"], "starý config (persona nahac) -> Plešoun")
check(favorite_keys({"persona": "miku", "mascots": ["miku", "plesoun"]}, personas) == ["miku", "plesoun"],
      "oblíbení maskoti ze Nastavení")
check(favorite_keys({}, personas) == ["miku"], "bez nastavení je maskot Miku")
check(favorite_keys({"mascots": ["neexistuje"]}, personas) == ["miku"], "neznámý maskot se ignoruje")

# --- aktualizace: porovnání verzí
from gamelock import updater
check(updater.is_newer("v1.2.0", "1.1.0") and updater.is_newer("v1.10.0", "1.9.3"), "novější verze se pozná")
check(not updater.is_newer("v1.1.0", "1.2.0") and not updater.is_newer("v1.2.0", "1.2.0"), "stejná/starší verze není aktualizace")
check(updater.ASSET.startswith("GameLockCompanion-") and "config.json" in updater.KEEP and "data.json" in updater.KEEP,
      "aktualizace nepřepíše nastavení ani data")

# --- skutečné zavření procesu (falešná hra = náš Python s sleep, zabíjí se jen jeho PID)
import psutil
p = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])
time.sleep(0.5)
fake_name = psutil.Process(p.pid).name()
kdet = GameDetector.from_config(dict(DET_CFG, game_exe=[fake_name]))
found = []
for _ in range(20):
    found = [x for x in kdet.find_running() if x[0] == p.pid]
    if found:
        break
    time.sleep(0.25)
check(bool(found), "běžící falešná hra je nalezena")
failed = kill_processes(found)
time.sleep(1)
check(not failed and p.poll() is not None, "falešná hra je zavřená")

print(f"\nVšech {ok} testů prošlo.")

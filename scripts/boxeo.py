"""Arma data/fights.json con las carteleras de boxeo (hoy → ~3 meses).

Fuente principal (confirmada 16-oct-2026): el calendario público de ESPN
  https://www.espn.com/boxing/story/_/id/12508267/boxing-schedule
Tiene un formato fijo, así que se lee SIN IA:
  <h3>Oct. 31: Riyadh, Saudi Arabia (DAZN PPV)</h3>
  <ul><li><b>Title fight:</b> Canelo Alvarez vs. Christian Mbilli, 12 rounds, for Mbilli's WBC ... title</li> ...</ul>
La primera pelea de cada cartelera es la estelar. ESPN no da la hora: se guarda el día (timeValid=false → "Hora por confirmar").

Si ESPN cambia el formato y no sale nada, se intenta con Groq (si hay GROQ_API_KEY). Si todo falla, se queda el archivo de antes.
Solo horario y canal oficial: nada de links de streams.

Probar sin internet:  python scripts/boxeo.py --demo
"""
import json, os, re, sys, time, html
from datetime import datetime, timedelta, timezone
import requests

OUT = os.path.join(os.path.dirname(__file__), "..", "data", "fights.json")
ESPN_URL = "https://www.espn.com/boxing/story/_/id/12508267/boxing-schedule"
UA = {"User-Agent": "Mozilla/5.0 (AgendaDeportiva robot; +https://github.com)", "Accept-Language": "en-US,en;q=0.9"}
MODEL = os.environ.get("GROQ_MODEL", "llama-3.3-70b-versatile")
PR = timezone(timedelta(hours=-4))
MONTHS = {"jan": 1, "feb": 2, "mar": 3, "apr": 4, "may": 5, "jun": 6, "jul": 7, "aug": 8, "sep": 9, "oct": 10, "nov": 11, "dec": 12}
# nombres que mueven la aguja (suben la importancia). Agregar a gusto.
STARS = ["canelo", "fury", "joshua", "usyk", "inoue", "crawford", "benavidez", "davis", "garcia", "haney", "stevenson", "bivol", "beterbiev",
         "munguia", "dubois", "fundora", "navarrete", "zepeda", "teofimo", "lopez", "paul", "taylor", "serrano", "shields", "mbilli", "wardley"]
ES_WEIGHT = [("junior middleweight", "superwélter"), ("super middleweight", "supermediano"), ("light heavyweight", "semipesado"), ("heavyweight", "pesado"),
             ("cruiserweight", "crucero"), ("middleweight", "mediano"), ("junior welterweight", "superligero"), ("welterweight", "wélter"),
             ("junior lightweight", "superpluma"), ("lightweight", "ligero"), ("junior featherweight", "supergallo"), ("featherweight", "pluma"),
             ("junior bantamweight", "supermosca"), ("bantamweight", "gallo"), ("junior flyweight", "minimosca"), ("flyweight", "mosca"), ("strawweight", "mínimo")]

def clean(s):
    s = re.sub(r"(?s)<[^>]+>", " ", s)
    return html.unescape(re.sub(r"\s+", " ", s)).strip()

def weight_es(t):
    t = t.lower()
    for en, es in ES_WEIGHT:
        if en in t:
            return ("femenino " if "women" in t else "") + es
    return ""

def parse_espn(page, now):
    """HTML del calendario de ESPN → lista de carteleras."""
    i = page.find("Full schedule")
    if i < 0:
        return []
    out = []
    for card in re.split(r"<h3[^>]*>", page[i:])[1:]:
        head = clean(card.split("</h3>")[0])
        m = re.match(r"^([A-Za-z]{3})[a-z]*\.? (\d{1,2}): ([^(]+?)\s*(?:\((.+)\))?$", head)
        if not m or m.group(1).lower()[:3] not in MONTHS:
            continue
        mon, day, where, tv = MONTHS[m.group(1).lower()[:3]], int(m.group(2)), m.group(3).strip(), m.group(4) or ""
        year = now.year + (1 if mon < now.month - 2 else 0)  # "Jan." en octubre = el año que viene
        try:
            d = datetime(year, mon, day, 0, 0, tzinfo=PR)
        except ValueError:
            continue
        ul = card.split("</h3>", 1)[1].split("</ul>")[0] if "</h3>" in card else ""
        bouts = []
        for li in re.findall(r"(?s)<li[^>]*>(.*?)</li>", ul):
            t = clean(li)
            title = t.lower().startswith("title fight")
            t = re.sub(r"^title fight:\s*", "", t, flags=re.I)
            mm = re.match(r"^(.+?\bvs\.?\s+.+?),\s*(\d+) rounds?,?\s*(.*)$", t)
            if not mm:
                continue
            bouts.append({"fight": mm.group(1).strip(), "rounds": int(mm.group(2)), "title": title, "info": mm.group(3).strip()[:120]})
        if not bouts:
            continue
        main = bouts[0]
        chans = [c.strip() for c in re.split(r",| and ", tv) if c.strip()]
        belts = sorted(set(re.findall(r"\b(WBC|WBA|WBO|IBF|IBO|The Ring)\b", main["info"])))
        low = (main["fight"] + " " + " ".join(b["fight"] for b in bouts[:3])).lower()
        imp = 45 + (18 if main["title"] else 0) + 6 * min(3, sum(b["title"] for b in bouts)) + (10 if "ppv" in tv.lower() else 0)
        imp += 22 if any(s in main["fight"].lower() for s in STARS) else 8 if any(s in low for s in STARS) else 0
        imp += 6 if "undisputed" in main["info"].lower() or len(belts) >= 2 else 0
        out.append({"date": d.isoformat(), "timeValid": False, "title": main["fight"], "where": where, "channels": chans, "channels_latam": [],
                    "weight": weight_es(main["info"]), "belts": belts, "titleFight": main["title"], "importance": min(100, imp),
                    "card": [{"fight": b["fight"], "title": b["title"], "rounds": b["rounds"], "weight": weight_es(b["info"])} for b in bouts[:14]],
                    "source": "ESPN"})
    return out

GROQ_PROMPT = """You extract boxing fight cards from a schedule web page. Return ONLY JSON: {"fights":[...]}.
Each fight: {"date": "YYYY-MM-DD", "title": "Fighter A vs. Fighter B" (main event), "where": "City", "channels": [US TV/streaming exactly as written],
"weight": "", "belts": ["WBC", ...], "importance": 0-100}. Only fights from {today} to {until}. Do not invent channels. Max 30.
PAGE TEXT:
{text}"""

def ask_groq(text, today, until):
    key = os.environ.get("GROQ_API_KEY")
    if not key:
        raise RuntimeError("falta GROQ_API_KEY")
    body = {"model": MODEL, "temperature": 0, "response_format": {"type": "json_object"},
            "messages": [{"role": "user", "content": GROQ_PROMPT.replace("{today}", today).replace("{until}", until).replace("{text}", text[:20000])}]}
    for i in range(3):
        r = requests.post("https://api.groq.com/openai/v1/chat/completions", json=body, headers={"Authorization": "Bearer " + key}, timeout=90)
        if r.status_code == 429:
            time.sleep(20 * (i + 1)); continue
        r.raise_for_status()
        fs = json.loads(r.json()["choices"][0]["message"]["content"]).get("fights", [])
        for f in fs:
            f["timeValid"] = False; f["source"] = "ESPN+IA"
            if re.match(r"^\d{4}-\d{2}-\d{2}$", str(f.get("date", ""))):
                f["date"] += "T00:00:00-04:00"
        return fs
    raise RuntimeError("Groq 429 (límite) varias veces")

def valid(f, now):
    try:
        d = datetime.fromisoformat(str(f.get("date", "")).replace("Z", "+00:00"))
        if d.tzinfo is None:
            d = d.replace(tzinfo=PR)
    except Exception:
        return None
    title = str(f.get("title", "")).strip()
    if not re.search(r"\bvs?\.?\s", title, re.I) or len(title) > 90 or re.search(r"\bTBA\b", title.split("vs")[0]):
        return None
    if not (now - timedelta(days=2) <= d <= now + timedelta(days=100)):
        return None
    lst = lambda xs: [str(x).strip()[:30] for x in (xs or []) if str(x).strip()][:6]
    return {**f, "date": d.isoformat(), "title": title, "where": str(f.get("where", ""))[:80], "channels": lst(f.get("channels")),
            "channels_latam": lst(f.get("channels_latam")), "belts": lst(f.get("belts")), "importance": max(0, min(100, int(f.get("importance") or 50)))}

def norm(s):
    return re.sub(r"[^a-z]", "", str(s).lower())

DEMO_HTML = """<h2>Full schedule:</h2><h2>October</h2><h3></h3>
<h3>Oct. 31: Riyadh, Saudi Arabia (DAZN PPV)</h3><ul><li><p><b>Title fight:</b> Canelo Alvarez vs. Christian Mbilli, 12 rounds, for Mbilli's WBC super middleweight title</li><li><p><b>Title fight:</b> Jaime Munguia vs. Nikita Zon, 12 rounds, for Munguia's WBA super middleweight title</li><li><p>David Picasso vs. Carl Jammes Martin, 12 rounds, junior featherweights</li></ul>
<h3>Oct. 24: Sheffield, England (DAZN)</h3><ul><li><p><b>Title fight:</b> Dalton Smith vs. Alberto Puello, 12 rounds, for Smith's WBC junior welterweight title</li><li><p>Leo Atang vs. TBA, 6 rounds, heavyweights</li></ul>
<h3>Nov. 14: Levallois-Perret, France</h3><ul><li><p>Nadir Benamar vs. Rival Uno, 10 rounds, welterweights</li></ul>
<h3>Jan. 23: Horsens, Denmark</h3><ul><li><p>Dane Uno vs. Dane Dos, 10 rounds, middleweights</li></ul>
<h3>Esto no es fecha</h3><ul><li><p>Nada vs. Nadie, 4 rounds</li></ul>"""

def main(demo=False):
    now = datetime(2026, 10, 16, 12, tzinfo=PR) if demo else datetime.now(timezone.utc)
    old = []
    if os.path.exists(OUT) and not demo:
        try:
            old = json.load(open(OUT)).get("fights", [])
        except Exception:
            old = []
    status, found = {}, []
    page = ""
    try:
        page = DEMO_HTML if demo else requests.get(ESPN_URL, headers=UA, timeout=30).text
        raw = parse_espn(page, now)
        ok = [v for v in (valid(f, now) for f in raw) if v]
        status["espn"] = {"ok": bool(ok), "count": len(ok), "at": now.isoformat(timespec="seconds")}
        found += ok
    except Exception as e:
        status["espn"] = {"ok": False, "error": str(e)[:200], "at": now.isoformat(timespec="seconds")}
    if not found and page and not demo and os.environ.get("GROQ_API_KEY"):
        try:  # ESPN cambió el formato: que la IA lo lea
            raw = ask_groq(clean(re.sub(r"(?is)<(script|style)[^>]*>.*?</\1>", " ", page)), now.date().isoformat(), (now + timedelta(days=90)).date().isoformat())
            ok = [v for v in (valid(f, now) for f in raw) if v]
            status["groq"] = {"ok": bool(ok), "count": len(ok)}
            found += ok
        except Exception as e:
            status["groq"] = {"ok": False, "error": str(e)[:200]}
    best = {}
    for f in found + [x for x in old if x.get("date", "") >= (now - timedelta(days=2)).isoformat()]:
        k = (f["date"][:10], "".join(sorted(norm(p)[-6:] for p in re.split(r"\bvs?\.?\s", f["title"], flags=re.I))))
        if k not in best or (f in found and best[k] not in found):
            best[k] = f
    fights = sorted(best.values(), key=lambda f: f["date"])
    if not found and old:
        print("ESPN falló: se deja el archivo de antes", status)
        fights = old
    res = {"updated": now.isoformat(timespec="seconds"), "sources": status, "fights": fights}
    if demo:
        return res
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    json.dump(res, open(OUT, "w"), ensure_ascii=False, indent=1)
    print(len(fights), "carteleras", status)

if __name__ == "__main__":
    if "--demo" in sys.argv:
        r = main(demo=True)
        t = [f["title"] for f in r["fights"]]
        checks = [("Canelo sale con DAZN PPV y es lo más importante", any("Canelo" in f["title"] and "DAZN PPV" in f["channels"] and f["importance"] >= 90 for f in r["fights"])),
                  ("cartelera completa con título", any(len(f["card"]) == 3 and f["card"][1]["title"] for f in r["fights"] if "Canelo" in f["title"])),
                  ("peso en español", any(f["weight"] == "supermediano" for f in r["fights"])),
                  ("sin canal → lista vacía (no se inventa)", any(f["channels"] == [] for f in r["fights"] if "Benamar" in f["title"])),
                  ("enero = año que viene", any(f["date"].startswith("2027-01-23") for f in r["fights"])),
                  ("'Esto no es fecha' se descarta", not any("Nada" in x for x in t)),
                  ("hora por confirmar", all(f["timeValid"] is False for f in r["fights"]))]
        for n, c in checks:
            print(("OK " if c else "FAIL ") + n)
        print("RESULT", sum(c for _, c in checks), "/", len(checks))
    else:
        main()

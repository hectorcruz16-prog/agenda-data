"""Arma data/rights.json: quién transmite cada liga EN CADA PAÍS (TV y streaming oficiales).

Fuente: Wikipedia (API oficial y gratis, licencia CC BY-SA). Cada liga tiene una página o sección
"broadcasters" con tablas País | Canal. Groq (gratis) pasa esa tabla desordenada a JSON limpio.
Cada país se valida contra una lista fija (no se aceptan países inventados) y cada liga
guarda su estado. Si una liga falla o sale con muy pocos países, se queda lo de antes (nunca se pisa con vacío).

Ojo: esto es "quién tiene los derechos de la liga en ese país". El canal exacto de cada partido
(cuando ESPN lo da) lo pone la app aparte. Corre 1 vez por semana: los derechos cambian por temporada.

Probar sin internet:  python scripts/canales.py --demo
"""
import json, os, re, sys, time, html
from datetime import datetime, timezone
import requests

OUT = os.path.join(os.path.dirname(__file__), "..", "data", "rights.json")
WIKI = "https://en.wikipedia.org/w/api.php"
# Wikimedia pide un User-Agent con forma de contacto; sin eso y con prisa da 429 (1ra corrida, 16-oct)
UA = {"User-Agent": "AgendaDeportiva/1.0 (https://github.com/hectorcruz16-prog/agenda-data; robot semanal de derechos de TV) python-requests"}
MODEL = os.environ.get("GROQ_MODEL", "llama-3.3-70b-versatile")
FALLBACK_MODEL = "llama-3.1-8b-instant"

# id = el slug de ESPN que usa la app. page = página de Wikipedia; sec = regex de las secciones a leer (None = toda la página).
# Páginas confirmadas el 16-oct-2026.
LEAGUES = [
    {"id": "esp.1", "name": "LaLiga", "page": "List of La Liga broadcasters", "sec": None},
    {"id": "eng.1", "name": "Premier League", "page": "List of Premier League overseas broadcasters", "sec": r"^(african|asian|european|latin american|middle eastern|north american|oceania)", "max": 8},
    {"id": "uefa.champions", "name": "UEFA Champions League", "page": "List of UEFA Champions League broadcasters", "sec": None},
    {"id": "ita.1", "name": "Serie A", "page": "List of Serie A broadcasters", "sec": None},
    {"id": "ger.1", "name": "Bundesliga", "page": "Bundesliga", "sec": r"media|broadcast"},
    {"id": "fra.1", "name": "Ligue 1", "page": "Ligue 1", "sec": r"media|broadcast|television"},
    {"id": "mex.1", "name": "Liga MX", "page": "Liga MX", "sec": r"broadcast|media"},
    {"id": "usa.1", "name": "MLS", "page": "List of current Major League Soccer broadcasters", "sec": None},
    {"id": "conmebol.libertadores", "name": "Copa Libertadores", "page": "Copa Libertadores", "sec": r"broadcast|media"},
    {"id": "f1", "name": "Fórmula 1", "page": "List of Formula One broadcasters", "sec": None},
    {"id": "mlb", "name": "MLB", "page": "Major League Baseball on television", "sec": r"^(united states|canada|united kingdom)$"},
    {"id": "nhl", "name": "NHL", "page": "NHL on television", "sec": r"^(canada|united states|international)$"},
    # NBA y NFL: Wikipedia no tiene tabla internacional (oct-2026). Se intentan; si salen < 3 países se marcan como fallidas y no se pisan.
    {"id": "nba", "name": "NBA", "page": "NBA on television", "sec": r"regular season|canadian"},
    {"id": "nfl", "name": "NFL", "page": "National Football League", "sec": r"media"},
    {"id": "ufc", "name": "UFC", "page": "Ultimate Fighting Championship", "sec": r"international broadcasters|^television$"},
]

# Países y regiones aceptados (nombre en inglés → código). Lo que no esté aquí se descarta.
COUNTRIES = dict(x.split(":") for x in """United States:US,USA:US,Puerto Rico:PR,Mexico:MX,Canada:CA,Spain:ES,United Kingdom:GB,UK:GB,England:GB,Ireland:IE,Argentina:AR,Brazil:BR,Chile:CL,
Colombia:CO,Peru:PE,Ecuador:EC,Venezuela:VE,Uruguay:UY,Paraguay:PY,Bolivia:BO,Dominican Republic:DO,Cuba:CU,Costa Rica:CR,Panama:PA,Guatemala:GT,
Honduras:HN,El Salvador:SV,Nicaragua:NI,Jamaica:JM,Trinidad and Tobago:TT,Haiti:HT,Bahamas:BS,Barbados:BB,France:FR,Germany:DE,Italy:IT,Portugal:PT,
Netherlands:NL,Belgium:BE,Switzerland:CH,Austria:AT,Poland:PL,Czech Republic:CZ,Czechia:CZ,Slovakia:SK,Hungary:HU,Romania:RO,Bulgaria:BG,Greece:GR,
Turkey:TR,Türkiye:TR,Russia:RU,Ukraine:UA,Belarus:BY,Croatia:HR,Serbia:RS,Slovenia:SI,Bosnia and Herzegovina:BA,Montenegro:ME,North Macedonia:MK,
Albania:AL,Kosovo:XK,Denmark:DK,Norway:NO,Sweden:SE,Finland:FI,Iceland:IS,Estonia:EE,Latvia:LV,Lithuania:LT,Cyprus:CY,Malta:MT,Luxembourg:LU,
Andorra:AD,Monaco:MC,San Marino:SM,Georgia:GE,Armenia:AM,Azerbaijan:AZ,Kazakhstan:KZ,Uzbekistan:UZ,Israel:IL,Iran:IR,Iraq:IQ,Saudi Arabia:SA,
United Arab Emirates:AE,Qatar:QA,Kuwait:KW,Bahrain:BH,Oman:OM,Jordan:JO,Lebanon:LB,Palestine:PS,Egypt:EG,Morocco:MA,Algeria:DZ,Tunisia:TN,Libya:LY,
Nigeria:NG,Ghana:GH,Kenya:KE,South Africa:ZA,Angola:AO,Mozambique:MZ,Cameroon:CM,Ivory Coast:CI,Senegal:SN,Ethiopia:ET,Tanzania:TZ,Uganda:UG,
India:IN,Pakistan:PK,Bangladesh:BD,Sri Lanka:LK,Nepal:NP,Afghanistan:AF,China:CN,Hong Kong:HK,Macau:MO,Taiwan:TW,Japan:JP,South Korea:KR,Korea:KR,
North Korea:KP,Mongolia:MN,Vietnam:VN,Thailand:TH,Cambodia:KH,Laos:LA,Myanmar:MM,Malaysia:MY,Singapore:SG,Indonesia:ID,Philippines:PH,Brunei:BN,
Australia:AU,New Zealand:NZ,Fiji:FJ,Papua New Guinea:PG""".replace("\n", "").split(","))
REGIONS = {"latin america": "LATAM", "south america": "LATAM", "central america": "CAMER", "caribbean": "CARIB", "worldwide": "WW", "international": "WW",
           "global": "WW", "middle east and north africa": "MENA", "mena": "MENA", "middle east": "MENA", "sub-saharan africa": "AFR", "africa": "AFR",
           "europe": "EU", "balkans": "BALK", "nordic countries": "NORD", "scandinavia": "NORD", "southeast asia": "SEA", "asia": "ASIA",
           "pacific islands": "PAC", "oceania": "PAC", "central asia": "CASIA", "commonwealth of independent states": "CIS", "baltic states": "BALT"}
LOWER = {k.lower(): v for k, v in COUNTRIES.items()}

def code_of(t):
    t = re.sub(r"\[.*?\]|\(.*?\)", "", str(t)).strip().strip(".").lower()
    return LOWER.get(t) or REGIONS.get(t)

def wiki(params):
    """Una llamada a la vez, con pausa, y si Wikipedia dice 429 (muy rápido) se espera y se reintenta."""
    for i in range(5):
        time.sleep(1.5)
        r = requests.get(WIKI, params={**params, "format": "json", "formatversion": 2, "redirects": 1, "maxlag": 5}, headers=UA, timeout=30)
        if r.status_code == 429 or "maxlag" in r.text[:200]:
            time.sleep(int(r.headers.get("Retry-After", "0") or 0) or 10 * (i + 1)); continue
        r.raise_for_status()
        return r.json()
    raise RuntimeError("Wikipedia 429 (muy rápido) varias veces")

def html_to_text(h):
    """HTML de Wikipedia → texto compacto: tablas como filas 'celda | celda', listas con '- '."""
    h = re.sub(r"\s+", " ", h)  # los saltos de línea del HTML no cuentan; las filas se arman con </tr>
    h = re.sub(r"(?is)<(style|script|sup)[^>]*>.*?</\1>", " ", h)
    h = re.sub(r"(?is)<span class=\"mw-editsection.*?</span>\s*</span>", " ", h)
    h = re.sub(r"(?i)<br\s*/?>|</li>|</p>|</div>", " ; ", h)
    h = re.sub(r"(?i)</t[dh]>", " | ", h)
    h = re.sub(r"(?i)</tr>", "\n", h)
    h = re.sub(r"(?i)<li[^>]*>", "\n- ", h)
    h = re.sub(r"(?i)<h[2-4][^>]*>", "\n## ", h)
    h = re.sub(r"(?s)<[^>]+>", " ", h)
    h = html.unescape(h)
    h = re.sub(r"\[\d+\]|\[[a-z]\]", "", h)
    h = re.sub(r"[ \t]+", " ", h)
    h = re.sub(r"(\s*;\s*)+", "; ", h)
    lines = [l.strip(" ;|") for l in h.split("\n")]
    return "\n".join(l for l in lines if len(l) > 1)

def league_text(L):
    if not L["sec"]:
        return html_to_text(wiki({"action": "parse", "page": L["page"], "prop": "text|revid"})["parse"]["text"]), None
    secs = wiki({"action": "parse", "page": L["page"], "prop": "sections"})["parse"]["sections"]
    pick, seen = [], set()
    for s in secs:  # la primera sección con cada nombre (la temporada actual va primero)
        if re.search(L["sec"], s["line"], re.I) and s["line"].lower() not in seen:
            seen.add(s["line"].lower()); pick.append(s)
    pick = pick[:L.get("max", 4)]
    if not pick:
        raise RuntimeError("no encontré la sección en Wikipedia")
    parts = []
    for s in pick:
        parts.append(html_to_text(wiki({"action": "parse", "page": L["page"], "section": s["index"], "prop": "text"})["parse"]["text"]))
    return "\n".join(parts), [s["line"] for s in pick]

def chunks(text, size=14000, most=3):
    """Páginas largas (F1, Serie A, Champions): primero se quitan las líneas sin país; después se parte en pedazos."""
    if len(text) > size:
        names = [k for k in LOWER] + list(REGIONS)
        keep = [l for l in text.split("\n") if l.startswith("##") or any(n in l[:160].lower() for n in names)]
        text = "\n".join(keep)
    out = []
    while text and len(out) < most:
        cut = text.rfind("\n", 0, size) if len(text) > size else len(text)
        cut = cut if cut > 0 else size
        out.append(text[:cut]); text = text[cut:]
    return out

PROMPT = """From this Wikipedia text about {name} broadcasting, list the CURRENT (2026 / 2026-27 season) TV and streaming rights holders per country or territory.
Return ONLY JSON: {"rights":[{"territory":"Mexico","channels":["TUDN","ViX"]}, ...]}
Rules:
- One entry per country. If a row lists several countries, repeat it for each country.
- Use English country names (e.g. "United States", "United Kingdom", "Dominican Republic"). Regions allowed only as "Latin America", "Caribbean", "Central America", "Middle East and North Africa", "Sub-Saharan Africa", "Worldwide".
- Channels: short brand names exactly as written (e.g. "ESPN", "Sky Sports", "DAZN", "Paramount+"). No URLs, no commentators, no radio.
- Skip past seasons and anything not in the text. Do not invent. Max 160 entries.
TEXT:
{text}"""

def ask_groq(name, text):
    key = os.environ.get("GROQ_API_KEY")
    if not key:
        raise RuntimeError("falta GROQ_API_KEY")
    for model in (MODEL, FALLBACK_MODEL):
        body = {"model": model, "temperature": 0, "response_format": {"type": "json_object"},
                "messages": [{"role": "user", "content": PROMPT.replace("{name}", name).replace("{text}", text[:15000])}]}
        for i in range(3):
            r = requests.post("https://api.groq.com/openai/v1/chat/completions", json=body, headers={"Authorization": "Bearer " + key}, timeout=120)
            if r.status_code == 429:
                time.sleep(25 * (i + 1)); continue
            if r.status_code >= 400:
                break
            return json.loads(r.json()["choices"][0]["message"]["content"]).get("rights", [])
    raise RuntimeError("Groq no respondió (límite o error)")

def validate(rows, text):
    """Solo países conocidos; cada canal tiene que aparecer en el texto de Wikipedia (así la IA no inventa)."""
    low = text.lower()
    out, bad = {}, 0
    for r in rows if isinstance(rows, list) else []:
        c = code_of(r.get("territory", ""))
        chans = []
        for ch in r.get("channels") or []:
            ch = re.sub(r"\[.*?\]", "", str(ch)).strip()[:40]
            if len(ch) >= 2 and ch.lower() in low and ch not in chans:
                chans.append(ch)
        if not c or not chans:
            bad += 1; continue
        out.setdefault(c, [])
        out[c] += [x for x in chans if x not in out[c]]
        out[c] = out[c][:6]
    return out, bad

DEMO_TEXT = """## Americas
Country | Broadcasters
Argentina Bolivia Chile Colombia Ecuador Peru Uruguay Venezuela | ESPN; DSports
Costa Rica El Salvador Guatemala Honduras Nicaragua Panama | Sky Sports; Vix+ en Tigo
Brazil | CazéTV; SportyNet
United States | ESPN; ESPN+
Mexico | Sky Sports"""
DEMO_GROQ = [{"territory": "Argentina", "channels": ["ESPN", "DSports"]}, {"territory": "Colombia", "channels": ["ESPN", "DSports"]},
             {"territory": "Costa Rica", "channels": ["Sky Sports", "Vix+ en Tigo"]}, {"territory": "United States", "channels": ["ESPN+", "ESPN"]},
             {"territory": "Mexico", "channels": ["Sky Sports", "Canal Inventado"]}, {"territory": "Narnia", "channels": ["ESPN"]},
             {"territory": "Brazil", "channels": ["CazéTV"]}]

def main(demo=False):
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    old = {}
    if os.path.exists(OUT) and not demo:
        try:
            old = json.load(open(OUT)).get("leagues", {})
        except Exception:
            old = {}
    leagues, status = dict(old), {}
    for i, L in enumerate(LEAGUES if not demo else LEAGUES[:1]):
        try:
            if demo:
                text, secs, rows = DEMO_TEXT, None, DEMO_GROQ
            else:
                text, secs = league_text(L)
                if len(text) < 200:
                    raise RuntimeError("Wikipedia devolvió muy poco texto")
                rows = []
                for part in chunks(text):
                    rows += ask_groq(L["name"], part)
                    time.sleep(6)
            got, bad = validate(rows, text)
            prev = len((old.get(L["id"]) or {}).get("countries", {}))
            if len(got) < 3 or (prev and len(got) < prev * 0.4):
                raise RuntimeError(f"solo {len(got)} países (antes {prev}): se queda lo de antes")
            leagues[L["id"]] = {"name": L["name"], "source": "https://en.wikipedia.org/wiki/" + L["page"].replace(" ", "_"), "sections": secs,
                                "checked": now, "countries": dict(sorted(got.items()))}
            status[L["id"]] = {"ok": True, "countries": len(got), "skipped": bad}
        except Exception as e:
            status[L["id"]] = {"ok": False, "error": str(e)[:200]}
        if not demo and i < len(LEAGUES) - 1:
            time.sleep(12)  # el plan gratis de Groq tiene límite por minuto
    res = {"updated": now, "status": status, "leagues": leagues}
    if demo:
        return res
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    json.dump(res, open(OUT, "w"), ensure_ascii=False, indent=1)
    print(json.dumps(status, ensure_ascii=False, indent=1))

if __name__ == "__main__":
    if "--demo" in sys.argv:
        r = main(demo=True)
        c = r["leagues"]["esp.1"]["countries"]
        checks = [("país conocido entra", c.get("AR") == ["ESPN", "DSports"]),
                  ("canal que no está en Wikipedia se descarta", c.get("MX") == ["Sky Sports"]),
                  ("país inventado se descarta", "Narnia" not in json.dumps(c) and r["status"]["esp.1"]["skipped"] == 1),
                  ("EE.UU. con sus canales", set(c.get("US", [])) == {"ESPN", "ESPN+"}),
                  ("acentos se respetan", c.get("BR") == ["CazéTV"]),
                  ("html_to_text arma filas", "Mexico | Sky Sports" in html_to_text("<table><tr>\n<td>Mexico\n</td>\n<td>Sky Sports<sup>[3]</sup>\n</td></tr></table>")),
                  ("página larga: se quitan líneas sin país y se parte", len(chunks("## A\n" + "historia sin datos\n" * 3000 + "Mexico | TUDN\n" * 1500)) == 2)]
        for n, ok in checks:
            print(("OK " if ok else "FAIL ") + n)
        print("RESULT", sum(ok for _, ok in checks), "/", len(checks))
    else:
        main()

# Robot de boxeo boricua: lee las secciones de boxeo de El Nuevo Día y Primera Hora, y con Groq saca las CARTELERAS
# que se van a pelear en Puerto Rico (fecha, lugar, peleas, canal). Guarda data/pr_fights.json en el mismo formato que
# fights.json (el de ESPN) y la app los junta. Solo se guardan datos (fecha, lugar, nombres) + el enlace a la nota original.
#   python scripts/boxeo_pr.py          → corrida normal (necesita GROQ_API_KEY)
#   python scripts/boxeo_pr.py --demo   → pruebas sin internet
import json, os, re, sys, html
from datetime import datetime, timedelta, timezone
sys.path.insert(0, os.path.dirname(__file__))
from boxeo import groq_json, UA, PR, weight_es  # mismas piezas que el robot de ESPN

try:
    import requests
except Exception:  # en --demo no hace falta
    requests = None

ROOT = os.path.join(os.path.dirname(__file__), "..", "data")
OUT, SEEN = os.path.join(ROOT, "pr_fights.json"), os.path.join(ROOT, "pr_seen.json")
SOURCES = [("El Nuevo Día", "https://www.elnuevodia.com", "/deportes/boxeo/"),
           ("Primera Hora", "https://www.primerahora.com", "/deportes/boxeo/")]
MAX_NEW = 18  # notas nuevas por corrida (las ya leídas se recuerdan en pr_seen.json)

PROMPT = """Eres un asistente que extrae CARTELERAS DE BOXEO PROFESIONAL que se van a pelear EN PUERTO RICO, a partir de notas de periódico.
Hoy es {today}. Devuelve SOLO JSON: {{"cards":[...]}}. Cada cartelera:
{{"date":"YYYY-MM-DD","time":"HH:MM" o null (hora de Puerto Rico, 24h, solo si la nota la dice),"venue":"lugar exacto como sale en la nota",
"city":"pueblo","promoter":"promotora o evento (ej. Es la Pelea)" o null,"broadcast":["canal o plataforma que diga la nota"],
"fights":[{{"a":"nombre completo","b":"nombre completo o null si la nota no dice el rival","rounds":número o null,"weight":"división o null","title":true/false}}],
"url":"url de la nota de donde sale"}}
Reglas: solo eventos FUTUROS o de hoy, solo en Puerto Rico. Copia los nombres y el lugar tal como salen en el texto. NO inventes nada: si un dato no está, usa null.
Si una nota no anuncia una cartelera en Puerto Rico, no devuelvas nada de ella. La pelea más importante va primero.

NOTAS:
{notes}"""

def strip(h):
    return re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", h or ""))).strip()

def norm(s):
    s = (s or "").lower()
    for a, b in zip("áéíóúüñ", "aeiouun"):
        s = s.replace(a, b)
    return re.sub(r"[^a-z0-9 ]+", " ", s).strip()

def get(url):
    r = requests.get(url, headers=UA, timeout=30)
    r.raise_for_status()
    return r.text

def note_links(base, path, page):
    out = []
    for m in re.finditer(r'href="((?:%s)?(%snotas/[^"#?]+/))"' % (re.escape(base), re.escape(path)), page):
        u = base + m.group(2)
        if u not in out:
            out.append(u)
    return out[:40]

def read_note(url, page):
    meta = lambda p: (re.search(r'<meta[^>]+(?:property|name)="%s"[^>]+content="([^"]*)"' % p, page) or [None, ""])[1]
    paras = [strip(p) for p in re.findall(r"<p[^>]*>(.*?)</p>", page, re.S)]
    body = " ".join(p for p in paras if len(p) > 40)[:3500]
    return {"url": url, "title": strip(meta("og:title")), "desc": strip(meta("og:description")), "published": meta("article:published_time"), "text": body}

def valid(c, notes, today):
    """Anti-invento: la fecha tiene que ser futura (≤ 6 meses), y el lugar y los peleadores tienen que salir en el texto de la nota."""
    try:
        d = datetime.strptime(c.get("date") or "", "%Y-%m-%d").date()
    except Exception:
        return None
    if d < today - timedelta(days=1) or d > today + timedelta(days=183):
        return None
    src = next((n for n in notes if n["url"] == c.get("url")), None) or (notes[0] if len(notes) == 1 else None)
    hay = norm(" ".join([src["title"], src["desc"], src["text"]])) if src else norm(" ".join(n["title"] + " " + n["desc"] + " " + n["text"] for n in notes))
    fights = []
    for f in c.get("fights") or []:
        a, b = (f.get("a") or "").strip(), (f.get("b") or "").strip()
        if not a or norm(a).split()[-1] not in hay:
            continue
        if b and norm(b).split()[-1] not in hay:
            b = ""
        fights.append({"a": a, "b": b, "rounds": f.get("rounds") if isinstance(f.get("rounds"), int) else None,
                       "weight": f.get("weight") or "", "title": bool(f.get("title"))})
    venue = (c.get("venue") or "").strip()
    if not fights or (venue and norm(venue).split()[-1] not in hay):
        return None
    return {**c, "fights": fights, "url": src["url"] if src else c.get("url"), "source": next((s for s, b, p in SOURCES if src and src["url"].startswith(b)), "Prensa de PR")}

def to_app(c):
    """Mismo formato que fights.json (el que ya lee la app)."""
    t = c.get("time") if re.match(r"^\d{1,2}:\d{2}$", c.get("time") or "") else None
    date = f"{c['date']}T{t if t else '00:00'}:00-04:00"
    fs = c["fights"]
    vs = lambda f: f"{f['a']} vs. {f['b']}" if f["b"] else f"{f['a']} (rival por anunciar)"
    where = ", ".join(x for x in [c.get("venue"), c.get("city"), "Puerto Rico"] if x)
    w = fs[0].get("weight") or ""
    return {"date": date, "timeValid": bool(t), "title": vs(fs[0]), "where": where, "channels": [x for x in (c.get("broadcast") or []) if x],
            "weight": weight_es(w) or w, "belts": [], "titleFight": bool(fs[0].get("title")), "importance": 62 + (8 if fs[0].get("title") else 0),
            "card": [{"fight": vs(f), "title": f["title"], "rounds": f["rounds"], "weight": weight_es(f.get("weight") or "") or (f.get("weight") or "")} for f in fs[:14]],
            "promoter": c.get("promoter") or "", "source": c.get("source", "Prensa de PR"), "url": c.get("url", ""), "pr": True}

def fkey(fight):
    """Pelea → clave por el primer peleador (nombre + primer apellido): "Juanmita López de Jesús (rival…)" = "Juanmita López vs. …"."""
    a = norm(re.split(r"\s+vs\.?\s+|\(", fight)[0]).split()
    return " ".join(a[:2])

def ckey(f):
    """Cartelera → fecha + pueblo ("Coliseo Roger Mendoza, Caguas" y "Cancha Roger Mendoza, Caguas" son la misma)."""
    parts = [x.strip() for x in f["where"].split(",")]
    town = parts[-2] if len(parts) >= 3 else parts[0]
    return f["date"][:10] + "|" + norm(town)

def merge(old, new):
    """Una cartelera por fecha+pueblo. Si llega otra nota de la misma, se juntan las peleas; una pelea con rival le gana a la misma "(rival por anunciar)"."""
    out = {}
    for f in list(old) + list(new):
        k = ckey(f)
        if k not in out:
            out[k] = {**f, "card": list(f["card"])}
            continue
        cur = out[k]
        for c in f["card"]:
            i = next((j for j, x in enumerate(cur["card"]) if fkey(x["fight"]) == fkey(c["fight"])), None)
            if i is None:
                cur["card"].append(c)
            elif "(rival" in cur["card"][i]["fight"] and "(rival" not in c["fight"]:
                cur["card"][i] = {**cur["card"][i], **{k2: v for k2, v in c.items() if v not in (None, "")}}
        for k2 in ("channels",):
            cur[k2] = list(dict.fromkeys((cur.get(k2) or []) + (f.get(k2) or [])))
        if f.get("timeValid") and not cur.get("timeValid"):
            cur["date"], cur["timeValid"] = f["date"], True
        if f["where"].lower().startswith("coliseo") and not cur["where"].lower().startswith("coliseo"):
            cur["where"] = f["where"]
    for f in out.values():  # la estelar: la primera pelea (con rival si ya se sabe)
        f["title"] = f["card"][0]["fight"]
    return sorted(out.values(), key=lambda f: f["date"])

def main(demo=False):
    today = datetime.now(PR).date()
    seen = {} if demo else (json.load(open(SEEN)) if os.path.exists(SEEN) else {})
    old = [] if demo else (json.load(open(OUT)).get("fights", []) if os.path.exists(OUT) else [])
    notes, status = [], {}
    for name, base, path in ([] if demo else SOURCES):
        try:
            links = note_links(base, path, get(base + path))
            new = [u for u in links if u not in seen][:MAX_NEW]
            for u in new:
                try:
                    notes.append(read_note(u, get(u)))
                except Exception as e:
                    status.setdefault(name + " notas", []).append(str(e)[:80])
                seen[u] = datetime.now(timezone.utc).isoformat()[:10]
            status[name] = f"{len(links)} notas en la sección, {len(new)} nuevas"
        except Exception as e:
            status[name] = "ERROR " + str(e)[:120]
    if demo:
        notes = [{"url": "https://www.elnuevodia.com/deportes/boxeo/notas/demo/", "title": "Juanmita López de Jesús encabezará la cuarta edición de ITF Es la Pelea en Caguas",
                  "desc": "La cartelera será el 17 de octubre en el Coliseo Roger Mendoza.", "published": "2026-09-03",
                  "text": "Juanmita López de Jesús encabezará la cuarta edición de Es la Pelea el 17 de octubre en el Coliseo Roger Mendoza de Caguas, "
                          "con Miguel Cotto Promotions, H2 Entertainment y Top Rank. También pelearán Ángel Tito Acosta y Bryan Chary Chevalier."}]
    cards = []
    if notes:
        blob = "\n\n".join(f"[{n['url']}] ({n['published'][:10]}) {n['title']}. {n['desc']} {n['text']}" for n in notes)
        chunks, cur = [], ""
        for part in blob.split("\n\n"):
            if len(cur) + len(part) > 9000 and cur:
                chunks.append(cur); cur = ""
            cur += part + "\n\n"
        if cur:
            chunks.append(cur)
        for ch in chunks:
            if demo:  # respuesta de Groq simulada: una buena y dos inventadas (que la validación debe botar)
                raw = [{"date": "2026-10-17", "time": None, "venue": "Coliseo Roger Mendoza", "city": "Caguas", "promoter": "Es la Pelea", "broadcast": [],
                        "fights": [{"a": "Juanmita López de Jesús", "b": None, "rounds": 8, "weight": None, "title": False},
                                   {"a": "Ángel Tito Acosta", "b": None}, {"a": "Bryan Chary Chevalier", "b": None}], "url": notes[0]["url"]},
                       {"date": "2026-10-17", "venue": "Madison Square Garden", "fights": [{"a": "Inventado Pérez", "b": "Otro Nadie"}], "url": notes[0]["url"]},
                       {"date": "2020-01-01", "venue": "Coliseo Roger Mendoza", "fights": [{"a": "Juanmita López de Jesús"}], "url": notes[0]["url"]}]
            else:
                try:
                    raw = groq_json(PROMPT.format(today=today.isoformat(), notes=ch), "cards")
                except Exception as e:
                    status["groq"] = str(e)[:160]; raw = []
            for c in raw:
                v = valid(c, notes, today)
                if v:
                    cards.append(to_app(v))
    fights = [f for f in merge(old, cards) if f["date"][:10] >= (today - timedelta(days=2)).isoformat()]
    data = {"updated": datetime.now(timezone.utc).isoformat(), "source": "El Nuevo Día y Primera Hora (notas de boxeo) + Groq", "status": status, "fights": fights}
    if demo:
        ok = lambda c, m: print(("OK " if c else "FALLA ") + m)
        ok(len(fights) == 1, "1 cartelera válida (las inventadas se botan)")
        f = fights[0] if fights else {}
        ok(f.get("where", "").startswith("Coliseo Roger Mendoza, Caguas"), "lugar: " + f.get("where", ""))
        ok(f.get("title") == "Juanmita López de Jesús (rival por anunciar)", "estelar: " + f.get("title", ""))
        ok(len(f.get("card", [])) == 3 and f.get("timeValid") is False, "3 peleas y hora por confirmar")
        ok(f.get("url", "").startswith("https://www.elnuevodia.com/"), "enlace a la nota original")
        m2 = merge([f], [{**f, "card": [{"fight": "Nuevo Uno vs. Nuevo Dos", "title": False, "rounds": 6, "weight": ""}]}])
        ok(len(m2) == 1 and len(m2[0]["card"]) == 4, "otra nota de la misma cartelera junta las peleas")
        other = {**f, "where": "Cancha Roger Mendoza, Caguas, Puerto Rico", "card": [{"fight": "Juanmita López vs. Jesús Pumita Ramírez", "title": False, "rounds": 8, "weight": ""}]}
        m3 = merge([f], [other])
        ok(len(m3) == 1 and m3[0]["title"] == "Juanmita López vs. Jesús Pumita Ramírez" and len(m3[0]["card"]) == 3 and m3[0]["where"].startswith("Coliseo"),
           "misma cartelera con otro nombre de lugar: se junta y el rival reemplaza al '(rival por anunciar)'")
        return
    os.makedirs(ROOT, exist_ok=True)
    json.dump(data, open(OUT, "w"), ensure_ascii=False, indent=1)
    json.dump(dict(sorted(seen.items(), key=lambda x: x[1])[-600:]), open(SEEN, "w"), ensure_ascii=False, indent=0)
    print(json.dumps(status, ensure_ascii=False), "→", len(fights), "carteleras en PR")

if __name__ == "__main__":
    main("--demo" in sys.argv)

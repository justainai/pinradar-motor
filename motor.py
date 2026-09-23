# -*- coding: utf-8 -*-
"""Pinterest-oogstmotor. Haalt publieke pins op en meet ze, zonder browser.

Keten: bootstrap -> zoeken -> doorklikken -> borden -> pins groeperen op product ->
saves meten -> winkel bereikbaar? -> productdata -> ruwe uitvoer als JSON.

Deze motor oordeelt niet en kiest niet. Welke hoeken en welke termen hij afgaat
staat in een configbestand BUITEN deze repo, meegegeven via $CONFIG. Zonder dat
bestand doet hij niets.

Technische aantekeningen, gemeten tegen de API:
  - saves staan in aggregated_stats; repin_count is een dood veld en geeft 0.
  - de related-feed (doorklikken) geeft vanaf datacenter-IP's een geldig antwoord
    met een LEGE lijst. Daarom is zoeken de hoofdmotor en klikken de bonus.
  - een winkel is dicht als de EIND-URL op /password of /opening-soon eindigt;
    bodytekst is geen betrouwbaar signaal.
  - herseeden tijdens klikken: de related-feed volgt de kijker, niet het onderwerp.
  - de widget-API (pidgets) werkt WEL vanaf een datacenter-IP. Die geeft per pin
    het bord, en per bord 50 pins met de saves erbij: doorklikken via borden.
    Bewezen op GitHub 11-09: 40 borden, 414 productpins, 69 nieuwe >=150 saves,
    tegen 8 uit de hele zoekronde van die ochtend.
  - bordpins hebben geen datum; de pinpagina wel, als "createdAt".
"""
import io, json, os, re, subprocess, sys, time, random, html, urllib.parse
from collections import deque, defaultdict
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

HIER = os.path.dirname(os.path.abspath(__file__))
# Waar de ruwe oogst landt. Buiten deze repo: de workflow wijst dit naar de
# prive-repo die met een token is uitgecheckt.
UITVOER = os.environ.get("RUWDIR") or os.path.join(HIER, "ruw")
os.makedirs(UITVOER, exist_ok=True)
# Productsleutels die eerder al geoogst zijn. Zonder deze lijst vind je bij een
# lange dag steeds hetzelfde terug.
GEZIEN_PAD = os.environ.get("GEZIEN") or ""
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/139.0.0.0 Safari/537.36")
COOKIE = os.path.join(HIER, "_cj.txt")

MINUTEN = int(os.environ.get("MINUTEN", "40"))
# Alleen aanzetten bij handmatig speurwerk, nooit op een publieke workflow.
LUIDOP = os.environ.get("LUIDOP") == "1"
START = time.time()

# Alles wat een keuze is komt uit het configbestand: de hoeken, de zoektermen
# en de ruislat. Deze repo bevat geen enkele instelling.
CONFIG = os.environ.get("CONFIG") or os.path.join(HIER, "config.json")
try:
    CFG = json.load(open(CONFIG, encoding="utf-8"))
    HOEKEN = CFG["hoeken"]
except Exception as e:
    sys.stderr.write("configbestand niet gelezen (%s): %s" % (CONFIG, e) + chr(10))
    raise SystemExit(2)
VOLGORDE = CFG.get("volgorde") or sorted(HOEKEN)
LAT_C = int(CFG.get("ruislat", 150))
# Versheidslat: hoe oud mag de NIEUWSTE pin van een product zijn? 0 = uit.
# Gemeten 09-09 op Justins eigen ja-lijst: een lat van 30 dagen kostte 2 van zijn
# 4 ja's, 120 dagen kost er 1 (en die viel toch om op de marge). Vandaar 120.
MAX_DAGEN = int(CFG.get("max_pin_dagen", 0))
# Bordfase: tot welk deel van het hoekbudget mag hij borden aflopen? 0 = uit.
# De rest van de tijd is voor het meten van wat hij vond.
BORD_TOT = float(CFG.get("bordfase_tot", 0))
KLIKS_MAX = int(os.environ.get("KLIKS", "90"))
# Vergelijkfase (23-09): "Meer zoals dit" per productpin (RelatedModulesResource).
# Werkt WEL vanaf een datacenter-IP, anders dan RelatedPinFeedResource (gemeten
# 23-09: 24 verzoeken gaven op de runner 146 winkelpins, de related-feed 0).
# Zaad: de productpins van een bord uit de config plus de winkelpins uit het
# zoeken. Aan/uit, bord, filter en budget staan allemaal in de config; zonder
# "aan": true doet de motor precies wat hij voor 23-09 deed.
VERGELIJK = CFG.get("vergelijkfase") or {}
W = "https://widgets.pinterest.com/v3/pidgets"

AFF = ("amazon.", "amzn.", "a.co", "instagram.com", "tiktok.com", "temu.", "shein.",
       "aliexpress", "shopee", "etsy.com", "youtube.com", "pinterest.", "facebook.com",
       "ebay.", "alibaba.com", "wish.com", "shop.app", "linktr.ee", "beacons.ai",
       "geni.us", "liketk.it", "shopmy.us", "tr.ee", "amzlink.to", "medium.com",
       "tumblr.com", "walmart.com", "target.com", "bit.ly", "tinyurl", "google.",
       "wayfair", "homedepot", "lowes.com", "ikea.com", "blogspot", "wordpress")

log_regels = []


def log(s):
    """Verzamelt altijd, toont alleen als $LUIDOP=1.

    Op een publieke repo zijn workflow-logs voor iedereen leesbaar. De zoektermen
    en de oogst horen dat niet te zijn, dus standaard zegt de motor niets. Het
    volledige log gaat mee in het motorlog-bestand bij de ruwe uitvoer."""
    log_regels.append(s)
    if LUIDOP:
        print(s, flush=True)


def tijd_op():
    return (time.time() - START) > MINUTEN * 60


def curl(args, timeout=45):
    try:
        p = subprocess.run(["curl", "-s", "-m", "30", "-A", UA] + args,
                           capture_output=True, timeout=timeout)
        return p.stdout.decode("utf-8", "replace")
    except Exception as e:
        return "@@FOUT:%s" % e


# ---------------------------------------------------------------- bootstrap
def bootstrap(term):
    """Cookiejar en appVersion halen we uit een gewone GET. Geen login nodig."""
    if os.path.exists(COOKIE):
        os.remove(COOKIE)
    url = "https://nl.pinterest.com/search/pins/?q=" + urllib.parse.quote(term)
    h = curl(["-c", COOKIE, "-L", url])
    app = None
    m = re.search(r'"appVersion"\s*:\s*"([0-9a-f]{6,10})"', h)
    if m:
        app = m.group(1)
    csrf = None
    if os.path.exists(COOKIE):
        for r in open(COOKIE, encoding="utf-8", errors="ignore"):
            if "csrftoken" in r:
                csrf = r.split()[-1]
    return csrf, app, len(h)


post_fouten = []


def post(resource, body, referer, csrf, app):
    """Geeft de JSON terug. Een LEEG antwoord wordt geregistreerd met statuscode en
    de eerste tekens van de body -- een nul zonder ijking is 'niet gemeten', niet
    'niets gevonden' (gemeten les 06-09)."""
    bp = os.path.join(HIER, "_body.txt")
    open(bp, "w", encoding="utf-8").write(body)
    try:
        out = curl(["-b", COOKIE, "-X", "POST",
                    "-w", "\n@@HTTP:%{http_code}",
                    "https://nl.pinterest.com/resource/%s/get/" % resource,
                    "-H", "content-type: application/x-www-form-urlencoded",
                    "-H", "x-app-version: " + (app or ""),
                    "-H", "x-csrftoken: " + (csrf or ""),
                    "-H", "x-pinterest-appstate: active",
                    "-H", "x-requested-with: XMLHttpRequest",
                    "-H", "referer: " + referer,
                    "--data-binary", "@" + bp], timeout=50)
        romp, _, code = out.rpartition("\n@@HTTP:")
        code = code.strip()
        try:
            return json.loads(romp)
        except Exception:
            if len(post_fouten) < 6:
                post_fouten.append("%s -> HTTP %s, %d bytes: %s"
                                   % (resource, code or "?", len(romp),
                                      re.sub(r"\s+", " ", romp[:180])))
            return {}
    except Exception as e:
        if len(post_fouten) < 6:
            post_fouten.append("%s -> uitzondering %s" % (resource, e))
        return {}
    finally:
        try:
            os.remove(bp)
        except Exception:
            pass


def zoek(term, csrf, app, bookmark=None):
    """Geeft (pins, volgende_bookmark). Met de bookmark haal je de volgende pagina op;
    zo komt een zoekterm veel verder dan de eerste ~17 pins."""
    data = {"options": {"query": term, "scope": "pins",
                        "bookmarks": [bookmark] if bookmark else [],
                        "redux_normalize_feed": True, "rs": "typed"}, "context": {}}
    body = urllib.parse.urlencode({
        "source_url": "/search/pins/?q=" + urllib.parse.quote(term),
        "data": json.dumps(data)})
    d = post("BaseSearchResource", body,
             "https://nl.pinterest.com/search/pins/?q=" + urllib.parse.quote(term),
             csrf, app)
    rr = d.get("resource_response") or {}
    res = (rr.get("data") or {}).get("results") or []
    bm = rr.get("bookmark") or (rr.get("data") or {}).get("bookmark")
    if not bm:
        bms = ((d.get("resource") or {}).get("options") or {}).get("bookmarks") or []
        bm = bms[0] if bms else None
    if bm in ("-end-", ""):
        bm = None
    return ([x for x in res if isinstance(x, dict) and x.get("type") == "pin"], bm)


def klik(pin_id, csrf, app):
    data = {"options": {"pin": pin_id, "prepend": False, "add_vase": True,
                        "bookmarks": []}, "context": {}}
    body = urllib.parse.urlencode({"source_url": "/pin/%s/" % pin_id,
                                   "data": json.dumps(data)})
    d = post("RelatedPinFeedResource", body,
             "https://nl.pinterest.com/pin/%s/" % pin_id, csrf, app)
    da = (d.get("resource_response") or {}).get("data")
    res = da if isinstance(da, list) else ((da or {}).get("results") or [])
    return [x for x in res if isinstance(x, dict) and x.get("type") == "pin"]


def vergelijk(pin_id, csrf, app):
    """'Meer zoals dit' onder een pin. Geeft de pins terug, of [] bij geen antwoord."""
    o = {"pin_id": pin_id, "context_pin_ids": [], "search_query": "",
         "source": "deep_linking", "top_level_source": "deep_linking",
         "top_level_source_depth": 1, "is_pdp": False}
    body = urllib.parse.urlencode({"source_url": "/pin/%s/" % pin_id,
                                   "data": json.dumps({"options": o, "context": {}})})
    d = post("RelatedModulesResource", body,
             "https://nl.pinterest.com/pin/%s/" % pin_id, csrf, app)
    da = (d.get("resource_response") or {}).get("data")
    res = da if isinstance(da, list) else ((da or {}).get("results") or [])
    return [x for x in res if isinstance(x, dict) and x.get("type") == "pin"]


def bordpins(src, bid, csrf, app):
    """Alle pins van een bord, via een GET op BoardFeedResource (een POST geeft 403,
    gemeten 23-09). Valt terug op de widget (alleen de nieuwste pins)."""
    alles, bm = {}, None
    for _ in range(40):
        o = {"board_id": bid, "board_url": src, "currentFilter": -1,
             "field_set_key": "react_grid_pin", "filter_section_pins": True,
             "sort": "default", "layout": "default", "page_size": 25,
             "redux_normalize_feed": True}
        if bm:
            o["bookmarks"] = [bm]
        q = urllib.parse.urlencode({"source_url": src,
                                    "data": json.dumps({"options": o, "context": {}})})
        out = curl(["-b", COOKIE, "--compressed",
                    "https://nl.pinterest.com/resource/BoardFeedResource/get/?" + q,
                    "-H", "x-app-version: " + (app or ""), "-H", "x-csrftoken: " + (csrf or ""),
                    "-H", "x-pinterest-appstate: active", "-H", "x-requested-with: XMLHttpRequest",
                    "-H", "x-pinterest-source-url: " + src,
                    "-H", "x-pinterest-pws-handler: www/[username]/[slug].js",
                    "-H", "referer: https://nl.pinterest.com" + src])
        try:
            r = json.loads(out).get("resource_response") or {}
        except Exception:
            break
        pins = [x for x in (r.get("data") or []) if isinstance(x, dict) and x.get("type") == "pin"]
        for p in pins:
            alles[p["id"]] = p
        bm = r.get("bookmark")
        if not pins or not bm or bm == "-end-":
            break
        time.sleep(0.5)
    bron = "feed"
    if not alles:
        bron = "widget"
        c, d = pidget(W + "/boards" + src.rstrip("/") + "/pins/")
        for p in ((d.get("data") or {}).get("pins") or []):
            alles[p["id"]] = p
    return list(alles.values()), bron


def vergelijkfase(alles, csrf, app, hoek):
    """Zo zoekt Justin zelf: bij een product 'Meer zoals dit', en alleen verder op
    wat er dropship uitziet. Alleen winkelpins die door het filter komen gaan de
    oogst in EN worden het zaad voor de volgende laag; zo drijft hij niet af naar
    cadeaus en maskers (gemeten 23-09: laag 2 zonder filter dwaalde af)."""
    grens = MINUTEN * 60 * float(VERGELIJK.get("tot", 0.5))
    max_verz = int(VERGELIJK.get("max_verzoeken", 80))
    lagen = int(VERGELIJK.get("lagen", 3))
    niet = re.compile(VERGELIJK["niet"], re.I) if VERGELIJK.get("niet") else None

    def goed(p):
        if not winkelpin(p):
            return False
        t = " ".join([p.get("grid_title") or p.get("title") or "",
                      (p.get("description") or "")[:200], p.get("link") or ""])
        return not (niet and niet.search(t))

    # Zaad 1: het bord, verdeeld over de hoeken (elke hoek een eigen deel, en
    # dagelijks een ander deel voorop), zodat twee hoeken niet hetzelfde oogsten.
    zaad = []
    b = VERGELIJK.get("bord") or {}
    if b.get("src") and b.get("id"):
        pins, bron = bordpins(b["src"], b["id"], csrf, app)
        eigen = sorted([p["id"] for p in pins if goed(p)])
        i = VOLGORDE.index(hoek) if hoek in VOLGORDE else 0
        n = max(len(VOLGORDE), 1)
        dag = datetime.now(timezone.utc).toordinal()
        deel = [pid for j, pid in enumerate(eigen) if (j + dag) % n == i]
        zaad += deel[:int(VERGELIJK.get("bordzaad_per_hoek", 10))]
        log("vergelijkfase: bord gaf %d pins (%s), %d productpins na filter, %d als zaad"
            % (len(pins), bron, len(eigen), len(zaad)))
    # Zaad 2: de winkelpins uit het zoeken van deze hoek (blijven bij het onderwerp).
    uit_zoek = [pid for pid, p in alles.items()
                if p.get("via") == "zoekterm" and goed(p)]
    random.shuffle(uit_zoek)
    zaad += uit_zoek[:int(VERGELIJK.get("zoekzaad_per_hoek", 15))]
    voor, verz, leeg, gezien = len(alles), 0, 0, set(zaad)
    laag_zaad = zaad
    for laag in range(1, lagen + 1):
        volgende = []
        for pid in laag_zaad:
            if verz >= max_verz or time.time() - START > grens or tijd_op():
                break
            buren = vergelijk(pid, csrf, app)
            verz += 1
            if not buren:
                leeg += 1
            for k in buren:
                if k["id"] in gezien:
                    continue
                gezien.add(k["id"])
                if not goed(k):
                    continue
                if k["id"] not in alles:
                    alles[k["id"]] = bewaar(k, laag, "vergelijk", "")
                volgende.append(k["id"])
            time.sleep(0.6 + random.random() * 0.4)
        log("  vergelijk laag %d: %d zaad, %d winkelpins door het filter (totaal %d verzoeken)"
            % (laag, len(laag_zaad), len(volgende), verz))
        # Breedte boven diepte: de volgende laag krijgt een gemengde greep, niet
        # alleen de eerste buren van het eerste zaad.
        random.shuffle(volgende)
        laag_zaad = volgende
        if not laag_zaad:
            break
    log("vergelijkfase: %d verzoeken (%d leeg), %d nieuwe winkelpins (totaal %d)"
        % (verz, leeg, len(alles) - voor, len(alles)))


def pin_dagen(s):
    """Hoeveel dagen geleden is deze pin geplaatst? None als de datum ontbreekt of
    onleesbaar is -- en dan telt hij NIET als te oud: een ontbrekende meting is geen
    meting, en daar gooien we niets op weg."""
    if not s:
        return None
    for ontleed in (parsedate_to_datetime,
                    lambda x: datetime.fromisoformat(x.replace("Z", "+00:00"))):
        try:
            d = ontleed(s)
            if d.tzinfo is None:
                d = d.replace(tzinfo=timezone.utc)
            return max((datetime.now(timezone.utc) - d).days, 0)
        except Exception:
            pass
    return None


def winkelpin(p):
    l = p.get("link") or ""
    if not re.search(r"/products?/[^/?#]", l):
        return False
    dom = (urllib.parse.urlparse(l).netloc or "").lower()
    return bool(dom) and not any(a in dom for a in AFF)


def bewaar(p, diepte, via, term):
    return {"pin_id": p["id"], "pin_url": "https://nl.pinterest.com/pin/%s/" % p["id"],
            "gesponsord": not bool(re.fullmatch(r"\d{6,25}", str(p["id"]))),
            "created_at": p.get("created_at"), "link": p.get("link"),
            "titel": (p.get("grid_title") or p.get("title") or p.get("seo_alt_text")
                      or p.get("auto_alt_text") or "").strip(),
            "beschrijving": (p.get("description") or "").strip()[:300],
            "diepte": diepte, "via": via, "zaadterm": term}


# ---------------------------------------------------------------- saves
def saves_pidgets(ids):
    """40 numerieke ids per verzoek. aggregated_stats.saves, nooit repin_count."""
    uit = {}
    for i in range(0, len(ids), 40):
        blok = ids[i:i + 40]
        out = curl(["https://widgets.pinterest.com/v3/pidgets/pins/info/?pin_ids="
                    + ",".join(blok)])
        try:
            d = json.loads(out)
        except Exception:
            continue
        for p in (d.get("data") or []):
            pid = str(p.get("id") or "")
            st = ((p.get("aggregated_pin_data") or {}).get("aggregated_stats") or {})
            if pid and st.get("saves") is not None:
                uit[pid] = int(st["saves"])
        time.sleep(0.6)
    return uit


def saves_pagina(pin_id):
    """Voor gesponsorde pins met een opaque id: uit de pinpagina zelf."""
    h = curl(["https://nl.pinterest.com/pin/%s/" % pin_id])
    m = re.search(r'"aggregatedStats"\s*:\s*\{\s*"saves"\s*:\s*(\d+)', h)
    return int(m.group(1)) if m else None


def datum_pagina(pin_id):
    """Bordpins hebben geen datum; de pinpagina wel, als "createdAt"."""
    h = curl(["https://nl.pinterest.com/pin/%s/" % pin_id])
    m = re.search(r'"createdAt"\s*:\s*"([^"]+)"', h)
    return m.group(1) if m else None


# ---------------------------------------------------------------- borden
def pidget(url):
    """Widget-API. Geeft (http-code, json); een lege of kapotte body is {}."""
    out = curl(["-w", "\n@@HTTP:%{http_code}", url])
    romp, _, code = out.rpartition("\n@@HTTP:")
    try:
        return code.strip(), json.loads(romp)
    except Exception:
        return code.strip(), {}


def bordfase(alles, vandaag):
    """Doorklikken via borden. Zaad: de pins uit het zoeken, winkelpins voorop.
    Per blok van 40 zaadpins: op welke borden staan ze, en dan die borden aflopen.
    De winkelpins van de borden gaan in `alles`; hun saves komen mee terug, zodat
    ze niet opnieuw gemeten hoeven te worden.

    Borden die vandaag al in een eerdere hoek zijn afgelopen staan in
    <datum>_borden.txt en worden overgeslagen."""
    grens = MINUTEN * 60 * BORD_TOT
    pad = os.path.join(UITVOER, "%s_borden.txt" % vandaag)
    try:
        al = set(open(pad, encoding="utf-8").read().split())
    except Exception:
        al = set()
    zaad = ([pid for pid, p in alles.items() if winkelpin(p)]
            + [pid for pid, p in alles.items() if not winkelpin(p)])
    zaad = [z for z in zaad if re.fullmatch(r"\d{6,25}", str(z))]
    codes = defaultdict(int)
    vooraf, bekeken, fout_op_rij, pauzes, stop = {}, 0, 0, 0, False
    voor = len(alles)
    for i in range(0, len(zaad), 40):
        if stop or time.time() - START > grens:
            break
        c, d = pidget(W + "/pins/info/?pin_ids=" + ",".join(zaad[i:i + 40]))
        codes["info " + c] += 1
        borden = []
        for p in d.get("data") or []:
            u = ((p or {}).get("board") or {}).get("url")
            if u and u not in al:
                al.add(u)
                borden.append(u)
        time.sleep(0.6)
        for u in borden:
            if time.time() - START > grens:
                break
            c, d = pidget(W + "/boards" + u.rstrip("/") + "/pins/")
            codes["bord " + c] += 1
            pins = (d.get("data") or {}).get("pins") or []
            if c != "200" or not pins:
                # Vijf keer op rij niets: even wachten. Blijft het zo, dan stoppen
                # in plaats van de hoektijd op te maken aan een dichte deur.
                fout_op_rij += 1
                if fout_op_rij >= 5:
                    pauzes += 1
                    if pauzes > 3:
                        log("bordfase: steeds geen antwoord -- gestopt")
                        stop = True
                        break
                    time.sleep(60)
                    fout_op_rij = 0
                continue
            fout_op_rij = 0
            bekeken += 1
            for p in pins:
                if not winkelpin(p) or p.get("id") in alles:
                    continue
                if not (p.get("title") or p.get("grid_title")):
                    p = dict(p, title=(p.get("description") or "")[:120])
                alles[p["id"]] = bewaar(p, 1, "bord", u)
                st = (p.get("aggregated_pin_data") or {}).get("aggregated_stats") or {}
                if st.get("saves") is not None:
                    vooraf[str(p["id"])] = int(st["saves"])
            time.sleep(0.6)
    try:
        open(pad, "w", encoding="utf-8").write("\n".join(sorted(al)))
    except Exception:
        pass
    log("bordfase: %d zaadpins, %d borden bekeken, %d winkelpins erbij, http %s"
        % (len(zaad), bekeken, len(alles) - voor, dict(codes)))
    return vooraf


# ---------------------------------------------------------------- winkel
def winkel_leeft(url):
    """Alleen de EIND-URL telt. 'notify me when available' is standaard Shopify-tekst
    en verklaarde op 06-09 vijf van acht winkels vals dood."""
    out = curl(["-o", os.devnull, "-L", "-w", "%{http_code}|%{url_effective}", url])
    code, _, eind = out.partition("|")
    dood = bool(re.search(r"/(password|opening-soon|challenge)(\?|/|$)", eind))
    return code.strip(), eind.strip(), (code.strip() == "200" and not dood)


def productdata(dom, handle, link):
    rec = {}
    body = curl(["-L", "https://%s/products/%s.json"
                 % (dom, urllib.parse.quote(handle, safe=""))])
    pr = None
    if body.lstrip().startswith("{"):
        try:
            pr = json.loads(body).get("product")
        except Exception:
            pr = None
    if not pr:
        h = curl(["-L", link.split("?")[0]])
        for blok in re.findall(
                r'<script[^>]*type="application/ld\+json"[^>]*>(.*?)</script>', h, re.S):
            try:
                j = json.loads(blok.strip())
            except Exception:
                continue
            for c in (j if isinstance(j, list) else [j]):
                if isinstance(c, dict) and "Product" in str(c.get("@type", "")):
                    o = c.get("offers")
                    o = o[0] if isinstance(o, list) and o else o
                    rec["titel_winkel"] = c.get("name")
                    if isinstance(o, dict) and o.get("price"):
                        try:
                            rec["prijs"] = float(o["price"])
                        except Exception:
                            pass
                        rec["valuta"] = o.get("priceCurrency")
                    rec["data_bron"] = "JSON-LD"
        return rec
    vs = pr.get("variants") or []
    pz = [float(v["price"]) for v in vs if v.get("price")]
    imgs = pr.get("images") or []
    rec.update({"titel_winkel": pr.get("title"), "type": pr.get("product_type"),
                "gepubliceerd": pr.get("published_at"),
                "prijs": min(pz) if pz else None,
                "prijs_max": max(pz) if pz else None,
                "n_varianten": len(vs),
                "varianten": [v.get("title") for v in vs][:10],
                "n_beelden": len(imgs),
                "beeld": (imgs[0].get("src") if imgs and isinstance(imgs[0], dict)
                          else None),
                "body": re.sub(r"\s+", " ", html.unescape(
                    re.sub(r"<[^>]+>", " ", pr.get("body_html") or "")))[:900],
                "data_bron": "products/<handle>.json"})
    return rec


# ---------------------------------------------------------------- hoek kiezen
def kies_hoek():
    if os.environ.get("HOEK"):
        return os.environ["HOEK"]
    vandaag = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    gedaan = {f.split("_", 1)[1].rsplit(".", 1)[0]
              for f in os.listdir(UITVOER)
              if f.startswith(vandaag) and f.endswith(".json")}
    for h in VOLGORDE:
        if h not in gedaan:
            return h
    return None


def main():
    hoek = kies_hoek()
    if not hoek:
        log("Alle hoeken hebben vandaag al een bestand. Niets te doen.")
        # exitcode 3 = niets meer te doen; de lus in de workflow stopt daarop.
        return 3
    vandaag = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    # Uit de pot van deze hoek een VERSE selectie trekken, elke dag een stuk
    # verderop. Dezelfde termen elke dag herhalen graaft hetzelfde gat dieper uit:
    # gemeten 08->09-09 gaf 2,5x zoveel pins maar maar 1,5x zoveel kandidaten, en
    # een steeds oudere staart. Een verse term opent een nieuw stuk Pinterest.
    pot = HOEKEN[hoek][:]
    n_per_dag = int(CFG.get("termen_per_ronde", 11))
    if len(pot) > n_per_dag:
        # Draaipunt uit de datum, niet willekeurig: twee runs op dezelfde dag
        # pakken dezelfde termen, en over de dagen heen loopt hij de pot rond.
        offset = (datetime.now(timezone.utc).toordinal() * n_per_dag) % len(pot)
        termen = [pot[(offset + i) % len(pot)] for i in range(n_per_dag)]
    else:
        termen = pot
    log("# Pinronde %s — hoek: %s (budget %d min)" % (vandaag, hoek, MINUTEN))
    log("termen: %d van de %d uit de pot" % (len(termen), len(pot)))

    csrf, app, n = bootstrap(termen[0])
    log("bootstrap: html=%d bytes, csrf=%s, appVersion=%s"
        % (n, "ja" if csrf else "NEE", app or "NIET GEVONDEN"))
    if not csrf:
        log("GEEN csrftoken — Pinterest gaf geen cookie. Ronde afgebroken, "
            "dit is een instrumentfout en geen uitspraak over de markt.")
        schrijf(hoek, vandaag, [])
        return 1

    # ---- fase 1: breed zoeken, met paginering
    # Doorklikken is lokaal 40x efficienter, maar geeft vanaf een datacenter-IP een
    # GELDIG antwoord met nul pins (gemeten 07-09, twee runs). Daarom eerst breed
    # zoeken: dat werkt overal. Doorklikken komt daarna, met de tijd die overblijft.
    alles, gezien, rij = {}, set(), deque()
    PAGINAS = int(os.environ.get("PAGINAS", "6"))
    for t in termen:
        # Zoeken mag hoogstens een derde van het budget kosten: meten is waar de
        # kaart van gemaakt wordt, en zonder meting is er geen kaart.
        if tijd_op() or (time.time() - START) > MINUTEN * 60 * 0.35:
            log("  (zoekfase afgekapt op tijd)")
            break
        bm, nieuw_totaal = None, 0
        for pg in range(PAGINAS):
            z, bm = zoek(t, csrf, app, bm)
            if not z:
                break
            for p in z:
                if p["id"] not in alles:
                    alles[p["id"]] = bewaar(p, 0, "zoekterm", t)
                    nieuw_totaal += 1
                    # Zaai met wat BEWEZEN naar een winkel wijst, niet met de eerste
                    # vier zoekresultaten. Die vier zijn juist de pins die de
                    # zoekfase toch al binnenhaalt; hun omgeving is dus het minst
                    # nieuw. Een pin die naar een echte productpagina linkt staat
                    # per definitie in het stuk feed waar wij naartoe willen.
                    if winkelpin(p):
                        rij.append((p["id"], 1, "zaad", t))
            if not bm:
                break
            time.sleep(0.7)
        log("  zoek '%s': %d nieuw over %d pagina's" % (t, nieuw_totaal, pg + 1))
        time.sleep(0.6)
    log("zoekfase: %d pins" % len(alles))
    # Zaad voor de doorklikfase die op een eigen pc draait: de related-feed is
    # vanaf een datacenter-IP leeg. Alleen de ids van de winkelpins uit het
    # zoeken, geen zoektermen. Doorklikken vanaf deze pins blijft bij het
    # onderwerp; vanaf de bordproducten drijft het af naar mode (gemeten 22-09).
    # BEWUST .txt en geen .json: oordeel.py en kies_hoek() lezen elk
    # <datum>_*.json in de uitvoermap als een hoek. Niet omzetten naar json.
    # Een fout hier mag de ronde nooit stoppen.
    try:
        zaad_ids = [str(pid) for pid, p in alles.items() if winkelpin(p)]
        with io.open(os.path.join(UITVOER, "%s_%s_zaad.txt" % (vandaag, hoek)),
                     "w", encoding="utf-8") as f:
            f.write(chr(10).join(zaad_ids) + chr(10))
        log("zaad voor lokaal doorklikken: %d ids" % len(zaad_ids))
    except Exception as e:
        log("zaad niet weggeschreven (%s)" % type(e).__name__)
    na_zoeken = len(alles)
    if not rij:
        # Geen enkele winkelpin gevonden: dan maar zaaien met wat er is, anders
        # slaat de doorklikfase helemaal over.
        for pid in list(alles)[:8]:
            rij.append((pid, 1, "zaad-terugval", ""))
        log("geen winkelpins om mee te zaaien — teruggevallen op %d gewone pins"
            % len(rij))
    else:
        log("zaad voor het doorklikken: %d winkelpins" % len(rij))

    # ---- fase 2: doorklikken zolang het iets oplevert
    kliks = 0
    # Twee TELLERS, geen een. 'terug' is wat de feed opstuurde, 'nieuw' is wat
    # daarvan nog niet in de oogst zat. Wie alleen 'nieuw' meet kan een lege feed
    # niet onderscheiden van een feed die niets toevoegt -- en die twee vragen om
    # een tegenovergestelde oplossing (ander IP versus ander zaad).
    terug = 0
    lege_feeds = 0
    while rij and not tijd_op() and kliks < KLIKS_MAX:
        pid, diepte, via, term = rij.popleft()
        if pid in gezien or diepte > 3:
            continue
        gezien.add(pid)
        uit_feed = klik(pid, csrf, app)
        terug += len(uit_feed)
        if not uit_feed:
            lege_feeds += 1
        for k in uit_feed:
            if k["id"] not in alles:
                alles[k["id"]] = bewaar(k, diepte, pid, term)
            if diepte < 3 and k["id"] not in gezien:
                if winkelpin(k):
                    rij.appendleft((k["id"], diepte + 1, pid, term))
                elif random.random() < 0.2:
                    rij.append((k["id"], diepte + 1, pid, term))
        kliks += 1
        # Levert doorklikken hier niets op, stop er dan mee in plaats van de tijd
        # op te maken aan kansloze calls -- maar zeg er WEL bij welke van de twee
        # oorzaken het is, anders staat er een conclusie in het log die nooit is
        # gemeten.
        if kliks == 8 and len(alles) == na_zoeken:
            if terug == 0:
                log("doorklikken: 8 kliks, de feed stuurde 0 pins terug -- deze machine "
                    "krijgt geen related-feed. Gestopt met klikken.")
            else:
                log("doorklikken: 8 kliks, de feed stuurde %d pins terug maar ALLE was al "
                    "geoogst -- de feed werkt hier wel, het zaad is te bekend. Gestopt."
                    % terug)
            break
        time.sleep(0.45 + random.random() * 0.4)
    log("doorklikfase: %d kliks, feed gaf %d pins terug (%d leeg), %d nieuw (totaal %d)"
        % (kliks, terug, lege_feeds, len(alles) - na_zoeken, len(alles)))
    if post_fouten:
        log("post-antwoorden die geen JSON waren (eerste %d):" % len(post_fouten))
        for f in post_fouten:
            log("  " + f)
    if kliks and len(alles) <= 40:
        log("LET OP: de kliks leverden vrijwel niets op. Zoeken werkte wel, dus dit "
            "is geen uitspraak over de markt maar over deze machine.")

    # ---- fase 3a: 'Meer zoals dit' per productpin (alleen als de config het aanzet)
    if VERGELIJK.get("aan"):
        try:
            vergelijkfase(alles, csrf, app, hoek)
        except Exception as e:
            # Een fout hier mag de ronde nooit stoppen: dan meten we wat er al is.
            log("vergelijkfase afgebroken (%s: %s)" % (type(e).__name__, str(e)[:120]))

    # ---- fase 3b: doorklikken via borden, met de tijd die overblijft
    vooraf = bordfase(alles, vandaag) if BORD_TOT > 0 else {}

    # ---- groepeer op product (domein + handle), niet op pin en niet op zoekterm
    producten = defaultdict(list)
    for p in alles.values():
        if not winkelpin(p):
            continue
        u = urllib.parse.urlparse(p["link"])
        m = re.search(r"/products?/([^/?#]+)", u.path)
        if not m:
            continue
        producten[(u.netloc.lower(), m.group(1).lower())].append(p)
    log("productpins: %d pins over %d producten" % (
        sum(len(v) for v in producten.values()), len(producten)))
    if not producten:
        log("Geen enkele productpin. Niets om te meten.")
        schrijf(hoek, vandaag, [])
        return 0

    # ---- saves per product (som over de eigen pins is fout: aggregated telt al
    #      het hele beeld, dus we nemen het HOOGSTE gemeten getal van de pins)
    numeriek = [p["pin_id"] for v in producten.values() for p in v
                if not p["gesponsord"] and str(p["pin_id"]) not in vooraf]
    gemeten = saves_pidgets(numeriek)
    log("saves via pidgets: %d van %d numerieke pins (+%d al gemeten op het bord)"
        % (len(gemeten), len(numeriek), len(vooraf)))
    gemeten.update(vooraf)

    # Meet de producten met de MEESTE eigen pins eerst: herhaald signaal binnen een
    # bron slaat elk los cijfer, en als de tijd opraakt wil je die eerst gehad hebben.
    op_volgorde = sorted(producten.items(),
                         key=lambda kv: (-len(kv[1]),
                                         -max((gemeten.get(p["pin_id"], 0)
                                               for p in kv[1]), default=0)))
    al_gezien = eerder_geoogst()
    if al_gezien:
        log("ontdubbelen tegen %d eerder geoogste producten" % len(al_gezien))
    overgeslagen = 0
    te_oud = 0
    datums = [0, 0]
    kand = []
    for (dom, handle), pins in op_volgorde:
        if "%s/%s" % (dom, handle) in al_gezien:
            overgeslagen += 1
            continue
        if tijd_op():
            log("tijdbudget op tijdens meten — rest niet gemeten")
            break
        waardes = [gemeten.get(p["pin_id"]) for p in pins]
        waardes = [w for w in waardes if w is not None]
        if not waardes:
            gs = [p for p in pins if p["gesponsord"]]
            if gs:
                w = saves_pagina(gs[0]["pin_id"])
                if w is not None:
                    waardes = [w]
                time.sleep(0.5)
        if not waardes:
            continue
        saves = max(waardes)
        if saves < LAT_C:
            continue
        # Versheidslat. Saves stapelen zich op zolang een pin bestaat, dus een oude
        # pin komt op TOTAAL vanzelf bovendrijven zonder dat er iets leeft: gemeten
        # 09-09 stond een pin van 1165 dagen op de derde plek. Meet de NIEUWSTE pin
        # van het product, niet de best scorende -- een oud product met een verse
        # pin is precies wat we zoeken, andersom niet.
        if MAX_DAGEN and all(pin_dagen(p.get("created_at")) is None for p in pins):
            # Bordpins hebben geen datum. Haal hem van de pinpagina van de best
            # scorende pin; lukt dat niet, dan gooien we niets weg.
            b = max(pins, key=lambda p: gemeten.get(p["pin_id"], 0))
            b["created_at"] = datum_pagina(b["pin_id"])
            datums[0 if b["created_at"] else 1] += 1
            time.sleep(0.5)
        jongste = min((pin_dagen(p.get("created_at")) for p in pins
                       if pin_dagen(p.get("created_at")) is not None), default=None)
        if MAX_DAGEN and jongste is not None and jongste > MAX_DAGEN:
            te_oud += 1
            continue
        beste = max(pins, key=lambda p: gemeten.get(p["pin_id"], 0))
        code, eind, leeft = winkel_leeft(beste["link"])
        time.sleep(0.8)
        rec = {"bron": "pinterest", "hoek": hoek,
               "product_sleutel": "%s/%s" % (dom, handle),
               "winkel": dom, "handle": handle,
               "url_bewijs": beste["pin_url"], "winkel_url": beste["link"],
               "gemeten_op": datetime.now(timezone.utc).isoformat(timespec="seconds"),
               "volume": saves, "volume_soort": "aggregated_saves",
               "n_pins": len(pins), "n_pins_gemeten": len(waardes),
               "gesponsord": any(p["gesponsord"] for p in pins),
               "titel_pin": beste["titel"], "beschrijving_pin": beste["beschrijving"],
               "created_at": beste.get("created_at"),
               "gevonden_via": ("zoeken" if any(p["via"] == "zoekterm" for p in pins)
                                else "vergelijk" if any(p["via"] == "vergelijk" for p in pins)
                                else "bord" if any(p["via"] == "bord" for p in pins)
                                else "doorklikken"),
               "http": code, "eind_url": eind, "winkel_leeft": leeft}
        if leeft:
            rec.update(productdata(dom, handle, beste["link"]))
            time.sleep(0.8)
        kand.append(rec)

    if overgeslagen:
        log("%d producten overgeslagen: die stonden al in de eerdere oogst" % overgeslagen)
    if datums[0] or datums[1]:
        log("datum van de pinpagina gehaald: %d gelukt, %d niet" % tuple(datums))
    if te_oud:
        log("%d producten afgevallen op de versheidslat (nieuwste pin ouder dan "
            "%d dagen)" % (te_oud, MAX_DAGEN))
    log("boven de ruislat (>=%d saves): %d nieuwe producten" % (LAT_C, len(kand)))
    schrijf(hoek, vandaag, kand)
    return 0


def eerder_geoogst():
    """Sleutels uit $GEZIEN. Ontbreekt het bestand, dan is de set leeg en oogst
    de motor alles -- nooit een harde fout, want dan verliest hij een hele ronde."""
    if not GEZIEN_PAD or not os.path.exists(GEZIEN_PAD):
        return set()
    try:
        d = json.load(open(GEZIEN_PAD, encoding="utf-8"))
        return set(d if isinstance(d, list) else d.get("producten") or [])
    except Exception as e:
        log("gezien-lijst niet gelezen (%s) -- ronde gaat door zonder ontdubbelen" % e)
        return set()


def schrijf(hoek, vandaag, kand):
    """Ruwe oogst als JSON, plus een technisch log met alleen tellingen.

    Er staat met opzet GEEN kandidatenlijst in de log-uitvoer: op een publieke
    repo zijn workflow-logs openbaar, en de oogst zelf hoort dat niet te zijn.
    Wat er gevonden is staat in het JSON-bestand, dat naar de prive-repo gaat.
    """
    NL = chr(10)
    jp = os.path.join(UITVOER, "%s_%s.json" % (vandaag, hoek))
    json.dump(kand, open(jp, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    lp = os.path.join(UITVOER, "%s_%s_motorlog.md" % (vandaag, hoek))
    with io.open(lp, "w", encoding="utf-8") as f:
        f.write("# Motorlog %s -- %s" % (vandaag, hoek) + NL + NL)
        f.write(NL.join("    " + r for r in log_regels) + NL)
    print("ronde klaar: %d producten geoogst" % len(kand))


if __name__ == "__main__":
    try:
        sys.exit(main())
    finally:
        for f in (COOKIE, os.path.join(HIER, "_body.txt")):
            try:
                os.remove(f)
            except Exception:
                pass

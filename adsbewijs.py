"""Adsbewijs: op welke producten adverteert een winkel echt, en hoe hard?

Draait in GitHub Actions. De code staat hier (publiek); de wachtrij, de uitslag
en alle namen staan in de prive-werkmap. In het publieke log staan alleen
tellingen.

WAAROM (gemeten 06-10). De Termenrun vindt winkels met een actieve ad, en de
stap daarna toonde hun catalogus. Maar een winkel adverteert meestal op een of
een paar producten: van 13 gekozen producten had er 1 een eigen ad, en van 893
geadverteerde producten stond de helft niet eens in de eerste 250 van de
catalogus. De catalogus is dus de verkeerde ingang. Deze stap vraagt per winkel
zijn grootste actieve ads op en groepeert ze per productlink.

TWEE SOORTEN OPDRACHTEN in de wachtrij (ads/bewijs_wachtrij.json):
  {"pid": ...,  "dom": ...}   pagina-id bekend: een call
  {"dom": ...}                alleen het domein bekend (bijvoorbeeld van een
                              andere bron): eerst zoeken op het domein -- de
                              zoekfunctie doorzoekt ook link_url -- en daarna de
                              pagina opvragen: twee calls

DAT DIT VANAF EEN RUNNER WERKT IS GEMETEN (Bereikproef, 06-10): vijf winkels
gaven hier exact dezelfde teller als thuis (52/79/134/68/68).

DE REM herken je aan count > 0 met 0 ads. Drie keer op rij en hij stopt; wat
niet gedaan is blijft in de wachtrij.

Per geadverteerd product haalt hij <handle>.js bij de winkel zelf op (titel,
prijs, foto). Dat kost geen Meta-budget. De grootste ad van elk product met
MIN_CR of meer creatives gaat naar ads/metingen.json, waar bereik.py hem meet.

Env: PRIVAAT, BUDGET (Meta-calls), MIN_CR.
"""
import asyncio
import json
import os
import re
import sys
import time
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from urllib.parse import quote, unquote, urlparse

from playwright.async_api import async_playwright

PRIV = os.environ.get("PRIVAAT", "privaat")
MAP = os.path.join(PRIV, "ads")
WACHTRIJ = os.path.join(MAP, "bewijs_wachtrij.json")
UIT = os.path.join(MAP, "adsbewijs.json")
METINGEN = os.path.join(MAP, "metingen.json")
BUDGET = int(os.environ.get("BUDGET", "120"))
MIN_CR = int(os.environ.get("MIN_CR", "3"))
MAX_DETAIL = 12      # productpagina's per winkel

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/128.0 Safari/537.36")
BASIS = "/ads/library/?active_status=active&ad_type=all&country=ALL&media_type=all"

HAAL = """
async (url) => {
  const conn = (h) => {
    const k = '"search_results_connection":';
    const i = h.indexOf(k);
    if (i < 0) return null;
    let j = i + k.length, dep = 0, inS = false, esc = false;
    for (let p = j; p < h.length; p++) {
      const c = h[p];
      if (inS) { if (esc) esc = false; else if (c === '\\\\') esc = true; else if (c === '"') inS = false; continue; }
      if (c === '"') inS = true;
      else if (c === '{') dep++;
      else if (c === '}') { dep--; if (dep === 0) return JSON.parse(h.slice(j, p + 1)); }
    }
    return null;
  };
  let c = null, h = '';
  for (let t = 0; t < 3 && !c; t++) { h = await fetch(url).then(r => r.text()); c = conn(h); }
  if (!c) return {gevonden: false, botcheck: h.indexOf('__rd_verif') >= 0};
  const ads = (c.edges || []).map(e => {
    const r = e.node.collated_results[0], s = r.snapshot || {};
    return {
      id: r.ad_archive_id, pid: r.page_id, pagina: s.page_name || r.page_name || '',
      start: r.start_date, link: (s.link_url || '').split('?')[0], caption: s.caption || '',
      titel: (s.title || '').slice(0, 90), video: (s.videos || []).length ? 1 : 0,
      cc: r.collation_count || 1,
    };
  });
  return {gevonden: true, count: c.count, ads: ads};
}
"""


def laad(pad, leeg):
    try:
        with open(pad, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return leeg


def bewaar(pad, wat):
    with open(pad, "w", encoding="utf-8") as f:
        json.dump(wat, f, ensure_ascii=False, indent=0)


def kaal(d):
    return re.sub(r"^(?:www\.|m\.|shop\.)", "", (d or "").lower().strip("/"))


def domein_van(a):
    if a.get("link"):
        m = re.match(r"https?://([^/]+)", a["link"])
        if m:
            return kaal(m.group(1))
    return kaal(a.get("caption"))


def per_product(ads):
    """Groepeer de ads van een winkel per productlink."""
    groep = {}
    for rang, a in enumerate(ads):
        if "/products/" not in a["link"]:
            continue
        handle = a["link"].split("/products/")[1].split("/")[0]
        g = groep.setdefault(unquote(handle).lower(), {"handle": handle, "host": urlparse(a["link"]).netloc, "ads": []})
        g["ads"].append((rang, a))
    uit = []
    for g in groep.values():
        rij = [a for _, a in g["ads"]]
        top = min(g["ads"], key=lambda x: x[0])[1]
        starts = [a["start"] for a in rij if a["start"]]
        uit.append({
            "handle": g["handle"], "host": g["host"], "n": len(rij), "cr": sum(a["cc"] for a in rij),
            "video": sum(a["video"] for a in rij), "jong": max(starts) if starts else None,
            "oud": min(starts) if starts else None, "top": top["id"], "top_start": top["start"],
            "titel_ad": top["titel"],
        })
    uit.sort(key=lambda p: -p["cr"])
    return uit


def detail(p):
    """Titel, prijs en foto van de winkel zelf. Kost geen Meta-budget."""
    url = "https://%s/products/%s.js" % (p["host"], p["handle"])
    try:
        req = urllib.request.Request(url, headers={"User-Agent": UA})
        with urllib.request.urlopen(req, timeout=15) as r:
            j = json.loads(r.read().decode("utf-8", "replace"))
        img = j.get("featured_image") or ""
        p.update(titel=j.get("title", "")[:160], prijs=(j.get("price") or 0) / 100,
                 type=(j.get("type") or "")[:60], img=("https:" + img) if img.startswith("//") else img)
    except Exception:
        p["detail_mis"] = True
    return p


async def main():
    rij = laad(WACHTRIJ, [])
    klaar = laad(UIT, {})
    metingen = laad(METINGEN, {})
    todo = [w for w in rij if kaal(w.get("dom")) not in klaar]
    print("wachtrij %d | al gedaan %d | te doen %d | budget %d calls" % (len(rij), len(rij) - len(todo), len(todo), BUDGET))
    if not todo:
        return 0

    calls = leeg = gedaan = geen = nieuw_meet = 0
    nu = datetime.now(timezone.utc).isoformat(timespec="seconds")
    async with async_playwright() as pw:
        b = await pw.chromium.launch(headless=True)
        ctx = await b.new_context(user_agent=UA, locale="nl-NL")
        page = await ctx.new_page()
        await page.goto("https://www.facebook.com" + BASIS + "&search_type=page&view_all_page_id=1",
                        wait_until="domcontentloaded", timeout=60000)
        await page.wait_for_timeout(4000)

        for w in todo:
            if calls >= BUDGET or leeg >= 3:
                break
            dom, pid = kaal(w.get("dom")), w.get("pid")
            if not pid:
                # Alleen een domein: zoek de pagina erbij.
                r = None
                try:
                    r = await page.evaluate(HAAL, BASIS + "&search_type=keyword_unordered&q=" + quote(dom))
                except Exception:
                    pass
                calls += 1
                await page.wait_for_timeout(900)
                if not r or not r.get("gevonden"):
                    continue
                if r["count"] and not r["ads"]:
                    leeg += 1
                    continue
                leeg = 0
                eigen = [a for a in r["ads"] if domein_van(a) == dom]
                if not eigen:
                    klaar[dom] = {"wanneer": nu, "bron": w.get("bron", ""), "geen_ad": True, "producten": []}
                    geen += 1
                    bewaar(UIT, klaar)
                    continue
                telling = {}
                for a in eigen:
                    telling[a["pid"]] = telling.get(a["pid"], 0) + 1
                pid = max(telling, key=telling.get)
            r = None
            try:
                r = await page.evaluate(HAAL, BASIS + "&search_type=page&view_all_page_id=" + str(pid))
            except Exception:
                pass
            calls += 1
            await page.wait_for_timeout(900)
            if not r or not r.get("gevonden"):
                continue
            if r["count"] and not r["ads"]:
                leeg += 1
                continue
            leeg = 0
            prod = per_product(r["ads"])
            with ThreadPoolExecutor(6) as ex:
                kop = list(ex.map(detail, [p for p in prod if p["cr"] >= 2][:MAX_DETAIL]))
            klaar[dom] = {"wanneer": nu, "bron": w.get("bron", ""), "pid": str(pid), "actief": r["count"] or 0,
                          "gezien": len(r["ads"]), "producten": prod}
            for p in kop:
                if p["cr"] >= MIN_CR and p["top"] not in metingen:
                    metingen[p["top"]] = {"dom": dom, "handle": p["handle"], "start": p["top_start"], "punten": []}
                    nieuw_meet += 1
            gedaan += 1
            bewaar(UIT, klaar)
            bewaar(METINGEN, metingen)
        await b.close()

    print("calls %d | winkels met antwoord %d | domein zonder ad %d | rem %s"
          % (calls, gedaan, geen, "DICHT" if leeg >= 3 else "open"))
    alle = [p for w in klaar.values() for p in w.get("producten", [])]
    print("producten met een ad (totaal in de werkmap) %d | waarvan %d+ creatives %d | nieuw op de meetlijst %d"
          % (len(alle), MIN_CR, sum(1 for p in alle if p["cr"] >= MIN_CR), nieuw_meet))
    return 0


sys.exit(asyncio.run(main()))

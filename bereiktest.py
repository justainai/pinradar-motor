"""Eenmalige proef: geeft de Ads Library het EU-bereik van een ad aan een datacenter-IP?

Aanleiding (06-10): zoeken werkt hier al (Termenrun), maar het bereik staat niet in
de zoekrespons. Het zit achter twee klikken in het detailvenster van een ad
('Advertentiegegevens bekijken' -> 'Transparantie per locatie'). Op de eigen pc
werkt dat, ongeveer 15 seconden per ad. Werkt het hier ook, dan kan de dagoogst
per product meten hoeveel bereik er de laatste dagen is bijgekomen, en dat is
de enige openbare maat voor wat er aan een ad wordt uitgegeven.

Tweede vraag in dezelfde proef: geeft een winkelpagina hier zijn actieve ads
terug (een call per winkel)? Daarmee weet je op welke producten een winkel
echt adverteert, in plaats van zijn hele catalogus te tonen.

IJKPUNT. De prive-werkmap levert ads/bereik_ijk.json: ad-id's met het bereik
dat op de eigen pc is gemeten, en pagina-id's met hun aantal actieve ads. Bereik
loopt alleen op, dus een ad klopt als het getal hier gelijk of hoger is en niet
meer dan een paar procent per dag afwijkt. Komt er niets terug terwijl de
pagina wel laadt, dan is dat de nul die we ijken.

Uitslag gaat naar de prive-werkmap; in het publieke log staan alleen tellingen,
geen advertenties en geen winkelnamen.

Env: PRIVAAT (pad naar de werkmap), MAX_ADS, MAX_WINKELS.
"""
import json
import os
import re
import time
from datetime import datetime, timezone

from playwright.sync_api import sync_playwright

PRIV = os.environ.get("PRIVAAT", "privaat")
IJK = os.path.join(PRIV, "ads", "bereik_ijk.json")
UIT = os.path.join(PRIV, "uitvoer", "bereiktest.json")
MAX_ADS = int(os.environ.get("MAX_ADS", "12"))
MAX_WINKELS = int(os.environ.get("MAX_WINKELS", "5"))

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/128.0 Safari/537.36")

BEREIK = re.compile(r"(?:Bereik|Reach)\s*\n\s*([\d.,  ]+)", re.IGNORECASE)
KNOP_DETAILS = ["Advertentiegegevens bekijken", "View ad details", "See ad details"]
KNOP_EU = ["Transparantie per locatie", "Transparency by location",
           "Transparantie in de Europese Unie", "European Union transparency"]
COOKIES = ['button:has-text("Alle cookies toestaan")', 'button:has-text("Allow all cookies")',
           'button:has-text("Accept all")', 'button:has-text("Optionele cookies weigeren")',
           'button:has-text("Decline optional cookies")']

WINKEL = """
async (pid) => {
  const u = '/ads/library/?active_status=active&ad_type=all&country=ALL&search_type=page&media_type=all&view_all_page_id=' + pid;
  const h = await fetch(u).then(r => r.text());
  const k = '"search_results_connection":';
  const i = h.indexOf(k);
  if (i < 0) return {gevonden: false, bytes: h.length, botcheck: h.indexOf('__rd_verif') >= 0};
  const m = h.slice(i, i + 400).match(/"count":(\\d+)/);
  const edges = (h.slice(i).match(/"ad_archive_id":"/g) || []).length;
  return {gevonden: true, count: m ? parseInt(m[1]) : null, ids: edges};
}
"""


def klik(page, labels):
    """Klik de eerste knop die bestaat; geeft terug welke tekst werkte."""
    for label in labels:
        for doel in (page.locator('div[role="dialog"]'), page):
            try:
                doel.get_by_text(label, exact=False).first.click(timeout=2500)
                return label
            except Exception:
                continue
    return None


def meet_ad(page, ad_id):
    r = {"geladen": False, "details": None, "eu": None, "bereik": None, "tekens": 0, "botcheck": False}
    url = "https://www.facebook.com/ads/library/?id=%s" % ad_id
    try:
        page.goto(url, wait_until="networkidle", timeout=25000)
    except Exception:
        try:
            page.goto(url, wait_until="domcontentloaded", timeout=25000)
            time.sleep(5)
        except Exception:
            return r
    time.sleep(3)
    for sel in COOKIES:
        try:
            page.click(sel, timeout=1500)
            time.sleep(1)
            break
        except Exception:
            pass
    tekst = page.evaluate("() => document.body.innerText") or ""
    r["tekens"] = len(tekst)
    r["botcheck"] = "__rd_verif" in page.content()
    r["geladen"] = len(tekst) > 400
    r["details"] = klik(page, KNOP_DETAILS)
    time.sleep(2)
    r["eu"] = klik(page, KNOP_EU)
    time.sleep(2)
    tekst = page.evaluate("() => document.body.innerText") or ""
    m = BEREIK.search(tekst)
    if m:
        cijfers = re.sub(r"[^\d]", "", m.group(1))
        if cijfers:
            r["bereik"] = int(cijfers)
    if r["bereik"] is None:
        # Zonder cijfer een stuk paginatekst bewaren (alleen prive), anders is een
        # mislukte proef niet te onderscheiden van een verkeerde knoptekst.
        r["staart"] = tekst[-600:]
        r["kop"] = tekst[:300]
    return r


def main():
    try:
        with open(IJK, encoding="utf-8") as f:
            ijk = json.load(f)
    except Exception:
        print("Geen ijkbestand in de werkmap; niets te meten.")
        return
    ads = ijk.get("ads", [])[:MAX_ADS]
    winkels = ijk.get("winkels", [])[:MAX_WINKELS]
    uit = {"wanneer": datetime.now(timezone.utc).isoformat(timespec="seconds"), "ads": [], "winkels": []}

    with sync_playwright() as p:
        b = p.chromium.launch(headless=True)
        ctx = b.new_context(user_agent=UA, locale="nl-NL")
        page = ctx.new_page()

        for a in ads:
            t = time.time()
            r = meet_ad(page, a["id"])
            r.update(id=a["id"], thuis=a.get("bereik"), thuis_t=a.get("t"), sec=round(time.time() - t))
            uit["ads"].append(r)

        if winkels:
            try:
                page.goto("https://www.facebook.com/ads/library/?active_status=active&ad_type=all"
                          "&country=ALL&search_type=page&view_all_page_id=1",
                          wait_until="domcontentloaded", timeout=40000)
                time.sleep(4)
            except Exception:
                pass
            for w in winkels:
                try:
                    r = page.evaluate(WINKEL, w["pid"])
                except Exception as e:
                    r = {"gevonden": False, "fout": str(e)[:80]}
                r.update(pid=w["pid"], thuis=w.get("actief"))
                uit["winkels"].append(r)
                time.sleep(1.5)
        b.close()

    os.makedirs(os.path.dirname(UIT), exist_ok=True)
    with open(UIT, "w", encoding="utf-8") as f:
        json.dump(uit, f, ensure_ascii=False, indent=1)

    # Publiek log: alleen tellingen.
    n = len(uit["ads"])
    met = [a for a in uit["ads"] if a["bereik"] is not None]
    klopt = [a for a in met if a["thuis"] and a["thuis"] <= a["bereik"] <= a["thuis"] * 1.25]
    print("ADS      gemeten %d | geladen %d | detailknop %d | EU-knop %d | bereik gevonden %d | klopt met thuis %d"
          % (n, sum(a["geladen"] for a in uit["ads"]), sum(1 for a in uit["ads"] if a["details"]),
             sum(1 for a in uit["ads"] if a["eu"]), len(met), len(klopt)))
    print("         botcheck %d | seconden per ad %s"
          % (sum(a["botcheck"] for a in uit["ads"]), sorted(a["sec"] for a in uit["ads"])))
    w = uit["winkels"]
    print("WINKELS  gevraagd %d | antwoord %d | met ads %d | botcheck %d"
          % (len(w), sum(1 for x in w if x.get("gevonden")), sum(1 for x in w if x.get("ids")),
             sum(1 for x in w if x.get("botcheck"))))


if __name__ == "__main__":
    main()

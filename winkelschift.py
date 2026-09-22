"""Schift de winkels die de Termenrun vond -- zonder één Meta-call.

De ruwe vangst is gemengd. Uit de eerste run kwamen echte dropshippers
(lotgenootje.com, topdoek.nl, purelaundryclub.nl) maar ook large.nl met
232.000 likes, een opleidingssite en een foldersite. Die eruit halen hoeft
geen zoekbudget te kosten: de winkel vertelt het zelf.

/products.json op een Shopify-winkel geeft de hele catalogus met prijzen en
publicatiedata. Dat is een call bij de winkel, niet bij Meta, en het beslist
drie dingen in een klap: is het een webshop, hoe groot is het assortiment, en
in welke prijsklasse zit het.

DIT IS EEN LABEL, GEEN ZEEF. Er wordt niets weggegooid -- elke winkel krijgt
een oordeel mee zodat de winkelkeuring daarna weet welke het eerst te doen.
Een categorie of een likes-getal is een neiging, geen bewijs, en Justins eigen
metingen laten zien dat een veto op zo'n kenmerk hem ja's kost.

Env: PRIVAAT (pad naar de werkmap), MAX (hoeveel winkels per run).
"""
import json
import os
import re
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone

PRIV = os.environ.get("PRIVAAT", "privaat")
MAP = os.path.join(PRIV, "ads")
WINKELS = os.path.join(MAP, "winkels_nieuw.json")
MAX = int(os.environ.get("MAX", "400"))

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/128.0 Safari/537.36")

# Paginacategorieen die geen webshop zijn. Dit LABELT alleen -- een winkel met
# zo'n categorie blijft gewoon staan, hij zakt alleen in de volgorde.
GEEN_WINKEL = {
    "media/news company", "education website", "school", "news & media website",
    "community", "community organization", "community center", "public figure",
    "personal blog", "blogger", "artist", "musician/band", "restaurant",
    "hotel", "travel company", "real estate", "insurance company", "bank",
    "software company", "advertising/marketing", "consulting agency",
    "political organization", "church", "sports team", "gym/physical fitness",
}


def laad(pad, leeg):
    try:
        with open(pad, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return leeg


def catalogus(dom):
    """Haal /products.json op. curl, niet requests: Shopify geeft `requests`
    een 429 op het IP-niveau, curl komt er wel door (gemeten eerder)."""
    for pad in ("/products.json?limit=250", "/collections/all/products.json?limit=250"):
        r = subprocess.run(["curl", "-sL", "-A", UA, "--max-time", "20",
                            "https://" + dom + pad], capture_output=True)
        tekst = r.stdout.decode("utf-8", "replace")
        if not tekst.startswith("{"):
            continue
        try:
            prod = json.loads(tekst).get("products")
        except Exception:
            continue
        if isinstance(prod, list):
            return prod
    return None


def beoordeel(dom, w):
    prod = catalogus(dom)
    uit = {"gekeurd_op": datetime.now(timezone.utc).isoformat(timespec="seconds")}

    if prod is None:
        uit["shopify"] = False
        uit["producten"] = None
    else:
        uit["shopify"] = True
        uit["producten"] = len(prod)
        prijzen = []
        for p in prod:
            for v in p.get("variants") or []:
                try:
                    prijzen.append(float(v.get("price")))
                except (TypeError, ValueError):
                    pass
        if prijzen:
            prijzen.sort()
            uit["prijs_mediaan"] = round(prijzen[len(prijzen) // 2], 2)
            uit["prijs_laag"] = round(prijzen[0], 2)
            uit["prijs_hoog"] = round(prijzen[-1], 2)
            # Justins band: onder de 40 is impulsaankoop. Een winkel waar bijna
            # niets daaronder valt, verkoopt iets anders dan wat hij zoekt.
            uit["deel_onder_40"] = round(
                sum(1 for p in prijzen if p <= 40) / len(prijzen), 2)

    cats = [c.lower() for c in (w.get("categorie") or [])]
    likes = w.get("likes")

    redenen = []
    if not uit["shopify"]:
        redenen.append("geen Shopify-catalogus")
    if any(c in GEEN_WINKEL for c in cats):
        redenen.append("categorie is geen webshop")
    if likes is not None and likes > 100000:
        redenen.append("groot merk (%d likes)" % likes)
    if uit.get("producten") is not None and uit["producten"] > 400:
        redenen.append("breed assortiment (%d producten)" % uit["producten"])

    uit["let_op"] = redenen
    # Volgorde voor de winkelkeuring daarna: een kleine Shopify-winkel met
    # betaalbare producten en weinig likes lijkt het meest op wat we zoeken.
    punt = 0
    if uit["shopify"]:
        punt += 3
    if uit.get("deel_onder_40", 0) >= 0.5:
        punt += 2
    if uit.get("producten") is not None and 1 <= uit["producten"] <= 60:
        punt += 2
    if likes is not None and likes < 20000:
        punt += 1
    if w.get("vorm") == "VIDEO":
        punt += 1
    punt -= len(redenen)
    uit["rang"] = punt
    return uit


def main():
    winkels = laad(WINKELS, {})
    todo = [d for d, w in winkels.items() if "shopify" not in w][:MAX]
    print("winkels totaal %d | nog te keuren %d | deze run %d"
          % (len(winkels), sum(1 for w in winkels.values() if "shopify" not in w), len(todo)))
    if not todo:
        return 0

    with ThreadPoolExecutor(8) as ex:
        uitslagen = list(ex.map(lambda d: (d, beoordeel(d, winkels[d])), todo))
    for d, u in uitslagen:
        winkels[d].update(u)
    json.dump(winkels, open(WINKELS, "w", encoding="utf-8"), ensure_ascii=False, indent=0)

    shop = sum(1 for d, u in uitslagen if u["shopify"])
    schoon = sum(1 for d, u in uitslagen if not u["let_op"])
    print("Shopify-catalogus gevonden: %d van %d" % (shop, len(uitslagen)))
    print("zonder enige waarschuwing  : %d" % schoon)
    top = sorted(uitslagen, key=lambda x: -x[1]["rang"])[:10]
    print("\nbeste tien voor de winkelkeuring (rang, producten, mediaanprijs):")
    for d, u in top:
        print("   %-2d %-34s %4s prod  mediaan %-8s %s"
              % (u["rang"], d[:34], u.get("producten"), u.get("prijs_mediaan"),
                 "; ".join(u["let_op"])))
    return 0


sys.exit(main())

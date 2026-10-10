"""Bereik: meet elke dag het EU-bereik van de ads op de meetlijst.

Draait in GitHub Actions. Code hier (publiek), meetlijst en uitslag in de
prive-werkmap; in het publieke log alleen tellingen.

WAAROM. Wat er aan een productad wordt uitgegeven staat nergens openbaar. Wat
de Ads Library wel toont, voor elke ad die in de EU draait, is het bereik:
unieke accounts sinds de start. Een meting geeft dus alleen een gemiddelde
over de hele looptijd, en dat misleidt: op 06-10 stonden 13 van 50 'grote' ads
stil terwijl hun gemiddelde hoog was. Twee metingen op verschillende dagen
geven wat er in de tussentijd is bijgekomen. Dit script legt alleen de
meetpunten vast; het omrekenen naar geld gebeurt in de prive-werkmap.

HOE. Het cijfer staat niet in de zoekrespons. Het zit achter twee klikken in
het detailvenster van een ad, ongeveer 16 seconden per ad. Gemeten vanaf een
runner (Bereikproef, 06-10): 12 van 12 ads gaven hetzelfde getal als thuis.

VOLGORDE. Drie rijen, om en om gemeten in het patroon tweede, nieuw, tweede,
nieuw, volgen:
- tweede: ads met precies een meetpunt waarvan de rusttijd om is. Pas een
  tweede punt geeft een verschil, en zonder verschil valt er niets om te
  rekenen.
- nieuw: ads zonder meetpunt, de laatst gestarte voorop.
- volgen: ads die al twee of meer punten hebben.
Binnen elke rij gaan jonge ads voor (korter dan JONG_DAGEN live): de vraag is
wat er nu getest wordt, niet wat al maanden draait. (Tot 07-10 gingen de nieuwe
voorop en kwam geen enkele ad aan een tweede punt; tot 10-10 gingen drie
hermetingen op een nieuwe, en bleven 573 jonge ads zonder enig punt liggen
terwijl dezelfde ads een derde en vierde keer gemeten werden.) Een ad die na
twee punten minder dan GROEI_MIN bereik per dag wint heeft zijn antwoord al:
die wordt nog maar eens per drie rusttijden gemeten, wat groeit elke rusttijd.
Een ad wordt MAX_DAGEN lang gevolgd na zijn eerste punt. Een ad die twee keer
niets gaf terwijl de ijk-ad wel antwoordde draait buiten de EU en wordt niet
meer gevraagd.

DE IJK. Geven drie ads op rij niets, dan vraagt hij een ad op die eerder wel
een cijfer gaf. Geeft die ook niets, dan zit de Ads Library dicht: hij stopt
en telt de laatste missers niet mee. Zonder die controle sla je een dichte
deur op als 'geen bereik'.

Env: PRIVAAT, MINUTEN (tijdsbudget), MAX_DAGEN, RUST_UUR, JONG_DAGEN, GROEI_MIN.
"""
import json
import os
import re
import sys
import time

from playwright.sync_api import sync_playwright

PRIV = os.environ.get("PRIVAAT", "privaat")
METINGEN = os.path.join(PRIV, "ads", "metingen.json")
MINUTEN = float(os.environ.get("MINUTEN", "90"))
MAX_DAGEN = float(os.environ.get("MAX_DAGEN", "8"))
RUST_UUR = float(os.environ.get("RUST_UUR", "20"))
JONG_DAGEN = float(os.environ.get("JONG_DAGEN", "21"))
GROEI_MIN = float(os.environ.get("GROEI_MIN", "1500"))

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/128.0 Safari/537.36")
BEREIK = re.compile(r"(?:Bereik|Reach)\s*\n\s*([\d.,  ]+)", re.IGNORECASE)
KNOP_DETAILS = ["Advertentiegegevens bekijken", "View ad details", "See ad details"]
KNOP_EU = ["Transparantie per locatie", "Transparency by location"]
COOKIES = ['button:has-text("Alle cookies toestaan")', 'button:has-text("Allow all cookies")',
           'button:has-text("Accept all")']


def klik(page, labels):
    for label in labels:
        for doel in (page.locator('div[role="dialog"]'), page):
            try:
                doel.get_by_text(label, exact=False).first.click(timeout=2500)
                return True
            except Exception:
                continue
    return False


def meet(page, ad_id):
    url = "https://www.facebook.com/ads/library/?id=%s" % ad_id
    try:
        page.goto(url, wait_until="networkidle", timeout=25000)
    except Exception:
        try:
            page.goto(url, wait_until="domcontentloaded", timeout=25000)
            time.sleep(5)
        except Exception:
            return None
    time.sleep(3)
    for sel in COOKIES:
        try:
            page.click(sel, timeout=1500)
            time.sleep(1)
            break
        except Exception:
            pass
    klik(page, KNOP_DETAILS)
    time.sleep(2)
    klik(page, KNOP_EU)
    time.sleep(2)
    m = BEREIK.search(page.evaluate("() => document.body.innerText") or "")
    if not m:
        return None
    cijfers = re.sub(r"[^\d]", "", m.group(1))
    return int(cijfers) if cijfers else None


def volgorde(M, nu):
    """Welke ads nu gemeten worden, in volgorde; plus hoeveel daarvan jong zijn."""
    tweede, nieuw, volgen = [], [], []
    for i, v in M.items():
        p = v.get("punten") or []
        if v.get("mis", 0) >= 2 and not p:
            continue                                   # buiten de EU
        oud = nu - (v.get("start") or 0) > JONG_DAGEN * 86400
        if not p:
            nieuw.append((oud, -(v.get("start") or 0), i))
            continue
        if nu - p[0]["t"] > MAX_DAGEN * 86400:
            continue
        if len(p) == 1:
            if nu - p[-1]["t"] >= RUST_UUR * 3600:
                tweede.append((oud, p[-1]["t"], i))
            continue
        per_dag = (p[-1]["bereik"] - p[-2]["bereik"]) / max(p[-1]["t"] - p[-2]["t"], 3600) * 86400
        groeit = per_dag >= GROEI_MIN
        if nu - p[-1]["t"] >= RUST_UUR * (1 if groeit else 3) * 3600:
            volgen.append((not groeit, oud, p[-1]["t"], i))
    jong_n = sum(1 for o in tweede + nieuw if not o[0]) + sum(1 for o in volgen if not o[1])
    rijen = [[o[-1] for o in sorted(r)] for r in (tweede, nieuw, volgen)]
    todo = []
    while any(rijen):
        for r in (0, 1, 0, 1, 2):
            if rijen[r]:
                todo.append(rijen[r].pop(0))
    return todo, jong_n


def main():
    try:
        with open(METINGEN, encoding="utf-8") as f:
            M = json.load(f)
    except Exception:
        print("Geen meetlijst in de werkmap.")
        return 0

    nu = time.time()
    todo, jong_n = volgorde(M, nu)
    ijk = next((i for i, v in M.items() if v.get("punten")), None)
    print("meetlijst %d | nu te meten %d (waarvan %d opnieuw, %d jonge ads) | tijdsbudget %d min"
          % (len(M), len(todo), sum(1 for i in todo if M[i].get("punten")), jong_n, MINUTEN))
    if not todo:
        return 0

    einde = nu + MINUTEN * 60
    gemeten = gemist = 0
    rij = []
    dicht = False

    def bewaar():
        with open(METINGEN, "w", encoding="utf-8") as f:
            json.dump(M, f, ensure_ascii=False, indent=0)

    with sync_playwright() as pw:
        b = pw.chromium.launch(headless=True)
        page = b.new_context(user_agent=UA, locale="nl-NL").new_page()
        for i in todo:
            if time.time() > einde:
                break
            r = meet(page, i)
            if r:
                M[i].setdefault("punten", []).append({"t": round(time.time()), "bereik": r})
                M[i].pop("mis", None)
                gemeten += 1
                rij = []
            else:
                M[i]["mis"] = M[i].get("mis", 0) + 1
                gemist += 1
                rij.append(i)
                if len(rij) == 3 and ijk and not meet(page, ijk):
                    for j in rij:
                        M[j]["mis"] -= 1
                    gemist -= 3
                    dicht = True
                    break
                if len(rij) >= 3:
                    rij = []
            if (gemeten + gemist) % 10 == 0:
                bewaar()
        b.close()
    bewaar()

    twee = sum(1 for v in M.values() if len(v.get("punten") or []) >= 2)
    print("gemeten %d | zonder cijfer %d | Ads Library %s"
          % (gemeten, gemist, "DICHT, gestopt" if dicht else "open"))
    print("ads met twee of meer meetpunten %d | buiten de EU (overgeslagen) %d"
          % (twee, sum(1 for v in M.values() if v.get("mis", 0) >= 2 and not v.get("punten"))))
    return 0


sys.exit(main())

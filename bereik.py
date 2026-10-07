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

VOLGORDE. Eerst de ads die al een meetpunt hebben en waarvan de rusttijd om
is, het oudste laatste punt voorop: pas een tweede punt geeft een verschil, en
zonder verschil valt er niets om te rekenen. Om de drie zulke ads een ad zonder
meetpunt, zodat nieuwe ads ook aan hun eerste punt komen. (Tot 07-10 gingen de
nieuwe voorop; de winkelstap zet er per run meer bij dan hier gemeten worden,
dus kwam geen enkele ad aan een tweede punt.) Een ad wordt MAX_DAGEN lang
gevolgd na zijn eerste punt. Een ad die twee keer niets gaf terwijl de ijk-ad
wel antwoordde draait buiten de EU en wordt niet meer gevraagd.

DE IJK. Geven drie ads op rij niets, dan vraagt hij een ad op die eerder wel
een cijfer gaf. Geeft die ook niets, dan zit de Ads Library dicht: hij stopt
en telt de laatste missers niet mee. Zonder die controle sla je een dichte
deur op als 'geen bereik'.

Env: PRIVAAT, MINUTEN (tijdsbudget), MAX_DAGEN, RUST_UUR.
"""
import json
import os
import re
import sys
import time

from playwright.sync_api import sync_playwright

PRIV = os.environ.get("PRIVAAT", "privaat")
METINGEN = os.path.join(PRIV, "ads", "metingen.json")
MINUTEN = float(os.environ.get("MINUTEN", "40"))
MAX_DAGEN = float(os.environ.get("MAX_DAGEN", "8"))
RUST_UUR = float(os.environ.get("RUST_UUR", "20"))

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


def main():
    try:
        with open(METINGEN, encoding="utf-8") as f:
            M = json.load(f)
    except Exception:
        print("Geen meetlijst in de werkmap.")
        return 0

    nu = time.time()
    opnieuw, nieuw = [], []
    for i, v in M.items():
        p = v.get("punten") or []
        if v.get("mis", 0) >= 2 and not p:
            continue                                   # buiten de EU
        if not p:
            nieuw.append(i)
        elif nu - p[0]["t"] <= MAX_DAGEN * 86400 and nu - p[-1]["t"] >= RUST_UUR * 3600:
            opnieuw.append((p[-1]["t"], i))
    opnieuw = [i for _, i in sorted(opnieuw)]
    todo = []
    while opnieuw or nieuw:
        todo += opnieuw[:3] + nieuw[:1]
        opnieuw, nieuw = opnieuw[3:], nieuw[1:]
    ijk = next((i for i, v in M.items() if v.get("punten")), None)
    print("meetlijst %d | nu te meten %d (waarvan %d opnieuw) | tijdsbudget %d min"
          % (len(M), len(todo), sum(1 for i in todo if M[i].get("punten")), MINUTEN))
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

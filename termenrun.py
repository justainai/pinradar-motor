"""Termenrun: nieuwe dropship-winkels vinden in de Meta Ads Library.

Draait in GitHub Actions. De code staat hier (publiek), de strategie en de
oogst staan in de prive-werkmap: termenbank.json, bekend.json, en wat de run
oplevert. In het publieke log staan alleen tellingen -- geen zoektermen en
geen winkelnamen.

DAT DIT VANAF EEN RUNNER WERKT IS GEMETEN (Adsproef, 22-09). Kale curl krijgt
403 met JS-botcheck; Playwright headless komt er wel door. Drie zoektermen
waarvan het antwoord vooraf bekend was gaven 15/39/208 tegen 15/38/206 vanaf
de eigen pc -- dezelfde getallen, met een paar verse ads erbij.

DRIE METINGEN STUREN HET ONTWERP (22-09):

1. Een zoekterm doorzoekt ook link_url en caption, niet alleen de tekst.
   Daarom mogen URL-fragmenten in de termenbank staan naast gewone taal.

2. DOORBLADEREN WERKT NIET. page_info geeft een end_cursor en
   has_next_page: true, maar pagina 2, 3 en 4 gaven exact dezelfde 25
   domeinen. 30 ads per zoekterm is een harde grens. De diepte komt dus uit
   VEEL termen en markten, niet uit diep bladeren: een tweede call op
   dezelfde term is weggegooid budget.

3. DE TELLER VOORSPELT DE OPBRENGST NIET. "Buy 2 Get 1 Free" heeft 50.001
   actieve ads en gaf 16 winkels; "OP=OP" heeft 6.888 en gaf er 25. Bij een
   enorme teller zijn de 30 grootste ads van een handvol grote adverteerders
   en herhaalt de lijst zich. Sorteer op wat een term eerder aan NIEUWE
   winkels opleverde, nooit op count.

DE REM. Gemeten 21-09: dicht na ~47 calls, daarna 58-132 minuten niets. Het
budget staat standaard op 40. De rem herken je aan count > 0 met 0 edges;
daar stopt hij en onthoudt waar hij was. Deze runner heeft bovendien een
ander IP dan Justins laptop, dus de twee eten niet van hetzelfde budget.

Env: PRIVAAT (pad naar de werkmap), BUDGET, COOLDOWN.
"""
import asyncio
import json
import os
import re
import sys
from datetime import datetime, timezone, timedelta
from urllib.parse import quote

from playwright.async_api import async_playwright

PRIV = os.environ.get("PRIVAAT", "privaat")
MAP = os.path.join(PRIV, "ads")
BANK = os.path.join(MAP, "termenbank.json")
BEKEND = os.path.join(MAP, "bekend.json")
GESCH = os.path.join(MAP, "termgeschiedenis.json")
WINKELS = os.path.join(MAP, "winkels_nieuw.json")
BUDGET = int(os.environ.get("BUDGET", "40"))
COOLDOWN = int(os.environ.get("COOLDOWN", "7"))

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/128.0 Safari/537.36")

# Een land per markt: NL dekt Belgie, DE dekt Oostenrijk. Een tweede land kost
# een hele call en geeft grotendeels dezelfde winkels.
HOOFDLAND = {"NL": "NL", "DE": "DE", "FR": "FR", "US": "US", "UK": "GB",
             "CA": "CA", "AU": "AU", "ES": "ES", "IT": "IT", "PL": "PL",
             "SE": "SE", "DK": "DK"}

HAAL = """
async (a) => {
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
  const h = await fetch(a.url).then(r => r.text());
  const c = conn(h);
  if (!c) return {gevonden: false, bytes: h.length, botcheck: h.indexOf('__rd_verif') >= 0};
  const ads = (c.edges || []).map(e => {
    const r = e.node.collated_results[0], s = r.snapshot || {};
    return {
      pagina: s.page_name || r.page_name || '', pid: r.page_id,
      caption: s.caption || '', link: (s.link_url || '').split('?')[0],
      titel: (s.title || '').slice(0, 90),
      cats: s.page_categories || [], likes: s.page_like_count,
      cta: s.cta_type || '', vorm: s.display_format || '',
      start: r.start_date, actief: r.is_active ? 1 : 0, cc: r.collation_count || 1,
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


def domein_van(a):
    d = (a.get("caption") or "").lower().strip("/")
    if not d and a.get("link"):
        m = re.match(r"https?://([^/]+)", a["link"])
        d = m.group(1).lower() if m else ""
    d = re.sub(r"^(?:www\.|m\.)", "", d)
    # caption is soms een hele url of een zin; dan is het geen domein
    if not d or "." not in d or " " in d or len(d) > 80:
        return ""
    return d


def kies(bank, gesch, budget, cooldown):
    """Round-robin over de markten; per markt eerst wat eerder werkte, dan wat
    we nog nooit probeerden."""
    nu = datetime.now(timezone.utc)
    per_taal = {}
    for t in bank["termen"]:
        per_taal.setdefault(t["taal"], []).append(t)

    def rust(q, land):
        r = gesch.get("%s|%s" % (q, land))
        if not r:
            return True
        return nu - datetime.fromisoformat(r["op"]) >= timedelta(days=cooldown)

    def score(q, land, taal):
        """Hoeveel nieuwe winkels gaf deze term eerder, IN DIT LAND?

        Een meting telt alleen voor het land waarin hij gedaan is: een term die
        in de VS 6 winkels gaf zegt niets over Denemarken. Binnen dezelfde taal
        telt hij afgezwakt mee. Onbeproefd geeft None -- die komen aan bod in
        de ontdek-beurten.
        """
        r = gesch.get("%s|%s" % (q, land))
        if r:
            return r.get("nieuw", 0)
        b = next((t.get("gemeten") for t in bank["termen"]
                  if t["q"] == q and t.get("gemeten")), None)
        if not b or b.get("nieuw") is None:
            return None
        if b.get("land") == land:
            return b["nieuw"]
        if taal != "*":
            return b["nieuw"] * 0.7
        # URL-handtekeningen gelden niet per taal en scoorden slecht
        # (pages/news 18 actieve ads). Nooit vooraan.
        return None

    rijen = []
    for m in bank["markten"]:
        if m not in HOOFDLAND:
            continue
        land = HOOFDLAND[m]
        taal = bank["markten"][m]["taal"]
        kand = [t for t in per_taal.get(taal, []) + per_taal.get("*", [])
                if rust(t["q"], land)]
        beproefd = sorted([t for t in kand if score(t["q"], land, t["taal"]) is not None],
                          key=lambda t: -score(t["q"], land, t["taal"]))
        # URL-termen achteraan in de ontdek-rij: goedkoop te proberen, maar ze
        # mogen nooit een taal-term verdringen.
        onbeproefd = [t for t in kand if score(t["q"], land, t["taal"]) is None
                      and t["taal"] != "*"]
        onbeproefd += [t for t in kand if score(t["q"], land, t["taal"]) is None
                       and t["taal"] == "*"]
        rijen.append((m, land, beproefd, onbeproefd))

    plan, ronde = [], 0
    while len(plan) < budget:
        gaf_iets = False
        for m, land, beproefd, onbeproefd in rijen:
            if len(plan) >= budget:
                break
            # 60/40: op twee van elke vijf beurten pakken we iets onbeproefds
            uit_nieuw = (ronde % 5) in (2, 4)
            bron = onbeproefd if uit_nieuw and onbeproefd else beproefd
            if not bron:
                bron = onbeproefd or beproefd
            if not bron:
                continue
            t = bron.pop(0)
            plan.append({"markt": m, "land": land, "q": t["q"], "soort": t["soort"]})
            gaf_iets = True
        ronde += 1
        if not gaf_iets:
            break
    return plan


def url(q, land):
    return ("https://www.facebook.com/ads/library/?active_status=active&ad_type=all"
            "&country=" + land + "&search_type=keyword_exact_phrase&media_type=all"
            "&q=" + quote(q))


async def main():
    if not os.path.exists(BANK):
        print("Geen termenbank in %s -- niets te doen." % MAP)
        return 3
    bank = json.load(open(BANK, encoding="utf-8"))
    gesch = laad(GESCH, {})
    winkels = laad(WINKELS, {})
    ken = {re.sub(r"^(?:www\.|m\.)", "", d.lower()) for d in laad(BEKEND, [])}
    ken |= set(winkels)

    plan = kies(bank, gesch, BUDGET, COOLDOWN)
    verdeling = {}
    for p in plan:
        verdeling[p["markt"]] = verdeling.get(p["markt"], 0) + 1
    print("bekende winkels %d | termen %d | plan %d calls"
          % (len(ken), len(bank["termen"]), len(plan)))
    print("markten: " + ", ".join("%s %d" % kv for kv in sorted(verdeling.items())))

    nu = datetime.now(timezone.utc).isoformat(timespec="seconds")
    tot_nieuw = gedaan = 0
    rem = False

    async with async_playwright() as pw:
        browser = await pw.chromium.launch(
            headless=True,
            args=["--disable-blink-features=AutomationControlled", "--no-sandbox"])
        ctx = await browser.new_context(user_agent=UA, locale="en-US",
                                        viewport={"width": 1440, "height": 900})
        page = await ctx.new_page()
        await page.goto("https://www.facebook.com/ads/library/",
                        wait_until="domcontentloaded", timeout=60000)
        await page.wait_for_timeout(4000)

        print("\n%-3s %-4s %-11s %8s %5s %6s" % ("mkt", "land", "soort", "count", "dom", "NIEUW"))
        for p in plan:
            try:
                r = await page.evaluate(HAAL, {"url": url(p["q"], p["land"])})
            except Exception as e:
                print("%-3s %-4s %-11s FOUT %s" % (p["markt"], p["land"], p["soort"], str(e)[:50]))
                continue
            gedaan += 1
            if not r.get("gevonden"):
                print("%-3s %-4s %-11s geen data (botcheck=%s)"
                      % (p["markt"], p["land"], p["soort"], r.get("botcheck")))
                continue
            if r["count"] and not r["ads"]:
                print("\nREM na %d calls (count=%s, 0 ads). Gestopt." % (gedaan, r["count"]))
                rem = True
                break

            nieuw_hier = 0
            for a in r["ads"]:
                d = domein_van(a)
                if not d or d in ken:
                    continue
                ken.add(d)
                nieuw_hier += 1
                winkels[d] = {
                    "pid": a["pid"], "pagina": a["pagina"], "categorie": a["cats"],
                    "likes": a["likes"], "cta": a["cta"], "vorm": a["vorm"],
                    "voorbeeld": a["link"], "titel": a["titel"], "start": a["start"],
                    "varianten": a["cc"],
                    "gevonden_via": "%s | %s" % (p["q"], p["land"]),
                    "gevonden_op": nu,
                }
            alle = {domein_van(a) for a in r["ads"]}
            alle.discard("")
            gesch["%s|%s" % (p["q"], p["land"])] = {
                "op": nu, "count": r["count"], "ads": len(r["ads"]),
                "domeinen": len(alle), "nieuw": nieuw_hier,
            }
            tot_nieuw += nieuw_hier
            # Publiek log: tellingen, geen zoekterm en geen winkelnaam.
            print("%-3s %-4s %-11s %8s %5d %6d"
                  % (p["markt"], p["land"], p["soort"], r["count"], len(alle), nieuw_hier))
            json.dump(gesch, open(GESCH, "w", encoding="utf-8"), ensure_ascii=False, indent=0)
            json.dump(winkels, open(WINKELS, "w", encoding="utf-8"), ensure_ascii=False, indent=0)
            await page.wait_for_timeout(1600)
        await browser.close()

    json.dump(gesch, open(GESCH, "w", encoding="utf-8"), ensure_ascii=False, indent=0)
    json.dump(winkels, open(WINKELS, "w", encoding="utf-8"), ensure_ascii=False, indent=0)
    print("\n%d calls, %d nieuwe winkels (%.1f per call)%s"
          % (gedaan, tot_nieuw, tot_nieuw / max(1, gedaan),
             "  -- gestopt op de rem" if rem else ""))
    print("winkels_nieuw.json bevat nu %d winkels." % len(winkels))
    return 0


sys.exit(asyncio.run(main()))

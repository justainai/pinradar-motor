"""Eenmalige proef: geeft de Meta Ads Library antwoord vanaf een datacenter-IP?

Aanleiding: de Pinterest-oogst levert 55% sier/meubel/mode en bijna geen
ergernis (gemeten 22-09 over 181 producten). De Ads Library levert dat wel,
maar hij draait nu met de hand op Justins eigen pc en eet daar de zoekrem op.
Als GitHub ook antwoord krijgt, hebben we een tweede IP en kan de oogst hier
door terwijl hij zelf zoekt.

Twee routes worden gemeten:
  1. kale curl            -- goedkoop; verwacht 403 met JS-botcheck
  2. Playwright headless  -- werkt op de eigen pc met een zichtbaar venster;
                             of de botcheck headless doorlaat is de vraag

IJKPUNT. Dezelfde drie zoektermen zijn 22-09 vanaf Justins pc gemeten:
    furniture lifter        US  count = 15
    furniture lifting tool  US  count = 38
    contour gauge           US  count = 206
Komt hier hetzelfde getal terug, dan werkt de route. Komt er 0 terug terwijl
de pagina wel laadt, dan is het IP geblokkeerd -- dat is de nul die we ijken.

Uitslag gaat naar de prive-werkmap; in het publieke log staan alleen tellingen
en HTTP-codes, geen winkelnamen en geen advertenties.
"""
import json
import os
import subprocess
from datetime import datetime, timezone

PRIV = os.environ.get("PRIVAAT", "privaat")
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/128.0 Safari/537.36")

IJK = [
    ("furniture lifter", "US", 15),
    ("furniture lifting tool", "US", 38),
    ("contour gauge", "US", 206),
]

BASIS = ("https://www.facebook.com/ads/library/?active_status=active&ad_type=all"
         "&country=%s&search_type=keyword_exact_phrase&media_type=all&q=%s")

# Dezelfde brace-matcher als keurlus2109.py. Geen eval(): Facebooks CSP
# verbiedt unsafe-eval, dus de functie gaat als functie mee, niet als string.
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
  const B = '/ads/library/?active_status=active&ad_type=all&country=' + a.land +
            '&search_type=keyword_exact_phrase&media_type=all&q=' + encodeURIComponent(a.q);
  let c = null, len = 0;
  for (let t = 0; t < 4 && !c; t++) {
    const h = await fetch(B).then(r => r.text());
    len = h.length;
    c = conn(h);
  }
  if (!c) return {gevonden: false, htmlbytes: len};
  return {gevonden: true, htmlbytes: len, count: c.count, edges: (c.edges || []).length};
}
"""


def via_curl(q, land):
    """Route 1. Verwacht 403 + /__rd_verif; meet het, geen aanname."""
    url = BASIS % (land, q.replace(" ", "%20"))
    r = subprocess.run(
        ["curl", "-s", "-L", "-A", UA, "--max-time", "30", "-w", "\n%{http_code}", url],
        capture_output=True)
    tekst, _, code = r.stdout.decode("utf-8", "replace").rpartition("\n")
    return {
        "http": code.strip(),
        "bytes": len(tekst),
        "botcheck": "__rd_verif" in tekst,
        "connection_in_html": '"search_results_connection":' in tekst,
    }


def via_playwright(paren):
    """Route 2. Een browser-context per run, daarna fetch vanuit de pagina."""
    from playwright.sync_api import sync_playwright
    uit = []
    with sync_playwright() as pw:
        browser = pw.chromium.launch(
            headless=True,
            args=["--disable-blink-features=AutomationControlled", "--no-sandbox"])
        ctx = browser.new_context(user_agent=UA, locale="en-US",
                                  viewport={"width": 1440, "height": 900})
        page = ctx.new_page()
        resp = page.goto("https://www.facebook.com/ads/library/",
                         wait_until="domcontentloaded", timeout=60000)
        start = {"http": resp.status if resp else None, "url": page.url}
        page.wait_for_timeout(4000)
        for q, land, verwacht in paren:
            try:
                r = page.evaluate(HAAL, {"q": q, "land": land})
            except Exception as e:
                r = {"fout": str(e)[:160]}
            r["term"] = q
            r["land"] = land
            r["ijk"] = verwacht
            r["klopt"] = bool(r.get("count")) and abs(r["count"] - verwacht) <= max(3, verwacht * 0.25)
            uit.append(r)
            page.wait_for_timeout(1500)
        browser.close()
    return start, uit


curl_uit = [dict(via_curl(q, land), term=q, land=land) for q, land, _ in IJK[:1]]
try:
    start, pw_uit = via_playwright(IJK)
    pw_fout = ""
except Exception as e:
    start, pw_uit, pw_fout = {}, [], str(e)[:300]

uitslag = {
    "gemeten_op": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    "waar": "github-actions",
    "curl": curl_uit,
    "playwright_start": start,
    "playwright": pw_uit,
    "playwright_fout": pw_fout,
}
os.makedirs(os.path.join(PRIV, "uitvoer"), exist_ok=True)
json.dump(uitslag, open(os.path.join(PRIV, "uitvoer", "adstest.json"), "w", encoding="utf-8"),
          ensure_ascii=False, indent=1)

# Publiek log: tellingen en codes, geen winkelnamen en geen advertenties.
for c in curl_uit:
    print("curl   : http %s, %d bytes, botcheck %s, data-in-html %s"
          % (c["http"], c["bytes"], c["botcheck"], c["connection_in_html"]))
if pw_fout:
    print("playwright: GESTRAND -- %s" % pw_fout)
else:
    print("playwright: startpagina http %s" % start.get("http"))
    for r in pw_uit:
        if r.get("fout"):
            print("  %-24s %s  FOUT %s" % (r["term"], r["land"], r["fout"]))
        elif not r.get("gevonden"):
            print("  %-24s %s  geen data in %d bytes html (ijk %d)"
                  % (r["term"], r["land"], r.get("htmlbytes", 0), r["ijk"]))
        else:
            print("  %-24s %s  count=%-5s edges=%-3s  ijk %-4d %s"
                  % (r["term"], r["land"], r["count"], r["edges"], r["ijk"],
                     "KLOPT" if r["klopt"] else "WIJKT AF"))
    goed = sum(1 for r in pw_uit if r.get("klopt"))
    print("\nUITSLAG: %d van de %d termen klopt met de meting van de eigen pc."
          % (goed, len(IJK)))
    print("3 = de route werkt hier; 0 met data = ander antwoord dan thuis; "
          "0 zonder data = IP geblokkeerd.")

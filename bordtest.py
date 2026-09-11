"""Eenmalige proef: werkt doorklikken via BORDEN vanaf een datacenter-IP?

De related-feed geeft op GitHub nul pins terug (gemeten 09-09 t/m 11-09). De
widget-API werkt hier wel: daar meet de motor elke dag de saves mee. Deze proef
vraagt via diezelfde widget-API per zaadpin het bord op, en van dat bord de pins.

Zaad en ontdubbellijst komen uit de prive-werkmap. De uitslag gaat daarheen
terug; in het publieke log staan alleen tellingen.
"""
import glob
import json
import os
import re
import subprocess
from collections import Counter
from datetime import datetime, timezone

PRIV = os.environ.get("PRIVAAT", "privaat")
MAX_BORDEN = int(os.environ.get("BORDEN", "40"))
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/128.0 Safari/537.36")
W = "https://widgets.pinterest.com/v3/pidgets"


def haal(url):
    r = subprocess.run(["curl", "-s", "-L", "-A", UA, "--max-time", "25",
                        "-w", "\n%{http_code}", url], capture_output=True)
    tekst, _, code = r.stdout.decode("utf-8", "replace").rpartition("\n")
    try:
        return code, json.loads(tekst)
    except Exception:
        return code, {}


def norm(k):
    return re.sub(r"^www\.", "", k.lower())


def sleutel(link):
    m = re.match(r"https?://(?:www\.)?([^/]+)/(?:[a-z]{2}(?:-[a-z]{2})?/)?"
                 r"(?:collections/[^/]+/)?products/([^/?#]+)", link or "")
    return norm(m.group(1) + "/" + m.group(2)) if m else None


def saves(p):
    return ((p.get("aggregated_pin_data") or {}).get("aggregated_stats") or {}).get("saves") or 0


g = json.load(open(os.path.join(PRIV, "gezien.json"), encoding="utf-8"))
gezien = {norm(k) for k in (g if isinstance(g, list) else g.get("producten") or [])}

ids = []
for f in sorted(glob.glob(os.path.join(PRIV, "uitvoer", "2026-*_*.json"))):
    try:
        for it in json.load(open(f, encoding="utf-8")):
            m = re.search(r"/pin/(\d+)", it.get("url_bewijs", ""))
            if m:
                ids.append(m.group(1))
    except Exception:
        pass
ids = list(dict.fromkeys(ids))

codes_info, codes_bord = Counter(), Counter()
borden, terug = [], 0
for i in range(0, len(ids), 50):
    code, d = haal(W + "/pins/info/?pin_ids=" + ",".join(ids[i:i + 50]))
    codes_info[code] += 1
    for p in d.get("data") or []:
        if p:
            terug += 1
            if (p.get("board") or {}).get("url"):
                borden.append(p["board"]["url"])
borden = list(dict.fromkeys(borden))

pins = prod = oud = 0
nieuw, per_bord = {}, []
for bu in borden[:MAX_BORDEN]:
    code, d = haal(W + "/boards" + bu.rstrip("/") + "/pins/")
    codes_bord[code] += 1
    bp = (d.get("data") or {}).get("pins") or []
    hier = 0
    for p in bp:
        pins += 1
        k = sleutel(p.get("link"))
        if not k:
            continue
        prod += 1
        if k in gezien:
            oud += 1
            continue
        if saves(p) >= 150:
            if k not in nieuw:
                hier += 1
            nieuw[k] = max(nieuw.get(k, 0), saves(p))
    per_bord.append(hier)

uitslag = {
    "gemeten_op": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    "zaadpins": len(ids), "pins_info_terug": terug, "http_pins_info": dict(codes_info),
    "borden_gevonden": len(borden), "borden_bevraagd": len(per_bord), "http_borden": dict(codes_bord),
    "bordpins": pins, "productpins": prod, "al_gezien": oud,
    "nieuw_150plus": len(nieuw), "nieuw_per_bord": per_bord,
    "nieuw": dict(sorted(nieuw.items(), key=lambda x: -x[1])),
}
os.makedirs(os.path.join(PRIV, "uitvoer"), exist_ok=True)
json.dump(uitslag, open(os.path.join(PRIV, "uitvoer", "bordtest.json"), "w", encoding="utf-8"),
          ensure_ascii=False, indent=1)

# Publiek log: alleen tellingen, geen namen.
print("pins/info: %d van %d terug, http %s" % (terug, len(ids), dict(codes_info)))
print("borden: %d gevonden, %d bevraagd, http %s" % (len(borden), len(per_bord), dict(codes_bord)))
print("bordpins %d, productpins %d, al gezien %d, nieuw >=150: %d" % (pins, prod, oud, len(nieuw)))

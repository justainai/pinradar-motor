"""Eenmalige proef: geeft Pinterest 'Meer ideeen' van een bord terug aan een
datacenter-IP? De bord-ids staan in de prive-werkmap (config/meerideeen.json);
in het publieke log komen alleen tellingen en HTTP-codes.
Run 2: met het productbord erbij (10 paginas)."""
import json, os, re, subprocess, urllib.parse

PRIV = os.environ.get("PRIVAAT", "privaat")
CFG = json.load(open(os.path.join(PRIV, "config", "meerideeen.json"), encoding="utf-8"))
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0 Safari/537.36"
JAR = "_jar.txt"


def curl(args):
    return subprocess.run(["curl", "-s", "-A", UA, "--max-time", "40"] + args,
                          capture_output=True, text=True, encoding="utf-8", errors="ignore").stdout


def winkelpin(p):
    l = p.get("link") or ""
    return bool(re.search(r"/products?/[^/?#]", l)) and "amazon" not in l and "amzn" not in l


uitslag = []
for i, b in enumerate(CFG["borden"]):
    ref = "https://nl.pinterest.com" + b["src"]
    h = curl(["-c", JAR, "-L", ref])
    m = re.search(r'"appVersion"\s*:\s*"([0-9a-f]{6,10})"', h)
    app = m.group(1) if m else ""
    csrf = ""
    for r in open(JAR, encoding="utf-8", errors="ignore"):
        if "csrftoken" in r:
            csrf = r.split()[-1]
    bm, alle, paginas = None, {}, []
    for pagina in range(b.get("paginas", 5)):
        o = {"id": b["id"], "type": "board"}
        if bm:
            o["bookmarks"] = [bm]
        body = "source_url=" + urllib.parse.quote(b["src"]) + "&data=" + urllib.parse.quote(json.dumps({"options": o, "context": {}}))
        out = curl(["-b", JAR, "-X", "POST", "-w", "\n@@HTTP:%{http_code}",
                    "https://nl.pinterest.com/resource/BoardContentRecommendationResource/get/",
                    "-H", "content-type: application/x-www-form-urlencoded",
                    "-H", "x-app-version: " + app, "-H", "x-csrftoken: " + csrf,
                    "-H", "x-pinterest-appstate: active", "-H", "x-requested-with: XMLHttpRequest",
                    "-H", "referer: " + ref, "--data-binary", body])
        romp, _, code = out.rpartition("\n@@HTTP:")
        try:
            d = json.loads(romp)
        except Exception:
            d = {}
        r = d.get("resource_response") or {}
        pins = [x for x in (r.get("data") or []) if isinstance(x, dict) and x.get("type") == "pin"]
        nieuw = [p for p in pins if p["id"] not in alle]
        for p in nieuw:
            alle[p["id"]] = p
        paginas.append({"http": code.strip(), "pins": len(pins), "nieuw": len(nieuw),
                        "winkel": sum(1 for p in nieuw if winkelpin(p))})
        print("bord %d pagina %d: http %s, %d pins, %d nieuw, %d winkelpins"
              % (i, pagina, code.strip(), len(pins), len(nieuw), paginas[-1]["winkel"]))
        bm = r.get("bookmark")
        if not bm or bm == "-end-" or not nieuw:
            break
    w = [p for p in alle.values() if winkelpin(p)]
    print("bord %d: bootstrap %d bytes, app %s, csrf %s -> %d pins, %d winkelpins"
          % (i, len(h), "ja" if app else "nee", "ja" if csrf else "nee", len(alle), len(w)))
    uitslag.append({"bord": b["src"], "paginas": paginas, "pins": len(alle),
                    "winkelpins": [{"id": p["id"], "titel": (p.get("grid_title") or p.get("title") or "")[:90],
                                    "link": p.get("link")} for p in w]})

os.makedirs(os.path.join(PRIV, "uitvoer"), exist_ok=True)
json.dump(uitslag, open(os.path.join(PRIV, "uitvoer", "meertest.json"), "w", encoding="utf-8"),
          ensure_ascii=False, indent=1)

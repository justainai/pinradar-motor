"""Eenmalige proef: geeft Pinterest 'Meer zoals dit' (RelatedModulesResource) per
pin antwoord aan een datacenter-IP? Ter controle ook RelatedPinFeedResource, die
hier in eerdere runs 0 gaf. Zaadpins staan in de prive-werkmap; in het publieke
log alleen tellingen en HTTP-codes. Twee lagen: zaad -> winkelpins -> hun buren."""
import json, os, re, subprocess, time, urllib.parse

PRIV = os.environ.get("PRIVAAT", "privaat")
CFG = json.load(open(os.path.join(PRIV, "config", "meerideeen.json"), encoding="utf-8"))
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0 Safari/537.36"
JAR = "_jar.txt"
AFF = ("amazon", "amzn", "etsy", "ebay", "aliexpress", "temu", "walmart", "instagram", "pinterest")


def curl(args):
    return subprocess.run(["curl", "-s", "-A", UA, "--max-time", "40"] + args,
                          capture_output=True, text=True, encoding="utf-8", errors="ignore").stdout


def winkelpin(p):
    l = p.get("link") or ""
    dom = urllib.parse.urlparse(l).netloc.lower()
    return bool(re.search(r"/products?/[^/?#]", l)) and dom and not any(a in dom for a in AFF)


h = curl(["-c", JAR, "-L", "https://nl.pinterest.com/pin/%s/" % CFG["zaadpins"][0]])
m = re.search(r'"appVersion"\s*:\s*"([0-9a-f]{6,10})"', h)
app = m.group(1) if m else ""
csrf = ""
for r in open(JAR, encoding="utf-8", errors="ignore"):
    if "csrftoken" in r:
        csrf = r.split()[-1]
print("bootstrap %d bytes, app %s, csrf %s" % (len(h), "ja" if app else "nee", "ja" if csrf else "nee"))


def vraag(res, pid):
    o = ({"pin_id": pid, "context_pin_ids": [], "search_query": "", "source": "deep_linking",
          "top_level_source": "deep_linking", "top_level_source_depth": 1, "is_pdp": False}
         if res == "RelatedModulesResource" else
         {"pin": pid, "add_vase": True, "field_set_key": "unauth_react", "page_size": 24})
    body = "source_url=" + urllib.parse.quote("/pin/%s/" % pid) + "&data=" + urllib.parse.quote(json.dumps({"options": o, "context": {}}))
    out = curl(["-b", JAR, "-X", "POST", "-w", "\n@@HTTP:%{http_code}",
                "https://nl.pinterest.com/resource/%s/get/" % res,
                "-H", "content-type: application/x-www-form-urlencoded",
                "-H", "x-app-version: " + app, "-H", "x-csrftoken: " + csrf,
                "-H", "x-pinterest-appstate: active", "-H", "x-requested-with: XMLHttpRequest",
                "-H", "referer: https://nl.pinterest.com/pin/%s/" % pid, "--data-binary", body])
    romp, _, code = out.rpartition("\n@@HTTP:")
    try:
        data = (json.loads(romp).get("resource_response") or {}).get("data")
    except Exception:
        data = None
    if isinstance(data, dict):
        data = data.get("results") or data.get("items") or []
    pins = [x for x in (data or []) if isinstance(x, dict) and x.get("type") == "pin"]
    time.sleep(0.8)
    return code.strip(), pins


uitslag = {}
for res in ("RelatedModulesResource", "RelatedPinFeedResource"):
    gezien, winkel, laag2, codes = set(CFG["zaadpins"]), {}, [], []
    for laag, zaad in ((1, CFG["zaadpins"]), (2, None)):
        if laag == 2:
            zaad = list(winkel)[:15]
        n0 = len(winkel)
        for pid in zaad:
            code, pins = vraag(res, pid)
            codes.append(code)
            for p in pins:
                if p["id"] in gezien:
                    continue
                gezien.add(p["id"])
                if winkelpin(p):
                    winkel[p["id"]] = {"titel": (p.get("grid_title") or p.get("title") or "")[:90],
                                       "link": p.get("link"), "laag": laag}
        print("%s laag %d: %d verzoeken, http %s, %d pins gezien, %d winkelpins erbij"
              % (res, laag, len(zaad), sorted(set(codes)), len(gezien), len(winkel) - n0))
    uitslag[res] = {"pins": len(gezien), "winkelpins": list(winkel.values())}

os.makedirs(os.path.join(PRIV, "uitvoer"), exist_ok=True)
json.dump(uitslag, open(os.path.join(PRIV, "uitvoer", "simtest.json"), "w", encoding="utf-8"),
          ensure_ascii=False, indent=1)

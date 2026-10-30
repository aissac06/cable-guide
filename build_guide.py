#!/usr/bin/env python3
"""Build guide.json for the Cable apps: what's on now and next on each channel.

Reads channels.txt ("number|name shown|playlist name|pluto id|guide id"), downloads free
XMLTV listings, matches each channel, and writes:
  guide.json        {"updated": <unix>, "ch": {"5": [[start, stop, "Title", "Episode"], ...]}}
  guide-report.txt  which listing each channel matched (or NOT FOUND)
Only the Python standard library is used.
"""
import gzip, json, re, sys, time, urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime

SOURCES = [   # (label, url, used for name matching?)
    ("us-locals", "https://epgshare01.online/epgshare01/epg_ripper_US_LOCALS1.xml.gz"),
    ("us",        "https://epgshare01.online/epgshare01/epg_ripper_US2.xml.gz"),
    ("us1",       "https://epgshare01.online/epgshare01/epg_ripper_US1.xml.gz"),
    ("us-sports", "https://epgshare01.online/epgshare01/epg_ripper_US_SPORTS1.xml.gz"),
    ("samsung",   "https://i.mjh.nz/SamsungTVPlus/us.xml.gz"),
    ("plex",      "https://i.mjh.nz/Plex/us.xml.gz"),
    ("roku",      "https://i.mjh.nz/Roku/all.xml.gz"),
    ("pluto",     "https://i.mjh.nz/PlutoTV/us.xml.gz"),
]
BACK, AHEAD, MAX_PER_CH = 2 * 3600, 12 * 3600, 10
DROP = {"hd", "east", "the"}
LOOSE = {"channel", "network", "tv", "west", "pacific", "entertainment", "television"}
ALIASES = {"lifetimemovies": ["lmn", "lifetimemovienetwork"], "e": ["eentertainment", "eentertainmenttelevision"],
           "tcm": ["turnerclassicmovies"], "own": ["oprahwinfreynetwork"], "nickjr": ["nickjunior"],
           "tbn": ["trinitybroadcastingnetwork"], "mtv2": ["mtv2hd"]}

def score(cid, names, want_west):
    """Lower is better: we're on the East Coast, and skip alternate/backup listings."""
    t = (cid + " " + " ".join(names)).lower()
    west = "pacific" in t or "west" in t
    sc = 0 if west == want_west else 10
    if "alternate" in t or "alt." in t:
        sc += 5
    return sc

def keys(s):
    """Ways to spell a channel name for matching: strict, and without words like channel/network/tv."""
    s = s.lower().replace("&", " and ").replace("+", " plus ")
    s = re.sub(r"\(.*?\)|\[.*?\]", " ", s)
    words = [w for w in re.split(r"[^a-z0-9]+", s) if w and w not in DROP]
    out = ["".join(words)]
    loose = "".join(w for w in words if w not in LOOSE)
    if loose and loose not in out:
        out.append(loose)
    return [k for k in out if k]

def callsign(name):
    m = re.search(r"\b([KW][A-Z]{2,3})\b", name)
    return m.group(1) if m else ""

def when(s):
    try:
        return int(datetime.strptime(s.strip()[:20], "%Y%m%d%H%M%S %z").timestamp())
    except Exception:
        return 0

def load_channels(path):
    out = []
    for line in open(path, encoding="utf-8"):
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        f = (line.split("|") + ["", "", "", "", ""])[:5]
        num, shown, plname, pluto, gid = [x.strip() for x in f]
        out.append({"num": num, "shown": shown, "plname": plname or shown, "pluto": pluto.lower(),
                    "gid": gid, "call": callsign(plname or shown), "match": None, "progs": []})
    return out

def fetch(url):
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 (cable-guide)"})
    with urllib.request.urlopen(req, timeout=180) as r:
        data = r.read()
    if data[:2] == b"\x1f\x8b":
        data = gzip.decompress(data)
    return data

def pick(chans, label, names_by_id):
    """Decide which of this source's channel ids we want, for channels still unmatched."""
    want = {}
    index = {}
    for cid, names in names_by_id.items():
        for n in names + [cid.rsplit(".", 1)[0].replace(".", " ")]:
            for k in keys(n):
                if cid not in index.setdefault(k, []):
                    index[k].append(cid)

    def best(k, want_west):
        return min(index[k], key=lambda i: score(i, names_by_id.get(i, []), want_west))
    for c in chans:
        if c["match"]:
            continue
        if c["gid"].lower() == "none":
            continue        # "none" in channels.txt: this stream doesn't follow any listing
        if c["pluto"] and label != "pluto":
            continue        # a Pluto stream has its own lineup: only Pluto's listings are right for it
        cid = None
        if c["gid"] and c["gid"] in names_by_id:
            cid = c["gid"]
        elif c["pluto"] and label == "pluto":
            cid = next((i for i in names_by_id if c["pluto"] in i.lower()), None)
        if not cid and c["call"] and label in ("us-locals", "us"):
            for i, names in names_by_id.items():
                if any(re.match(c["call"] + r"(\b|-|$)", n.upper()) for n in names + [i.upper()]):
                    cid = i
                    break
        if not cid:
            ww = "west" in (c["shown"] + " " + c["plname"]).lower()
            tries = []
            for n in (c["shown"], c["plname"]):
                for k in keys(n):
                    tries += [k] + ALIASES.get(k, [])
            hit = next((best(k, ww) for k in tries if k in index), None)
            if hit:
                cid = hit
        if cid:
            c["match"] = (label, cid)
            want.setdefault(cid, []).append(c)
    return want

def read_source(chans, label, url, now):
    try:
        data = fetch(url)
    except Exception as e:
        print(f"  {label}: could not download ({e})")
        return
    names_by_id, want = {}, None
    lo, hi = now - BACK, now + AHEAD
    count = 0
    for ev, el in ET.iterparse(__import__("io").BytesIO(data), events=("end",)):
        if el.tag == "channel":
            names_by_id[el.get("id", "")] = [d.text or "" for d in el.findall("display-name")]
            el.clear()
        elif el.tag == "programme":
            if want is None:
                want = pick(chans, label, names_by_id)
            targets = want.get(el.get("channel", ""))
            if targets:
                a, b = when(el.get("start", "")), when(el.get("stop", ""))
                if b > lo and a < hi:
                    t = (el.findtext("title") or "").strip()
                    sub = (el.findtext("sub-title") or "").strip()
                    for c in targets:
                        c["progs"].append([a, b, t, sub])
                        count += 1
            el.clear()
    if want is None:
        want = pick(chans, label, names_by_id)
    print(f"  {label}: {len(names_by_id)} channels listed, matched {len(want)}, {count} shows")

def main():
    chans = load_channels(sys.argv[1] if len(sys.argv) > 1 else "channels.txt")
    now = int(time.time())
    print(f"{len(chans)} channels to find")
    for label, url in SOURCES:
        read_source(chans, label, url, now)
    # A second feed of a channel shares the first one's listings.
    for c in chans:
        if not c["progs"]:
            twin = next((o for o in chans if o is not c and o["progs"] and not c["pluto"] and not o["pluto"] and c["gid"].lower() != "none" and keys(c["plname"]) and keys(o["plname"])[:1] == keys(c["plname"])[:1]), None)
            if twin:
                c["progs"] = list(twin["progs"])
                c["match"] = ("same as", twin["num"])
    out = {"updated": now, "ch": {}}
    rep = []
    for c in chans:
        ps = sorted({(p[0], p[1], p[2], p[3]) for p in c["progs"]})
        ps = [list(p) for p in ps if p[1] > now - 600][:MAX_PER_CH]
        if ps:
            out["ch"][c["num"]] = ps
        m = c["match"]
        now_show = next((p[2] for p in ps if p[0] <= now < p[1]), "")
        rep.append(f'{c["num"]:>4}  {c["shown"]:<28} ' + (f'{m[0]}: {m[1]}   now: {now_show}' if m and ps else ("found but no shows listed: " + f"{m[0]}: {m[1]}" if m else "NOT FOUND")))
    json.dump(out, open("guide.json", "w", encoding="utf-8"), ensure_ascii=False, separators=(",", ":"))
    open("guide-report.txt", "w", encoding="utf-8").write("\n".join(rep) + "\n")
    print(f'guide.json: {len(out["ch"])} of {len(chans)} channels have listings')

if __name__ == "__main__":
    main()

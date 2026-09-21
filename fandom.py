import os, sys, subprocess

# 1. Auto-install missing packages
REQUIRED = {
    'requests': 'requests', 
    'bs4': 'beautifulsoup4', 
    'gspread': 'gspread', 
    'google.oauth2': 'google-auth'
}

for module_name, pip_name in REQUIRED.items():
    try:
        __import__(module_name)
    except ImportError:
        print(f"Installing missing library: {pip_name}... (this may take a moment)")
        subprocess.check_call([sys.executable, "-m", "pip", "install", pip_name])

# 2. Now safely import everything your script needs
import re, time, json, requests, gspread
from bs4 import BeautifulSoup, Tag
from google.oauth2.service_account import Credentials

SPREADSHEET_ID  = "1Fpp_sJbGjuKxUAcWhZOM1YqHJJrUHTWE56lO4dthhak"
SHEET_NAME      = "fandom"

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
SERVICE_ACCOUNT = os.path.join(SCRIPT_DIR, "service_account.json")
CACHE_FILE      = "fandom_cache.json"
API             = "https://projectsekai.fandom.com/api.php"
DELAY           = 0.4

IS_GITHUB_ACTIONS = os.environ.get("GITHUB_ACTIONS") == "true"

S = requests.Session()
S.headers.update({"User-Agent": "Mozilla/5.0"})

def fetch(page, retries=3):
    for i in range(retries):
        try:
            r = S.get(API, params={"action":"parse","page":page,"prop":"text","format":"json"}, timeout=20)
            return r.json()
        except:
            if i < retries-1: time.sleep(1.5)
    return None

MONTHS = {"january":"01","february":"02","march":"03","april":"04","may":"05","june":"06",
          "july":"07","august":"08","september":"09","october":"10","november":"11","december":"12"}

def normalize_date(t):
    t = re.sub(r"\s*[\[\(]\s*\d+\s*[\]\)][\[\(\d\]\)]*$", "", t).strip()
    m = re.match(r"([A-Za-z]+)\s+(\d{1,2}),?\s+(\d{4})", t)
    if m:
        mon = MONTHS.get(m.group(1).lower())
        if mon: return f"{int(m.group(2)):02d}/{mon}/{m.group(3)}"
    m = re.match(r"^(\d{1,2})/(\d{1,2})/(\d{4})$", t)
    if m: return f"{int(m.group(2)):02d}/{int(m.group(1)):02d}/{m.group(3)}"
    return t

def clean(tag):
    if tag is None: return "-"
    t = tag.get_text(" ", strip=True) if isinstance(tag, Tag) else str(tag)
    t = re.sub(r"\s+", " ", t).strip()
    t = t.replace("\u2014", "-").replace("\u2013", "-")
    return t if t else "-"

def get_field(ib, *sources):
    if not ib: return "-"
    for src in sources:
        tag = ib.find(attrs={"data-source": src})
        if tag:
            v = tag.find(class_=re.compile(r"pi-data-value"))
            if v:
                t = clean(v)
                if t and t != "-": return t
    return "-"

def get_date_field(ib, *sources):
    t = get_field(ib, *sources)
    if t == "-": return "-"
    return normalize_date(t)

def FANDOM():
    print("=== FANDOM() started ===")

    cache = {}
    if IS_GITHUB_ACTIONS:
        print("  Running on GitHub Actions: Cache ignored.")
    elif os.path.exists(CACHE_FILE):
        with open(CACHE_FILE, "r", encoding="utf-8") as f:
            cache = json.load(f)
        print(f"  Cache loaded: {len(cache)} songs already stored")

    print("Fetching song list...")
    raw = fetch("Song_List")
    if not raw or "parse" not in raw:
        print("[error] Could not fetch Song_List"); sys.exit(1)

    html = raw["parse"]["text"]["*"].replace("724&#91;1&#93;", "724")
    soup = BeautifulSoup(html, "html.parser")
    song_rows, section = [], ""

    for tag in soup.find_all(["h2","tbody"]):
        if tag.name == "h2":
            h = tag.find(class_="mw-headline")
            if h: section = h.get_text(strip=True).replace(" Songs","")
            continue
        for tr in tag.find_all("tr"):
            cells = tr.find_all("td")
            if len(cells) < 6: continue
            sid_raw = clean(cells[0])
            sid = re.sub(r"[\[\]\s]", "", sid_raw)
            if re.match(r"^-+$", sid): sid = "TBA"
            if not sid or len(sid) > 15: continue
            if not re.match(r"^(\d+|TBA)$", sid): continue
            link = cells[2].find("a", href=re.compile(r"^/wiki/"))
            url  = ("https://projectsekai.fandom.com" + link["href"]) if link else ""
            title = BeautifulSoup(clean(cells[2]), "html.parser").get_text()
            song_rows.append([
                sid, title, url, clean(cells[4]),
                clean(cells[5]), clean(cells[6]),
                clean(cells[7]) if len(cells)>7 else "-",
                clean(cells[8]) if len(cells)>8 else "-",
                clean(cells[9]) if len(cells)>9 else "-",
                section
            ])

    song_rows.sort(key=lambda r: (0, int(r[0])) if r[0].isdigit() else (1, r[1]))
    print(f"  -> {len(song_rows)} songs")

    EMPTY = ["-"]*22
    meta_rows = []
    new_entries = 0

    new_total = sum(1 for r in song_rows if f"{r[0]}|{r[1]}" not in cache)
    new_count = 0

    for row in song_rows:
        sid, title = row[0], row[1]
        cache_key = f"{sid}|{title}"

        if not IS_GITHUB_ACTIONS and cache_key in cache:
            meta_rows.append(cache[cache_key])
            continue

        new_count += 1
        print(f"  [{new_count}/{new_total}] {title}")
        time.sleep(DELAY)

        data = fetch(title)
        html2 = None
        for candidate in [title, title+" (song)", title.replace("[","(").replace("]",")")]:
            d = fetch(candidate) if candidate != title else data
            if d and "parse" in d:
                h = d["parse"]["text"]["*"]
                if 'class="portable-infobox' in h:
                    html2 = h; break

        if html2 is None:
            meta_rows.append(EMPTY[:])
            cache[cache_key] = EMPTY[:]
            new_entries += 1
            continue

        sp = BeautifulSoup(html2, "html.parser")
        ib = sp.find("aside", class_=re.compile(r"portable-infobox"))

        romanized = "-"
        if ib:
            pt = ib.find("h2", class_=re.compile(r"pi-title"))
            if pt: romanized = clean(pt)

        english = get_field(ib, "english", "official english")
        chinese = get_field(ib, "simp cn")
        hangul  = get_field(ib, "hangul")
        bpm     = get_field(ib, "bpm")

        dur = get_field(ib, "duration")
        full_len = game_len = "-"
        if dur != "-":
            mf = re.search(r"(\d+:\d{2})\s*\(Full\)", dur, re.I)
            mg = re.search(r"(\d+:\d{2})\s*\(Game(?:\s*Size)?\)", dur, re.I)
            if mf: full_len = mf.group(1)
            if mg: game_len = mg.group(1)
            if full_len == "-" and game_len == "-":
                ma = re.search(r"(\d+:\d{2})", dur)
                if ma: full_len = ma.group(1)

        append = ["-"]*5
        for idx, srcs in enumerate([
            ["append_date","app_date"], ["append_tw_date","app_tw_date"],
            ["append_en_date","app_en_date"], ["append_kr_date","app_kr_date"],
            ["append_cn_date","app_cn_date"]
        ]):
            append[idx] = get_date_field(ib, *srcs)

        if ib:
            panel = ib.find("section", class_=re.compile(r"wds-tabber"))
            if panel:
                for ti, block in enumerate(panel.find_all("div", class_=re.compile(r"wds-tab__content"))[:5]):
                    for lbl in block.find_all(class_=re.compile(r"pi-data-label")):
                        if lbl.get_text(strip=True).upper() == "APPEND":
                            v = lbl.find_next(class_=re.compile(r"pi-data-value"))
                            if v: append[ti] = normalize_date(clean(v))

        intro = "-"
        aside = sp.find("aside")
        if aside:
            for p in aside.find_all_next("p"):
                t = clean(p)
                if len(t) > 30: intro = t; break

        trivia_items = []
        th = sp.find(id="Trivia")
        if th:
            parent = th.find_parent()
            if parent:
                ul = parent.find_next_sibling("ul") or parent.find_next("ul")
                if ul:
                    for li in ul.find_all("li"): trivia_items.append(clean(li))
        trivia = " | ".join(trivia_items) if trivia_items else "-"

        KNOWN_DIFFS   = {"EASY","NORMAL","HARD","EXPERT","MASTER","APPEND"}
        AUDIO_ORDER   = ["Full Version","English Version","Instrumental"]
        KNOWN_CHARS   = {
            "Hoshino Ichika","Tenma Saki","Mochizuki Honami","Hinomori Shiho",
            "Hanasato Minori","Kiritani Haruka","Momoi Airi","Hinomori Shizuku",
            "Azusawa Kohane","Shiraishi An","Shinonome Akito","Aoyagi Toya",
            "Tenma Tsukasa","Otori Emu","Kusanagi Nene","Kamishiro Rui",
            "Yoisaki Kanade","Asahina Mafuyu","Shinonome Ena","Akiyama Mizuki",
            "Hatsune Miku","Kagamine Rin","Kagamine Len","Megurine Luka","MEIKO","KAITO"
        }

        def fix_artist(a):
            a = re.sub(r"VIRTUAL SINGER 1$", "VIRTUAL SINGER", a)
            a = re.sub(r" and ", ", ", a)
            a = re.sub(r"\s+&\s+", ", ", a)
            a = a.replace("All Original Units","ALL ORIGINAL UNITS")
            a = a.replace("Project SEKAI x Ensemble Stars","Project SEKAI × Ensemble Stars!!")
            a = re.sub(r"25 ji,|25-ji,", "25-ji", a)
            a = re.sub(r"Nightcord de\b(?!\.)", "Nightcord de.", a)
            a = a.replace("Kyujitsu, Shumijin Doushi de.","Kyujitsu Shumijin Doushi de.")
            return a

        audio_entries = []
        ah = sp.find(id="Audio")
        if ah:
            parent_h2 = ah.find_parent()
            tbl = parent_h2.find_next("table") if parent_h2 else None
            if tbl:
                for tr in tbl.find_all("tr"):
                    cells = tr.find_all("td")
                    if len(cells) < 2: continue
                    src_tag = tr.find("source") or tr.find("audio")
                    if not src_tag: continue
                    aurl = src_tag.get("src", "-")
                    if "static.wikia.nocookie.net" not in aurl or ".ogg" not in aurl:
                        continue
                    raw_ver = clean(cells[1]) if len(cells) > 1 else "Unknown"
                    dur2    = "-"
                    if len(cells) > 2:
                        dc = clean(cells[2])
                        if re.match(r"^\d+:\d{2}$", dc): dur2 = dc

                    open_c  = raw_ver.count("(")
                    close_c = raw_ver.count(")")
                    if open_c > close_c:
                        raw_ver += ")" * (open_c - close_c)

                    all_parens = re.findall(r"\(([^)]*)\)", raw_ver)
                    version = raw_ver
                    for inner in reversed(all_parens):
                        if ("Version" in inner or "Instrumental" in inner
                                or inner.startswith("Full/Game")
                                or re.match(r"^Game\s*[-–]", inner)
                                or re.match(r"^Game\s+Version", inner)):
                            version = inner
                            break

                    oc2 = version.count("("); cc2 = version.count(")")
                    if oc2 > cc2: version += ")" * (oc2 - cc2)

                    version = re.sub(r"Full\/Game Size", "Full/Game Version", version, flags=re.I)
                    version = re.sub(r"^Game(?!\s+Version)(\s+-)", r"Game Version\1", version)

                    parts      = version.split(" - ")
                    ver_type   = parts[0].strip().replace("(","").replace(")","").strip()
                    artist_raw = " - ".join(parts[1:]).strip().replace("(","").replace(")","").strip() if len(parts) > 1 else ""
                    artist_str = fix_artist(artist_raw)

                    audio_entries.append({
                        "versionType": ver_type,
                        "artist":      artist_str,
                        "url":         aurl,
                        "duration":    dur2,
                    })

        non_instrumental_indices = [
            i for i, e in enumerate(audio_entries)
            if e["versionType"] not in ("Instrumental","Menu Instrumental","Menu Instrumental - New")
        ]
        versions_map = {}
        vh = sp.find(id="Versions")
        if vh:
            vs_parent = vh.find_parent()
            if vs_parent:
                tab_contents = []
                nxt = vs_parent
                while nxt:
                    nxt = nxt.find_next_sibling()
                    if not nxt: break
                    if nxt.name and re.match(r"h[12]", nxt.name): break
                    tab_contents += nxt.find_all("div", class_=re.compile(r"wds-tab__content")) if hasattr(nxt, "find_all") else []

                for vti, block in enumerate(tab_contents):
                    unit2 = ""
                    first_row = block.find("tr")
                    if first_row:
                        link_tag = first_row.find("a", href=re.compile(r"/wiki/"))
                        if link_tag:
                            candidate = link_tag.get("title","") or link_tag.get_text(strip=True)
                            if candidate.lower() in {c.lower() for c in KNOWN_CHARS}:
                                unit2 = "OTHER"
                            else:
                                unit2 = candidate
                    singers = []
                    for b_tag in block.find_all("b"):
                        a_tag = b_tag.find("a")
                        if a_tag:
                            sname = re.sub(r"^w:c:[^:]+:", "", a_tag.get_text(strip=True))
                            sname = sname.replace("&amp;","&").strip()
                            if sname != unit2:
                                singers.append(sname)
                    orig_idx = non_instrumental_indices[vti] if vti < len(non_instrumental_indices) else None
                    if orig_idx is not None:
                        versions_map[orig_idx] = {"unit": unit2 or "OTHER", "singers": ",".join(singers)}

        audio_archive, audio_normal, audio_special = [], [], []
        for i, e in enumerate(audio_entries):
            e["origIdx"] = i
            if e["artist"] == "VBS Archive":
                audio_archive.append(e)
            elif e["versionType"] in AUDIO_ORDER:
                audio_special.append((AUDIO_ORDER.index(e["versionType"]), e))
            else:
                audio_normal.append(e)
        audio_special.sort(key=lambda x: x[0])
        audio_sorted = audio_normal + [x[1] for x in audio_special] + audio_archive

        audio_parts, audio_parts_new = [], []
        non_menu_count = 0
        for e in audio_sorted:
            vt   = e["versionType"]
            is_menu     = (vt == "Menu Instrumental")
            is_menu_new = (vt in ("Menu Instrumental","Menu Instrumental - New")
                           and e["artist"] == "New") or vt == "Menu Instrumental - New"
            if not is_menu:
                vdata   = versions_map.get(e["origIdx"], {"unit":"","singers":""})
                singers2 = vdata["singers"]
                unit3    = vdata["unit"] or "OTHER"
            else:
                singers2 = ""
                unit3    = ""
            part = f"{vt};{e['artist']};{e['duration']};{e['url']};{singers2};{unit3}"
            if is_menu_new:
                audio_parts_new.append("99;" + part)
            elif is_menu:
                audio_parts.append("00;" + part)
            else:
                non_menu_count += 1
                id_str = f"{non_menu_count:02d}"
                audio_parts.append(id_str + ";" + part)

        all_parts = audio_parts + audio_parts_new

        used = {e["origIdx"] for e in audio_sorted}
        ver_tab_labels = []
        if vh:
            vs_parent2 = vh.find_parent()
            if vs_parent2:
                for a_tag in vs_parent2.find_all("a", class_=re.compile(r"wds-tabs__tab")):
                    ver_tab_labels.append(a_tag.get_text(strip=True))
                if not ver_tab_labels:
                    for div in vs_parent2.find_all("div", class_=re.compile(r"wds-tabs__tab-label")):
                        a2 = div.find("a")
                        if a2: ver_tab_labels.append(a2.get_text(strip=True))
        for ni, orig_idx2 in enumerate(non_instrumental_indices):
            if orig_idx2 not in used and orig_idx2 in versions_map:
                vdata2    = versions_map[orig_idx2]
                tab_label = ver_tab_labels[ni] if ni < len(ver_tab_labels) else "-"
                non_menu_count += 1
                id_str2 = f"{non_menu_count:02d}"
                all_parts.append(f"{id_str2};-;{tab_label};-;;{vdata2['singers']};{vdata2['unit'] or 'OTHER'}")

        audio_links = ("[" + ";".join(all_parts) + "]") if all_parts else "-"

        lyrics = {}
        lh = sp.find(id="Lyrics")
        if lh:
            lparent = lh.find_parent()
            labels = []
            tw = lparent.find_next(class_=re.compile(r"wds-tabs__wrapper")) if lparent else None
            if tw:
                for a in tw.find_all("a"): labels.append(a.get_text(strip=True))
            poems = []
            if lparent:
                for el in lparent.find_all_next():
                    if not isinstance(el, Tag): continue
                    if el.name and re.match(r"h[12]", el.name): break
                    if el.attrs is None: continue
                    if "poem" in (el.attrs.get("class") or []):
                        for span in el.find_all("span", class_=re.compile(r"copy")): span.decompose()
                        text = re.sub(r"[ \t]+"," ", el.get_text("\n"))
                        text = re.sub(r"\n{3,}","\n\n", text).strip()
                        if len(text) > 5: poems.append(text)
            for idx2, lbl in enumerate(labels):
                if idx2 < len(poems): lyrics[lbl] = poems[idx2]

        meta = [
            romanized, english, chinese, hangul, bpm,
            append[0], append[1], append[2], append[3], append[4],
            intro, trivia, full_len, game_len, audio_links,
            lyrics.get("Romaji","-"), lyrics.get("Japanese","-"),
            lyrics.get("English","-"), lyrics.get("Korean","-"),
            lyrics.get("Romanja","-"), lyrics.get("Pinyin","-"),
            lyrics.get("Chinese","-"),
        ]
        meta_rows.append(meta)
        cache[cache_key] = meta
        new_entries += 1

    if not IS_GITHUB_ACTIONS:
        with open(CACHE_FILE, "w", encoding="utf-8") as f:
            json.dump(cache, f, ensure_ascii=False, indent=2)
        print(f"  Cache saved ({new_entries} new entries)")
    else:
        print("  Running on GitHub Actions: Cache save skipped.")

if __name__ == "__main__":
    import traceback
    try:
        FANDOM()
    except Exception as e:
        print("\n" + "="*40)
        print("CRASH LOG:")
        traceback.print_exc()
        print("="*40 + "\n")
    finally:
        if not IS_GITHUB_ACTIONS:
            input("Press Enter to exit...")
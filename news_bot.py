import html
import json
import os
import re
import sys
import time
from datetime import datetime, timezone
import urllib.error
import urllib.request
from pathlib import Path

API = "https://discord.com/api/v10"
BOT_UA = "DiscordBot (https://github.com/Nalathan01/nalathan-news-bot, 1.0)"
WEB_UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) NalathanNewsBot/1.0"
ROOT = Path(__file__).resolve().parent
CONFIG = json.loads((ROOT / "config.json").read_text(encoding="utf-8"))
STATE_FILE = ROOT / "state.json"
NEWS_FILE = ROOT / "news.json"
SITE_FILE = ROOT / "site.json"
DRY = "--dry" in sys.argv
INIT = "--init" in sys.argv
MODHUB_MOD = "https://www.farming-simulator.com/mod.php?lang=de&country=de&mod_id={}&title=fs2025"
FBM_FILE = "https://forbidden-mods.de/filebase/file/{}/"
COLORS = {"fbm": 0xC0392B, "modhub": 0x2E8B57, "github": 0x5865F2}


def fetch(url, headers=None):
    req = urllib.request.Request(url, headers={"User-Agent": WEB_UA, **(headers or {})})
    for attempt in range(3):
        try:
            with urllib.request.urlopen(req, timeout=30) as r:
                return r.read().decode("utf-8", "replace")
        except urllib.error.HTTPError:
            raise
        except (urllib.error.URLError, TimeoutError):
            if attempt == 2:
                raise
            time.sleep(5)


def clean(fragment):
    text = re.sub(r"<br\s*/?>", "\n", fragment)
    text = re.sub(r"<li[^>]*>", "\n• ", text)
    text = re.sub(r"</(p|ul|ol|li|div)>", "\n", text)
    text = html.unescape(re.sub(r"<[^>]+>", "", text))
    lines = [re.sub(r"^\s*[-*]\s+", "• ", l.strip()) for l in text.splitlines()]
    text = "\n".join(l for l in lines if l)
    return text if len(text) <= 1500 else text[:1500].rsplit("\n", 1)[0] + "\n…"


def fbm_entries():
    page = fetch(f"https://forbidden-mods.de/filebase/user-file-list/{CONFIG['fbmUser']}/")
    urls = sorted(set(re.findall(r'href="(https://forbidden-mods\.de/filebase/file/\d+-[^"/]+/)"', page)))
    for url in urls:
        s = fetch(url)
        title = re.search(r'<meta property="og:title" content="([^"]+?)\s-\sForbidden Mods', s)
        image = re.search(r'<meta property="og:image" content="([^"]+)"', s)
        block = s.split('<li class="filebaseVersion', 1)
        if not title or len(block) < 2:
            continue
        block = block[1].split('filebaseVersionLikesSummary', 1)[0]
        version = re.search(r">\s*Version\s+([0-9][^<\s]*)", block)
        if not version:
            continue
        notes = re.search(r'<div class="containerContent htmlContent">(.*?)</div>', block, re.S)
        file_id = re.search(r"/file/(\d+)-", url).group(1)
        mh = CONFIG.get("modhubForFbm", {}).get(file_id)
        yield {
            "key": f"fbm:{file_id}", "version": version.group(1), "source": "fbm",
            "name": html.unescape(title.group(1)), "url": url, "channel": "ls25",
            "notes": re.sub(r"^Changelog[^\n]*:\n", "", clean(notes.group(1))) if notes else "",
            "image": image.group(1) if image else None,
            "links": [("ModHub", MODHUB_MOD.format(mh))] if mh else []}


def modhub_entries():
    ids = []
    for page in range(5):
        s = fetch(f"https://www.farming-simulator.com/mods.php?lang=de&country=de&title=fs2025&filter=org&org_id={CONFIG['modhubOrg']}&page={page}")
        found = [i for i in dict.fromkeys(re.findall(r"mod\.php\?[^\"']*?mod_id=(\d+)", s)) if i not in ids]
        if not found:
            break
        ids += found
    for mod_id in ids:
        url = MODHUB_MOD.format(mod_id)
        s = fetch(url)
        flat = re.sub(r"\s+", " ", s)
        title = re.search(r"<h2[^>]*>([^<]+)</h2>", s)
        version = re.search(r'<b>Version</b></div>\s*<div class="table-cell">([^<]+)', flat)
        if not title or not version or f"org_id={CONFIG['modhubOrg']}&" not in flat:
            continue
        version = version.group(1).strip()
        notes = ""
        m = re.search(r"Changelog\s*" + re.escape(version) + r"\s*:?(.*?)(?:Changelog\s*[0-9]|</p>|</div>)", flat, re.S)
        if m:
            notes = clean(m.group(1))
        image = re.search(r'src="(https://cdn\d*\.giants-software\.com/modHub/storage/\d+/screenshot0\.jpg)"', s)
        yield {
            "key": f"modhub:{mod_id}", "version": version, "source": "modhub",
            "name": html.unescape(title.group(1).strip()), "url": url, "channel": "ls25",
            "notes": notes, "image": image.group(1) if image else None,
            "links": [("Forbidden Mods", FBM_FILE.format(f)) for f, m in CONFIG.get("modhubForFbm", {}).items() if m == mod_id]}


def github_entries():
    headers = {"Accept": "application/vnd.github+json"}
    if os.environ.get("GITHUB_TOKEN"):
        headers["Authorization"] = "Bearer " + os.environ["GITHUB_TOKEN"]
    for repo in CONFIG["githubRepos"]:
        try:
            rel = json.loads(fetch(f"https://api.github.com/repos/{repo['repo']}/releases/latest", headers))
        except urllib.error.HTTPError as e:
            if e.code == 404:
                continue
            raise
        yield {
            "key": f"github:{repo['repo']}", "version": rel["tag_name"].lstrip("v"), "source": "github",
            "name": repo["name"], "url": rel["html_url"], "channel": repo["channel"],
            "notes": f"Was sich geändert hat, steht in den [Release Notes]({rel['html_url']}).",
            "image": None, "links": repo.get("links", [])}


def discord(method, path, body=None):
    req = urllib.request.Request(API + path, data=json.dumps(body).encode() if body is not None else None, method=method, headers={
        "Authorization": "Bot " + os.environ["DISCORD_NEWS_BOT_TOKEN"].strip().lstrip("﻿"), "User-Agent": BOT_UA, "Content-Type": "application/json"})
    while True:
        try:
            with urllib.request.urlopen(req, timeout=30) as r:
                txt = r.read().decode()
                return json.loads(txt) if txt else {}
        except urllib.error.HTTPError as e:
            if e.code == 429:
                time.sleep(json.loads(e.read().decode()).get("retry_after", 1) + 0.5)
                continue
            raise RuntimeError(f"Discord {e.code} {method} {path}: {e.read().decode()[:300]}")


def build(entry, is_new):
    where = {"fbm": "Forbidden Mods", "modhub": "ModHub", "github": "GitHub"}[entry["source"]]
    if entry["source"] == "modhub":
        head = "Neu im ModHub" if is_new else "Update im ModHub"
    elif entry["source"] == "fbm":
        head = "Neuer Mod auf Forbidden Mods" if is_new else "Update auf Forbidden Mods"
    else:
        head = "Neue Version"
    links = [f"[{where}]({entry['url']})"] + [f"[{t}]({u})" for t, u in entry.get("links", [])]
    desc = (entry["notes"] + "\n\n" if entry["notes"] else "") + "**Download:** " + " · ".join(links)
    embed = {"author": {"name": head}, "title": f"{entry['name']} {entry['version']}", "url": entry["url"],
             "description": desc, "color": COLORS[entry["source"]]}
    if entry["image"]:
        embed["thumbnail"] = {"url": entry["image"]}
    return {"embeds": [embed], "allowed_mentions": {"parse": []}}


def main():
    first_run = INIT or not STATE_FILE.exists()
    state = {} if not STATE_FILE.exists() else json.loads(STATE_FILE.read_text(encoding="utf-8"))
    entries, failed = [], []
    for source in (fbm_entries, modhub_entries, github_entries):
        try:
            entries += list(source())
        except Exception as e:
            failed.append(f"{source.__name__}: {e}")
    news = json.loads(NEWS_FILE.read_text(encoding="utf-8")) if NEWS_FILE.exists() else []
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    for entry in entries:
        known = state.get(entry["key"])
        if known == entry["version"]:
            continue
        if not first_run and not DRY:
            news.insert(0, {"date": now, "key": entry["key"], "name": entry["name"], "version": entry["version"],
                            "source": entry["source"], "game": entry["channel"], "new": known is None,
                            "url": entry["url"], "image": entry["image"]})
        if not first_run:
            message = build(entry, known is None)
            if DRY:
                print(json.dumps(message, ensure_ascii=False, indent=1))
                continue
            channel = CONFIG["channels"][entry["channel"]]
            msg = discord("POST", f"/channels/{channel}/messages", message)
            try:
                discord("POST", f"/channels/{channel}/messages/{msg['id']}/crosspost")
            except RuntimeError as e:
                failed.append(str(e))
            time.sleep(1)
        print(("bekannt" if first_run else "gemeldet"), entry["key"], entry["name"], entry["version"])
        state[entry["key"]] = entry["version"]
    if not DRY:
        NEWS_FILE.write_text(json.dumps(news[:300], indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
        if not failed:
            site = {e["key"]: {"name": e["name"], "version": e["version"], "source": e["source"], "game": e["channel"],
                               "url": e["url"], "image": e["image"], "links": e.get("links", [])} for e in entries}
            SITE_FILE.write_text(json.dumps({"updated": now, "items": dict(sorted(site.items()))}, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
        STATE_FILE.write_text(json.dumps(dict(sorted(state.items())), indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
    for f in failed:
        print("FEHLER", f, file=sys.stderr)
    if failed and not entries:
        sys.exit(1)


if __name__ == "__main__":
    main()

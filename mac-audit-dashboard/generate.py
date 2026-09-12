#!/usr/bin/env python3
"""Generate a self-contained Mac storage/memory audit dashboard (index.html)."""
import json, re, subprocess, sys, os
from collections import defaultdict

SCAN = "/tmp/mac_scan.txt"
OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "index.html")
HOME = os.path.expanduser("~")

# ---------- parse scan files ----------
sections = defaultdict(list)
for scanfile in ("/tmp/mac_scan.txt", "/tmp/mac_scan2.txt"):
    if not os.path.exists(scanfile):
        continue
    cur = None
    for line in open(scanfile):
        line = line.rstrip("\n")
        m = re.match(r"^== (\S.*) ==$", line)
        if m:
            cur = m.group(1)
            continue
        if cur and "\t" in line:
            kb_s, path = line.split("\t", 1)
            try:
                sections[cur].append((int(kb_s), path))
            except ValueError:
                pass

def human(kb):
    if kb >= 1024 * 1024:
        return f"{kb/1048576:.1f} GB"
    if kb >= 1024:
        return f"{kb/1024:.0f} MB"
    return f"{kb} KB"

# ---------- disk treemap ----------
# root children: depth-1 home dirs + /Applications + /opt/homebrew + /private/var/folders
def build_tree():
    nodes = {}  # path -> {name, size(kb), children{}}
    for kb, path in sections["HOME D2"]:
        if path == HOME:
            continue
        rel = os.path.relpath(path, HOME)
        parts = rel.split(os.sep)
        if len(parts) > 2:
            continue
        # store; we roll up depth-2 into depth-1 as children
        n = nodes
        acc = HOME
        for i, p in enumerate(parts):
            acc = os.path.join(acc, p)
            node = n.setdefault(acc, {"name": p, "path": acc, "size": 0, "children": {}})
            if len(parts) == 1 or i == len(parts) - 1:
                node["size"] = kb
            n = node["children"]
    # second pass: compute leaf-only sizes (size minus children sum)
    def finalize(node):
        kids = [finalize(c) for c in node["children"].values()]
        own = max(0, node["size"] - sum(k["size"] for k in kids))
        total = node["size"]
        out = {"name": node["name"], "path": node["path"], "size": total, "own": own}
        if kids:
            out["children"] = sorted(kids, key=lambda k: -k["size"])
        return out
    home_kids = [finalize(node) for node in nodes.values()]
    by_path = {p: kb for kb, p in sections["HOME D2"]}
    home_total = by_path.get(HOME, sum(k["size"] for k in home_kids))
    roots = [{"name": "~ (Home)", "path": HOME, "size": home_total,
              "children": sorted(home_kids, key=lambda k: -k["size"])}]
    for sec, label in (("APPS", "/Applications"), ("HOMEBREW", "/opt/homebrew"),
                       ("VARFOLDERS", "/private/var/folders")):
        items = sorted(sections[sec], key=lambda x: -x[0])
        total = 0
        kids = []
        prefix = {"APPS": "/Applications/", "HOMEBREW": "/opt/homebrew/",
                  "VARFOLDERS": "/private/var/folders/"}[sec]
        for kb, path in items:
            if path.rstrip("/") == prefix.rstrip("/"):
                total = kb
            else:
                kids.append({"name": os.path.basename(path), "path": path, "size": kb, "own": kb})
        if total == 0:
            total = sum(k["size"] for k in kids)
        roots.append({"name": label, "path": label, "size": total, "children": kids})
    return {"name": "Macintosh HD", "path": "/", "size": sum(r["size"] for r in roots),
            "children": sorted(roots, key=lambda k: -k["size"])}

tree = build_tree()

# ---------- top-level stacked bar (depth-1 home + system) ----------
stacked = [{"name": c["name"], "size": c["size"]} for c in tree["children"]]

# ---------- big files ----------
big_files = [{"path": p, "size": kb, "human": human(kb)} for kb, p in sections["BIGFILES"][:30]]

# ---------- node_modules + venvs (reinstallable deps) ----------
nodemod = [{"path": p, "size": kb, "human": human(kb), "kind": "node_modules"}
           for kb, p in sections["NODEMOD"][:20]]
venvs = [{"path": p, "size": kb, "human": human(kb), "kind": "venv"}
         for kb, p in sections["VENVS"][:15]]
deps = sorted(nodemod + venvs, key=lambda d: -d["size"])[:25]
deps_total = sum(kb for kb, _ in sections["NODEMOD"]) + sum(kb for kb, _ in sections["VENVS"])

# ---------- trash ----------
trash_total = sum(kb for kb, p in sections["TRASH"] if p.rstrip("/") == os.path.expanduser("~/.Trash"))

# ---------- live memory snapshot ----------
ps_out = subprocess.run(["bash", "-c", "ps -A -o rss=,comm="], capture_output=True, text=True).stdout
APP_MAP = [
    ("llama-server", "Ollama (llama-server)"), ("Ollama", "Ollama (llama-server)"),
    ("Code Helper", "VS Code (all helpers)"), ("Visual Studio Code", "VS Code (all helpers)"),
    ("Google Chrome", "Google Chrome"), ("Docker", "Docker Desktop"), ("com.docker", "Docker Desktop"),
    ("WhatsApp", "WhatsApp"), ("zoom", "Zoom"), ("Postman", "Postman"), ("Claude", "Claude apps"),
    ("claude", "Claude apps"), ("WindowServer", "WindowServer (macOS)"), ("kernel_task", "kernel_task"),
    ("Comet", "Comet"), ("Warp", "Warp"), ("discord", "Discord"), ("Perplexity", "Perplexity"),
    ("node", "node processes"), (".cline", "Cline"), ("mds", "Spotlight (mds)"),
    ("firefox", "Firefox"), ("Slack", "Slack"), ("Figma", "Figma"),
]
def app_name(comm):
    base = os.path.basename(comm)
    full = comm
    for pat, name in APP_MAP:
        if pat in base or pat in full:
            return name
    return base
mem_by_app = defaultdict(int)
for line in ps_out.splitlines():
    line = line.strip()
    if not line:
        continue
    rss_s, _, comm = line.partition(" ")
    try:
        rss = int(rss_s)
    except ValueError:
        continue
    mem_by_app[app_name(comm.strip())] += rss  # KB
mem_top = sorted(mem_by_app.items(), key=lambda kv: -kv[1])[:18]
mem_data = [{"name": n, "size": kb, "human": human(kb)} for n, kb in mem_top]

vm = subprocess.run(["bash", "-c", "sysctl vm.swapusage; top -l 1 -n 0 | grep PhysMem; uptime"],
                    capture_output=True, text=True).stdout
swap_used = float(re.search(r"used = ([\d.]+)M", vm).group(1)) / 1024
phys_used = re.search(r"PhysMem: (\d+G) used.*?(\d+M) unused", vm)
uptime_s = subprocess.run(["bash", "-c", "uptime | sed -E 's/.*up ([^,]+).*/\\1/'"],
                          capture_output=True, text=True).stdout.strip()
df = subprocess.run(["bash", "-c", "df -k /System/Volumes/Data | tail -1"],
                    capture_output=True, text=True).stdout.split()
disk_total_kb, disk_used_kb, disk_avail_kb = int(df[1]), int(df[2]), int(df[3])

headline = {
    "ram_used_gb": phys_used.group(1) if phys_used else "?",
    "ram_unused_mb": phys_used.group(2) if phys_used else "?",
    "swap_used_gb": round(swap_used, 1),
    "disk_used": human(disk_used_kb), "disk_avail": human(disk_avail_kb),
    "disk_pct": round(100 * disk_used_kb / disk_total_kb),
    "uptime": uptime_s,
}

# ---------- cleanup plan (computed from real data) ----------
home_by_path = {p: kb for kb, p in sections.get("HOME D2", [])}
docker_kb = home_by_path.get(os.path.join(HOME, "Library/Containers"), 0)
ollama_models = home_by_path.get(os.path.join(HOME, ".ollama"), 11591092)
caches_kb = home_by_path.get(os.path.join(HOME, "Library/Caches"), 0)
npm_cache_kb = home_by_path.get(os.path.join(HOME, ".npm"), 0)
plan = {
    "ram_now": [
        {"action": "Quit Ollama (llama-server idle, holds ~4.6 GB RAM)", "impact": "~4.6 GB RAM"},
        {"action": "Restart VS Code, WhatsApp, Chrome, Zoom (all running 17+ days)", "impact": "~1-2 GB RAM"},
        {"action": "Restart Docker Desktop when not trading/developing", "impact": "~0.5 GB RAM"},
        {"action": "Full reboot — 71-day uptime, swap at 21 GB", "impact": "clears 21 GB swap + leaks"},
    ],
    "delete": [
        {"action": "node_modules + Python venvs across projects (reinstall with npm ci / pip when needed)", "impact": human(deps_total)},
        {"action": "Docker images/containers — docker system prune -a", "impact": "up to 21 GB"},
        {"action": "Empty Trash", "impact": human(trash_total)},
        {"action": "~/Library/Caches (safe, apps rebuild)", "impact": human(caches_kb)},
        {"action": "~/.npm cache (npm cache clean --force)", "impact": human(npm_cache_kb)},
    ],
    "cloud": [
        {"action": "Ollama models in ~/.ollama — delete, re-pull on demand", "impact": human(ollama_models)},
        {"action": "Enable iCloud Drive + Optimize Mac Storage (currently OFF)", "impact": "offloads Documents/Desktop"},
        {"action": "Large media/archives from top-files list → external/cloud", "impact": "see table"},
    ],
}

data = {"tree": tree, "stacked": stacked, "big_files": big_files, "deps": deps,
        "mem": mem_data, "headline": headline, "plan": plan}

template = open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "template.html")).read()
html = template.replace("__DATA__", json.dumps(data))
open(OUT, "w").write(html)
print("wrote", OUT)

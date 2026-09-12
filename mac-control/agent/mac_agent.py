#!/usr/bin/env python3
"""
mac-control host agent — stdlib-only HTTP agent bound to 127.0.0.1:9999.

Collects real macOS stats (memory, swap, CPU, processes, disk) and executes
a WHITELISTED set of host actions (quit app, kill own process, sleep,
restart, shutdown, Docker control, caches/trash cleanup).

The Docker container can't touch macOS — this agent is the bridge.
All endpoints except /api/health require header: X-Agent-Token.
"""
import json, os, re, shlex, signal, subprocess, sys, threading, time
from collections import defaultdict
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

PORT = 9999
HERE = os.path.dirname(os.path.abspath(__file__))
TOKEN_FILE = os.path.join(HERE, "..", ".agent-token")
SCAN_RAW = os.path.join(HERE, "scan_raw.txt")
SCAN_CACHE = os.path.join(HERE, "scan_cache.json")
HOME = os.path.expanduser("~")
MY_UID = os.getuid()
import pwd
MY_USER = pwd.getpwuid(MY_UID).pw_name

def load_token():
    tok = os.environ.get("AGENT_TOKEN", "").strip()
    if tok:
        return tok
    try:
        return open(TOKEN_FILE).read().strip()
    except OSError:
        return ""

TOKEN = load_token()

def run(cmd, timeout=30, shell=True):
    try:
        p = subprocess.run(cmd, shell=shell, capture_output=True, text=True, timeout=timeout)
        return p.returncode, (p.stdout + p.stderr).strip()
    except subprocess.TimeoutExpired:
        return 124, f"timed out after {timeout}s"
    except Exception as e:
        return 1, str(e)

def human(kb):
    if kb >= 1024 * 1024: return f"{kb/1048576:.1f} GB"
    if kb >= 1024: return f"{kb/1024:.0f} MB"
    return f"{kb} KB"

# ---------------------------------------------------------------- stats ----
def get_stats():
    s = {}
    _, out = run("sysctl -n hw.memsize")
    total_bytes = int(out.strip() or 0)
    s["ram_total_gb"] = round(total_bytes / 1e9, 1)

    _, vm = run("vm_stat")
    page = 16384
    m = re.search(r"page size of (\d+) bytes", vm)
    if m: page = int(m.group(1))
    raw = {}
    for line in vm.splitlines():
        mm = re.match(r"^(.+?):\s+(\d+)\.$", line.strip())
        if mm:
            raw[mm.group(1).lower()] = int(mm.group(2)) * page
    anon = raw.get("anonymous pages", 0)
    purgeable = raw.get("pages purgeable", 0)
    s["mem"] = {
        "wired_gb":      round(raw.get("pages wired down", 0) / 1e9, 2),
        "compressed_gb": round(raw.get("pages occupied by compressor", 0) / 1e9, 2),
        "app_gb":        round(max(0, anon - purgeable) / 1e9, 2),
        "cache_gb":      round((raw.get("file-backed pages", 0) + raw.get("pages speculative", 0) + purgeable) / 1e9, 2),
        "free_gb":       round(raw.get("pages free", 0) / 1e9, 2),
    }
    _, sw = run("sysctl vm.swapusage")
    m = re.search(r"total = ([\d.]+)M\s+used = ([\d.]+)M\s+free = ([\d.]+)M", sw)
    if m:
        s["swap"] = {"total_gb": round(float(m.group(1))/1024, 2),
                     "used_gb": round(float(m.group(2))/1024, 2)}
    _, top = run("top -l 1 -n 0 | grep -E 'CPU usage|PhysMem'")
    m = re.search(r"CPU usage: ([\d.]+)% user, ([\d.]+)% sys, ([\d.]+)% idle", top)
    if m:
        s["cpu"] = {"user": float(m.group(1)), "sys": float(m.group(2)), "idle": float(m.group(3))}
    _, up = run("uptime")
    s["uptime_raw"] = up.strip()
    m = re.search(r"up (.+?),\s+\d+ users?.*averages?: ([\d.]+) ([\d.]+) ([\d.]+)", up)
    if m:
        s["uptime"] = m.group(1)
        s["load"] = [float(m.group(2)), float(m.group(3)), float(m.group(4))]
    _, batt = run("pmset -g batt")
    m = re.search(r"(\d+)%", batt)
    if m: s["battery_pct"] = int(m.group(1))
    s["charging"] = ("AC Power" in batt) or ("charging" in batt.lower())
    _, df = run("df -k /System/Volumes/Data | tail -1")
    parts = df.split()
    if len(parts) >= 4:
        s["disk"] = {"total_gb": round(int(parts[1])/1048576, 1),
                     "used_gb": round(int(parts[2])/1048576, 1),
                     "avail_gb": round(int(parts[3])/1048576, 1)}
    _, hn = run("hostname")
    s["hostname"] = hn.strip()
    s["ts"] = time.time()
    return s

def get_processes(limit=30):
    _, out = run("ps -A -o pid=,rss=,pcpu=,etime=,user=,comm=")
    procs = []
    for line in out.splitlines():
        parts = line.strip().split(None, 5)
        if len(parts) < 6:
            continue
        pid, rss, pcpu, etime, user, comm = parts
        try:
            pid_i, rss_i = int(pid), int(rss)
        except ValueError:
            continue
        # derive app bundle for GUI apps: /Applications/Foo.app/...
        m = re.match(r"(/(?:Applications|System/Applications)/[^/]+\.app)", comm)
        app = os.path.basename(m.group(1))[:-4] if m else None
        procs.append({"pid": pid_i, "rss_kb": rss_i, "rss": human(rss_i),
                      "cpu": float(pcpu), "etime": etime, "user": user,
                      "name": os.path.basename(comm), "app": app,
                      "own": user == MY_USER})
    procs.sort(key=lambda p: -p["rss_kb"])
    return procs[:limit]

# ----------------------------------------------------------------- scan ----
scan_state = {"state": "idle", "started": None, "finished": None, "error": None}

SCAN_SCRIPT = r"""
{ echo "== HOME D2 =="; du -x -k -d 2 ~ 2>/dev/null;
  echo "== HOMEBREW =="; du -x -k -d 1 /opt/homebrew 2>/dev/null;
  echo "== APPS =="; du -x -k -d 1 /Applications 2>/dev/null;
  echo "== VARFOLDERS =="; du -x -k -d 2 /private/var/folders 2>/dev/null;
  echo "== TRASH =="; du -x -k -d 1 ~/.Trash 2>/dev/null;
  echo "== BIGFILES =="; find -x ~ -type f -size +500M 2>/dev/null | while read f; do du -k "$f" 2>/dev/null; done | sort -rn | head -60;
  echo "== NODEMOD =="; find -x ~ -type d -name node_modules -prune 2>/dev/null | while read d; do du -sk "$d" 2>/dev/null; done | sort -rn | head -40;
  echo "== VENVS =="; find -x ~ -maxdepth 6 -type d \( -name ".venv*" -o -name "venv" \) -prune 2>/dev/null | while read d; do du -sk "$d" 2>/dev/null; done | sort -rn | head -15;
  echo "== DONE =="; } > "%s" 2>&1
""" % SCAN_RAW

def parse_scan(path):
    sections = defaultdict(list)
    cur = None
    for line in open(path, errors="replace"):
        line = line.rstrip("\n")
        m = re.match(r"^== (\S.*) ==$", line)
        if m:
            cur = m.group(1); continue
        if cur and "\t" in line:
            kb_s, p = line.split("\t", 1)
            try: sections[cur].append((int(kb_s), p))
            except ValueError: pass

    def build_tree():
        nodes = {}
        for kb, p in sections["HOME D2"]:
            if p == HOME: continue
            parts = os.path.relpath(p, HOME).split(os.sep)
            if len(parts) > 2: continue
            n = nodes; acc = HOME
            for i, part in enumerate(parts):
                acc = os.path.join(acc, part)
                node = n.setdefault(acc, {"name": part, "path": acc, "size": 0, "children": {}})
                if len(parts) == 1 or i == len(parts) - 1:
                    node["size"] = kb
                n = node["children"]
        def finalize(node):
            kids = [finalize(c) for c in node["children"].values()]
            own = max(0, node["size"] - sum(k["size"] for k in kids))
            out = {"name": node["name"], "path": node["path"],
                   "size": node["size"], "own": own}
            if kids: out["children"] = sorted(kids, key=lambda k: -k["size"])
            return out
        kids = [finalize(n) for n in nodes.values()]
        by_path = {p: kb for kb, p in sections["HOME D2"]}
        home_total = by_path.get(HOME, sum(k["size"] for k in kids))
        roots = [{"name": "~ (Home)", "path": HOME, "size": home_total,
                  "children": sorted(kids, key=lambda k: -k["size"])}]
        for sec, label in (("APPS", "/Applications"), ("HOMEBREW", "/opt/homebrew"),
                           ("VARFOLDERS", "/private/var/folders")):
            total, s2 = 0, []
            pref = label.rstrip("/")
            for kb, p in sorted(sections[sec], key=lambda x: -x[0]):
                if p.rstrip("/") == pref: total = kb
                else: s2.append({"name": os.path.basename(p), "path": p, "size": kb, "own": kb})
            if total == 0: total = sum(k["size"] for k in s2)
            roots.append({"name": label, "path": label, "size": total, "children": s2})
        return {"name": "Macintosh HD", "path": "/",
                "size": sum(r["size"] for r in roots),
                "children": sorted(roots, key=lambda k: -k["size"])}

    tree = build_tree()
    by_path = {p: kb for kb, p in sections.get("HOME D2", [])}
    return {
        "tree": tree,
        "stacked": [{"name": c["name"], "size": c["size"]} for c in tree["children"]],
        "big_files": [{"path": p, "size": kb, "human": human(kb)} for kb, p in sections["BIGFILES"][:30]],
        "deps": sorted(
            [{"path": p, "size": kb, "human": human(kb), "kind": "node_modules"} for kb, p in sections["NODEMOD"][:20]] +
            [{"path": p, "size": kb, "human": human(kb), "kind": "venv"} for kb, p in sections["VENVS"][:15]],
            key=lambda d: -d["size"])[:25],
        "totals": {
            "trash_kb": sum(kb for kb, p in sections["TRASH"] if p.rstrip("/") == os.path.join(HOME, ".Trash")),
            "caches_kb": by_path.get(os.path.join(HOME, "Library/Caches"), 0),
            "npm_kb": by_path.get(os.path.join(HOME, ".npm"), 0),
            "ollama_kb": by_path.get(os.path.join(HOME, ".ollama"), 0),
            "docker_kb": by_path.get(os.path.join(HOME, "Library/Containers"), 0),
            "deps_kb": sum(kb for kb, _ in sections["NODEMOD"]) + sum(kb for kb, _ in sections["VENVS"]),
        },
        "generated": time.time(),
    }

def _rescan_worker():
    scan_state.update(state="running", started=time.time(), error=None)
    rc, out = run(SCAN_SCRIPT, timeout=1800)
    if rc != 0 and not os.path.exists(SCAN_RAW):
        scan_state.update(state="idle", error=out or f"scan exited {rc}")
        return
    try:
        cache = parse_scan(SCAN_RAW)
        with open(SCAN_CACHE, "w") as f:
            json.dump(cache, f)
        scan_state.update(state="idle", finished=time.time())
    except Exception as e:
        scan_state.update(state="idle", error=str(e))

def get_scan():
    try:
        with open(SCAN_CACHE) as f:
            data = json.load(f)
        data["scan_status"] = scan_state
        return data
    except OSError:
        if os.path.exists(SCAN_RAW):
            try:
                cache = parse_scan(SCAN_RAW)
                with open(SCAN_CACHE, "w") as f:
                    json.dump(cache, f)
                cache["scan_status"] = scan_state
                return cache
            except Exception as e:
                return {"status": "parse_error", "error": str(e), "scan_status": scan_state}
        return {"status": "empty", "scan_status": scan_state}

# --------------------------------------------------------------- actions ----
APP_NAME_RE = re.compile(r"^[A-Za-z0-9 ._'+-]+$")

def act_quit_app(name):
    if not APP_NAME_RE.match(name) or len(name) > 60:
        return False, "bad app name"
    rc, out = run(f"osascript -e 'quit app \"{name}\"'", timeout=15)
    time.sleep(2)
    rc2, pids = run(f"pgrep -fl {shlex.quote(name)} | head -5")
    if rc2 != 0 or not pids.strip():
        return True, f"{name} quit"
    # fallback: SIGTERM its processes (only own ones)
    rc3, out3 = run(f"pkill -TERM -u {MY_UID} -f {shlex.quote(name + '.app')}")
    time.sleep(2)
    rc4, pids = run(f"pgrep -fl {shlex.quote(name)} | head -5")
    if rc4 != 0 or not pids.strip():
        return True, f"{name} terminated (AppleScript failed: {out[:120]})"
    return False, f"{name} still running (pids: {pids.splitlines()[:3]}) — use Force from the process table"

def act_kill_pid(arg):
    try:
        pid = int(arg.get("pid")); sig = int(arg.get("sig", 15))
    except Exception:
        return False, "bad pid"
    if sig not in (15, 9):
        return False, "only SIGTERM(15)/SIGKILL(9) allowed"
    rc, out = run(f"ps -o uid=,comm= -p {pid}")
    if rc != 0 or not out.strip():
        return False, f"pid {pid} not found"
    uid = int(out.split()[0])
    comm = " ".join(out.split()[1:])
    if uid != MY_UID:
        return False, f"refused: {comm} belongs to uid {uid}, not you"
    if pid < 100:
        return False, "refused: system pid"
    os.kill(pid, sig)
    return True, f"sent signal {sig} to {pid} ({os.path.basename(comm)})"

def act_docker(sub):
    if sub == "status":
        rc, out = run("docker info --format '{{.ServerVersion}}'", timeout=8)
        return (rc == 0), (f"Docker up, server {out}" if rc == 0 else "Docker not running")
    if sub == "start":
        run("open -a Docker", timeout=10)
        return True, "Docker Desktop launching (takes ~30s)"
    if sub == "quit":
        for target in ("Docker Desktop", "Docker"):
            rc, _ = run(f"osascript -e 'quit app \"{target}\"'", timeout=15)
            if rc == 0: break
        time.sleep(3)
        run("pkill -TERM -u %d -f 'com.docker'" % MY_UID, timeout=10)
        return True, "Docker Desktop quit signal sent"
    if sub == "prune":
        rc, out = run("docker system prune -a -f", timeout=600)
        return rc == 0, out[-2000:] or "(no output)"
    return False, "unknown docker action"

def act_empty_trash():
    rc, out = run(f"find {shlex.quote(HOME)}/.Trash -mindepth 1 -delete 2>&1 | head -5", timeout=180)
    return True, "Trash emptied" if not out else f"done with notes: {out[:300]}"

def act_npm_clean():
    rc, out = run("command -v npm >/dev/null && npm cache clean --force 2>&1 || rm -rf ~/.npm/_cacache/* 2>&1", timeout=120)
    return True, "npm cache cleaned"

ACTIONS = {
    "quit_app":      lambda a: act_quit_app(str(a.get("name", ""))),
    "kill_pid":      act_kill_pid,
    "docker":        lambda a: act_docker(str(a.get("sub", "status"))),
    "empty_trash":   lambda a: act_empty_trash(),
    "npm_clean":     lambda a: act_npm_clean(),
    "rescan":        lambda a: act_rescan(),
    "restart_finder":lambda a: (run("killall Finder", timeout=10)[0] in (0, 1), "Finder restarted"),
    "restart_dock":  lambda a: (run("killall Dock", timeout=10)[0] in (0, 1), "Dock restarted"),
    "sleep":         lambda a: (run("pmset sleepnow", timeout=10)[0] == 0, "sleeping…"),
    "lock":          lambda a: (run("'/System/Library/CoreServices/Menu Extras/User.menu/Contents/Resources/CGSession' -suspend 2>/dev/null || pmset displaysleepnow", timeout=10)[0] == 0, "locked"),
    "reboot":        lambda a: (run("osascript -e 'tell application \"System Events\" to restart'", timeout=20)[0] == 0, "reboot requested — if apps block it, macOS will show a dialog"),
    "shutdown":      lambda a: (run("osascript -e 'tell application \"System Events\" to shut down'", timeout=20)[0] == 0, "shutdown requested — if apps block it, macOS will show a dialog"),
}

def act_rescan():
    if scan_state["state"] == "running":
        return True, "scan already running"
    threading.Thread(target=_rescan_worker, daemon=True).start()
    return True, "disk scan started (takes several minutes)"

# ---------------------------------------------------------------- server ----
class H(BaseHTTPRequestHandler):
    def log_message(self, *a):  # quiet
        pass
    def _json(self, obj, code=200):
        body = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)
    def _auth(self):
        if TOKEN and self.headers.get("X-Agent-Token") != TOKEN:
            self._json({"error": "bad token"}, 403)
            return False
        return True
    def do_GET(self):
        path = self.path.split("?")[0]
        if path == "/api/health":
            return self._json({"ok": True, "ts": time.time()})
        if not self._auth():
            return
        if path == "/api/stats":
            try: return self._json(get_stats())
            except Exception as e: return self._json({"error": str(e)}, 500)
        if path == "/api/processes":
            return self._json({"processes": get_processes()})
        if path == "/api/scan":
            return self._json(get_scan())
        return self._json({"error": "not found"}, 404)
    def do_POST(self):
        if not self._auth():
            return
        if self.path.split("?")[0] != "/api/action":
            return self._json({"error": "not found"}, 404)
        try:
            n = int(self.headers.get("Content-Length", 0))
            body = json.loads(self.rfile.read(n) or b"{}")
        except Exception:
            return self._json({"error": "bad json"}, 400)
        name = body.get("action")
        fn = ACTIONS.get(name)
        if not fn:
            return self._json({"error": f"unknown action {name!r}"}, 400)
        try:
            ok, msg = fn(body.get("arg") or {})
            return self._json({"ok": ok, "message": msg})
        except Exception as e:
            return self._json({"ok": False, "message": str(e)}, 500)
    def do_OPTIONS(self):
        self.send_response(204); self.end_headers()

if __name__ == "__main__":
    if not TOKEN:
        print("WARNING: no token found (%s); refusing to start." % TOKEN_FILE)
        sys.exit(1)
    print(f"mac-control agent on 127.0.0.1:{PORT} (token loaded, {len(TOKEN)} chars)")
    ThreadingHTTPServer(("127.0.0.1", PORT), H).serve_forever()

# Mac Control

Local web app to monitor and control your Mac: live memory/swap/CPU, top processes
with quit/kill buttons, the disk block-map (treemap), and one-click actions
(quit apps, Docker control, cleanup, sleep / restart / shutdown).

## Architecture (why two parts)

A Docker container **cannot see or control macOS** — Docker Desktop runs in a Linux VM.
So the app is split:

| Part | Runs where | Does what |
|---|---|---|
| `agent/mac_agent.py` | natively on the Mac (stdlib Python, port `127.0.0.1:9999`) | stats, disk scans, whitelisted actions |
| `server/` in Docker | container, port `8100` | serves the UI, proxies `/api/*` to the agent with the shared token |

The agent binds localhost only and requires a token (`.agent-token`, auto-generated).
The token is mounted into the container read-only; the browser never sees it.

## Run

```bash
# 1. start the host agent (once; survives reboots only if you re-run it — see below)
./agent/agent.sh start          # stop | status also work

# 2. start the web app
docker compose up --build -d

# 3. open
open http://localhost:8100
```

## Notes / permissions

- First time the agent tries to quit an app or reboot via AppleScript, macOS may show an
  **Automation permission** prompt for your terminal — approve it once.
- If a graceful quit is refused by the app (e.g. Ollama shows a dialog), the agent falls
  back to SIGTERM on your own processes. System/other-user processes are refused.
- `Restart Mac` / `Shut down` go through System Events; apps with unsaved work can block them.
- Disk rescan takes several minutes; results are cached in `agent/scan_cache.json`.
- To start the agent at login: System Settings → Login Items → add a wrapper, or ask for a launchd plist.

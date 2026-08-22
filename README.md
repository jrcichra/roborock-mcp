# Roborock MCP Server

Control your Roborock vacuum from any MCP-compatible client. Just ask — "start cleaning", "send the vacuum home", "what's the battery?", "clean the kitchen" — and your assistant talks directly to your robot.

Built with [FastMCP](https://github.com/jlowin/fastmcp) and [python-roborock](https://github.com/Python-roborock/python-roborock).

---

## What you can ask

- **"Start cleaning"** — full home clean
- **"Stop cleaning"** / **"Pause cleaning"**
- **"Send [name] home"** / **"Return to dock"**
- **"What's the battery?"** / **"Get status"**
- **"Clean the kitchen"** / **"Clean the living room"** — room-specific cleaning
- **"Find [name]"** — makes the vacuum beep so you can locate it
- **"List my rooms"** — see all rooms the vacuum knows about
- **"Does it need a new filter?"** / **"Check consumables"** — remaining life on filter/brushes/sensors
- **"How much have I cleaned?"** — lifetime + last-clean stats
- **"Turn the volume down"** / **"What's the volume set to?"**
- **"Lock the buttons"** — enable/disable the child lock
- **"Don't clean between 10pm and 8am"** — set Do Not Disturb hours
- **"What maps do I have?"** / **"Switch to the [name] map"** — for accounts with multiple saved maps (e.g. different floors or locations)
- **"Show me the map"** — get a rendered image of the current floor plan
- **"Wash the mop"** / **"Empty the dust bin"** — dock actions (docks that support them)
- **"What's the dock status?"** — dock type, errors, dust collection, mop washing

---

## Requirements

- [uv](https://docs.astral.sh/uv/) (Python 3.10+ is installed automatically by uv)
- A Roborock vacuum linked to a Roborock account
- An MCP-compatible client (e.g. [Claude Desktop](https://claude.ai/download), [Claude Code](https://claude.com/product/claude-code), or any other MCP client)

---

## Setup

### 1. Install dependencies

```bash
uv sync
```

This creates a `.venv` and installs the pinned dependencies from `uv.lock`.

### 2. Authenticate with Roborock

Set your Roborock account email, then run the auth script:

**Windows (Command Prompt):**
```cmd
set ROBOROCK_EMAIL=your_email@example.com && uv run auth.py
```

**Mac/Linux:**
```bash
ROBOROCK_EMAIL=your_email@example.com uv run auth.py
```

Check your email for a verification code, enter it when prompted. Your credentials are saved locally in `.cache/credentials.json` — this file is gitignored and never shared.

**Optional:** if your Roborock account has a password set, you can skip
`auth.py` entirely and set `ROBOROCK_PASSWORD` alongside `ROBOROCK_EMAIL`
instead — the server logs in on its own the first time it runs, and
automatically re-logs in if the cached token ever stops working. Useful for
unattended/headless deployments. Leave it unset to keep using the one-time
`auth.py` flow with no password stored anywhere.

### 3. Add to your MCP client

Most MCP clients (Claude Desktop, Claude Code, etc.) read a JSON config with an `mcpServers` block. Add an entry like this:

```json
"roborock": {
  "command": "uv",
  "args": ["run", "--directory", "/full/path/to/roborock-mcp", "server.py"],
  "env": {
    "ROBOROCK_EMAIL": "your_email@example.com"
  }
}
```

Replace `/full/path/to/roborock-mcp/` with the actual folder path where you cloned this repo.

For Claude Desktop specifically, this goes in:
- **Windows:** `%APPDATA%\Claude\claude_desktop_config.json`
- **Mac:** `~/Library/Application Support/Claude/claude_desktop_config.json`

Check your client's docs for where it expects MCP server config.

### 4. Restart your client

Quit completely and reopen so it picks up the new server. Roborock tools should now be available.

---

## If you have more than one vacuum

By default the server just uses whichever vacuum it finds — no setup needed if
you only have one. If your Roborock account has multiple vacuums, set one of
these env vars (alongside `ROBOROCK_EMAIL`) to pick which one this server
controls:

```bash
ROBOROCK_DEVICE_NAME=Rocky            # the vacuum's name in the Roborock app
# or
ROBOROCK_DEVICE_MODEL=roborock.vacuum.a170   # the vacuum's model ID
```

You can find your model ID in the Roborock app under device settings, or it
will be printed when you run `auth.py`. If neither is set and more than one
device is found, the server logs a warning and defaults to the first one.

---

## Tools exposed

| Tool | Description |
|------|-------------|
| `roborock_get_status` | Battery, state, area cleaned, fan speed |
| `roborock_start_cleaning` | Start a full clean |
| `roborock_stop_cleaning` | Stop current clean |
| `roborock_pause_cleaning` | Pause (can be resumed) |
| `roborock_return_to_dock` | Send home to charge |
| `roborock_get_rooms` | List all mapped rooms |
| `roborock_clean_room` | Clean a specific room by name |
| `roborock_locate` | Play a sound to find the vacuum |
| `roborock_set_fan_power` | Set suction/fan power (quiet, balanced, turbo, max, ...) |
| `roborock_set_water_level` | Set mop water flow level (off, low, medium, high, ...) |
| `roborock_get_consumables` | Remaining life on filter, brushes, sensors, etc. |
| `roborock_get_clean_history` | Lifetime cleaning stats + details of the last clean |
| `roborock_get_volume` | Get the current sound volume |
| `roborock_set_volume` | Set the sound volume (0-100) |
| `roborock_set_child_lock` | Lock/unlock the vacuum's physical buttons |
| `roborock_set_dnd` | Enable/disable Do Not Disturb hours |
| `roborock_list_maps` | List saved maps (floors/locations) and which is active |
| `roborock_switch_map` | Switch the active map by name |
| `roborock_get_map_image` | Get a rendered image of the current floor plan |
| `roborock_start_mop_wash` / `roborock_stop_mop_wash` | Start/stop washing the mop at the dock |
| `roborock_empty_dust_bin` | Trigger the dock to empty the dust bin |
| `roborock_get_dock_status` | Dock type, errors, dust collection, mop washing state |

---

## Running as an HTTP server (e.g. in a container)

By default `server.py` talks over stdio, which is what desktop MCP clients
expect. To run it as a standalone HTTP server instead — for a long-running
deployment rather than a per-client subprocess — set:

```bash
MCP_TRANSPORT=streamable-http MCP_HOST=0.0.0.0 MCP_PORT=8000 python server.py
```

The MCP endpoint is then served at `http://<host>:<port>/mcp`. A `Dockerfile`
is included and builds/runs with these defaults already set; `.cache/credentials.json`
still needs to come from somewhere (e.g. a mounted secret), since `auth.py`'s
login flow is interactive and can't run inside a container.

---

## File structure

```
roborock-mcp/
├── server.py          # MCP server — the main file
├── auth.py            # Run once to authenticate
├── pyproject.toml     # Python dependencies
├── uv.lock            # Locked dependency versions
├── .env.example       # Example environment variable
└── .cache/            # Created by auth.py — gitignored, never shared
    └── credentials.json
```

---

## Troubleshooting

**"Could not attach MCP server"**
- Make sure you've run `auth.py` first
- Check the path in your client's MCP config is correct and uses the full absolute path
- On Windows, make sure backslashes are doubled: `C:\\Users\\...`

**"No devices discovered"**
- Re-run `auth.py` to refresh your credentials
- Make sure your vacuum is online and linked to your Roborock account

**"invalid user agreement" / response code 3006 during login**
- Roborock periodically bumps the version of its user-agreement doc, and older
  versions of `python-roborock` send a stale hardcoded version when logging in,
  which the server rejects. `auth.py` works around this by fetching the current
  agreement version live before logging in — just make sure you're running the
  `auth.py` from this repo, not an older cached copy.

**Room cleaning not working**
- Run "list my rooms" first — the vacuum needs to have completed a mapping run
- Room names are matched loosely, so "kitchen" will match "Kitchen"

---

## Notes

- Credentials are cached locally in `.cache/credentials.json`. This folder is gitignored. **Never commit or share this file.**
- The auth token expires eventually — there's no documented lifetime for it. If `ROBOROCK_PASSWORD` is set, the server refreshes it automatically; otherwise re-run `auth.py`.
- Tested on python-roborock v5.0.0 with a Roborock Q Revo (a170).

---

## Credits

Built by [rainbowllamaspatula](https://github.com/rainbowllamaspatula)), forked and extended by [jrcichra](https://github.com/jrcichra).
Powered by [python-roborock](https://github.com/Python-roborock/python-roborock) and [FastMCP](https://github.com/jlowin/fastmcp).

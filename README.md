# Roborock MCP Server

Control your Roborock vacuum from any MCP-compatible client. Just ask — "start cleaning", "send Kronk home", "what's the battery?", "clean the kitchen" — and your assistant talks directly to your robot.

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

## Customising for your vacuum

By default the server looks for a device named **"Kronk"** or model **roborock.vacuum.a170**. To use your own vacuum, edit these two lines near the top of `server.py`:

```python
DEVICE_NICKNAME = "Kronk"       # ← your vacuum's name in the Roborock app
TARGET_MODEL    = "roborock.vacuum.a170"   # ← your model ID
```

You can find your model ID in the Roborock app under device settings, or it will be printed when you run `auth.py`.

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
- The auth token expires eventually — if you start getting errors, re-run `auth.py` to refresh.
- Tested on python-roborock v5.0.0 with a Roborock Q Revo (a170).

---

## Credits

Built by [rainbowllamaspatula](https://github.com/rainbowllamaspatula)), forked and extended by [jrcichra](https://github.com/jrcichra).
Powered by [python-roborock](https://github.com/Python-roborock/python-roborock) and [FastMCP](https://github.com/jlowin/fastmcp).

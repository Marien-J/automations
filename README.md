# automations

Screen-based (visual) automations for my own self-hosted RuneScape private
server, played through RuneLite. They work only from what is visible on screen:
colours, shapes and labels. They use normal mouse and keyboard input. There is
no game-state access, no server API, no packet or memory inspection and no
client modification.

Not for official RuneScape worlds.

## What's here

| Path | What |
| --- | --- |
| [`autokit/`](autokit) | Game-agnostic framework: capture, colour/template recognition, guarded human-paced input, emergency stop, dialogue rule engine, calibration wizard, runner, simulator support |
| [`osrskit/`](osrskit) | RuneLite/OSRS building blocks: right-click menus, inventory, highlights, client/disconnect detection |
| [`automations/`](automations) | One package per automation |
| [`automations/osrs_construction_mahogany_tables`](automations/osrs_construction_mahogany_tables) | Build/remove Mahogany tables, with Demon Butler refills ([guide](automations/osrs_construction_mahogany_tables/README.md)) |
| [`docs/architecture.md`](docs/architecture.md) | How it fits together, and the safety model |
| [`docs/writing-an-automation.md`](docs/writing-an-automation.md) | Adding the next automation |
| `profiles/` | Your calibration and run logs (git-ignored) |

## Quick start

```bash
python -m venv .venv && source .venv/bin/activate     # Windows: .venv\Scripts\activate
python -m pip install --upgrade pip                   # editable installs need pip >= 21.3
pip install -e ".[dev]"

python -m autokit list
python -m autokit init osrs_construction_mahogany_tables
python -m autokit calibrate osrs_construction_mahogany_tables   # guided capture from your client
python -m autokit check osrs_construction_mahogany_tables       # what it recognises; sends no input
python -m autokit run osrs_construction_mahogany_tables         # F12 / move the mouse / STOP file to stop
```

Try it without a game client first:

```bash
python -m autokit simulate osrs_construction_mahogany_tables --scenario default
```

## Tests

```bash
pytest          # unit tests + end-to-end runs against the simulated house (~1 min)
ruff check .
```

Python 3.10+. On machines without a display (CI), install
`opencv-python-headless` instead of `opencv-python`. The emergency hotkey and
input need a desktop session: on macOS, grant Accessibility permission; on
Linux, use X11.

**Windows / WSL:** run the automation with Windows Python, from PowerShell, on
the same desktop as RuneLite. A process inside WSL cannot see Windows windows
or send them input. WSL is still fine for editing, `pytest` and `simulate`.

# HomeKit MCP ... and CLI ... and Room Plan/Apply script

[![GitHub Release](https://img.shields.io/github/v/release/TimCinel/homekit-mcp?style=for-the-badge)](https://github.com/TimCinel/homekit-mcp/releases)
[![GitHub Activity](https://img.shields.io/github/commit-activity/m/TimCinel/homekit-mcp?style=for-the-badge)](https://github.com/TimCinel/homekit-mcp/commits/main)
[![License](https://img.shields.io/github/license/TimCinel/homekit-mcp?style=for-the-badge)](LICENSE)
[![CI](https://img.shields.io/github/actions/workflow/status/TimCinel/homekit-mcp/ci.yml?style=for-the-badge&label=CI)](https://github.com/TimCinel/homekit-mcp/actions/workflows/ci.yml)

HTTP-based Model Context Protocol (MCP) server for HomeKit. Provides tools to list and manage HomeKit accessories and rooms.

Also a CLI for HomeKit.

Also a script to help manage Home Assistant accessories across rooms, which is the actual motivation for all of this.

This is implemented as a macOS app rather than a CLI tool because HomeKit requires a signed binary with proper entitlements that can only be achieved through an Xcode project.

## Prerequisites

- macOS 13.0+ with HomeKit setup
- Xcode (with Command Line Tools)
- Apple ID (free tier sufficient for development)
- HomeKit accessories configured in the Home app

## Quick Start

```bash
git clone https://github.com/TimCinel/HomeKitSync.git
cd HomeKitSync
make build
make run
```

The server will start on `http://localhost:8080`.

Add to Claude Code:
```bash
claude mcp add --transport http homekit http://localhost:8080/mcp
```

## Available MCP Tools

| Tool | Description | Parameters |
|------|-------------|------------|
| `get_all_accessories` | List all HomeKit accessories with names, rooms, categories, and UUIDs | None |
| `get_all_rooms` | List all HomeKit rooms with names and UUIDs | None |
| `set_accessory_room` | Move an accessory to a different room using UUIDs | `accessory_uuid`, `room_uuid` |
| `create_room` | Create a room in a home, or return the existing room with the same name (ignoring case) | `home_uuid`, `name` |
| `get_accessory_by_name` | Find a HomeKit accessory by name | `name` |
| `get_room_by_name` | Find a HomeKit room by name | `name` |
| `set_accessory_room_by_name` | Move an accessory to a different room using names | `accessory_name`, `room_name` |
| `rename_accessory` | Rename a HomeKit accessory | `accessory_name`, `new_name` |
| `rename_room` | Rename a HomeKit room | `room_name`, `new_name` |
| `get_room_accessories` | Get all accessories in a specific room | `room_name` |
| `accessory_on` | Turn on an accessory (lights, switches) or open covers | `accessory_name` |
| `accessory_off` | Turn off an accessory (lights, switches) or close covers | `accessory_name` |
| `accessory_toggle` | Toggle an accessory between on/off or open/close | `accessory_name` |

## Available CLI Tools

### CLI Wrapper

If you would rather script against the HTTP service directly, use the lightweight Python CLI in [scripts/homekitctl.py](scripts/homekitctl.py). It uses only the Python standard library, so it can run from non-macOS machines as long as they can reach the Mac host over HTTP.

```bash
scripts/homekitctl.py --server http://mac-mini.local:8080 rooms
scripts/homekitctl.py --server http://mac-mini.local:8080 accessories
scripts/homekitctl.py --server http://mac-mini.local:8080 find-accessory "Desk Lamp"
scripts/homekitctl.py --server http://mac-mini.local:8080 move "Desk Lamp" Office
scripts/homekitctl.py --server http://mac-mini.local:8080 on "Desk Lamp"
```

The CLI talks to `http://127.0.0.1:8080` by default. Override it with `--server` or `HOMEKIT_MCP_URL`:

```bash
python3 scripts/homekitctl.py --server http://localhost:8080 rooms
HOMEKIT_MCP_URL=http://mac-mini.local:8080 python3 scripts/homekitctl.py tools
```

For anything not covered by the convenience commands, use raw tool calls:

```bash
scripts/homekitctl.py --server http://mac-mini.local:8080 call rename_room room_name=Office new_name=Study
```

### Room Sync - Home Assistant areas to Home rooms

[scripts/manage_homekit_rooms.py](scripts/manage_homekit_rooms.py) moves the accessories that Home Assistant's HomeKit Bridge exposes into the Apple Home room named after each entity's Home Assistant area. Apple Home ignores the bridge's room for bridged accessories, so without this every accessory has to be placed by hand.

How it works:

- Home Assistant's HomeKit Bridge sets each bridged accessory's serial number to its `entity_id`. Only accessories whose serial number looks like an entity id are managed. Native HomeKit accessories and the bridge itself are never moved.
- It asks Home Assistant for each entity's area through the REST API (`POST /api/template` with `area_name()`, which also follows the device's area).
- It reads accessories and rooms from the HomeKit MCP app, matches rooms by name ignoring case and surrounding spaces, creates missing rooms and moves accessories by UUID, so duplicate accessory names are safe.
- Dry run by default: it writes a plan CSV and changes nothing until you ask it to.

It uses only the Python 3.9+ standard library.

#### Mac prerequisites

1. Xcode with Command Line Tools.
2. Open `HomeKitSync.xcodeproj`, select the `HomeKitSync` target, and under Signing & Capabilities pick your own Team (a free personal team works). Keep the HomeKit capability enabled.
3. Build and run (`make build && make run`, or Run in Xcode). On first launch macOS asks for permission to access your Home data: allow it. If you declined, enable it in System Settings > Privacy & Security > Home.
4. The Home app on the same Apple ID has the Home Assistant HomeKit Bridge paired.
5. Keep the app running while the script runs. It listens on `127.0.0.1:8080` only, so run the script on the same Mac.

#### Home Assistant access token

In Home Assistant open your profile, Security tab, and under Long-lived access tokens choose Create token. Copy it once; Home Assistant does not show it again. The script sends it only to `HASS_SERVER` and never prints it.

#### Configuration

| Variable | Meaning |
|----------|---------|
| `HASS_SERVER` | Home Assistant base URL, for example `http://homeassistant.local:8123` |
| `HASS_TOKEN` | Long-lived access token |
| `HOMEKIT_MCP_URL` | HomeKit MCP app URL, default `http://127.0.0.1:8080` |

Set them in the environment, or put `KEY=VALUE` lines in a file and pass `--env-file PATH` (values in the file win over the environment). Keep that file out of git.

#### Workflow

1. Dry run. Nothing in Home changes:

   ```
   $ scripts/manage_homekit_rooms.py --env-file ~/.config/homekit-room-sync.env
   Home: Home
     move             Kitchen Log Light [light.kitchen_log_light] (6F1C...): Default Room -> Kitchen
     create_room+move Bedroom Blinds [cover.bedroom_blinds] (0B7A...): Default Room -> Bedroom
   Wrote plan CSV: artifacts/homekit-plan-20261104-213000.csv
   Accessories in plan: 48
   To move: 2
   Rooms to create: Bedroom
   Already in place: 44
   Skipped (see reason column): 2
   Dry run only. Re-run with --apply, or pass the plan CSV to --apply-plan.
   ```

2. Read the plan CSV. Columns: `accessory_uuid`, `name`, `serial`, `home`, `current_room`, `target_room`, `action`, `reason`. Actions:
   - `move`: move into an existing room.
   - `create_room+move`: the room does not exist in Home yet; it is created once, then the accessory is moved.
   - `none`: already in the right room.
   - `skip`: not managed, with the reason (not a Home Assistant entity id, entity not found in Home Assistant, no area in Home Assistant, or the bridge itself).

3. Apply, either by planning again and applying straight away, or by applying the plan you reviewed:

   ```
   $ scripts/manage_homekit_rooms.py --apply
   $ scripts/manage_homekit_rooms.py --apply-plan artifacts/homekit-plan-20261104-213000.csv
   ```

   `--apply-plan` re-reads Home first and skips rows whose accessory no longer exists, is already in the target room, or was moved somewhere else since the plan was written. You can edit `target_room` in a plan before applying it; only `move` and `create_room+move` rows are applied.

If the account has several homes, pass `--home NAME` unless exactly one home has Home Assistant accessories. Plans and snapshots go to `artifacts/` in the repository (git-ignored); change it with `--output-dir`.

Exit codes: `0` success or nothing to change, `1` error (Home Assistant or the app unreachable, token rejected, bad input), `2` some moves or room creations failed (they are listed on stderr; the rest were applied).

#### Snapshot and restore

Before applying anything the script writes `artifacts/homekit-snapshot-<time>.csv` with every accessory's `accessory_uuid`, `name`, `serial`, `home` and `room`. To go back:

```
$ scripts/manage_homekit_rooms.py --restore artifacts/homekit-snapshot-20261104-213005.csv
$ scripts/manage_homekit_rooms.py --restore artifacts/homekit-snapshot-20261104-213005.csv --apply
```

Restore matches accessories by UUID, recreates rooms that no longer exist, and works like a sync: dry run first, then `--apply`. Restore does not need Home Assistant. You can edit the `room` column of a snapshot to place accessories by hand.

#### Limits

- Only accessories from Home Assistant's HomeKit Bridge are managed. Device-trigger accessories (serial is a device id) and native HomeKit accessories are left alone.
- Home Assistant area names become Home room names. An existing room with the same name (ignoring case) is reused; otherwise a room is created with the area's name. Renaming an area in Home Assistant therefore creates a new room on the next sync, and the old room stays in Home until you delete it.
- Rooms are only created and filled, never deleted or renamed.
- It runs when you run it. Run it again after moving entities between areas in Home Assistant.

## Development

### Testing

Full test suite (requires HomeKit):
```bash
make test
```

CI-friendly tests (no HomeKit dependency):
```bash
make test-ci
```

Room sync script tests (any OS, Python 3.9+):
```bash
python3 -m unittest discover -s scripts/tests -v
```

### Code Quality

```bash
make install-deps  # Install SwiftLint
make lint
make lint-fix      # Auto-fix issues
```

## API Reference

### HTTP Endpoints

| Method | Endpoint | Description |
|--------|----------|-------------|
| `GET` | `/mcp` | MCP server discovery |
| `POST` | `/mcp/tools/list` | List available tools |
| `POST` | `/mcp/tools/call` | Execute tools |

## Contributing

1. Fork the repository
2. Create a feature branch
3. Make your changes
4. Run tests (`make test-ci`) and linting (`make lint`)
5. Submit a pull request

## License

This project is a fork of [TimCinel/homekit-mcp](https://github.com/TimCinel/homekit-mcp) and is licensed under the MIT License - see the [LICENSE](LICENSE) file for details.

## Troubleshooting

**"No such module 'HomeKit'"**
- Ensure you're building for the correct target
- Verify Xcode is properly installed

**"App cannot run on the current OS version"**
- Check deployment target in Xcode project settings

**"HomeKit permissions denied"**
- Verify HomeKit entitlement is enabled
- Check Apple ID signing configuration

**Server not reachable**
- Ensure app is running and visible in Dock
- Verify server logs in Console.app (search for "MCP") or run `log stream --info --process $(ps aux | grep MCP | grep -v grep | awk '{ print $2 }')`

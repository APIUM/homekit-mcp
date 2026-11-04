#!/usr/bin/env python3

import argparse
import csv
import os
import re
import sys
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import homekit_http
from homekit_http import RequestError


DEFAULT_HOMEKIT_URL = "http://127.0.0.1:8080"
DEFAULT_OUTPUT_DIR = Path(__file__).resolve().parent.parent / "artifacts"
HTTP_TIMEOUT = 30.0
HA_BATCH_SIZE = 100
# Same rule as homeassistant.core.valid_entity_id; HA fails the whole template render on an invalid id.
ENTITY_ID_PATTERN = re.compile(r"^(?!.+__)(?!_)[\da-z_]+(?<!_)\.(?!_)[\da-z_]+(?<!_)$")
# Serial number Home Assistant's HomeKit Bridge gives the bridge accessory itself.
HA_BRIDGE_SERIAL = "homekit.bridge"

MOVE = "move"
CREATE_AND_MOVE = "create_room+move"
NO_CHANGE = "none"
SKIP = "skip"
ACTIONABLE = (MOVE, CREATE_AND_MOVE)

PLAN_FIELDS = [
    "accessory_uuid",
    "name",
    "serial",
    "home",
    "current_room",
    "target_room",
    "action",
    "reason",
]
SNAPSHOT_FIELDS = ["accessory_uuid", "name", "serial", "home", "room"]

AREA_TEMPLATE = """\
{%- set ns = namespace(out={}) -%}
{%- for id in ids -%}
{%- set ns.out = dict(ns.out, **{id: {"area": area_name(id), "known": states[id] is not none or device_id(id) is not none}}) -%}
{%- endfor -%}
{{ ns.out | tojson }}"""


class SyncError(Exception):
    pass


class ToolError(Exception):
    pass


@dataclass
class Accessory:
    uuid: str
    name: str
    serial: str
    bridge_serial: str
    room: str
    home: str
    home_uuid: str


@dataclass
class Room:
    uuid: str
    name: str
    home: str
    home_uuid: str


@dataclass
class PlanRow:
    accessory_uuid: str
    name: str
    serial: str
    home: str
    current_room: str
    target_room: str
    action: str
    reason: str


@dataclass
class HomeState:
    accessories: List[Accessory]
    rooms: List[Room]

    def accessory(self, uuid: str) -> Optional[Accessory]:
        for item in self.accessories:
            if item.uuid.casefold() == uuid.casefold():
                return item
        return None

    def find_room(self, home_uuid: str, name: str) -> Optional[Room]:
        for room in self.rooms:
            if room.home_uuid == home_uuid and room_key(room.name) == room_key(name):
                return room
        return None

    def homes(self) -> Dict[str, str]:
        homes: Dict[str, str] = {}
        for item in [*self.rooms, *self.accessories]:
            homes.setdefault(item.home_uuid, item.home)
        return homes

    def managed(self, home_uuid: str) -> List[Accessory]:
        return [a for a in self.accessories if a.home_uuid == home_uuid and unmanaged_reason(a) is None]


@dataclass
class ApplyResult:
    moved: List[str] = field(default_factory=list)
    created_rooms: List[str] = field(default_factory=list)
    skipped: List[str] = field(default_factory=list)
    failures: List[str] = field(default_factory=list)


def room_key(name: str) -> str:
    return (name or "").strip().casefold()


def unmanaged_reason(accessory: Accessory) -> Optional[str]:
    if accessory.serial == HA_BRIDGE_SERIAL:
        return "Home Assistant bridge"
    if accessory.bridge_serial != HA_BRIDGE_SERIAL:
        return "not bridged by Home Assistant"
    if not ENTITY_ID_PATTERN.fullmatch(accessory.serial):
        return "serial is not a Home Assistant entity id"
    return None


class HomeKitClient:
    def __init__(self, base_url: str):
        self.url = homekit_http.endpoint(base_url, "/mcp/tools/call")
        self.next_id = 0

    def call(self, tool: str, arguments: dict) -> dict:
        self.next_id += 1
        body = homekit_http.tool_call_body(tool, arguments, self.next_id)
        payload = homekit_http.post_json(self.url, body, timeout=HTTP_TIMEOUT)
        if not isinstance(payload, dict):
            raise ToolError(f"{tool} returned an unexpected response")
        error = payload.get("error")
        if error:
            message = error.get("message") if isinstance(error, dict) else None
            raise ToolError(message or str(error))
        result = payload.get("result")
        structured = result.get("structuredContent") if isinstance(result, dict) else None
        if not isinstance(structured, dict):
            raise ToolError(f"{tool} returned no structuredContent; build the app from this repository")
        return structured

    def read_state(self) -> HomeState:
        try:
            accessories = self.call("get_all_accessories", {}).get("accessories")
            rooms = self.call("get_all_rooms", {}).get("rooms")
        except RequestError as exc:
            raise SyncError(
                f"HomeKit MCP app request to {self.url} failed: {exc}. "
                "Check the app is running and HOMEKIT_MCP_URL is correct."
            ) from exc
        except ToolError as exc:
            raise SyncError(f"HomeKit MCP app error: {exc}") from exc
        for items in (accessories, rooms):
            if not isinstance(items, list) or not all(isinstance(item, dict) for item in items):
                raise SyncError("HomeKit MCP app returned accessories or rooms in an unexpected format")
        return HomeState(
            accessories=[
                Accessory(
                    uuid=text(item, "uuid"),
                    name=text(item, "name"),
                    serial=text(item, "serial_number"),
                    bridge_serial=text(item, "bridge_serial_number"),
                    room=text(item, "room"),
                    home=text(item, "home"),
                    home_uuid=text(item, "home_uuid"),
                )
                for item in accessories
            ],
            rooms=[
                Room(
                    uuid=text(item, "uuid"),
                    name=text(item, "name"),
                    home=text(item, "home"),
                    home_uuid=text(item, "home_uuid"),
                )
                for item in rooms
            ],
        )


def text(item: dict, key: str) -> str:
    value = item.get(key)
    return "" if value is None else str(value)


def fetch_ha_areas(server: str, token: str, entity_ids: List[str]) -> Dict[str, dict]:
    url = homekit_http.endpoint(server, "/api/template")
    headers = {"Authorization": f"Bearer {token}"}
    areas: Dict[str, dict] = {}
    for start in range(0, len(entity_ids), HA_BATCH_SIZE):
        batch = entity_ids[start : start + HA_BATCH_SIZE]
        body = {"template": AREA_TEMPLATE, "variables": {"ids": batch}}
        try:
            payload = homekit_http.post_json(url, body, headers=headers, timeout=HTTP_TIMEOUT)
        except RequestError as exc:
            if exc.status in (401, 403):
                raise SyncError(
                    f"Home Assistant at {server} rejected the access token (HTTP {exc.status}). "
                    "HASS_TOKEN must be a long-lived access token of a Home Assistant admin user."
                ) from exc
            raise SyncError(f"Home Assistant request to {url} failed: {exc}") from exc
        if not isinstance(payload, dict):
            raise SyncError("Home Assistant template API returned an unexpected response")
        for entity_id, info in payload.items():
            if not isinstance(info, dict) or not isinstance(info.get("area"), (str, type(None))):
                raise SyncError(f"Home Assistant template API returned an unexpected entry for {entity_id}")
        areas.update(payload)
    return areas


def load_env_file(path: Path) -> Dict[str, str]:
    try:
        lines = path.read_text(encoding="utf-8-sig").splitlines()
    except OSError as exc:
        raise SyncError(f"Cannot read env file {path}: {exc.strerror}") from exc
    except UnicodeDecodeError as exc:
        raise SyncError(f"Cannot read env file {path}: it is not UTF-8 text") from exc
    loaded: Dict[str, str] = {}
    for raw_line in lines:
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[len("export ") :]
        if "=" not in line:
            continue
        key, value = line.split("=", 1)
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        loaded[key.strip()] = value
    return loaded


def select_home(state: HomeState, requested: Optional[str]) -> str:
    homes = state.homes()
    if not homes:
        raise SyncError(
            "The HomeKit MCP app reports no homes. Check that the Apple ID signed in on this Mac "
            "has a home in the Home app."
        )
    listed = ", ".join(sorted(homes.values()))
    if requested:
        matches = [uuid for uuid, name in homes.items() if room_key(name) == room_key(requested)]
        if not matches:
            raise SyncError(f"No home named '{requested}'. Homes: {listed}")
        if len(matches) > 1:
            raise SyncError(f"Several homes are named '{requested}'; rename one in the Home app.")
        return matches[0]
    if len(homes) == 1:
        return next(iter(homes))
    with_managed = {item.home_uuid for item in state.accessories if unmanaged_reason(item) is None}
    if len(with_managed) == 1:
        return with_managed.pop()
    raise SyncError(f"Several homes found ({listed}); pass --home NAME to choose one.")


def row_for(accessory: Accessory, target_room: str, action: str, reason: str) -> PlanRow:
    return PlanRow(
        accessory_uuid=accessory.uuid,
        name=accessory.name,
        serial=accessory.serial,
        home=accessory.home,
        current_room=accessory.room,
        target_room=target_room,
        action=action,
        reason=reason,
    )


def target_row(state: HomeState, accessory: Accessory, target: str, source: str) -> PlanRow:
    existing = state.find_room(accessory.home_uuid, target)
    if room_key(accessory.room) == room_key(target):
        return row_for(accessory, accessory.room, NO_CHANGE, f"{source}; already in room")
    if existing:
        return row_for(accessory, existing.name, MOVE, source)
    return row_for(accessory, target, CREATE_AND_MOVE, f"{source}; room missing in Home")


def plan_sync(state: HomeState, home_uuid: str, areas: Dict[str, dict]) -> List[PlanRow]:
    rows: List[PlanRow] = []
    for accessory in state.accessories:
        if accessory.home_uuid != home_uuid:
            continue
        reason = unmanaged_reason(accessory)
        if reason:
            rows.append(row_for(accessory, "", SKIP, reason))
            continue
        info = areas.get(accessory.serial) or {}
        area = (info.get("area") or "").strip()
        if not area:
            reason = "no area in Home Assistant" if info.get("known") else "entity not found in Home Assistant"
            rows.append(row_for(accessory, "", SKIP, reason))
            continue
        rows.append(target_row(state, accessory, area, "Home Assistant area"))
    return rows


def plan_restore(state: HomeState, home_uuid: str, snapshot: List[dict]) -> List[PlanRow]:
    rows: List[PlanRow] = []
    for entry in snapshot:
        uuid = (entry.get("accessory_uuid") or "").strip()
        room = (entry.get("room") or "").strip()
        accessory = state.accessory(uuid)
        if accessory is None:
            rows.append(
                PlanRow(
                    accessory_uuid=uuid,
                    name=entry.get("name") or "",
                    serial=entry.get("serial") or "",
                    home=entry.get("home") or "",
                    current_room="",
                    target_room=room,
                    action=SKIP,
                    reason="accessory not found in Home",
                )
            )
        elif accessory.home_uuid != home_uuid:
            rows.append(row_for(accessory, "", SKIP, "accessory is in another home"))
        elif unmanaged_reason(accessory):
            rows.append(row_for(accessory, "", SKIP, unmanaged_reason(accessory)))
        elif not room:
            rows.append(row_for(accessory, "", SKIP, "no room in snapshot"))
        else:
            rows.append(target_row(state, accessory, room, "snapshot"))
    return rows


def label(row: PlanRow) -> str:
    serial = f" [{row.serial}]" if row.serial else ""
    return f"{row.name}{serial} ({row.accessory_uuid})"


def apply_rows(client: HomeKitClient, state: HomeState, home_uuid: str, rows: List[PlanRow]) -> ApplyResult:
    result = ApplyResult()
    ready_rooms: Dict[Tuple[str, str], str] = {}
    failed_rooms: Dict[Tuple[str, str], str] = {}
    for row in rows:
        if row.action not in ACTIONABLE:
            continue
        accessory = state.accessory(row.accessory_uuid)
        if accessory is None:
            result.skipped.append(f"{label(row)}: accessory no longer exists")
            continue
        if accessory.home_uuid != home_uuid:
            result.skipped.append(f"{label(row)}: accessory is in another home ({accessory.home})")
            continue
        reason = unmanaged_reason(accessory)
        if reason:
            result.skipped.append(f"{label(row)}: not managed ({reason})")
            continue
        if room_key(accessory.room) == room_key(row.target_room):
            result.skipped.append(f"{label(row)}: already in {accessory.room}")
            continue
        if room_key(accessory.room) != room_key(row.current_room):
            result.skipped.append(f"{label(row)}: room changed since the plan was made (now {accessory.room})")
            continue

        existing = state.find_room(accessory.home_uuid, row.target_room)
        key = (accessory.home_uuid, room_key(row.target_room))
        if existing:
            room_uuid = existing.uuid
        elif key in failed_rooms:
            result.failures.append(f"{label(row)}: cannot create room {row.target_room}: {failed_rooms[key]}")
            continue
        elif key in ready_rooms:
            room_uuid = ready_rooms[key]
        else:
            name = row.target_room.strip()
            try:
                room = client.call("create_room", {"home_uuid": accessory.home_uuid, "name": name}).get("room")
                if not isinstance(room, dict) or not room.get("uuid"):
                    raise ToolError("create_room returned no room uuid")
            except (RequestError, ToolError) as exc:
                failed_rooms[key] = str(exc)
                result.failures.append(f"{label(row)}: cannot create room {name}: {exc}")
                continue
            room_uuid = ready_rooms[key] = str(room["uuid"])
            if room.get("created", True):
                result.created_rooms.append(f"{room.get('name') or name} in {accessory.home}")

        try:
            client.call("set_accessory_room", {"accessory_uuid": accessory.uuid, "room_uuid": room_uuid})
        except (RequestError, ToolError) as exc:
            result.failures.append(f"{label(row)}: move to {row.target_room} failed: {exc}")
            continue
        result.moved.append(f"{label(row)}: {accessory.room} -> {row.target_room}")
    return result


def artifact_path(output_dir: Path, kind: str) -> Path:
    try:
        output_dir.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise SyncError(f"Cannot create output directory {output_dir}: {exc.strerror}") from exc
    stem = f"homekit-{kind}-{datetime.now().strftime('%Y%m%d-%H%M%S')}"
    path = output_dir / f"{stem}.csv"
    counter = 1
    while path.exists():
        path = output_dir / f"{stem}-{counter}.csv"
        counter += 1
    return path


def write_csv(path: Path, fields: List[str], rows: List[dict]) -> None:
    try:
        with path.open("w", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(handle, fieldnames=fields)
            writer.writeheader()
            writer.writerows(rows)
    except OSError as exc:
        raise SyncError(f"Cannot write {path}: {exc.strerror}") from exc


def read_csv(path: Path, required: List[str]) -> List[dict]:
    try:
        with path.open(newline="", encoding="utf-8-sig") as handle:
            reader = csv.DictReader(handle)
            rows = list(reader)
            columns = set(reader.fieldnames or [])
    except OSError as exc:
        raise SyncError(f"Cannot read {path}: {exc.strerror}") from exc
    except (UnicodeDecodeError, csv.Error) as exc:
        raise SyncError(f"Cannot read {path} ({exc}); save it as \"CSV UTF-8\" and try again") from exc
    missing = [name for name in required if name not in columns]
    if missing:
        raise SyncError(f"{path} is missing columns: {', '.join(missing)}")
    return rows


def write_plan(output_dir: Path, rows: List[PlanRow]) -> Path:
    path = artifact_path(output_dir, "plan")
    write_csv(path, PLAN_FIELDS, [{name: getattr(row, name) for name in PLAN_FIELDS} for row in rows])
    return path


def load_plan(path: Path) -> List[PlanRow]:
    rows = read_csv(path, ["accessory_uuid", "current_room", "target_room", "action"])
    plan = [PlanRow(**{name: (row.get(name) or "") for name in PLAN_FIELDS}) for row in rows]
    for number, row in enumerate(plan, start=1):
        if row.action in ACTIONABLE and not row.target_room.strip():
            raise SyncError(f"{path} row {number} ({row.accessory_uuid}): target_room is empty for a {row.action} row")
    return plan


def write_snapshot(output_dir: Path, accessories: List[Accessory]) -> Path:
    path = artifact_path(output_dir, "snapshot")
    write_csv(
        path,
        SNAPSHOT_FIELDS,
        [
            {"accessory_uuid": a.uuid, "name": a.name, "serial": a.serial, "home": a.home, "room": a.room}
            for a in accessories
        ],
    )
    return path


def print_plan(rows: List[PlanRow], plan_path: Path) -> None:
    pending = [row for row in rows if row.action in ACTIONABLE]
    for row in pending:
        print(f"  {row.action:<16} {label(row)}: {row.current_room or '-'} -> {row.target_room}")
    rooms_by_key: Dict[str, str] = {}
    for row in pending:
        if row.action == CREATE_AND_MOVE:
            rooms_by_key.setdefault(room_key(row.target_room), row.target_room.strip())
    new_rooms = [rooms_by_key[key] for key in sorted(rooms_by_key)]
    print(f"Wrote plan CSV: {plan_path}")
    print(f"Accessories in plan: {len(rows)}")
    print(f"To move: {len(pending)}")
    if new_rooms:
        print(f"Rooms to create: {', '.join(new_rooms)}")
    print(f"Already in place: {sum(1 for row in rows if row.action == NO_CHANGE)}")
    print(f"Skipped (see reason column): {sum(1 for row in rows if row.action == SKIP)}")


def apply_and_report(
    client: HomeKitClient, state: HomeState, home_uuid: str, rows: List[PlanRow], output_dir: Path
) -> int:
    if not any(row.action in ACTIONABLE for row in rows):
        print("Nothing to apply.")
        return 0
    print(f"Wrote snapshot CSV: {write_snapshot(output_dir, state.managed(home_uuid))}")
    result = apply_rows(client, state, home_uuid, rows)
    for line in result.created_rooms:
        print(f"Created room: {line}")
    for line in result.moved:
        print(f"Moved: {line}")
    for line in result.skipped:
        print(f"Skipped: {line}")
    print(f"Moves applied: {len(result.moved)}")
    if result.failures:
        print(f"Failures: {len(result.failures)}", file=sys.stderr)
        for line in result.failures:
            print(f"  {line}", file=sys.stderr)
        return 2
    return 0


def run(args: argparse.Namespace, env: Dict[str, str]) -> int:
    client = HomeKitClient(env.get("HOMEKIT_MCP_URL") or DEFAULT_HOMEKIT_URL)
    output_dir = Path(args.output_dir)

    if args.apply_plan:
        rows = load_plan(Path(args.apply_plan))
        state = client.read_state()
        home_uuid = select_home(state, args.home)
        print(f"Loaded plan CSV: {args.apply_plan} ({len(rows)} rows)")
        print(f"Home: {state.homes()[home_uuid]}")
        return apply_and_report(client, state, home_uuid, rows, output_dir)

    if args.restore:
        snapshot = read_csv(Path(args.restore), ["accessory_uuid", "room"])
        state = client.read_state()
        home_uuid = select_home(state, args.home)
        rows = plan_restore(state, home_uuid, snapshot)
    else:
        server = env.get("HASS_SERVER", "").strip()
        token = env.get("HASS_TOKEN", "").strip()
        if not server or not token:
            raise SyncError("HASS_SERVER and HASS_TOKEN must be set in the environment or --env-file.")
        state = client.read_state()
        home_uuid = select_home(state, args.home)
        candidates = sorted({a.serial for a in state.managed(home_uuid)})
        rows = plan_sync(state, home_uuid, fetch_ha_areas(server, token, candidates) if candidates else {})

    print(f"Home: {state.homes()[home_uuid]}")
    print_plan(rows, write_plan(output_dir, rows))
    if not args.apply:
        print("Dry run only. Re-run with --apply, or pass the plan CSV to --apply-plan.")
        return 0
    return apply_and_report(client, state, home_uuid, rows, output_dir)


class ArgumentParser(argparse.ArgumentParser):
    def error(self, message: str):
        self.print_usage(sys.stderr)
        self.exit(1, f"{self.prog}: error: {message}\n")


def parse_args(argv: Optional[List[str]]) -> argparse.Namespace:
    parser = ArgumentParser(
        description=(
            "Move Apple Home accessories bridged from Home Assistant into the Home room named after "
            "their Home Assistant area. Dry run by default: writes a plan CSV and changes nothing."
        ),
        epilog=(
            "Environment: HASS_SERVER, HASS_TOKEN (long-lived access token of an admin user), "
            f"HOMEKIT_MCP_URL (default {DEFAULT_HOMEKIT_URL}). "
            "Exit codes: 0 success or nothing to do, 1 error, 2 some changes failed."
        ),
    )
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--apply-plan", metavar="PLAN_CSV", help="Apply the move rows of an existing plan CSV.")
    mode.add_argument(
        "--restore",
        metavar="SNAPSHOT_CSV",
        help="Plan moves back to the rooms in a snapshot CSV (matched by accessory UUID).",
    )
    parser.add_argument("--apply", action="store_true", help="Apply the plan after writing it.")
    parser.add_argument("--home", metavar="NAME", help="Home to use when the account has several homes.")
    parser.add_argument(
        "--output-dir",
        default=str(DEFAULT_OUTPUT_DIR),
        help="Directory for plan and snapshot CSV files. Default: artifacts/ in the repository.",
    )
    parser.add_argument("--env-file", metavar="PATH", help="File with KEY=VALUE lines, applied over the environment.")
    return parser.parse_args(argv)


def main(argv: Optional[List[str]] = None, environ: Optional[Dict[str, str]] = None) -> int:
    args = parse_args(argv)
    env = dict(os.environ if environ is None else environ)
    try:
        if args.env_file:
            env.update(load_env_file(Path(args.env_file)))
        return run(args, env)
    except SyncError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())

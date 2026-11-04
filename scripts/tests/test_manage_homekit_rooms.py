import contextlib
import csv
import io
import json
import socket
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import manage_homekit_rooms  # noqa: E402
from fakes import FakeHomeAssistant, FakeHomeKit, accessory, room  # noqa: E402

try:
    import jinja2.sandbox
except ImportError:
    jinja2 = None


TOKEN = "test-token-5f1c9a7e-do-not-print"

HA_ENTITIES = {
    "light.kitchen_log_light": {"area": "Kitchen", "known": True},
    "cover.bedroom_blinds": {"area": "Bedroom", "known": True},
    "light.bedside": {"area": "Bedroom", "known": True},
    "light.desk": {"area": "Office", "known": True},
    "switch.fan": {"area": None, "known": True},
    "light.lounge_lamp": {"area": "Living Room", "known": True},
    "light.office_lamp": {"area": " Office ", "known": True},
    "light.a__b": {"area": "Office", "known": True},
}

ROOMS = [
    room("R-DEFAULT", "Default Room", is_default=True),
    room("R-KITCHEN", "Kitchen"),
    room("R-OFFICE", "Office"),
    room("R-LIVING", "living room"),
]

ACCESSORIES = [
    accessory("A1", "Kitchen Log Light", "light.kitchen_log_light", "Office"),
    accessory("A2", "Blinds", "cover.bedroom_blinds", "Default Room"),
    accessory("A3", "Bedside", "light.bedside", "Default Room"),
    accessory("A4", "Desk", "light.desk", "Office"),
    accessory("A5", "Hue Bulb", "ABC123XYZ", "Kitchen", bridge_serial=None),
    accessory("A6", "Ghost", "light.ghost", "Office"),
    accessory("A7", "Fan", "switch.fan", "Office"),
    accessory("A8", "Home Assistant Bridge", "homekit.bridge", "Office", bridge_serial=None),
    accessory("A9", "Lamp", "light.lounge_lamp", "Office"),
    accessory("A10", "Lamp", "light.office_lamp", "Kitchen"),
    accessory("A11", "Other Bridge Desk", "light.desk", "Kitchen", bridge_serial="OTHER-BRIDGE-1"),
    accessory("A12", "Odd Serial", "light.a__b", "Kitchen"),
]

MANAGED = ["A1", "A2", "A3", "A4", "A6", "A7", "A9", "A10"]
UNMANAGED = ["A5", "A8", "A11", "A12"]


def closed_port_url() -> str:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    return f"http://127.0.0.1:{port}"


def read_rows(path: str):
    with open(path, newline="", encoding="utf-8-sig") as handle:
        return list(csv.DictReader(handle))


def path_after(output: str, prefix: str) -> str:
    for line in output.splitlines():
        if line.startswith(prefix):
            return line[len(prefix) :].strip()
    raise AssertionError(f"No line starting with {prefix!r} in output:\n{output}")


def edit_plan(path: str, changes: dict) -> None:
    rows = read_rows(path)
    for row in rows:
        row.update(changes.get(row["accessory_uuid"], {}))
    manage_homekit_rooms.write_csv(Path(path), manage_homekit_rooms.PLAN_FIELDS, rows)


class RoomSyncTestCase(unittest.TestCase):
    accessories = ACCESSORIES
    rooms = ROOMS

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.output_dir = self.tmp.name
        self.ha = FakeHomeAssistant(TOKEN, dict(HA_ENTITIES))
        self.addCleanup(self.ha.server.stop)
        self.homekit = FakeHomeKit(self.accessories, self.rooms)
        self.addCleanup(self.homekit.stop)

    def run_script(self, *argv, output_dir=None, **env_overrides):
        environ = {"HASS_SERVER": self.ha.url, "HASS_TOKEN": TOKEN, "HOMEKIT_MCP_URL": self.homekit.url}
        environ.update(env_overrides)
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = manage_homekit_rooms.main([*argv, "--output-dir", output_dir or self.output_dir], environ)
        return code, out.getvalue(), err.getvalue()

    def plan_by_uuid(self, output: str):
        return {row["accessory_uuid"]: row for row in read_rows(path_after(output, "Wrote plan CSV:"))}

    def moves(self):
        return {args["accessory_uuid"]: args["room_uuid"] for tool, args in self.homekit.calls if tool == "set_accessory_room"}

    def creates(self):
        return [args["name"] for tool, args in self.homekit.calls if tool == "create_room"]

    def rooms_of(self, uuids):
        return {uuid: self.homekit.room_of(uuid) for uuid in uuids}

    def all_artifacts_text(self) -> str:
        return "".join(path.read_text(encoding="utf-8") for path in Path(self.output_dir).glob("*.csv"))


class PlanTests(RoomSyncTestCase):
    def test_dry_run_plans_every_case_without_changes(self):
        code, out, err = self.run_script()
        self.assertEqual(code, 0, err)
        plan = self.plan_by_uuid(out)

        expected = {
            "A1": ("move", "Office", "Kitchen", ""),
            "A2": ("create_room+move", "Default Room", "Bedroom", "room missing"),
            "A3": ("create_room+move", "Default Room", "Bedroom", "room missing"),
            "A4": ("none", "Office", "Office", "already in room"),
            "A5": ("skip", "Kitchen", "", "not bridged by Home Assistant"),
            "A6": ("skip", "Office", "", "entity not found in Home Assistant"),
            "A7": ("skip", "Office", "", "no area in Home Assistant"),
            "A8": ("skip", "Office", "", "Home Assistant bridge"),
            "A9": ("move", "Office", "living room", ""),
            "A10": ("move", "Kitchen", "Office", ""),
            "A11": ("skip", "Kitchen", "", "not bridged by Home Assistant"),
            "A12": ("skip", "Kitchen", "", "serial is not a Home Assistant entity id"),
        }
        self.assertEqual(set(plan), set(expected))
        for uuid, (action, current, target, reason) in expected.items():
            row = plan[uuid]
            self.assertEqual((row["action"], row["current_room"], row["target_room"]), (action, current, target), uuid)
            self.assertIn(reason, row["reason"], uuid)
        self.assertEqual(set(read_rows(path_after(out, "Wrote plan CSV:"))[0]), set(manage_homekit_rooms.PLAN_FIELDS))

        self.assertEqual(self.homekit.mutating_calls(), [])
        self.assertIn("Rooms to create: Bedroom", out)
        self.assertIn("Dry run only", out)

    def test_ha_is_asked_only_about_managed_serials(self):
        code, _, err = self.run_script()
        self.assertEqual(code, 0, err)
        asked = [entity_id for body in self.ha.requests for entity_id in body["variables"]["ids"]]
        self.assertEqual(
            asked,
            sorted(
                [
                    "light.kitchen_log_light",
                    "cover.bedroom_blinds",
                    "light.bedside",
                    "light.desk",
                    "light.ghost",
                    "switch.fan",
                    "light.lounge_lamp",
                    "light.office_lamp",
                ]
            ),
        )
        self.assertEqual(self.ha.requests[0]["template"], manage_homekit_rooms.AREA_TEMPLATE)

    def test_null_serial_is_skipped(self):
        unread = accessory("N1", "Unread", "light.unread", "Office")
        unread["serial_number"] = None
        self.homekit.accessories.append(unread)
        code, out, err = self.run_script()
        self.assertEqual(code, 0, err)
        row = self.plan_by_uuid(out)["N1"]
        self.assertEqual((row["action"], row["serial"]), ("skip", ""))
        self.assertEqual(row["reason"], "serial is not a Home Assistant entity id")

    def test_entity_id_rule_matches_home_assistant(self):
        valid = ["light.kitchen", "sensor.a1_b2", "switch.0x1"]
        invalid = ["light._x", "light.x_", "light.a__b", "_a.b", "a_.b", "Light.x", "light.x\n", "light", "a.b.c"]
        for serial in valid:
            self.assertTrue(manage_homekit_rooms.ENTITY_ID_PATTERN.fullmatch(serial), serial)
        for serial in invalid:
            self.assertFalse(manage_homekit_rooms.ENTITY_ID_PATTERN.fullmatch(serial), serial)

    def test_room_match_ignores_case_and_whitespace(self):
        code, out, err = self.run_script()
        self.assertEqual(code, 0, err)
        plan = self.plan_by_uuid(out)
        self.assertEqual(plan["A9"]["action"], "move")
        self.assertEqual(plan["A9"]["target_room"], "living room")
        self.assertEqual(plan["A10"]["target_room"], "Office")
        self.assertNotIn("Living Room", out.split("Rooms to create:")[-1])

    def test_large_entity_list_is_batched(self):
        self.homekit.accessories = [accessory(f"B{i}", f"Light {i}", f"light.batch_{i}", "Office") for i in range(250)]
        code, out, err = self.run_script()
        self.assertEqual(code, 0, err)
        sizes = [len(body["variables"]["ids"]) for body in self.ha.requests]
        self.assertEqual(sizes, [100, 100, 50])
        self.assertEqual(len(self.plan_by_uuid(out)), 250)

    def test_help_runs(self):
        out = io.StringIO()
        with contextlib.redirect_stdout(out), self.assertRaises(SystemExit) as raised:
            manage_homekit_rooms.main(["--help"], {})
        self.assertEqual(raised.exception.code, 0)
        self.assertIn("--apply-plan", out.getvalue())

    def test_usage_error_exits_1(self):
        with contextlib.redirect_stderr(io.StringIO()) as err, self.assertRaises(SystemExit) as raised:
            manage_homekit_rooms.main(["--apply-plan", "a.csv", "--restore", "b.csv"], {})
        self.assertEqual(raised.exception.code, 1)
        self.assertIn("not allowed with argument", err.getvalue())

    def test_env_file_supplies_configuration(self):
        env_file = Path(self.output_dir) / "room-sync.env"
        env_file.write_text(
            f"# comment\nexport HASS_SERVER={self.ha.url}\nHASS_TOKEN=\"{TOKEN}\"\nHOMEKIT_MCP_URL={self.homekit.url}\n",
            encoding="utf-8",
        )
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = manage_homekit_rooms.main(["--env-file", str(env_file), "--output-dir", self.output_dir], {})
        self.assertEqual(code, 0, err.getvalue())
        self.assertEqual(self.plan_by_uuid(out.getvalue())["A1"]["target_room"], "Kitchen")


class ApplyTests(RoomSyncTestCase):
    def test_apply_moves_by_uuid_and_creates_room_once(self):
        before = self.rooms_of(UNMANAGED + ["A4", "A6", "A7"])
        code, out, err = self.run_script("--apply")
        self.assertEqual(code, 0, err)

        self.assertEqual(self.creates(), ["Bedroom"])
        bedroom = self.homekit.room_named("HOME-1", "Bedroom")["uuid"]
        self.assertEqual(
            self.moves(),
            {"A1": "R-KITCHEN", "A2": bedroom, "A3": bedroom, "A9": "R-LIVING", "A10": "R-OFFICE"},
        )
        self.assertEqual(self.homekit.room_of("A9"), "living room")
        self.assertEqual(self.homekit.room_of("A10"), "Office")
        self.assertEqual(self.rooms_of(before), before)
        self.assertIn("Moves applied: 5", out)

        snapshot_rows = read_rows(path_after(out, "Wrote snapshot CSV:"))
        self.assertEqual([row["accessory_uuid"] for row in snapshot_rows], MANAGED)
        self.assertEqual(snapshot_rows[0]["room"], "Office")
        self.assertEqual(set(snapshot_rows[0]), set(manage_homekit_rooms.SNAPSHOT_FIELDS))

    def test_second_apply_has_nothing_to_do(self):
        self.assertEqual(self.run_script("--apply")[0], 0)
        self.homekit.calls.clear()
        code, out, err = self.run_script("--apply")
        self.assertEqual(code, 0, err)
        self.assertIn("Nothing to apply.", out)
        self.assertEqual(self.homekit.mutating_calls(), [])

    def test_default_room_can_be_a_target(self):
        self.ha.entities["light.desk"] = {"area": "default room", "known": True}
        code, out, err = self.run_script("--apply")
        self.assertEqual(code, 0, err)
        self.assertEqual(self.plan_by_uuid(out)["A4"]["action"], "move")
        self.assertEqual(self.moves()["A4"], "R-DEFAULT")
        self.assertEqual(self.creates(), ["Bedroom"])
        self.assertEqual(self.homekit.room_of("A4"), "Default Room")

    def test_duplicate_serials_are_moved_by_uuid(self):
        twin = accessory("D1", "Kitchen Log Light", "light.kitchen_log_light", "Default Room")
        twin["room_uuid"] = "R-DEFAULT"
        self.homekit.accessories.append(twin)
        code, out, err = self.run_script("--apply")
        self.assertEqual(code, 0, err)
        asked = [entity_id for body in self.ha.requests for entity_id in body["variables"]["ids"]]
        self.assertEqual(asked.count("light.kitchen_log_light"), 1)
        self.assertEqual(self.moves()["A1"], "R-KITCHEN")
        self.assertEqual(self.moves()["D1"], "R-KITCHEN")
        self.assertEqual(self.rooms_of(["A1", "D1"]), {"A1": "Kitchen", "D1": "Kitchen"})

    def test_partial_failure_exits_2_and_lists_failures(self):
        self.homekit.fail_moves = {"A1", "A3"}
        code, out, err = self.run_script("--apply")
        self.assertEqual(code, 2)
        self.assertIn("Failures: 2", err)
        self.assertIn("Kitchen Log Light [light.kitchen_log_light] (A1)", err)
        self.assertIn("Bedside [light.bedside] (A3)", err)
        self.assertIn("HomeKit refused the move", err)
        self.assertEqual(self.homekit.room_of("A2"), "Bedroom")
        self.assertEqual(self.homekit.room_of("A1"), "Office")
        self.assertIn("Moves applied: 3", out)

    def test_create_room_failure_is_tried_once_and_fails_every_row_for_it(self):
        self.homekit.fail_creates = {"bedroom"}
        code, out, err = self.run_script("--apply")
        self.assertEqual(code, 2)
        self.assertEqual(self.creates(), ["Bedroom"])
        self.assertIn("Failures: 2", err)
        self.assertIn("(A2): cannot create room Bedroom: HomeKit refused to create the room", err)
        self.assertIn("(A3): cannot create room Bedroom: HomeKit refused to create the room", err)
        self.assertNotIn("A2", self.moves())
        self.assertEqual(self.homekit.room_of("A1"), "Kitchen")
        self.assertIn("Moves applied: 3", out)

    def test_apply_plan_skips_stale_rows(self):
        code, out, err = self.run_script()
        self.assertEqual(code, 0, err)
        plan_path = path_after(out, "Wrote plan CSV:")

        self.homekit.accessories.remove(self.homekit.accessory_by_uuid("A2"))
        self.homekit.move("A1", "Kitchen")
        self.homekit.move("A10", "living room")

        code, out, err = self.run_script("--apply-plan", plan_path)
        self.assertEqual(code, 0, err)
        self.assertEqual(self.creates(), ["Bedroom"])
        self.assertEqual(set(self.moves()), {"A3", "A9"})
        self.assertIn("Skipped: Blinds [cover.bedroom_blinds] (A2): accessory no longer exists", out)
        self.assertIn("Skipped: Kitchen Log Light [light.kitchen_log_light] (A1): already in Kitchen", out)
        self.assertIn("(A10): room changed since the plan was made (now living room)", out)
        self.assertEqual(self.homekit.room_of("A10"), "living room")

    def test_apply_plan_never_moves_unmanaged_accessories(self):
        code, out, _ = self.run_script()
        plan_path = path_after(out, "Wrote plan CSV:")
        edit_plan(
            plan_path,
            {
                "A5": {"action": "move", "target_room": "Office"},
                "A8": {"action": "move", "target_room": "Kitchen"},
                "A11": {"action": "move", "target_room": "Office"},
            },
        )
        before = self.rooms_of(["A5", "A8", "A11"])
        code, out, err = self.run_script("--apply-plan", plan_path)
        self.assertEqual(code, 0, err)
        self.assertIn("(A5): not managed (not bridged by Home Assistant)", out)
        self.assertIn("(A8): not managed (Home Assistant bridge)", out)
        self.assertIn("(A11): not managed (not bridged by Home Assistant)", out)
        self.assertEqual(self.rooms_of(before), before)

    def test_apply_plan_rejects_empty_target_room(self):
        code, out, _ = self.run_script()
        plan_path = path_after(out, "Wrote plan CSV:")
        edit_plan(plan_path, {"A1": {"target_room": "  "}})
        code, _, err = self.run_script("--apply-plan", plan_path)
        self.assertEqual(code, 1)
        self.assertIn("(A1): target_room is empty for a move row", err)
        self.assertEqual(self.homekit.mutating_calls(), [])

    def test_apply_plan_accepts_utf8_bom(self):
        code, out, _ = self.run_script()
        plan_path = Path(path_after(out, "Wrote plan CSV:"))
        plan_path.write_text("\ufeff" + plan_path.read_text(encoding="utf-8"), encoding="utf-8")
        code, _, err = self.run_script("--apply-plan", str(plan_path))
        self.assertEqual(code, 0, err)
        self.assertEqual(self.homekit.room_of("A1"), "Kitchen")

    def test_apply_plan_matches_lowercased_uuids(self):
        code, out, _ = self.run_script()
        plan_path = path_after(out, "Wrote plan CSV:")
        edit_plan(plan_path, {uuid: {"accessory_uuid": uuid.lower()} for uuid in MANAGED})
        code, _, err = self.run_script("--apply-plan", plan_path)
        self.assertEqual(code, 0, err)
        self.assertEqual(self.homekit.room_of("A1"), "Kitchen")

    def test_snapshot_restore_round_trip_leaves_unmanaged_alone(self):
        before = self.rooms_of(MANAGED)
        code, out, err = self.run_script("--apply")
        self.assertEqual(code, 0, err)
        snapshot_path = path_after(out, "Wrote snapshot CSV:")
        self.homekit.delete_room("Office")
        self.homekit.move("A5", "Default Room")
        self.homekit.move("A11", "Default Room")
        self.homekit.calls.clear()

        code, out, err = self.run_script("--restore", snapshot_path)
        self.assertEqual(code, 0, err)
        self.assertEqual(self.homekit.mutating_calls(), [])
        self.assertIn("Rooms to create: Office", out)

        code, out, err = self.run_script("--restore", snapshot_path, "--apply")
        self.assertEqual(code, 0, err)
        self.assertEqual(self.creates(), ["Office"])
        self.assertEqual(self.rooms_of(MANAGED), before)
        self.assertEqual(
            self.rooms_of(UNMANAGED),
            {"A5": "Default Room", "A8": "Default Room", "A11": "Default Room", "A12": "Kitchen"},
        )
        self.assertFalse(set(self.moves()) & set(UNMANAGED))

    def test_restore_skips_unmanaged_rows_in_snapshot(self):
        snapshot = Path(self.output_dir) / "old-snapshot.csv"
        rows = [
            {"accessory_uuid": "A5", "name": "Hue Bulb", "serial": "ABC123XYZ", "home": "Home", "room": "Office"},
            {"accessory_uuid": "A8", "name": "Bridge", "serial": "homekit.bridge", "home": "Home", "room": "Kitchen"},
            {"accessory_uuid": "A11", "name": "Other", "serial": "light.desk", "home": "Home", "room": "Office"},
            {"accessory_uuid": "A1", "name": "Kitchen Log Light", "serial": "", "home": "Home", "room": "Kitchen"},
            {"accessory_uuid": "GONE", "name": "Gone", "serial": "", "home": "Home", "room": "Kitchen"},
        ]
        manage_homekit_rooms.write_csv(snapshot, manage_homekit_rooms.SNAPSHOT_FIELDS, rows)
        code, out, err = self.run_script("--restore", str(snapshot), "--apply")
        self.assertEqual(code, 0, err)
        self.assertEqual(set(self.moves()), {"A1"})
        plan = self.plan_by_uuid(out)
        self.assertEqual(plan["A5"]["reason"], "not bridged by Home Assistant")
        self.assertEqual(plan["A8"]["reason"], "Home Assistant bridge")
        self.assertEqual(plan["A11"]["reason"], "not bridged by Home Assistant")
        self.assertEqual(plan["GONE"]["reason"], "accessory not found in Home")


class HomeSelectionTests(RoomSyncTestCase):
    accessories = ACCESSORIES + [
        accessory("H1", "Beach Light", "light.kitchen_log_light", "Deck", home="Beach House", home_uuid="HOME-2"),
    ]
    rooms = ROOMS + [
        room("H-DEFAULT", "Default Room", home="Beach House", home_uuid="HOME-2", is_default=True),
        room("H-DECK", "Deck", home="Beach House", home_uuid="HOME-2"),
    ]

    def test_several_homes_with_ha_accessories_require_home(self):
        code, out, err = self.run_script("--apply")
        self.assertEqual(code, 1)
        self.assertIn("--home", err)
        self.assertEqual(self.homekit.mutating_calls(), [])
        self.assertEqual(self.ha.requests, [])

    def test_home_option_limits_plan_to_that_home(self):
        code, out, err = self.run_script("--home", "beach house")
        self.assertEqual(code, 0, err)
        plan = self.plan_by_uuid(out)
        self.assertEqual(list(plan), ["H1"])
        self.assertEqual(plan["H1"]["action"], "create_room+move")
        self.assertEqual(plan["H1"]["target_room"], "Kitchen")

    def test_apply_plan_honours_home(self):
        code, out, err = self.run_script("--home", "Home")
        self.assertEqual(code, 0, err)
        plan_path = path_after(out, "Wrote plan CSV:")
        edit_plan(plan_path, {"A1": {"accessory_uuid": "H1", "current_room": "Deck"}})

        code, _, err = self.run_script("--apply-plan", plan_path)
        self.assertEqual(code, 1)
        self.assertIn("--home", err)

        code, out, err = self.run_script("--apply-plan", plan_path, "--home", "Home")
        self.assertEqual(code, 0, err)
        self.assertIn("(H1): accessory is in another home (Beach House)", out)
        self.assertEqual(self.homekit.room_of("H1"), "Deck")
        self.assertEqual(self.homekit.room_of("A2"), "Bedroom")

    def test_restore_honours_home(self):
        snapshot = Path(self.output_dir) / "snapshot.csv"
        rows = [
            {"accessory_uuid": "H1", "name": "Beach Light", "serial": "", "home": "Beach House", "room": "Kitchen"},
            {"accessory_uuid": "A1", "name": "Kitchen Log Light", "serial": "", "home": "Home", "room": "Kitchen"},
        ]
        manage_homekit_rooms.write_csv(snapshot, manage_homekit_rooms.SNAPSHOT_FIELDS, rows)
        self.assertEqual(self.run_script("--restore", str(snapshot))[0], 1)
        code, out, err = self.run_script("--restore", str(snapshot), "--home", "Home", "--apply")
        self.assertEqual(code, 0, err)
        self.assertEqual(self.plan_by_uuid(out)["H1"]["reason"], "accessory is in another home")
        self.assertEqual(set(self.moves()), {"A1"})

    def test_unknown_home_is_an_error(self):
        code, _, err = self.run_script("--home", "Moon Base")
        self.assertEqual(code, 1)
        self.assertIn("No home named 'Moon Base'", err)

    def test_home_names_differing_only_by_case_are_ambiguous(self):
        for item in self.homekit.rooms + self.homekit.accessories:
            if item["home_uuid"] == "HOME-2":
                item["home"] = "home"
        code, _, err = self.run_script("--home", "HOME")
        self.assertEqual(code, 1)
        self.assertIn("Several homes are named 'HOME'", err)

    def test_home_without_ha_accessories_does_not_need_option(self):
        self.homekit.accessories[-1]["bridge_serial_number"] = None
        code, out, err = self.run_script()
        self.assertEqual(code, 0, err)
        self.assertIn("Home: Home", out)
        self.assertNotIn("H1", self.plan_by_uuid(out))


class ErrorTests(RoomSyncTestCase):
    def test_token_never_in_output(self):
        outputs = []
        for argv in ((), ("--apply",)):
            code, out, err = self.run_script(*argv)
            self.assertEqual(code, 0, err)
            outputs += [out, err]
        self.ha.token = "a-different-token"
        code, out, err = self.run_script()
        outputs += [out, err]
        self.assertEqual(code, 1)
        self.assertIn("rejected the access token (HTTP 401)", err)
        self.assertIn("admin user", err)
        for text in outputs + [self.all_artifacts_text()]:
            self.assertNotIn(TOKEN, text)

    def test_redirect_is_refused_and_token_not_forwarded(self):
        elsewhere = FakeHomeAssistant(TOKEN, dict(HA_ENTITIES))
        self.addCleanup(elsewhere.server.stop)
        self.ha.reply = (307, "text/plain", "", {"Location": f"{elsewhere.url}/api/template"})
        code, _, err = self.run_script()
        self.assertEqual(code, 1)
        self.assertIn("redirects are not followed", err)
        self.assertEqual(elsewhere.seen_headers, [])

    def test_missing_ha_config_is_an_error(self):
        code, _, err = self.run_script(HASS_TOKEN="")
        self.assertEqual(code, 1)
        self.assertIn("HASS_SERVER and HASS_TOKEN must be set", err)

    def test_ha_unreachable_exits_1(self):
        code, _, err = self.run_script(HASS_SERVER=closed_port_url())
        self.assertEqual(code, 1)
        self.assertIn("Home Assistant request to", err)
        self.assertEqual(self.homekit.mutating_calls(), [])

    def test_ha_template_error_exits_1(self):
        self.ha.reply = (400, "application/json", '{"message": "Error rendering template: Invalid entity ID"}', {})
        code, _, err = self.run_script("--apply")
        self.assertEqual(code, 1)
        self.assertIn("HTTP 400", err)
        self.assertIn("Invalid entity ID", err)
        self.assertEqual(self.homekit.mutating_calls(), [])

    def test_ha_non_json_reply_exits_1(self):
        self.ha.reply = (200, "text/plain", "Kitchen", {})
        code, _, err = self.run_script()
        self.assertEqual(code, 1)
        self.assertIn("Invalid JSON response", err)

    def test_ha_reply_with_unexpected_entries_exits_1(self):
        self.ha.reply = (200, "text/plain", json.dumps({"light.desk": "Office"}), {})
        code, _, err = self.run_script()
        self.assertEqual(code, 1)
        self.assertIn("unexpected entry for light.desk", err)

    def test_homekit_unreachable_exits_1(self):
        code, _, err = self.run_script(HOMEKIT_MCP_URL=closed_port_url())
        self.assertEqual(code, 1)
        self.assertIn("HomeKit MCP app request to", err)
        self.assertIn("Check the app is running", err)

    def test_homekit_tool_error_is_reported(self):
        self.homekit.tool_errors["get_all_accessories"] = "HomeKit has not finished loading homes"
        code, _, err = self.run_script()
        self.assertEqual(code, 1)
        self.assertIn("HomeKit MCP app error: HomeKit has not finished loading homes", err)

    def test_homekit_without_structured_content_exits_1(self):
        self.homekit.structured = False
        code, _, err = self.run_script()
        self.assertEqual(code, 1)
        self.assertIn("returned no structuredContent", err)

    def test_no_homes_is_an_error(self):
        self.homekit.accessories = []
        self.homekit.rooms = []
        code, _, err = self.run_script()
        self.assertEqual(code, 1)
        self.assertIn("reports no homes", err)

    def test_unwritable_output_dir_exits_1(self):
        blocker = Path(self.output_dir) / "not-a-directory"
        blocker.write_text("", encoding="utf-8")
        code, _, err = self.run_script("--apply", output_dir=str(blocker))
        self.assertEqual(code, 1)
        self.assertIn("Error: Cannot create output directory", err)
        self.assertEqual(self.homekit.mutating_calls(), [])


class HomeAssistantStates:
    def __init__(self, states):
        self.states = states

    def __getattr__(self, name):
        return self.states.get(name)


@unittest.skipUnless(jinja2, "jinja2 is not installed")
class AreaTemplateTests(unittest.TestCase):
    def test_template_renders_area_and_known_flags(self):
        areas = {"light.kitchen": "Kitchen", "light.by_device": "Office"}
        devices = {"light.kitchen": "dev1", "light.by_device": "dev2", "switch.no_state": "dev3"}
        states = HomeAssistantStates({"light.kitchen": "on", "light.by_device": "off", "switch.no_area": "on"})
        env = jinja2.sandbox.ImmutableSandboxedEnvironment()
        env.globals.update(states=states, area_name=areas.get, device_id=devices.get)
        ids = ["light.kitchen", "light.by_device", "switch.no_area", "switch.no_state", "light.unknown"]
        rendered = env.from_string(manage_homekit_rooms.AREA_TEMPLATE).render(ids=ids)
        self.assertEqual(
            json.loads(rendered),
            {
                "light.kitchen": {"area": "Kitchen", "known": True},
                "light.by_device": {"area": "Office", "known": True},
                "switch.no_area": {"area": None, "known": True},
                "switch.no_state": {"area": None, "known": True},
                "light.unknown": {"area": None, "known": False},
            },
        )


if __name__ == "__main__":
    unittest.main()

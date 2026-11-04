import contextlib
import csv
import io
import socket
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import manage_homekit_rooms  # noqa: E402
from fakes import FakeHomeAssistant, FakeHomeKit, accessory, room  # noqa: E402


TOKEN = "test-token-5f1c9a7e-do-not-print"

HA_ENTITIES = {
    "light.kitchen_log_light": {"area": "Kitchen", "known": True},
    "cover.bedroom_blinds": {"area": "Bedroom", "known": True},
    "light.bedside": {"area": "Bedroom", "known": True},
    "light.desk": {"area": "Office", "known": True},
    "switch.fan": {"area": None, "known": True},
    "light.lounge_lamp": {"area": "Living Room", "known": True},
    "light.office_lamp": {"area": " Office ", "known": True},
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
    accessory("A5", "Hue Bulb", "ABC123XYZ", "Kitchen"),
    accessory("A6", "Ghost", "light.ghost", "Office"),
    accessory("A7", "Fan", "switch.fan", "Office"),
    accessory("A8", "Home Assistant Bridge", "homekit.bridge", "Office"),
    accessory("A9", "Lamp", "light.lounge_lamp", "Office"),
    accessory("A10", "Lamp", "light.office_lamp", "Kitchen"),
]


def closed_port_url() -> str:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    return f"http://127.0.0.1:{port}"


def read_rows(path: str):
    with open(path, newline="", encoding="utf-8") as handle:
        return list(csv.DictReader(handle))


def path_after(output: str, prefix: str) -> str:
    for line in output.splitlines():
        if line.startswith(prefix):
            return line[len(prefix) :].strip()
    raise AssertionError(f"No line starting with {prefix!r} in output:\n{output}")


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

    def run_script(self, *argv, **env_overrides):
        environ = {"HASS_SERVER": self.ha.url, "HASS_TOKEN": TOKEN, "HOMEKIT_MCP_URL": self.homekit.url}
        environ.update(env_overrides)
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = manage_homekit_rooms.main([*argv, "--output-dir", self.output_dir], environ)
        return code, out.getvalue(), err.getvalue()

    def plan_by_uuid(self, output: str):
        return {row["accessory_uuid"]: row for row in read_rows(path_after(output, "Wrote plan CSV:"))}

    def moves(self):
        return {args["accessory_uuid"]: args["room_uuid"] for tool, args in self.homekit.calls if tool == "set_accessory_room"}

    def creates(self):
        return [args["name"] for tool, args in self.homekit.calls if tool == "create_room"]

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
            "A5": ("skip", "Kitchen", "", "not a Home Assistant entity id"),
            "A6": ("skip", "Office", "", "entity not found in Home Assistant"),
            "A7": ("skip", "Office", "", "no area in Home Assistant"),
            "A8": ("skip", "Office", "", "Home Assistant bridge"),
            "A9": ("move", "Office", "living room", ""),
            "A10": ("move", "Kitchen", "Office", ""),
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

    def test_ha_is_asked_only_about_entity_id_serials(self):
        code, _, err = self.run_script()
        self.assertEqual(code, 0, err)
        asked = [entity_id for body in self.ha.requests for entity_id in body["variables"]["ids"]]
        self.assertEqual(
            sorted(asked),
            sorted(serial for serial in (a["serial_number"] for a in ACCESSORIES) if serial not in ("ABC123XYZ", "homekit.bridge")),
        )
        self.assertIn("area_name(id)", self.ha.requests[0]["template"])

    def test_room_match_ignores_case_and_whitespace(self):
        code, out, err = self.run_script()
        self.assertEqual(code, 0, err)
        plan = self.plan_by_uuid(out)
        self.assertEqual(plan["A9"]["action"], "move")
        self.assertEqual(plan["A9"]["target_room"], "living room")
        self.assertEqual(plan["A10"]["target_room"], "Office")
        self.assertNotIn("Living Room", out.split("Rooms to create:")[-1])

    def test_large_entity_list_is_batched(self):
        many = [accessory(f"B{i}", f"Light {i}", f"light.batch_{i}", "Office") for i in range(250)]
        self.homekit.accessories = many
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
        for untouched, room_name in (("A5", "Kitchen"), ("A6", "Office"), ("A7", "Office"), ("A8", "Office")):
            self.assertEqual(self.homekit.room_of(untouched), room_name)
        self.assertIn("Moves applied: 5", out)

        snapshot = {row["accessory_uuid"]: row for row in read_rows(path_after(out, "Wrote snapshot CSV:"))}
        self.assertEqual(snapshot["A1"]["room"], "Office")
        self.assertEqual(set(read_rows(path_after(out, "Wrote snapshot CSV:"))[0]), set(manage_homekit_rooms.SNAPSHOT_FIELDS))

    def test_second_apply_has_nothing_to_do(self):
        self.assertEqual(self.run_script("--apply")[0], 0)
        self.homekit.calls.clear()
        code, out, err = self.run_script("--apply")
        self.assertEqual(code, 0, err)
        self.assertIn("Nothing to apply.", out)
        self.assertEqual(self.homekit.mutating_calls(), [])

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

    def test_apply_plan_matches_uppercase_uuids_case_insensitively(self):
        code, out, _ = self.run_script()
        plan_path = Path(path_after(out, "Wrote plan CSV:"))
        rows = read_rows(str(plan_path))
        for row in rows:
            row["accessory_uuid"] = row["accessory_uuid"].lower()
        manage_homekit_rooms.write_csv(plan_path, manage_homekit_rooms.PLAN_FIELDS, rows)

        code, _, err = self.run_script("--apply-plan", str(plan_path))
        self.assertEqual(code, 0, err)
        self.assertEqual(self.homekit.room_of("A1"), "Kitchen")

    def test_snapshot_restore_round_trip(self):
        before = {item["uuid"]: item["room"] for item in self.homekit.accessories}
        code, out, err = self.run_script("--apply")
        self.assertEqual(code, 0, err)
        snapshot_path = path_after(out, "Wrote snapshot CSV:")
        self.homekit.delete_room("Office")
        self.homekit.calls.clear()

        code, out, err = self.run_script("--restore", snapshot_path)
        self.assertEqual(code, 0, err)
        self.assertEqual(self.homekit.mutating_calls(), [])
        self.assertIn("Rooms to create: Office", out)

        code, out, err = self.run_script("--restore", snapshot_path, "--apply")
        self.assertEqual(code, 0, err)
        self.assertEqual(self.creates(), ["Office"])
        after = {item["uuid"]: item["room"] for item in self.homekit.accessories}
        self.assertEqual(after, before)


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

    def test_unknown_home_is_an_error(self):
        code, _, err = self.run_script("--home", "Moon Base")
        self.assertEqual(code, 1)
        self.assertIn("No home named 'Moon Base'", err)

    def test_home_without_ha_accessories_does_not_need_option(self):
        self.homekit.accessories[-1]["serial_number"] = "NATIVE-1"
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
        for text in outputs + [self.all_artifacts_text()]:
            self.assertNotIn(TOKEN, text)

    def test_missing_ha_config_is_an_error(self):
        code, _, err = self.run_script(HASS_TOKEN="")
        self.assertEqual(code, 1)
        self.assertIn("HASS_SERVER and HASS_TOKEN must be set", err)

    def test_ha_unreachable_exits_1(self):
        code, _, err = self.run_script(HASS_SERVER=closed_port_url())
        self.assertEqual(code, 1)
        self.assertIn("Home Assistant request to", err)
        self.assertEqual(self.homekit.mutating_calls(), [])

    def test_homekit_unreachable_exits_1(self):
        code, _, err = self.run_script(HOMEKIT_MCP_URL=closed_port_url())
        self.assertEqual(code, 1)
        self.assertIn("HomeKit MCP app request to", err)
        self.assertIn("Check the app is running", err)


if __name__ == "__main__":
    unittest.main()

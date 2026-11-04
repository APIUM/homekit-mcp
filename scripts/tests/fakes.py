import copy
import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import Callable, Dict, List, Optional, Tuple


Handler = Callable[[str, dict, Dict[str, str]], Tuple[int, str, str]]


class FakeServer:
    def __init__(self, handler: Handler):
        class RequestHandler(BaseHTTPRequestHandler):
            def do_POST(self):
                length = int(self.headers.get("Content-Length") or 0)
                body = json.loads(self.rfile.read(length) or b"{}")
                status, content_type, text = handler(self.path, body, dict(self.headers))
                data = text.encode("utf-8")
                self.send_response(status)
                self.send_header("Content-Type", content_type)
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def log_message(self, *args):
                pass

        self.server = HTTPServer(("127.0.0.1", 0), RequestHandler)
        self.url = f"http://127.0.0.1:{self.server.server_port}"
        self.thread = threading.Thread(target=self.server.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True)
        self.thread.start()

    def stop(self) -> None:
        self.server.shutdown()
        self.server.server_close()
        self.thread.join()


class FakeHomeAssistant:
    def __init__(self, token: str, entities: Dict[str, dict]):
        self.token = token
        self.entities = entities
        self.requests: List[dict] = []
        self.server = FakeServer(self.handle)
        self.url = self.server.url

    def handle(self, path: str, body: dict, headers: Dict[str, str]) -> Tuple[int, str, str]:
        if path != "/api/template":
            return 404, "text/plain", "404: Not Found"
        if headers.get("Authorization") != f"Bearer {self.token}":
            return 401, "text/plain", "401: Unauthorized"
        self.requests.append(body)
        ids = body.get("variables", {}).get("ids", [])
        result = {
            entity_id: self.entities.get(entity_id, {"area": None, "known": False}) for entity_id in ids
        }
        return 200, "text/plain; charset=utf-8", json.dumps(result)


def accessory(uuid: str, name: str, serial: str, room: str, home: str = "Home", home_uuid: str = "HOME-1") -> dict:
    return {
        "uuid": uuid,
        "name": name,
        "serial_number": serial,
        "room": room,
        "room_uuid": "",
        "home": home,
        "home_uuid": home_uuid,
        "category": "Lightbulb",
        "reachable": True,
    }


def room(uuid: str, name: str, home: str = "Home", home_uuid: str = "HOME-1", is_default: bool = False) -> dict:
    return {"uuid": uuid, "name": name, "home": home, "home_uuid": home_uuid, "is_default": is_default}


class FakeHomeKit:
    def __init__(self, accessories: List[dict], rooms: List[dict]):
        self.accessories = copy.deepcopy(accessories)
        self.rooms = copy.deepcopy(rooms)
        for item in self.accessories:
            match = self.room_named(item["home_uuid"], item["room"])
            item["room_uuid"] = match["uuid"] if match else ""
        self.calls: List[Tuple[str, dict]] = []
        self.fail_moves = set()
        self.room_counter = 0
        self.server = FakeServer(self.handle)
        self.url = self.server.url

    def room_named(self, home_uuid: str, name: str) -> Optional[dict]:
        for item in self.rooms:
            if item["home_uuid"] == home_uuid and item["name"].strip().casefold() == name.strip().casefold():
                return item
        return None

    def accessory_by_uuid(self, uuid: str) -> Optional[dict]:
        return next((item for item in self.accessories if item["uuid"] == uuid), None)

    def room_of(self, uuid: str) -> str:
        return self.accessory_by_uuid(uuid)["room"]

    def move(self, uuid: str, room_name: str) -> None:
        item = self.accessory_by_uuid(uuid)
        target = self.room_named(item["home_uuid"], room_name)
        item["room"] = target["name"]
        item["room_uuid"] = target["uuid"]

    def delete_room(self, name: str, home_uuid: str = "HOME-1") -> None:
        doomed = self.room_named(home_uuid, name)
        default = next(r for r in self.rooms if r["home_uuid"] == home_uuid and r["is_default"])
        self.rooms.remove(doomed)
        for item in self.accessories:
            if item["room_uuid"] == doomed["uuid"]:
                item["room"] = default["name"]
                item["room_uuid"] = default["uuid"]

    def mutating_calls(self) -> List[Tuple[str, dict]]:
        return [call for call in self.calls if not call[0].startswith("get_")]

    def handle(self, path: str, body: dict, headers: Dict[str, str]) -> Tuple[int, str, str]:
        if path != "/mcp/tools/call":
            return 404, "text/plain", "Not Found"
        tool = body["params"]["name"]
        arguments = body["params"].get("arguments", {})
        self.calls.append((tool, arguments))
        try:
            structured = self.run_tool(tool, arguments)
        except ValueError as exc:
            reply = {"jsonrpc": "2.0", "id": body.get("id"), "error": {"code": -32000, "message": str(exc)}}
        else:
            reply = {
                "jsonrpc": "2.0",
                "id": body.get("id"),
                "result": {"content": [{"type": "text", "text": "ok"}], "structuredContent": structured},
            }
        return 200, "application/json", json.dumps(reply)

    def run_tool(self, tool: str, arguments: dict) -> dict:
        if tool == "get_all_accessories":
            return {"accessories": copy.deepcopy(self.accessories)}
        if tool == "get_all_rooms":
            return {"rooms": copy.deepcopy(self.rooms)}
        if tool == "create_room":
            home = next(r for r in self.rooms if r["home_uuid"] == arguments["home_uuid"])
            existing = self.room_named(arguments["home_uuid"], arguments["name"])
            if existing:
                return {"room": {**existing, "created": False}}
            self.room_counter += 1
            new_room = room(f"NEW-ROOM-{self.room_counter}", arguments["name"], home["home"], home["home_uuid"])
            self.rooms.append(new_room)
            return {"room": {**new_room, "created": True}}
        if tool == "set_accessory_room":
            item = self.accessory_by_uuid(arguments["accessory_uuid"])
            target = next((r for r in self.rooms if r["uuid"] == arguments["room_uuid"]), None)
            if item is None or target is None:
                raise ValueError("Accessory or room not found")
            if item["uuid"] in self.fail_moves:
                raise ValueError("HomeKit refused the move")
            item["room"] = target["name"]
            item["room_uuid"] = target["uuid"]
            return {"accessory_uuid": item["uuid"], "room_uuid": target["uuid"], "room": target["name"]}
        raise ValueError(f"Unknown tool {tool}")

    def stop(self) -> None:
        self.server.stop()

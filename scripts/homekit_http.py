import http.client
import json
import urllib.error
import urllib.parse
import urllib.request
from typing import Dict, Optional


class RequestError(Exception):
    def __init__(self, message: str, status: Optional[int] = None):
        super().__init__(message)
        self.status = status


def post_json(url: str, body: dict, headers: Optional[Dict[str, str]] = None, timeout: float = 30.0) -> object:
    try:
        request = urllib.request.Request(
            url,
            data=json.dumps(body).encode("utf-8"),
            headers={"Content-Type": "application/json", **(headers or {})},
            method="POST",
        )
        with urllib.request.urlopen(request, timeout=timeout) as response:
            payload = response.read().decode("utf-8")
    except urllib.error.HTTPError as exc:
        message = exc.read().decode("utf-8", errors="replace")
        raise RequestError(f"Server returned HTTP {exc.code}: {message}", status=exc.code) from exc
    except urllib.error.URLError as exc:
        raise RequestError(f"Request failed: {exc.reason}") from exc
    except (OSError, ValueError, http.client.HTTPException) as exc:
        raise RequestError(f"Request failed: {exc}") from exc

    try:
        return json.loads(payload)
    except json.JSONDecodeError as exc:
        raise RequestError(f"Invalid JSON response: {exc}") from exc


def endpoint(base_url: str, path: str) -> str:
    return urllib.parse.urljoin(base_url.rstrip("/") + "/", path.lstrip("/"))


def tool_call_body(tool_name: str, arguments: dict, request_id: int = 1) -> dict:
    return {
        "jsonrpc": "2.0",
        "id": request_id,
        "method": "tools/call",
        "params": {
            "name": tool_name,
            "arguments": arguments,
        },
    }

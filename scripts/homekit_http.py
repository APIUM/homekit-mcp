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


class RefuseRedirects(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


# urllib copies the Authorization header to any host a redirect names, and would send it through a system proxy.
opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), RefuseRedirects)


def post_json(url: str, body: dict, headers: Optional[Dict[str, str]] = None, timeout: float = 30.0) -> object:
    try:
        request = urllib.request.Request(
            url,
            data=json.dumps(body).encode("utf-8"),
            headers={"Content-Type": "application/json", **(headers or {})},
            method="POST",
        )
        with opener.open(request, timeout=timeout) as response:
            payload = response.read().decode("utf-8")
    except urllib.error.HTTPError as exc:
        if 300 <= exc.code < 400:
            location = exc.headers.get("Location", "another URL")
            raise RequestError(
                f"Server redirected (HTTP {exc.code}) to {location}; redirects are not followed, use the final URL",
                status=exc.code,
            ) from exc
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

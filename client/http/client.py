"""Низкоуровневый HTTP-клиент к headless-серверу opencode.

Использует только стандартную библиотеку (urllib/http.client). Поддерживает:
  * Basic-auth (username/password из конфига);
  * обязательный для opencode заголовок ``x-opencode-directory`` (work_dir),
    а также query-параметр ``directory`` для GET/HEAD (как делает JS SDK);
  * JSON тела запросов/ответов.
"""
from __future__ import annotations

import base64
import json
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Dict, Optional

from ..core.errors import AuthenticationError, SessionError


class HttpClient:
    def __init__(
        self,
        base_url: str,
        username: str,
        password: str,
        directory: str,
        timeout: float = 30.0,
    ):
        self.base_url = base_url.rstrip("/")
        self.username = username
        self.password = password
        self.directory = directory
        self.timeout = timeout
        self._auth = base64.b64encode(f"{username}:{password}".encode()).decode()

    def _headers(self, with_directory: bool = True) -> Dict[str, str]:
        headers = {
            "Authorization": f"Basic {self._auth}",
            "Accept": "application/json",
            "Content-Type": "application/json",
            "User-Agent": "gigatool-headless-python-client",
        }
        if with_directory:
            headers["x-opencode-directory"] = urllib.parse.quote(self.directory)
        return headers

    def _url(self, path: str, params: Optional[Dict[str, Any]] = None) -> str:
        url = f"{self.base_url}{path}"
        if params:
            url += "?" + urllib.parse.urlencode(params)
        return url

    def request(
        self,
        method: str,
        path: str,
        json_body: Optional[Any] = None,
        params: Optional[Dict[str, Any]] = None,
        with_directory: bool = True,
        timeout: Optional[float] = None,
    ) -> Any:
        url = self._url(path, params)
        body = None
        if json_body is not None:
            body = json.dumps(json_body).encode("utf-8")
        req = urllib.request.Request(url, data=body, method=method, headers=self._headers(with_directory))
        try:
            with urllib.request.urlopen(req, timeout=timeout or self.timeout) as resp:
                raw = resp.read()
                if not raw:
                    return None
                # Response может быть text/event-stream — тогда просто вернуть bytes.
                ctype = resp.headers.get("Content-Type", "")
                if "json" in ctype:
                    return json.loads(raw.decode("utf-8"))
                return raw.decode("utf-8")
        except urllib.error.HTTPError as e:
            detail = e.read().decode("utf-8", "replace")
            if e.code in (401, 403):
                raise AuthenticationError(f"{method} {path} -> HTTP {e.code}: {detail}") from e
            raise SessionError(f"{method} {path} -> HTTP {e.code}: {detail}") from e
        except urllib.error.URLError as e:
            raise SessionError(f"{method} {path} -> network error: {e!r}") from e

    def get(self, path: str, params: Optional[Dict[str, Any]] = None, **kw) -> Any:
        return self.request("GET", path, params=params, **kw)

    def post(self, path: str, json_body: Optional[Any] = None, **kw) -> Any:
        return self.request("POST", path, json_body=json_body, **kw)
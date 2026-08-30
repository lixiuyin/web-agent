"""Two-origin deterministic website for safe, stateful browser-agent evaluation."""

from __future__ import annotations

import base64
import hashlib
import json
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any
from urllib.parse import parse_qs, urlparse

_FILE_PAYLOAD = b"case_id,status\nORBIT-731,ready\n"


class _SandboxState:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self.reset()

    def reset(self) -> None:
        with getattr(self, "_lock", threading.Lock()):
            self.logged_in = False
            self.cross_origin: dict[str, str] = {"case_id": "", "owner": "", "priority": ""}
            self.upload: dict[str, str] = {"name": "", "sha256": ""}
            self.cart: dict[str, int] = {"orbit": 0}
            self.order: dict[str, Any] = {
                "submitted": False,
                "address": "",
                "terms": False,
            }

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            return {
                "logged_in": self.logged_in,
                "cross_origin": dict(self.cross_origin),
                "upload": dict(self.upload),
                "cart": dict(self.cart),
                "order": dict(self.order),
            }


def _page(title: str, body: str, *, script: str = "") -> bytes:
    return f"""<!doctype html>
<html lang="en">
<head><meta charset="utf-8"><title>{title}</title></head>
<body><main>{body}</main>{f"<script>{script}</script>" if script else ""}</body>
</html>""".encode()


def _handler(
    state: _SandboxState,
    *,
    role: str,
    origins: dict[str, str],
) -> type[BaseHTTPRequestHandler]:
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, _format: str, *args: object) -> None:
            del args

        def _send(
            self,
            body: bytes,
            *,
            status: HTTPStatus = HTTPStatus.OK,
            content_type: str = "text/html; charset=utf-8",
            headers: dict[str, str] | None = None,
        ) -> None:
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            for name, value in (headers or {}).items():
                self.send_header(name, value)
            self.end_headers()
            self.wfile.write(body)

        def _redirect(self, location: str, *, cookie: str | None = None) -> None:
            self.send_response(HTTPStatus.SEE_OTHER)
            self.send_header("Location", location)
            if cookie:
                self.send_header("Set-Cookie", cookie)
            self.end_headers()

        def _form(self) -> dict[str, list[str]]:
            length = int(self.headers.get("Content-Length", "0"))
            return parse_qs(self.rfile.read(length).decode())

        def do_GET(self) -> None:
            parsed = urlparse(self.path)
            path = parsed.path
            if path == "/api/state":
                self._send(
                    json.dumps(state.snapshot()).encode(),
                    content_type="application/json",
                )
                return
            if role == "primary":
                self._get_primary(path)
                return
            self._get_secondary(path, parse_qs(parsed.query))

        def _get_primary(self, path: str) -> None:
            if path == "/api/spa/items":
                payload = {
                    "items": [
                        {"id": "ember", "name": "Ember Queue", "active": False},
                        {"id": "orbit", "name": "Orbit Queue", "active": True},
                        {"id": "river", "name": "River Queue", "active": True},
                    ]
                }
                self._send(json.dumps(payload).encode(), content_type="application/json")
                return
            if path in {"/spa", "/spa/items/orbit"}:
                self._send(
                    _page(
                        "Queue SPA",
                        """
                        <h1>Queue console</h1>
                        <button id="show-active" disabled>Show active queues</button>
                        <div id="app">Hydrating…</div>
                        """,
                        script="""
                        let items = [];
                        async function hydrate() {
                          const response = await fetch('/api/spa/items');
                          items = (await response.json()).items;
                          document.querySelector('#show-active').disabled = false;
                          render(items);
                        }
                        function render(values) {
                          document.querySelector('#app').innerHTML = values.map(item =>
                            `<button class="queue" id="open-${item.id}" data-id="${item.id}">${item.name}</button>`
                          ).join('');
                          document.querySelectorAll('.queue').forEach(button => {
                            button.addEventListener('click', () => openQueue(button.dataset.id));
                          });
                        }
                        function openQueue(id) {
                          const item = items.find(candidate => candidate.id === id);
                          history.pushState({id}, '', `/spa/items/${id}`);
                          document.querySelector('#app').innerHTML =
                            `<h2 id="queue-name">${item.name}</h2><p id="queue-status">Status: active</p>`;
                        }
                        document.querySelector('#show-active').addEventListener('click', () => {
                          render(items.filter(item => item.active));
                        });
                        hydrate();
                        """,
                    )
                )
                return
            if path == "/login":
                self._send(
                    _page(
                        "Sandbox login",
                        """
                        <h1>Sandbox account</h1>
                        <form method="post" action="/login">
                          <label>User <input id="username" name="username"></label>
                          <label>Password <input id="password" name="password" type="password"></label>
                          <button id="sign-in" type="submit">Sign in</button>
                        </form>
                        """,
                    )
                )
                return
            if path == "/account":
                cookie = self.headers.get("Cookie", "")
                if "sandbox_session=valid" not in cookie:
                    self._redirect("/login")
                    return
                self._send(
                    _page(
                        "Protected account",
                        '<h1 id="welcome">Welcome, benchmark-agent</h1><p id="plan">Plan: sandbox</p>',
                    )
                )
                return
            if path == "/handoff":
                self._send(
                    _page(
                        "Case handoff",
                        f"""
                        <h1>Case handoff</h1><p id="case-id">Case: ORBIT-731</p>
                        <a id="continue-intake" href="{origins["secondary"]}/intake?case=ORBIT-731">
                          Continue intake on partner origin
                        </a>
                        """,
                    )
                )
                return
            if path == "/files":
                self._send(
                    _page(
                        "File handoff",
                        f"""
                        <h1>File handoff</h1>
                        <a id="download-payload" download="sandbox-payload.txt"
                           href="/downloads/sandbox-payload.txt">Download case file</a>
                        <a id="upload-destination" href="{origins["secondary"]}/upload">
                          Continue to upload portal
                        </a>
                        """,
                    )
                )
                return
            if path == "/downloads/sandbox-payload.txt":
                self._send(
                    _FILE_PAYLOAD,
                    content_type="text/plain; charset=utf-8",
                    headers={"Content-Disposition": 'attachment; filename="sandbox-payload.txt"'},
                )
                return
            if path == "/shop":
                self._send(
                    _page(
                        "Sandbox shop",
                        """
                        <h1>Sandbox shop</h1><p>Orbit Notebook — test credits only</p>
                        <form method="post" action="/cart/add">
                          <button id="add-orbit" type="submit">Add sandbox item</button>
                        </form>
                        """,
                    )
                )
                return
            self._send(_page("Not found", "<h1>Not found</h1>"), status=HTTPStatus.NOT_FOUND)

        def _get_secondary(self, path: str, query: dict[str, list[str]]) -> None:
            if path == "/intake":
                case_id = query.get("case", [""])[0]
                self._send(
                    _page(
                        "Partner intake",
                        f"""
                        <h1>Partner intake</h1>
                        <form method="post" action="/intake">
                          <label>Case <input id="intake-case" name="case_id" value="{case_id}"></label>
                          <label>Owner <input id="intake-owner" name="owner"></label>
                          <label>Priority <select id="intake-priority" name="priority">
                            <option value="normal">Normal</option><option value="urgent">Urgent</option>
                          </select></label>
                          <button id="submit-intake" type="submit">Submit sandbox intake</button>
                        </form>
                        """,
                    )
                )
                return
            if path == "/intake/complete":
                self._send(_page("Intake complete", '<p id="intake-complete">Intake complete</p>'))
                return
            if path == "/upload":
                self._send(
                    _page(
                        "Upload portal",
                        """
                        <h1>Upload portal</h1>
                        <form method="post" action="/upload">
                          <input id="upload-file" type="file">
                          <input id="file-name" name="file_name" type="hidden">
                          <input id="file-content" name="file_content" type="hidden">
                          <button id="submit-upload" type="submit" disabled>Submit sandbox upload</button>
                        </form><p id="upload-state">Waiting for file</p>
                        """,
                        script="""
                        document.querySelector('#upload-file').addEventListener('change', async event => {
                          const file = event.target.files[0];
                          document.querySelector('#file-name').value = file.name;
                          const bytes = new Uint8Array(await file.arrayBuffer());
                          document.querySelector('#file-content').value =
                            btoa(String.fromCharCode(...bytes));
                          document.querySelector('#submit-upload').disabled = false;
                          document.querySelector('#upload-state').id = 'upload-ready';
                          document.querySelector('#upload-ready').textContent = 'File ready';
                        });
                        """,
                    )
                )
                return
            if path == "/upload/complete":
                self._send(_page("Upload complete", '<p id="upload-complete">Upload complete</p>'))
                return
            if path == "/checkout":
                self._send(
                    _page(
                        "Sandbox checkout",
                        """
                        <h1>Sandbox checkout</h1><p>No payment method; test credits only.</p>
                        <form method="post" action="/checkout">
                          <label>Address <input id="order-address" name="address"></label>
                          <label><input id="order-terms" name="terms" type="checkbox" value="yes"> Accept sandbox terms</label>
                          <button id="place-sandbox-order" type="submit">Place sandbox order</button>
                        </form>
                        """,
                    )
                )
                return
            if path == "/order/complete":
                self._send(
                    _page("Order complete", '<p id="order-complete">Sandbox order placed</p>')
                )
                return
            self._send(_page("Not found", "<h1>Not found</h1>"), status=HTTPStatus.NOT_FOUND)

        def do_POST(self) -> None:
            path = urlparse(self.path).path
            form = self._form()
            if path == "/api/reset":
                state.reset()
                self._send(b'{"ok": true}', content_type="application/json")
                return
            if role == "primary" and path == "/login":
                valid = form.get("username") == ["benchmark-agent"] and form.get("password") == [
                    "orbit42"
                ]
                if valid:
                    with state._lock:
                        state.logged_in = True
                    self._redirect(
                        "/account",
                        cookie="sandbox_session=valid; HttpOnly; SameSite=Lax; Path=/",
                    )
                else:
                    self._redirect("/login")
                return
            if role == "primary" and path == "/cart/add":
                with state._lock:
                    state.cart["orbit"] = 1
                self._redirect(f"{origins['secondary']}/checkout")
                return
            if role == "secondary" and path == "/intake":
                with state._lock:
                    state.cross_origin = {
                        "case_id": form.get("case_id", [""])[0],
                        "owner": form.get("owner", [""])[0],
                        "priority": form.get("priority", [""])[0],
                    }
                self._redirect("/intake/complete")
                return
            if role == "secondary" and path == "/upload":
                content = base64.b64decode(form.get("file_content", [""])[0])
                with state._lock:
                    state.upload = {
                        "name": form.get("file_name", [""])[0],
                        "sha256": hashlib.sha256(content).hexdigest(),
                    }
                self._redirect("/upload/complete")
                return
            if role == "secondary" and path == "/checkout":
                with state._lock:
                    state.order = {
                        "submitted": True,
                        "address": form.get("address", [""])[0],
                        "terms": form.get("terms") == ["yes"],
                    }
                self._redirect("/order/complete")
                return
            self._send(_page("Not found", "<h1>Not found</h1>"), status=HTTPStatus.NOT_FOUND)

    return Handler


@dataclass(frozen=True)
class SandboxOrigins:
    primary: str
    secondary: str


@contextmanager
def sandbox_interaction_site() -> Iterator[SandboxOrigins]:
    """Run two isolated loopback origins that share only evaluator-visible state."""
    state = _SandboxState()
    origins: dict[str, str] = {}
    primary = ThreadingHTTPServer(
        ("127.0.0.1", 0), _handler(state, role="primary", origins=origins)
    )
    secondary = ThreadingHTTPServer(
        ("127.0.0.1", 0), _handler(state, role="secondary", origins=origins)
    )
    origins.update(
        {
            "primary": f"http://127.0.0.1:{primary.server_address[1]}",
            "secondary": f"http://127.0.0.1:{secondary.server_address[1]}",
        }
    )
    threads = [
        threading.Thread(target=server.serve_forever, daemon=True)
        for server in (primary, secondary)
    ]
    for thread in threads:
        thread.start()
    try:
        yield SandboxOrigins(**origins)
    finally:
        for server in (primary, secondary):
            server.shutdown()
            server.server_close()
        for thread in threads:
            thread.join(timeout=5)


__all__ = ["SandboxOrigins", "sandbox_interaction_site"]

"""Stateful 60-stage website for reproducible long-horizon browser evaluation."""

from __future__ import annotations

import json
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any
from urllib.parse import parse_qs, urlparse

STAGE_COUNT = 60
CUES = {4: "CEDAR", 14: "ORBIT", 24: "LANTERN", 34: "DELTA"}
RECALLS = {44: "CEDAR", 49: "ORBIT", 54: "LANTERN", 59: "DELTA"}
TRANSIENT_STAGE = 21


class _MissionState:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self.reset()

    def reset(self) -> None:
        with getattr(self, "_lock", threading.Lock()):
            self.stage = 1
            self.completed = False
            self.invalid_actions = 0
            self.memory_errors = 0
            self.recalled: list[str] = []
            self.transient_seen = False

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            return {
                "stage": self.stage,
                "completed": self.completed,
                "invalid_actions": self.invalid_actions,
                "memory_errors": self.memory_errors,
                "recalled": list(self.recalled),
                "expected_sequence": list(CUES.values()),
            }

    def advance(self, requested_stage: int, answer: str) -> bool:
        with self._lock:
            if self.completed or requested_stage != self.stage:
                self.invalid_actions += 1
                return False
            expected = RECALLS.get(self.stage)
            if expected is not None and answer.strip().upper() != expected:
                self.invalid_actions += 1
                self.memory_errors += 1
                return False
            if expected is not None:
                self.recalled.append(expected)
            if self.stage == STAGE_COUNT:
                self.completed = True
            else:
                self.stage += 1
            return True


def _page(title: str, body: str) -> bytes:
    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><title>{title}</title></head>
<body><main>{body}</main></body></html>""".encode()


def _stage_page(stage: int, *, error: bool = False) -> bytes:
    cue = (
        f'<aside id="memory-cue">Durable mission cue: <strong>{CUES[stage]}</strong>. '
        "Retain it for a later checkpoint.</aside>"
        if stage in CUES
        else ""
    )
    warning = (
        '<p id="action-error">That action was rejected; inspect and retry.</p>' if error else ""
    )
    if stage in RECALLS:
        body = f"""
        <h1>Mission stage {stage} of {STAGE_COUNT}</h1>{warning}
        <p>Enter the durable cue introduced at stage {stage - 40}, then continue.</p>
        <form method="post" action="/advance">
          <input type="hidden" name="stage" value="{stage}">
          <label>Remembered cue <input id="recall-answer" name="answer" autocomplete="off"></label>
          <button id="continue-{stage}" type="submit">Validate cue and continue</button>
        </form>
        """
    else:
        body = f"""
        <h1>Mission stage {stage} of {STAGE_COUNT}</h1>{warning}{cue}
        <p>Continue the evidence-preserving workflow. Do not use the abort control.</p>
        <form method="post" action="/advance">
          <input type="hidden" name="stage" value="{stage}">
          <button id="continue-{stage}" type="submit">Continue stage {stage}</button>
        </form>
        <button id="abort-{stage}" type="button">Abort mission</button>
        """
    return _page(f"Mission stage {stage}", body)


def _handler(state: _MissionState) -> type[BaseHTTPRequestHandler]:
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, _format: str, *args: object) -> None:
            del args

        def _send(
            self,
            body: bytes,
            *,
            status: HTTPStatus = HTTPStatus.OK,
            content_type: str = "text/html; charset=utf-8",
        ) -> None:
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def _redirect(self, location: str) -> None:
            self.send_response(HTTPStatus.SEE_OTHER)
            self.send_header("Location", location)
            self.end_headers()

        def do_GET(self) -> None:
            parsed = urlparse(self.path)
            if parsed.path == "/api/state":
                self._send(
                    json.dumps(state.snapshot()).encode(),
                    content_type="application/json",
                )
                return
            if parsed.path == "/api/reset":
                state.reset()
                self._send(json.dumps(state.snapshot()).encode(), content_type="application/json")
                return
            if parsed.path in {"/", "/mission"}:
                snapshot = state.snapshot()
                if snapshot["completed"]:
                    self._send(
                        _page(
                            "Mission complete",
                            '<h1 id="mission-complete">Mission complete</h1>'
                            "<p>The retained cue sequence must be reported from memory.</p>",
                        )
                    )
                    return
                self._redirect(f"/mission/{snapshot['stage']}")
                return
            if parsed.path.startswith("/mission/"):
                try:
                    stage = int(parsed.path.rsplit("/", 1)[1])
                except ValueError:
                    self._send(
                        _page("Not found", "<h1>Not found</h1>"), status=HTTPStatus.NOT_FOUND
                    )
                    return
                snapshot = state.snapshot()
                if snapshot["completed"]:
                    self._redirect("/mission")
                    return
                if stage != snapshot["stage"]:
                    self._redirect(f"/mission/{snapshot['stage']}")
                    return
                if stage == TRANSIENT_STAGE and not state.transient_seen:
                    state.transient_seen = True
                    self._send(
                        _page(
                            "Transient interruption",
                            f"""<h1>Transient interruption</h1>
                            <p>The workflow state is intact. Retry this stage.</p>
                            <a id="retry-{stage}" href="/mission/{stage}">Retry stage {stage}</a>""",
                        ),
                        status=HTTPStatus.SERVICE_UNAVAILABLE,
                    )
                    return
                error = parse_qs(parsed.query).get("error") == ["1"]
                self._send(_stage_page(stage, error=error))
                return
            self._send(_page("Not found", "<h1>Not found</h1>"), status=HTTPStatus.NOT_FOUND)

        def do_POST(self) -> None:
            if urlparse(self.path).path == "/api/reset":
                state.reset()
                self._send(json.dumps(state.snapshot()).encode(), content_type="application/json")
                return
            if urlparse(self.path).path != "/advance":
                self._send(_page("Not found", "<h1>Not found</h1>"), status=HTTPStatus.NOT_FOUND)
                return
            length = int(self.headers.get("Content-Length", "0"))
            values = parse_qs(self.rfile.read(length).decode())
            try:
                stage = int(values.get("stage", ["0"])[0])
            except ValueError:
                stage = 0
            accepted = state.advance(stage, values.get("answer", [""])[0])
            snapshot = state.snapshot()
            if snapshot["completed"]:
                self._redirect("/mission")
            else:
                suffix = "?error=1" if not accepted else ""
                self._redirect(f"/mission/{snapshot['stage']}{suffix}")

    return Handler


@contextmanager
def long_horizon_site() -> Iterator[str]:
    """Run an isolated mission server on an ephemeral localhost port."""
    state = _MissionState()
    server = ThreadingHTTPServer(("127.0.0.1", 0), _handler(state))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


__all__ = ["CUES", "RECALLS", "STAGE_COUNT", "long_horizon_site"]

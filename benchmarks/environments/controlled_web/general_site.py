"""Deterministic local website used by the general web-interaction benchmark."""

from __future__ import annotations

import json
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any
from urllib.parse import parse_qs, urlparse


class _SiteState:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self.reset()

    def reset(self) -> None:
        with getattr(self, "_lock", threading.Lock()):
            self.profile: dict[str, str] = {"name": "", "email": "", "role": ""}
            self.cart: dict[str, int] = {"amber": 0}
            self.logged_in = False
            self.booking: dict[str, str] = {"date": "", "time": "", "guests": ""}
            self.order: dict[str, Any] = {"submitted": False, "address": "", "terms": False}
            self.recover_visits = 0

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            return {
                "profile": dict(self.profile),
                "cart": dict(self.cart),
                "logged_in": self.logged_in,
                "booking": dict(self.booking),
                "order": dict(self.order),
                "recover_visits": self.recover_visits,
            }


def _page(title: str, body: str, *, script: str = "") -> bytes:
    return f"""<!doctype html>
<html lang="en">
<head><meta charset="utf-8"><title>{title}</title></head>
<body>
<nav><a href="/">Home</a></nav>
<main>{body}</main>
{f"<script>{script}</script>" if script else ""}
</body>
</html>""".encode()


def _handler(state: _SiteState) -> type[BaseHTTPRequestHandler]:
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
            path = urlparse(self.path).path
            if path == "/api/state":
                payload = json.dumps(state.snapshot()).encode()
                self._send(payload, content_type="application/json")
                return
            if path == "/":
                self._send(
                    _page(
                        "WebAgent Test Shop",
                        """
                        <h1>WebAgent Test Shop</h1>
                        <a id="browse-products" href="/catalog">Browse products</a>
                        <a id="team-directory" href="/teams">Team directory</a>
                        <a id="edit-profile" href="/profile">Edit profile</a>
                        <a id="view-cart" href="/cart">View cart</a>
                        <a id="dynamic-demo" href="/dynamic">Dynamic content</a>
                        <a id="service-status" href="/recover">Service status</a>
                        <a id="account-login" href="/login">Account login</a>
                        <a id="inventory-table" href="/inventory">Inventory table</a>
                        <a id="location-map" href="/locations">Location map</a>
                        <a id="book-visit" href="/booking">Book a visit</a>
                        """,
                    )
                )
                return
            if path == "/catalog":
                self._send(
                    _page(
                        "Product catalog",
                        """
                        <h1>Product catalog</h1>
                        <a id="product-slate" href="/product/slate">Slate Pencil</a>
                        <a id="product-amber" href="/product/amber">Amber Notebook</a>
                        """,
                    )
                )
                return
            if path == "/product/amber":
                self._send(
                    _page(
                        "Amber Notebook",
                        """
                        <h1 id="product-name">Amber Notebook</h1>
                        <p id="sku">SKU: NOTE-AMBER-7</p>
                        <form method="post" action="/cart/add">
                          <button id="add-amber" type="submit">Add Amber Notebook to cart</button>
                        </form>
                        """,
                    )
                )
                return
            if path == "/product/slate":
                self._send(_page("Slate Pencil", '<h1 id="product-name">Slate Pencil</h1>'))
                return
            if path == "/teams":
                self._send(
                    _page(
                        "Team directory",
                        """
                        <h1>Team directory</h1>
                        <p>The reliability lead is listed in Research.</p>
                        <a id="research-team" href="/teams/research">Research</a>
                        <a id="operations-team" href="/teams/operations">Operations</a>
                        """,
                    )
                )
                return
            if path == "/teams/research":
                self._send(
                    _page(
                        "Research team",
                        """
                        <h1>Research team</h1>
                        <a id="mira-profile" href="/people/mira">Mira Chen — Reliability Lead</a>
                        <a href="/people/noah">Noah Park — Systems Engineer</a>
                        """,
                    )
                )
                return
            if path == "/teams/operations":
                self._send(_page("Operations", "<h1>Operations team</h1>"))
                return
            if path == "/people/mira":
                self._send(
                    _page(
                        "Mira Chen",
                        """
                        <h1>Mira Chen</h1>
                        <p id="role">Reliability Lead</p>
                        <p id="email">mira.chen@example.test</p>
                        """,
                    )
                )
                return
            if path == "/profile":
                saved = '<p id="saved">Profile saved</p>' if "saved=1" in self.path else ""
                self._send(
                    _page(
                        "Profile",
                        f"""
                        <h1>Edit profile</h1>{saved}
                        <form method="post" action="/profile">
                          <label>Name <input id="name" name="name"></label>
                          <label>Email <input id="email" name="email" type="email"></label>
                          <label>Role
                            <select id="role" name="role">
                              <option value="">Choose role</option>
                              <option value="researcher">Researcher</option>
                              <option value="engineer">Engineer</option>
                            </select>
                          </label>
                          <button id="save-profile" type="submit">Save profile</button>
                        </form>
                        """,
                    )
                )
                return
            if path == "/cart":
                count = state.snapshot()["cart"]["amber"]
                self._send(
                    _page(
                        "Cart",
                        f"""
                        <h1>Shopping cart</h1>
                        <p id="amber-count">Amber Notebook quantity: {count}</p>
                        <a id="checkout" href="/checkout">Checkout</a>
                        """,
                    )
                )
                return
            if path == "/login":
                message = (
                    '<p id="login-error">Invalid credentials</p>' if "error=1" in self.path else ""
                )
                self._send(
                    _page(
                        "Account login",
                        f"""
                        <h1>Account login</h1>{message}
                        <form method="post" action="/login">
                          <label>Username <input id="username" name="username"></label>
                          <label>Password <input id="password" name="password" type="password"></label>
                          <button id="login" type="submit">Sign in</button>
                        </form>
                        """,
                    )
                )
                return
            if path == "/dashboard":
                if not state.snapshot()["logged_in"]:
                    self._redirect("/login")
                    return
                self._send(
                    _page(
                        "Account dashboard",
                        '<h1 id="welcome">Welcome, benchmark-agent</h1><p>Plan: Research</p>',
                    )
                )
                return
            if path == "/inventory":
                self._send(
                    _page(
                        "Inventory table",
                        """
                        <h1>Inventory table</h1>
                        <table id="inventory"><thead><tr><th>Item</th><th>Category</th><th>Stock</th></tr></thead>
                        <tbody>
                          <tr><td><a id="inventory-ember" href="/inventory/ember">Ember Lamp</a></td><td>Office</td><td>12</td></tr>
                          <tr><td><a id="inventory-nova" href="/inventory/nova">Nova Stand</a></td><td>Office</td><td>37</td></tr>
                          <tr><td><a id="inventory-river" href="/inventory/river">River Mug</a></td><td>Kitchen</td><td>54</td></tr>
                        </tbody></table>
                        """,
                    )
                )
                return
            if path == "/inventory/nova":
                self._send(
                    _page(
                        "Nova Stand",
                        '<h1 id="inventory-name">Nova Stand</h1><p id="inventory-stock">Stock: 37</p>',
                    )
                )
                return
            if path == "/inventory/ember":
                self._send(_page("Ember Lamp", '<h1 id="inventory-name">Ember Lamp</h1>'))
                return
            if path == "/inventory/river":
                self._send(_page("River Mug", '<h1 id="inventory-name">River Mug</h1>'))
                return
            if path == "/locations":
                self._send(
                    _page(
                        "Location map",
                        """
                        <h1>Clinic map</h1><p>Distances from Central Station:</p>
                        <ul id="map-results">
                          <li><a id="clinic-north" href="/locations/north">North Clinic</a> — 3.8 km</li>
                          <li><a id="clinic-harbor" href="/locations/harbor">Harbor Clinic</a> — 1.2 km</li>
                          <li><a id="clinic-hill" href="/locations/hill">Hill Clinic</a> — 2.6 km</li>
                        </ul>
                        """,
                    )
                )
                return
            if path == "/locations/harbor":
                self._send(
                    _page(
                        "Harbor Clinic",
                        '<h1 id="location-name">Harbor Clinic</h1><p id="location-hours">Open until 20:00</p>',
                    )
                )
                return
            if path.startswith("/locations/"):
                self._send(_page("Clinic", "<h1>Clinic details</h1>"))
                return
            if path == "/booking":
                confirmed = (
                    '<p id="booking-confirmed">Booking confirmed</p>'
                    if "saved=1" in self.path
                    else ""
                )
                self._send(
                    _page(
                        "Book a visit",
                        f"""
                        <h1>Book a visit</h1>{confirmed}
                        <form method="post" action="/booking">
                          <label>Date <input id="booking-date" name="date"></label>
                          <label>Time <select id="booking-time" name="time">
                            <option value="09:00">09:00</option><option value="14:30">14:30</option>
                          </select></label>
                          <label>Guests <input id="booking-guests" name="guests"></label>
                          <button id="confirm-booking" type="submit">Confirm booking</button>
                        </form>
                        """,
                    )
                )
                return
            if path == "/checkout":
                placed = (
                    '<p id="order-confirmed">Order placed</p>' if "placed=1" in self.path else ""
                )
                self._send(
                    _page(
                        "Checkout",
                        f"""
                        <h1>Checkout</h1>{placed}
                        <form method="post" action="/checkout">
                          <label>Address <input id="address" name="address"></label>
                          <label><input id="terms" name="terms" type="checkbox" value="yes"> Accept terms</label>
                          <button id="place-order" type="submit">Place order</button>
                        </form>
                        """,
                    )
                )
                return
            if path == "/dynamic":
                self._send(
                    _page(
                        "Dynamic content",
                        '<h1>Dynamic content</h1><div id="dynamic-root">Loading controls…</div>',
                        script="""
                        setTimeout(() => {
                          const root = document.getElementById('dynamic-root');
                          root.innerHTML = '<button id="reveal">Reveal access code</button>';
                          document.getElementById('reveal').addEventListener('click', () => {
                            root.innerHTML = '<p id="access-code">Access code: ORBIT-42</p>';
                          });
                        }, 250);
                        """,
                    )
                )
                return
            if path == "/recover":
                with state._lock:
                    state.recover_visits += 1
                    visit = state.recover_visits
                if visit == 1:
                    self._send(
                        _page(
                            "Temporary error",
                            """
                            <h1>Service temporarily unavailable</h1>
                            <p>Please retry the request.</p>
                            <a id="retry" href="/recover">Retry</a>
                            """,
                        ),
                        status=HTTPStatus.SERVICE_UNAVAILABLE,
                    )
                else:
                    self._send(
                        _page(
                            "Service recovered",
                            '<h1 id="recovered">Service recovered</h1><p>Status: healthy</p>',
                        )
                    )
                return
            self._send(_page("Not found", "<h1>Not found</h1>"), status=HTTPStatus.NOT_FOUND)

        def do_POST(self) -> None:
            path = urlparse(self.path).path
            length = int(self.headers.get("Content-Length", "0"))
            form = parse_qs(self.rfile.read(length).decode())
            if path == "/api/reset":
                state.reset()
                self._send(b'{"ok": true}', content_type="application/json")
                return
            if path == "/profile":
                with state._lock:
                    state.profile = {
                        "name": form.get("name", [""])[0],
                        "email": form.get("email", [""])[0],
                        "role": form.get("role", [""])[0],
                    }
                self._redirect("/profile?saved=1")
                return
            if path == "/login":
                if form.get("username") == ["benchmark-agent"] and form.get("password") == [
                    "orbit42"
                ]:
                    with state._lock:
                        state.logged_in = True
                    self._redirect("/dashboard")
                else:
                    self._redirect("/login?error=1")
                return
            if path == "/booking":
                with state._lock:
                    state.booking = {
                        "date": form.get("date", [""])[0],
                        "time": form.get("time", [""])[0],
                        "guests": form.get("guests", [""])[0],
                    }
                self._redirect("/booking?saved=1")
                return
            if path == "/checkout":
                with state._lock:
                    state.order = {
                        "submitted": True,
                        "address": form.get("address", [""])[0],
                        "terms": form.get("terms") == ["yes"],
                    }
                self._redirect("/checkout?placed=1")
                return
            if path == "/cart/add":
                with state._lock:
                    state.cart["amber"] += 1
                self._redirect("/cart")
                return
            self._send(_page("Not found", "<h1>Not found</h1>"), status=HTTPStatus.NOT_FOUND)

    return Handler


@contextmanager
def benchmark_site() -> Iterator[str]:
    """Run the local site on a random loopback port and yield its base URL."""
    state = _SiteState()
    server = ThreadingHTTPServer(("127.0.0.1", 0), _handler(state))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    port = server.server_address[1]
    try:
        yield f"http://127.0.0.1:{port}"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)

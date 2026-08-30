"""Task manifest for the deterministic web-interaction benchmark."""

from __future__ import annotations

from webagent.evaluation.models import BenchmarkAssertion, BenchmarkTask


def build_tasks(base_url: str) -> list[BenchmarkTask]:
    """Return tasks spanning navigation, forms, mutation, dynamics, and recovery."""
    return [
        BenchmarkTask(
            id="navigate_product",
            category="navigation",
            goal=(
                "From the home page, find the Amber Notebook in the product catalog and open "
                "its product page. Finish only after confirming its SKU."
            ),
            start_url=f"{base_url}/",
            scenario="general_interaction",
            environment="sandbox",
            assertions=[
                BenchmarkAssertion(kind="url_contains", expected="/product/amber"),
                BenchmarkAssertion(
                    kind="element_text_equals", selector="#sku", expected="SKU: NOTE-AMBER-7"
                ),
            ],
            tags=["multi-page", "link-grounding"],
        ),
        BenchmarkTask(
            id="cross_page_lookup",
            category="navigation",
            goal=(
                "Use the team directory to find the Reliability Lead and open that person's "
                "profile. Finish on the profile containing their email address."
            ),
            start_url=f"{base_url}/teams",
            scenario="general_interaction",
            environment="sandbox",
            assertions=[
                BenchmarkAssertion(kind="url_contains", expected="/people/mira"),
                BenchmarkAssertion(
                    kind="element_text_equals",
                    selector="#email",
                    expected="mira.chen@example.test",
                ),
            ],
            tags=["multi-page", "information-seeking"],
        ),
        BenchmarkTask(
            id="submit_profile",
            category="form",
            goal=(
                "Set the profile name to Ada Lovelace, email to ada@example.test, and role to "
                "Researcher, then save it."
            ),
            start_url=f"{base_url}/profile",
            scenario="general_interaction",
            environment="sandbox",
            risk_scope="sandbox_mutation",
            assertions=[
                BenchmarkAssertion(kind="element_visible", selector="#saved", expected=True),
                BenchmarkAssertion(
                    kind="json_equals",
                    endpoint="/api/state",
                    json_path="profile.name",
                    expected="Ada Lovelace",
                ),
                BenchmarkAssertion(
                    kind="json_equals",
                    endpoint="/api/state",
                    json_path="profile.email",
                    expected="ada@example.test",
                ),
                BenchmarkAssertion(
                    kind="json_equals",
                    endpoint="/api/state",
                    json_path="profile.role",
                    expected="researcher",
                ),
            ],
            tags=["form", "dropdown", "server-state"],
        ),
        BenchmarkTask(
            id="mutate_cart",
            category="state_mutation",
            goal="Open the Amber Notebook product and add exactly one unit to the shopping cart.",
            start_url=f"{base_url}/catalog",
            scenario="general_interaction",
            environment="sandbox",
            risk_scope="sandbox_mutation",
            assertions=[
                BenchmarkAssertion(kind="url_contains", expected="/cart"),
                BenchmarkAssertion(
                    kind="json_equals",
                    endpoint="/api/state",
                    json_path="cart.amber",
                    expected=1,
                ),
                BenchmarkAssertion(
                    kind="element_text_equals",
                    selector="#amber-count",
                    expected="Amber Notebook quantity: 1",
                ),
            ],
            tags=["state-change", "server-state"],
        ),
        BenchmarkTask(
            id="dynamic_reveal",
            category="dynamic",
            goal="Wait for the dynamically loaded control, use it, and reveal the access code.",
            start_url=f"{base_url}/dynamic",
            scenario="general_interaction",
            environment="sandbox",
            assertions=[
                BenchmarkAssertion(
                    kind="element_text_equals",
                    selector="#access-code",
                    expected="Access code: ORBIT-42",
                )
            ],
            tags=["javascript", "wait", "dynamic-dom"],
        ),
        BenchmarkTask(
            id="recover_transient",
            category="recovery",
            goal=(
                "The service may initially return a temporary error. Recover using the page's "
                "available controls and finish only when its status is healthy."
            ),
            start_url=f"{base_url}/recover",
            scenario="recovery",
            environment="sandbox",
            assertions=[
                BenchmarkAssertion(kind="element_visible", selector="#recovered", expected=True),
                BenchmarkAssertion(kind="text_contains", expected="Status: healthy"),
                BenchmarkAssertion(
                    kind="json_equals",
                    endpoint="/api/state",
                    json_path="recover_visits",
                    expected=2,
                ),
            ],
            tags=["retry", "http-503", "recovery"],
        ),
        BenchmarkTask(
            id="login_account",
            category="account",
            goal=(
                "Sign in with username benchmark-agent and password orbit42. Finish only after "
                "the account dashboard welcomes benchmark-agent."
            ),
            start_url=f"{base_url}/login",
            scenario="authenticated_session",
            environment="sandbox",
            entry_mode="authenticated",
            risk_scope="sandbox_mutation",
            assertions=[
                BenchmarkAssertion(kind="url_contains", expected="/dashboard"),
                BenchmarkAssertion(
                    kind="element_text_equals",
                    selector="#welcome",
                    expected="Welcome, benchmark-agent",
                ),
                BenchmarkAssertion(
                    kind="json_equals",
                    endpoint="/api/state",
                    json_path="logged_in",
                    expected=True,
                ),
            ],
            tags=["authentication", "password", "session-state"],
        ),
        BenchmarkTask(
            id="table_lookup",
            category="table_reasoning",
            goal=(
                "From the inventory table, identify the Office-category item with the highest "
                "stock, open it, and report its name and stock count."
            ),
            start_url=f"{base_url}/inventory",
            scenario="general_interaction",
            environment="sandbox",
            assertions=[
                BenchmarkAssertion(kind="url_contains", expected="/inventory/nova"),
                BenchmarkAssertion(
                    kind="element_text_equals", selector="#inventory-stock", expected="Stock: 37"
                ),
                BenchmarkAssertion(kind="answer_contains", expected="Nova Stand"),
                BenchmarkAssertion(kind="answer_regex", expected=r"\b37\b"),
                BenchmarkAssertion(kind="history_url_observed", expected=f"{base_url}/inventory"),
            ],
            tags=["table", "comparison", "answer-grounding"],
        ),
        BenchmarkTask(
            id="map_lookup",
            category="map_reasoning",
            goal=(
                "Use the clinic map results to open the clinic closest to Central Station, then "
                "report its name, distance, and closing time."
            ),
            start_url=f"{base_url}/locations",
            scenario="general_interaction",
            environment="sandbox",
            assertions=[
                BenchmarkAssertion(kind="url_contains", expected="/locations/harbor"),
                BenchmarkAssertion(
                    kind="element_text_equals",
                    selector="#location-hours",
                    expected="Open until 20:00",
                ),
                BenchmarkAssertion(kind="answer_contains", expected="Harbor Clinic"),
                BenchmarkAssertion(kind="answer_contains", expected="1.2 km"),
                BenchmarkAssertion(kind="answer_contains", expected="20:00"),
            ],
            tags=["map-like", "distance-comparison", "answer-grounding"],
        ),
        BenchmarkTask(
            id="create_booking",
            category="booking",
            goal=("Book a visit for 2026-09-15 at 14:30 for 3 guests and confirm the booking."),
            start_url=f"{base_url}/booking",
            scenario="general_interaction",
            environment="sandbox",
            risk_scope="sandbox_mutation",
            assertions=[
                BenchmarkAssertion(
                    kind="element_visible", selector="#booking-confirmed", expected=True
                ),
                BenchmarkAssertion(
                    kind="json_equals",
                    endpoint="/api/state",
                    json_path="booking.date",
                    expected="2026-09-15",
                ),
                BenchmarkAssertion(
                    kind="json_equals",
                    endpoint="/api/state",
                    json_path="booking.time",
                    expected="14:30",
                ),
                BenchmarkAssertion(
                    kind="json_equals",
                    endpoint="/api/state",
                    json_path="booking.guests",
                    expected="3",
                ),
            ],
            tags=["booking", "date", "dropdown", "server-state"],
        ),
        BenchmarkTask(
            id="checkout_purchase",
            category="checkout",
            goal=(
                "Add one Amber Notebook, proceed to checkout, enter 42 Orbit Road, accept the "
                "terms, and place the order."
            ),
            start_url=f"{base_url}/catalog",
            scenario="sandbox_transaction",
            environment="sandbox",
            risk_scope="sandbox_mutation",
            assertions=[
                BenchmarkAssertion(
                    kind="element_visible", selector="#order-confirmed", expected=True
                ),
                BenchmarkAssertion(
                    kind="json_equals",
                    endpoint="/api/state",
                    json_path="cart.amber",
                    expected=1,
                ),
                BenchmarkAssertion(
                    kind="json_equals",
                    endpoint="/api/state",
                    json_path="order.address",
                    expected="42 Orbit Road",
                ),
                BenchmarkAssertion(
                    kind="json_equals",
                    endpoint="/api/state",
                    json_path="order.terms",
                    expected=True,
                ),
                BenchmarkAssertion(
                    kind="json_equals",
                    endpoint="/api/state",
                    json_path="order.submitted",
                    expected=True,
                ),
            ],
            tags=["shopping", "checkout", "checkbox", "server-state"],
        ),
    ]

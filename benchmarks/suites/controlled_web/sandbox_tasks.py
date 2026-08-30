"""Typed task suite for the deterministic multi-origin browser sandbox."""

from __future__ import annotations

import hashlib

from benchmarks.environments.controlled_web.sandbox_site import SandboxOrigins
from webagent.evaluation import BenchmarkAssertion, BenchmarkTask

_PAYLOAD_SHA256 = hashlib.sha256(b"case_id,status\nORBIT-731,ready\n").hexdigest()


def build_sandbox_tasks(origins: SandboxOrigins) -> list[BenchmarkTask]:
    """Cover SPA, authenticated, cross-origin, file, and transaction workflows."""
    primary = origins.primary
    secondary = origins.secondary
    return [
        BenchmarkTask(
            id="spa_hydration_route",
            category="spa",
            goal=(
                "Wait for the queue SPA to hydrate, filter it to active queues, then open "
                "Orbit Queue and finish on its client-side detail route."
            ),
            start_url=f"{primary}/spa",
            scenario="spa_interaction",
            environment="sandbox",
            entry_mode="direct",
            risk_scope="read_only",
            assertions=[
                BenchmarkAssertion(kind="url_contains", expected="/spa/items/orbit"),
                BenchmarkAssertion(
                    kind="element_text_equals", selector="#queue-name", expected="Orbit Queue"
                ),
                BenchmarkAssertion(
                    kind="element_text_equals", selector="#queue-status", expected="Status: active"
                ),
                BenchmarkAssertion(
                    kind="history_tool_sequence",
                    expected=["wait_for_element", "click", "wait_for_element", "click"],
                ),
            ],
            tags=["spa", "fetch-hydration", "client-routing", "rerender"],
        ),
        BenchmarkTask(
            id="authenticated_account",
            category="authentication",
            goal=(
                "Sign in to the sandbox account with username benchmark-agent and password "
                "orbit42, then finish on the protected account page."
            ),
            start_url=f"{primary}/login",
            scenario="authenticated_session",
            environment="sandbox",
            entry_mode="authenticated",
            risk_scope="sandbox_mutation",
            assertions=[
                BenchmarkAssertion(kind="url_contains", expected="/account"),
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
                BenchmarkAssertion(kind="history_tool_succeeded", expected="type"),
            ],
            tags=["login", "cookie", "protected-route"],
        ),
        BenchmarkTask(
            id="cross_origin_intake",
            category="cross_origin_form",
            goal=(
                "Read the case identifier on the handoff page, continue to the partner origin, "
                "assign it to Ada with urgent priority, and submit the sandbox intake."
            ),
            start_url=f"{primary}/handoff",
            scenario="cross_origin_form",
            environment="sandbox",
            entry_mode="direct",
            risk_scope="sandbox_mutation",
            assertions=[
                BenchmarkAssertion(kind="url_contains", expected="/intake/complete"),
                BenchmarkAssertion(
                    kind="element_visible", selector="#intake-complete", expected=True
                ),
                BenchmarkAssertion(
                    kind="json_equals",
                    endpoint="/api/state",
                    json_path="cross_origin.case_id",
                    expected="ORBIT-731",
                ),
                BenchmarkAssertion(
                    kind="json_equals",
                    endpoint="/api/state",
                    json_path="cross_origin.owner",
                    expected="Ada",
                ),
                BenchmarkAssertion(
                    kind="json_equals",
                    endpoint="/api/state",
                    json_path="cross_origin.priority",
                    expected="urgent",
                ),
                BenchmarkAssertion(kind="history_origin_observed", expected=primary),
                BenchmarkAssertion(kind="history_origin_observed", expected=secondary),
            ],
            tags=["multi-origin", "multi-step-form", "server-state"],
        ),
        BenchmarkTask(
            id="download_upload_handoff",
            category="file_workflow",
            goal=(
                "Download the case file, continue to the partner upload portal, upload that exact "
                "downloaded file, wait until it is ready, and submit it."
            ),
            start_url=f"{primary}/files",
            scenario="file_workflow",
            environment="sandbox",
            entry_mode="direct",
            risk_scope="sandbox_mutation",
            assertions=[
                BenchmarkAssertion(kind="url_contains", expected="/upload/complete"),
                BenchmarkAssertion(
                    kind="json_equals",
                    endpoint="/api/state",
                    json_path="upload.name",
                    expected="sandbox-payload.txt",
                ),
                BenchmarkAssertion(
                    kind="json_equals",
                    endpoint="/api/state",
                    json_path="upload.sha256",
                    expected=_PAYLOAD_SHA256,
                ),
                BenchmarkAssertion(
                    kind="artifact_sha256",
                    expected={
                        "path": "downloads/sandbox-payload.txt",
                        "sha256": _PAYLOAD_SHA256,
                    },
                ),
                BenchmarkAssertion(
                    kind="history_tool_sequence",
                    expected=[
                        "download_file",
                        "click",
                        "upload_file",
                        "wait_for_element",
                        "click",
                    ],
                ),
                BenchmarkAssertion(kind="history_origin_observed", expected=primary),
                BenchmarkAssertion(kind="history_origin_observed", expected=secondary),
            ],
            tags=["download", "upload", "content-hash", "multi-origin"],
        ),
        BenchmarkTask(
            id="sandbox_checkout",
            category="sandbox_transaction",
            goal=(
                "Add exactly one Orbit Notebook in the sandbox shop, continue to sandbox "
                "checkout, enter 42 Orbit Road, accept the sandbox terms, and place the order."
            ),
            start_url=f"{primary}/shop",
            scenario="sandbox_transaction",
            environment="sandbox",
            entry_mode="direct",
            risk_scope="sandbox_mutation",
            assertions=[
                BenchmarkAssertion(kind="url_contains", expected="/order/complete"),
                BenchmarkAssertion(
                    kind="element_visible", selector="#order-complete", expected=True
                ),
                BenchmarkAssertion(
                    kind="json_equals",
                    endpoint="/api/state",
                    json_path="cart.orbit",
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
                BenchmarkAssertion(kind="history_origin_observed", expected=primary),
                BenchmarkAssertion(kind="history_origin_observed", expected=secondary),
            ],
            tags=["sandbox-only", "checkout", "cross-origin", "server-state"],
        ),
    ]


__all__ = ["build_sandbox_tasks"]

from types import SimpleNamespace

import pytest
from benchmarks.suites.browsergym.adapter import (
    BACKEND_VARIABLES,
    backend_configuration_sha256,
    require_evaluator_device,
)


def test_backend_configuration_fingerprint_fails_closed_and_hides_values(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    benchmark = "webarena_verified"
    for name in (*BACKEND_VARIABLES[benchmark], "WA_FULL_RESET"):
        monkeypatch.delenv(name, raising=False)

    with pytest.raises(RuntimeError, match="WA_SHOPPING"):
        backend_configuration_sha256(benchmark)

    for index, name in enumerate(BACKEND_VARIABLES[benchmark]):
        monkeypatch.setenv(name, f"https://private-{index}.example.test")
    first = backend_configuration_sha256(benchmark)
    monkeypatch.setenv("WA_SHOPPING", "https://different.example.test")
    second = backend_configuration_sha256(benchmark)

    assert len(first) == 64
    assert first != second
    assert "private" not in first


def test_evaluator_device_skips_torch_until_visual_cuda_is_requested(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fail_import(_name: str) -> None:
        raise AssertionError("torch should not be imported")

    monkeypatch.setattr("benchmarks.suites.browsergym.adapter.importlib.import_module", fail_import)

    require_evaluator_device("webarena_verified", "not_applicable")
    require_evaluator_device("visualwebarena", "cpu")


def test_evaluator_device_reports_missing_or_unavailable_cuda(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def missing_torch(_name: str) -> None:
        raise ModuleNotFoundError("No module named 'torch'", name="torch")

    monkeypatch.setattr(
        "benchmarks.suites.browsergym.adapter.importlib.import_module", missing_torch
    )
    with pytest.raises(RuntimeError, match="requires torch"):
        require_evaluator_device("visualwebarena", "cuda")

    torch_without_cuda = SimpleNamespace(cuda=SimpleNamespace(is_available=lambda: False))
    monkeypatch.setattr(
        "benchmarks.suites.browsergym.adapter.importlib.import_module",
        lambda _name: torch_without_cuda,
    )
    with pytest.raises(RuntimeError, match=r"torch\.cuda\.is_available\(\) is false"):
        require_evaluator_device("visualwebarena", "cuda")


def test_evaluator_device_accepts_available_cuda(monkeypatch: pytest.MonkeyPatch) -> None:
    torch_with_cuda = SimpleNamespace(cuda=SimpleNamespace(is_available=lambda: True))
    monkeypatch.setattr(
        "benchmarks.suites.browsergym.adapter.importlib.import_module",
        lambda _name: torch_with_cuda,
    )

    require_evaluator_device("visualwebarena", "cuda")

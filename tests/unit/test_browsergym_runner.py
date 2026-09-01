import pytest
from benchmarks.suites.browsergym.adapter import (
    BACKEND_VARIABLES,
    backend_configuration_sha256,
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

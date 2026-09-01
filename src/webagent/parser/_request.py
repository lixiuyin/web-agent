"""Per-parse request bundle passed to each provider."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Protocol

import httpx

from ._profile import DocumentProfile
from .models import PDFParseResult

if TYPE_CHECKING:
    from webagent.core.config import AgentConfig


@dataclass(frozen=True)
class ParseRequest:
    """Everything a provider needs to parse one document."""

    file_path: Path
    profile: DocumentProfile
    output_dir: Path
    images_dir: Path
    config: AgentConfig


class Provider(Protocol):
    """Structural contract for a parser provider."""

    name: str

    async def parse(self, client: httpx.AsyncClient, req: ParseRequest) -> PDFParseResult: ...

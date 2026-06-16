"""Cloud and local parser providers."""

from .local import LocalPyMuPDFParser
from .marker import MarkerAPIParser
from .mineru import MinerUAPIParser
from .paddle import PaddleOCRAPIParser

__all__ = [
    "LocalPyMuPDFParser",
    "MarkerAPIParser",
    "MinerUAPIParser",
    "PaddleOCRAPIParser",
]

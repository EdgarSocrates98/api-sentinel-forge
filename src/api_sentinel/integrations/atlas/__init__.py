"""Code Atlas adapter — structural context through the versioned export."""

from api_sentinel.integrations.atlas.file_adapter import FileStructuralContext
from api_sentinel.integrations.atlas.models import StructuralEdge, StructuralRef
from api_sentinel.integrations.atlas.port import StructuralContextPort

__all__ = [
    "FileStructuralContext",
    "StructuralContextPort",
    "StructuralEdge",
    "StructuralRef",
]

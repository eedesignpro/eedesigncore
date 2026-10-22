"""Common models shared across KiCad file formats."""

from eedesigncore.common.bbox import BoundingBox
from eedesigncore.common.context import SaveContext
from eedesigncore.common.escape import unescape_string
from eedesigncore.common.layers import item_layers, items_on_layers
from eedesigncore.common.models import (
    Color,
    EmbeddedFile,
    Fill,
    FillType,
    JustifyH,
    JustifyV,
    Property,
    PtsArc,
    PtsElement,
    RenderCache,
    RenderCachePolygon,
    Stroke,
    StrokeType,
    TextAttributes,
)
from eedesigncore.common.page import Paper, TitleBlock
from eedesigncore.common.uuid_ops import (
    DERIVED_UUID_NAMESPACE,
    derive_uuids,
    generate_uuids,
    strip_uuids,
)

__all__ = [
    "BoundingBox",
    "item_layers",
    "items_on_layers",
    "Stroke",
    "StrokeType",
    "Fill",
    "FillType",
    "TextAttributes",
    "JustifyH",
    "JustifyV",
    "Color",
    "Property",
    "PtsArc",
    "PtsElement",
    "RenderCache",
    "RenderCachePolygon",
    "EmbeddedFile",
    "Paper",
    "TitleBlock",
    "SaveContext",
    "strip_uuids",
    "generate_uuids",
    "derive_uuids",
    "DERIVED_UUID_NAMESPACE",
    "unescape_string",
]

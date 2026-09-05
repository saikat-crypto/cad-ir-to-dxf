"""
sanitizer.py — Robust Geometry Validation, Name Sanitization & Defect Guards.

Defends the DXF compiler against malformed input that would silently corrupt
or crash the output file:
  - Non-finite (NaN / Inf) coordinates & extreme coordinates
  - 1D, empty, non-numeric, or malformed coordinate lists
  - 3D vertical lines (dx=0, dy=0, dz!=0)
  - Zero / negative radius arcs and circles
  - Under-specified or degenerate polylines
  - Prohibited characters in AutoCAD symbol names (layers, blocks, layouts)
  - Sub-epsilon and negative zero scale reflection inversion
"""

import math
import re
from typing import Any, List, Optional, Tuple

_EPSILON = 1e-6          # Minimum meaningful geometric size
_MAX_COORD = 1e12        # Maximum realistic CAD coordinate to prevent overflow
_INVALID_NAME_CHARS = re.compile(r'[<>/\":;?|=,\'\x00-\x1f]')


def is_numeric_and_finite(*values: Any) -> bool:
    """Return True if every value is a real number (not None, not bool, not str) and is finite."""
    for v in values:
        if v is None or isinstance(v, bool) or not isinstance(v, (int, float)):
            return False
        if not math.isfinite(float(v)):
            return False
        if abs(float(v)) > _MAX_COORD:
            return False
    return True


# Backward-compatibility alias
is_finite = is_numeric_and_finite


def sanitize_symbol_name(name: Any, fallback: str = "0") -> str:
    """
    Sanitize layer, block, and layout names to comply with AutoCAD requirements.
    Replaces prohibited characters [<>/\":;?*|=,'] with underscores.
    """
    if name is None:
        return fallback
    clean = _INVALID_NAME_CHARS.sub("_", str(name)).strip()
    return clean if clean else fallback


def validate_line(start: Any, end: Any) -> bool:
    """
    A line is valid if:
    - Both endpoints are list/tuple with at least 2 numeric, finite coordinates.
    - 3D Euclidean distance between start and end is greater than epsilon
      (preserves vertical 3D lines where dx=0, dy=0, dz!=0).
    """
    if not (isinstance(start, (list, tuple)) and isinstance(end, (list, tuple))):
        return False
    if len(start) < 2 or len(end) < 2:
        return False
    if not (is_numeric_and_finite(*start[:2]) and is_numeric_and_finite(*end[:2])):
        return False

    z1 = float(start[2]) if len(start) > 2 and is_numeric_and_finite(start[2]) else 0.0
    z2 = float(end[2]) if len(end) > 2 and is_numeric_and_finite(end[2]) else 0.0

    p1 = (float(start[0]), float(start[1]), z1)
    p2 = (float(end[0]), float(end[1]), z2)

    return math.dist(p1, p2) > _EPSILON


def validate_arc(center: Any, radius: Any,
                 start_angle: Any, end_angle: Any) -> bool:
    """
    An arc is valid if:
    - Center is a list/tuple of at least 2 finite coordinates.
    - Radius is a positive finite number greater than epsilon.
    - Start and end angles are finite numbers.
    """
    if not (isinstance(center, (list, tuple)) and len(center) >= 2):
        return False
    if not is_numeric_and_finite(center[0], center[1], radius, start_angle, end_angle):
        return False
    return float(radius) > _EPSILON


def validate_circle(center: Any, radius: Any) -> bool:
    """
    A circle is valid if:
    - Center is a list/tuple of at least 2 finite coordinates.
    - Radius is a positive finite number greater than epsilon.
    """
    if not (isinstance(center, (list, tuple)) and len(center) >= 2):
        return False
    if not is_numeric_and_finite(center[0], center[1], radius):
        return False
    return float(radius) > _EPSILON


def validate_polyline(points: Any) -> bool:
    """
    A polyline is valid if:
    - Points is a sequence of at least 2 vertices.
    - Each vertex is a list/tuple of at least 2 finite numbers.
    - Not all vertices are coincident.
    """
    if not isinstance(points, (list, tuple)) or len(points) < 2:
        return False
    first_pt = None
    has_non_coincident = False
    for pt in points:
        if not (isinstance(pt, (list, tuple)) and len(pt) >= 2):
            return False
        if not is_numeric_and_finite(pt[0], pt[1]):
            return False
        curr_xy = (float(pt[0]), float(pt[1]))
        if first_pt is None:
            first_pt = curr_xy
        elif not has_non_coincident:
            if math.dist(first_pt, curr_xy) > _EPSILON:
                has_non_coincident = True
    return has_non_coincident


def clamp_scale(scale_raw: Any, minimum: float = _EPSILON) -> Tuple[float, float, float]:
    """
    Ensure insertion scale factors are valid, non-zero, finite numbers.
    Preserves sign (negative scale = mirror reflection) using math.copysign.
    """
    if not isinstance(scale_raw, (list, tuple)):
        return (1.0, 1.0, 1.0)

    def _clamp(v: Any) -> float:
        if not is_numeric_and_finite(v):
            return 1.0
        v_flt = float(v)
        if abs(v_flt) < minimum:
            # Preserve negative sign using copysign!
            return math.copysign(minimum, v_flt if v_flt != 0.0 else 1.0)
        return v_flt

    sx = _clamp(scale_raw[0]) if len(scale_raw) > 0 else 1.0
    sy = _clamp(scale_raw[1]) if len(scale_raw) > 1 else 1.0
    sz = _clamp(scale_raw[2]) if len(scale_raw) > 2 else 1.0
    return (sx, sy, sz)


def sanitize_color(color: Optional[str]) -> Optional[str]:
    """
    Validates a color string.
    Returns None (BYLAYER) if the string is malformed, empty, or None.
    """
    if color is None:
        return None
    if color == "BYBLOCK":
        return "BYBLOCK"
    if isinstance(color, str) and color.startswith("#") and len(color) == 7:
        try:
            int(color[1:], 16)
            return color.lower()
        except ValueError:
            return None
    return None


def hex_to_rgb(hex_color: str) -> Tuple[int, int, int]:
    """Convert a validated #RRGGBB string to (R, G, B) integer tuple."""
    h = hex_color.lstrip("#")
    return (int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16))


def hex_to_truecolor(hex_color: str) -> int:
    """Convert #RRGGBB to the DXF 24-bit TrueColor integer (R<<16 | G<<8 | B)."""
    r, g, b = hex_to_rgb(hex_color)
    return (r << 16) | (g << 8) | b

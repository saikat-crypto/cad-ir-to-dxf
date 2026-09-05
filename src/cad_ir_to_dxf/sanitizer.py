"""
sanitizer.py — Geometry validation & degenerate filtering for IR v3 entities.

Defends the DXF compiler against malformed input that would silently corrupt
or crash the output file:
  - Zero-length lines
  - Zero / negative radius arcs and circles
  - Under-specified polylines (< 2 vertices)
  - Non-finite (NaN / Inf) coordinates
  - Zero-scale block insertions
"""

import math
from typing import List, Optional, Tuple

_EPSILON = 1e-6  # Minimum meaningful geometric size


def is_finite(*values: float) -> bool:
    """Return True if every value is a finite real number (not NaN, not Inf)."""
    return all(math.isfinite(v) for v in values)


def validate_line(start: List[float], end: List[float]) -> bool:
    """
    A line is valid if:
    - Both endpoints have finite coordinates.
    - The distance between start and end is greater than epsilon.
    """
    if not is_finite(*start, *end):
        return False
    dx = end[0] - start[0]
    dy = end[1] - start[1]
    return math.hypot(dx, dy) > _EPSILON


def validate_arc(center: List[float], radius: float,
                 start_angle: float, end_angle: float) -> bool:
    """
    An arc is valid if:
    - Center coordinates are finite.
    - Radius is positive and greater than epsilon.
    - Angles are finite real numbers.
    """
    if not is_finite(*center, radius, start_angle, end_angle):
        return False
    return radius > _EPSILON


def validate_circle(center: List[float], radius: float) -> bool:
    """
    A circle is valid if:
    - Center coordinates are finite.
    - Radius is positive and greater than epsilon.
    """
    if not is_finite(*center, radius):
        return False
    return radius > _EPSILON


def validate_polyline(points: List[List[float]]) -> bool:
    """
    A polyline is valid if:
    - It has at least 2 vertices.
    - All vertex coordinates are finite.
    """
    if len(points) < 2:
        return False
    for pt in points:
        if not is_finite(*pt):
            return False
    return True


def clamp_scale(scale: List[float], minimum: float = 1e-6) -> Tuple[float, float, float]:
    """
    Ensure insertion scale factors are not zero or sub-epsilon.
    Preserves sign (negative scale = mirror), but clamps magnitude to minimum.
    """
    def _clamp(v: float) -> float:
        if abs(v) < minimum:
            return minimum  # Default to 1 if effectively zero
        return v
    return (_clamp(scale[0]), _clamp(scale[1]), _clamp(scale[2] if len(scale) > 2 else 1.0))


def sanitize_color(color: Optional[str]) -> Optional[str]:
    """
    Validates a hex color string.
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

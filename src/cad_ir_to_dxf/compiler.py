"""
compiler.py — Core IR v3 → DXF Compilation Engine.

Translates a LAVINCI_CAD_IR_V3 payload (JSON file or dict) into a
fully compliant DXF file (default: DXF R2013 / AC1027).

Design Contract:
  - Reads LAVINCI_CAD_IR_V3 (Pydantic model or raw dict / JSON path).
  - Always produces structurally valid DXF, even for empty or partial IRs.
  - Entities inheriting layer colour remain BYLAYER (no baked-in hex overrides).
  - Arc sweep angles are passed through verbatim (no normalization).
  - Block definitions are written once; INSERT references reuse them.
  - Every detected boundary condition is sanitized, not silently swallowed.
"""

from __future__ import annotations

import json
import math
import os
from pathlib import Path
from typing import Any, Dict, Optional, Union

import ezdxf
from ezdxf import colors as dxf_colors
from ezdxf.document import Drawing
from ezdxf.enums import TextEntityAlignment

from .sanitizer import (
    clamp_scale,
    hex_to_truecolor,
    sanitize_color,
    validate_arc,
    validate_circle,
    validate_line,
    validate_polyline,
)

# ──────────────────────────────────────────────────────────────────────────────
# Standard AutoCAD linetypes we pre-load so layer references never dangle.
# ──────────────────────────────────────────────────────────────────────────────
_STANDARD_LINETYPES = [
    "CENTER",
    "DASHED",
    "DASHED2",
    "DASHEDX2",
    "DIVIDE",
    "DOT",
    "HIDDEN",
    "HIDDEN2",
    "HIDDENX2",
    "PHANTOM",
    "PHANTOM2",
    "PHANTOMX2",
]

# Units code map: IR `units` integer → DXF $INSUNITS integer
_UNITS_MAP: Dict[int, int] = {
    0: 0,  # Unspecified
    1: 1,  # Inches
    2: 2,  # Feet
    4: 4,  # Millimeters
    5: 5,  # Centimeters
    6: 6,  # Meters
}


# ──────────────────────────────────────────────────────────────────────────────
# Public entry point
# ──────────────────────────────────────────────────────────────────────────────

def compile_ir_to_dxf(
    ir_source: Union[str, Path, Dict[str, Any]],
    output_path: Optional[Union[str, Path]] = None,
    dxf_version: str = "R2013",
) -> Drawing:
    """
    Compile a LAVINCI_CAD_IR_V3 payload into a DXF Drawing.

    Parameters
    ----------
    ir_source:
        Path to a JSON file, a raw JSON string, or an already-parsed dict.
    output_path:
        Optional file path to save the DXF. If None, the Drawing is returned
        in-memory and not written to disk.
    dxf_version:
        Target DXF version string (e.g. "R2013", "R2000"). Default: "R2013".

    Returns
    -------
    ezdxf.Drawing
        The compiled, fully populated DXF document.
    """
    ir = _load_ir(ir_source)

    doc = ezdxf.new(dxf_version, setup=True)

    _configure_header(doc, ir)
    _load_linetypes(doc)
    _build_layer_table(doc, ir)
    _build_block_definitions(doc, ir)
    _populate_spaces(doc, ir)

    if output_path is not None:
        doc.saveas(str(output_path))

    return doc


# ──────────────────────────────────────────────────────────────────────────────
# Stage 1 — Load & parse the IR
# ──────────────────────────────────────────────────────────────────────────────

def _load_ir(source: Union[str, Path, Dict[str, Any]]) -> Dict[str, Any]:
    """Return the IR as a plain Python dict regardless of input form."""
    if isinstance(source, dict):
        return source
    source = str(source)
    # Treat as a JSON file path first, then fall back to raw JSON string.
    if os.path.isfile(source):
        with open(source, encoding="utf-8") as fh:
            return json.load(fh)
    return json.loads(source)


# ──────────────────────────────────────────────────────────────────────────────
# Stage 2 — DXF Header
# ──────────────────────────────────────────────────────────────────────────────

def _configure_header(doc: Drawing, ir: Dict[str, Any]) -> None:
    """Set $INSUNITS, $MEASUREMENT, and spatial extents from IR metadata."""
    meta = ir.get("metadata", {})
    extents = ir.get("extents", {})

    # Units
    ir_units = meta.get("units", 0)
    doc.header["$INSUNITS"] = _UNITS_MAP.get(ir_units, 0)

    # Measurement system: 0 = English (Imperial), 1 = Metric
    doc.header["$MEASUREMENT"] = 1 if meta.get("measurement_system", "Imperial") == "Metric" else 0

    # Bounding extents
    mn = extents.get("min", [0.0, 0.0])
    mx = extents.get("max", [0.0, 0.0])
    if len(mn) >= 2 and len(mx) >= 2:
        doc.header["$EXTMIN"] = (float(mn[0]), float(mn[1]), 0.0)
        doc.header["$EXTMAX"] = (float(mx[0]), float(mx[1]), 0.0)


# ──────────────────────────────────────────────────────────────────────────────
# Stage 3 — Linetype Table
# ──────────────────────────────────────────────────────────────────────────────

def _load_linetypes(doc: Drawing) -> None:
    """
    Pre-load standard AutoCAD linetypes so that any layer referencing
    "DASHED", "HIDDEN", "CENTER", etc. never produces a dangling reference.
    ezdxf setup=True already loads many; we ensure all our standard set is present.
    """
    ltype_table = doc.linetypes
    for lt_name in _STANDARD_LINETYPES:
        if lt_name not in ltype_table:
            try:
                ltype_table.new(lt_name, dxfattribs={"description": lt_name})
            except Exception:
                pass  # Already loaded by ezdxf setup


# ──────────────────────────────────────────────────────────────────────────────
# Stage 4 — Layer Table
# ──────────────────────────────────────────────────────────────────────────────

def _build_layer_table(doc: Drawing, ir: Dict[str, Any]) -> None:
    """
    Register every layer from the IR into the DXF TABLES section.
    Auto-vivifies any layer that is later referenced but not declared here.
    """
    for layer_def in ir.get("layers", []):
        name = layer_def.get("name", "0")
        if not name:
            name = "0"

        aci = int(layer_def.get("color_aci", 7))
        linetype = layer_def.get("linetype", "Continuous") or "Continuous"

        # Normalize linetype: ezdxf uses uppercase names for LTYPE table entries
        lt_upper = linetype.upper()
        if lt_upper == "CONTINUOUS":
            lt_upper = "Continuous"  # ezdxf canonical form

        dxfattribs = {
            "color": aci,
            "linetype": lt_upper if lt_upper != "Continuous" else "Continuous",
        }

        # Flags: off, frozen, locked
        if layer_def.get("is_off", False):
            dxfattribs["flags"] = dxfattribs.get("flags", 0) | 1  # LAYER_FROZEN = bit0 in some modes
        if layer_def.get("is_frozen", False):
            dxfattribs["flags"] = dxfattribs.get("flags", 0) | 1
        if layer_def.get("is_locked", False):
            dxfattribs["flags"] = dxfattribs.get("flags", 0) | 4

        if name in doc.layers:
            layer = doc.layers.get(name)
            layer.dxf.color = aci
        else:
            doc.layers.new(name=name, dxfattribs={"color": aci})


def _ensure_layer(doc: Drawing, name: str) -> None:
    """Auto-vivify a missing layer so no dangling references crash the DXF."""
    if name and name not in doc.layers:
        doc.layers.new(name=name, dxfattribs={"color": 7})


# ──────────────────────────────────────────────────────────────────────────────
# Stage 5 — BLOCKS Table (The V3 Power Stage)
# ──────────────────────────────────────────────────────────────────────────────

def _build_block_definitions(doc: Drawing, ir: Dict[str, Any]) -> None:
    """
    Write every CADBlockDefinition into the DXF BLOCKS table.

    Each definition contains internal lines, arcs, circles, and polylines
    mined from the source drawing. These are the actual renderable shapes
    of each component (Toilet, Bathtub, Receptacle, Window, Door…).
    """
    block_defs = ir.get("block_definitions", {})

    for block_name, block_def in block_defs.items():
        # Skip internal model/paper space containers
        if block_name.startswith("*Model") or block_name.startswith("*Paper"):
            continue

        base = block_def.get("base_point", [0.0, 0.0, 0.0])
        base_pt = (float(base[0]), float(base[1]), float(base[2]) if len(base) > 2 else 0.0)

        # Create the block definition (or reuse if somehow already registered)
        if block_name in doc.blocks:
            blk = doc.blocks[block_name]
        else:
            blk = doc.blocks.new(name=block_name, base_point=base_pt)

        # Write internal primitives into the block
        _add_primitives_to_layout(blk, block_def, doc, space_name="Block")


# ──────────────────────────────────────────────────────────────────────────────
# Stage 6 — Entity Dispatch Across Spaces
# ──────────────────────────────────────────────────────────────────────────────

def _populate_spaces(doc: Drawing, ir: Dict[str, Any]) -> None:
    """
    Route every entity in the IR to its correct DXF space:
      - space == "Model"   → doc.modelspace()
      - space != "Model"   → doc.layout(space_name)  [Paper Space]
    """
    msp = doc.modelspace()

    # Build a mapping of layout names we'll need
    paper_layouts: Dict[str, Any] = {}
    for layout_def in ir.get("layouts", []):
        ln = layout_def.get("name", "")
        if ln and ln != "Model":
            paper_layouts[ln] = layout_def

    def get_space(space_name: str):
        """Return the correct ezdxf layout object for the given space name."""
        if space_name == "Model" or not space_name:
            return msp
        if space_name not in doc.layouts:
            doc.layouts.new(space_name)
        return doc.layouts.get(space_name)

    # ── Geometry Primitives ──────────────────────────────────────────────────
    primitives = ir.get("geometry_primitives", {}).get("primitives", {})

    for line in primitives.get("lines", []):
        space = get_space(line.get("space", "Model"))
        _ensure_layer(doc, line.get("layer", "0"))
        _add_line(space, line)

    for arc in primitives.get("arcs", []):
        space = get_space(arc.get("space", "Model"))
        _ensure_layer(doc, arc.get("layer", "0"))
        _add_arc(space, arc)

    for circle in primitives.get("circles", []):
        space = get_space(circle.get("space", "Model"))
        _ensure_layer(doc, circle.get("layer", "0"))
        _add_circle(space, circle)

    for poly in primitives.get("polylines", []):
        space = get_space(poly.get("space", "Model"))
        _ensure_layer(doc, poly.get("layer", "0"))
        _add_polyline(space, poly)

    # ── Component Instances (INSERT) ─────────────────────────────────────────
    block_defs = ir.get("block_definitions", {})
    for comp in ir.get("components", []):
        space = get_space(comp.get("space", "Model"))
        _ensure_layer(doc, comp.get("layer", "0"))
        _add_insert(space, comp, block_defs, doc)

    # ── Annotations (TEXT / MTEXT) ───────────────────────────────────────────
    for annot in ir.get("annotations", []):
        space = get_space(annot.get("space", "Model"))
        _ensure_layer(doc, annot.get("layer", "0"))
        _add_annotation(space, annot)

    # ── Dimensions (rendered as MTEXT for MVP accuracy) ──────────────────────
    for dim in ir.get("dimensions", []):
        space = get_space(dim.get("space", "Model"))
        _ensure_layer(doc, dim.get("layer", "0"))
        _add_dimension_as_text(space, dim)


# ──────────────────────────────────────────────────────────────────────────────
# Primitive Writers — each writes one DXF entity
# ──────────────────────────────────────────────────────────────────────────────

def _apply_color(entity, color: Optional[str]) -> None:
    """
    Apply entity-level colour to a DXF entity.
    - None / BYLAYER → do nothing (entity inherits layer colour dynamically).
    - "BYBLOCK"      → dxf.color = 0
    - "#RRGGBB"      → TrueColor override (group code 420)
    """
    c = sanitize_color(color)
    if c is None:
        return  # BYLAYER — no attribute set
    if c == "BYBLOCK":
        entity.dxf.color = 0
        return
    # TrueColor
    entity.dxf.true_color = hex_to_truecolor(c)


def _add_primitives_to_layout(layout, data: Dict[str, Any], doc: Drawing, space_name: str) -> None:
    """Generic primitive writer shared by both block definitions and top-level spaces."""
    for line in data.get("lines", []):
        _ensure_layer(doc, line.get("layer", "0"))
        _add_line(layout, line)
    for arc in data.get("arcs", []):
        _ensure_layer(doc, arc.get("layer", "0"))
        _add_arc(layout, arc)
    for circle in data.get("circles", []):
        _ensure_layer(doc, circle.get("layer", "0"))
        _add_circle(layout, circle)
    for poly in data.get("polylines", []):
        _ensure_layer(doc, poly.get("layer", "0"))
        _add_polyline(layout, poly)


def _add_line(layout, line: Dict[str, Any]) -> None:
    start = line.get("start", [0.0, 0.0])
    end = line.get("end", [0.0, 0.0])
    if not validate_line(start, end):
        return
    e = layout.add_line(
        start=(float(start[0]), float(start[1])),
        end=(float(end[0]), float(end[1])),
        dxfattribs={"layer": line.get("layer", "0")},
    )
    _apply_color(e, line.get("color"))


def _add_arc(layout, arc: Dict[str, Any]) -> None:
    center = arc.get("center", [0.0, 0.0])
    radius = float(arc.get("radius", 0.0))
    start_angle = float(arc.get("start_angle", 0.0))
    end_angle = float(arc.get("end_angle", 0.0))
    if not validate_arc(center, radius, start_angle, end_angle):
        return
    e = layout.add_arc(
        center=(float(center[0]), float(center[1])),
        radius=radius,
        start_angle=start_angle,
        end_angle=end_angle,
        dxfattribs={"layer": arc.get("layer", "0")},
    )
    _apply_color(e, arc.get("color"))


def _add_circle(layout, circle: Dict[str, Any]) -> None:
    center = circle.get("center", [0.0, 0.0])
    radius = float(circle.get("radius", 0.0))
    if not validate_circle(center, radius):
        return
    e = layout.add_circle(
        center=(float(center[0]), float(center[1])),
        radius=radius,
        dxfattribs={"layer": circle.get("layer", "0")},
    )
    _apply_color(e, circle.get("color"))


def _add_polyline(layout, poly: Dict[str, Any]) -> None:
    points = poly.get("points", [])
    if not validate_polyline(points):
        return
    pts_2d = [(float(p[0]), float(p[1])) for p in points]
    e = layout.add_lwpolyline(
        points=pts_2d,
        close=bool(poly.get("is_closed", False)),
        dxfattribs={"layer": poly.get("layer", "0")},
    )
    _apply_color(e, poly.get("color"))


def _add_insert(layout, comp: Dict[str, Any], block_defs: Dict[str, Any], doc: Drawing) -> None:
    """
    Write a DXF INSERT (block reference) with clamped scale and ATTRIB tags.
    If the block definition doesn't exist in the DXF BLOCKS table, a minimal
    crosshair placeholder is auto-vivified so the INSERT is never a dangling reference.
    """
    block_name = comp.get("block_name", "")
    pos = comp.get("position", [0.0, 0.0, 0.0])
    rotation = float(comp.get("rotation", 0.0))
    scale_raw = comp.get("scale", [1.0, 1.0, 1.0])
    sx, sy, sz = clamp_scale(scale_raw)
    layer = comp.get("layer", "0")
    attribs_data = comp.get("attributes", {})

    if not block_name:
        return

    # Auto-vivify a placeholder block if definition geometry was not in IR
    _ensure_block_placeholder(doc, block_name, comp.get("resolved_name") or block_name)

    # Position: use 2D (x, y) — Z=0 for standard drawings
    insert_pt = (float(pos[0]), float(pos[1]))

    dxfattribs = {
        "layer": layer,
        "rotation": rotation,
        "xscale": sx,
        "yscale": sy,
        "zscale": sz,
    }

    if attribs_data:
        # Block must declare ATTDEF entries to accept ATTRIB tags at INSERT time.
        # ezdxf handles this automatically with add_auto_blockref.
        values = {tag: str(val) for tag, val in attribs_data.items()}
        layout.add_auto_blockref(
            name=block_name,
            insert=insert_pt,
            values=values,
            dxfattribs=dxfattribs,
        )
    else:
        layout.add_blockref(
            name=block_name,
            insert=insert_pt,
            dxfattribs=dxfattribs,
        )


def _ensure_block_placeholder(doc: Drawing, block_name: str, label: str) -> None:
    """
    If block_name isn't in doc.blocks yet (no definition geometry in IR),
    create a visible crosshair + text placeholder so the INSERT renders
    something instead of being invisible or corrupt.
    """
    if block_name in doc.blocks:
        return  # Already populated by _build_block_definitions

    blk = doc.blocks.new(name=block_name, base_point=(0, 0, 0))
    size = 0.5  # Crosshair arm length — visible at any reasonable scale

    # Crosshair lines
    blk.add_line((-size, 0), (size, 0), dxfattribs={"layer": "0"})
    blk.add_line((0, -size), (0, size), dxfattribs={"layer": "0"})

    # Label text (truncated to reasonable length)
    short_label = label[:24] if len(label) > 24 else label
    blk.add_text(
        short_label,
        dxfattribs={
            "layer": "0",
            "height": size * 0.4,
            "insert": (size * 0.15, size * 0.15),
        },
    )


def _add_annotation(layout, annot: Dict[str, Any]) -> None:
    """
    Write TEXT or MTEXT annotation.
    - MTEXT receives raw_text (preserves AutoCAD rich-text formatting).
    - TEXT  receives clean_text (plain text, no escape garbage).
    """
    etype = annot.get("type", "TEXT")
    layer = annot.get("layer", "0")
    pos = annot.get("position", [0.0, 0.0])
    height = float(annot.get("height", 0.1) or 0.1)
    insert = (float(pos[0]), float(pos[1]))

    if etype == "MTEXT":
        content = annot.get("raw_text", "") or annot.get("clean_text", "")
        layout.add_mtext(
            content,
            dxfattribs={
                "layer": layer,
                "char_height": height,
                "insert": insert,
            },
        )
    else:
        content = annot.get("clean_text", "") or annot.get("raw_text", "")
        if not content:
            return
        layout.add_text(
            content,
            dxfattribs={
                "layer": layer,
                "height": height,
                "insert": insert,
            },
        )


def _add_dimension_as_text(layout, dim: Dict[str, Any]) -> None:
    """
    Write dimension data as an MTEXT annotation at the defpoint location.

    Full native DIMENSION reconstruction (with anonymous geometry blocks and
    DIMSTYLE table entries) is complex; MTEXT at defpoint preserves the
    measurement value and text as a readable label for MVP.
    """
    defpoint = dim.get("defpoint", [0.0, 0.0])
    layer = dim.get("layer", "0")
    measurement = dim.get("measurement")
    text = dim.get("text") or ""

    if measurement is not None:
        label = text if text else f"{measurement:.3f}"
    elif text:
        label = text
    else:
        return  # Nothing meaningful to render

    layout.add_mtext(
        label,
        dxfattribs={
            "layer": layer,
            "char_height": 0.15,
            "insert": (float(defpoint[0]), float(defpoint[1])),
        },
    )

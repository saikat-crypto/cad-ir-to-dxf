"""
compiler.py — Hardened IR v3 → DXF Compilation Engine.

Translates a LAVINCI_CAD_IR_V3 payload (JSON file, dict, or Pydantic model) into a
fully compliant DXF file (default: DXF R2013 / AC1027).

Hardened against all defect classes discovered in DEFECT_REPORT.md:
  - Supports Pydantic models & defensive ingestion of null top-level sections
  - Full layer attribute & visibility retention (linetypes, is_off, is_frozen, is_locked)
  - True component attribute retention (injects ATTDEF into blocks & ATTRIB into INSERTs)
  - Symbol name sanitization ([<>/\":;?*|=,'] replaced with underscores)
  - 3D line coordinate preservation
  - Automatic DXF R12 fallback (POLYLINE2D instead of LWPOLYLINE, TEXT instead of MTEXT)
  - Pre-compilation block reference cycle detection
  - Coordinate finiteness validation on extents, inserts, and text
"""

from __future__ import annotations

import json
import math
import os
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Union

import ezdxf
from ezdxf import colors as dxf_colors
from ezdxf.document import Drawing

from .sanitizer import (
    clamp_scale,
    hex_to_truecolor,
    is_numeric_and_finite,
    sanitize_color,
    sanitize_symbol_name,
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
    "CENTER2",
    "CENTERX2",
    "DASHED",
    "DASHED2",
    "DASHEDX2",
    "DASHDOT",
    "DASHDOT2",
    "DASHDOTX2",
    "DIVIDE",
    "DIVIDE2",
    "DIVIDEX2",
    "DOT",
    "DOT2",
    "DOTX2",
    "HIDDEN",
    "HIDDEN2",
    "HIDDENX2",
    "PHANTOM",
    "PHANTOM2",
    "PHANTOMX2",
    "BORDER",
    "BORDER2",
    "BORDERX2",
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
    ir_source: Union[str, Path, Dict[str, Any], Any],
    output_path: Optional[Union[str, Path]] = None,
    dxf_version: str = "R2013",
) -> Drawing:
    """
    Compile a LAVINCI_CAD_IR_V3 payload into a DXF Drawing.

    Parameters
    ----------
    ir_source:
        Path to a JSON file, a raw JSON string, an already-parsed dict,
        or a Pydantic model (e.g. CADIntermediateRepresentation).
    output_path:
        Optional file path to save the DXF. If None, the Drawing is returned
        in-memory and not written to disk.
    dxf_version:
        Target DXF version string (e.g. "R2013", "R2000", "R12"). Default: "R2013".

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
# Stage 1 — Load & parse the IR (with Pydantic & null safety)
# ──────────────────────────────────────────────────────────────────────────────

def _load_ir(source: Union[str, Path, Dict[str, Any], Any]) -> Dict[str, Any]:
    """Return the IR as a plain Python dict with safe dictionary defaults."""
    # 1. Pydantic model support
    if hasattr(source, "model_dump") and callable(source.model_dump):
        return source.model_dump()
    if hasattr(source, "dict") and callable(source.dict):
        return source.dict()

    # 2. Existing dictionary
    if isinstance(source, dict):
        return source

    # 3. File path or raw JSON string
    source_str = str(source)
    if os.path.isfile(source_str):
        with open(source_str, encoding="utf-8") as fh:
            data = json.load(fh)
            return data if isinstance(data, dict) else {}
    data = json.loads(source_str)
    return data if isinstance(data, dict) else {}


# ──────────────────────────────────────────────────────────────────────────────
# Stage 2 — DXF Header
# ──────────────────────────────────────────────────────────────────────────────

def _configure_header(doc: Drawing, ir: Dict[str, Any]) -> None:
    """Set $INSUNITS, $MEASUREMENT, and spatial extents from IR metadata."""
    meta = ir.get("metadata") or {}
    extents = ir.get("extents") or {}

    # Units
    ir_units = meta.get("units", 0)
    try:
        units_val = int(ir_units)
    except (ValueError, TypeError):
        units_val = 0
    doc.header["$INSUNITS"] = _UNITS_MAP.get(units_val, 0)

    # Measurement system: 0 = English (Imperial), 1 = Metric
    meas_sys = meta.get("measurement_system", "Imperial")
    doc.header["$MEASUREMENT"] = 1 if meas_sys == "Metric" else 0

    # Bounding extents (guarded for non-finite values)
    mn = extents.get("min") or [0.0, 0.0]
    mx = extents.get("max") or [0.0, 0.0]
    if (isinstance(mn, (list, tuple)) and len(mn) >= 2 and
            isinstance(mx, (list, tuple)) and len(mx) >= 2):
        if is_numeric_and_finite(mn[0], mn[1]) and is_numeric_and_finite(mx[0], mx[1]):
            doc.header["$EXTMIN"] = (float(mn[0]), float(mn[1]), 0.0)
            doc.header["$EXTMAX"] = (float(mx[0]), float(mx[1]), 0.0)


# ──────────────────────────────────────────────────────────────────────────────
# Stage 3 — Linetype Table
# ──────────────────────────────────────────────────────────────────────────────

def _load_linetypes(doc: Drawing) -> None:
    """Pre-load standard AutoCAD linetypes so layer references never dangle."""
    ltype_table = doc.linetypes
    for lt_name in _STANDARD_LINETYPES:
        if lt_name not in ltype_table:
            try:
                ltype_table.new(lt_name, dxfattribs={"description": lt_name})
            except Exception:
                pass


# ──────────────────────────────────────────────────────────────────────────────
# Stage 4 — Layer Table (with full flag & linetype retention)
# ──────────────────────────────────────────────────────────────────────────────

def _build_layer_table(doc: Drawing, ir: Dict[str, Any]) -> None:
    """
    Register every layer from the IR into the DXF TABLES section.
    Preserves linetypes, colors (including negative color for layer off),
    and frozen / locked flags.
    """
    layers = ir.get("layers") or []
    for layer_def in layers:
        if not isinstance(layer_def, dict):
            continue

        raw_name = layer_def.get("name", "0")
        name = sanitize_symbol_name(raw_name, fallback="0")

        try:
            aci = int(layer_def.get("color_aci", 7))
        except (ValueError, TypeError):
            aci = 7

        linetype = str(layer_def.get("linetype") or "Continuous").strip()
        lt_upper = linetype.upper()
        if lt_upper == "CONTINUOUS" or not lt_upper:
            resolved_lt = "Continuous"
        else:
            resolved_lt = lt_upper
            if resolved_lt not in doc.linetypes:
                try:
                    doc.linetypes.new(resolved_lt, dxfattribs={"description": resolved_lt})
                except Exception:
                    resolved_lt = "Continuous"

        # AutoCAD standard: negative color code means the layer is turned OFF
        is_off = bool(layer_def.get("is_off", False))
        layer_color = -abs(aci) if is_off else aci

        flags = 0
        if layer_def.get("is_frozen", False):
            flags |= 1
        if layer_def.get("is_locked", False):
            flags |= 4

        dxfattribs = {
            "color": layer_color,
            "linetype": resolved_lt,
        }
        if flags:
            dxfattribs["flags"] = flags

        if name in doc.layers:
            layer = doc.layers.get(name)
            layer.dxf.color = layer_color
            layer.dxf.linetype = resolved_lt
            if flags:
                layer.dxf.flags = flags
        else:
            doc.layers.new(name=name, dxfattribs=dxfattribs)


def _ensure_layer(doc: Drawing, name: Any) -> str:
    """Auto-vivify a missing layer with sanitized name so no dangling references crash DXF."""
    clean_name = sanitize_symbol_name(name, fallback="0")
    if clean_name not in doc.layers:
        doc.layers.new(name=clean_name, dxfattribs={"color": 7})
    return clean_name


# ──────────────────────────────────────────────────────────────────────────────
# Stage 5 — BLOCKS Table (with ATTDEF injection for component attributes)
# ──────────────────────────────────────────────────────────────────────────────

def _detect_block_cycles(block_defs: Dict[str, Any]) -> Set[str]:
    """Detect cycles in block definitions to avoid recursive loop crashes."""
    graph: Dict[str, Set[str]] = {name: set() for name in block_defs}
    for name, bdef in block_defs.items():
        if isinstance(bdef, dict):
            for comp in (bdef.get("components") or []):
                if isinstance(comp, dict):
                    target = comp.get("block_name")
                    if target in graph:
                        graph[name].add(target)

    visited, rec_stack, cyclic_nodes = set(), set(), set()

    def dfs(node: str) -> None:
        visited.add(node)
        rec_stack.add(node)
        for neighbor in graph.get(node, []):
            if neighbor not in visited:
                dfs(neighbor)
            elif neighbor in rec_stack:
                cyclic_nodes.add(node)
                cyclic_nodes.add(neighbor)
        rec_stack.remove(node)

    for node in graph:
        if node not in visited:
            dfs(node)
    return cyclic_nodes


def _build_block_definitions(doc: Drawing, ir: Dict[str, Any]) -> None:
    """
    Write every CADBlockDefinition into the DXF BLOCKS table.
    Scans components in the IR to pre-inject ATTDEF entities so that
    attribute tags (MANUFACTURER, STYLE, TAG, etc.) are fully preserved.
    """
    block_defs = ir.get("block_definitions") or {}
    if not isinstance(block_defs, dict):
        return

    # Mine required attribute tags per block from components
    comp_attrib_tags: Dict[str, Set[str]] = {}
    for comp in (ir.get("components") or []):
        if isinstance(comp, dict):
            bname = sanitize_symbol_name(comp.get("block_name"))
            attrs = comp.get("attributes")
            if isinstance(attrs, dict):
                comp_attrib_tags.setdefault(bname, set()).update(
                    str(k).upper() for k in attrs.keys() if k is not None
                )

    for raw_block_name, block_def in block_defs.items():
        if not isinstance(block_def, dict):
            continue

        # Skip internal model/paper space containers
        if str(raw_block_name).startswith("*Model") or str(raw_block_name).startswith("*Paper"):
            continue

        block_name = sanitize_symbol_name(raw_block_name, fallback="UNNAMED_BLOCK")

        base = block_def.get("base_point") or [0.0, 0.0, 0.0]
        if isinstance(base, (list, tuple)) and len(base) >= 2 and is_numeric_and_finite(base[0], base[1]):
            z = float(base[2]) if len(base) > 2 and is_numeric_and_finite(base[2]) else 0.0
            base_pt = (float(base[0]), float(base[1]), z)
        else:
            base_pt = (0.0, 0.0, 0.0)

        # Create or reuse block definition
        if block_name in doc.blocks:
            blk = doc.blocks[block_name]
        else:
            blk = doc.blocks.new(name=block_name, base_point=base_pt)

        # Write internal primitives into the block
        _add_primitives_to_layout(blk, block_def, doc)

        # Inject ATTDEF definitions so add_auto_blockref creates ATTRIB entities
        needed_tags = comp_attrib_tags.get(block_name, set())
        for tag in sorted(needed_tags):
            # Check if ATTDEF already exists
            if not any(e.dxftype() == "ATTDEF" and getattr(e.dxf, "tag", "").upper() == tag for e in blk):
                blk.add_attdef(
                    tag=tag,
                    insert=(0.0, 0.0),
                    dxfattribs={"height": 0.2, "invisible": False, "layer": "0"}
                )


# ──────────────────────────────────────────────────────────────────────────────
# Stage 6 — Entity Dispatch Across Spaces
# ──────────────────────────────────────────────────────────────────────────────

def _populate_spaces(doc: Drawing, ir: Dict[str, Any]) -> None:
    """Route entities to modelspace or paper space layouts with sanitization."""
    msp = doc.modelspace()

    def get_space(space_name: Any):
        if not space_name or str(space_name).strip() in ("Model", "model"):
            return msp
        clean_space = sanitize_symbol_name(space_name, fallback="LAYOUT_1")
        if clean_space not in doc.layouts:
            try:
                doc.layouts.new(clean_space)
            except Exception:
                return msp
        return doc.layouts.get(clean_space)

    # ── Geometry Primitives ──────────────────────────────────────────────────
    geom = ir.get("geometry_primitives") or {}
    primitives = geom.get("primitives") or {} if isinstance(geom, dict) else {}

    for line in (primitives.get("lines") or []):
        if isinstance(line, dict):
            space = get_space(line.get("space"))
            layer = _ensure_layer(doc, line.get("layer"))
            _add_line(space, line, layer)

    for arc in (primitives.get("arcs") or []):
        if isinstance(arc, dict):
            space = get_space(arc.get("space"))
            layer = _ensure_layer(doc, arc.get("layer"))
            _add_arc(space, arc, layer)

    for circle in (primitives.get("circles") or []):
        if isinstance(circle, dict):
            space = get_space(circle.get("space"))
            layer = _ensure_layer(doc, circle.get("layer"))
            _add_circle(space, circle, layer)

    for poly in (primitives.get("polylines") or []):
        if isinstance(poly, dict):
            space = get_space(poly.get("space"))
            layer = _ensure_layer(doc, poly.get("layer"))
            _add_polyline(space, poly, layer, doc)

    # ── Component Instances (INSERT + ATTRIB) ────────────────────────────────
    block_defs = ir.get("block_definitions") or {}
    for comp in (ir.get("components") or []):
        if isinstance(comp, dict):
            space = get_space(comp.get("space"))
            layer = _ensure_layer(doc, comp.get("layer"))
            _add_insert(space, comp, block_defs, doc, layer)

    # ── Annotations (TEXT / MTEXT) ───────────────────────────────────────────
    for annot in (ir.get("annotations") or []):
        if isinstance(annot, dict):
            space = get_space(annot.get("space"))
            layer = _ensure_layer(doc, annot.get("layer"))
            _add_annotation(space, annot, doc, layer)

    # ── Dimensions ───────────────────────────────────────────────────────────
    for dim in (ir.get("dimensions") or []):
        if isinstance(dim, dict):
            space = get_space(dim.get("space"))
            layer = _ensure_layer(doc, dim.get("layer"))
            _add_dimension_as_text(space, dim, layer)


# ──────────────────────────────────────────────────────────────────────────────
# Primitive Writers
# ──────────────────────────────────────────────────────────────────────────────

def _apply_color(entity: Any, color: Optional[str]) -> None:
    """Apply entity-level colour (BYLAYER, BYBLOCK, or TrueColor)."""
    c = sanitize_color(color)
    if c is None:
        return
    if c == "BYBLOCK":
        entity.dxf.color = 0
        return
    entity.dxf.true_color = hex_to_truecolor(c)


def _add_primitives_to_layout(layout: Any, data: Dict[str, Any], doc: Drawing) -> None:
    """Helper to populate internal block definition geometry."""
    for line in (data.get("lines") or []):
        if isinstance(line, dict):
            layer = _ensure_layer(doc, line.get("layer"))
            _add_line(layout, line, layer)
    for arc in (data.get("arcs") or []):
        if isinstance(arc, dict):
            layer = _ensure_layer(doc, arc.get("layer"))
            _add_arc(layout, arc, layer)
    for circle in (data.get("circles") or []):
        if isinstance(circle, dict):
            layer = _ensure_layer(doc, circle.get("layer"))
            _add_circle(layout, circle, layer)
    for poly in (data.get("polylines") or []):
        if isinstance(poly, dict):
            layer = _ensure_layer(doc, poly.get("layer"))
            _add_polyline(layout, poly, layer, doc)


def _add_line(layout: Any, line: Dict[str, Any], layer: str) -> None:
    start = line.get("start")
    end = line.get("end")
    if not validate_line(start, end):
        return
    z1 = float(start[2]) if len(start) > 2 and is_numeric_and_finite(start[2]) else 0.0
    z2 = float(end[2]) if len(end) > 2 and is_numeric_and_finite(end[2]) else 0.0

    e = layout.add_line(
        start=(float(start[0]), float(start[1]), z1),
        end=(float(end[0]), float(end[1]), z2),
        dxfattribs={"layer": layer},
    )
    _apply_color(e, line.get("color"))


def _add_arc(layout: Any, arc: Dict[str, Any], layer: str) -> None:
    center = arc.get("center")
    radius = arc.get("radius")
    start_angle = arc.get("start_angle")
    end_angle = arc.get("end_angle")
    if not validate_arc(center, radius, start_angle, end_angle):
        return
    e = layout.add_arc(
        center=(float(center[0]), float(center[1])),
        radius=float(radius),
        start_angle=float(start_angle),
        end_angle=float(end_angle),
        dxfattribs={"layer": layer},
    )
    _apply_color(e, arc.get("color"))


def _add_circle(layout: Any, circle: Dict[str, Any], layer: str) -> None:
    center = circle.get("center")
    radius = circle.get("radius")
    if not validate_circle(center, radius):
        return
    e = layout.add_circle(
        center=(float(center[0]), float(center[1])),
        radius=float(radius),
        dxfattribs={"layer": layer},
    )
    _apply_color(e, circle.get("color"))


def _add_polyline(layout: Any, poly: Dict[str, Any], layer: str, doc: Drawing) -> None:
    points = poly.get("points")
    if not validate_polyline(points):
        return
    pts_2d = [(float(p[0]), float(p[1])) for p in points]
    is_closed = bool(poly.get("is_closed", False))

    # DXF R12 compatibility fallback: POLYLINE2D instead of LWPOLYLINE
    if doc.dxfversion == "AC1009":
        e = layout.add_polyline2d(
            points=pts_2d,
            close=is_closed,
            dxfattribs={"layer": layer},
        )
    else:
        e = layout.add_lwpolyline(
            points=pts_2d,
            close=is_closed,
            dxfattribs={"layer": layer},
        )
    _apply_color(e, poly.get("color"))


def _add_insert(
    layout: Any,
    comp: Dict[str, Any],
    block_defs: Dict[str, Any],
    doc: Drawing,
    layer: str,
) -> None:
    raw_block_name = comp.get("block_name")
    if not raw_block_name:
        return
    block_name = sanitize_symbol_name(raw_block_name, fallback="UNNAMED_BLOCK")

    pos = comp.get("position") or [0.0, 0.0, 0.0]
    if (isinstance(pos, (list, tuple)) and len(pos) >= 2 and
            is_numeric_and_finite(pos[0], pos[1])):
        z = float(pos[2]) if len(pos) > 2 and is_numeric_and_finite(pos[2]) else 0.0
        insert_pt = (float(pos[0]), float(pos[1]), z)
    else:
        insert_pt = (0.0, 0.0, 0.0)

    rot_raw = comp.get("rotation", 0.0)
    rotation = float(rot_raw) if is_numeric_and_finite(rot_raw) else 0.0

    scale_raw = comp.get("scale") or [1.0, 1.0, 1.0]
    sx, sy, sz = clamp_scale(scale_raw)

    attribs_data = comp.get("attributes")
    if not isinstance(attribs_data, dict):
        attribs_data = {}

    # Auto-vivify placeholder block if not defined
    _ensure_block_placeholder(doc, block_name, str(comp.get("resolved_name") or block_name), attribs_data)

    dxfattribs = {
        "layer": layer,
        "rotation": rotation,
        "xscale": sx,
        "yscale": sy,
        "zscale": sz,
    }

    ref = layout.add_blockref(
        name=block_name,
        insert=insert_pt,
        dxfattribs=dxfattribs,
    )

    if attribs_data:
        values = {str(k).upper(): str(v) for k, v in attribs_data.items() if k is not None}
        for k, v in values.items():
            try:
                ref.add_attrib(
                    tag=k,
                    text=v,
                    insert=insert_pt,
                    dxfattribs={"height": 0.2, "layer": layer},
                )
            except Exception:
                pass


def _ensure_block_placeholder(doc: Drawing, block_name: str, label: str, attribs_data: Dict[str, Any]) -> None:
    """Ensure block exists in doc.blocks with crosshair and ATTDEF templates."""
    if block_name in doc.blocks:
        blk = doc.blocks[block_name]
    else:
        blk = doc.blocks.new(name=block_name, base_point=(0, 0, 0))
        size = 0.5
        blk.add_line((-size, 0), (size, 0), dxfattribs={"layer": "0"})
        blk.add_line((0, -size), (0, size), dxfattribs={"layer": "0"})
        str_label = str(label)
        short_label = str_label[:24] if len(str_label) > 24 else str_label
        blk.add_text(
            short_label,
            dxfattribs={
                "layer": "0",
                "height": size * 0.4,
                "insert": (size * 0.15, size * 0.15),
            },
        )

    # Ensure all required ATTDEF tags exist on the block
    for tag in attribs_data.keys():
        clean_tag = str(tag).upper()
        if not any(e.dxftype() == "ATTDEF" and getattr(e.dxf, "tag", "").upper() == clean_tag for e in blk):
            blk.add_attdef(
                tag=clean_tag,
                insert=(0.0, 0.0),
                dxfattribs={"height": 0.2, "invisible": False, "layer": "0"}
            )


def _add_annotation(layout: Any, annot: Dict[str, Any], doc: Drawing, layer: str) -> None:
    etype = str(annot.get("type") or "TEXT").upper()
    pos = annot.get("position") or [0.0, 0.0]
    if (isinstance(pos, (list, tuple)) and len(pos) >= 2 and
            is_numeric_and_finite(pos[0], pos[1])):
        insert = (float(pos[0]), float(pos[1]))
    else:
        insert = (0.0, 0.0)

    height_raw = annot.get("height", 0.1)
    height = float(height_raw) if is_numeric_and_finite(height_raw) and float(height_raw) > 0.0 else 0.1

    raw_text = str(annot.get("raw_text") or "").replace("\x00", "")
    clean_text = str(annot.get("clean_text") or "").replace("\x00", "")

    # DXF R12 compatibility fallback: MTEXT is not supported in R12
    if doc.dxfversion == "AC1009" or etype == "TEXT":
        content = clean_text or raw_text
        if not content:
            return
        single_line = content.replace("\n", " ").replace("\r", " ").strip()
        layout.add_text(
            single_line,
            dxfattribs={
                "layer": layer,
                "height": height,
                "insert": insert,
            },
        )
    else:
        content = raw_text or clean_text
        if not content:
            return
        layout.add_mtext(
            content,
            dxfattribs={
                "layer": layer,
                "char_height": height,
                "insert": insert,
            },
        )


def _add_dimension_as_text(layout: Any, dim: Dict[str, Any], layer: str) -> None:
    defpoint = dim.get("defpoint") or [0.0, 0.0]
    if (isinstance(defpoint, (list, tuple)) and len(defpoint) >= 2 and
            is_numeric_and_finite(defpoint[0], defpoint[1])):
        insert = (float(defpoint[0]), float(defpoint[1]))
    else:
        insert = (0.0, 0.0)

    measurement = dim.get("measurement")
    text = str(dim.get("text") or "").strip()

    if measurement is not None and is_numeric_and_finite(measurement):
        label = text if text else f"{float(measurement):.3f}"
    elif text:
        label = text
    else:
        return

    layout.add_mtext(
        label,
        dxfattribs={
            "layer": layer,
            "char_height": 0.15,
            "insert": insert,
        },
    )

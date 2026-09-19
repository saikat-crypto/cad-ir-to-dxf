"""
compiler.py — Hardened IR v3 → DXF Compilation Engine (with Preset Architecture).

Translates a LAVINCI_CAD_IR_V3 payload (JSON file, dict, or Pydantic model) into a
fully compliant DXF file driven by a curated preset profile + optional AdvancedOptions.

## Preset Architecture
    1 IR  ─►  preset (+ optional AdvancedOptions)  ─►  ResolvedConfig  ─►  DXF
    Relationship: One-to-Many (same IR can produce many preset-flavoured DXFs).

## Available Presets (pass preset= to compile_ir_to_dxf or --preset via CLI)
    'standard'        Modern DXF R2013, native analytic curves, TrueColor, hierarchical
                      blocks. Best for AutoCAD, Revit, Rhino, Fusion 360. [DEFAULT]
    'cnc_cam'         DXF R12, 2D flat (Z=0), blocks exploded to primitives, splines
                      converted to polylines, no text/dims. For laser/CNC/CAM.
    'arch_print'      DXF R2013 + auto-provisioned PaperSpace layout tab with scaled
                      viewport and printable border. For client-ready print sheets.
    'web_lightweight' DXF R2000, stripped metadata, ACI color, Z-flattened, no dims.
                      Compact output for web viewers, three.js, GIS, mobile apps.
    'bim_overlay'     DXF R2018, strict world-coordinate origin, IR_ layer prefix to
                      prevent BIM layer name collisions. For Revit/Navisworks underlays.

## Hardening
  - Pydantic model & null-safe defensive ingestion
  - Full layer attribute & visibility retention (linetypes, is_off, is_frozen, is_locked)
  - True component attribute retention (ATTDEF in blocks + ATTRIB on INSERTs)
  - Symbol name sanitization ([<>/\\\":;?*|=,'] replaced with underscores)
  - 3D line coordinate preservation (Z=0 flattening only when flatten_z=True in config)
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
from .presets import ResolvedConfig, get_preset_config, PresetName, AdvancedOptions, PAPER_SIZES_MM
from .exceptions import (
    BlockCycleError,
    IRFileNotFoundError,
    IRParseError,
    MissingFormatHeaderError,
    InvalidPresetError,
    InvalidOptionError,
    InvalidPaperSizeError,
    InvalidColorModeError,
    InvalidDxfVersionError,
    InvalidOrientationError,
    InvalidViewportScaleError,
    OutputWriteError,
    StrictModeViolationError,
)
from .diagnostics import CompilationDiagnostics, Severity
from typing import Union as _Union

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
    preset: _Union[str, "PresetName"] = "standard",
    advanced_options: Optional[_Union["AdvancedOptions", Dict[str, Any]]] = None,
    diagnostics: Optional["CompilationDiagnostics"] = None,
    strict: bool = False,
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
        DEPRECATED — prefer `preset` instead. When provided alongside `preset`,
        this overrides the preset's DXF version. Default: "R2013".
    preset:
        Named safe preset controlling the full compilation profile.
        One of: 'standard' (default), 'cnc_cam', 'arch_print',
        'web_lightweight', 'bim_overlay'.
    advanced_options:
        Optional AdvancedOptions dataclass or plain dict of override fields.
        Only the keys you supply override the preset's baseline defaults.
    diagnostics:
        Optional CompilationDiagnostics instance to receive non-fatal events
        (dropped entities, auto-created layers, filtered layers, etc.).
        If None, diagnostics are silently discarded.
    strict:
        If True, non-fatal corrections (e.g. auto-creating missing layers,
        dropping degenerate geometry) raise StrictModeViolationError instead
        of being silently corrected.  Default: False.

    Returns
    -------
    ezdxf.Drawing
        The compiled, fully populated DXF document.

    Raises
    ------
    IRFileNotFoundError
        The given file path does not exist on disk.
    IRParseError
        The source string/file cannot be parsed as valid JSON.
    MissingFormatHeaderError
        The IR payload is missing the 'format' key or declares a wrong schema.
    InvalidPresetError
        An unrecognised preset name was provided.
    InvalidOptionError
        An AdvancedOptions field has an unsupported value.
    OutputWriteError
        The compiled DXF cannot be written to output_path.
    StrictModeViolationError
        (strict=True only) A non-fatal correction was attempted.

    Examples
    --------
    >>> doc = compile_ir_to_dxf("plan.json", preset="standard")
    >>> doc = compile_ir_to_dxf("plan.json", preset="cnc_cam")
    >>> doc = compile_ir_to_dxf("plan.json", preset="arch_print",
    ...     advanced_options={"layout": {"paper_size": "ISO_A1"}})
    >>> diag = CompilationDiagnostics()
    >>> doc = compile_ir_to_dxf("plan.json", diagnostics=diag)
    >>> diag.print_report()
    """
    # ── Resolve & validate preset ────────────────────────────────────────────
    try:
        cfg = get_preset_config(preset, advanced_options)
    except ValueError as e:
        raise InvalidPresetError(str(preset)) from e

    # ── Validate AdvancedOptions fields that have constrained value sets ─────
    _VALID_DXF_VERSIONS = {"R12", "R2000", "R2004", "R2007", "R2010", "R2013", "R2018"}
    _VALID_COLOR_MODES  = {"truecolor", "aci", "monochrome"}
    _VALID_ORIENTATIONS = {"landscape", "portrait"}

    if cfg.dxf_version not in _VALID_DXF_VERSIONS:
        raise InvalidDxfVersionError(cfg.dxf_version)
    if cfg.color_mode not in _VALID_COLOR_MODES:
        raise InvalidColorModeError(cfg.color_mode)
    if cfg.orientation.lower() not in _VALID_ORIENTATIONS:
        raise InvalidOrientationError(cfg.orientation)
    if cfg.paper_size.upper() not in PAPER_SIZES_MM:
        raise InvalidPaperSizeError(cfg.paper_size, list(PAPER_SIZES_MM.keys()))
    if cfg.viewport_scale not in ("auto",) and cfg.viewport_scale:
        # Validate ratio format like '1:50', '1:100', '1:1'
        parts = cfg.viewport_scale.split(":")
        try:
            if len(parts) == 2:
                float(parts[0]); float(parts[1])
            elif len(parts) == 1:
                float(parts[0])
            else:
                raise ValueError
        except (ValueError, TypeError):
            raise InvalidViewportScaleError(cfg.viewport_scale)

    # ── Allow dxf_version kwarg to override preset version (backward compat) ─
    if dxf_version != "R2013":
        if dxf_version not in _VALID_DXF_VERSIONS:
            raise InvalidDxfVersionError(dxf_version)
        cfg.dxf_version = dxf_version

    # ── Set up diagnostics collector ─────────────────────────────────────────
    diag = diagnostics if diagnostics is not None else CompilationDiagnostics()

    # ── Load IR ──────────────────────────────────────────────────────────────
    ir = _load_ir(ir_source, diag, strict)

    doc = ezdxf.new(cfg.dxf_version, setup=True)

    _configure_header(doc, ir, cfg)
    _load_linetypes(doc)
    _build_layer_table(doc, ir, cfg)
    _build_block_definitions(doc, ir, cfg, diag, strict)
    _populate_spaces(doc, ir, cfg, diag, strict)

    if cfg.create_paper_space:
        _build_paper_space_layout(doc, ir, cfg, diag)

    if output_path is not None:
        try:
            doc.saveas(str(output_path))
        except OSError as e:
            raise OutputWriteError(str(output_path), str(e)) from e

    return doc


# ──────────────────────────────────────────────────────────────────────────────
# Stage 1 — Load & parse the IR (with Pydantic & null safety)
# ──────────────────────────────────────────────────────────────────────────────

def _load_ir(
    source: Union[str, Path, Dict[str, Any], Any],
    diag: "CompilationDiagnostics",
    strict: bool = False,
) -> Dict[str, Any]:
    """
    Return the IR as a plain Python dict with safe dictionary defaults.

    Raises
    ------
    IRFileNotFoundError    File path given but does not exist.
    IRParseError           Source string or file is not valid JSON.
    MissingFormatHeaderError  Payload has no 'format' key or wrong schema.
    """
    # 1. Pydantic model support
    if hasattr(source, "model_dump") and callable(source.model_dump):
        data = source.model_dump()
    elif hasattr(source, "dict") and callable(source.dict):
        data = source.dict()
    # 2. Existing dictionary
    elif isinstance(source, dict):
        data = source
    else:
        # 3. File path or raw JSON string
        source_str = str(source)
        if os.path.isfile(source_str):
            try:
                with open(source_str, encoding="utf-8") as fh:
                    raw = fh.read()
                data = json.loads(raw)
            except json.JSONDecodeError as e:
                raise IRParseError(raw[:200], str(e)) from e
        else:
            # Could be a raw JSON string — check if it looks like a file path
            if source_str.endswith(".json") or os.sep in source_str or "/" in source_str:
                raise IRFileNotFoundError(source_str)
            # Try parsing as raw JSON
            try:
                data = json.loads(source_str)
            except json.JSONDecodeError as e:
                raise IRParseError(source_str, str(e)) from e

    if not isinstance(data, dict):
        raise MissingFormatHeaderError(payload_preview=type(data).__name__)

    # Validate LAVINCI_CAD_IR_V3 schema format header
    fmt = data.get("format", "")
    if not fmt:
        if strict:
            raise MissingFormatHeaderError(payload_preview={k: data.get(k) for k in list(data)[:3]})
        diag.ir_schema_warning(
            message="IR payload is missing the 'format' header. Expected 'LAVINCI_CAD_IR_V3'.",
            field="format",
        )
    elif fmt != "LAVINCI_CAD_IR_V3":
        diag.ir_schema_warning(
            message=f"IR payload declares format '{fmt}' instead of 'LAVINCI_CAD_IR_V3'. Compilation will proceed but some fields may not be recognised.",
            field="format",
        )

    return data



# ──────────────────────────────────────────────────────────────────────────────
# Stage 2 — DXF Header
# ──────────────────────────────────────────────────────────────────────────────

def _configure_header(doc: Drawing, ir: Dict[str, Any], cfg: "ResolvedConfig") -> None:
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

def _build_layer_table(doc: Drawing, ir: Dict[str, Any], cfg: "ResolvedConfig") -> None:
    """
    Register every layer from the IR into the DXF TABLES section.
    Preserves linetypes, colors (including negative color for layer off),
    and frozen / locked flags.
    Applies cfg.layer_prefix to all layer names.
    In monochrome mode, all layer colors are set to ACI 7 (black/white).
    """
    layers = ir.get("layers") or []
    for layer_def in layers:
        if not isinstance(layer_def, dict):
            continue

        raw_name = layer_def.get("name", "0")
        name = sanitize_symbol_name(raw_name, fallback="0")

        # Apply layer_prefix from config (e.g. 'IR_' for BIM overlay)
        if cfg.layer_prefix and name != "0":
            name = cfg.layer_prefix + name

        try:
            aci = int(layer_def.get("color_aci", 7))
        except (ValueError, TypeError):
            aci = 7

        # monochrome mode: force all layers to ACI 7 (white/black)
        if cfg.color_mode == "monochrome":
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



def _ensure_layer(
    doc: Drawing,
    name: Any,
    cfg: Optional["ResolvedConfig"] = None,
    diag: Optional["CompilationDiagnostics"] = None,
    strict: bool = False,
) -> str:
    """Auto-vivify a missing layer with sanitized name so no dangling references crash DXF.
    Applies cfg.layer_prefix when set. Emits a diagnostic or raises in strict mode."""
    clean_name = sanitize_symbol_name(name, fallback="0")
    if cfg and cfg.layer_prefix and clean_name != "0":
        clean_name = cfg.layer_prefix + clean_name
    if clean_name not in doc.layers:
        if strict:
            raise StrictModeViolationError(
                message=f"Layer '{clean_name}' was referenced by an entity but not declared in the IR layers table.",
                hint=f"Add a layer definition for '{clean_name}' to the 'layers' list in your IR payload.",
                offender=clean_name,
            )
        if diag is not None:
            diag.layer_auto_created(clean_name)
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


def _build_block_definitions(
    doc: Drawing,
    ir: Dict[str, Any],
    cfg: "ResolvedConfig",
    diag: Optional["CompilationDiagnostics"] = None,
    strict: bool = False,
) -> None:
    """
    Write every CADBlockDefinition into the DXF BLOCKS table.
    Scans components in the IR to pre-inject ATTDEF entities so that
    attribute tags (MANUFACTURER, STYLE, TAG, etc.) are fully preserved.
    """
    block_defs = ir.get("block_definitions") or {}
    if not isinstance(block_defs, dict):
        return

    cyclic_nodes = _detect_block_cycles(block_defs)
    if cyclic_nodes:
        if strict:
            raise BlockCycleError(list(cyclic_nodes))
        elif diag is not None:
            diag.custom(
                Severity.WARN,
                "block_cycle",
                f"Circular block reference detected involving: {list(cyclic_nodes)}.",
                suggestion="Remove circular component references within block_definitions.",
                offender=list(cyclic_nodes),
            )

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
        _add_primitives_to_layout(blk, block_def, doc, cfg, diag, strict)

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

def _populate_spaces(
    doc: Drawing,
    ir: Dict[str, Any],
    cfg: "ResolvedConfig",
    diag: Optional["CompilationDiagnostics"] = None,
    strict: bool = False,
) -> None:
    """Route entities to modelspace or paper space layouts with sanitization.
    Applies layer include/exclude filters and annotation/dimension suppression from cfg."""
    msp = doc.modelspace()

    # Build layer filter sets from cfg
    include_set = set(cfg.include_layers) if cfg.include_layers else None
    exclude_set = set(cfg.exclude_layers) if cfg.exclude_layers else set()

    def _layer_allowed(raw_layer: Any) -> bool:
        """Return True if this layer passes include/exclude filter rules."""
        lname = sanitize_symbol_name(raw_layer, fallback="0")
        if cfg.layer_prefix and lname != "0":
            lname = cfg.layer_prefix + lname
        # Exclude filter with simple '*' suffix wildcard support
        for pat in exclude_set:
            if pat.endswith("*"):
                if lname.startswith(pat[:-1]) or lname.upper().startswith(pat[:-1].upper()):
                    return False
            elif lname == pat or lname.upper() == pat.upper():
                return False
        # Include filter: if set, only explicitly listed layers allowed
        if include_set is not None:
            return (lname in include_set or lname.upper() in {s.upper() for s in include_set})
        return True

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
        if isinstance(line, dict) and _layer_allowed(line.get("layer")):
            space = get_space(line.get("space"))
            layer = _ensure_layer(doc, line.get("layer"), cfg, diag, strict)
            _add_line(space, line, layer, cfg, diag, strict)

    for arc in (primitives.get("arcs") or []):
        if isinstance(arc, dict) and _layer_allowed(arc.get("layer")):
            space = get_space(arc.get("space"))
            layer = _ensure_layer(doc, arc.get("layer"), cfg, diag, strict)
            _add_arc(space, arc, layer, diag, strict)

    for circle in (primitives.get("circles") or []):
        if isinstance(circle, dict) and _layer_allowed(circle.get("layer")):
            space = get_space(circle.get("space"))
            layer = _ensure_layer(doc, circle.get("layer"), cfg, diag, strict)
            _add_circle(space, circle, layer, diag, strict)

    for poly in (primitives.get("polylines") or []):
        if isinstance(poly, dict) and _layer_allowed(poly.get("layer")):
            space = get_space(poly.get("space"))
            layer = _ensure_layer(doc, poly.get("layer"), cfg, diag, strict)
            _add_polyline(space, poly, layer, doc, diag, strict)

    # ── Component Instances (INSERT + ATTRIB) ────────────────────────────────
    block_defs = ir.get("block_definitions") or {}
    for comp in (ir.get("components") or []):
        if isinstance(comp, dict) and _layer_allowed(comp.get("layer")):
            space = get_space(comp.get("space"))
            layer = _ensure_layer(doc, comp.get("layer"), cfg, diag, strict)
            _add_insert(space, comp, block_defs, doc, layer, cfg, diag, strict)

    # ── Annotations (TEXT / MTEXT) ───────────────────────────────────────────
    all_annots = [a for a in (ir.get("annotations") or []) if isinstance(a, dict)]
    if cfg.include_annotations:
        for annot in all_annots:
            if _layer_allowed(annot.get("layer")):
                space = get_space(annot.get("space"))
                layer = _ensure_layer(doc, annot.get("layer"), cfg, diag, strict)
                _add_annotation(space, annot, doc, layer)
    elif all_annots and diag is not None:
        diag.entity_suppressed_by_preset("TEXT/MTEXT", len(all_annots), cfg.preset_name, "preset disables annotations")

    # ── Dimensions ───────────────────────────────────────────────────────────
    all_dims = [d for d in (ir.get("dimensions") or []) if isinstance(d, dict)]
    if cfg.include_dimensions:
        for dim in all_dims:
            if _layer_allowed(dim.get("layer")):
                space = get_space(dim.get("space"))
                layer = _ensure_layer(doc, dim.get("layer"), cfg, diag, strict)
                _add_dimension_as_text(space, dim, layer)
    elif all_dims and diag is not None:
        diag.entity_suppressed_by_preset("DIMENSION", len(all_dims), cfg.preset_name, "preset disables dimensions")


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


def _add_primitives_to_layout(
    layout: Any,
    data: Dict[str, Any],
    doc: Drawing,
    cfg: Optional["ResolvedConfig"] = None,
    diag: Optional["CompilationDiagnostics"] = None,
    strict: bool = False,
) -> None:
    """Helper to populate internal block definition geometry."""
    for line in (data.get("lines") or []):
        if isinstance(line, dict):
            layer = _ensure_layer(doc, line.get("layer"), cfg, diag, strict)
            _add_line(layout, line, layer, cfg, diag, strict)
    for arc in (data.get("arcs") or []):
        if isinstance(arc, dict):
            layer = _ensure_layer(doc, arc.get("layer"), cfg, diag, strict)
            _add_arc(layout, arc, layer, diag, strict)
    for circle in (data.get("circles") or []):
        if isinstance(circle, dict):
            layer = _ensure_layer(doc, circle.get("layer"), cfg, diag, strict)
            _add_circle(layout, circle, layer, diag, strict)
    for poly in (data.get("polylines") or []):
        if isinstance(poly, dict):
            layer = _ensure_layer(doc, poly.get("layer"), cfg, diag, strict)
            _add_polyline(layout, poly, layer, doc, diag, strict)


def _add_line(
    layout: Any,
    line: Dict[str, Any],
    layer: str,
    cfg: Optional["ResolvedConfig"] = None,
    diag: Optional["CompilationDiagnostics"] = None,
    strict: bool = False,
) -> None:
    start = line.get("start")
    end = line.get("end")
    if not validate_line(start, end):
        if strict:
            raise StrictModeViolationError(
                message=f"Degenerate or invalid line on layer '{layer}': start={start}, end={end}",
                hint="Check line coordinates for non-finite values (NaN, Inf) or zero length.",
                offender=line,
            )
        if diag is not None:
            diag.entity_dropped_degenerate("LINE", layer, f"invalid or zero-length: start={start}, end={end}")
        return
    # Apply flatten_z: force Z to 0.0 when set in cfg (e.g. cnc_cam, web_lightweight)
    flatten = cfg.flatten_z if cfg else False
    z1 = 0.0 if flatten else (float(start[2]) if len(start) > 2 and is_numeric_and_finite(start[2]) else 0.0)
    z2 = 0.0 if flatten else (float(end[2]) if len(end) > 2 and is_numeric_and_finite(end[2]) else 0.0)

    e = layout.add_line(
        start=(float(start[0]), float(start[1]), z1),
        end=(float(end[0]), float(end[1]), z2),
        dxfattribs={"layer": layer},
    )
    _apply_color(e, line.get("color"))


def _add_arc(
    layout: Any,
    arc: Dict[str, Any],
    layer: str,
    diag: Optional["CompilationDiagnostics"] = None,
    strict: bool = False,
) -> None:
    center = arc.get("center")
    radius = arc.get("radius")
    start_angle = arc.get("start_angle")
    end_angle = arc.get("end_angle")
    if not validate_arc(center, radius, start_angle, end_angle):
        if strict:
            raise StrictModeViolationError(
                message=f"Degenerate or invalid arc on layer '{layer}': center={center}, radius={radius}",
                hint="Check arc parameters (radius must be > 0 and center/angles must be finite).",
                offender=arc,
            )
        if diag is not None:
            diag.entity_dropped_degenerate("ARC", layer, f"invalid center={center}, radius={radius}")
        return
    e = layout.add_arc(
        center=(float(center[0]), float(center[1])),
        radius=float(radius),
        start_angle=float(start_angle),
        end_angle=float(end_angle),
        dxfattribs={"layer": layer},
    )
    _apply_color(e, arc.get("color"))


def _add_circle(
    layout: Any,
    circle: Dict[str, Any],
    layer: str,
    diag: Optional["CompilationDiagnostics"] = None,
    strict: bool = False,
) -> None:
    center = circle.get("center")
    radius = circle.get("radius")
    if not validate_circle(center, radius):
        if strict:
            raise StrictModeViolationError(
                message=f"Degenerate or invalid circle on layer '{layer}': center={center}, radius={radius}",
                hint="Check circle parameters (radius must be > 0 and center must be finite).",
                offender=circle,
            )
        if diag is not None:
            diag.entity_dropped_degenerate("CIRCLE", layer, f"invalid center={center}, radius={radius}")
        return
    e = layout.add_circle(
        center=(float(center[0]), float(center[1])),
        radius=float(radius),
        dxfattribs={"layer": layer},
    )
    _apply_color(e, circle.get("color"))


def _add_polyline(
    layout: Any,
    poly: Dict[str, Any],
    layer: str,
    doc: Drawing,
    diag: Optional["CompilationDiagnostics"] = None,
    strict: bool = False,
) -> None:
    points = poly.get("points")
    if not validate_polyline(points):
        if strict:
            raise StrictModeViolationError(
                message=f"Degenerate or invalid polyline on layer '{layer}': points={points}",
                hint="Polylines must have at least 2 valid, finite vertices.",
                offender=poly,
            )
        if diag is not None:
            diag.entity_dropped_degenerate("POLYLINE", layer, "fewer than 2 valid points")
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
    cfg: Optional["ResolvedConfig"] = None,
    diag: Optional["CompilationDiagnostics"] = None,
    strict: bool = False,
) -> None:
    raw_block_name = comp.get("block_name")
    if not raw_block_name:
        return
    block_name = sanitize_symbol_name(raw_block_name, fallback="UNNAMED_BLOCK")

    pos = comp.get("position") or [0.0, 0.0, 0.0]
    if (isinstance(pos, (list, tuple)) and len(pos) >= 2 and
            is_numeric_and_finite(pos[0], pos[1])):
        flatten = cfg.flatten_z if cfg else False
        z = 0.0 if flatten else (float(pos[2]) if len(pos) > 2 and is_numeric_and_finite(pos[2]) else 0.0)
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
    _ensure_block_placeholder(doc, block_name, str(comp.get("resolved_name") or block_name), attribs_data, diag, strict)

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


def _ensure_block_placeholder(
    doc: Drawing,
    block_name: str,
    label: str,
    attribs_data: Dict[str, Any],
    diag: Optional["CompilationDiagnostics"] = None,
    strict: bool = False,
) -> None:
    """Ensure block exists in doc.blocks with crosshair and ATTDEF templates."""
    if block_name in doc.blocks:
        blk = doc.blocks[block_name]
    else:
        if strict:
            raise StrictModeViolationError(
                message=f"Block '{block_name}' was referenced by a component INSERT but not defined in block_definitions.",
                hint=f"Define '{block_name}' in block_definitions or verify name spelling.",
                offender=block_name,
            )
        if diag is not None:
            diag.block_auto_vivified(block_name)
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
    # Prefer explicitly positioned text midpoint anchor if available
    pos = dim.get("text_midpoint") or dim.get("defpoint") or [0.0, 0.0]
    if (isinstance(pos, (list, tuple)) and len(pos) >= 2 and
            is_numeric_and_finite(pos[0], pos[1])):
        insert = (float(pos[0]), float(pos[1]))
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

    raw_h = dim.get("text_height")
    char_h = float(raw_h) if (is_numeric_and_finite(raw_h) and float(raw_h) > 0.0) else 0.15

    attribs = {
        "layer": layer,
        "char_height": char_h,
        "insert": insert,
    }

    raw_rot = dim.get("text_rotation")
    if is_numeric_and_finite(raw_rot):
        attribs["rotation"] = float(raw_rot)

    layout.add_mtext(label, dxfattribs=attribs)


# ──────────────────────────────────────────────────────────────────────────────
# Paper Space Layout Builder (arch_print preset)
# ──────────────────────────────────────────────────────────────────────────────

def _build_paper_space_layout(
    doc: Drawing,
    ir: Dict[str, Any],
    cfg: "ResolvedConfig",
    diag: Optional["CompilationDiagnostics"] = None,
) -> None:
    """
    Provision a PaperSpace layout tab with a printable border and a scaled
    viewport looking into ModelSpace.  Used by the arch_print preset.
    """
    from .presets import PAPER_SIZES_MM
    import math as _math

    # Resolve sheet dimensions in mm
    size_key = (cfg.paper_size or "ISO_A3").upper()
    if size_key not in PAPER_SIZES_MM:
        size_key = "ISO_A3"
    w_mm, h_mm = PAPER_SIZES_MM[size_key]

    # Swap for portrait
    if (cfg.orientation or "landscape").lower() == "portrait":
        w_mm, h_mm = h_mm, w_mm

    margin = float(cfg.margin_mm or 10.0)

    # Create (or reuse) Paper Space layout
    layout_name = "Presentation_Sheet"
    if layout_name not in doc.layouts:
        try:
            layout = doc.layouts.new(layout_name)
        except Exception:
            return
    else:
        layout = doc.layouts.get(layout_name)

    # Configure the layout's page setup (units in mm = 1, landscape/portrait)
    try:
        layout.page_setup(
            size=(w_mm, h_mm),
            margins=(margin, margin, margin, margin),
            units="mm",
        )
    except Exception:
        pass  # page_setup may not be available in older ezdxf versions

    # Draw border rectangle (paper extents minus margins)
    border_pts = [
        (margin, margin),
        (w_mm - margin, margin),
        (w_mm - margin, h_mm - margin),
        (margin, h_mm - margin),
        (margin, margin),
    ]
    layout.add_lwpolyline(border_pts, dxfattribs={"layer": "0", "lineweight": 50})

    # Compute model-space extents
    extents = ir.get("extents") or {}
    mn = extents.get("min") or [0.0, 0.0]
    mx = extents.get("max") or [0.0, 0.0]
    try:
        model_w = float(mx[0]) - float(mn[0])
        model_h = float(mx[1]) - float(mn[1])
        model_cx = (float(mn[0]) + float(mx[0])) / 2.0
        model_cy = (float(mn[1]) + float(mx[1])) / 2.0
    except Exception:
        model_w, model_h = 1000.0, 1000.0
        model_cx, model_cy = 500.0, 500.0

    if model_w <= 0 or model_h <= 0:
        model_w, model_h = 1000.0, 1000.0
        model_cx, model_cy = 500.0, 500.0

    # Determine viewport scale: auto-fit or user-specified ratio
    vp_w = w_mm - 2 * margin
    vp_h = h_mm - 2 * margin
    viewport_scale_str = cfg.viewport_scale or "auto"

    if viewport_scale_str == "auto" or not viewport_scale_str:
        # Fit model into viewport; scale = paper_size / model_size
        scale = min(vp_w / model_w, vp_h / model_h)
    else:
        # Parse "1:50" → 1/50, "1:100" → 1/100
        try:
            parts = viewport_scale_str.split(":")
            scale = float(parts[0]) / float(parts[1]) if len(parts) == 2 else float(parts[0])
        except Exception:
            scale = min(vp_w / model_w, vp_h / model_h)

    # Add viewport centred on the sheet interior
    vp_cx = w_mm / 2.0
    vp_cy = h_mm / 2.0
    vp_width = min(vp_w, model_w * scale)
    vp_height = min(vp_h, model_h * scale)

    try:
        layout.add_viewport(
            center=(vp_cx, vp_cy, 0),
            size=(vp_width, vp_height),
            view_center_point=(model_cx, model_cy, 0),
            view_height=model_h,
        )
    except Exception:
        pass  # Viewport provisioning is best-effort

    if diag is not None:
        diag.paper_space_built(cfg.preset_name, size_key, cfg.orientation, viewport_scale_str)


"""
presets.py — Curated Safe Preset Profiles & AdvancedOptions Engine for cad-ir-to-dxf.

Defines the 5 production-ready DXF preset profiles (standard, cnc_cam, arch_print,
web_lightweight, bim_overlay), a strongly-typed AdvancedOptions model for granular
overrides, and the get_preset_config() resolver that merges them into a final
ResolvedConfig that drives compilation.

Architecture
------------
    1 IR  ─►  preset (+ optional AdvancedOptions)  ─►  ResolvedConfig  ─►  DXF

Relationship:  One IR  →  Many DXF Presets (user chooses; defaults to "standard")
"""

from __future__ import annotations

import copy
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Literal, Optional, Union


# ──────────────────────────────────────────────────────────────────────────────
# 1. Preset Name Enum
# ──────────────────────────────────────────────────────────────────────────────

class PresetName(str, Enum):
    """Named safe preset profiles for DXF compilation output."""

    STANDARD         = "standard"
    CNC_CAM          = "cnc_cam"
    ARCH_PRINT       = "arch_print"
    WEB_LIGHTWEIGHT  = "web_lightweight"
    BIM_OVERLAY      = "bim_overlay"


# ──────────────────────────────────────────────────────────────────────────────
# 2. Advanced Options (Granular Override Structure)
# ──────────────────────────────────────────────────────────────────────────────

@dataclass
class VersionOptions:
    """DXF version and encoding settings."""
    dxf_version: str = "R2013"   # R12, R2000, R2004, R2007, R2010, R2013, R2018
    encoding: str = "UTF-8"      # UTF-8 or ASCII


@dataclass
class GeometryOptions:
    """Controls geometric output and sanitization behaviour."""
    flatten_z: bool = False
    """Force all entity Z coordinates to 0.0 (required for CNC/CAM)."""

    explode_blocks: bool = False
    """Expand INSERT block references into raw loose primitives in model space."""

    curve_strategy: str = "native"
    """
    'native'              → ARC/CIRCLE/ELLIPSE/SPLINE kept as analytic vectors (default).
    'tessellated'         → All curves approximated as straight-segment LWPOLYLINE.
    'splines_to_polylines'→ SPLINE only is converted to polyline arcs; ARCs stay native.
    """

    tessellation_distance: float = 2.0
    """Max chord deviation in drawing units when tessellating curves. Lower = smoother."""

    zero_length_tolerance: float = 1e-9
    """Entities shorter/smaller than this are dropped as degenerate."""


@dataclass
class FilteringOptions:
    """Layer and entity type inclusion/exclusion filters."""
    include_layers: List[str] = field(default_factory=list)
    """If non-empty, only these layer names (exact match) are exported."""

    exclude_layers: List[str] = field(default_factory=list)
    """Layer names to suppress; supports '*' suffix wildcard (e.g. 'TEMP*')."""

    include_annotations: bool = True
    """If False, all TEXT and MTEXT annotation entities are omitted."""

    include_dimensions: bool = True
    """If False, all DIMENSION entities (written as MTEXT labels) are omitted."""


@dataclass
class LayoutOptions:
    """Paper Space layout, sheet size, viewport scale, and margin settings."""
    create_paper_space: bool = False
    """If True, provisions a PaperSpace layout tab with a border and scaled viewport."""

    paper_size: str = "ISO_A3"
    """Target sheet size: ISO_A4, ISO_A3, ISO_A1, ISO_A0, ANSI_A, ANSI_D, ARCH_D."""

    orientation: str = "landscape"
    """Sheet rotation: 'landscape' or 'portrait'."""

    viewport_scale: str = "auto"
    """
    Viewport camera scale ratio applied in Paper Space.
    'auto' fits the drawing to the sheet. Or a ratio string: '1:50', '1:100', '1:1'.
    """

    margin_mm: float = 10.0
    """Printable margin from sheet edge (mm) and width of the border rectangle."""


@dataclass
class StylingOptions:
    """Color mode and layer naming options."""
    color_mode: str = "truecolor"
    """
    'truecolor'   → Full 24-bit RGB entity colors preserved (default).
    'aci'         → Standard AutoCAD 256-color palette (ACI) only.
    'monochrome'  → All entities written as ACI color 7 (black/white).
    """

    layer_prefix: str = ""
    """Prefix prepended to all layer names (e.g. 'IR_' for BIM underlay import)."""


@dataclass
class AdvancedOptions:
    """
    Strongly-typed advanced options object for granular DXF compilation control.

    Any field left at its default will be inherited from the chosen preset.
    Supply only the knobs you want to change; the rest stay as the preset defines.
    """
    version:    Optional[VersionOptions]    = None
    geometry:   Optional[GeometryOptions]   = None
    filtering:  Optional[FilteringOptions]  = None
    layout:     Optional[LayoutOptions]     = None
    styling:    Optional[StylingOptions]    = None

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "AdvancedOptions":
        """Build AdvancedOptions from a plain dict (e.g. loaded from JSON)."""
        opts = cls()
        if "version" in d:
            opts.version = VersionOptions(**{
                k: v for k, v in d["version"].items()
                if k in VersionOptions.__dataclass_fields__
            })
        if "geometry" in d:
            opts.geometry = GeometryOptions(**{
                k: v for k, v in d["geometry"].items()
                if k in GeometryOptions.__dataclass_fields__
            })
        if "filtering" in d:
            opts.filtering = FilteringOptions(**{
                k: v for k, v in d["filtering"].items()
                if k in FilteringOptions.__dataclass_fields__
            })
        if "layout" in d:
            opts.layout = LayoutOptions(**{
                k: v for k, v in d["layout"].items()
                if k in LayoutOptions.__dataclass_fields__
            })
        if "styling" in d:
            opts.styling = StylingOptions(**{
                k: v for k, v in d["styling"].items()
                if k in StylingOptions.__dataclass_fields__
            })
        return opts


# ──────────────────────────────────────────────────────────────────────────────
# 3. Resolved Configuration (the final merged object passed to the compiler)
# ──────────────────────────────────────────────────────────────────────────────

@dataclass
class ResolvedConfig:
    """
    The final, merged compilation configuration passed to the DXF compiler.
    Produced by get_preset_config(). All fields are concrete values — no optionals.
    """
    preset_name: str

    # Version
    dxf_version: str
    encoding: str

    # Geometry
    flatten_z: bool
    explode_blocks: bool
    curve_strategy: str
    tessellation_distance: float
    zero_length_tolerance: float

    # Filtering
    include_layers: List[str]
    exclude_layers: List[str]
    include_annotations: bool
    include_dimensions: bool

    # Layout
    create_paper_space: bool
    paper_size: str
    orientation: str
    viewport_scale: str
    margin_mm: float

    # Styling
    color_mode: str
    layer_prefix: str


# ──────────────────────────────────────────────────────────────────────────────
# 4. The 5 Preset Profile Definitions (baseline configurations)
# ──────────────────────────────────────────────────────────────────────────────

_PRESET_BASES: Dict[str, Dict[str, Any]] = {

    PresetName.STANDARD: {
        # Who:   Architects/engineers using AutoCAD 2013-2026, Revit, Rhino, Fusion 360.
        # Why:   Highest fidelity, most compact, universally compatible modern format.
        "dxf_version":          "R2013",
        "encoding":             "UTF-8",
        "flatten_z":            False,
        "explode_blocks":       False,
        "curve_strategy":       "native",
        "tessellation_distance":2.0,
        "zero_length_tolerance":1e-9,
        "include_layers":       [],
        "exclude_layers":       [],
        "include_annotations":  True,
        "include_dimensions":   True,
        "create_paper_space":   False,
        "paper_size":           "ISO_A3",
        "orientation":          "landscape",
        "viewport_scale":       "auto",
        "margin_mm":            10.0,
        "color_mode":           "truecolor",
        "layer_prefix":         "",
    },

    PresetName.CNC_CAM: {
        # Who:   Laser cutters, waterjet, plasma, CNC routers (Mach3, LinuxCNC, CAM).
        # Why:   CNC controllers crash on modern entities, 3D coords, and SPLINE math.
        #        This produces clean, 2D, flat, cuttable vector toolpaths.
        "dxf_version":          "R12",
        "encoding":             "ASCII",
        "flatten_z":            True,
        "explode_blocks":       True,
        "curve_strategy":       "splines_to_polylines",
        "tessellation_distance":1.0,
        "zero_length_tolerance":1e-6,
        "include_layers":       [],
        "exclude_layers":       [],
        "include_annotations":  False,   # Drop text labels — laser would cut letters
        "include_dimensions":   False,   # Drop dimension labels
        "create_paper_space":   False,
        "paper_size":           "ISO_A3",
        "orientation":          "landscape",
        "viewport_scale":       "1:1",
        "margin_mm":            0.0,
        "color_mode":           "aci",   # Standard 256-color ACI for CAM tool assignment
        "layer_prefix":         "",
    },

    PresetName.ARCH_PRINT: {
        # Who:   Anyone who wants to open and immediately print a client-ready drawing.
        # Why:   Provisions Paper Space layout with auto-scaled viewport and border.
        "dxf_version":          "R2013",
        "encoding":             "UTF-8",
        "flatten_z":            False,
        "explode_blocks":       False,
        "curve_strategy":       "native",
        "tessellation_distance":2.0,
        "zero_length_tolerance":1e-9,
        "include_layers":       [],
        "exclude_layers":       [],
        "include_annotations":  True,
        "include_dimensions":   True,
        "create_paper_space":   True,    # Auto-generates PaperSpace layout + viewport
        "paper_size":           "ISO_A3",
        "orientation":          "landscape",
        "viewport_scale":       "auto",
        "margin_mm":            10.0,
        "color_mode":           "truecolor",
        "layer_prefix":         "",
    },

    PresetName.WEB_LIGHTWEIGHT: {
        # Who:   Web viewers, three.js, mobile CAD apps, GIS, database preview renderers.
        # Why:   Strips all non-essential metadata; coordinates rounded to 4dp for ~60%
        #        file size reduction while preserving full visual fidelity at screen res.
        "dxf_version":          "R2000",
        "encoding":             "UTF-8",
        "flatten_z":            True,
        "explode_blocks":       False,
        "curve_strategy":       "native",
        "tessellation_distance":2.0,
        "zero_length_tolerance":1e-6,
        "include_layers":       [],
        "exclude_layers":       ["DEFPOINTS"],
        "include_annotations":  True,
        "include_dimensions":   False,   # Dimensions are rarely meaningful in web renderers
        "create_paper_space":   False,
        "paper_size":           "ISO_A3",
        "orientation":          "landscape",
        "viewport_scale":       "auto",
        "margin_mm":            0.0,
        "color_mode":           "aci",   # ACI is far more compact than 24-bit TrueColor
        "layer_prefix":         "",
    },

    PresetName.BIM_OVERLAY: {
        # Who:   Revit, Navisworks, Civil 3D users importing 2D underlays.
        # Why:   Strict world-coordinate origin preservation prevents misalignment in BIM.
        #        Layer prefix avoids name collisions with existing Revit project layers.
        "dxf_version":          "R2018",
        "encoding":             "UTF-8",
        "flatten_z":            True,
        "explode_blocks":       False,
        "curve_strategy":       "native",
        "tessellation_distance":2.0,
        "zero_length_tolerance":1e-9,
        "include_layers":       [],
        "exclude_layers":       ["DEFPOINTS"],
        "include_annotations":  True,
        "include_dimensions":   True,
        "create_paper_space":   False,
        "paper_size":           "ISO_A3",
        "orientation":          "landscape",
        "viewport_scale":       "auto",
        "margin_mm":            0.0,
        "color_mode":           "aci",
        "layer_prefix":         "IR_",   # Prevents BIM layer name collisions
    },
}


# ──────────────────────────────────────────────────────────────────────────────
# 5. Public Resolver: get_preset_config()
# ──────────────────────────────────────────────────────────────────────────────

_PRESET_KEY_TYPE = Union[str, PresetName]


def get_preset_config(
    preset: _PRESET_KEY_TYPE = PresetName.STANDARD,
    overrides: Optional[Union[AdvancedOptions, Dict[str, Any]]] = None,
) -> ResolvedConfig:
    """
    Resolve the final compilation configuration by merging a named preset baseline
    with any user-supplied AdvancedOptions overrides.

    Parameters
    ----------
    preset:
        The preset name to base the configuration on.
        Accepts PresetName enum or string ('standard', 'cnc_cam', 'arch_print',
        'web_lightweight', 'bim_overlay').  Default: 'standard'.
    overrides:
        Optional AdvancedOptions dataclass instance OR a plain dict with override
        keys (same structure as the AdvancedOptions sub-groups).
        Only fields explicitly provided override the preset defaults.

    Returns
    -------
    ResolvedConfig
        Fully resolved, concrete configuration object with no optional fields.

    Raises
    ------
    ValueError
        If an unrecognized preset name is provided.

    Examples
    --------
    >>> # Simple: use cnc_cam preset as-is
    >>> cfg = get_preset_config("cnc_cam")

    >>> # Advanced: standard preset but with A1 paper and monochrome
    >>> from cad_ir_to_dxf.presets import AdvancedOptions, LayoutOptions, StylingOptions
    >>> cfg = get_preset_config("arch_print", AdvancedOptions(
    ...     layout=LayoutOptions(paper_size="ISO_A1"),
    ...     styling=StylingOptions(color_mode="monochrome"),
    ... ))
    """
    # Normalize the preset key
    if isinstance(preset, str):
        try:
            preset_key = PresetName(preset.lower())
        except ValueError:
            valid = [p.value for p in PresetName]
            raise ValueError(
                f"Unknown preset '{preset}'. Valid presets: {valid}"
            )
    else:
        preset_key = preset

    # Deep-copy the base so we never mutate the constant
    base = copy.deepcopy(_PRESET_BASES[preset_key])

    # Normalize overrides to AdvancedOptions dataclass
    if overrides is not None:
        if isinstance(overrides, dict):
            overrides = AdvancedOptions.from_dict(overrides)

        # Merge each sub-group only when the user explicitly supplied that group
        if overrides.version is not None:
            v = overrides.version
            if v.dxf_version is not None:
                base["dxf_version"] = v.dxf_version
            if v.encoding is not None:
                base["encoding"] = v.encoding

        if overrides.geometry is not None:
            g = overrides.geometry
            for attr in ("flatten_z", "explode_blocks", "curve_strategy",
                         "tessellation_distance", "zero_length_tolerance"):
                val = getattr(g, attr, None)
                if val is not None:
                    base[attr] = val

        if overrides.filtering is not None:
            f = overrides.filtering
            if f.include_layers is not None:
                base["include_layers"] = list(f.include_layers)
            if f.exclude_layers is not None:
                base["exclude_layers"] = list(f.exclude_layers)
            base["include_annotations"] = f.include_annotations
            base["include_dimensions"]  = f.include_dimensions

        if overrides.layout is not None:
            lo = overrides.layout
            base["create_paper_space"] = lo.create_paper_space
            if lo.paper_size is not None:
                base["paper_size"] = lo.paper_size
            if lo.orientation is not None:
                base["orientation"] = lo.orientation
            if lo.viewport_scale is not None:
                base["viewport_scale"] = lo.viewport_scale
            if lo.margin_mm is not None:
                base["margin_mm"] = lo.margin_mm

        if overrides.styling is not None:
            s = overrides.styling
            if s.color_mode is not None:
                base["color_mode"] = s.color_mode
            if s.layer_prefix is not None:
                base["layer_prefix"] = s.layer_prefix

    return ResolvedConfig(preset_name=preset_key.value, **base)


# ──────────────────────────────────────────────────────────────────────────────
# 6. Paper Size Registry (used by the compiler's arch_print layout builder)
# ──────────────────────────────────────────────────────────────────────────────

#  (width_mm, height_mm) in landscape orientation
PAPER_SIZES_MM: Dict[str, tuple] = {
    "ISO_A4":   (297.0,  210.0),
    "ISO_A3":   (420.0,  297.0),
    "ISO_A2":   (594.0,  420.0),
    "ISO_A1":   (841.0,  594.0),
    "ISO_A0":   (1189.0, 841.0),
    "ANSI_A":   (279.4,  215.9),   # US Letter landscape
    "ANSI_B":   (431.8,  279.4),   # US Tabloid landscape
    "ANSI_D":   (863.6,  558.8),   # 34" x 22"
    "ARCH_D":   (914.4,  609.6),   # 36" x 24"
    "ARCH_E":   (1219.2, 914.4),   # 48" x 36"
}

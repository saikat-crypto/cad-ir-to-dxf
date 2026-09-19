"""
cad-ir-to-dxf

Compiles the La Vinci CAD Intermediate Representation (LAVINCI_CAD_IR_V3)
into industry-standard DXF files via curated preset profiles.

Presets: standard (default), cnc_cam, arch_print, web_lightweight, bim_overlay.
Part of the La Vinci engineering initiative.
"""

from .compiler import compile_ir_to_dxf
from .presets import (
    PresetName,
    AdvancedOptions,
    VersionOptions,
    GeometryOptions,
    FilteringOptions,
    LayoutOptions,
    StylingOptions,
    ResolvedConfig,
    get_preset_config,
)
from .exceptions import (
    CadIrToDxfError,
    InvalidIRPayloadError,
    MissingFormatHeaderError,
    IRFileNotFoundError,
    IRParseError,
    InvalidPresetError,
    InvalidOptionError,
    InvalidPaperSizeError,
    InvalidColorModeError,
    InvalidDxfVersionError,
    InvalidOrientationError,
    InvalidViewportScaleError,
    CompilationError,
    BlockCycleError,
    OutputWriteError,
    StrictModeViolationError,
)
from .diagnostics import (
    CompilationDiagnostics,
    DiagnosticEntry,
    Severity,
)

__all__ = [
    "compile_ir_to_dxf",
    "PresetName",
    "AdvancedOptions",
    "VersionOptions",
    "GeometryOptions",
    "FilteringOptions",
    "LayoutOptions",
    "StylingOptions",
    "ResolvedConfig",
    "get_preset_config",
    # Exceptions
    "CadIrToDxfError",
    "InvalidIRPayloadError",
    "MissingFormatHeaderError",
    "IRFileNotFoundError",
    "IRParseError",
    "InvalidPresetError",
    "InvalidOptionError",
    "InvalidPaperSizeError",
    "InvalidColorModeError",
    "InvalidDxfVersionError",
    "InvalidOrientationError",
    "InvalidViewportScaleError",
    "CompilationError",
    "BlockCycleError",
    "OutputWriteError",
    "StrictModeViolationError",
    # Diagnostics
    "CompilationDiagnostics",
    "DiagnosticEntry",
    "Severity",
]
__version__ = "1.2.0"


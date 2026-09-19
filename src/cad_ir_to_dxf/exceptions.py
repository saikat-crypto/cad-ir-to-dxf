"""
exceptions.py — Custom Exception Hierarchy for cad-ir-to-dxf.

All exceptions raised by the compiler, preset resolver, and CLI are
subclasses of CadIrToDxfError. Each exception includes:

  - A concise, human-readable error message.
  - An actionable 'hint' explaining exactly how to fix it.
  - An 'offender' field naming the specific file, key, entity, or value
    that caused the problem.

Usage
-----
    try:
        doc = compile_ir_to_dxf(payload, preset="my_preset")
    except InvalidPresetError as e:
        print(e)            # Full formatted message with hint
        print(e.hint)       # Just the fix suggestion
        print(e.offender)   # The bad value ('my_preset')

Exception Hierarchy
-------------------
    CadIrToDxfError                         Base for all library errors
    ├── InvalidIRPayloadError                IR schema/format problems
    │   ├── MissingFormatHeaderError         'format' key absent or wrong
    │   └── IRFileNotFoundError              File path does not exist
    ├── InvalidPresetError                   Unknown preset name
    ├── InvalidOptionError                   Bad AdvancedOptions value
    │   ├── InvalidPaperSizeError            Unknown paper_size string
    │   ├── InvalidColorModeError            Unknown color_mode string
    │   └── InvalidDxfVersionError           Unsupported DXF version string
    ├── CompilationError                     Error during DXF generation
    │   ├── BlockCycleError                  Circular block reference
    │   └── OutputWriteError                 Cannot save DXF file to disk
    └── StrictModeViolationError             Non-fatal issue raised in strict mode
"""

from __future__ import annotations

from typing import Any, List, Optional


# ──────────────────────────────────────────────────────────────────────────────
# Base Exception
# ──────────────────────────────────────────────────────────────────────────────

class CadIrToDxfError(Exception):
    """
    Base exception for all cad-ir-to-dxf errors.

    All exceptions carry:
      message  — What went wrong (human-readable, one sentence).
      hint     — How to fix it (actionable suggestion).
      offender — The specific value, key, or entity that caused the fault.
    """

    def __init__(
        self,
        message: str,
        hint: str = "",
        offender: Optional[Any] = None,
    ) -> None:
        self.message = message
        self.hint = hint
        self.offender = offender
        full = f"\n[cad-ir-to-dxf] {message}"
        if offender is not None:
            full += f"\n  Offender  : {offender!r}"
        if hint:
            full += f"\n  Fix       : {hint}"
        super().__init__(full)


# ──────────────────────────────────────────────────────────────────────────────
# IR Payload Errors
# ──────────────────────────────────────────────────────────────────────────────

class InvalidIRPayloadError(CadIrToDxfError):
    """Raised when the IR payload cannot be parsed or does not conform to the schema."""


class MissingFormatHeaderError(InvalidIRPayloadError):
    """
    Raised when the IR payload is missing the required 'format' key
    or declares an unsupported schema version.

    Example
    -------
    >>> compile_ir_to_dxf({"layers": []})
    MissingFormatHeaderError: IR payload is missing the required 'format' header.
      Offender  : {}
      Fix       : Ensure the IR contains {"format": "LAVINCI_CAD_IR_V3", ...}.
    """

    def __init__(self, payload_preview: Any = None) -> None:
        super().__init__(
            message="IR payload is missing the required 'format' header or declares an unsupported schema version.",
            hint=(
                "Ensure the IR payload contains {\"format\": \"LAVINCI_CAD_IR_V3\", ...}. "
                "If you are using an older La Vinci IR v1/v2 schema, upgrade by re-extracting "
                "with the latest cad-extractor-ir version."
            ),
            offender=payload_preview,
        )


class IRFileNotFoundError(InvalidIRPayloadError):
    """
    Raised when a file path is passed to compile_ir_to_dxf but the file does not exist.

    Example
    -------
    >>> compile_ir_to_dxf("missing_plan.json")
    IRFileNotFoundError: IR source file not found: 'missing_plan.json'
      Fix: Check that the file path exists and the process has read permission.
    """

    def __init__(self, path: str) -> None:
        super().__init__(
            message=f"IR source file not found: {path!r}",
            hint=(
                "Check that the file path is correct and that the running process "
                "has read permission on that directory. Absolute paths are safer than relative ones."
            ),
            offender=path,
        )


class IRParseError(InvalidIRPayloadError):
    """
    Raised when the IR source string or file cannot be parsed as valid JSON.

    Example
    -------
    IRParseError: Failed to parse IR source as JSON.
      Offender  : '{format: LAVINCI_CAD_IR_V3}'  ← (missing quotes around key)
      Fix       : Validate the JSON with a linter such as `python -m json.tool plan.json`.
    """

    def __init__(self, raw: str, json_error: str) -> None:
        preview = raw[:120] + "..." if len(raw) > 120 else raw
        super().__init__(
            message=f"Failed to parse IR source as JSON. JSON error: {json_error}",
            hint=(
                "Validate the JSON with a linter: `python -m json.tool <your_file.json>`. "
                "Common causes: trailing commas, unquoted keys, single quotes instead of double quotes."
            ),
            offender=preview,
        )


# ──────────────────────────────────────────────────────────────────────────────
# Preset & Option Errors
# ──────────────────────────────────────────────────────────────────────────────

class InvalidPresetError(CadIrToDxfError):
    """
    Raised when an unrecognised preset name is passed.

    Example
    -------
    >>> compile_ir_to_dxf(ir, preset="laser_cutter")
    InvalidPresetError: Unknown preset 'laser_cutter'.
      Fix: Valid presets are: ['standard', 'cnc_cam', 'arch_print', 'web_lightweight', 'bim_overlay']
    """

    _VALID = ["standard", "cnc_cam", "arch_print", "web_lightweight", "bim_overlay"]

    def __init__(self, preset_name: str) -> None:
        import difflib
        close = difflib.get_close_matches(preset_name, self._VALID, n=1, cutoff=0.4)
        did_you_mean = f" Did you mean '{close[0]}'?" if close else ""
        super().__init__(
            message=f"Unknown preset '{preset_name}'.{did_you_mean}",
            hint=f"Valid presets are: {self._VALID}",
            offender=preset_name,
        )


class InvalidOptionError(CadIrToDxfError):
    """Raised when an AdvancedOptions value is outside the allowed range or set."""


class InvalidPaperSizeError(InvalidOptionError):
    """
    Raised when paper_size is set to an unsupported string.

    Example
    -------
    InvalidPaperSizeError: Unknown paper size 'US_LETTER'.
      Fix: Supported paper sizes: ['ISO_A4', 'ISO_A3', 'ISO_A2', 'ISO_A1', 'ISO_A0', ...]
    """

    def __init__(self, paper_size: str, valid: List[str]) -> None:
        super().__init__(
            message=f"Unknown paper size '{paper_size}'.",
            hint=f"Supported paper sizes are: {sorted(valid)}",
            offender=paper_size,
        )


class InvalidColorModeError(InvalidOptionError):
    """
    Raised when color_mode is set to an unsupported string.

    Example
    -------
    InvalidColorModeError: Unknown color mode 'rgb256'.
      Fix: Supported modes: ['truecolor', 'aci', 'monochrome']
    """

    _VALID = ["truecolor", "aci", "monochrome"]

    def __init__(self, color_mode: str) -> None:
        super().__init__(
            message=f"Unknown color mode '{color_mode}'.",
            hint=f"Supported color modes are: {self._VALID}",
            offender=color_mode,
        )


class InvalidDxfVersionError(InvalidOptionError):
    """
    Raised when an unsupported DXF version string is passed.

    Example
    -------
    InvalidDxfVersionError: Unsupported DXF version 'R2025'.
      Fix: Supported versions: ['R12', 'R2000', 'R2004', 'R2007', 'R2010', 'R2013', 'R2018']
    """

    _VALID = ["R12", "R2000", "R2004", "R2007", "R2010", "R2013", "R2018"]

    def __init__(self, version: str) -> None:
        super().__init__(
            message=f"Unsupported DXF version '{version}'.",
            hint=f"Supported DXF versions are: {self._VALID}",
            offender=version,
        )


class InvalidOrientationError(InvalidOptionError):
    """Raised when orientation is not 'landscape' or 'portrait'."""

    def __init__(self, orientation: str) -> None:
        super().__init__(
            message=f"Unknown orientation '{orientation}'.",
            hint="Orientation must be 'landscape' or 'portrait'.",
            offender=orientation,
        )


class InvalidViewportScaleError(InvalidOptionError):
    """
    Raised when viewport_scale is neither 'auto' nor a valid ratio string.

    Example
    -------
    InvalidViewportScaleError: Invalid viewport_scale '1-50'.
      Fix: Use 'auto', or a ratio string like '1:50', '1:100', '1:1'.
    """

    def __init__(self, scale: str) -> None:
        super().__init__(
            message=f"Invalid viewport_scale '{scale}'.",
            hint=(
                "Use 'auto' to auto-fit the drawing to the sheet, "
                "or a ratio string like '1:50', '1:100', '1:200', '1:1'."
            ),
            offender=scale,
        )


# ──────────────────────────────────────────────────────────────────────────────
# Compilation Errors
# ──────────────────────────────────────────────────────────────────────────────

class CompilationError(CadIrToDxfError):
    """Raised when DXF generation fails for a structural or data reason."""


class BlockCycleError(CompilationError):
    """
    Raised when circular block references are detected and would cause an
    infinite recursion loop during compilation.

    Example
    -------
    BlockCycleError: Circular block reference detected: DESK → CHAIR → DESK
      Fix: Remove the circular reference from the block_definitions table in your IR.
    """

    def __init__(self, cycle_nodes: List[str]) -> None:
        cycle_str = " → ".join(sorted(cycle_nodes))
        super().__init__(
            message=f"Circular block reference detected involving: {cycle_str}.",
            hint=(
                "Remove the circular reference from the 'block_definitions' table in your IR. "
                "A block cannot directly or indirectly insert itself as a component."
            ),
            offender=sorted(cycle_nodes),
        )


class OutputWriteError(CompilationError):
    """
    Raised when the compiler cannot write the DXF to the specified output path.

    Example
    -------
    OutputWriteError: Cannot write DXF to '/read-only/output.dxf'.
      Fix: Check that the directory exists and the process has write permission.
    """

    def __init__(self, path: str, os_error: str) -> None:
        super().__init__(
            message=f"Cannot write DXF output to '{path}'. OS error: {os_error}",
            hint=(
                "Check that the output directory exists and the process has write permission. "
                "Create parent directories first: os.makedirs(parent, exist_ok=True)."
            ),
            offender=path,
        )


# ──────────────────────────────────────────────────────────────────────────────
# Strict Mode
# ──────────────────────────────────────────────────────────────────────────────

class StrictModeViolationError(CadIrToDxfError):
    """
    Raised when strict=True is set and a non-fatal issue is encountered that
    would normally be silently corrected (e.g. auto-vivifying a missing layer,
    dropping a zero-length line, suppressing a text entity for cnc_cam).

    In non-strict (default) mode, these are collected as diagnostics only.

    Example
    -------
    StrictModeViolationError: [strict] Layer 'EQUIPMENT' was referenced but not
        declared in the IR layers table. In non-strict mode this would be auto-created.
      Fix: Add a layer entry for 'EQUIPMENT' to the 'layers' list in your IR payload.
    """

    def __init__(self, message: str, hint: str = "", offender: Any = None) -> None:
        super().__init__(
            message=f"[strict] {message}",
            hint=hint or "Fix the IR payload or set strict=False to allow auto-correction.",
            offender=offender,
        )

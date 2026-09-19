"""
test_errors_and_diagnostics.py — Unit tests for Exceptions and Compilation Diagnostics.

Tests:
  - Custom typed exceptions with actionable hints and offender attributes.
  - IRFileNotFoundError, IRParseError, MissingFormatHeaderError.
  - InvalidPresetError (with "Did you mean?" suggestion).
  - InvalidOptionError subclasses (paper size, color mode, DXF version, orientation, scale).
  - StrictModeViolationError on undeclared layers, missing blocks, degenerate geometry.
  - CompilationDiagnostics collection, categories, print_report, and to_dict.
"""

import io
import json
import os
import tempfile
import unittest

from cad_ir_to_dxf import (
    compile_ir_to_dxf,
    CompilationDiagnostics,
    Severity,
    CadIrToDxfError,
    IRFileNotFoundError,
    IRParseError,
    MissingFormatHeaderError,
    InvalidPresetError,
    InvalidPaperSizeError,
    InvalidColorModeError,
    InvalidDxfVersionError,
    InvalidOrientationError,
    InvalidViewportScaleError,
    OutputWriteError,
    StrictModeViolationError,
    BlockCycleError,
    AdvancedOptions,
    LayoutOptions,
    StylingOptions,
    VersionOptions,
)


def _valid_ir() -> dict:
    return {
        "format": "LAVINCI_CAD_IR_V3",
        "metadata": {"source_file": "test.dwg"},
        "layers": [{"name": "0", "color_aci": 7}],
        "geometry_primitives": {
            "primitives": {
                "lines": [{"start": [0, 0, 0], "end": [10, 10, 0], "layer": "0"}],
            }
        },
    }


class TestExceptions(unittest.TestCase):

    def test_missing_file_raises_typed_exception(self):
        with self.assertRaises(IRFileNotFoundError) as ctx:
            compile_ir_to_dxf("non_existent_blueprint_plan.json")
        err = ctx.exception
        self.assertIn("non_existent_blueprint_plan.json", str(err))
        self.assertTrue(err.hint)
        self.assertEqual(err.offender, "non_existent_blueprint_plan.json")

    def test_invalid_json_raises_ir_parse_error(self):
        with self.assertRaises(IRParseError) as ctx:
            compile_ir_to_dxf("{format: invalid_json_syntax}")
        err = ctx.exception
        self.assertIn("Failed to parse IR source as JSON", str(err))
        self.assertTrue(err.hint)

    def test_missing_format_header_in_strict_mode(self):
        ir = {"layers": []}
        with self.assertRaises(MissingFormatHeaderError) as ctx:
            compile_ir_to_dxf(ir, strict=True)
        err = ctx.exception
        self.assertIn("missing the required 'format' header", str(err))
        self.assertTrue(err.hint)

    def test_unknown_preset_with_suggestion(self):
        with self.assertRaises(InvalidPresetError) as ctx:
            compile_ir_to_dxf(_valid_ir(), preset="cnc-cam")
        err = ctx.exception
        self.assertIn("Did you mean 'cnc_cam'?", str(err))
        self.assertIn("cnc_cam", err.hint)
        self.assertEqual(err.offender, "cnc-cam")

    def test_invalid_dxf_version_raises(self):
        with self.assertRaises(InvalidDxfVersionError) as ctx:
            compile_ir_to_dxf(
                _valid_ir(),
                advanced_options={"version": {"dxf_version": "R2050"}},
            )
        err = ctx.exception
        self.assertIn("R2050", str(err))
        self.assertIn("R2013", err.hint)

    def test_invalid_color_mode_raises(self):
        with self.assertRaises(InvalidColorModeError) as ctx:
            compile_ir_to_dxf(
                _valid_ir(),
                advanced_options={"styling": {"color_mode": "cmyk"}},
            )
        err = ctx.exception
        self.assertIn("cmyk", str(err))
        self.assertIn("truecolor", err.hint)

    def test_invalid_paper_size_raises(self):
        with self.assertRaises(InvalidPaperSizeError) as ctx:
            compile_ir_to_dxf(
                _valid_ir(),
                preset="arch_print",
                advanced_options={"layout": {"paper_size": "SUPER_GIANT_POSTER"}},
            )
        err = ctx.exception
        self.assertIn("SUPER_GIANT_POSTER", str(err))
        self.assertIn("ISO_A3", err.hint)

    def test_invalid_orientation_raises(self):
        with self.assertRaises(InvalidOrientationError) as ctx:
            compile_ir_to_dxf(
                _valid_ir(),
                preset="arch_print",
                advanced_options={"layout": {"orientation": "diagonal"}},
            )
        err = ctx.exception
        self.assertIn("diagonal", str(err))
        self.assertIn("landscape", err.hint)

    def test_invalid_viewport_scale_raises(self):
        with self.assertRaises(InvalidViewportScaleError) as ctx:
            compile_ir_to_dxf(
                _valid_ir(),
                preset="arch_print",
                advanced_options={"layout": {"viewport_scale": "scale_1_to_50"}},
            )
        err = ctx.exception
        self.assertIn("scale_1_to_50", str(err))
        self.assertIn("1:50", err.hint)

    def test_output_write_error_on_bad_path(self):
        bad_path = "/non_existent_folder_abc123/out.dxf" if os.name != "nt" else "Z:\\non_existent_drive_987\\out.dxf"
        with self.assertRaises(OutputWriteError) as ctx:
            compile_ir_to_dxf(_valid_ir(), output_path=bad_path)
        err = ctx.exception
        self.assertTrue(err.hint)


class TestStrictMode(unittest.TestCase):

    def test_strict_mode_undeclared_layer_raises(self):
        ir = _valid_ir()
        ir["geometry_primitives"]["primitives"]["lines"].append(
            {"start": [0, 0, 0], "end": [5, 5, 0], "layer": "UNREGISTERED_LAYER"}
        )
        with self.assertRaises(StrictModeViolationError) as ctx:
            compile_ir_to_dxf(ir, strict=True)
        self.assertIn("UNREGISTERED_LAYER", str(ctx.exception))

    def test_strict_mode_undefined_block_raises(self):
        ir = _valid_ir()
        ir["components"] = [
            {"block_name": "PHANTOM_CHAIR", "position": [0, 0, 0], "layer": "0"}
        ]
        with self.assertRaises(StrictModeViolationError) as ctx:
            compile_ir_to_dxf(ir, strict=True)
        self.assertIn("PHANTOM_CHAIR", str(ctx.exception))

    def test_strict_mode_degenerate_line_raises(self):
        ir = _valid_ir()
        ir["geometry_primitives"]["primitives"]["lines"].append(
            {"start": [0, 0, 0], "end": [0, 0, 0], "layer": "0"}  # zero length
        )
        with self.assertRaises(StrictModeViolationError) as ctx:
            compile_ir_to_dxf(ir, strict=True)
        self.assertIn("Degenerate or invalid line", str(ctx.exception))


class TestDiagnostics(unittest.TestCase):

    def test_diagnostics_records_auto_created_layer(self):
        diag = CompilationDiagnostics()
        ir = _valid_ir()
        ir["geometry_primitives"]["primitives"]["lines"].append(
            {"start": [0, 0, 0], "end": [5, 5, 0], "layer": "NEW_LAYER"}
        )
        compile_ir_to_dxf(ir, diagnostics=diag)
        self.assertTrue(diag.has_warnings)
        entries = diag.by_category("layer_auto_created")
        self.assertEqual(len(entries), 1)
        self.assertEqual(entries[0].offender, "NEW_LAYER")
        self.assertTrue(entries[0].suggestion)

    def test_diagnostics_records_suppressed_annotations(self):
        diag = CompilationDiagnostics()
        ir = _valid_ir()
        ir["annotations"] = [
            {"type": "TEXT", "raw_text": "Room 101", "position": [0, 0], "layer": "0"},
            {"type": "TEXT", "raw_text": "Room 102", "position": [10, 0], "layer": "0"},
        ]
        compile_ir_to_dxf(ir, preset="cnc_cam", diagnostics=diag)
        entries = diag.by_category("entity_suppressed")
        self.assertEqual(len(entries), 1)
        self.assertIn("2 TEXT/MTEXT entities suppressed by preset 'cnc_cam'", entries[0].message)

    def test_diagnostics_records_paper_space_creation(self):
        diag = CompilationDiagnostics()
        compile_ir_to_dxf(_valid_ir(), preset="arch_print", diagnostics=diag)
        entries = diag.by_category("paper_space_created")
        self.assertEqual(len(entries), 1)
        self.assertIn("Presentation_Sheet", entries[0].message)

    def test_diagnostics_print_report_and_dict(self):
        diag = CompilationDiagnostics()
        ir = _valid_ir()
        ir["geometry_primitives"]["primitives"]["lines"].append(
            {"start": [0, 0, 0], "end": [5, 5, 0], "layer": "GHOST_LAYER"}
        )
        compile_ir_to_dxf(ir, diagnostics=diag)

        buf = io.StringIO()
        diag.print_report(file=buf)
        report_output = buf.getvalue()
        self.assertIn("Compilation Diagnostics Report", report_output)
        self.assertIn("GHOST_LAYER", report_output)

        d = diag.to_dict()
        self.assertGreater(d["total"], 0)
        self.assertEqual(len(d["entries"]), d["total"])
        self.assertEqual(d["entries"][0]["category"], "layer_auto_created")


if __name__ == "__main__":
    unittest.main()

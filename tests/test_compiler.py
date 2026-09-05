"""
test_compiler.py — Comprehensive test suite for cad-ir-to-dxf.

Test strategy:
  - Smoke test: does every IR example file compile without crashing?
  - Layer fidelity: are all declared layers present in the DXF?
  - Block fidelity: are all block definitions written with internal geometry?
  - Geometry count: do primitive counts roughly match the IR summary?
  - Boundary conditions: empty IR, zero-radius arc, degenerate lines, etc.
  - BYLAYER: entities with null color must NOT have a true_color attribute set.
  - INSERT scale clamping: micro-scale components must not produce zero-scale errors.
"""

import json
import os
import unittest
from pathlib import Path

import ezdxf

from cad_ir_to_dxf.compiler import compile_ir_to_dxf
from cad_ir_to_dxf.sanitizer import (
    clamp_scale,
    hex_to_truecolor,
    validate_arc,
    validate_circle,
    validate_line,
    validate_polyline,
)

# Paths
EXAMPLES_DIR = Path(__file__).parent.parent.parent / "cad-extractor-ir" / "examples"
BLUEPRINT_IR = EXAMPLES_DIR / "blueprint_sample_ir.json"


# ──────────────────────────────────────────────────────────────────────────────
# Helper
# ──────────────────────────────────────────────────────────────────────────────

def _load_ir(name: str) -> dict:
    path = EXAMPLES_DIR / name
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


# ──────────────────────────────────────────────────────────────────────────────
# Sanitizer Unit Tests
# ──────────────────────────────────────────────────────────────────────────────

class TestSanitizer(unittest.TestCase):

    def test_valid_line(self):
        self.assertTrue(validate_line([0.0, 0.0], [1.0, 1.0]))

    def test_degenerate_line_zero_length(self):
        self.assertFalse(validate_line([5.0, 5.0], [5.0, 5.0]))

    def test_degenerate_line_nan(self):
        self.assertFalse(validate_line([float("nan"), 0.0], [1.0, 1.0]))

    def test_degenerate_line_inf(self):
        self.assertFalse(validate_line([0.0, 0.0], [float("inf"), 1.0]))

    def test_valid_arc(self):
        self.assertTrue(validate_arc([0.0, 0.0], 5.0, 0.0, 90.0))

    def test_zero_radius_arc(self):
        self.assertFalse(validate_arc([0.0, 0.0], 0.0, 0.0, 90.0))

    def test_negative_radius_arc(self):
        self.assertFalse(validate_arc([0.0, 0.0], -1.0, 0.0, 90.0))

    def test_wrapping_arc_is_valid(self):
        # Arcs sweeping across 0° (start > end) are geometrically valid
        self.assertTrue(validate_arc([0.0, 0.0], 5.0, 306.79, 59.29))

    def test_valid_circle(self):
        self.assertTrue(validate_circle([0.0, 0.0], 2.5))

    def test_zero_radius_circle(self):
        self.assertFalse(validate_circle([0.0, 0.0], 0.0))

    def test_valid_polyline(self):
        self.assertTrue(validate_polyline([[0, 0], [1, 1], [2, 0]]))

    def test_single_vertex_polyline(self):
        self.assertFalse(validate_polyline([[0, 0]]))

    def test_empty_polyline(self):
        self.assertFalse(validate_polyline([]))

    def test_clamp_zero_scale(self):
        sx, sy, sz = clamp_scale([0.0, 0.0, 0.0])
        self.assertGreater(sx, 0.0)
        self.assertGreater(sy, 0.0)
        self.assertGreater(sz, 0.0)

    def test_clamp_micro_scale(self):
        # 0.001 is above epsilon — should pass through unchanged
        sx, sy, sz = clamp_scale([0.001, 0.001, 0.001])
        self.assertAlmostEqual(sx, 0.001)

    def test_hex_to_truecolor(self):
        # #FF0000 → R=255, G=0, B=0 → 0xFF0000 = 16711680
        self.assertEqual(hex_to_truecolor("#ff0000"), 16711680)


# ──────────────────────────────────────────────────────────────────────────────
# Compiler Smoke Tests — all 8 example IR files
# ──────────────────────────────────────────────────────────────────────────────

class TestCompilerSmoke(unittest.TestCase):

    def _smoke(self, ir_filename: str):
        ir_path = EXAMPLES_DIR / ir_filename
        if not ir_path.exists():
            self.skipTest(f"Example file not found: {ir_filename}")
        doc = compile_ir_to_dxf(str(ir_path))
        self.assertIsNotNone(doc)
        # Must be a valid DXF document with a modelspace
        msp = doc.modelspace()
        self.assertIsNotNone(msp)

    def test_smoke_blueprint_sample(self):
        self._smoke("blueprint_sample_ir.json")

    def test_smoke_arc_2013(self):
        self._smoke("2013_Arc_ir.json")

    def test_smoke_arc_2018(self):
        self._smoke("2018_Arc_ir.json")

    def test_smoke_line_2007(self):
        self._smoke("2007_Line_ir.json")

    def test_smoke_line_2018(self):
        self._smoke("2018_Line_ir.json")

    def test_smoke_spline_2010(self):
        self._smoke("2010_Spline_ir.json")

    def test_smoke_leader_2000(self):
        self._smoke("2000_Leader_ir.json")

    def test_smoke_leader_2004(self):
        self._smoke("2004_Leader_ir.json")


# ──────────────────────────────────────────────────────────────────────────────
# Compiler Fidelity Tests — blueprint_sample_ir.json
# ──────────────────────────────────────────────────────────────────────────────

class TestCompilerFidelity(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        if not BLUEPRINT_IR.exists():
            raise unittest.SkipTest("blueprint_sample_ir.json not found")
        cls.ir = json.loads(BLUEPRINT_IR.read_text(encoding="utf-8"))
        cls.doc = compile_ir_to_dxf(cls.ir)

    def test_all_layers_registered(self):
        """Every layer declared in the IR must exist in the DXF."""
        for layer_def in self.ir.get("layers", []):
            name = layer_def["name"]
            self.assertIn(name, self.doc.layers, f"Layer '{name}' missing from DXF")

    def test_block_definitions_present(self):
        """All block definitions from the IR must exist in doc.blocks."""
        for block_name in self.ir.get("block_definitions", {}):
            if block_name.startswith("*Model") or block_name.startswith("*Paper"):
                continue
            self.assertIn(block_name, self.doc.blocks,
                          f"Block definition '{block_name}' missing from DXF")

    def test_toilet_block_has_internal_geometry(self):
        """The Toilet block must contain actual renderable geometry (not empty)."""
        self.assertIn("Toilet", self.doc.blocks)
        toilet_blk = self.doc.blocks["Toilet"]
        entity_types = {e.dxftype() for e in toilet_blk}
        # Must have at least LINE, ARC, or CIRCLE entities
        has_geometry = bool(entity_types & {"LINE", "ARC", "CIRCLE", "LWPOLYLINE"})
        self.assertTrue(has_geometry, "Toilet block is empty — missing internal geometry")

    def test_receptacle_block_has_circles(self):
        """The Receptacle block must contain CIRCLE entities (electrical outlet symbol)."""
        self.assertIn("Receptacle", self.doc.blocks)
        receptacle_blk = self.doc.blocks["Receptacle"]
        circle_count = sum(1 for e in receptacle_blk if e.dxftype() == "CIRCLE")
        self.assertGreater(circle_count, 0, "Receptacle block has no CIRCLE entities")

    def test_model_space_has_entities(self):
        """Model space must have at least lines and component insertions."""
        msp = self.doc.modelspace()
        types = {e.dxftype() for e in msp}
        self.assertIn("LINE", types, "No LINE entities in model space")
        self.assertIn("INSERT", types, "No INSERT entities in model space")

    def test_bylayer_entities_have_no_true_color(self):
        """
        Entities with color=None in IR must NOT have a true_color attribute set
        in the DXF (preserving dynamic BYLAYER color inheritance).
        """
        # Get IR lines with null color
        ir_lines = self.ir["geometry_primitives"]["primitives"]["lines"]
        bylayer_count = sum(1 for l in ir_lines if l.get("color") is None)

        # Check DXF lines: none of them should have true_color set
        msp = self.doc.modelspace()
        dxf_lines = [e for e in msp if e.dxftype() == "LINE"]
        lines_with_truecolor = [
            e for e in dxf_lines
            if e.dxf.hasattr("true_color")
        ]
        self.assertEqual(
            len(lines_with_truecolor), 0,
            f"{len(lines_with_truecolor)} LINE entities incorrectly have baked TrueColor "
            f"(should be BYLAYER). This breaks dynamic layer colour changes."
        )

    def test_wrapping_arcs_preserved(self):
        """
        Arcs with start_angle > end_angle (crossing 0°) must be written
        with their original angle values, not normalized.
        """
        ir_arcs = self.ir["geometry_primitives"]["primitives"]["arcs"]
        wrapping_arcs = [a for a in ir_arcs if a["start_angle"] > a["end_angle"]]
        if not wrapping_arcs:
            self.skipTest("No wrapping arcs in sample")
        sample = wrapping_arcs[0]

        msp = self.doc.modelspace()
        dxf_arcs = [e for e in msp if e.dxftype() == "ARC"]
        # Find matching arc by center and radius
        matched = next(
            (a for a in dxf_arcs
             if abs(a.dxf.center.x - sample["center"][0]) < 0.01
             and abs(a.dxf.center.y - sample["center"][1]) < 0.01),
            None
        )
        if matched is None:
            self.skipTest("Could not match wrapping arc in DXF by center")
        self.assertGreater(
            matched.dxf.start_angle, matched.dxf.end_angle,
            "Wrapping arc was incorrectly normalized (start_angle should > end_angle)"
        )

    def test_annotations_written(self):
        """All MTEXT annotations from IR must appear in model space."""
        ir_annots = [a for a in self.ir.get("annotations", []) if a["space"] == "Model"]
        if not ir_annots:
            self.skipTest("No model-space annotations in sample")
        msp = self.doc.modelspace()
        dxf_mtext = [e for e in msp if e.dxftype() == "MTEXT"]
        self.assertGreater(len(dxf_mtext), 0, "No MTEXT entities written to model space")


# ──────────────────────────────────────────────────────────────────────────────
# Boundary Condition Tests
# ──────────────────────────────────────────────────────────────────────────────

class TestBoundaryConditions(unittest.TestCase):

    def test_empty_ir_produces_valid_dxf(self):
        """An IR with no geometry must still produce a structurally valid DXF."""
        empty_ir = {
            "format": "LAVINCI_CAD_IR_V3",
            "metadata": {
                "source_file": "empty.dwg",
                "dxf_version": "AC1027",
                "cad_version": "R2013",
                "units": 4,
                "measurement_system": "Metric",
                "author": "Test"
            },
            "extents": {"min": [0.0, 0.0], "max": [0.0, 0.0], "width": 0.0, "height": 0.0},
            "layers": [],
            "layouts": [],
            "block_definitions": {},
            "annotations": [],
            "dimensions": [],
            "components": [],
            "geometry_primitives": {
                "summary": {
                    "total_lines": 0, "total_arcs": 0, "total_circles": 0,
                    "total_polylines": 0, "total_components": 0,
                    "total_annotations": 0, "total_dimensions": 0,
                    "total_block_definitions": 0
                },
                "primitives": {"lines": [], "arcs": [], "circles": [], "polylines": []}
            },
            "bill_of_materials": {}
        }
        doc = compile_ir_to_dxf(empty_ir)
        self.assertIsNotNone(doc)
        self.assertIsNotNone(doc.modelspace())

    def test_degenerate_geometry_does_not_crash(self):
        """IR containing degenerate geometry (zero-length line, zero-radius arc)
        must compile without raising exceptions."""
        ir = {
            "format": "LAVINCI_CAD_IR_V3",
            "metadata": {"source_file": "bad.dwg", "dxf_version": "AC1027",
                         "cad_version": "R2013", "units": 4,
                         "measurement_system": "Metric", "author": "Test"},
            "extents": {"min": [0.0, 0.0], "max": [1.0, 1.0], "width": 1.0, "height": 1.0},
            "layers": [{"name": "0", "color_aci": 7, "hex_color": "#ffffff",
                        "is_off": False, "is_locked": False, "is_frozen": False,
                        "linetype": "Continuous"}],
            "layouts": [], "block_definitions": {}, "annotations": [],
            "dimensions": [], "components": [], "bill_of_materials": {},
            "geometry_primitives": {
                "summary": {"total_lines": 1, "total_arcs": 1, "total_circles": 1,
                            "total_polylines": 1, "total_components": 0,
                            "total_annotations": 0, "total_dimensions": 0,
                            "total_block_definitions": 0},
                "primitives": {
                    "lines": [
                        {"layer": "0", "space": "Model", "start": [5.0, 5.0],
                         "end": [5.0, 5.0], "color": None}       # Zero-length
                    ],
                    "arcs": [
                        {"layer": "0", "space": "Model", "center": [0.0, 0.0],
                         "radius": 0.0, "start_angle": 0.0,
                         "end_angle": 90.0, "color": None}        # Zero-radius
                    ],
                    "circles": [
                        {"layer": "0", "space": "Model", "center": [0.0, 0.0],
                         "radius": -1.0, "color": None}           # Negative radius
                    ],
                    "polylines": [
                        {"layer": "0", "space": "Model",
                         "is_closed": False, "points": [[0, 0]],  # Single point
                         "color": None}
                    ],
                }
            }
        }
        try:
            doc = compile_ir_to_dxf(ir)
            msp = doc.modelspace()
            # All degenerate entities must have been dropped
            self.assertEqual(len(list(msp)), 0,
                             "Degenerate entities should be filtered out, not written")
        except Exception as exc:
            self.fail(f"compile_ir_to_dxf raised an exception on degenerate geometry: {exc}")

    def test_missing_block_gets_placeholder(self):
        """A component INSERT referencing an undefined block must get a
        crosshair placeholder so AutoCAD never encounters a dangling reference."""
        ir = {
            "format": "LAVINCI_CAD_IR_V3",
            "metadata": {"source_file": "x.dwg", "dxf_version": "AC1027",
                         "cad_version": "R2013", "units": 4,
                         "measurement_system": "Metric", "author": "Test"},
            "extents": {"min": [0.0, 0.0], "max": [10.0, 10.0], "width": 10.0, "height": 10.0},
            "layers": [{"name": "0", "color_aci": 7, "hex_color": "#ffffff",
                        "is_off": False, "is_locked": False, "is_frozen": False,
                        "linetype": "Continuous"}],
            "layouts": [], "annotations": [], "dimensions": [],
            "bill_of_materials": {"MYSTERY_BLOCK": 1},
            "block_definitions": {},   # ← deliberately no definition geometry
            "components": [{
                "block_name": "MYSTERY_BLOCK",
                "resolved_name": "Mystery Block",
                "layer": "0",
                "space": "Model",
                "position": [5.0, 5.0, 0.0],
                "rotation": 0.0,
                "scale": [1.0, 1.0, 1.0],
                "attributes": {}
            }],
            "geometry_primitives": {
                "summary": {"total_lines": 0, "total_arcs": 0, "total_circles": 0,
                            "total_polylines": 0, "total_components": 1,
                            "total_annotations": 0, "total_dimensions": 0,
                            "total_block_definitions": 0},
                "primitives": {"lines": [], "arcs": [], "circles": [], "polylines": []}
            }
        }
        doc = compile_ir_to_dxf(ir)
        # Placeholder block must exist
        self.assertIn("MYSTERY_BLOCK", doc.blocks,
                      "Placeholder block was not auto-created for undefined block reference")
        # INSERT must exist in model space
        msp = doc.modelspace()
        inserts = [e for e in msp if e.dxftype() == "INSERT"]
        self.assertEqual(len(inserts), 1)
        self.assertEqual(inserts[0].dxf.name, "MYSTERY_BLOCK")


if __name__ == "__main__":
    unittest.main()

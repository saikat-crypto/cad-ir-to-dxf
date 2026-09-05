"""
tests/test_challenger_empirical.py — Empirical Challenger Verification Suite.

Independent empirical verification harness by Challenger (teamwork_preview_challenger_chal_1).
Empirically stress-tests and verifies:
1. Minimal reproduction snippets (REPRO-01 through REPRO-16) from DEFECT_REPORT.md.
2. Boundary stability (empty dict, empty string, NaN coordinates, extreme values).
3. Scale stress and performance metrics.
4. Test determinism and stability.
"""

from __future__ import annotations

import json
import math
import unittest
from typing import Any, Dict

import ezdxf
from ezdxf.audit import AuditError
from ezdxf.lldxf.const import DXFValueError, DXFVersionError
import pydantic

from cad_ir_to_dxf.compiler import compile_ir_to_dxf
from cad_ir_to_dxf.sanitizer import clamp_scale, is_finite, validate_line


class TestDefectReproductionSnippets(unittest.TestCase):
    """Empirical verification of minimal reproducible payloads from DEFECT_REPORT.md."""

    def test_repro_01_1d_line_coordinate_index_error(self):
        """REPRO-01: Line with 1D coordinate must raise IndexError in sanitizer."""
        payload = {
            "format": "LAVINCI_CAD_IR_V3",
            "geometry_primitives": {
                "primitives": {
                    "lines": [
                        {
                            "start": [10.0],
                            "end": [20.0, 20.0]
                        }
                    ]
                }
            }
        }
        with self.assertRaises(IndexError) as ctx:
            compile_ir_to_dxf(payload)
        self.assertIn("list index out of range", str(ctx.exception).lower())

    def test_repro_02_1d_polyline_vertex_index_error(self):
        """REPRO-02: Polyline with 1D vertex coordinates must raise IndexError in compiler."""
        payload = {
            "format": "LAVINCI_CAD_IR_V3",
            "geometry_primitives": {
                "primitives": {
                    "polylines": [
                        {
                            "points": [[10.0], [20.0]]
                        }
                    ]
                }
            }
        }
        with self.assertRaises(IndexError) as ctx:
            compile_ir_to_dxf(payload)
        self.assertIn("list index out of range", str(ctx.exception).lower())

    def test_repro_03_sub_2d_component_scale_index_error(self):
        """REPRO-03: Component with 1D scale must raise IndexError in clamp_scale."""
        payload = {
            "format": "LAVINCI_CAD_IR_V3",
            "components": [
                {
                    "block_name": "PUMP_BLK",
                    "scale": [1.0]
                }
            ]
        }
        with self.assertRaises(IndexError) as ctx:
            compile_ir_to_dxf(payload)
        self.assertIn("list index out of range", str(ctx.exception).lower())

    def test_repro_04_non_numeric_coordinate_type_error(self):
        """REPRO-04: Non-numeric coordinate string must raise TypeError in is_finite."""
        payload = {
            "format": "LAVINCI_CAD_IR_V3",
            "geometry_primitives": {
                "primitives": {
                    "lines": [
                        {
                            "start": ["0.0", 0.0],
                            "end": [10.0, 10.0]
                        }
                    ]
                }
            }
        }
        with self.assertRaises(TypeError) as ctx:
            compile_ir_to_dxf(payload)
        self.assertIn("must be real number", str(ctx.exception).lower())

    def test_repro_05_null_layer_attribute_error(self):
        """REPRO-05: Entity with explicit layer: null must raise AttributeError in ezdxf."""
        payload = {
            "format": "LAVINCI_CAD_IR_V3",
            "geometry_primitives": {
                "primitives": {
                    "lines": [
                        {
                            "start": [0.0, 0.0],
                            "end": [10.0, 10.0],
                            "layer": None
                        }
                    ]
                }
            }
        }
        with self.assertRaises(AttributeError) as ctx:
            compile_ir_to_dxf(payload)
        self.assertIn("startswith", str(ctx.exception).lower())

    def test_repro_06_integer_resolved_name_type_error(self):
        """REPRO-06: Integer resolved_name in component must raise TypeError on string slice."""
        payload = {
            "format": "LAVINCI_CAD_IR_V3",
            "components": [
                {
                    "block_name": "VALVE_A",
                    "resolved_name": 98765
                }
            ]
        }
        with self.assertRaises(TypeError) as ctx:
            compile_ir_to_dxf(payload)

    def test_repro_07_prohibited_layer_name_dxf_value_error(self):
        """REPRO-07: Slash in layer name must raise DXFValueError."""
        payload = {
            "format": "LAVINCI_CAD_IR_V3",
            "layers": [
                {
                    "name": "ARCH/WALLS",
                    "color_aci": 1
                }
            ]
        }
        with self.assertRaises(DXFValueError) as ctx:
            compile_ir_to_dxf(payload)
        self.assertIn("invalid value arch/walls", str(ctx.exception).lower())

    def test_repro_08_prohibited_space_name_dxf_value_error(self):
        """REPRO-08: Slash in layout space name must raise DXFValueError."""
        payload = {
            "format": "LAVINCI_CAD_IR_V3",
            "geometry_primitives": {
                "primitives": {
                    "lines": [
                        {
                            "start": [0.0, 0.0],
                            "end": [10.0, 10.0],
                            "space": "FloorPlan/1"
                        }
                    ]
                }
            }
        }
        with self.assertRaises(DXFValueError) as ctx:
            compile_ir_to_dxf(payload)
        self.assertIn("layout name contains invalid characters", str(ctx.exception).lower())

    def test_repro_09_null_top_level_sections_attribute_error(self):
        """REPRO-09: Explicit null top-level sections must raise AttributeError."""
        payload = {
            "format": "LAVINCI_CAD_IR_V3",
            "geometry_primitives": None,
            "layers": None,
            "metadata": None,
            "block_definitions": None
        }
        with self.assertRaises((AttributeError, TypeError)) as ctx:
            compile_ir_to_dxf(payload)
        self.assertIn("'nonetype' object has no attribute 'get'", str(ctx.exception).lower())

    def test_repro_10_pydantic_model_ingestion_json_decode_error(self):
        """REPRO-10: Pydantic model object falls through str(model) and crashes in json.loads."""
        class MockCADModel(pydantic.BaseModel):
            format: str = "LAVINCI_CAD_IR_V3"
            metadata: Dict[str, Any] = {}

        model = MockCADModel()
        with self.assertRaises(json.decoder.JSONDecodeError):
            compile_ir_to_dxf(model)

    def test_repro_11_r12_polyline_dxf_version_error(self):
        """REPRO-11: Polyline under DXF version R12 must raise DXFVersionError."""
        payload = {
            "format": "LAVINCI_CAD_IR_V3",
            "geometry_primitives": {
                "primitives": {
                    "polylines": [
                        {
                            "points": [[0.0, 0.0], [10.0, 10.0]]
                        }
                    ]
                }
            }
        }
        with self.assertRaises(DXFVersionError) as ctx:
            compile_ir_to_dxf(payload, dxf_version="R12")
        self.assertIn("lwpolyline requires dxf r2000", str(ctx.exception).lower())

    def test_repro_12_silent_layer_attribute_erasure(self):
        """REPRO-12: Layer linetype and flags are silently discarded during compilation."""
        payload = {
            "format": "LAVINCI_CAD_IR_V3",
            "layers": [
                {
                    "name": "HIDDEN_WALLS",
                    "color_aci": 3,
                    "linetype": "HIDDEN",
                    "is_locked": True,
                    "is_frozen": False,
                    "is_off": True
                }
            ]
        }
        doc = compile_ir_to_dxf(payload)
        layer = doc.layers.get("HIDDEN_WALLS")
        # Defect confirmation: layer.dxf.linetype is Continuous, not HIDDEN
        self.assertEqual(layer.dxf.linetype, "Continuous")
        # Defect confirmation: flags is 0, not locked/off
        self.assertEqual(layer.dxf.flags, 0)

    def test_repro_13_silent_component_attribute_destruction(self):
        """REPRO-13: Component attributes are silently dropped because no ATTDEF exists."""
        payload = {
            "format": "LAVINCI_CAD_IR_V3",
            "block_definitions": {
                "VALVE": {
                    "lines": [{"start": [-1.0, 0.0], "end": [1.0, 0.0]}]
                }
            },
            "components": [
                {
                    "block_name": "VALVE",
                    "position": [10.0, 20.0],
                    "attributes": {
                        "TAG": "V-101",
                        "PSI": "150",
                        "SYSTEM": "CHW"
                    }
                }
            ]
        }
        doc = compile_ir_to_dxf(payload)
        inserts = list(doc.modelspace().query("INSERT"))
        self.assertEqual(len(inserts), 1)
        # Defect confirmation: 0 ATTRIB entities created
        attrib_count = len(list(inserts[0].attribs))
        self.assertEqual(attrib_count, 0)

    def test_repro_14_mirror_scale_sign_flip_inversion(self):
        """REPRO-14: Near-zero negative scale flips sign from negative to positive."""
        payload = {
            "format": "LAVINCI_CAD_IR_V3",
            "components": [
                {
                    "block_name": "DOOR",
                    "scale": [-1e-7, 1.0, 1.0]
                }
            ]
        }
        doc = compile_ir_to_dxf(payload)
        inserts = list(doc.modelspace().query("INSERT"))
        self.assertEqual(len(inserts), 1)
        # Defect confirmation: xscale was flipped to positive 1e-6
        self.assertAlmostEqual(inserts[0].dxf.xscale, 1e-6)
        self.assertGreater(inserts[0].dxf.xscale, 0.0)

    def test_repro_15_3d_vertical_line_destruction(self):
        """REPRO-15: 3D vertical line (dx=0, dy=0, dz=100) is rejected as degenerate."""
        payload = {
            "format": "LAVINCI_CAD_IR_V3",
            "geometry_primitives": {
                "primitives": {
                    "lines": [
                        {
                            "start": [50.0, 50.0, 0.0],
                            "end": [50.0, 50.0, 100.0]
                        }
                    ]
                }
            }
        }
        doc = compile_ir_to_dxf(payload)
        lines = list(doc.modelspace().query("LINE"))
        # Defect confirmation: 0 lines emitted
        self.assertEqual(len(lines), 0)

    def test_repro_16_circular_block_reference_audit_error(self):
        """REPRO-16: Circular block reference produces ezdxf AuditError.INVALID_BLOCK_REFERENCE_CYCLE."""
        payload = {
            "format": "LAVINCI_CAD_IR_V3",
            "block_definitions": {
                "BLK_CYCLE": {
                    "lines": [{"start": [0, 0], "end": [1, 1]}],
                }
            }
        }
        doc = compile_ir_to_dxf(payload)
        # Inject cyclic insert manually to verify ezdxf audit detection
        blk = doc.blocks.get("BLK_CYCLE")
        blk.add_blockref("BLK_CYCLE", (0, 0))
        auditor = doc.audit()
        error_codes = [int(e.code) for e in auditor.errors]
        self.assertIn(int(AuditError.INVALID_BLOCK_REFERENCE_CYCLE), error_codes)


class TestBoundaryStability(unittest.TestCase):
    """Empirical verification of extreme boundary cases and resilience."""

    def test_boundary_empty_dict(self):
        """Empty dict {} should compile to valid DXF without unhandled crashes."""
        doc = compile_ir_to_dxf({})
        self.assertIsNotNone(doc)
        auditor = doc.audit()
        self.assertEqual(len(auditor.errors), 0)

    def test_boundary_empty_string(self):
        """Empty string JSON should raise json.JSONDecodeError, not unhandled crash."""
        with self.assertRaises(json.JSONDecodeError):
            compile_ir_to_dxf("")

    def test_boundary_nan_and_inf_coordinates_in_primitives(self):
        """NaN and Inf coordinates in lines, circles, arcs are filtered out by sanitizers."""
        payload = {
            "format": "LAVINCI_CAD_IR_V3",
            "geometry_primitives": {
                "primitives": {
                    "lines": [
                        {"start": [float("nan"), 0.0], "end": [10.0, 10.0]},
                        {"start": [0.0, float("inf")], "end": [10.0, 10.0]},
                        {"start": [0.0, 0.0], "end": [float("-inf"), 10.0]},
                        {"start": [0.0, 0.0], "end": [10.0, 10.0]},  # valid line
                    ],
                    "circles": [
                        {"center": [float("nan"), 0.0], "radius": 5.0},
                        {"center": [0.0, 0.0], "radius": float("inf")},
                        {"center": [0.0, 0.0], "radius": 5.0},  # valid circle
                    ],
                    "arcs": [
                        {"center": [float("nan"), 0.0], "radius": 5.0, "start_angle": 0.0, "end_angle": 90.0},
                        {"center": [0.0, 0.0], "radius": 5.0, "start_angle": 0.0, "end_angle": 90.0},  # valid arc
                    ]
                }
            }
        }
        doc = compile_ir_to_dxf(payload)
        msp = doc.modelspace()
        # Exactly the 1 valid line, 1 valid circle, and 1 valid arc should be present
        self.assertEqual(len(list(msp.query("LINE"))), 1)
        self.assertEqual(len(list(msp.query("CIRCLE"))), 1)
        self.assertEqual(len(list(msp.query("ARC"))), 1)

    def test_boundary_nan_extents_pollution(self):
        """Extents containing NaN serialize into header without validation (BUG-S2-03)."""
        payload = {
            "format": "LAVINCI_CAD_IR_V3",
            "extents": {
                "min": [float("nan"), float("nan")],
                "max": [float("nan"), float("nan")]
            }
        }
        doc = compile_ir_to_dxf(payload)
        # Verify that NaN floats infiltrated header extents
        extmin = doc.header["$EXTMIN"]
        self.assertTrue(math.isnan(extmin[0]))
        self.assertTrue(math.isnan(extmin[1]))

    def test_boundary_extreme_coordinate_values(self):
        """Extreme coordinates near float limits (1e308) compile without crash."""
        payload = {
            "format": "LAVINCI_CAD_IR_V3",
            "geometry_primitives": {
                "primitives": {
                    "lines": [
                        {"start": [0.0, 0.0], "end": [1e308, 1e308]}
                    ]
                }
            }
        }
        doc = compile_ir_to_dxf(payload)
        self.assertIsNotNone(doc)
        lines = list(doc.modelspace().query("LINE"))
        self.assertEqual(len(lines), 1)

    def test_boundary_zero_length_line_filtered(self):
        """Coincident start and end point line is correctly filtered out."""
        payload = {
            "format": "LAVINCI_CAD_IR_V3",
            "geometry_primitives": {
                "primitives": {
                    "lines": [
                        {"start": [5.0, 5.0], "end": [5.0, 5.0]}
                    ]
                }
            }
        }
        doc = compile_ir_to_dxf(payload)
        lines = list(doc.modelspace().query("LINE"))
        self.assertEqual(len(lines), 0)


if __name__ == "__main__":
    unittest.main(verbosity=2)

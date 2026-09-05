"""
test_fuzz_malformed.py — Automated Adversarial Fuzzing & Malformed Ingestion Harness

Adversarial Red-Team Fuzzing Suite for cad-ir-to-dxf compiler (Milestone 1).
Generates and executes >= 1,000 distinct malformed and extreme parameter permutations
against `compile_ir_to_dxf`, covering all 9 fuzzing categories:
  1. Extreme & non-finite floats (NaN, +/-Inf, 1e309, subnormal 1e-320, -0.0)
  2. Coordinate dimensionality anomalies (empty [], 1D [1.0], mismatched lengths)
  3. Type mismatches (None, str, bool, dict, list where floats are expected)
  4. Graph cycles & recursion (A->A, A->B->A, deep nesting depth 50+)
  5. Symbol table illegal characters (names with / \\ : ; ? * | = <> " control chars)
  6. Top-level & section nulls (None for geometry_primitives, layers, metadata, layouts)
  7. Scale & transformation edge cases (0.0, -0.0, 1e-7, -1e-7, non-finite scales)
  8. Character encodings & annotations (Unicode surrogates, null bytes \\x00, unmatched MTEXT format codes)
  9. Format & model ingestion (Pydantic models, unrecognized format strings)

Execution Modes:
  - Standalone script: python tests/test_fuzz_malformed.py
  - Unittest suite:    python -m unittest tests.test_fuzz_malformed
"""

import contextlib
import copy
import io
import json
import math
import os
import sys
import unittest
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple, Union

from cad_ir_to_dxf.compiler import compile_ir_to_dxf

try:
    from cad_extractor.models import CADIntermediateRepresentation
except ImportError:
    CADIntermediateRepresentation = None

try:
    import pydantic
except ImportError:
    pydantic = None


# ──────────────────────────────────────────────────────────────────────────────
# Data Models
# ──────────────────────────────────────────────────────────────────────────────

@dataclass
class FuzzCase:
    case_id: str
    category_id: int
    category_name: str
    subcategory: str
    description: str
    payload: Any
    kwargs: Dict[str, Any] = field(default_factory=dict)


@dataclass
class FuzzResult:
    case: FuzzCase
    outcome: str  # "COMPILED_OR_SANITIZED" or "CRASH"
    exception_type: Optional[str] = None
    exception_message: Optional[str] = None
    traceback_str: Optional[str] = None
    minimal_repro: Optional[str] = None


@dataclass
class CrashVector:
    vector_id: str
    category_id: int
    category_name: str
    exception_type: str
    exception_message: str
    root_cause: str
    minimal_repro: str
    occurrence_count: int = 0
    sample_case_ids: List[str] = field(default_factory=list)


@dataclass
class HarnessStats:
    total_executed: int = 0
    total_sanitized: int = 0
    total_crashes: int = 0
    category_counts: Dict[int, int] = field(default_factory=dict)
    category_sanitized: Dict[int, int] = field(default_factory=dict)
    category_crashes: Dict[int, int] = field(default_factory=dict)
    crash_type_counts: Dict[str, int] = field(default_factory=dict)
    crash_vectors: Dict[str, CrashVector] = field(default_factory=dict)
    results: List[FuzzResult] = field(default_factory=list)


# ──────────────────────────────────────────────────────────────────────────────
# Baseline IR Template
# ──────────────────────────────────────────────────────────────────────────────

def _base_ir() -> Dict[str, Any]:
    """Return a pristine, minimal valid CAD IR dictionary."""
    return {
        "format": "LAVINCI_CAD_IR_V3",
        "metadata": {
            "source_file": "fuzz_test.dxf",
            "dxf_version": "R2013",
            "units": 4,
            "measurement_system": "Metric",
        },
        "extents": {
            "min": [0.0, 0.0],
            "max": [100.0, 100.0],
        },
        "layers": [
            {"name": "0", "color_aci": 7, "linetype": "Continuous"},
            {"name": "WALLS", "color_aci": 1, "linetype": "Continuous"},
        ],
        "geometry_primitives": {
            "summary": {"total_count": 0},
            "primitives": {
                "lines": [],
                "arcs": [],
                "circles": [],
                "polylines": [],
            },
        },
        "components": [],
        "annotations": [],
        "dimensions": [],
        "block_definitions": {},
        "layouts": [],
    }


# ──────────────────────────────────────────────────────────────────────────────
# Generators for all 9 Categories
# ──────────────────────────────────────────────────────────────────────────────

def gen_category_1() -> List[FuzzCase]:
    """Category 1: Extreme & Non-Finite Floats."""
    cat_id = 1
    cat_name = "Extreme & Non-Finite Floats"
    cases: List[FuzzCase] = []

    float_values = [
        ("nan", float("nan")),
        ("pos_inf", float("inf")),
        ("neg_inf", float("-inf")),
        ("pos_1e309", 1e309),
        ("neg_1e309", -1e309),
        ("subnormal_pos", 1e-320),
        ("subnormal_neg", -1e-320),
        ("neg_zero", -0.0),
        ("pos_zero", 0.0),
        ("max_float", 1.79e308),
        ("min_float", -1.79e308),
        ("tiny_float", 1e-300),
    ]

    # 1. Lines
    for name, val in float_values:
        # Start coordinates
        ir = _base_ir()
        ir["geometry_primitives"]["primitives"]["lines"] = [{"start": [val, 10.0], "end": [20.0, 20.0]}]
        cases.append(FuzzCase(f"C1_LINE_START_X_{name}", cat_id, cat_name, "Lines", f"Line start X is {name}", ir))

        ir = _base_ir()
        ir["geometry_primitives"]["primitives"]["lines"] = [{"start": [10.0, val], "end": [20.0, 20.0]}]
        cases.append(FuzzCase(f"C1_LINE_START_Y_{name}", cat_id, cat_name, "Lines", f"Line start Y is {name}", ir))

        ir = _base_ir()
        ir["geometry_primitives"]["primitives"]["lines"] = [{"start": [val, val], "end": [20.0, 20.0]}]
        cases.append(FuzzCase(f"C1_LINE_START_XY_{name}", cat_id, cat_name, "Lines", f"Line start XY is {name}", ir))

        # End coordinates
        ir = _base_ir()
        ir["geometry_primitives"]["primitives"]["lines"] = [{"start": [0.0, 0.0], "end": [val, 20.0]}]
        cases.append(FuzzCase(f"C1_LINE_END_X_{name}", cat_id, cat_name, "Lines", f"Line end X is {name}", ir))

        ir = _base_ir()
        ir["geometry_primitives"]["primitives"]["lines"] = [{"start": [0.0, 0.0], "end": [20.0, val]}]
        cases.append(FuzzCase(f"C1_LINE_END_Y_{name}", cat_id, cat_name, "Lines", f"Line end Y is {name}", ir))

        # 3D Z coordinate
        ir = _base_ir()
        ir["geometry_primitives"]["primitives"]["lines"] = [{"start": [0.0, 0.0, val], "end": [10.0, 10.0, val]}]
        cases.append(FuzzCase(f"C1_LINE_3D_Z_{name}", cat_id, cat_name, "Lines", f"Line 3D Z is {name}", ir))

    # 2. Arcs
    for name, val in float_values:
        ir = _base_ir()
        ir["geometry_primitives"]["primitives"]["arcs"] = [{"center": [val, 0.0], "radius": 5.0, "start_angle": 0.0, "end_angle": 90.0}]
        cases.append(FuzzCase(f"C1_ARC_CENTER_X_{name}", cat_id, cat_name, "Arcs", f"Arc center X is {name}", ir))

        ir = _base_ir()
        ir["geometry_primitives"]["primitives"]["arcs"] = [{"center": [0.0, 0.0], "radius": val, "start_angle": 0.0, "end_angle": 90.0}]
        cases.append(FuzzCase(f"C1_ARC_RADIUS_{name}", cat_id, cat_name, "Arcs", f"Arc radius is {name}", ir))

        ir = _base_ir()
        ir["geometry_primitives"]["primitives"]["arcs"] = [{"center": [0.0, 0.0], "radius": 5.0, "start_angle": val, "end_angle": 90.0}]
        cases.append(FuzzCase(f"C1_ARC_START_ANG_{name}", cat_id, cat_name, "Arcs", f"Arc start angle is {name}", ir))

        ir = _base_ir()
        ir["geometry_primitives"]["primitives"]["arcs"] = [{"center": [0.0, 0.0], "radius": 5.0, "start_angle": 0.0, "end_angle": val}]
        cases.append(FuzzCase(f"C1_ARC_END_ANG_{name}", cat_id, cat_name, "Arcs", f"Arc end angle is {name}", ir))

    # 3. Circles
    for name, val in float_values:
        ir = _base_ir()
        ir["geometry_primitives"]["primitives"]["circles"] = [{"center": [val, 0.0], "radius": 5.0}]
        cases.append(FuzzCase(f"C1_CIRCLE_CENTER_X_{name}", cat_id, cat_name, "Circles", f"Circle center X is {name}", ir))

        ir = _base_ir()
        ir["geometry_primitives"]["primitives"]["circles"] = [{"center": [0.0, 0.0], "radius": val}]
        cases.append(FuzzCase(f"C1_CIRCLE_RADIUS_{name}", cat_id, cat_name, "Circles", f"Circle radius is {name}", ir))

    # 4. Polylines
    for name, val in float_values:
        ir = _base_ir()
        ir["geometry_primitives"]["primitives"]["polylines"] = [{"points": [[val, 0.0], [10.0, 10.0]]}]
        cases.append(FuzzCase(f"C1_POLY_PT0_X_{name}", cat_id, cat_name, "Polylines", f"Polyline pt0 X is {name}", ir))

        ir = _base_ir()
        ir["geometry_primitives"]["primitives"]["polylines"] = [{"points": [[0.0, 0.0], [val, val]]}]
        cases.append(FuzzCase(f"C1_POLY_PT1_XY_{name}", cat_id, cat_name, "Polylines", f"Polyline pt1 XY is {name}", ir))

    # 5. Components
    for name, val in float_values:
        ir = _base_ir()
        ir["components"] = [{"block_name": "FUZZ_BLK", "position": [val, 0.0]}]
        cases.append(FuzzCase(f"C1_COMP_POS_X_{name}", cat_id, cat_name, "Components", f"Component position X is {name}", ir))

        ir = _base_ir()
        ir["components"] = [{"block_name": "FUZZ_BLK", "rotation": val}]
        cases.append(FuzzCase(f"C1_COMP_ROT_{name}", cat_id, cat_name, "Components", f"Component rotation is {name}", ir))

        ir = _base_ir()
        ir["components"] = [{"block_name": "FUZZ_BLK", "scale": [val, 1.0, 1.0]}]
        cases.append(FuzzCase(f"C1_COMP_SCALE_X_{name}", cat_id, cat_name, "Components", f"Component scale X is {name}", ir))

        ir = _base_ir()
        ir["components"] = [{"block_name": "FUZZ_BLK", "scale": [1.0, val, 1.0]}]
        cases.append(FuzzCase(f"C1_COMP_SCALE_Y_{name}", cat_id, cat_name, "Components", f"Component scale Y is {name}", ir))

        ir = _base_ir()
        ir["components"] = [{"block_name": "FUZZ_BLK", "scale": [val, val, val]}]
        cases.append(FuzzCase(f"C1_COMP_SCALE_XYZ_{name}", cat_id, cat_name, "Components", f"Component scale XYZ is {name}", ir))

    # 6. Annotations
    for name, val in float_values:
        ir = _base_ir()
        ir["annotations"] = [{"type": "TEXT", "clean_text": "Sample", "position": [val, 0.0], "height": 1.0}]
        cases.append(FuzzCase(f"C1_ANNOT_POS_X_{name}", cat_id, cat_name, "Annotations", f"Annotation position X is {name}", ir))

        ir = _base_ir()
        ir["annotations"] = [{"type": "TEXT", "clean_text": "Sample", "position": [0.0, 0.0], "height": val}]
        cases.append(FuzzCase(f"C1_ANNOT_HEIGHT_{name}", cat_id, cat_name, "Annotations", f"Annotation height is {name}", ir))

    # 7. Dimensions
    for name, val in float_values:
        ir = _base_ir()
        ir["dimensions"] = [{"defpoint": [val, 0.0], "measurement": 10.0}]
        cases.append(FuzzCase(f"C1_DIM_DEFPOINT_X_{name}", cat_id, cat_name, "Dimensions", f"Dimension defpoint X is {name}", ir))

        ir = _base_ir()
        ir["dimensions"] = [{"defpoint": [0.0, 0.0], "measurement": val}]
        cases.append(FuzzCase(f"C1_DIM_MEASURE_{name}", cat_id, cat_name, "Dimensions", f"Dimension measurement is {name}", ir))

    # 8. Extents
    for name, val in float_values:
        ir = _base_ir()
        ir["extents"] = {"min": [val, 0.0], "max": [100.0, 100.0]}
        cases.append(FuzzCase(f"C1_EXT_MIN_X_{name}", cat_id, cat_name, "Extents", f"Extents min X is {name}", ir))

        ir = _base_ir()
        ir["extents"] = {"min": [0.0, 0.0], "max": [val, 100.0]}
        cases.append(FuzzCase(f"C1_EXT_MAX_X_{name}", cat_id, cat_name, "Extents", f"Extents max X is {name}", ir))

    return cases


def gen_category_2() -> List[FuzzCase]:
    """Category 2: Coordinate Dimensionality Anomalies."""
    cat_id = 2
    cat_name = "Coordinate Dimensionality Anomalies"
    cases: List[FuzzCase] = []

    # Coordinate length variations
    coords_list = [
        ("empty", []),
        ("1d_pos", [1.0]),
        ("1d_zero", [0.0]),
        ("1d_neg", [-5.5]),
        ("3d", [1.0, 2.0, 3.0]),
        ("4d", [1.0, 2.0, 3.0, 4.0]),
        ("5d", [1.0, 2.0, 3.0, 4.0, 5.0]),
        ("6d", [float(i) for i in range(6)]),
        ("7d", [float(i) for i in range(7)]),
        ("8d", [float(i) for i in range(8)]),
        ("10d", [float(i) for i in range(10)]),
        ("20d", [float(i) for i in range(20)]),
        ("50d", [float(i) for i in range(50)]),
    ]

    # Lines: start / end dimension combinations
    for name, coord in coords_list:
        ir = _base_ir()
        ir["geometry_primitives"]["primitives"]["lines"] = [{"start": coord, "end": [10.0, 10.0]}]
        cases.append(FuzzCase(f"C2_LINE_START_{name}", cat_id, cat_name, "Lines", f"Line start is {name}", ir))

        ir = _base_ir()
        ir["geometry_primitives"]["primitives"]["lines"] = [{"start": [0.0, 0.0], "end": coord}]
        cases.append(FuzzCase(f"C2_LINE_END_{name}", cat_id, cat_name, "Lines", f"Line end is {name}", ir))

        ir = _base_ir()
        ir["geometry_primitives"]["primitives"]["lines"] = [{"start": coord, "end": coord}]
        cases.append(FuzzCase(f"C2_LINE_BOTH_{name}", cat_id, cat_name, "Lines", f"Line both start/end are {name}", ir))

    # Mismatched 3D vertical lines
    ir = _base_ir()
    ir["geometry_primitives"]["primitives"]["lines"] = [{"start": [0.0, 0.0, 0.0], "end": [0.0, 0.0, 50.0]}]
    cases.append(FuzzCase("C2_LINE_3D_VERTICAL", cat_id, cat_name, "Lines", "3D vertical line (dx=dy=0, dz>0)", ir))

    # Arcs: center dimensions
    for name, coord in coords_list:
        ir = _base_ir()
        ir["geometry_primitives"]["primitives"]["arcs"] = [{"center": coord, "radius": 5.0, "start_angle": 0.0, "end_angle": 90.0}]
        cases.append(FuzzCase(f"C2_ARC_CENTER_{name}", cat_id, cat_name, "Arcs", f"Arc center is {name}", ir))

    # Circles: center dimensions
    for name, coord in coords_list:
        ir = _base_ir()
        ir["geometry_primitives"]["primitives"]["circles"] = [{"center": coord, "radius": 5.0}]
        cases.append(FuzzCase(f"C2_CIRCLE_CENTER_{name}", cat_id, cat_name, "Circles", f"Circle center is {name}", ir))

    # Polylines: point list and vertex dimensionalities
    poly_patterns = [
        ("empty_points", []),
        ("nested_empty", [[]]),
        ("single_vertex_1d", [[1.0]]),
        ("single_vertex_2d", [[1.0, 2.0]]),
        ("two_vertices_1d", [[1.0], [2.0]]),
        ("mismatched_1d_2d", [[1.0], [2.0, 3.0]]),
        ("mismatched_2d_1d", [[1.0, 2.0], [3.0]]),
        ("contains_empty_mid", [[1.0, 2.0], [], [3.0, 4.0]]),
        ("two_vertices_3d", [[1.0, 2.0, 3.0], [4.0, 5.0, 6.0]]),
        ("two_vertices_4d", [[1.0, 2.0, 3.0, 4.0], [5.0, 6.0, 7.0, 8.0]]),
        ("coincident_2d", [[0.0, 0.0], [0.0, 0.0]]),
        ("coincident_3d", [[0.0, 0.0, 0.0], [0.0, 0.0, 0.0]]),
        ("repeated_5_vertices", [[1.0, 1.0]] * 5),
        ("repeated_20_vertices", [[1.0, 1.0]] * 20),
        ("repeated_100_vertices", [[1.0, 1.0]] * 100),
    ]
    for name, pts in poly_patterns:
        ir = _base_ir()
        ir["geometry_primitives"]["primitives"]["polylines"] = [{"points": pts}]
        cases.append(FuzzCase(f"C2_POLY_{name}", cat_id, cat_name, "Polylines", f"Polyline points {name}", ir))

    # Components: position and scale dimensionalities
    for name, coord in coords_list:
        ir = _base_ir()
        ir["components"] = [{"block_name": "FUZZ_BLK", "position": coord}]
        cases.append(FuzzCase(f"C2_COMP_POS_{name}", cat_id, cat_name, "Components", f"Component position is {name}", ir))

        ir = _base_ir()
        ir["components"] = [{"block_name": "FUZZ_BLK", "scale": coord}]
        cases.append(FuzzCase(f"C2_COMP_SCALE_{name}", cat_id, cat_name, "Components", f"Component scale is {name}", ir))

    # Annotations: position dimensionality
    for name, coord in coords_list:
        ir = _base_ir()
        ir["annotations"] = [{"type": "TEXT", "clean_text": "Sample", "position": coord, "height": 1.0}]
        cases.append(FuzzCase(f"C2_ANNOT_POS_{name}", cat_id, cat_name, "Annotations", f"Annotation position is {name}", ir))

    # Dimensions: defpoint dimensionality
    for name, coord in coords_list:
        ir = _base_ir()
        ir["dimensions"] = [{"defpoint": coord, "measurement": 10.0}]
        cases.append(FuzzCase(f"C2_DIM_DEFPOINT_{name}", cat_id, cat_name, "Dimensions", f"Dimension defpoint is {name}", ir))

    # Extents: min/max dimensionality
    for name, coord in coords_list:
        ir = _base_ir()
        ir["extents"] = {"min": coord, "max": [100.0, 100.0]}
        cases.append(FuzzCase(f"C2_EXT_MIN_{name}", cat_id, cat_name, "Extents", f"Extents min is {name}", ir))

        ir = _base_ir()
        ir["extents"] = {"min": [0.0, 0.0], "max": coord}
        cases.append(FuzzCase(f"C2_EXT_MAX_{name}", cat_id, cat_name, "Extents", f"Extents max is {name}", ir))

    # Block definitions base_point
    for name, coord in coords_list:
        ir = _base_ir()
        ir["block_definitions"] = {"BLK1": {"base_point": coord, "lines": []}}
        cases.append(FuzzCase(f"C2_BLK_BASE_{name}", cat_id, cat_name, "BlockDefinitions", f"Block base_point is {name}", ir))

    return cases


def gen_category_3() -> List[FuzzCase]:
    """Category 3: Type Mismatches."""
    cat_id = 3
    cat_name = "Type Mismatches"
    cases: List[FuzzCase] = []

    mismatch_types = [
        ("none", None),
        ("str_int", "0"),
        ("str_float", "0.0"),
        ("str_nan", "NaN"),
        ("str_bad", "not_a_number"),
        ("str_empty", ""),
        ("bool_true", True),
        ("bool_false", False),
        ("dict_empty", {}),
        ("dict_filled", {"x": 1.0}),
        ("list_none", [None]),
        ("list_str", ["0.0"]),
        ("list_bool", [True]),
        ("list_dict", [{}]),
        ("list_nested", [[0.0]]),
        ("tuple_coords", (0.0, 0.0)),
    ]

    # Lines: start / end / layer / color
    for name, val in mismatch_types:
        ir = _base_ir()
        ir["geometry_primitives"]["primitives"]["lines"] = [{"start": val, "end": [10.0, 10.0]}]
        cases.append(FuzzCase(f"C3_LINE_START_{name}", cat_id, cat_name, "Lines", f"Line start type is {name}", ir))

        ir = _base_ir()
        ir["geometry_primitives"]["primitives"]["lines"] = [{"start": [0.0, 0.0], "end": val}]
        cases.append(FuzzCase(f"C3_LINE_END_{name}", cat_id, cat_name, "Lines", f"Line end type is {name}", ir))

        ir = _base_ir()
        ir["geometry_primitives"]["primitives"]["lines"] = [{"start": [0.0, 0.0], "end": [10.0, 10.0], "layer": val}]
        cases.append(FuzzCase(f"C3_LINE_LAYER_{name}", cat_id, cat_name, "Lines", f"Line layer type is {name}", ir))

        ir = _base_ir()
        ir["geometry_primitives"]["primitives"]["lines"] = [{"start": [0.0, 0.0], "end": [10.0, 10.0], "color": val}]
        cases.append(FuzzCase(f"C3_LINE_COLOR_{name}", cat_id, cat_name, "Lines", f"Line color type is {name}", ir))

    # Arcs: center / radius / angles
    for name, val in mismatch_types:
        ir = _base_ir()
        ir["geometry_primitives"]["primitives"]["arcs"] = [{"center": val, "radius": 5.0, "start_angle": 0.0, "end_angle": 90.0}]
        cases.append(FuzzCase(f"C3_ARC_CENTER_{name}", cat_id, cat_name, "Arcs", f"Arc center type is {name}", ir))

        ir = _base_ir()
        ir["geometry_primitives"]["primitives"]["arcs"] = [{"center": [0.0, 0.0], "radius": val, "start_angle": 0.0, "end_angle": 90.0}]
        cases.append(FuzzCase(f"C3_ARC_RADIUS_{name}", cat_id, cat_name, "Arcs", f"Arc radius type is {name}", ir))

    # Circles: center / radius
    for name, val in mismatch_types:
        ir = _base_ir()
        ir["geometry_primitives"]["primitives"]["circles"] = [{"center": val, "radius": 5.0}]
        cases.append(FuzzCase(f"C3_CIRCLE_CENTER_{name}", cat_id, cat_name, "Circles", f"Circle center type is {name}", ir))

        ir = _base_ir()
        ir["geometry_primitives"]["primitives"]["circles"] = [{"center": [0.0, 0.0], "radius": val}]
        cases.append(FuzzCase(f"C3_CIRCLE_RADIUS_{name}", cat_id, cat_name, "Circles", f"Circle radius type is {name}", ir))

    # Polylines: points / is_closed
    for name, val in mismatch_types:
        ir = _base_ir()
        ir["geometry_primitives"]["primitives"]["polylines"] = [{"points": val}]
        cases.append(FuzzCase(f"C3_POLY_PTS_{name}", cat_id, cat_name, "Polylines", f"Polyline points type is {name}", ir))

        ir = _base_ir()
        ir["geometry_primitives"]["primitives"]["polylines"] = [{"points": [[0.0, 0.0], [10.0, 10.0]], "is_closed": val}]
        cases.append(FuzzCase(f"C3_POLY_CLOSED_{name}", cat_id, cat_name, "Polylines", f"Polyline is_closed type is {name}", ir))

    # Components: block_name, position, scale, rotation, resolved_name, attributes
    for name, val in mismatch_types:
        ir = _base_ir()
        ir["components"] = [{"block_name": val, "position": [0.0, 0.0]}]
        cases.append(FuzzCase(f"C3_COMP_BLKNAME_{name}", cat_id, cat_name, "Components", f"Component block_name type is {name}", ir))

        ir = _base_ir()
        ir["components"] = [{"block_name": "BLK1", "position": val}]
        cases.append(FuzzCase(f"C3_COMP_POS_{name}", cat_id, cat_name, "Components", f"Component position type is {name}", ir))

        ir = _base_ir()
        ir["components"] = [{"block_name": "BLK1", "scale": val}]
        cases.append(FuzzCase(f"C3_COMP_SCALE_{name}", cat_id, cat_name, "Components", f"Component scale type is {name}", ir))

        ir = _base_ir()
        ir["components"] = [{"block_name": "BLK1", "resolved_name": val}]
        cases.append(FuzzCase(f"C3_COMP_RESNAME_{name}", cat_id, cat_name, "Components", f"Component resolved_name type is {name}", ir))

        ir = _base_ir()
        ir["components"] = [{"block_name": "BLK1", "attributes": val}]
        cases.append(FuzzCase(f"C3_COMP_ATTRIBS_{name}", cat_id, cat_name, "Components", f"Component attributes type is {name}", ir))

    # Annotations: type, position, height, clean_text
    for name, val in mismatch_types:
        ir = _base_ir()
        ir["annotations"] = [{"type": val, "clean_text": "Sample", "position": [0.0, 0.0], "height": 1.0}]
        cases.append(FuzzCase(f"C3_ANNOT_TYPE_{name}", cat_id, cat_name, "Annotations", f"Annotation type is {name}", ir))

        ir = _base_ir()
        ir["annotations"] = [{"type": "TEXT", "clean_text": "Sample", "position": val, "height": 1.0}]
        cases.append(FuzzCase(f"C3_ANNOT_POS_{name}", cat_id, cat_name, "Annotations", f"Annotation position type is {name}", ir))

        ir = _base_ir()
        ir["annotations"] = [{"type": "TEXT", "clean_text": "Sample", "position": [0.0, 0.0], "height": val}]
        cases.append(FuzzCase(f"C3_ANNOT_HEIGHT_{name}", cat_id, cat_name, "Annotations", f"Annotation height type is {name}", ir))

    # Dimensions: defpoint, measurement
    for name, val in mismatch_types:
        ir = _base_ir()
        ir["dimensions"] = [{"defpoint": val, "measurement": 10.0}]
        cases.append(FuzzCase(f"C3_DIM_DEFPOINT_{name}", cat_id, cat_name, "Dimensions", f"Dimension defpoint type is {name}", ir))

        ir = _base_ir()
        ir["dimensions"] = [{"defpoint": [0.0, 0.0], "measurement": val}]
        cases.append(FuzzCase(f"C3_DIM_MEASURE_{name}", cat_id, cat_name, "Dimensions", f"Dimension measurement type is {name}", ir))

    # Extents
    for name, val in mismatch_types:
        ir = _base_ir()
        ir["extents"] = val
        cases.append(FuzzCase(f"C3_EXTENTS_{name}", cat_id, cat_name, "Extents", f"Extents type is {name}", ir))

    return cases


def gen_category_4() -> List[FuzzCase]:
    """Category 4: Graph Cycles & Block Recursion."""
    cat_id = 4
    cat_name = "Graph Cycles & Block Recursion"
    cases: List[FuzzCase] = []

    # 1. Direct self-reference: Block A contains component A
    for i in range(10):
        ir = _base_ir()
        ir["block_definitions"] = {
            f"CYCLE_A_{i}": {
                "base_point": [0.0, 0.0, 0.0],
                "lines": [{"start": [0.0, 0.0], "end": [1.0, 1.0]}],
                "components": [{"block_name": f"CYCLE_A_{i}", "position": [float(i), float(i)]}],
            }
        }
        ir["components"] = [{"block_name": f"CYCLE_A_{i}", "position": [0.0, 0.0]}]
        cases.append(FuzzCase(f"C4_DIRECT_CYCLE_{i}", cat_id, cat_name, "DirectCycle", f"Direct self-reference cycle A->A (iter {i})", ir))

    # 2. Mutual 2-node cycles: A -> B -> A
    for i in range(10):
        ir = _base_ir()
        ir["block_definitions"] = {
            f"NODE_A_{i}": {
                "base_point": [0.0, 0.0, 0.0],
                "lines": [],
                "components": [{"block_name": f"NODE_B_{i}", "position": [1.0, 0.0]}],
            },
            f"NODE_B_{i}": {
                "base_point": [0.0, 0.0, 0.0],
                "lines": [],
                "components": [{"block_name": f"NODE_A_{i}", "position": [0.0, 1.0]}],
            },
        }
        ir["components"] = [{"block_name": f"NODE_A_{i}", "position": [0.0, 0.0]}]
        cases.append(FuzzCase(f"C4_MUTUAL_CYCLE_{i}", cat_id, cat_name, "MutualCycle", f"Mutual 2-node cycle A->B->A (iter {i})", ir))

    # 3. 3-node cycle: A -> B -> C -> A
    for i in range(10):
        ir = _base_ir()
        ir["block_definitions"] = {
            f"C3_A_{i}": {"components": [{"block_name": f"C3_B_{i}"}], "lines": []},
            f"C3_B_{i}": {"components": [{"block_name": f"C3_C_{i}"}], "lines": []},
            f"C3_C_{i}": {"components": [{"block_name": f"C3_A_{i}"}], "lines": []},
        }
        ir["components"] = [{"block_name": f"C3_A_{i}"}]
        cases.append(FuzzCase(f"C4_3NODE_CYCLE_{i}", cat_id, cat_name, "3NodeCycle", f"3-node cycle A->B->C->A (iter {i})", ir))

    # 4. 4-node cycle: A -> B -> C -> D -> A
    for i in range(10):
        ir = _base_ir()
        ir["block_definitions"] = {
            f"C4_A_{i}": {"components": [{"block_name": f"C4_B_{i}"}], "lines": []},
            f"C4_B_{i}": {"components": [{"block_name": f"C4_C_{i}"}], "lines": []},
            f"C4_C_{i}": {"components": [{"block_name": f"C4_D_{i}"}], "lines": []},
            f"C4_D_{i}": {"components": [{"block_name": f"C4_A_{i}"}], "lines": []},
        }
        ir["components"] = [{"block_name": f"C4_A_{i}"}]
        cases.append(FuzzCase(f"C4_4NODE_CYCLE_{i}", cat_id, cat_name, "4NodeCycle", f"4-node cycle A->B->C->D->A (iter {i})", ir))

    # 5. 5-node cycle: A -> B -> C -> D -> E -> A
    for i in range(6):
        ir = _base_ir()
        ir["block_definitions"] = {
            f"C5_A_{i}": {"components": [{"block_name": f"C5_B_{i}"}], "lines": []},
            f"C5_B_{i}": {"components": [{"block_name": f"C5_C_{i}"}], "lines": []},
            f"C5_C_{i}": {"components": [{"block_name": f"C5_D_{i}"}], "lines": []},
            f"C5_D_{i}": {"components": [{"block_name": f"C5_E_{i}"}], "lines": []},
            f"C5_E_{i}": {"components": [{"block_name": f"C5_A_{i}"}], "lines": []},
        }
        ir["components"] = [{"block_name": f"C5_A_{i}"}]
        cases.append(FuzzCase(f"C4_5NODE_CYCLE_{i}", cat_id, cat_name, "5NodeCycle", f"5-node cycle A->B->C->D->E->A (iter {i})", ir))

    # 6. Figure-8 / Dual Cycles
    for i in range(5):
        ir = _base_ir()
        ir["block_definitions"] = {
            f"FIG8_A_{i}": {"components": [{"block_name": f"FIG8_B_{i}"}], "lines": []},
            f"FIG8_B_{i}": {"components": [{"block_name": f"FIG8_A_{i}"}, {"block_name": f"FIG8_C_{i}"}], "lines": []},
            f"FIG8_C_{i}": {"components": [{"block_name": f"FIG8_B_{i}"}], "lines": []},
        }
        ir["components"] = [{"block_name": f"FIG8_B_{i}"}]
        cases.append(FuzzCase(f"C4_FIG8_CYCLE_{i}", cat_id, cat_name, "Figure8Cycle", f"Figure-8 dual cycle (iter {i})", ir))

    # 7. Reserved block insertion (*Model_Space, *Paper_Space)
    for i in range(5):
        ir = _base_ir()
        ir["components"] = [{"block_name": "*Model_Space", "position": [float(i), 0.0]}]
        cases.append(FuzzCase(f"C4_INSERT_MODEL_SPACE_{i}", cat_id, cat_name, "ReservedBlocks", f"Insert *Model_Space into modelspace (iter {i})", ir))

        ir = _base_ir()
        ir["components"] = [{"block_name": "*Paper_Space", "position": [float(i), 0.0]}]
        cases.append(FuzzCase(f"C4_INSERT_PAPER_SPACE_{i}", cat_id, cat_name, "ReservedBlocks", f"Insert *Paper_Space into modelspace (iter {i})", ir))

        ir = _base_ir()
        ir["components"] = [{"block_name": "*Model_Space", "space": "Layout1"}]
        cases.append(FuzzCase(f"C4_INSERT_MODEL_IN_PAPER_{i}", cat_id, cat_name, "ReservedBlocks", f"Insert *Model_Space into paper space (iter {i})", ir))

        ir = _base_ir()
        ir["components"] = [{"block_name": "*Paper_Space", "space": "Layout1"}]
        cases.append(FuzzCase(f"C4_INSERT_PAPER_IN_PAPER_{i}", cat_id, cat_name, "ReservedBlocks", f"Insert *Paper_Space into paper space (iter {i})", ir))

    # 8. Deep nesting chains: Depth 10 to 200
    nesting_depths = [10, 20, 30, 40, 50, 60, 70, 80, 90, 100, 120, 150, 180, 200]
    for depth in nesting_depths:
        ir = _base_ir()
        defs = {}
        for d in range(depth):
            next_name = f"DEPTH_{depth}_BLK_{d+1}" if d + 1 < depth else None
            defs[f"DEPTH_{depth}_BLK_{d}"] = {
                "base_point": [0.0, 0.0, 0.0],
                "lines": [{"start": [0.0, 0.0], "end": [1.0, 1.0]}],
                "components": [{"block_name": next_name}] if next_name else [],
            }
        ir["block_definitions"] = defs
        ir["components"] = [{"block_name": f"DEPTH_{depth}_BLK_0"}]
        cases.append(FuzzCase(f"C4_DEEP_CHAIN_{depth}", cat_id, cat_name, "DeepHierarchy", f"Deep block chain depth {depth}", ir))

    # 9. Wide block hierarchies (1 block referencing many unique blocks)
    for width in [10, 25, 50, 75, 100]:
        ir = _base_ir()
        defs = {}
        sub_comps = []
        for w in range(width):
            sub_name = f"WIDE_{width}_SUB_{w}"
            defs[sub_name] = {"lines": [{"start": [0.0, 0.0], "end": [float(w), float(w)]}]}
            sub_comps.append({"block_name": sub_name, "position": [float(w), 0.0]})
        defs[f"WIDE_ROOT_{width}"] = {"components": sub_comps, "lines": []}
        ir["block_definitions"] = defs
        ir["components"] = [{"block_name": f"WIDE_ROOT_{width}"}]
        cases.append(FuzzCase(f"C4_WIDE_HIERARCHY_{width}", cat_id, cat_name, "WideHierarchy", f"Wide block hierarchy width {width}", ir))

    # 10. Missing block references in chain
    for i in range(10):
        ir = _base_ir()
        ir["components"] = [
            {"block_name": f"MISSING_BLOCK_CHAIN_{i}_{j}", "position": [float(j), 0.0]}
            for j in range(5)
        ]
        cases.append(FuzzCase(f"C4_MISSING_BLOCKS_{i}", cat_id, cat_name, "MissingBlocks", f"Chain of missing blocks auto-vivification (iter {i})", ir))

    return cases


def gen_category_5() -> List[FuzzCase]:
    """Category 5: Symbol Table Illegal Characters."""
    cat_id = 5
    cat_name = "Symbol Table Illegal Characters"
    cases: List[FuzzCase] = []

    # AutoCAD prohibited characters in symbol names: < > / \ " : ; ? * | = '
    forbidden_chars = [
        ("slash", "/"),
        ("backslash", "\\"),
        ("colon", ":"),
        ("semicolon", ";"),
        ("question", "?"),
        ("asterisk", "*"),
        ("pipe", "|"),
        ("equals", "="),
        ("less_than", "<"),
        ("greater_than", ">"),
        ("quote", '"'),
        ("single_quote", "'"),
        ("comma", ","),
        ("backtick", "`"),
    ]

    # Control characters
    control_chars = [
        ("null", "\x00"),
        ("soh", "\x01"),
        ("stx", "\x02"),
        ("etx", "\x03"),
        ("bel", "\x07"),
        ("bs", "\x08"),
        ("tab", "\t"),
        ("lf", "\n"),
        ("vt", "\x0b"),
        ("ff", "\x0c"),
        ("cr", "\r"),
        ("esc", "\x1b"),
        ("del", "\x7f"),
    ]

    # Whitespace patterns
    whitespace_patterns = [
        ("empty", ""),
        ("spaces_3", "   "),
        ("spaces_tabs", " \t "),
        ("leading_space", " LAYER"),
        ("trailing_space", "LAYER "),
        ("crlf", "\r\n"),
    ]

    # 1. Layers table definitions
    for name, char in forbidden_chars:
        ir = _base_ir()
        ir["layers"].append({"name": f"TEST{char}LAYER", "color_aci": 1})
        cases.append(FuzzCase(f"C5_LAYER_DEF_FORBIDDEN_{name}", cat_id, cat_name, "LayerDefinition", f"Layer definition name has '{char}'", ir))

    for name, char in control_chars:
        ir = _base_ir()
        ir["layers"].append({"name": f"CTRL{char}LAYER", "color_aci": 1})
        cases.append(FuzzCase(f"C5_LAYER_DEF_CTRL_{name}", cat_id, cat_name, "LayerDefinition", f"Layer definition name has control char {name}", ir))

    for name, ws in whitespace_patterns:
        ir = _base_ir()
        ir["layers"].append({"name": ws, "color_aci": 1})
        cases.append(FuzzCase(f"C5_LAYER_DEF_WS_{name}", cat_id, cat_name, "LayerDefinition", f"Layer definition name is whitespace pattern {name}", ir))

    # Combined illegal characters
    combined_illegal = [
        ("all_forbidden", "/\\:;?*|=<>'\",`"),
        ("path_like", "ROOT/SUBDIR/LAYER"),
        ("windows_path", "C:\\WALLS\\EXTERIOR"),
        ("urn_like", "urn:cad:layer:walls"),
        ("wildcard", "WALLS*LIGHTS?"),
        ("expression", "L1=L2|L3"),
        ("bracketed", "<SPECIAL_LAYER>"),
        ("quoted", '"QUOTED_LAYER"'),
        ("super_long", "A" * 255 + "/" + "B" * 255),
    ]
    for name, val in combined_illegal:
        ir = _base_ir()
        ir["layers"].append({"name": val, "color_aci": 2})
        cases.append(FuzzCase(f"C5_LAYER_DEF_COMBINED_{name}", cat_id, cat_name, "LayerDefinition", f"Layer definition name is {name}", ir))

    # 2. Entity referencing layer with forbidden chars (auto-vivification)
    for name, char in forbidden_chars:
        # On lines
        ir = _base_ir()
        ir["geometry_primitives"]["primitives"]["lines"] = [{"start": [0.0, 0.0], "end": [10.0, 10.0], "layer": f"LINE{char}LAYER"}]
        cases.append(FuzzCase(f"C5_LINE_LAYER_AUTO_{name}", cat_id, cat_name, "EntityLayerAutoViv", f"Line references layer with '{char}'", ir))

        # On polylines
        ir = _base_ir()
        ir["geometry_primitives"]["primitives"]["polylines"] = [{"points": [[0.0, 0.0], [10.0, 10.0]], "layer": f"POLY{char}LAYER"}]
        cases.append(FuzzCase(f"C5_POLY_LAYER_AUTO_{name}", cat_id, cat_name, "EntityLayerAutoViv", f"Polyline references layer with '{char}'", ir))

        # On components
        ir = _base_ir()
        ir["components"] = [{"block_name": "BLK1", "layer": f"COMP{char}LAYER"}]
        cases.append(FuzzCase(f"C5_COMP_LAYER_AUTO_{name}", cat_id, cat_name, "EntityLayerAutoViv", f"Component references layer with '{char}'", ir))

    for name, char in control_chars:
        ir = _base_ir()
        ir["geometry_primitives"]["primitives"]["lines"] = [{"start": [0.0, 0.0], "end": [10.0, 10.0], "layer": f"L{char}N"}]
        cases.append(FuzzCase(f"C5_LINE_LAYER_CTRL_{name}", cat_id, cat_name, "EntityLayerAutoViv", f"Line references layer with control char {name}", ir))

    # 3. Block definitions table names
    for name, char in forbidden_chars:
        ir = _base_ir()
        ir["block_definitions"] = {f"BLK{char}NAME": {"lines": [{"start": [0.0, 0.0], "end": [1.0, 1.0]}]}}
        cases.append(FuzzCase(f"C5_BLK_DEF_FORBIDDEN_{name}", cat_id, cat_name, "BlockDefinitionName", f"Block definition name has '{char}'", ir))

    for name, char in control_chars:
        ir = _base_ir()
        ir["block_definitions"] = {f"B{char}K": {"lines": []}}
        cases.append(FuzzCase(f"C5_BLK_DEF_CTRL_{name}", cat_id, cat_name, "BlockDefinitionName", f"Block definition name has control char {name}", ir))

    # Reserved block names
    reserved_blocks = ["*CUSTOM", "$CUSTOM", "", "0", "*MODEL_SPACE", "*PAPER_SPACE", "*U1", "*D1"]
    for idx, rname in enumerate(reserved_blocks):
        ir = _base_ir()
        ir["block_definitions"] = {rname: {"lines": []}}
        cases.append(FuzzCase(f"C5_BLK_DEF_RESERVED_{idx}", cat_id, cat_name, "BlockDefinitionName", f"Block definition name is reserved '{rname}'", ir))

    # 4. Component block_name (placeholder auto-vivification)
    for name, char in forbidden_chars:
        ir = _base_ir()
        ir["components"] = [{"block_name": f"PH{char}NAME", "position": [0.0, 0.0]}]
        cases.append(FuzzCase(f"C5_COMP_PH_FORBIDDEN_{name}", cat_id, cat_name, "ComponentPlaceholder", f"Component placeholder name has '{char}'", ir))

    for name, char in control_chars:
        ir = _base_ir()
        ir["components"] = [{"block_name": f"PH{char}CTRL", "position": [0.0, 0.0]}]
        cases.append(FuzzCase(f"C5_COMP_PH_CTRL_{name}", cat_id, cat_name, "ComponentPlaceholder", f"Component placeholder name has control char {name}", ir))

    # 5. Entity space name (paper space auto-vivification)
    for name, char in forbidden_chars:
        ir = _base_ir()
        ir["geometry_primitives"]["primitives"]["lines"] = [{"start": [0.0, 0.0], "end": [1.0, 1.0], "space": f"LAYOUT{char}1"}]
        cases.append(FuzzCase(f"C5_SPACE_FORBIDDEN_{name}", cat_id, cat_name, "LayoutSpaceName", f"Entity space name has '{char}'", ir))

    for name, char in control_chars:
        ir = _base_ir()
        ir["geometry_primitives"]["primitives"]["lines"] = [{"start": [0.0, 0.0], "end": [1.0, 1.0], "space": f"S{char}P"}]
        cases.append(FuzzCase(f"C5_SPACE_CTRL_{name}", cat_id, cat_name, "LayoutSpaceName", f"Entity space name has control char {name}", ir))

    reserved_spaces = ["*Model", "*Paper", "Model/1", "Layout:2", "Sheet|A", "Page?1", "View<1>"]
    for idx, sname in enumerate(reserved_spaces):
        ir = _base_ir()
        ir["geometry_primitives"]["primitives"]["lines"] = [{"start": [0.0, 0.0], "end": [1.0, 1.0], "space": sname}]
        cases.append(FuzzCase(f"C5_SPACE_RESERVED_{idx}", cat_id, cat_name, "LayoutSpaceName", f"Entity space name is reserved/malformed '{sname}'", ir))

    return cases


def gen_category_6() -> List[FuzzCase]:
    """Category 6: Top-Level & Section Nulls."""
    cat_id = 6
    cat_name = "Top-Level & Section Nulls"
    cases: List[FuzzCase] = []

    # 1. Complete root empties
    cases.append(FuzzCase("C6_ROOT_EMPTY_DICT", cat_id, cat_name, "RootNull", "Root IR is empty dictionary", {}))
    cases.append(FuzzCase("C6_ROOT_FORMAT_ONLY", cat_id, cat_name, "RootNull", "Root IR has only format key", {"format": "LAVINCI_CAD_IR_V3"}))

    # 2. Top-level keys set to None
    top_level_keys = [
        "geometry_primitives",
        "layers",
        "metadata",
        "layouts",
        "block_definitions",
        "components",
        "annotations",
        "dimensions",
        "extents",
        "bill_of_materials",
    ]

    for key in top_level_keys:
        ir = _base_ir()
        ir[key] = None
        cases.append(FuzzCase(f"C6_TOP_NULL_{key}", cat_id, cat_name, "TopLevelNull", f"Top-level key '{key}' is None", ir))

    # Combinations of 2 top-level keys as None
    for i in range(len(top_level_keys)):
        for j in range(i + 1, min(i + 5, len(top_level_keys))):
            k1, k2 = top_level_keys[i], top_level_keys[j]
            ir = _base_ir()
            ir[k1] = None
            ir[k2] = None
            cases.append(FuzzCase(f"C6_TOP_NULL_PAIR_{k1}_{k2}", cat_id, cat_name, "TopLevelNullPair", f"Keys '{k1}' and '{k2}' are None", ir))

    # All top-level keys as None
    all_null_ir = {k: None for k in top_level_keys}
    all_null_ir["format"] = "LAVINCI_CAD_IR_V3"
    cases.append(FuzzCase("C6_TOP_ALL_NULL", cat_id, cat_name, "TopLevelAllNull", "All top-level keys are None", all_null_ir))

    # 3. Sub-section nulls
    sub_nulls = [
        ("primitives_null", {"summary": {}, "primitives": None}),
        ("summary_null", {"summary": None, "primitives": {}}),
        ("lines_null", {"summary": {}, "primitives": {"lines": None}}),
        ("arcs_null", {"summary": {}, "primitives": {"arcs": None}}),
        ("circles_null", {"summary": {}, "primitives": {"circles": None}}),
        ("polylines_null", {"summary": {}, "primitives": {"polylines": None}}),
        ("lines_arcs_null", {"summary": {}, "primitives": {"lines": None, "arcs": None}}),
        ("all_prims_null", {"summary": {}, "primitives": {"lines": None, "arcs": None, "circles": None, "polylines": None}}),
    ]
    for name, geo in sub_nulls:
        ir = _base_ir()
        ir["geometry_primitives"] = geo
        cases.append(FuzzCase(f"C6_GEO_SUB_{name}", cat_id, cat_name, "GeometrySubNull", f"Geometry subsection {name}", ir))

    # 4. List items contain None
    item_nulls = [
        ("lines_single_none", "geometry_primitives", {"summary": {}, "primitives": {"lines": [None]}}),
        ("lines_double_none", "geometry_primitives", {"summary": {}, "primitives": {"lines": [None, None]}}),
        ("lines_mixed_none", "geometry_primitives", {"summary": {}, "primitives": {"lines": [{"start": [0, 0], "end": [1, 1]}, None]}}),
        ("arcs_single_none", "geometry_primitives", {"summary": {}, "primitives": {"arcs": [None]}}),
        ("circles_single_none", "geometry_primitives", {"summary": {}, "primitives": {"circles": [None]}}),
        ("polylines_single_none", "geometry_primitives", {"summary": {}, "primitives": {"polylines": [None]}}),
        ("components_single_none", "components", [None]),
        ("components_mixed_none", "components", [{"block_name": "BLK1"}, None]),
        ("annotations_single_none", "annotations", [None]),
        ("dimensions_single_none", "dimensions", [None]),
        ("layers_single_none", "layers", [None]),
        ("layouts_single_none", "layouts", [None]),
    ]
    for name, sec, val in item_nulls:
        ir = _base_ir()
        ir[sec] = val
        cases.append(FuzzCase(f"C6_ITEM_NULL_{name}", cat_id, cat_name, "ItemNull", f"Item in {sec} is None", ir))

    # Block definition value is None
    ir = _base_ir()
    ir["block_definitions"] = {"BLK_NONE": None}
    cases.append(FuzzCase("C6_BLK_DEF_VAL_NONE", cat_id, cat_name, "BlockDefinitionNull", "Block definition value is None", ir))

    # 5. Empty dictionaries for entities
    empty_entities = [
        ("line_empty", "lines", {}),
        ("arc_empty", "arcs", {}),
        ("circle_empty", "circles", {}),
        ("poly_empty", "polylines", {}),
    ]
    for name, ptype, entity in empty_entities:
        ir = _base_ir()
        ir["geometry_primitives"]["primitives"][ptype] = [entity]
        cases.append(FuzzCase(f"C6_ENTITY_EMPTY_{name}", cat_id, cat_name, "EntityEmptyDict", f"{ptype} entity is empty dictionary", ir))

    # Entity attributes explicitly all None
    ir = _base_ir()
    ir["geometry_primitives"]["primitives"]["lines"] = [{"start": None, "end": None, "layer": None, "color": None}]
    cases.append(FuzzCase("C6_LINE_ATTRS_ALL_NONE", cat_id, cat_name, "EntityAttrsAllNone", "Line entity attributes all None", ir))

    ir = _base_ir()
    ir["geometry_primitives"]["primitives"]["arcs"] = [{"center": None, "radius": None, "start_angle": None, "end_angle": None, "layer": None}]
    cases.append(FuzzCase("C6_ARC_ATTRS_ALL_NONE", cat_id, cat_name, "EntityAttrsAllNone", "Arc entity attributes all None", ir))

    ir = _base_ir()
    ir["geometry_primitives"]["primitives"]["circles"] = [{"center": None, "radius": None, "layer": None}]
    cases.append(FuzzCase("C6_CIRCLE_ATTRS_ALL_NONE", cat_id, cat_name, "EntityAttrsAllNone", "Circle entity attributes all None", ir))

    ir = _base_ir()
    ir["geometry_primitives"]["primitives"]["polylines"] = [{"points": None, "layer": None, "is_closed": None}]
    cases.append(FuzzCase("C6_POLY_ATTRS_ALL_NONE", cat_id, cat_name, "EntityAttrsAllNone", "Polyline entity attributes all None", ir))

    ir = _base_ir()
    ir["components"] = [{"block_name": None, "position": None, "scale": None, "rotation": None, "layer": None, "attributes": None}]
    cases.append(FuzzCase("C6_COMP_ATTRS_ALL_NONE", cat_id, cat_name, "EntityAttrsAllNone", "Component entity attributes all None", ir))

    ir = _base_ir()
    ir["annotations"] = [{"type": None, "clean_text": None, "raw_text": None, "position": None, "height": None, "layer": None}]
    cases.append(FuzzCase("C6_ANNOT_ATTRS_ALL_NONE", cat_id, cat_name, "EntityAttrsAllNone", "Annotation entity attributes all None", ir))

    ir = _base_ir()
    ir["dimensions"] = [{"defpoint": None, "measurement": None, "text": None, "layer": None}]
    cases.append(FuzzCase("C6_DIM_ATTRS_ALL_NONE", cat_id, cat_name, "EntityAttrsAllNone", "Dimension entity attributes all None", ir))

    ir = _base_ir()
    ir["layers"] = [{"name": None, "color_aci": None, "linetype": None, "is_off": None, "is_locked": None, "is_frozen": None}]
    cases.append(FuzzCase("C6_LAYER_ATTRS_ALL_NONE", cat_id, cat_name, "EntityAttrsAllNone", "Layer entity attributes all None", ir))

    return cases


def gen_category_7() -> List[FuzzCase]:
    """Category 7: Scale & Transformation Edge Cases."""
    cat_id = 7
    cat_name = "Scale & Transformation Edge Cases"
    cases: List[FuzzCase] = []

    scale_vectors = [
        # Zero and negative zero
        ("zero_all", [0.0, 0.0, 0.0]),
        ("zero_x", [0.0, 1.0, 1.0]),
        ("zero_y", [1.0, 0.0, 1.0]),
        ("zero_z", [1.0, 1.0, 0.0]),
        ("zero_xy", [0.0, 0.0, 1.0]),
        ("neg_zero_all", [-0.0, -0.0, -0.0]),
        ("neg_zero_x", [-0.0, 1.0, 1.0]),
        ("neg_zero_y", [1.0, -0.0, 1.0]),
        ("neg_zero_z", [1.0, 1.0, -0.0]),
        # Sub-epsilon scales (< 1e-6)
        ("sub_eps_pos_1e7_all", [1e-7, 1e-7, 1e-7]),
        ("sub_eps_pos_1e7_x", [1e-7, 1.0, 1.0]),
        ("sub_eps_pos_1e7_y", [1.0, 1e-7, 1.0]),
        ("sub_eps_pos_1e7_z", [1.0, 1.0, 1e-7]),
        ("sub_eps_neg_1e7_all", [-1e-7, -1e-7, -1e-7]),
        ("sub_eps_neg_1e7_x", [-1e-7, 1.0, 1.0]),
        ("sub_eps_neg_1e7_y", [1.0, -1e-7, 1.0]),
        ("sub_eps_neg_1e7_z", [1.0, 1.0, -1e-7]),
        ("sub_eps_1e8", [1e-8, 1.0, 1.0]),
        ("sub_eps_neg_1e8", [-1e-8, 1.0, 1.0]),
        ("sub_eps_1e12", [1e-12, 1e-12, 1e-12]),
        ("sub_eps_neg_1e12", [-1e-12, -1e-12, -1e-12]),
        ("sub_eps_1e15", [1e-15, 1.0, 1.0]),
        ("sub_eps_subnormal", [1e-320, 1.0, 1.0]),
        ("sub_eps_neg_subnormal", [-1e-320, 1.0, 1.0]),
        # Inverted scales (mirrored components)
        ("mirror_x", [-1.0, 1.0, 1.0]),
        ("mirror_y", [1.0, -1.0, 1.0]),
        ("mirror_z", [1.0, 1.0, -1.0]),
        ("mirror_xy", [-1.0, -1.0, 1.0]),
        ("mirror_xz", [-1.0, 1.0, -1.0]),
        ("mirror_yz", [1.0, -1.0, -1.0]),
        ("mirror_xyz", [-1.0, -1.0, -1.0]),
        # Non-finite scales
        ("scale_nan_x", [float("nan"), 1.0, 1.0]),
        ("scale_nan_y", [1.0, float("nan"), 1.0]),
        ("scale_nan_z", [1.0, 1.0, float("nan")]),
        ("scale_nan_all", [float("nan"), float("nan"), float("nan")]),
        ("scale_inf_x", [float("inf"), 1.0, 1.0]),
        ("scale_inf_y", [1.0, float("inf"), 1.0]),
        ("scale_inf_z", [1.0, 1.0, float("inf")]),
        ("scale_inf_all", [float("inf"), float("inf"), float("inf")]),
        ("scale_neg_inf_x", [float("-inf"), 1.0, 1.0]),
        ("scale_1e309_x", [1e309, 1.0, 1.0]),
        # Extreme finite scales
        ("scale_huge_1e10", [1e10, 1e10, 1e10]),
        ("scale_huge_neg_1e10", [-1e10, -1e10, -1e10]),
        ("scale_huge_1e30", [1e30, 1.0, 1.0]),
        ("scale_huge_1e308", [1.79e308, 1.0, 1.0]),
    ]

    for name, svec in scale_vectors:
        ir = _base_ir()
        ir["components"] = [{"block_name": "FUZZ_BLK", "scale": svec, "position": [0.0, 0.0]}]
        cases.append(FuzzCase(f"C7_SCALE_{name}", cat_id, cat_name, "ComponentScale", f"Component scale vector is {name}", ir))

    # Rotations
    rotations = [
        ("zero", 0.0),
        ("neg_zero", -0.0),
        ("360", 360.0),
        ("neg_360", -360.0),
        ("720", 720.0),
        ("neg_720", -720.0),
        ("sub_eps", 1e-7),
        ("neg_sub_eps", -1e-7),
        ("huge_1e8", 1e8),
        ("huge_neg_1e8", -1e8),
        ("nan", float("nan")),
        ("inf", float("inf")),
        ("neg_inf", float("-inf")),
    ]
    for name, rot in rotations:
        ir = _base_ir()
        ir["components"] = [{"block_name": "FUZZ_BLK", "rotation": rot, "position": [0.0, 0.0]}]
        cases.append(FuzzCase(f"C7_ROTATION_{name}", cat_id, cat_name, "ComponentRotation", f"Component rotation is {name}", ir))

    # Positions with scale combinations
    positions = [
        ("zero", [0.0, 0.0]),
        ("neg_zero", [-0.0, -0.0]),
        ("sub_eps", [1e-7, 1e-7]),
        ("neg_sub_eps", [-1e-7, -1e-7]),
        ("huge", [1e12, -1e12]),
        ("nan", [float("nan"), float("nan")]),
        ("inf", [float("inf"), float("inf")]),
        ("neg_inf", [float("-inf"), float("-inf")]),
    ]
    for name, pos in positions:
        ir = _base_ir()
        ir["components"] = [{"block_name": "FUZZ_BLK", "position": pos, "scale": [1.0, 1.0, 1.0]}]
        cases.append(FuzzCase(f"C7_POS_{name}", cat_id, cat_name, "ComponentPosition", f"Component position is {name}", ir))

    # Block definition base points
    base_points = [
        ("zero", [0.0, 0.0, 0.0]),
        ("neg_zero", [-0.0, -0.0, -0.0]),
        ("sub_eps", [1e-7, 1e-7, 1e-7]),
        ("huge", [1e10, 1e10, 1e10]),
        ("nan", [float("nan"), float("nan"), float("nan")]),
        ("inf", [float("inf"), float("inf"), float("inf")]),
    ]
    for name, bp in base_points:
        ir = _base_ir()
        ir["block_definitions"] = {"BLK1": {"base_point": bp, "lines": []}}
        cases.append(FuzzCase(f"C7_BLK_BASE_{name}", cat_id, cat_name, "BlockBasePoint", f"Block base point is {name}", ir))

    # Complex combination transforms (scale + rot + pos)
    for idx in range(15):
        s = 0.0 if idx % 3 == 0 else (-1e-7 if idx % 3 == 1 else -1.0)
        r = float(idx * 45)
        p = [float(idx * 10), float(-idx * 5)]
        ir = _base_ir()
        ir["components"] = [{"block_name": "FUZZ_BLK", "scale": [s, s, 1.0], "rotation": r, "position": p}]
        cases.append(FuzzCase(f"C7_COMBO_TRANSFORM_{idx}", cat_id, cat_name, "ComboTransform", f"Combined scale={s}, rot={r}, pos={p}", ir))

    return cases


def gen_category_8() -> List[FuzzCase]:
    """Category 8: Character Encodings & Annotations."""
    cat_id = 8
    cat_name = "Character Encodings & Annotations"
    cases: List[FuzzCase] = []

    # 1. Unicode Surrogates
    surrogates = [
        ("lone_lead_d800", "\ud800"),
        ("lone_lead_d801", "\ud801"),
        ("lone_lead_d83d", "\ud83d"),
        ("lone_trail_dc00", "\udc00"),
        ("lone_trail_dc01", "\udc01"),
        ("lone_trail_dfff", "\udfff"),
        ("double_lead", "\ud800\ud800"),
        ("double_trail", "\udc00\udc00"),
        ("inverted_pair", "\udc00\ud800"),
        ("embedded_surrogate", "Start \ud800 Middle \udfff End"),
    ]
    for name, surr in surrogates:
        ir = _base_ir()
        ir["annotations"] = [{"type": "TEXT", "clean_text": surr, "position": [0.0, 0.0], "height": 1.0}]
        cases.append(FuzzCase(f"C8_TEXT_SURR_{name}", cat_id, cat_name, "UnicodeSurrogates", f"TEXT clean_text has surrogate {name}", ir))

        ir = _base_ir()
        ir["annotations"] = [{"type": "MTEXT", "raw_text": surr, "position": [0.0, 0.0], "height": 1.0}]
        cases.append(FuzzCase(f"C8_MTEXT_SURR_{name}", cat_id, cat_name, "UnicodeSurrogates", f"MTEXT raw_text has surrogate {name}", ir))

    # 2. Null bytes & control characters
    control_texts = [
        ("null_byte_only", "\x00"),
        ("null_byte_embedded", "Prefix\x00Suffix"),
        ("null_byte_triple", "\x00\x00\x00"),
        ("bell", "Bell\x07Alert"),
        ("backspace", "Back\x08Space"),
        ("vertical_tab", "Vert\x0bTab"),
        ("form_feed", "Form\x0cFeed"),
        ("escape", "Esc\x1bCode"),
        ("delete", "Del\x7fChar"),
        ("newline_lf", "Line 1\nLine 2"),
        ("newline_crlf", "Line 1\r\nLine 2"),
        ("newline_cr", "Line 1\rLine 2"),
        ("multiline_lf", "\n\n\n"),
    ]
    for name, ctext in control_texts:
        ir = _base_ir()
        ir["annotations"] = [{"type": "TEXT", "clean_text": ctext, "position": [0.0, 0.0], "height": 1.0}]
        cases.append(FuzzCase(f"C8_TEXT_CTRL_{name}", cat_id, cat_name, "ControlChars", f"TEXT clean_text has control char {name}", ir))

        ir = _base_ir()
        ir["annotations"] = [{"type": "MTEXT", "raw_text": ctext, "position": [0.0, 0.0], "height": 1.0}]
        cases.append(FuzzCase(f"C8_MTEXT_CTRL_{name}", cat_id, cat_name, "ControlChars", f"MTEXT raw_text has control char {name}", ir))

    # 3. Unmatched & corrupted MTEXT formatting codes
    mtext_codes = [
        ("unclosed_font", r"{\fArial;Bold Text"),
        ("unclosed_color", r"{\C1;Red text"),
        ("unclosed_height", r"{\H2x;Large text"),
        ("unclosed_width", r"{\W0.8;Narrow text"),
        ("unclosed_oblique", r"{\Q15;Oblique text"),
        ("unclosed_tracking", r"{\T2;Tracking text"),
        ("deeply_unclosed_braces", r"{{{{\fArial;Deeply {nested {text"),
        ("extra_closing_braces", r"Text}\fArial;More Text}}}"),
        ("unclosed_underline", r"\LUnderlined text without reset"),
        ("unclosed_overline", r"\OOverlined text without reset"),
        ("unclosed_strikethrough", r"\KStrikethrough text without reset"),
        ("unclosed_stack_align", r"\XStacked without reset"),
        ("corrupted_fraction_caret", r"{\S1^2;"),
        ("corrupted_fraction_hash", r"{\S1#2;"),
        ("corrupted_fraction_slash", r"{\S1/2;"),
        ("escaped_backslash_seq", r"\\P \\~ \\{ \\}"),
    ]
    for name, code in mtext_codes:
        ir = _base_ir()
        ir["annotations"] = [{"type": "MTEXT", "raw_text": code, "position": [0.0, 0.0], "height": 1.0}]
        cases.append(FuzzCase(f"C8_MTEXT_CODE_{name}", cat_id, cat_name, "MTextFormatting", f"MTEXT format code {name}", ir))

    # 4. Extreme text lengths & empty strings
    text_lengths = [
        ("empty", ""),
        ("spaces_only", "      "),
        ("tab_only", "\t\t"),
        ("len_500", "A" * 500),
        ("len_2000", "B" * 2000),
        ("len_10000", "C" * 10000),
    ]
    for name, tlen in text_lengths:
        ir = _base_ir()
        ir["annotations"] = [{"type": "TEXT", "clean_text": tlen, "position": [0.0, 0.0], "height": 1.0}]
        cases.append(FuzzCase(f"C8_TEXT_LEN_{name}", cat_id, cat_name, "TextLength", f"TEXT string length {name}", ir))

    # 5. Annotation heights
    heights = [
        ("zero", 0.0),
        ("neg_zero", -0.0),
        ("neg_one", -1.0),
        ("tiny", 1e-8),
        ("huge", 1e8),
        ("nan", float("nan")),
        ("inf", float("inf")),
    ]
    for name, h in heights:
        ir = _base_ir()
        ir["annotations"] = [{"type": "TEXT", "clean_text": "Sample", "position": [0.0, 0.0], "height": h}]
        cases.append(FuzzCase(f"C8_ANNOT_H_{name}", cat_id, cat_name, "AnnotationHeight", f"Annotation height is {name}", ir))

    # 6. Non-ASCII multilingual scripts, emojis, format strings
    scripts = [
        ("cjk_chinese", "建筑平面图 墙体 门窗"),
        ("cjk_japanese", "建築設計図面 レイヤー0"),
        ("cjk_korean", "건축 도면 레이어"),
        ("arabic_rtl", "مخطط معماري جدار"),
        ("hebrew_rtl", "תוכנית אדריכלית קיר"),
        ("devanagari", "वास्तुशिल्प खाका दीवार"),
        ("greek", "Αρχιτεκτονικό σχέδιο τοίχος"),
        ("cyrillic", "Архитектурный чертеж стена"),
        ("emojis", "📐 📏 🏗️ ⚡ 🔥 🚪 🪟"),
        ("symbols", "© ® ™ € ¥ § ¶ † ‡ •"),
        ("format_injection", "%s %d %x %n {0} {name} ${PATH}"),
        ("sql_injection", "'; DROP TABLE LAYERS; --"),
        ("html_injection", "<script>alert('xss')</script>"),
    ]
    for name, script_text in scripts:
        ir = _base_ir()
        ir["annotations"] = [{"type": "TEXT", "clean_text": script_text, "position": [0.0, 0.0], "height": 1.0}]
        cases.append(FuzzCase(f"C8_SCRIPT_{name}", cat_id, cat_name, "MultilingualScripts", f"Annotation script {name}", ir))

    # 7. Dimensions: text and measurement edge cases
    for idx, (name, script_text) in enumerate(scripts[:10]):
        ir = _base_ir()
        ir["dimensions"] = [{"defpoint": [0.0, 0.0], "measurement": float(idx * 10), "text": script_text}]
        cases.append(FuzzCase(f"C8_DIM_TEXT_{name}", cat_id, cat_name, "DimensionText", f"Dimension text is {name}", ir))

    return cases


def gen_category_9() -> List[FuzzCase]:
    """Category 9: Format & Model Ingestion."""
    cat_id = 9
    cat_name = "Format & Model Ingestion"
    cases: List[FuzzCase] = []

    # 1. Pydantic Models
    if CADIntermediateRepresentation:
        # Pydantic model loaded from valid blueprint sample
        sample_path = Path(__file__).resolve().parent.parent.parent / "cad-extractor-ir" / "examples" / "blueprint_sample_ir.json"
        if sample_path.exists():
            with open(sample_path, encoding="utf-8") as fh:
                raw_data = json.load(fh)
            model_inst = CADIntermediateRepresentation.model_validate(raw_data)
            cases.append(FuzzCase("C9_PYDANTIC_CANONICAL_MODEL", cat_id, cat_name, "PydanticModel", "Canonical CADIntermediateRepresentation instance", model_inst))

    if pydantic:
        # Custom Pydantic models
        class MinimalPydanticIR(pydantic.BaseModel):
            format: str = "LAVINCI_CAD_IR_V3"
            metadata: Dict[str, Any] = {}
            geometry_primitives: Dict[str, Any] = {"primitives": {}}

        class ExtraFieldsPydanticIR(pydantic.BaseModel):
            format: str = "LAVINCI_CAD_IR_V3"
            unexpected_field: str = "danger"
            layers: List[Dict[str, Any]] = []

        cases.append(FuzzCase("C9_PYDANTIC_MINIMAL", cat_id, cat_name, "PydanticModel", "Minimal custom Pydantic BaseModel instance", MinimalPydanticIR()))
        cases.append(FuzzCase("C9_PYDANTIC_EXTRA_FIELDS", cat_id, cat_name, "PydanticModel", "Pydantic BaseModel with unexpected fields", ExtraFieldsPydanticIR()))

    # 2. Format string variations
    format_strings = [
        ("v1", "LAVINCI_CAD_IR_V1"),
        ("v2", "LAVINCI_CAD_IR_V2"),
        ("v4", "LAVINCI_CAD_IR_V4"),
        ("v99", "LAVINCI_CAD_IR_V99"),
        ("dwg", "AUTOCAD_DWG_2024"),
        ("rvt", "REVIT_RVT_2024"),
        ("unknown", "UNKNOWN_FORMAT"),
        ("empty", ""),
        ("none", None),
        ("int_format", 12345),
        ("bool_format", True),
        ("list_format", ["LAVINCI_CAD_IR_V3"]),
    ]
    for name, fmt in format_strings:
        ir = _base_ir()
        ir["format"] = fmt
        cases.append(FuzzCase(f"C9_FORMAT_STR_{name}", cat_id, cat_name, "FormatString", f"Format string is {name}", ir))

    # Missing format key
    ir_no_fmt = _base_ir()
    del ir_no_fmt["format"]
    cases.append(FuzzCase("C9_NO_FORMAT_KEY", cat_id, cat_name, "FormatString", "IR dictionary missing 'format' key", ir_no_fmt))

    # 3. Primitive & Non-Dict Types passed to compile_ir_to_dxf
    primitive_inputs = [
        ("none_input", None),
        ("int_zero", 0),
        ("int_positive", 12345),
        ("int_negative", -999),
        ("float_pi", 3.14159),
        ("bool_true", True),
        ("bool_false", False),
        ("list_empty", []),
        ("list_numbers", [1, 2, 3]),
        ("list_dict", [{"format": "LAVINCI_CAD_IR_V3"}]),
        ("tuple_empty", ()),
        ("tuple_val", (1, 2)),
        ("set_empty", set()),
        ("bytes_input", b'{"format": "LAVINCI_CAD_IR_V3"}'),
    ]
    for name, val in primitive_inputs:
        cases.append(FuzzCase(f"C9_PRIMITIVE_INPUT_{name}", cat_id, cat_name, "PrimitiveInput", f"Input to compile_ir_to_dxf is {name}", val))

    # 4. Invalid JSON string payloads
    json_strings = [
        ("empty_str", ""),
        ("single_brace", "{"),
        ("plain_text", "this is not json"),
        ("single_quotes", "{'format': 'LAVINCI_CAD_IR_V3'}"),
        ("trailing_comma", '{"format": "LAVINCI_CAD_IR_V3",}'),
        ("unclosed_string", '{"format": "LAVINCI'),
        ("truncated_array", '{"layers": ['),
        ("bare_null", "null"),
        ("bare_true", "true"),
        ("bare_int", "12345"),
        ("bare_float", "3.14159"),
        ("bare_array", "[1, 2, 3]"),
        ("valid_minimal_json", '{"format": "LAVINCI_CAD_IR_V3", "geometry_primitives": {"primitives": {}}}'),
    ]
    for name, jstr in json_strings:
        cases.append(FuzzCase(f"C9_JSON_STR_{name}", cat_id, cat_name, "JSONString", f"JSON string input {name}", jstr))

    # 5. Non-existent file paths vs raw string detection
    filepath_inputs = [
        ("nonexistent_json", "non_existent_file_xyz_998877.json"),
        ("path_obj_nonexistent", Path("non_existent_file_xyz_998877.json")),
        ("path_with_null", "path_with\x00null.json"),
        ("long_filename", "A" * 300 + ".json"),
    ]
    for name, fp in filepath_inputs:
        cases.append(FuzzCase(f"C9_FILEPATH_{name}", cat_id, cat_name, "FilePathHandling", f"File path input {name}", fp))

    # 6. DXF Version parameter variations
    version_tests = [
        # Problematic version R12
        ("r12_empty", _base_ir(), {"dxf_version": "R12"}),
        ("r12_lines", _base_ir(), {"dxf_version": "R12"}),
        ("r12_polylines", _base_ir(), {"dxf_version": "R12"}),
        ("r12_mtext", _base_ir(), {"dxf_version": "R12"}),
        ("r12_dimensions", _base_ir(), {"dxf_version": "R12"}),
        # Valid versions
        ("r2000", _base_ir(), {"dxf_version": "R2000"}),
        ("r2004", _base_ir(), {"dxf_version": "R2004"}),
        ("r2007", _base_ir(), {"dxf_version": "R2007"}),
        ("r2010", _base_ir(), {"dxf_version": "R2010"}),
        ("r2013", _base_ir(), {"dxf_version": "R2013"}),
        ("r2018", _base_ir(), {"dxf_version": "R2018"}),
        # Unsupported / invalid versions
        ("ver_r10", _base_ir(), {"dxf_version": "R10"}),
        ("ver_r14", _base_ir(), {"dxf_version": "R14"}),
        ("ver_ac1027", _base_ir(), {"dxf_version": "AC1027"}),
        ("ver_dxf2024", _base_ir(), {"dxf_version": "DXF2024"}),
        ("ver_invalid", _base_ir(), {"dxf_version": "INVALID"}),
        ("ver_empty", _base_ir(), {"dxf_version": ""}),
        ("ver_none", _base_ir(), {"dxf_version": None}),
        ("ver_int", _base_ir(), {"dxf_version": 2013}),
        ("ver_bool", _base_ir(), {"dxf_version": True}),
    ]

    for name, ir_payload, kwargs in version_tests:
        # Populate entities for specific R12 crash tests
        payload = copy.deepcopy(ir_payload)
        if name == "r12_lines":
            payload["geometry_primitives"]["primitives"]["lines"] = [{"start": [0, 0], "end": [10, 10]}]
        elif name == "r12_polylines":
            payload["geometry_primitives"]["primitives"]["polylines"] = [{"points": [[0, 0], [10, 10]]}]
        elif name == "r12_mtext":
            payload["annotations"] = [{"type": "MTEXT", "raw_text": "MTEXT in R12", "position": [0, 0]}]
        elif name == "r12_dimensions":
            payload["dimensions"] = [{"defpoint": [0, 0], "measurement": 10.0}]

        cases.append(FuzzCase(f"C9_VERSION_{name}", cat_id, cat_name, "DXFVersion", f"DXF version test {name}", payload, kwargs))

    return cases


def get_all_fuzz_cases() -> List[FuzzCase]:
    """Aggregate all 9 category generator outputs."""
    all_cases: List[FuzzCase] = []
    all_cases.extend(gen_category_1())
    all_cases.extend(gen_category_2())
    all_cases.extend(gen_category_3())
    all_cases.extend(gen_category_4())
    all_cases.extend(gen_category_5())
    all_cases.extend(gen_category_6())
    all_cases.extend(gen_category_7())
    all_cases.extend(gen_category_8())
    all_cases.extend(gen_category_9())
    return all_cases


# ──────────────────────────────────────────────────────────────────────────────
# Execution Engine & Minimal Reproduction Extraction
# ──────────────────────────────────────────────────────────────────────────────

def format_minimal_repro(case: FuzzCase) -> str:
    """Format payload and invocation as a compact, reproducible Python/JSON snippet."""
    kwargs_str = f", {case.kwargs}" if case.kwargs else ""
    if isinstance(case.payload, dict):
        try:
            payload_str = json.dumps(case.payload, indent=2, default=str)
        except Exception:
            payload_str = repr(case.payload)
    elif isinstance(case.payload, str):
        payload_str = repr(case.payload)
    else:
        payload_str = repr(case.payload)

    snippet = (
        f"# Reproduction for {case.case_id} ({case.description})\n"
        f"from cad_ir_to_dxf.compiler import compile_ir_to_dxf\n\n"
        f"payload = {payload_str}\n"
        f"doc = compile_ir_to_dxf(payload{kwargs_str})\n"
    )
    return snippet


def execute_fuzz_case(case: FuzzCase) -> FuzzResult:
    """Execute a single fuzz permutation with silent stdout/stderr capturing."""
    with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
        try:
            _ = compile_ir_to_dxf(case.payload, **case.kwargs)
            return FuzzResult(case=case, outcome="COMPILED_OR_SANITIZED")
        except Exception as exc:
            exc_type = type(exc).__name__
            exc_msg = str(exc)
            repro = format_minimal_repro(case)
            return FuzzResult(
                case=case,
                outcome="CRASH",
                exception_type=exc_type,
                exception_message=exc_msg,
                minimal_repro=repro,
            )


def run_fuzz_campaign(cases: Optional[List[FuzzCase]] = None) -> HarnessStats:
    """Execute the fuzz campaign across all provided (or all generated) cases."""
    if cases is None:
        cases = get_all_fuzz_cases()

    stats = HarnessStats()
    stats.total_executed = len(cases)

    for case in cases:
        stats.category_counts[case.category_id] = stats.category_counts.get(case.category_id, 0) + 1

        result = execute_fuzz_case(case)
        stats.results.append(result)

        if result.outcome == "COMPILED_OR_SANITIZED":
            stats.total_sanitized += 1
            stats.category_sanitized[case.category_id] = stats.category_sanitized.get(case.category_id, 0) + 1
        else:
            stats.total_crashes += 1
            stats.category_crashes[case.category_id] = stats.category_crashes.get(case.category_id, 0) + 1

            etype = result.exception_type or "UnknownException"
            stats.crash_type_counts[etype] = stats.crash_type_counts.get(etype, 0) + 1

            # Group into unique crash vectors by category + exception_type + short message
            msg_key = (result.exception_message or "")[:60]
            vector_key = f"CAT{case.category_id}_{etype}_{msg_key}"
            if vector_key not in stats.crash_vectors:
                vid = f"CV-{len(stats.crash_vectors) + 1:02d}"
                stats.crash_vectors[vector_key] = CrashVector(
                    vector_id=vid,
                    category_id=case.category_id,
                    category_name=case.category_name,
                    exception_type=etype,
                    exception_message=result.exception_message or "",
                    root_cause=f"{case.category_name} -> {case.subcategory}: {case.description}",
                    minimal_repro=result.minimal_repro or "",
                    occurrence_count=1,
                    sample_case_ids=[case.case_id],
                )
            else:
                cv = stats.crash_vectors[vector_key]
                cv.occurrence_count += 1
                if len(cv.sample_case_ids) < 5:
                    cv.sample_case_ids.append(case.case_id)

    return stats


# ──────────────────────────────────────────────────────────────────────────────
# Standalone CLI Report Formatting
# ──────────────────────────────────────────────────────────────────────────────

CATEGORY_NAMES = {
    1: "Extreme & Non-Finite Floats",
    2: "Coordinate Dimensionality Anomalies",
    3: "Type Mismatches",
    4: "Graph Cycles & Block Recursion",
    5: "Symbol Table Illegal Characters",
    6: "Top-Level & Section Nulls",
    7: "Scale & Transformation Edge Cases",
    8: "Character Encodings & Annotations",
    9: "Format & Model Ingestion",
}


def print_ascii_summary(stats: HarnessStats) -> None:
    """Print an ASCII summary table of the fuzzing harness execution."""
    print("\n" + "=" * 80)
    print("       CAD-IR-TO-DXF ADVERSARIAL FUZZING HARNESS EXECUTION REPORT")
    print("=" * 80)
    print(f"Total Permutations Executed: {stats.total_executed:,} (Requirement >= 1,000: {'PASSED' if stats.total_executed >= 1000 else 'FAILED'})")
    print(f"Graceful Sanitizations / Compiled: {stats.total_sanitized:,} ({stats.total_sanitized / max(1, stats.total_executed) * 100:.1f}%)")
    print(f"Unhandled Crashes / Exceptions:   {stats.total_crashes:,} ({stats.total_crashes / max(1, stats.total_executed) * 100:.1f}%)")
    print(f"Unique Crash Vectors Discovered:  {len(stats.crash_vectors)}")
    print("-" * 80)

    print(f"{'Cat':<4} | {'Category Name':<38} | {'Total':<7} | {'Sanitized':<9} | {'Crashes':<7}")
    print("-" * 80)
    for cat_id in range(1, 10):
        cname = CATEGORY_NAMES.get(cat_id, "Unknown")
        total = stats.category_counts.get(cat_id, 0)
        san = stats.category_sanitized.get(cat_id, 0)
        cra = stats.category_crashes.get(cat_id, 0)
        print(f"{cat_id:<4} | {cname:<38} | {total:<7} | {san:<9} | {cra:<7}")
    print("-" * 80)

    print("\nUNHANDLED EXCEPTION BREAKDOWN BY TYPE:")
    print("-" * 50)
    for exc_type, count in sorted(stats.crash_type_counts.items(), key=lambda x: -x[1]):
        pct = count / max(1, stats.total_crashes) * 100
        print(f"  - {exc_type:<25} : {count:>5} ({pct:>5.1f}%)")
    print("-" * 50)

    print("\nDISCOVERED UNIQUE CRASH VECTORS (SAMPLE):")
    print("-" * 80)
    for cv in list(stats.crash_vectors.values())[:10]:
        print(f"[{cv.vector_id}] Cat {cv.category_id} ({cv.exception_type}): {cv.exception_message[:60]}")
        print(f"     Root cause: {cv.root_cause}")
        print(f"     Occurrences: {cv.occurrence_count} (e.g. {', '.join(cv.sample_case_ids[:2])})")
    print("-" * 80)


# ──────────────────────────────────────────────────────────────────────────────
# Global Harness Result Cache for Unittest
# ──────────────────────────────────────────────────────────────────────────────

_GLOBAL_STATS: Optional[HarnessStats] = None


def get_or_run_campaign() -> HarnessStats:
    global _GLOBAL_STATS
    if _GLOBAL_STATS is None:
        _GLOBAL_STATS = run_fuzz_campaign()
    return _GLOBAL_STATS


# ──────────────────────────────────────────────────────────────────────────────
# Unittest Test Suite
# ──────────────────────────────────────────────────────────────────────────────

class TestFuzzMalformedHarness(unittest.TestCase):
    """
    Automated Adversarial Fuzzing Test Suite verifying input resilience,
    fuzzing coverage across all 9 categories, and crash classification integrity.
    """

    @classmethod
    def setUpClass(cls):
        cls.stats = get_or_run_campaign()

    def test_01_extreme_and_non_finite_floats(self):
        """Category 1: Verify extreme & non-finite float permutations."""
        count = self.stats.category_counts.get(1, 0)
        self.assertGreaterEqual(count, 100, f"Cat 1 count {count} is below threshold 100")

    def test_02_coordinate_dimensionality(self):
        """Category 2: Verify coordinate dimensionality anomaly permutations."""
        count = self.stats.category_counts.get(2, 0)
        self.assertGreaterEqual(count, 50, f"Cat 2 count {count} is below threshold 50")

    def test_03_type_mismatches(self):
        """Category 3: Verify type mismatch permutations."""
        count = self.stats.category_counts.get(3, 0)
        self.assertGreaterEqual(count, 50, f"Cat 3 count {count} is below threshold 50")

    def test_04_graph_cycles_and_recursion(self):
        """Category 4: Verify graph cycles & block recursion permutations."""
        count = self.stats.category_counts.get(4, 0)
        self.assertGreaterEqual(count, 50, f"Cat 4 count {count} is below threshold 50")

    def test_05_symbol_table_illegal_characters(self):
        """Category 5: Verify symbol table illegal character permutations."""
        count = self.stats.category_counts.get(5, 0)
        self.assertGreaterEqual(count, 50, f"Cat 5 count {count} is below threshold 50")

    def test_06_top_level_and_section_nulls(self):
        """Category 6: Verify top-level & section null permutations."""
        count = self.stats.category_counts.get(6, 0)
        self.assertGreaterEqual(count, 50, f"Cat 6 count {count} is below threshold 50")

    def test_07_scale_and_transformation(self):
        """Category 7: Verify scale & transformation edge cases."""
        count = self.stats.category_counts.get(7, 0)
        self.assertGreaterEqual(count, 50, f"Cat 7 count {count} is below threshold 50")

    def test_08_character_encodings_and_annotations(self):
        """Category 8: Verify character encodings & annotation permutations."""
        count = self.stats.category_counts.get(8, 0)
        self.assertGreaterEqual(count, 50, f"Cat 8 count {count} is below threshold 50")

    def test_09_format_and_model_ingestion(self):
        """Category 9: Verify format & model ingestion permutations."""
        count = self.stats.category_counts.get(9, 0)
        self.assertGreaterEqual(count, 50, f"Cat 9 count {count} is below threshold 50")

    def test_10_aggregate_thresholds_and_taxonomy(self):
        """Milestone 1 Acceptance Verification: >= 1,000 permutations & crash taxonomy."""
        total = self.stats.total_executed
        self.assertGreaterEqual(total, 1000, f"Total permutations {total} must be >= 1,000")

        # Verify all 9 categories covered
        self.assertEqual(len(self.stats.category_counts), 9, "All 9 categories must be evaluated")

        # Verify crash taxonomy includes identified exception types
        for expected_exc in ["TypeError", "IndexError", "AttributeError", "DXFValueError", "DXFVersionError"]:
            self.assertIn(expected_exc, self.stats.crash_type_counts, f"Expected crash type '{expected_exc}' was not recorded")

        # Verify crash vectors have minimal reproduction snippets
        self.assertGreater(len(self.stats.crash_vectors), 0, "Crash vectors must be populated")
        for cv in self.stats.crash_vectors.values():
            self.assertTrue(len(cv.minimal_repro) > 0, f"Crash vector {cv.vector_id} missing minimal reproduction")


# ──────────────────────────────────────────────────────────────────────────────
# Standalone Main
# ──────────────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] in ("-v", "--verbose", "-q", "--quiet"):
        unittest.main()
    else:
        stats = get_or_run_campaign()
        print_ascii_summary(stats)
        print("\nExecuting verification unittest suite...\n")
        suite = unittest.TestLoader().loadTestsFromTestCase(TestFuzzMalformedHarness)
        runner = unittest.TextTestRunner(verbosity=2)
        test_result = runner.run(suite)
        sys.exit(0 if test_result.wasSuccessful() else 1)

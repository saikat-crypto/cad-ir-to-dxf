"""
tests/test_scale_stress.py — Scale, Performance & Stress Profiling Benchmark Suite.

Authoritative Benchmark & Stress Test Harness for `cad-ir-to-dxf`.
Measures compilation wall time, entity throughput, ezdxf audit validation time,
serialization time, peak heap memory (tracemalloc), process RSS delta (psutil),
and DXF file size across 10,000, 50,000, and 100,000 entity scale tiers,
block explosions (200+ blocks, 10,000+ insertions), and deep block hierarchies (depth 30-50).
Evaluates asymptotic scaling linearity (O(N) vs O(N^2)) and checks for complexity traps.

Execution modes:
  1. Standalone benchmark:
     $env:PYTHONPATH="src"; python tests/test_scale_stress.py
  2. Unittest test runner:
     $env:PYTHONPATH="src"; python -m unittest tests.test_scale_stress
"""

from __future__ import annotations

import argparse
from dataclasses import asdict, dataclass
import gc
import json
import math
import os
from pathlib import Path
import sys
import tempfile
import time
import tracemalloc
from typing import Any, Dict, List, Optional, Tuple
import unittest

import ezdxf
from ezdxf.audit import AuditError, Auditor
try:
    import psutil
except ImportError:
    psutil = None

from cad_ir_to_dxf.compiler import compile_ir_to_dxf


# ──────────────────────────────────────────────────────────────────────────────
# Data Structures
# ──────────────────────────────────────────────────────────────────────────────

@dataclass
class BenchmarkMetrics:
    name: str
    entity_count: int
    topology: str
    compile_time_s: float
    compile_throughput_eps: float
    audit_time_s: float
    audit_throughput_eps: float
    serialize_time_s: float
    serialize_throughput_eps: float
    total_wall_time_s: float
    peak_heap_mb: float
    rss_delta_mb: float
    dxf_size_mb: float
    audit_errors: int
    audit_fixes: int
    memory_per_entity_bytes: float
    details: Dict[str, Any]

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


# ──────────────────────────────────────────────────────────────────────────────
# Synthetic IR Generators
# ──────────────────────────────────────────────────────────────────────────────

def generate_dense_primitive_soup(
    n_entities: int,
    num_layers: int = 10,
    seed: int = 42,
) -> Dict[str, Any]:
    """
    Generate a high-density synthetic drawing containing `n_entities` primitives.

    Distribution:
      - 35% Lines (connecting spatial grid points)
      - 25% Circles (varying radii, centered across the envelope)
      - 20% Arcs (sweep angles, spanning quadrants)
      - 20% Polylines (closed and open multi-segment polylines)
    Distributed across `num_layers` architectural layers with alternating
    BYLAYER (None) and TrueColor hex overrides.
    """
    layer_names = [
        "WALLS", "DOORS", "WINDOWS", "FURNITURE", "ELECTRICAL",
        "PLUMBING", "HVAC", "ANNOTATIONS", "STRUCTURE", "SITE",
    ][:num_layers]

    linetypes = ["Continuous", "CENTER", "DASHED", "HIDDEN", "PHANTOM"]
    layers = []
    for idx, name in enumerate(layer_names):
        layers.append({
            "name": name,
            "color_aci": (idx % 7) + 1,
            "linetype": linetypes[idx % len(linetypes)],
            "is_off": False,
            "is_frozen": False,
            "is_locked": False,
        })

    # Primitive counts
    n_lines = int(n_entities * 0.35)
    n_circles = int(n_entities * 0.25)
    n_arcs = int(n_entities * 0.20)
    n_polys = n_entities - (n_lines + n_circles + n_arcs)

    hex_palette = ["#FF5733", "#33FF57", "#3357FF", "#F3FF33", "#FF33F3", None]

    lines: List[Dict[str, Any]] = []
    for i in range(n_lines):
        x = float((i * 13) % 2000)
        y = float((i * 17) % 2000)
        dx = float(((i % 50) + 1) * 2.0)
        dy = float(((i % 40) + 1) * 2.0)
        layer = layer_names[i % len(layer_names)]
        color = hex_palette[i % len(hex_palette)]
        lines.append({
            "start": [x, y],
            "end": [x + dx, y + dy],
            "layer": layer,
            "space": "Model",
            "color": color,
        })

    circles: List[Dict[str, Any]] = []
    for i in range(n_circles):
        x = float((i * 19) % 2000)
        y = float((i * 23) % 2000)
        radius = float((i % 25) + 1.5)
        layer = layer_names[(i + 1) % len(layer_names)]
        color = hex_palette[(i + 1) % len(hex_palette)]
        circles.append({
            "center": [x, y],
            "radius": radius,
            "layer": layer,
            "space": "Model",
            "color": color,
        })

    arcs: List[Dict[str, Any]] = []
    for i in range(n_arcs):
        x = float((i * 29) % 2000)
        y = float((i * 31) % 2000)
        radius = float((i % 30) + 2.0)
        start_angle = float((i * 30) % 360)
        end_angle = float((start_angle + 60.0 + (i % 180)) % 360)
        layer = layer_names[(i + 2) % len(layer_names)]
        color = hex_palette[(i + 2) % len(hex_palette)]
        arcs.append({
            "center": [x, y],
            "radius": radius,
            "start_angle": start_angle,
            "end_angle": end_angle,
            "layer": layer,
            "space": "Model",
            "color": color,
        })

    polylines: List[Dict[str, Any]] = []
    for i in range(n_polys):
        x0 = float((i * 37) % 2000)
        y0 = float((i * 41) % 2000)
        step = float((i % 15) + 2.0)
        pts = [
            [x0, y0],
            [x0 + step, y0],
            [x0 + step, y0 + step],
            [x0, y0 + step],
        ]
        layer = layer_names[(i + 3) % len(layer_names)]
        color = hex_palette[(i + 3) % len(hex_palette)]
        polylines.append({
            "points": pts,
            "is_closed": (i % 2 == 0),
            "layer": layer,
            "space": "Model",
            "color": color,
        })

    return {
        "format": "LAVINCI_CAD_IR_V3",
        "metadata": {
            "source_file": f"synthetic_soup_{n_entities}.dwg",
            "units": 4,  # Millimeters
            "measurement_system": "Metric",
            "author": "ScaleStressHarness",
        },
        "extents": {
            "min": [0.0, 0.0],
            "max": [2200.0, 2200.0],
            "width": 2200.0,
            "height": 2200.0,
        },
        "layouts": [{"name": "Model", "is_active": True}],
        "layers": layers,
        "geometry_primitives": {
            "summary": {
                "total_lines": len(lines),
                "total_arcs": len(arcs),
                "total_circles": len(circles),
                "total_polylines": len(polylines),
                "total_components": 0,
                "total_annotations": 0,
                "total_dimensions": 0,
                "total_block_definitions": 0,
            },
            "primitives": {
                "lines": lines,
                "arcs": arcs,
                "circles": circles,
                "polylines": polylines,
            },
        },
        "block_definitions": {},
        "components": [],
        "annotations": [],
        "dimensions": [],
    }


def generate_block_explosion(
    num_blocks: int = 250,
    num_inserts: int = 10000,
    seed: int = 42,
) -> Dict[str, Any]:
    """
    Generate a drawing with hundreds of unique block definitions and thousands
    of INSERT references with varying coordinates, scales, rotations, and attributes.
    """
    block_defs: Dict[str, Any] = {}
    for b in range(num_blocks):
        bname = f"EQUIP_BLOCK_{b:03d}"
        block_defs[bname] = {
            "name": bname,
            "base_point": [0.0, 0.0, 0.0],
            "lines": [
                {"start": [-2.0, -2.0], "end": [2.0, -2.0], "layer": "0"},
                {"start": [2.0, -2.0], "end": [2.0, 2.0], "layer": "0"},
                {"start": [2.0, 2.0], "end": [-2.0, 2.0], "layer": "0"},
                {"start": [-2.0, 2.0], "end": [-2.0, -2.0], "layer": "0"},
                {"start": [-2.0, -2.0], "end": [2.0, 2.0], "layer": "0"},
            ],
            "circles": [
                {"center": [0.0, 0.0], "radius": 0.75, "layer": "0"},
            ],
            "arcs": [
                {"center": [0.0, 0.0], "radius": 1.5, "start_angle": 0.0, "end_angle": 90.0, "layer": "0"},
            ],
            "polylines": [],
        }

    components: List[Dict[str, Any]] = []
    rotations = [0.0, 45.0, 90.0, 135.0, 180.0, 225.0, 270.0, 315.0]
    scales = [
        [1.0, 1.0, 1.0],
        [0.5, 0.5, 0.5],
        [1.5, 1.5, 1.5],
        [2.0, 2.0, 2.0],
        [-1.0, 1.0, 1.0],  # Mirrored X
    ]

    for i in range(num_inserts):
        bname = f"EQUIP_BLOCK_{i % num_blocks:03d}"
        col = i % 100
        row = i // 100
        x = float(col * 15.0)
        y = float(row * 15.0)
        rot = rotations[i % len(rotations)]
        scale = scales[i % len(scales)]
        layer = f"COMP_LAYER_{i % 6}"
        components.append({
            "block_name": bname,
            "resolved_name": f"{bname}_INST",
            "position": [x, y, 0.0],
            "rotation": rot,
            "scale": scale,
            "layer": layer,
            "space": "Model",
            "attributes": {
                "TAG": f"TAG-{i:05d}",
                "PANEL": f"PNL-{(i // 100) + 1}",
                "VOLTS": "240V" if (i % 2 == 0) else "120V",
            },
        })

    layers = [
        {"name": "0", "color_aci": 7},
        {"name": "COMP_LAYER_0", "color_aci": 1},
        {"name": "COMP_LAYER_1", "color_aci": 2},
        {"name": "COMP_LAYER_2", "color_aci": 3},
        {"name": "COMP_LAYER_3", "color_aci": 4},
        {"name": "COMP_LAYER_4", "color_aci": 5},
        {"name": "COMP_LAYER_5", "color_aci": 6},
    ]

    return {
        "format": "LAVINCI_CAD_IR_V3",
        "metadata": {
            "source_file": "synthetic_block_explosion.dwg",
            "units": 4,
            "measurement_system": "Metric",
            "author": "ScaleStressHarness",
        },
        "extents": {
            "min": [0.0, 0.0],
            "max": [1600.0, 1600.0],
            "width": 1600.0,
            "height": 1600.0,
        },
        "layouts": [{"name": "Model", "is_active": True}],
        "layers": layers,
        "block_definitions": block_defs,
        "components": components,
        "geometry_primitives": {
            "summary": {
                "total_lines": 0,
                "total_arcs": 0,
                "total_circles": 0,
                "total_polylines": 0,
                "total_components": len(components),
                "total_annotations": 0,
                "total_dimensions": 0,
                "total_block_definitions": len(block_defs),
            },
            "primitives": {
                "lines": [],
                "arcs": [],
                "circles": [],
                "polylines": [],
            },
        },
        "annotations": [],
        "dimensions": [],
    }


def generate_deep_block_hierarchy_ir(depth: int = 30) -> Dict[str, Any]:
    """
    Generate an IR with a chain of `depth` block definitions.
    Tests compiler resolution, dictionary storage, and root modelspace insertion.
    """
    block_defs: Dict[str, Any] = {}
    for i in range(depth):
        bname = f"CHAIN_BLK_{i:02d}"
        block_defs[bname] = {
            "name": bname,
            "base_point": [0.0, 0.0, 0.0],
            "lines": [
                {"start": [0.0, 0.0], "end": [float(i + 1), float(i + 1)], "layer": "0"},
            ],
            "circles": [
                {"center": [0.0, 0.0], "radius": float(i + 1) * 0.5, "layer": "0"},
            ],
            "arcs": [],
            "polylines": [],
        }

    # Root component inserting the leaf block
    components = [
        {
            "block_name": f"CHAIN_BLK_{depth - 1:02d}",
            "position": [0.0, 0.0, 0.0],
            "rotation": 0.0,
            "scale": [1.0, 1.0, 1.0],
            "layer": "0",
            "space": "Model",
            "attributes": {"DEPTH": str(depth)},
        }
    ]

    return {
        "format": "LAVINCI_CAD_IR_V3",
        "metadata": {
            "source_file": f"deep_block_hierarchy_{depth}.dwg",
            "units": 4,
            "measurement_system": "Metric",
            "author": "ScaleStressHarness",
        },
        "extents": {"min": [0.0, 0.0], "max": [100.0, 100.0]},
        "layouts": [{"name": "Model", "is_active": True}],
        "layers": [{"name": "0", "color_aci": 7}],
        "block_definitions": block_defs,
        "components": components,
        "geometry_primitives": {
            "summary": {
                "total_lines": 0,
                "total_arcs": 0,
                "total_circles": 0,
                "total_polylines": 0,
                "total_components": 1,
                "total_annotations": 0,
                "total_dimensions": 0,
                "total_block_definitions": depth,
            },
            "primitives": {"lines": [], "arcs": [], "circles": [], "polylines": []},
        },
        "annotations": [],
        "dimensions": [],
    }


def build_deep_block_hierarchy_dxf(depth: int = 30) -> ezdxf.document.Drawing:
    """
    Construct a true nested block reference tree directly in an ezdxf Drawing:
    Block(N) inserts Block(N-1) ... inserts Block(0).
    Allows measuring ezdxf audit recursion, cycle detection, and serialization on deep trees.
    """
    doc = ezdxf.new("R2013", setup=True)
    prev_name = "DEEP_0"
    b0 = doc.blocks.new(name=prev_name, base_point=(0, 0, 0))
    b0.add_line((0, 0), (1, 1))

    for d in range(1, depth):
        curr_name = f"DEEP_{d}"
        blk = doc.blocks.new(name=curr_name, base_point=(0, 0, 0))
        blk.add_line((0, 0), (float(d), float(d)))
        blk.add_blockref(prev_name, insert=(0, 0))
        prev_name = curr_name

    doc.modelspace().add_blockref(prev_name, insert=(10, 10))
    return doc


# ──────────────────────────────────────────────────────────────────────────────
# Profiling & Measurement Engine
# ──────────────────────────────────────────────────────────────────────────────

def profile_ir_compilation(
    ir_payload: Dict[str, Any],
    name: str,
    topology: str,
    entity_count: int,
) -> Tuple[ezdxf.document.Drawing, BenchmarkMetrics]:
    """
    Execute full multi-phase profiling of CAD-IR compilation, ezdxf audit,
    and DXF serialization with high-precision metrics.
    """
    gc.collect()

    if psutil is not None:
        process = psutil.Process(os.getpid())
        rss_start = process.memory_info().rss
    else:
        process = None
        rss_start = 0

    # Phase 1: Compile IR -> DXF
    tracemalloc.start()
    t_start = time.perf_counter()

    doc = compile_ir_to_dxf(ir_payload)

    t_compile_end = time.perf_counter()
    _, peak_heap_bytes = tracemalloc.get_traced_memory()
    tracemalloc.stop()

    rss_after_compile = process.memory_info().rss if process is not None else 0
    compile_time_s = max(t_compile_end - t_start, 1e-6)

    # Phase 2: ezdxf Audit
    t_audit_start = time.perf_counter()
    auditor = doc.audit()
    t_audit_end = time.perf_counter()
    audit_time_s = max(t_audit_end - t_audit_start, 1e-6)

    # Phase 3: DXF File Serialization to Disk
    t_save_start = time.perf_counter()
    temp_fd, temp_path = tempfile.mkstemp(suffix=".dxf", prefix="cad_stress_")
    os.close(temp_fd)

    try:
        doc.saveas(temp_path)
        t_save_end = time.perf_counter()
        save_time_s = max(t_save_end - t_save_start, 1e-6)
        file_size_bytes = os.path.getsize(temp_path)
    finally:
        if os.path.exists(temp_path):
            try:
                os.remove(temp_path)
            except OSError:
                pass

    total_wall_time_s = compile_time_s + audit_time_s + save_time_s
    rss_delta_bytes = max(rss_after_compile - rss_start, 0)

    metrics = BenchmarkMetrics(
        name=name,
        entity_count=entity_count,
        topology=topology,
        compile_time_s=compile_time_s,
        compile_throughput_eps=entity_count / compile_time_s,
        audit_time_s=audit_time_s,
        audit_throughput_eps=entity_count / audit_time_s,
        serialize_time_s=save_time_s,
        serialize_throughput_eps=entity_count / save_time_s,
        total_wall_time_s=total_wall_time_s,
        peak_heap_mb=peak_heap_bytes / (1024 * 1024),
        rss_delta_mb=rss_delta_bytes / (1024 * 1024),
        dxf_size_mb=file_size_bytes / (1024 * 1024),
        audit_errors=len(auditor.errors),
        audit_fixes=len(auditor.fixes),
        memory_per_entity_bytes=peak_heap_bytes / max(entity_count, 1),
        details={
            "error_codes": [int(e.code) for e in auditor.errors],
            "fix_codes": [int(f.code) for f in auditor.fixes],
        },
    )

    return doc, metrics


def profile_deep_dxf_tree(depth: int = 30) -> BenchmarkMetrics:
    """
    Profile audit and save operations on an in-memory deep block hierarchy.
    """
    gc.collect()
    if psutil is not None:
        process = psutil.Process(os.getpid())
        rss_start = process.memory_info().rss
    else:
        process = None
        rss_start = 0

    tracemalloc.start()
    t_start = time.perf_counter()
    doc = build_deep_block_hierarchy_dxf(depth=depth)
    t_build = time.perf_counter()
    _, peak_heap_bytes = tracemalloc.get_traced_memory()
    tracemalloc.stop()

    t_audit_start = time.perf_counter()
    auditor = doc.audit()
    t_audit_end = time.perf_counter()

    t_save_start = time.perf_counter()
    temp_fd, temp_path = tempfile.mkstemp(suffix=".dxf", prefix="cad_deep_")
    os.close(temp_fd)
    try:
        doc.saveas(temp_path)
        t_save_end = time.perf_counter()
        file_size_bytes = os.path.getsize(temp_path)
    finally:
        if os.path.exists(temp_path):
            try:
                os.remove(temp_path)
            except OSError:
                pass

    rss_end = process.memory_info().rss if process is not None else 0
    compile_time = max(t_build - t_start, 1e-6)
    audit_time = max(t_audit_end - t_audit_start, 1e-6)
    save_time = max(t_save_end - t_save_start, 1e-6)

    return BenchmarkMetrics(
        name=f"Deep Hierarchy (Depth {depth})",
        entity_count=depth,
        topology="Deep Block Reference Chain",
        compile_time_s=compile_time,
        compile_throughput_eps=depth / compile_time,
        audit_time_s=audit_time,
        audit_throughput_eps=depth / audit_time,
        serialize_time_s=save_time,
        serialize_throughput_eps=depth / save_time,
        total_wall_time_s=compile_time + audit_time + save_time,
        peak_heap_mb=peak_heap_bytes / (1024 * 1024),
        rss_delta_mb=max(rss_end - rss_start, 0) / (1024 * 1024),
        dxf_size_mb=file_size_bytes / (1024 * 1024),
        audit_errors=len(auditor.errors),
        audit_fixes=len(auditor.fixes),
        memory_per_entity_bytes=peak_heap_bytes / max(depth, 1),
        details={
            "depth": depth,
            "error_codes": [int(e.code) for e in auditor.errors],
            "fix_codes": [int(f.code) for f in auditor.fixes],
        },
    )


# ──────────────────────────────────────────────────────────────────────────────
# Asymptotic Linearity Analysis
# ──────────────────────────────────────────────────────────────────────────────

def evaluate_scaling_linearity(
    m_10k: BenchmarkMetrics,
    m_50k: BenchmarkMetrics,
    m_100k: BenchmarkMetrics,
) -> Dict[str, Any]:
    """
    Evaluate empirical asymptotic scaling linearity (O(N) vs O(N^2)).
    Calculates empirical scaling exponent alpha where Time ~ N^alpha.
    """
    # Exponent alpha = ln(T2 / T1) / ln(N2 / N1)
    alpha_10_to_50 = math.log(m_50k.compile_time_s / m_10k.compile_time_s) / math.log(
        m_50k.entity_count / m_10k.entity_count
    )
    alpha_50_to_100 = math.log(m_100k.compile_time_s / m_50k.compile_time_s) / math.log(
        m_100k.entity_count / m_50k.entity_count
    )
    alpha_10_to_100 = math.log(m_100k.compile_time_s / m_10k.compile_time_s) / math.log(
        m_100k.entity_count / m_10k.entity_count
    )

    ratio_10_to_50 = m_50k.compile_time_s / m_10k.compile_time_s
    ratio_50_to_100 = m_100k.compile_time_s / m_50k.compile_time_s
    ratio_10_to_100 = m_100k.compile_time_s / m_10k.compile_time_s

    # Memory scaling linearity
    mem_ratio_10_to_50 = m_50k.peak_heap_mb / max(m_10k.peak_heap_mb, 1e-3)
    mem_ratio_10_to_100 = m_100k.peak_heap_mb / max(m_10k.peak_heap_mb, 1e-3)

    is_linear = alpha_10_to_100 < 1.30
    is_quadratic = alpha_10_to_100 >= 1.80

    complexity_class = "O(N) Linear" if is_linear else ("O(N^2) Quadratic" if is_quadratic else "O(N log N) Super-Linear")

    return {
        "alpha_10k_to_50k": alpha_10_to_50,
        "alpha_50k_to_100k": alpha_50_to_100,
        "alpha_overall": alpha_10_to_100,
        "time_ratio_10k_to_50k": ratio_10_to_50,
        "time_ratio_50k_to_100k": ratio_50_to_100,
        "time_ratio_overall": ratio_10_to_100,
        "expected_linear_overall": 10.0,
        "expected_quadratic_overall": 100.0,
        "mem_ratio_10k_to_50k": mem_ratio_10_to_50,
        "mem_ratio_overall": mem_ratio_10_to_100,
        "bytes_per_ent_10k": m_10k.memory_per_entity_bytes,
        "bytes_per_ent_50k": m_50k.memory_per_entity_bytes,
        "bytes_per_ent_100k": m_100k.memory_per_entity_bytes,
        "complexity_classification": complexity_class,
        "is_linear": is_linear,
        "has_exponential_trap": alpha_10_to_100 > 2.0,
    }


# ──────────────────────────────────────────────────────────────────────────────
# Formatting & Presentation
# ──────────────────────────────────────────────────────────────────────────────

def format_benchmark_table(results: List[BenchmarkMetrics]) -> str:
    """Format benchmark results into an ASCII table."""
    headers = [
        "Tier / Topology",
        "Entities",
        "Compile (s)",
        "Comp ent/s",
        "Audit (s)",
        "Save (s)",
        "Peak Heap (MB)",
        "RSS Delta (MB)",
        "DXF Size (MB)",
        "Audit Status",
    ]
    rows = []
    for r in results:
        audit_str = f"OK (0/{r.audit_fixes})" if r.audit_errors == 0 else f"ERR: {r.audit_errors}"
        rows.append([
            r.name,
            f"{r.entity_count:,}",
            f"{r.compile_time_s:.3f}",
            f"{r.compile_throughput_eps:,.1f}",
            f"{r.audit_time_s:.3f}",
            f"{r.serialize_time_s:.3f}",
            f"{r.peak_heap_mb:.2f}",
            f"{r.rss_delta_mb:.2f}",
            f"{r.dxf_size_mb:.2f}",
            audit_str,
        ])

    col_widths = [len(h) for h in headers]
    for row in rows:
        for i, val in enumerate(row):
            col_widths[i] = max(col_widths[i], len(val))

    def make_row(items: List[str]) -> str:
        return "| " + " | ".join(item.ljust(col_widths[i]) for i, item in enumerate(items)) + " |"

    sep = "|-" + "-|-".join("-" * w for w in col_widths) + "-|"

    lines = [sep, make_row(headers), sep]
    for row in rows:
        lines.append(make_row(row))
    lines.append(sep)
    return "\n".join(lines)


def format_scaling_table(scaling: Dict[str, Any]) -> str:
    """Format scaling linearity analysis into an ASCII table."""
    lines = [
        "+------------------------------------+-------------+----------------+",
        "| Metric / Scale Transition          | Observed    | Expected O(N)  |",
        "+------------------------------------+-------------+----------------+",
        f"| Scaling Exponent (10k -> 50k)      | {scaling['alpha_10k_to_50k']:.4f}      | 1.0000 (Linear)|",
        f"| Scaling Exponent (50k -> 100k)     | {scaling['alpha_50k_to_100k']:.4f}      | 1.0000 (Linear)|",
        f"| Scaling Exponent (Overall 10x)     | {scaling['alpha_overall']:.4f}      | 1.0000 (Linear)|",
        f"| Time Ratio T(50k) / T(10k)         | {scaling['time_ratio_10k_to_50k']:.2f}x        | 5.00x (Linear) |",
        f"| Time Ratio T(100k) / T(50k)        | {scaling['time_ratio_50k_to_100k']:.2f}x        | 2.00x (Linear) |",
        f"| Time Ratio T(100k) / T(10k)        | {scaling['time_ratio_overall']:.2f}x        | 10.00x (Linear)|",
        f"| Memory Ratio Heap(50k)/Heap(10k)   | {scaling['mem_ratio_10k_to_50k']:.2f}x        | 5.00x (Linear) |",
        f"| Memory Ratio Heap(100k)/Heap(10k)  | {scaling['mem_ratio_overall']:.2f}x        | 10.00x (Linear)|",
        f"| Heap Intensity @ 10k entities      | {scaling['bytes_per_ent_10k']:.1f} B/ent   | Constant       |",
        f"| Heap Intensity @ 50k entities      | {scaling['bytes_per_ent_50k']:.1f} B/ent   | Constant       |",
        f"| Heap Intensity @ 100k entities     | {scaling['bytes_per_ent_100k']:.1f} B/ent   | Constant       |",
        f"| Complexity Classification          | {scaling['complexity_classification']}   | O(N)           |",
        f"| Exponential Complexity Trap?       | {'NO' if not scaling['has_exponential_trap'] else 'YES (CRITICAL)'}          | NO             |",
        "+------------------------------------+-------------+----------------+",
    ]
    return "\n".join(lines)


# ──────────────────────────────────────────────────────────────────────────────
# Standalone Benchmark Execution
# ──────────────────────────────────────────────────────────────────────────────

def run_benchmark_suite(
    tiers: Optional[List[str]] = None,
    json_out_path: Optional[str] = None,
) -> Tuple[List[BenchmarkMetrics], Dict[str, Any]]:
    """
    Execute the benchmark suite across requested tiers and topologies.
    """
    tiers = tiers or ["10k", "50k", "100k", "blocks", "deep"]
    results: List[BenchmarkMetrics] = []

    print("=" * 80)
    print("CAD-IR-TO-DXF SCALE, PERFORMANCE & STRESS PROFILING BENCHMARK")
    print("=" * 80)

    m_10k: Optional[BenchmarkMetrics] = None
    m_50k: Optional[BenchmarkMetrics] = None
    m_100k: Optional[BenchmarkMetrics] = None

    if "10k" in tiers:
        print("[1/5] Generating and profiling Tier A: 10,000 Mixed Primitive Soup...")
        ir_10k = generate_dense_primitive_soup(10000, num_layers=10, seed=10)
        _, m_10k = profile_ir_compilation(ir_10k, "Tier A (10k)", "Mixed Primitive Soup", 10000)
        results.append(m_10k)
        print(f"      Compile: {m_10k.compile_time_s:.3f}s ({m_10k.compile_throughput_eps:,.1f} ent/s) | "
              f"Audit: {m_10k.audit_time_s:.3f}s | Save: {m_10k.serialize_time_s:.3f}s | "
              f"Peak Heap: {m_10k.peak_heap_mb:.2f} MB | RSS Delta: {m_10k.rss_delta_mb:.2f} MB | "
              f"DXF: {m_10k.dxf_size_mb:.2f} MB")

    if "50k" in tiers:
        print("[2/5] Generating and profiling Tier B: 50,000 Mixed Primitive Soup (Mandatory R3)...")
        ir_50k = generate_dense_primitive_soup(50000, num_layers=10, seed=50)
        _, m_50k = profile_ir_compilation(ir_50k, "Tier B (50k)", "Mixed Primitive Soup", 50000)
        results.append(m_50k)
        print(f"      Compile: {m_50k.compile_time_s:.3f}s ({m_50k.compile_throughput_eps:,.1f} ent/s) | "
              f"Audit: {m_50k.audit_time_s:.3f}s | Save: {m_50k.serialize_time_s:.3f}s | "
              f"Peak Heap: {m_50k.peak_heap_mb:.2f} MB | RSS Delta: {m_50k.rss_delta_mb:.2f} MB | "
              f"DXF: {m_50k.dxf_size_mb:.2f} MB")

    if "100k" in tiers:
        print("[3/5] Generating and profiling Tier C: 100,000 Mixed Primitive Soup (High-Density Stress)...")
        ir_100k = generate_dense_primitive_soup(100000, num_layers=10, seed=100)
        _, m_100k = profile_ir_compilation(ir_100k, "Tier C (100k)", "Mixed Primitive Soup", 100000)
        results.append(m_100k)
        print(f"      Compile: {m_100k.compile_time_s:.3f}s ({m_100k.compile_throughput_eps:,.1f} ent/s) | "
              f"Audit: {m_100k.audit_time_s:.3f}s | Save: {m_100k.serialize_time_s:.3f}s | "
              f"Peak Heap: {m_100k.peak_heap_mb:.2f} MB | RSS Delta: {m_100k.rss_delta_mb:.2f} MB | "
              f"DXF: {m_100k.dxf_size_mb:.2f} MB")

    if "blocks" in tiers:
        print("[4/5] Generating and profiling Block Explosion (250 blocks, 10,000 insertions)...")
        ir_blocks = generate_block_explosion(num_blocks=250, num_inserts=10000, seed=250)
        _, m_blocks = profile_ir_compilation(ir_blocks, "Block Explosion", "250 Blocks / 10k Inserts", 10000)
        results.append(m_blocks)
        print(f"      Compile: {m_blocks.compile_time_s:.3f}s ({m_blocks.compile_throughput_eps:,.1f} ins/s) | "
              f"Audit: {m_blocks.audit_time_s:.3f}s | Save: {m_blocks.serialize_time_s:.3f}s | "
              f"Peak Heap: {m_blocks.peak_heap_mb:.2f} MB | RSS Delta: {m_blocks.rss_delta_mb:.2f} MB | "
              f"DXF: {m_blocks.dxf_size_mb:.2f} MB")

    if "deep" in tiers:
        print("[5/5] Profiling Deep Block Hierarchy (Depth 30 nested reference tree)...")
        m_deep = profile_deep_dxf_tree(depth=30)
        results.append(m_deep)
        print(f"      Build: {m_deep.compile_time_s:.3f}s | "
              f"Audit: {m_deep.audit_time_s:.3f}s | Save: {m_deep.serialize_time_s:.3f}s | "
              f"Peak Heap: {m_deep.peak_heap_mb:.2f} MB | Errors: {m_deep.audit_errors}")

    scaling_data: Dict[str, Any] = {}
    if m_10k and m_50k and m_100k:
        scaling_data = evaluate_scaling_linearity(m_10k, m_50k, m_100k)

    print("\n" + "=" * 80)
    print("BENCHMARK RESULTS TABLE")
    print("=" * 80)
    print(format_benchmark_table(results))

    if scaling_data:
        print("\n" + "=" * 80)
        print("ASYMPTOTIC SCALING LINEARITY & COMPLEXITY ANALYSIS")
        print("=" * 80)
        print(format_scaling_table(scaling_data))

    if json_out_path:
        out_dict = {
            "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "metrics": [r.to_dict() for r in results],
            "scaling": scaling_data,
        }
        with open(json_out_path, "w", encoding="utf-8") as fh:
            json.dump(out_dict, fh, indent=2)
        print(f"\n[INFO] Benchmark results written to JSON: {json_out_path}")

    return results, scaling_data


# ──────────────────────────────────────────────────────────────────────────────
# Unittest Test Suite
# ──────────────────────────────────────────────────────────────────────────────

class TestScaleStress(unittest.TestCase):
    """
    Automated unittest test suite validating scale compliance, execution limits,
    audit integrity, and asymptotic complexity boundaries.
    """

    @classmethod
    def setUpClass(cls):
        cls._results: Dict[str, BenchmarkMetrics] = {}

    def test_01_tier_a_10k_mixed_primitive_soup(self):
        """Tier A: 10,000 mixed primitives warmup and baseline."""
        ir = generate_dense_primitive_soup(10000, num_layers=10, seed=10)
        doc, m = profile_ir_compilation(ir, "Tier A (10k)", "Mixed Primitive Soup", 10000)
        self.__class__._results["10k"] = m

        # Assertions
        self.assertLess(m.compile_time_s, 10.0, f"Tier A compile time {m.compile_time_s}s exceeded 10.0s threshold")
        self.assertGreater(m.compile_throughput_eps, 4000.0, f"Tier A throughput {m.compile_throughput_eps} below 4,000 ent/s")
        self.assertLess(m.peak_heap_mb, 50.0, f"Tier A peak heap {m.peak_heap_mb}MB exceeded 50MB limit")
        self.assertEqual(m.audit_errors, 0, f"Tier A produced {m.audit_errors} audit errors")
        self.assertEqual(m.audit_fixes, 0, f"Tier A produced {m.audit_fixes} audit fixes")

        # Entity count verification in Model Space
        msp_count = len(list(doc.modelspace()))
        self.assertEqual(msp_count, 10000, f"Expected 10,000 modelspace entities, found {msp_count}")

    def test_02_tier_b_50k_mandatory_scale(self):
        """Tier B: 50,000 mixed primitives mandatory requirement (R3)."""
        ir = generate_dense_primitive_soup(50000, num_layers=10, seed=50)
        doc, m = profile_ir_compilation(ir, "Tier B (50k)", "Mixed Primitive Soup", 50000)
        self.__class__._results["50k"] = m

        # Assertions per R3 specification
        self.assertLess(m.compile_time_s, 20.0, f"Tier B compile time {m.compile_time_s}s exceeded 20.0s threshold")
        self.assertGreater(m.compile_throughput_eps, 5000.0, f"Tier B throughput {m.compile_throughput_eps} below 5,000 ent/s target")
        self.assertLess(m.peak_heap_mb, 100.0, f"Tier B peak heap {m.peak_heap_mb}MB exceeded 100MB limit")
        self.assertEqual(m.audit_errors, 0, f"Tier B produced {m.audit_errors} audit errors")
        self.assertEqual(m.audit_fixes, 0, f"Tier B produced {m.audit_fixes} audit fixes")

        msp_count = len(list(doc.modelspace()))
        self.assertEqual(msp_count, 50000, f"Expected 50,000 modelspace entities, found {msp_count}")

    def test_03_tier_c_100k_high_density_stress(self):
        """Tier C: 100,000 mixed primitives high-density stress test."""
        ir = generate_dense_primitive_soup(100000, num_layers=10, seed=100)
        doc, m = profile_ir_compilation(ir, "Tier C (100k)", "Mixed Primitive Soup", 100000)
        self.__class__._results["100k"] = m

        # Assertions
        self.assertLess(m.compile_time_s, 45.0, f"Tier C compile time {m.compile_time_s}s exceeded 45.0s threshold")
        self.assertGreater(m.compile_throughput_eps, 5000.0, f"Tier C throughput {m.compile_throughput_eps} below 5,000 ent/s target")
        self.assertLess(m.peak_heap_mb, 200.0, f"Tier C peak heap {m.peak_heap_mb}MB exceeded 200MB limit")
        self.assertEqual(m.audit_errors, 0, f"Tier C produced {m.audit_errors} audit errors")
        self.assertEqual(m.audit_fixes, 0, f"Tier C produced {m.audit_fixes} audit fixes")

        msp_count = len(list(doc.modelspace()))
        self.assertEqual(msp_count, 100000, f"Expected 100,000 modelspace entities, found {msp_count}")

    def test_04_block_explosion(self):
        """Block Explosion: 250 unique block definitions and 10,000 INSERT references."""
        ir = generate_block_explosion(num_blocks=250, num_inserts=10000, seed=250)
        doc, m = profile_ir_compilation(ir, "Block Explosion", "250 Blocks / 10k Inserts", 10000)
        self.__class__._results["blocks"] = m

        self.assertLess(m.compile_time_s, 20.0, f"Block explosion compile time {m.compile_time_s}s exceeded 20s threshold")
        self.assertEqual(m.audit_errors, 0, f"Block explosion produced {m.audit_errors} audit errors")
        self.assertEqual(m.audit_fixes, 0, f"Block explosion produced {m.audit_fixes} audit fixes")

        # Verify all 250 blocks exist in BLOCKS table
        for b in range(250):
            bname = f"EQUIP_BLOCK_{b:03d}"
            self.assertIn(bname, doc.blocks, f"Block definition {bname} missing from DXF BLOCKS table")

        # Verify 10,000 INSERT entities exist in modelspace
        inserts = [e for e in doc.modelspace() if e.dxftype() == "INSERT"]
        self.assertEqual(len(inserts), 10000, f"Expected 10,000 INSERT entities, found {len(inserts)}")

    def test_05_deep_block_hierarchy(self):
        """Deep Hierarchy: 30-level nested block reference chain."""
        # 1. Test compiler IR ingestion of hierarchical block definitions
        ir_deep = generate_deep_block_hierarchy_ir(depth=30)
        doc_ir, _ = profile_ir_compilation(ir_deep, "Deep IR", "30-Level IR Definitions", 30)
        self.assertEqual(len(doc_ir.audit().errors), 0, "Deep IR compilation produced audit errors")

        # 2. Test true nested DXF reference tree
        m_deep = profile_deep_dxf_tree(depth=30)
        self.assertLess(m_deep.compile_time_s, 5.0, "Deep hierarchy build exceeded 5.0s")
        self.assertLess(m_deep.audit_time_s, 2.0, "Deep hierarchy audit exceeded 2.0s")
        self.assertEqual(m_deep.audit_errors, 0, "Deep hierarchy produced audit errors")
        self.assertEqual(m_deep.audit_fixes, 0, "Deep hierarchy produced audit fixes")

    def test_06_asymptotic_linearity_and_complexity_bounds(self):
        """Asymptotic scaling linearity evaluation across 10k -> 50k -> 100k."""
        res = self.__class__._results
        if "10k" not in res or "50k" not in res or "100k" not in res:
            self.skipTest("Scale tiers not all executed; skipping asymptotic linearity assertion")

        scaling = evaluate_scaling_linearity(res["10k"], res["50k"], res["100k"])

        # Overall scaling exponent alpha: Time ~ N^alpha
        # alpha must be strictly < 1.30 (sub-quadratic, linear)
        self.assertLess(
            scaling["alpha_overall"],
            1.30,
            f"Asymptotic scaling exponent alpha={scaling['alpha_overall']:.3f} indicates super-linear or quadratic bottleneck",
        )
        self.assertFalse(
            scaling["has_exponential_trap"],
            "Detected potential exponential complexity trap across scale tiers!",
        )

        # Time ratio 100k / 10k must be strictly < 18.0 (ideal linear is 10.0, quadratic would be 100.0)
        self.assertLess(
            scaling["time_ratio_overall"],
            18.0,
            f"Overall time scaling ratio {scaling['time_ratio_overall']:.2f}x exceeds threshold 18.0x",
        )

        # Memory per entity must remain bounded and stable (constant heap intensity)
        self.assertLess(
            scaling["bytes_per_ent_100k"],
            1000.0,
            f"Heap intensity at 100k entities ({scaling['bytes_per_ent_100k']:.1f} B/ent) exceeded 1000 B/entity",
        )


# ──────────────────────────────────────────────────────────────────────────────
# CLI Main Entry Point
# ──────────────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="CAD-IR-to-DXF Scale & Stress Profiling Benchmark")
    parser.add_argument("--tiers", nargs="+", choices=["10k", "50k", "100k", "blocks", "deep", "all"], default=["all"],
                        help="Scale tiers to execute")
    parser.add_argument("--json-out", type=str, default=None,
                        help="Optional path to output JSON benchmark metrics")
    parser.add_argument("--unittest", action="store_true",
                        help="Run full unittest test runner instead of standalone benchmark")
    args = parser.parse_args()

    if args.unittest:
        suite = unittest.TestLoader().loadTestsFromTestCase(TestScaleStress)
        runner = unittest.TextTestRunner(verbosity=2)
        result = runner.run(suite)
        sys.exit(0 if result.wasSuccessful() else 1)

    selected_tiers = ["10k", "50k", "100k", "blocks", "deep"] if "all" in args.tiers else args.tiers
    results, scaling = run_benchmark_suite(tiers=selected_tiers, json_out_path=args.json_out)

    # Exit code: 0 if all tests passed audit with 0 errors
    has_errors = any(r.audit_errors > 0 for r in results)
    sys.exit(1 if has_errors else 0)


if __name__ == "__main__":
    main()

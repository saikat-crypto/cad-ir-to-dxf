# cad-ir-to-dxf

<div align="center">

[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](https://opensource.org/licenses/MIT)
[![Python: 3.10+](https://img.shields.io/badge/Python-3.10+-brightgreen.svg)](https://python.org)
[![DXF: R2013](https://img.shields.io/badge/DXF-R2013%20(AC1027)-orange.svg)](#)
[![Ecosystem](https://img.shields.io/badge/Project-La%20Vinci-purple.svg)](#)

**La Vinci CAD IR → DXF Compiler.**  
Converts a [`LAVINCI_CAD_IR_V3`](https://github.com/saikat-crypto/cad-extractor-ir) JSON payload into a fully compliant, high-fidelity DXF file.

*Engineered by **Saikat Dutta Chowdhury** as part of the **La Vinci** engineering initiative.*

</div>

---

## 💡 What This Does

This package is the **first downstream spoke** of the La Vinci Hub-and-Spoke CAD converter architecture:

```
DWG ──► cad-extractor-ir ──► LAVINCI_CAD_IR_V3 ──► cad-ir-to-dxf ──► DXF
```

It takes the clean, structured IR JSON and compiles it into an industry-standard DXF file (default: **DXF R2013 / AC1027**) that opens without errors in AutoCAD, LibreCAD, QCAD, and Autodesk Fusion 360.

---

## 🏗️ Compiler Pipeline

```
[ Input: blueprint_ir.json (LAVINCI_CAD_IR_V3) ]
                    │
    ┌───────────────▼──────────────────┐
    │  1. Header Setup                 │  $INSUNITS, $MEASUREMENT, $EXTMIN/$EXTMAX
    ├──────────────────────────────────┤
    │  2. Linetype Table               │  Pre-loads DASHED, HIDDEN, CENTER, etc.
    ├──────────────────────────────────┤
    │  3. Layer Table                  │  Full 256 ACI palette, flags
    ├──────────────────────────────────┤
    │  4. BLOCKS Table (V3 Powerhouse) │  Full internal geometry per symbol
    │     Toilet: 46 lines, 13 arcs…  │
    │     Receptacle: circles + lines  │
    ├──────────────────────────────────┤
    │  5. Space-Aware Entity Dispatch  │  Model Space → doc.modelspace()
    │     LINE, ARC, CIRCLE, POLYLINE  │  Paper Space → doc.layout(name)
    │     INSERT + ATTRIB tags         │
    │     MTEXT / TEXT annotations     │
    │     Dimensions as MTEXT labels   │
    ├──────────────────────────────────┤
    │  6. Geometry Sanitizer           │  Drops zero-length, zero-radius,
    │                                  │  NaN/Inf, and under-specified entities
    └──────────────────────────────────┘
                    │
  [ Output: blueprint_generated.dxf (DXF R2013) ]
```

---

## ⚡ Key Design Guarantees

| Property | Guarantee |
| :--- | :--- |
| **Complete Visual Fidelity** | Block definitions contain full geometry (lines, arcs, circles); all 164 components (doors, windows, plumbing) render with their actual shapes |
| **True BYLAYER Semantics** | Entities with `color: null` in IR are written with no colour override — layer colour changes in AutoCAD propagate dynamically |
| **Arc Sweep Fidelity** | Angles are passed verbatim without normalization; arcs crossing 0° are preserved correctly |
| **Auto-placeholder Blocks** | INSERT references to blocks with no geometry auto-vivify a visible crosshair marker instead of crashing |
| **Degenerate Geometry Guard** | Zero-length lines, zero-radius arcs/circles, single-point polylines, NaN/Inf coords are silently filtered |
| **Zero C-Dependencies** | 100% pure Python (ezdxf) — runs on Windows, macOS, Linux, Docker, AWS Lambda without compilation |

---

## 🚀 Quick Start

### Installation

```bash
git clone https://github.com/saikat-crypto/cad-ir-to-dxf.git
cd cad-ir-to-dxf
pip install -e .
```

### Command Line Usage

**Compile IR → DXF:**
```bash
cad-ir-to-dxf blueprint_ir.json -o blueprint.dxf
```

**Choose DXF version (R12, R2000, R2004, R2007, R2010, R2013, R2018):**
```bash
cad-ir-to-dxf blueprint_ir.json -o blueprint.dxf --version R2000
```

**Print a summary of the IR without compiling:**
```bash
cad-ir-to-dxf blueprint_ir.json --summary
```

**Output:**
```
────────────────────────────────────────────────────
  La Vinci CAD IR Summary
────────────────────────────────────────────────────
  Format:           LAVINCI_CAD_IR_V3
  Source:           blueprint_sample.dwg
  CAD Version:      R2007
  Measurement:      Metric
  Canvas:           524.403 × 501.854 (width × height)
────────────────────────────────────────────────────
  Layers:           17
  Block Defs:       54
  Lines:            282
  Arcs:             31
  Circles:          0
  Polylines:        7
  Components:       164
  Annotations:      17
  Dimensions:       0

  Bill of Materials (33 component types):
    Receptacle                           44
    Lighting fixture                     28
    *B20                                 13
    ANDERSEN CASEMENT (*U48)             10
    ...
────────────────────────────────────────────────────
```

### Python API

```python
from cad_ir_to_dxf import compile_ir_to_dxf

# From a JSON file path
doc = compile_ir_to_dxf("blueprint_ir.json", output_path="output.dxf")

# From a Python dict (e.g. received from a Lambda event)
doc = compile_ir_to_dxf(ir_dict, output_path="output.dxf", dxf_version="R2013")

# In-memory only (no file write) — inspect or stream the doc
doc = compile_ir_to_dxf(ir_dict)
doc.saveas("/tmp/output.dxf")
```

---

## 🧪 Test Results

```
Ran 35 tests in 0.323s — OK (0 failures, 0 errors)

TestSanitizer           (14 tests) — zero-length lines, NaN/Inf coords,
                                     negative radius, wrapping arcs, scale clamping
TestCompilerSmoke       ( 8 tests) — smoke compilation of all 8 real-world example IRs
TestCompilerFidelity    ( 8 tests) — layer fidelity, block geometry, BYLAYER colour,
                                     arc angle preservation, annotation output
TestBoundaryConditions  ( 3 tests) — empty IR, degenerate geometry, missing block placeholder
```

---

## 🔗 Ecosystem

| Repository | Role |
| :--- | :--- |
| [`cad-extractor-ir`](https://github.com/saikat-crypto/cad-extractor-ir) | DWG → LAVINCI_CAD_IR_V3 (the extractor / source) |
| `cad-ir-to-dxf` *(this repo)* | LAVINCI_CAD_IR_V3 → DXF (this compiler) |
| `cad-ir-to-svg` *(coming soon)* | LAVINCI_CAD_IR_V3 → SVG |
| `cad-ir-to-pdf` *(coming soon)* | LAVINCI_CAD_IR_V3 → PDF |

---

## 📄 License & Credits

* **Author**: [Saikat Dutta Chowdhury](https://github.com/saikat-crypto)
* **Project**: Part of the **La Vinci** engineering initiative.
* **License**: Licensed under the [MIT License](LICENSE).

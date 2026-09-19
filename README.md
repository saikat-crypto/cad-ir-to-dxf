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

## ⚙️ Compilation Presets & Configuration Architecture

An Intermediate Representation (`LAVINCI_CAD_IR_V3`) is the **Single Source of Truth** for pure geometry and semantic data. A DXF file, by contrast, is a **rendered target document** whose packaging depends on the recipient's software, machine workflow, or printing needs.

The relationship between IR and DXF is fundamentally **One-to-Many**: a single IR payload can be compiled into multiple specialized DXF presets.

### Active Default Configuration

The current `cad-ir-to-dxf` engine compiles using a **True-Scale Model-Space Master Profile**:

| Parameter Group | Parameter | Active Default | Description / Design Rationale |
| :--- | :--- | :--- | :--- |
| **DXF Version** | `dxf_version` | `R2013` (AC1027) | AutoCAD 2013 standard. Universal compatibility across modern CAD/BIM tools (2013–2026, Revit, Rhino, Fusion 360). |
| **Encoding** | `encoding` | `UTF-8` | Full Unicode support preventing symbol and foreign language character corruption. |
| **Target Space** | `target_space` | `ModelSpace` | Real-world 1:1 coordinate space. Geometry matches true physical dimensions for direct measuring and editing. |
| **Spatial Units** | `$INSUNITS` | From IR (or Metric `4` = mm) | Retains original scale and insertion units captured from the source drawing. |
| **Measurement** | `$MEASUREMENT` | `1` (Metric) / `0` (Imperial) | Controls default linetype definitions and hatch pattern scaling matching the source drawing. |
| **Vector Geometry** | `primitives` | Native Analytic Vectors | `ARC`, `CIRCLE`, `LWPOLYLINE`, `ELLIPSE`, and `SPLINE` are preserved mathematically without polygonal faceting. |
| **Block Topology** | `blocks` | Hierarchical `INSERT` + `BLOCK_RECORD` | Compact vector reuse. Components reference centralized symbol geometry definitions. |
| **Attributes** | `attributes` | Attached `ATTRIB` tags | Component tags and instance attributes are bound to their respective block insertions. |
| **Layer Fidelity** | `layers` | Explicit State Mapping | Layers retain `is_off`, `is_frozen`, `is_locked`, `color`, and `linetype` states verbatim. |
| **Color Fidelity** | `color_mode` | TrueColor (24-bit RGB) + ACI | Preserves full 24-bit color fidelity with automatic fallback to standard AutoCAD Color Index. |
| **Sanitization** | `zero_length_tol` | `1e-9` | Rejects degenerate micro-geometry without affecting legitimate fine details. |

### 5 Curated Safe Presets

| Preset | Target DXF | Space | Key Characteristics & Target Workflows |
| :--- | :--- | :--- | :--- |
| **`standard`** *(default)* | `R2013` (AC1027) | ModelSpace | High-fidelity master profile: native analytic curves, TrueColor, hierarchical blocks, full layers. |
| **`cnc_cam`** | `R12` (AC1009) | ModelSpace | Flat 2D ($Z=0$), block definitions exploded, annotations & dimensions omitted to prevent cutting labels. |
| **`arch_print`** | `R2013` (AC1027) | PaperSpace | Auto-provisions `Presentation_Sheet` layout tab with printable border and scaled viewport. |
| **`web_lightweight`** | `R2000` (AC1015) | ModelSpace | Compact output for web viewers (three.js), stripped tables, ACI color, dimensions omitted. |
| **`bim_overlay`** | `R2018` (AC1032) | ModelSpace | Strict world-origin coordinate lock; `IR_` layer prefix to prevent layer collisions in Revit. |

---

## 🛡️ Developer Diagnostic Engine & Error Messages

For production library consumers and external developers, `cad-ir-to-dxf` provides actionable, typed errors and compilation diagnostic telemetry instead of cryptic Python tracebacks.

### 1. Custom Exception Hierarchy (`exceptions.py`)
All exceptions inherit from `CadIrToDxfError` and carry three properties:
* **`message`**: Clear explanation of what failed.
* **`offender`**: The exact bad value, key, or entity causing the issue.
* **`hint`**: Actionable guidance explaining how to resolve it.

```python
from cad_ir_to_dxf import compile_ir_to_dxf, InvalidPresetError, StrictModeViolationError

try:
    doc = compile_ir_to_dxf("plan.json", preset="cnc-cam")
except InvalidPresetError as e:
    print(e.message)   # "Unknown preset 'cnc-cam'. Did you mean 'cnc_cam'?"
    print(e.hint)      # "Valid presets are: ['standard', 'cnc_cam', 'arch_print', ...]"
    print(e.offender)  # "cnc-cam"
```

### 2. Compilation Diagnostics (`diagnostics.py`)
Non-fatal events (e.g., auto-vivified layers, dropped micro-geometry, suppressed annotation text) are gathered into a structured diagnostic report:

```python
from cad_ir_to_dxf import compile_ir_to_dxf, CompilationDiagnostics

diag = CompilationDiagnostics()
doc = compile_ir_to_dxf("plan.json", preset="cnc_cam", diagnostics=diag)

# Print human-readable summary to stderr
diag.print_report()

# Or inspect programmatically:
report = diag.to_dict()
```

CLI usage:
```bash
# Print diagnostic report
cad-ir-to-dxf plan.json --preset cnc_cam --diagnostics

# Enforce strict validation (fail fast on any missing layer or degenerate geometry)
cad-ir-to-dxf plan.json --preset standard --strict
```

---

## 🧪 Test Results

```
Ran 85 tests in 0.460s — OK (0 failures, 0 errors)

TestSanitizer                  (14 tests) — zero-length lines, NaN/Inf coords, scale clamping
TestCompilerSmoke              ( 8 tests) — smoke compilation of real-world example IRs
TestCompilerFidelity           ( 8 tests) — layer fidelity, block geometry, BYLAYER colour
TestBoundaryConditions         ( 4 tests) — empty IR, degenerate geometry, missing block placeholder
TestPresetResolver             ( 7 tests) — preset profile defaults, enum and string parsing
TestAdvancedOptionsOverrides   ( 9 tests) — version, geometry, filtering, layout, styling overrides
TestPresetCompilationSmoke     ( 6 tests) — all 5 presets compile valid DXF outputs
TestPresetBehaviourFunctional  (10 tests) — flatten_z, layer prefixes, layer filtering, PaperSpace
TestExceptions                 ( 9 tests) — typed errors, actionable hints, offender inspection
TestStrictMode                 ( 3 tests) — strict-mode enforcement for layers, blocks, geometry
TestDiagnostics                ( 5 tests) — diagnostic telemetry, print_report, and to_dict
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

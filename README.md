# cad-ir-to-dxf: High-Fidelity AutoCAD DXF Re-synthesizer & CAM Post-Processor

<div align="center">

[![Python: 3.12+](https://img.shields.io/badge/Python-3.12+-3776AB.svg?style=for-the-badge&logo=python&logoColor=white)](https://python.org)
[![DXF Standard: R12-R2018](https://img.shields.io/badge/DXF%20Standard-AC1009%20to%20AC1032-orange.svg?style=for-the-badge)](#)
[![Domain: CNC / CAM](https://img.shields.io/badge/Domain-CNC%20%7C%20CAM%20%7C%20Laser%20Cutting-00A86B.svg?style=for-the-badge)](#)
[![Validation Suite](https://img.shields.io/badge/Test%20Suite-100%25%20Passing-2ED573.svg?style=for-the-badge)](#)
[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg?style=for-the-badge)](https://opensource.org/licenses/MIT)

**A high-precision CAD compiler translating canonical `LAVINCI_CAD_IR_V3` JSON models into industry-compliant AutoCAD DXF files, featuring automated ground-plane $Z$-flattening for CNC/CAM machining and PaperSpace layout generation.**

*Part of the **La Vinci** engineering initiative by **Saikat Dutta Chowdhury** (Mechanical Engineering).*

</div>

---

## 💡 The Manufacturing Problem & Mechanical Scope

In automated manufacturing (laser cutting, CNC routing, waterjet machining, sheet metal punching), CAD-to-CAM pipelines constantly fail due to **dirty geometric data**:
1. **Floating $Z$-Coordinates**: 2D drawings snapped to 3D reference points contain subtle non-zero elevations (e.g. $Z = 0.0012\text{ mm}$), causing CAM software to reject toolpaths with *"Entities are non-coplanar"* errors.
2. **Spline Chattering**: High-order mathematical NURBS splines overwhelm legacy machine CNC controllers with dense control vertices, causing cutting head deceleration and jagged surface finishes.
3. **Block Hierarchy Lockouts**: Block references (`INSERT`) cannot be recognized by simple 2D G-code generators without manual flattening.

**`cad-ir-to-dxf`** resolves these challenges by serving as an intelligent geometric re-synthesizer:
* Reconstructs standard AutoCAD DXF files (supporting versions from legacy **R12 (AC1009)** to modern **R2018 (AC1032)**).
* Features automated **ground-plane $Z$-flattening** and block exploding for digital manufacturing.
* Implements **directed-graph cycle detection** to prevent recursive block reference lockups.
* Automatically provisions **PaperSpace sheet layouts** with scaled viewports and printable borders.

```
[ Input: LAVINCI_CAD_IR_V3 JSON ]
               │
               ▼
┌──────────────────────────────────────────────┐
│         cad-ir-to-dxf Compiler               │
│                                              │
│  1. Profile Resolver (5 Presets)             │
│     • standard (R2013 TrueColor)             │
│     • cnc_cam (R12 Flat, Z=0, Exploded)      │
│     • arch_print (PaperSpace + Viewport)     │
│     • web_lightweight (R2000 Stripped)       │
│     • bim_overlay (R2018 World Origin)       │
│                                              │
│  2. Graph Analysis & Cycle Detection         │
│     • Directed graph DFS on block defs       │
│                                              │
│  3. Geometric Sanitization                   │
│     • Z-coordinate ground projection (Z=0.0) │
│     • Spline-to-polyline bi-arc conversion   │
│     • AutoCAD symbol name sanitization       │
│                                              │
│  4. ezdxf Synthesis & Table Emission         │
│     • Standard linetypes (CENTER, DASHED)    │
│     • Full 24-bit TrueColor / 256 ACI        │
└──────────────────────────────────────────────┘
               │
               ▼
[ Output: production_ready.dxf ]
```

---

## 🔬 Computational Geometry Invariants

### 1. Ground-Plane $Z$-Flattening for 2D Fabrication
When the `cnc_cam` preset is active, all 3D coordinates are flattened to strictly coplanar 2D vectors:
$$\vec{P}_{\text{flat}} = \begin{bmatrix} X & Y & 0.0 \end{bmatrix}^T$$
Eliminating non-coplanar toolpath rejection across all CNC/CAM software.

### 2. Spline-to-Polyline Tessellation & Feedrate Optimization
To eliminate machine chatter on CNC cutting heads, NURBS splines are converted to contiguous piecewise linear polylines (`LWPOLYLINE`) within an adaptive chord-deviation tolerance ($\epsilon \le 2.0\text{ mm}$), ensuring smooth machine acceleration profiles.

### 3. Directed Graph Block Cycle Detection
Circular block references (Block A inserting Block B, which in turn inserts Block A) cause fatal infinite memory loops. `cad-ir-to-dxf` performs a pre-compilation Depth-First Search (DFS) traversal across the block dependency graph:
```python
def check_block_cycles(block_defs: Dict[str, Any]) -> None:
    # Traverses block graph; raises BlockCycleError before memory exhaustion
```

### 4. Automatic PaperSpace Sheet Provisioning (`arch_print`)
Calculates optimal viewport scaling to fit drawing extents inside standard ISO (A4 to A0) and ANSI (Letter to Arch D) sheet boundaries:
$$\text{Scale} = \min\left(\frac{W_{\text{sheet}} - 2M}{W_{\text{model}}}, \; \frac{H_{\text{sheet}} - 2M}{H_{\text{model}}}\right)$$

---

## ⚡ Quick Start

### Installation
```bash
pip install -e products/cad-ir-to-dxf
```

### Python SDK
```python
from cad_ir_to_dxf import compile_ir_to_dxf

# 1. Standard Modern CAD Output (AutoCAD R2013 / TrueColor)
compile_ir_to_dxf("assembly_ir.json", "output_standard.dxf", preset="standard")

# 2. CNC / CAM Fabrication (Z=0, Exploded Blocks, R12 Legacy)
compile_ir_to_dxf("assembly_ir.json", "laser_cut.dxf", preset="cnc_cam")

# 3. Client Print Sheet with PaperSpace Tab (A3 Landscape Viewport)
compile_ir_to_dxf("assembly_ir.json", "review_sheet.dxf", preset="arch_print")
```

### Command Line Interface (CLI)
```bash
# Convert to standard DXF
python -m cad_ir_to_dxf.cli drawing_ir.json -o drawing.dxf

# Convert for CNC waterjet cutting
python -m cad_ir_to_dxf.cli drawing_ir.json -o waterjet.dxf --preset cnc_cam

# Exclude scratch or temporary layers
python -m cad_ir_to_dxf.cli drawing_ir.json -o clean.dxf --exclude-layers "TEMP*,DEFPOINTS"
```

---

## 📄 License

Licensed under the [MIT License](LICENSE).

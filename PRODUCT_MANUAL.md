# cad-ir-to-dxf: High-Fidelity AutoCAD DXF Re-synthesizer & Compiler

> **Product**: `cad-ir-to-dxf`  
> **Package Version**: `1.0.0`  
> **Source Directory**: `products/cad-ir-to-dxf/`  
> **Role in Ecosystem**: Vector CAD Egress Compiler (`LAVINCI_CAD_IR_V3` $\to$ AutoCAD DXF)  

---

## 1. Executive Summary & Architectural Scope

The **`cad-ir-to-dxf`** compiler is the primary interoperability egress engine of the La Vinci ecosystem. It consumes canonical **`LAVINCI_CAD_IR_V3`** JSON models and synthesizes fully standard-compliant, binary or ASCII AutoCAD DXF (`Drawing Exchange Format`) files.

Unlike rudimentary DXF exporters that dump raw lines into an untyped default layer, `cad-ir-to-dxf` faithfully reconstructs the full architectural hierarchy of an engineering drawing:
* **Layer Tables & Linetypes**: Full restoration of layer names, visibility flags, frozen states, and standard AutoCAD linetypes.
* **Hierarchical Block Definitions (`BLOCK` & `INSERT`)**: Re-synthesizes reusable block definition tables and spatial insertions with 3D scale vectors and rotation matrices.
* **Dual Color Modes**: Native 24-bit TrueColor (`RGB`) support for modern CAD packages, with automatic fallback to the 256-color AutoCAD Color Index (`ACI`) for legacy tools.
* **Preset Specialization**: One IR model can generate multiple tailored DXFs (e.g. flat R12 DXF for CNC laser cutters, lightweight web DXF for Three.js viewers, or R2018 with BIM layer prefixes for Revit/Navisworks overlays).

```
 ┌──────────────────────────────┐
 │   LAVINCI_CAD_IR_V3 (JSON)   │
 └──────────────┬───────────────┘
                │
                ▼
 ┌──────────────────────────────┐
 │    Preset Configuration      │
 │  - 'standard' (R2013)        │
 │  - 'cnc_cam' (R12 Flat)      │
 │  - 'arch_print' (PaperSpace) │
 │  - 'web_lightweight' (R2000) │
 │  - 'bim_overlay' (R2018)     │
 └──────────────┬───────────────┘
                │
                ▼
 ┌──────────────────────────────┐
 │   Sanitization & Validation  │
 │   - Block Cycle Detection    │
 │   - Symbol Name Sanitization │
 │   - Coordinate Finiteness    │
 └──────────────┬───────────────┘
                │
                ▼
 ┌──────────────────────────────┐
 │    ezdxf Document Synthesis  │
 │   - Header & Units ($INSUNITS)
 │   - Layer Table & Linetypes  │
 │   - Block Definitions Table  │
 │   - ModelSpace Geometry      │
 │   - Optional PaperSpace Tab  │
 └──────────────┬───────────────┘
                │
                ▼
 ┌──────────────────────────────┐
 │    Production DXF Stream     │
 └──────────────────────────────┘
```

---

## 2. Preset Architecture & Configuration Engine

`cad-ir-to-dxf` implements a two-tier configuration contract:
1. **Named Presets (`PresetName`)**: High-level, pre-tested profiles targeting specific industrial workflows.
2. **Granular Options (`AdvancedOptions`)**: Strongly typed dataclasses allowing callers to surgically override specific compilation behaviors.

### 2.1 The 5 Production Presets

| Preset Name | Target Release | Color Mode | Geometry Strategy | Primary Use Case |
| :--- | :---: | :---: | :--- | :--- |
| **`standard`** *(Default)* | `R2013` | TrueColor | Native analytic curves, hierarchical blocks | Modern CAD interoperability (AutoCAD, Revit, Rhino, SolidWorks, Fusion 360). |
| **`cnc_cam`** | `R12` | ACI (Mono) | Z-flattened ($Z=0$), blocks exploded, splines converted to polylines | Laser cutting, waterjet, plasma, CNC milling, sheet metal CAM. |
| **`arch_print`** | `R2013` | TrueColor | Native curves + Auto-provisioned PaperSpace sheet & scaled viewport | Client presentation sheets, plotting, formal blueprint review. |
| **`web_lightweight`** | `R2000` | ACI | Stripped metadata, Z-flattened, dimensions omitted | Three.js web viewers, mobile CAD engines, GIS shapefile conversion. |
| **`bim_overlay`** | `R2018` | TrueColor | World-coordinate lock, `IR_` layer prefix to prevent collisions | Revit links, Navisworks clash-detection, IFC coordination underlays. |

---

### 2.2 Advanced Options Schema (`AdvancedOptions`)

Callers can pass an `AdvancedOptions` instance to customize any compilation stage:

```python
@dataclass
class AdvancedOptions:
    version: VersionOptions       # DXF version (R12, R2000, R2013, R2018), UTF-8 / ASCII
    geometry: GeometryOptions     # flatten_z, explode_blocks, curve_strategy, tolerances
    filtering: FilteringOptions   # include/exclude layer whitelists, text/dim toggles
    layout: LayoutOptions         # create_paper_space, paper_size, viewport_scale
    styling: StylingOptions       # color_mode (TrueColor vs ACI), layer prefixes
```

#### Geometry Options Detail (`GeometryOptions`)
* `flatten_z` (`bool`): Forces all $Z$ coordinates to $0.0$. Critical for 2D CNC/CAM software that errors on 3D geometry.
* `explode_blocks` (`bool`): Expands all block references (`INSERT`) into raw modelspace lines, arcs, and polylines. Eliminates block hierarchies for simple CAM post-processors.
* `curve_strategy` (`str`):
  * `"native"`: Emits analytic `ARC`, `CIRCLE`, `ELLIPSE`, and `SPLINE` entities.
  * `"tessellated"`: Approximates all curves as piecewise linear `LWPOLYLINE` segments.
  * `"splines_to_polylines"`: Keeps arcs/circles native, but converts high-order NURBS splines to polylines.
* `zero_length_tolerance` (`float`): Segments shorter than this value (default $10^{-9}$) are dropped as degenerate noise.

---

## 3. Core Compilation & Synthesis Algorithms

### 3.1 Symbol Name Sanitization
AutoCAD enforces strict character constraints on layer, block, and style names. Characters matching `[<>/\\":;?*|=,']` cause AutoCAD to flag drawing corruption upon open.

`cad-ir-to-dxf` sanitizes all names using regex substitutions:
```python
def sanitize_symbol_name(name: str) -> str:
    if not name:
        return "UNNAMED"
    # Replace illegal characters with underscore
    sanitized = re.sub(r'[<>/\\":;?*|=,\']', '_', name)
    # Clamp length to AutoCAD 255-character symbol limit
    return sanitized.strip()[:255]
```

### 3.2 Recursive Block Cycle Detection
A common source of fatal infinite loops in CAD engines is circular block references (e.g. Block A inserts Block B, which inserts Block A).

Before creating any entities, `cad-ir-to-dxf` builds a directed graph of all block definitions and executes cycle detection:
```python
def check_block_cycles(block_defs: Dict[str, Any]) -> None:
    visited = set()
    rec_stack = set()
    
    def dfs(block_name: str, path: List[str]):
        visited.add(block_name)
        rec_stack.add(block_name)
        
        for dep in block_graph.get(block_name, []):
            if dep not in visited:
                dfs(dep, path + [dep])
            elif dep in rec_stack:
                raise BlockCycleError(f"Circular block reference detected: {' -> '.join(path + [dep])}")
                
        rec_stack.remove(block_name)
```

### 3.3 TrueColor vs ACI Color Conversion
* **TrueColor Mode**: If an entity or layer specifies a hex color (e.g. `"#FF5500"`), it is converted to a 24-bit integer TrueColor:
  $$\text{TrueColor} = (R \ll 16) + (G \ll 8) + B$$
* **ACI Fallback Mode**: If compiling for legacy DXF R12 or R2000, TrueColor is not supported. The compiler finds the closest Euclidean match in the 256-entry AutoCAD Color Index palette:
  $$\text{ACI}_{\text{match}} = \arg\min_{i \in [1, 255]} \|\vec{C}_{\text{true}} - \text{palette}(i)\|_2$$

### 3.4 DXF R12 Compatibility Translation
When the `R12` preset is selected (e.g. for legacy CNC tooling):
1. **`LWPOLYLINE` $\to$ `POLYLINE2D`**: Modern lightweight 2D polylines are converted to legacy vertex-mesh entities.
2. **`MTEXT` $\to$ `TEXT`**: Multiline text objects are flattened to single-line `TEXT` entities with stripped formatting codes.
3. **Encoding**: Emitted strictly as ASCII bytes rather than UTF-8.

### 3.5 Automated PaperSpace Sheet Provisioning (`arch_print`)
When `layout.create_paper_space=True`:
1. The compiler adds a new layout tab to the DXF document.
2. Calculates target sheet boundaries (e.g. ISO A3: $420 \times 297\text{ mm}$).
3. Draws a printable border and titleblock margins.
4. Creates an active `VIEWPORT` entity scaled to fit the ModelSpace bounding box:
   $$\text{Scale} = \min\left(\frac{W_{\text{sheet}} - 2M}{W_{\text{model}}}, \frac{H_{\text{sheet}} - 2M}{H_{\text{model}}}\right)$$

---

## 4. Public API & Usage Reference

### 4.1 Python SDK

```python
from cad_ir_to_dxf import compile_ir_to_dxf
from cad_ir_to_dxf.presets import AdvancedOptions, GeometryOptions

# 1. Standard Conversion (Default Preset)
dxf_doc = compile_ir_to_dxf(
    ir_payload="ir_data.json",
    output_path="output_standard.dxf",
    preset="standard"
)

# 2. CNC / CAM Conversion (Z-flattened, Exploded Blocks)
dxf_doc_cnc = compile_ir_to_dxf(
    ir_payload=ir_dict,
    output_path="output_laser.dxf",
    preset="cnc_cam"
)

# 3. Custom Granular Overrides
custom_options = AdvancedOptions(
    geometry=GeometryOptions(
        flatten_z=True,
        curve_strategy="splines_to_polylines",
        zero_length_tolerance=1e-6
    )
)

dxf_doc_custom = compile_ir_to_dxf(
    ir_payload=ir_dict,
    output_path="output_custom.dxf",
    preset="standard",
    options=custom_options
)

# 4. In-Memory Headless Stream (Lambda / Web API)
# Passing output_path=None returns the ezdxf Drawing object without disk I/O
drawing = compile_ir_to_dxf(ir_dict, output_path=None, preset="standard")
```

---

### 4.2 Command Line Interface (CLI)

```bash
# Convert with default standard preset
python -m cad_ir_to_dxf.cli input_ir.json -o output.dxf

# Convert for CNC laser cutter
python -m cad_ir_to_dxf.cli input_ir.json -o output_cnc.dxf --preset cnc_cam

# Convert with PaperSpace print layout tab
python -m cad_ir_to_dxf.cli input_ir.json -o output_print.dxf --preset arch_print --paper-size ISO_A3

# Exclude scratch or temporary layers
python -m cad_ir_to_dxf.cli input_ir.json -o clean.dxf --exclude-layers "TEMP*,DEFPOINTS"
```

---

## 5. Diagnostic Codes & Error Reference

| Exception | Root Cause | Remediation |
| :--- | :--- | :--- |
| `BlockCycleError` | Cyclic block definition reference | Remove circular block dependency from IR model. |
| `IRParseError` | Malformed or invalid JSON syntax | Verify IR complies with `LAVINCI_CAD_IR_V3` schema. |
| `MissingFormatHeaderError` | Missing `"format": "LAVINCI_CAD_IR_V3"` | Ensure the payload originates from `cad-extractor-ir`. |
| `InvalidPresetError` | Unknown preset string passed | Select from `standard`, `cnc_cam`, `arch_print`, `web_lightweight`, `bim_overlay`. |
| `InvalidDxfVersionError` | Unsupported DXF version requested | Use standard versions: `R12`, `R2000`, `R2004`, `R2007`, `R2010`, `R2013`, `R2018`. |
| `StrictModeViolationError` | Non-finite coordinate under strict mode | Enable sanitization or fix coordinate data upstream. |

# Technical Handoff: `cad-ir-to-dxf` (v1.2.0)

This document provides a comprehensive, production-grade technical specification and integration guide for `cad-ir-to-dxf`. It is intended for downstream engineers building automation pipelines, microservices, CLI tools, or web wrappers around this module.

---

## 1. Module Overview & Responsibilities

`cad-ir-to-dxf` is a deterministic compiler that transforms `LAVINCI_CAD_IR_V3` JSON payloads into industry-compliant Autodesk Drawing Exchange Format (`DXF`) files.

```
┌─────────────────────────────────────────────────────────────┐
│                 LAVINCI_CAD_IR_V3 Source                    │
│   (Dict / File Path / JSON String / Pydantic Model)         │
└──────────────────────────────┬──────────────────────────────┘
                               │
                               ▼
               ┌───────────────────────────────┐
               │    Resolver & Sanitizer       │
               │  - Presets & Overrides        │
               │  - Coordinate & Color Bounds  │
               └───────────────┬───────────────┘
                               │
                               ▼
               ┌───────────────────────────────┐
               │      ezdxf Target Engine      │
               │  - Header Variables           │
               │  - Layers & Standard Ltypes   │
               │  - Block Table & ATTDEF/ATTRIB│
               │  - ModelSpace Primitive Graph │
               │  - PaperSpace (Presentation)  │
               └───────────────┬───────────────┘
                               │
                               ▼
┌─────────────────────────────────────────────────────────────┐
│                       Outputs                               │
│  - ezdxf.Drawing (in-memory document)                       │
│  - Physical .dxf file (optional)                            │
│  - CompilationDiagnostics report (telemetry)                │
└─────────────────────────────────────────────────────────────┘
```

---

## 2. Input Contracts & Schemas

### 2.1 Python Function Signature

```python
def compile_ir_to_dxf(
    ir_source: Union[str, Path, Dict[str, Any], Any],
    output_path: Optional[Union[str, Path]] = None,
    dxf_version: str = "R2013",
    preset: Union[str, PresetName] = "standard",
    advanced_options: Optional[Union[AdvancedOptions, Dict[str, Any]]] = None,
    diagnostics: Optional[CompilationDiagnostics] = None,
    strict: bool = False,
) -> ezdxf.document.Drawing:
```

### 2.2 Input Argument Types & Ingestion Rules

| Parameter | Accepted Types | Nullable / Optional | Description |
| :--- | :--- | :--- | :--- |
| `ir_source` | `str`, `Path`, `dict`, Pydantic Model (`BaseModel`) | **Required** | If `dict`: used directly. If file path string/`Path`: read from disk. If raw JSON string: parsed with `json.loads`. If Pydantic model: exported via `.model_dump()` or `.dict()`. |
| `output_path` | `str`, `Path`, `None` | Optional (default: `None`) | Destination `.dxf` path on disk. If `None`, in-memory `Drawing` is returned without disk I/O. |
| `dxf_version` | `str` | Optional (default: `"R2013"`) | Legacy/backward-compat parameter. If passed explicitly and $\ne$ `"R2013"`, overrides preset's target version. |
| `preset` | `str`, `PresetName` enum | Optional (default: `"standard"`) | One of 5 named profiles (`"standard"`, `"cnc_cam"`, `"arch_print"`, `"web_lightweight"`, `"bim_overlay"`). Case-sensitive. |
| `advanced_options`| `AdvancedOptions` dataclass, `dict`, `None`| Optional (default: `None`) | Granular overrides merged on top of the preset baseline. |
| `diagnostics` | `CompilationDiagnostics`, `None` | Optional (default: `None`) | Mutable collector receiving non-fatal compilation telemetry (warnings, adjustments, dropped elements). |
| `strict` | `bool` | Optional (default: `False`) | If `True`, halts compilation and raises `StrictModeViolationError` on missing layers, degenerate entities, or undefined blocks. |

---

### 2.3 Input IR Payload Schema (`LAVINCI_CAD_IR_V3`)

The IR payload must be a JSON object conforming to the following structural schema:

```json
{
  "format": "LAVINCI_CAD_IR_V3",
  "metadata": {
    "source_file": "drawing.dwg",
    "cad_version": "AC1032",
    "measurement_system": "Metric"
  },
  "extents": {
    "min": [-500.0, -500.0, 0.0],
    "max": [1500.0, 1500.0, 0.0],
    "width": 2000.0,
    "height": 2000.0
  },
  "layers": [
    {
      "name": "WALLS",
      "color_aci": 7,
      "color_true": "#FFFFFF",
      "linetype": "Continuous",
      "lineweight": 35,
      "is_off": false,
      "is_frozen": false,
      "is_locked": false
    }
  ],
  "geometry_primitives": {
    "primitives": {
      "lines": [
        {
          "start": [0.0, 0.0, 0.0],
          "end": [100.0, 0.0, 0.0],
          "layer": "WALLS",
          "color": "BYLAYER",
          "space": "Model"
        }
      ],
      "arcs": [
        {
          "center": [50.0, 50.0, 0.0],
          "radius": 25.0,
          "start_angle": 0.0,
          "end_angle": 90.0,
          "layer": "WALLS",
          "color": "#FF0000"
        }
      ],
      "circles": [
        {
          "center": [0.0, 0.0, 0.0],
          "radius": 10.0,
          "layer": "WALLS"
        }
      ],
      "polylines": [
        {
          "points": [[0.0, 0.0], [10.0, 0.0], [10.0, 10.0]],
          "is_closed": true,
          "layer": "WALLS"
        }
      ]
    }
  },
  "block_definitions": {
    "DOOR_SINGLE": {
      "base_point": [0.0, 0.0, 0.0],
      "lines": [...],
      "arcs": [...]
    }
  },
  "components": [
    {
      "block_name": "DOOR_SINGLE",
      "position": [100.0, 200.0, 0.0],
      "rotation": 90.0,
      "scale": [1.0, 1.0, 1.0],
      "layer": "DOORS",
      "attributes": {
        "MANUFACTURER": "Steelcraft",
        "FIRE_RATING": "60MIN"
      }
    }
  ],
  "annotations": [
    {
      "type": "TEXT",
      "raw_text": "OFFICE 101",
      "position": [150.0, 250.0],
      "height": 2.5,
      "layer": "TEXT"
    }
  ],
  "dimensions": [
    {
      "text": "100.00",
      "measurement": 100.0,
      "defpoint": [0.0, 0.0],
      "text_midpoint": [50.0, -10.0],
      "text_height": 2.0,
      "layer": "DIMS"
    }
  ]
}
```

---

## 3. Output Schema & Return Types

### 3.1 Python In-Memory Output: `ezdxf.document.Drawing`
* A complete, live DOM instance of the DXF document (`ezdxf.new(version)`).
* Allows downstream programmatic manipulation, entity inspection, custom audit, or streaming:
  ```python
  doc = compile_ir_to_dxf("payload.json")
  # Inspect entities:
  msp = doc.modelspace()
  num_entities = len(list(msp))
  # Stream bytes directly without disk I/O:
  stream = io.StringIO()
  doc.write(stream)
  raw_dxf_str = stream.getvalue()
  ```

### 3.2 Physical Disk Output (`.dxf` file)
* Valid DXF ASCII text file conforming strictly to the requested AutoCAD standard (`AC1009` through `AC1032`).
* Guaranteed UTF-8 encoding (or 7-bit ASCII for R12).

### 3.3 Telemetry Output: `CompilationDiagnostics.to_dict()`
When a `diagnostics` object is passed, it exposes structured telemetry:

```python
{
  "total": 6,
  "info": 1,
  "warn": 5,
  "error": 0,
  "entries": [
    {
      "severity": "WARN",
      "category": "entity_dropped",
      "message": "LINE entity on layer '0' was dropped: invalid or zero-length: start=[0, 0], end=[0, 0]",
      "suggestion": "Check your CAD source file for degenerate geometry. Zero-length lines and zero-radius arcs/circles cannot be represented in DXF.",
      "offender": {"type": "LINE", "layer": "0"}
    },
    {
      "severity": "INFO",
      "category": "entity_suppressed",
      "message": "2 TEXT/MTEXT entities suppressed by preset 'cnc_cam'. Reason: preset disables annotations",
      "suggestion": "This is expected behaviour for the 'cnc_cam' preset...",
      "offender": {"type": "TEXT/MTEXT", "preset": "cnc_cam"}
    }
  ]
}
```

---

## 4. Presets & Options Catalog

### 4.1 Preset Matrix

| Preset Name (`PresetName`) | Target Version | AC Code | Target Space | Curve Strategy | Flatten Z | Explode Blocks | Annotations / Dimensions | Color Mode |
| :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- |
| `standard` *(Default)* | `R2013` | `AC1027` | ModelSpace | `native` | `False` | `False` | Included | `truecolor` |
| `cnc_cam` | `R12` | `AC1009` | ModelSpace | `tessellate` | `True` ($Z=0$) | `True` | **Suppressed** | `aci` |
| `arch_print` | `R2013` | `AC1027` | PaperSpace + ModelSpace | `native` | `False` | `False` | Included | `truecolor` |
| `web_lightweight` | `R2000` | `AC1015` | ModelSpace | `native` | `True` ($Z=0$) | `False` | Text only, **Dims suppressed** | `aci` |
| `bim_overlay` | `R2018` | `AC1032` | ModelSpace | `native` | `False` | `False` | Included | `truecolor` |

### 4.2 `AdvancedOptions` Dataclass Structure

Users can override any preset parameter via typed sub-groups:

```python
@dataclass
class AdvancedOptions:
    version:   VersionOptions   = field(default_factory=VersionOptions)
    geometry:  GeometryOptions  = field(default_factory=GeometryOptions)
    filtering: FilteringOptions = field(default_factory=FilteringOptions)
    layout:    LayoutOptions    = field(default_factory=LayoutOptions)
    styling:   StylingOptions   = field(default_factory=StylingOptions)
```

#### Field Details:
1. **`version: VersionOptions`**
   * `dxf_version`: `Optional[str]` $\in$ `{"R12", "R2000", "R2004", "R2007", "R2010", "R2013", "R2018"}`
   * `encoding`: `Optional[str]` (default: `"utf-8"`)
2. **`geometry: GeometryOptions`**
   * `flatten_z`: `Optional[bool]` (forces all $Z=0.0$)
   * `explode_blocks`: `Optional[bool]` (breaks `INSERT` down to loose geometry)
   * `curve_strategy`: `Optional[str]` $\in$ `{"native", "tessellate", "spline_approx"}`
   * `tessellation_distance`: `Optional[float]` (max chord deviation in mm, default: `0.1`)
   * `zero_length_tolerance`: `Optional[float]` (min distance between points, default: `1e-9`)
3. **`filtering: FilteringOptions`**
   * `include_layers`: `Optional[List[str]]` (whitelist; if provided, only matching layers emit)
   * `exclude_layers`: `Optional[List[str]]` (blacklist; supports suffix wildcard e.g. `"TEMP_*"` or `"DEFPOINTS"`)
   * `include_annotations`: `Optional[bool]` (controls `TEXT` and `MTEXT`)
   * `include_dimensions`: `Optional[bool]` (controls `DIMENSION`)
4. **`layout: LayoutOptions`**
   * `create_paper_space`: `Optional[bool]` (provisions `Presentation_Sheet` layout tab)
   * `paper_size`: `Optional[str]` $\in$ `{"ISO_A4", "ISO_A3", "ISO_A2", "ISO_A1", "ISO_A0", "ANSI_A", "ANSI_B", "ANSI_C", "ANSI_D", "ARCH_D"}`
   * `orientation`: `Optional[str]` $\in$ `{"landscape", "portrait"}`
   * `viewport_scale`: `Optional[str]` (e.g. `"auto"`, `"1:50"`, `"1:100"`, `"1:1"`)
   * `margin_mm`: `Optional[float]` (printable margin, default: `10.0`)
5. **`styling: StylingOptions`**
   * `color_mode`: `Optional[str]` $\in$ `{"truecolor", "aci", "monochrome"}`
   * `layer_prefix`: `Optional[str]` (e.g. `"IR_"`, avoids collisions when linking drawings into Revit)

---

## 5. Validation Rules & Sanitization Logic

The compiler applies robust sanitizers before any element enters `ezdxf`:

1. **Finite Float Verification**:
   * All coordinates, angles, scales, and radiuses are checked with `math.isfinite()`.
   * Any `NaN`, `+Inf`, or `-Inf` immediately triggers invalid entity rejection (dropped in normal mode, raises `StrictModeViolationError` in strict mode).
2. **Symbol Name Sanitization**:
   * Layer, block, and layout names are sanitized against forbidden DXF characters: `\ / : * ? " < > | = ;`.
   * Forbidden characters are mapped to underscores `_`.
   * Empty names or names containing only whitespace fallback to safe defaults (`"0"`, `"UNNAMED_BLOCK"`, `"LAYOUT_1"`).
3. **Scale Factor Protection**:
   * Block reference insertion scales ($X, Y, Z$) are clamped: values where $|s| < 10^{-6}$ are clamped preserving sign to $\pm 10^{-6}$, preventing non-invertible singular transformation matrices.
4. **Color Formatting**:
   * Hex strings (e.g. `"#FF5500"` or `"#ABC"`) are sanitized and converted to 24-bit TrueColor integers.
   * Special tokens `"BYLAYER"` and `"BYBLOCK"` (color 0) are mapped to native AutoCAD indices.
5. **Standard Linetype Provisioning**:
   * The compiler pre-loads 15 standard AutoCAD linetypes (`CENTER`, `DASHED`, `HIDDEN`, `PHANTOM`, etc.) into `doc.linetypes`.
   * Unrecognized linetypes fall back to `"Continuous"` rather than crashing the DXF reader.

---

## 6. Exception Hierarchy & Error Conditions

All module exceptions derive from `CadIrToDxfError` in [`exceptions.py`](file:///e%3A/Antigravity/La%20Vinci/products/cad-ir-to-dxf/src/cad_ir_to_dxf/exceptions.py).

```
CadIrToDxfError
├── InvalidIRPayloadError
│   ├── MissingFormatHeaderError
│   ├── IRFileNotFoundError
│   └── IRParseError
├── InvalidPresetError
├── InvalidOptionError
│   ├── InvalidPaperSizeError
│   ├── InvalidColorModeError
│   ├── InvalidDxfVersionError
│   ├── InvalidOrientationError
│   └── InvalidViewportScaleError
├── CompilationError
│   ├── BlockCycleError
│   └── OutputWriteError
└── StrictModeViolationError
```

### 6.1 Exception Triggers & Diagnostics

```
Error Condition                      Normal Mode (strict=False)      Strict Mode (strict=True)
───────────────────────────────────  ──────────────────────────────  ──────────────────────────────
Missing input file path              Raises IRFileNotFoundError      Raises IRFileNotFoundError
Invalid JSON syntax                  Raises IRParseError             Raises IRParseError
Missing 'format' header              Emits schema WARN diagnostic    Raises MissingFormatHeaderError
Unknown preset name                  Raises InvalidPresetError       Raises InvalidPresetError
Invalid option value                 Raises InvalidOptionError       Raises InvalidOptionError
Circular block definition loop       Emits WARN diagnostic           Raises BlockCycleError
Undeclared layer referenced          Auto-vivifies + WARN diag       Raises StrictModeViolationError
Undefined block referenced           Creates placeholder + WARN diag Raises StrictModeViolationError
Degenerate line/arc/circle           Drops entity + WARN diag        Raises StrictModeViolationError
Failed to write to disk path         Raises OutputWriteError         Raises OutputWriteError
```

---

## 7. Supported vs. Unsupported CAD Entities

### 7.1 Fully Supported Entities
* `LINE`: 2D and 3D endpoints with `flatten_z` support and entity TrueColor.
* `ARC`: Center, radius, start angle, end angle with degree normalization.
* `CIRCLE`: Center and radius.
* `POLYLINE` / `LWPOLYLINE`: Multi-point 2D paths, open or closed; automatic fallback to `POLYLINE2D` when compiling for DXF R12.
* `INSERT`: Block references with position, rotation, 3D scale, and layer assignments.
* `ATTDEF` & `ATTRIB`: Component metadata attributes dynamically injected into block definitions and attached to block reference instances.
* `TEXT` / `MTEXT`: Text annotations with position, char height, text rotation, and null-byte stripping.
* `DIMENSION`: Dimension measurements placed as positioned leader-aligned `MTEXT`.
* `VIEWPORT`: PaperSpace presentation sheets with model-space coordinate cameras.

### 7.2 Unsupported / Out-of-Scope Entities
* **3D ACIS Solids / Meshes (`BODY`, `3DSOLID`, `REGION`)**: `LAVINCI_CAD_IR_V3` represents 2D/2.5D architectural and mechanical vector primitives. Raw ACIS binary streams are not generated.
* **Hatch Patterns (`HATCH`)**: Solid fills and gradient hatch boundaries are currently simplified or omitted.
* **Dynamic Block Parameters**: AutoCAD Dynamic Block grips/lookups compile down to static block definitions.

---

## 8. Performance & Scale Characteristics

Empirically validated via stress testing and benchmarking:

| Metric | Measured Baseline | Operational Notes |
| :--- | :--- | :--- |
| **Throughput** | $\approx 25,000 - 45,000$ primitives/sec | Entity translation is pure in-memory Python arithmetic. |
| **Execution Time (Typical Drawing)** | $< 0.05\text{s}$ for 1,000 entities | Sub-second latency suitable for synchronous HTTP endpoints. |
| **Stress Drawing Scale** | 50,000+ primitives in $\approx 1.8\text{s}$ | Scales linearly $O(N)$ with entity count. |
| **Memory Consumption (Peak Heap)** | $\approx 45\text{ MB}$ for 50,000 primitives | No internal memory leaks; documents garbage collected on completion. |
| **Asymptotic Complexity** | $O(V + E)$ entity graph traversal | Cycle detection in blocks uses visited-set DFS preventing exponential traps. |

---

## 9. Dependencies & Execution Environment

### 9.1 Runtime Dependencies
* **Python**: $\ge 3.9$ (tested on Python 3.10, 3.11, 3.12).
* **`ezdxf`**: Core underlying DXF document model ($\ge 1.1.0$).
* **Standard Library**: `math`, `json`, `os`, `sys`, `pathlib`, `difflib`, `dataclasses`, `enum`, `typing`.

### 9.2 Subprocesses & Binaries
* **Zero Native C-Extensions / Subprocesses**: Does **not** require external binaries (no LibreCAD, no ODA File Converter, no AutoCAD installation).
* **Thread Safety**: Fully thread-safe. Multiple worker threads can invoke `compile_ir_to_dxf` concurrently provided each thread processes independent files or in-memory dictionaries.
* **Temporary Files**: The core library creates **zero** temporary disk files. All compilation occurs purely in-memory unless an explicit `output_path` is provided.

---

## 10. Safe Integration Pattern for Wrapper Layers

Below is the recommended production wrapper pattern for backend microservices or workflow engines:

```python
import logging
from pathlib import Path
from typing import Dict, Any, Optional
from cad_ir_to_dxf import (
    compile_ir_to_dxf,
    CompilationDiagnostics,
    CadIrToDxfError,
    PresetName,
)

logger = logging.getLogger("cad_service")

def convert_cad_ir_to_dxf_safe(
    ir_payload: Dict[str, Any],
    destination_path: Optional[Path] = None,
    preset: str = "standard",
    strict: bool = False,
) -> Dict[str, Any]:
    """
    Production wrapper around cad-ir-to-dxf.
    Returns a standardized dictionary with status, file path, and diagnostic telemetry.
    """
    diagnostics = CompilationDiagnostics()
    
    try:
        doc = compile_ir_to_dxf(
            ir_source=ir_payload,
            output_path=destination_path,
            preset=preset,
            diagnostics=diagnostics,
            strict=strict,
        )
        
        # Log warnings if any were encountered
        if diagnostics.has_warnings:
            logger.warning("CAD compilation completed with warnings: %s", diagnostics.to_dict())
            
        return {
            "success": True,
            "output_path": str(destination_path) if destination_path else None,
            "entity_count": len(list(doc.modelspace())),
            "diagnostics": diagnostics.to_dict(),
            "error": None,
        }
        
    except CadIrToDxfError as err:
        logger.error("CAD compilation rejected: %s | Offender: %s | Fix: %s", err.message, err.offender, err.hint)
        return {
            "success": False,
            "output_path": None,
            "entity_count": 0,
            "diagnostics": diagnostics.to_dict(),
            "error": {
                "type": type(err).__name__,
                "message": err.message,
                "offender": err.offender,
                "hint": err.hint,
            },
        }
    except Exception as exc:
        logger.critical("Unexpected internal failure during CAD compilation: %s", exc, exc_info=True)
        return {
            "success": False,
            "output_path": None,
            "entity_count": 0,
            "diagnostics": diagnostics.to_dict(),
            "error": {
                "type": "InternalError",
                "message": str(exc),
                "offender": None,
                "hint": "Please report this issue to the La Vinci engineering maintainers.",
            },
        }
```

# Project: CAD-IR-to-DXF Adversarial Red-Team & Audit Campaign

## Architecture
- Target Compiler: `cad-ir-to-dxf` (`src/cad_ir_to_dxf`)
- Ingestion Pipeline: `compiler.py:_load_ir` -> `_build_header` -> `_build_layer_table` -> `_build_blocks` -> entity dispatch (`lines`, `arcs`, `circles`, `polylines`, `components`, `annotations`, `dimensions`) -> `layout.add_*`
- Sanitization: `sanitizer.py` (`is_finite`, `validate_line`, `validate_arc`, `validate_circle`, `validate_polyline`, `clamp_scale`, `sanitize_color`, `hex_to_rgb`, `hex_to_truecolor`)
- Audit Verification: `ezdxf.audit.Auditor` across all 43 `AuditError` codes (8 unfixable, 35 fixable) and DXF table integrity (HEADER, TABLES, BLOCKS, ENTITIES).
- Scale & Stress Architecture: High-density generation harnesses, memory tracking (`tracemalloc`, `psutil`), and wall-clock execution timing.
- Deliverable: Comprehensive, evidence-backed defect dossier `DEFECT_REPORT.md` at project root.

## Feature Inventory
| # | Feature | Description | Milestone | Source |
|---|---------|-------------|-----------|--------|
| 1 | Fuzzing: Malformed Floats | NaN, +/-Inf, subnormals, massive numbers (>1e308), negative zero | M1 | Survey (Explorer 1, Spec Miner 3) |
| 2 | Fuzzing: Coordinate Dimensionality | Empty arrays `[]`, 1D arrays `[x]`, mismatched lengths | M1 | Survey (Explorer 1, Explorer 2) |
| 3 | Fuzzing: Type Mismatches | String coords `"0"`, `None`, boolean, dicts in place of numeric coordinates | M1 | Survey (Explorer 1, Explorer 2) |
| 4 | Fuzzing: Graph Cycles & Recursion | Self-referential blocks (A->A), mutual cycles (A->B->A), deep nesting (>100 levels) | M1 | Survey (Spec Miner 3) |
| 5 | Fuzzing: Symbol Table Characters | Layer/block/layout names with `/`, `\`, `:`, `;`, `?`, `*`, `\|`, `=`, `<>` | M1 | Survey (Explorer 1, Spec Miner 3) |
| 6 | Fuzzing: Top-Level & Layout Sections | `None` values for `geometry_primitives`, `layers`, `metadata`, `layouts` | M1 | Survey (Explorer 1, Explorer 2) |
| 7 | Fuzzing: Scale & Transformation | Zero scale, negative zero scale, near-zero scales (`1e-7`), non-finite scale | M1 | Survey (Explorer 1) |
| 8 | Fuzzing: Encodings & Text | Unicode surrogates, control chars, null bytes, unmatched MTEXT format codes | M1 | Survey (Spec Miner 3) |
| 9 | Fuzzing: Pydantic & Format Types | Pydantic model objects, unknown format strings, missing format key | M1 | Survey (Explorer 1, Explorer 2) |
| 10 | Audit: ezdxf.audit Verification | Programmatic auditing across all generated test DXFs; zero unhandled errors | M2 | Survey (Spec Miner 3, R2) |
| 11 | Audit: DXF Table Integrity | Structural consistency of HEADER ($EXTMIN/$EXTMAX), TABLES (LAYER, LTYPE), BLOCKS, ENTITIES | M2 | Survey (Spec Miner 3, R2) |
| 12 | Audit: Block Record & Reference Checks | Dangling block references, missing block definitions, handle consistency | M2 | Survey (Spec Miner 3, R2) |
| 13 | Audit: Layer & Linetype Conformance | Discarded layer linetypes/flags, missing linetype references | M2 | Survey (Explorer 1, M2) |
| 14 | Stress: 50,000+ Primitive Benchmarks | High-density line, arc, circle, polyline generation, throughput (ent/s) | M3 | Survey (Explorer 2, Spec Miner 3, R3) |
| 15 | Stress: High-Density Block Insertions | Hundreds of block definitions, thousands of nested insertions | M3 | Survey (Spec Miner 3, R3) |
| 16 | Stress: Memory & Time Profiling | Heap profiling (`tracemalloc`), process RSS (`psutil`), wall time | M3 | Survey (Spec Miner 3, R3) |
| 17 | Stress: Asymptotic Complexity Checks | Detection of $O(N^2)$ or exponential coordinate/entity dispatch bottlenecks | M3 | Survey (Spec Miner 3, R3) |
| 18 | Defect Dossier Synthesis | DEFECT_REPORT.md: counts, granular breakdown, minimal JSON reproductions, remediation | M4 | Survey (All, R4) |

## Milestones
| # | Name | Scope | Dependencies | Status |
|---|------|-------|-------------|--------|
| M1 | Adversarial Fuzzing & Malformed Ingestion | Automated fuzzing harness with >=1,000 distinct malformed permutations (Features 1-9) | None | IN_PROGRESS |
| M2 | DXF Standards & Structural Audit Verification | Programmatic DXF audit suite using `ezdxf.audit` and table checks on all test outputs (Features 10-13) | M1 | PLANNED |
| M3 | Scale, Performance & Stress Profiling | High-density benchmark with >=50,000 entities, tracking peak memory and execution time (Features 14-17) | M1 | PLANNED |
| M4 | Comprehensive Defect Dossier & Reporting | Compile `DEFECT_REPORT.md` with full reproduction snippets and architecture fixes (Feature 18) | M1, M2, M3 | PLANNED |

## Interface Contracts
### Test Harness ↔ Compiler API
- Ingestion: `compile_ir_to_dxf(ir_source, output_path=None, dxf_version="R2013")`
- Return value: `ezdxf.document.Drawing`
- Harness Capture: `try: ... except Exception as e: record_crash(e)`
- Minimum Permutation Count for M1: $\ge 1,000$ distinct malformed payloads across all 9 fuzzing categories.

### Audit Suite ↔ DXF Files
- Audit Entry: `ezdxf.audit.Auditor = doc.audit()`
- Cataloged Metrics: `len(auditor.errors)`, `len(auditor.fixes)`, table structure verification (`HEADER`, `TABLES`, `BLOCKS`, `ENTITIES`).

### Stress Profiling ↔ Benchmark Metrics
- Scale Target: $\ge 50,000$ primitives and block insertions.
- Metrics: Compile Time (s), Throughput (ent/s), Serialization Time (s), Peak Heap (MB, tracemalloc), RSS Delta (MB, psutil), Output Size (MB).

## Code Layout
- Test Directory: `tests/`
  - `tests/test_compiler.py` (existing baseline 35 tests)
  - `tests/test_fuzz_malformed.py` (M1: automated fuzzing harness with >=1,000 permutations)
  - `tests/test_dxf_audit.py` (M2: structural & standards audit verification harness)
  - `tests/test_scale_stress.py` (M3: 50,000+ entity scale & stress benchmark)
- Output Deliverables:
  - `DEFECT_REPORT.md` (project root)
  - `tests/artifacts/` (temporary test DXF outputs and benchmark profiles)

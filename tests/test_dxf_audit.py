"""
tests/test_dxf_audit.py — Automated DXF Standards & Structural Audit Verification Suite.

Milestone 2 (R2) Verification Suite for cad-ir-to-dxf.

This test suite programmatically audits all DXF outputs generated during testing
using ezdxf.audit.Auditor and verifies the structural consistency of standard DXF
tables:
  - HEADER: $ACADVER, $INSUNITS, $MEASUREMENT, $HANDSEED, and non-finite NaN/Inf float corruption in $EXTMIN/$EXTMAX.
  - TABLES: LAYER, LTYPE, STYLE, BLOCK_RECORD tables. Audits whether custom layer
    linetypes and flags (is_locked, is_frozen, is_off) are preserved or silently dropped.
  - BLOCKS: Block definitions, modelspace *Model_Space, paperspace *Paper_Space,
    nested blocks, block cycles (INVALID_BLOCK_REFERENCE_CYCLE = 104), and attribute retention (ATTRIB/ATTDEF).
  - ENTITIES: Handle uniqueness and validity, layer reference existence, coordinate finiteness.
  - TARGET VERSIONS: Comprehensive compliance across R12, R2000, R2004, R2007, R2010, R2013, R2018.

Execution Modes:
  1. Standard library unittest:
     python -m unittest tests.test_dxf_audit
  2. Standalone execution:
     python tests/test_dxf_audit.py
"""

from __future__ import annotations

import json
import math
import os
import sys
import tempfile
import unittest
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

# Ensure src/ is on sys.path for direct script execution
SRC_DIR = Path(__file__).resolve().parent.parent / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

import ezdxf
from ezdxf.audit import AuditError, Auditor
from ezdxf.document import Drawing
from ezdxf.lldxf.const import DXFValueError, DXFVersionError

from cad_ir_to_dxf.compiler import compile_ir_to_dxf

# AUTHORITATIVE PATHS
PROJECT_ROOT = Path(__file__).resolve().parent.parent
EXAMPLES_DIR = PROJECT_ROOT.parent / "cad-extractor-ir" / "examples"
BASELINE_SAMPLE_DXF = PROJECT_ROOT / "blueprint_sample_output.dxf"
BLUEPRINT_IR_PATH = EXAMPLES_DIR / "blueprint_sample_ir.json"

VERSION_MAP = {
    "R12": "AC1009",
    "R2000": "AC1015",
    "R2004": "AC1018",
    "R2007": "AC1021",
    "R2010": "AC1024",
    "R2013": "AC1027",
    "R2018": "AC1032",
}


# ──────────────────────────────────────────────────────────────────────────────
# 1. Structural Audit Infrastructure & Result Models
# ──────────────────────────────────────────────────────────────────────────────

@dataclass
class AuditIssue:
    code: int
    code_name: str
    message: str
    entity: Optional[str] = None
    data: Optional[Any] = None
    severity: str = "ERROR"  # "ERROR" or "FIX"


@dataclass
class TableViolation:
    table: str
    element: str
    rule: str
    details: str
    severity: str = "VIOLATION"  # "VIOLATION" or "DEFICIT"


@dataclass
class DXFAuditReport:
    name: str
    dxf_version: str
    total_entities: int = 0
    ezdxf_errors: List[AuditIssue] = field(default_factory=list)
    ezdxf_fixes: List[AuditIssue] = field(default_factory=list)
    table_violations: List[TableViolation] = field(default_factory=list)
    header_values: Dict[str, Any] = field(default_factory=dict)
    is_valid: bool = True

    @property
    def total_ezdxf_issues(self) -> int:
        return len(self.ezdxf_errors) + len(self.ezdxf_fixes)

    @property
    def total_table_violations(self) -> int:
        return len(self.table_violations)


class AuditMetricsCollector:
    """Global registry aggregating audit metrics across all test executions."""
    _instance: Optional[AuditMetricsCollector] = None

    def __init__(self):
        self.reports: List[DXFAuditReport] = []
        self.error_catalog: Dict[int, Dict[str, Any]] = {}
        self.fix_catalog: Dict[int, Dict[str, Any]] = {}
        self.deficit_catalog: Dict[str, int] = {}

    @classmethod
    def get_instance(cls) -> AuditMetricsCollector:
        if cls._instance is None:
            cls._instance = AuditMetricsCollector()
        return cls._instance

    def record(self, report: DXFAuditReport) -> None:
        self.reports.append(report)
        for err in report.ezdxf_errors:
            if err.code not in self.error_catalog:
                self.error_catalog[err.code] = {
                    "code_name": err.code_name,
                    "count": 0,
                    "sample_messages": set(),
                }
            self.error_catalog[err.code]["count"] += 1
            self.error_catalog[err.code]["sample_messages"].add(err.message)

        for fix in report.ezdxf_fixes:
            if fix.code not in self.fix_catalog:
                self.fix_catalog[fix.code] = {
                    "code_name": fix.code_name,
                    "count": 0,
                    "sample_messages": set(),
                }
            self.fix_catalog[fix.code]["count"] += 1
            self.fix_catalog[fix.code]["sample_messages"].add(fix.message)

        for tv in report.table_violations:
            key = f"{tv.table}:{tv.rule}"
            self.deficit_catalog[key] = self.deficit_catalog.get(key, 0) + 1

    def summary_stats(self) -> Dict[str, Any]:
        total_dxfs = len(self.reports)
        total_errors = sum(len(r.ezdxf_errors) for r in self.reports)
        total_fixes = sum(len(r.ezdxf_fixes) for r in self.reports)
        total_table_viols = sum(len(r.table_violations) for r in self.reports)
        return {
            "total_dxfs_audited": total_dxfs,
            "total_auditor_errors": total_errors,
            "total_auditor_fixes": total_fixes,
            "total_table_violations": total_table_viols,
            "distinct_error_codes": len(self.error_catalog),
            "distinct_fix_codes": len(self.fix_catalog),
        }


class DXFStructuralAuditor:
    """
    Deep CAD Standards and Structural Integrity Auditor.
    
    Validates DXF Drawing instances across:
    1. ezdxf.audit.Auditor (errors and fixes)
    2. HEADER ($ACADVER, $INSUNITS, $MEASUREMENT, $HANDSEED, $EXTMIN, $EXTMAX)
    3. TABLES (LAYER, LTYPE, STYLE, BLOCK_RECORD)
    4. BLOCKS (*Model_Space, *Paper_Space, block cycles, attribute retention)
    5. ENTITIES (handles, layer references, coordinate finiteness)
    """

    PROHIBITED_SYMBOL_CHARS = set('<>/\\":;?*|=`\'')

    @classmethod
    def audit_document(
        cls,
        doc: Drawing,
        name: str = "anonymous_drawing",
        ir_source: Optional[Dict[str, Any]] = None,
        expected_version: Optional[str] = None,
        expected_units: Optional[int] = None,
    ) -> DXFAuditReport:
        dxf_version = doc.dxfversion
        total_ents = len(list(doc.modelspace()))
        report = DXFAuditReport(
            name=name,
            dxf_version=dxf_version,
            total_entities=total_ents,
        )

        # 1. Run ezdxf Auditor
        cls._run_ezdxf_auditor(doc, report)

        # 2. Audit HEADER section
        cls._audit_header(doc, report, expected_version, expected_units, ir_source)

        # 3. Audit TABLES section
        cls._audit_tables(doc, report, ir_source)

        # 4. Audit BLOCKS section
        cls._audit_blocks(doc, report, ir_source)

        # 5. Audit ENTITIES section
        cls._audit_entities(doc, report)

        # Set validity flag: True if zero hard ezdxf errors and zero hard violations
        if report.ezdxf_errors or any(v.severity == "VIOLATION" for v in report.table_violations):
            report.is_valid = False

        # Record into global metrics
        AuditMetricsCollector.get_instance().record(report)
        return report

    @classmethod
    def _run_ezdxf_auditor(cls, doc: Drawing, report: DXFAuditReport) -> None:
        auditor = doc.audit()
        for err in auditor.errors:
            code_val = int(err.code)
            code_name = err.code.name if hasattr(err.code, "name") else str(err.code)
            report.ezdxf_errors.append(
                AuditIssue(
                    code=code_val,
                    code_name=code_name,
                    message=err.message,
                    entity=str(err.entity) if err.entity else None,
                    data=err.data,
                    severity="ERROR",
                )
            )

        for fix in auditor.fixes:
            code_val = int(fix.code)
            code_name = fix.code.name if hasattr(fix.code, "name") else str(fix.code)
            report.ezdxf_fixes.append(
                AuditIssue(
                    code=code_val,
                    code_name=code_name,
                    message=fix.message,
                    entity=str(fix.entity) if fix.entity else None,
                    data=fix.data,
                    severity="FIX",
                )
            )

    @classmethod
    def _audit_header(
        cls,
        doc: Drawing,
        report: DXFAuditReport,
        expected_version: Optional[str],
        expected_units: Optional[int],
        ir_source: Optional[Dict[str, Any]],
    ) -> None:
        # $ACADVER check
        try:
            acadver = doc.header.get("$ACADVER")
        except Exception:
            acadver = doc.dxfversion
        report.header_values["$ACADVER"] = acadver

        if expected_version:
            expected_acadver = VERSION_MAP.get(expected_version, expected_version)
            if acadver != expected_acadver:
                report.table_violations.append(
                    TableViolation(
                        table="HEADER",
                        element="$ACADVER",
                        rule="VERSION_MISMATCH",
                        details=f"Expected $ACADVER={expected_acadver} for {expected_version}, got {acadver}",
                        severity="VIOLATION",
                    )
                )

        # $INSUNITS check
        try:
            insunits = doc.header.get("$INSUNITS")
        except Exception:
            insunits = None
        report.header_values["$INSUNITS"] = insunits

        if expected_units is not None and insunits != expected_units:
            # Note: R12 does not export $INSUNITS
            if doc.dxfversion != "AC1009":
                report.table_violations.append(
                    TableViolation(
                        table="HEADER",
                        element="$INSUNITS",
                        rule="UNITS_MISMATCH",
                        details=f"Expected $INSUNITS={expected_units}, got {insunits}",
                        severity="VIOLATION",
                    )
                )

        # $EXTMIN / $EXTMAX check — Audit for NaN / Inf corruption
        for ext_key in ["$EXTMIN", "$EXTMAX"]:
            try:
                coords = doc.header.get(ext_key)
            except Exception:
                coords = None
            report.header_values[ext_key] = coords
            if coords:
                for idx, c in enumerate(coords):
                    if not math.isfinite(c):
                        report.table_violations.append(
                            TableViolation(
                                table="HEADER",
                                element=ext_key,
                                rule="NON_FINITE_EXTENTS",
                                details=f"Corrupted non-finite float ({c}) at index {idx} in {ext_key}",
                                severity="VIOLATION",
                            )
                        )

        # $HANDSEED check (R13+)
        if doc.dxfversion > "AC1009":
            try:
                handseed_str = doc.header.get("$HANDSEED")
                report.header_values["$HANDSEED"] = handseed_str
                if handseed_str:
                    handseed_val = int(handseed_str, 16)
                    max_db_handle = 0
                    for h in doc.entitydb.keys():
                        try:
                            max_db_handle = max(max_db_handle, int(h, 16))
                        except ValueError:
                            pass
                    if handseed_val <= max_db_handle:
                        report.table_violations.append(
                            TableViolation(
                                table="HEADER",
                                element="$HANDSEED",
                                rule="HANDSEED_TOO_LOW",
                                details=f"$HANDSEED ({handseed_str} = {handseed_val}) <= max handle ({hex(max_db_handle)})",
                                severity="DEFICIT",
                            )
                        )
            except Exception as e:
                report.table_violations.append(
                    TableViolation(
                        table="HEADER",
                        element="$HANDSEED",
                        rule="HANDSEED_AUDIT_ERROR",
                        details=str(e),
                        severity="DEFICIT",
                    )
                )

    @classmethod
    def _audit_tables(
        cls,
        doc: Drawing,
        report: DXFAuditReport,
        ir_source: Optional[Dict[str, Any]],
    ) -> None:
        # 1. LAYER Table
        layers = doc.layers
        layer_names = {l.dxf.name for l in layers}

        # Rule: Mandatory Layer '0'
        if "0" not in layer_names:
            report.table_violations.append(
                TableViolation(
                    table="TABLES",
                    element="LAYER",
                    rule="MANDATORY_LAYER_ZERO_MISSING",
                    details="Layer '0' is mandatory in all DXF files but was not found in doc.layers",
                    severity="VIOLATION",
                )
            )

        # Rule: Layer names cannot contain prohibited characters
        for name in layer_names:
            invalid_chars = set(name) & cls.PROHIBITED_SYMBOL_CHARS
            if invalid_chars:
                report.table_violations.append(
                    TableViolation(
                        table="TABLES",
                        element=f"LAYER:{name}",
                        rule="INVALID_LAYER_NAME_CHARS",
                        details=f"Layer name contains prohibited CAD characters: {invalid_chars}",
                        severity="VIOLATION",
                    )
                )

        # Rule: Layer linetype must exist in doc.linetypes
        ltype_names = {lt.dxf.name for lt in doc.linetypes}
        for layer in layers:
            lt_ref = layer.dxf.linetype
            if lt_ref and lt_ref not in ltype_names:
                report.table_violations.append(
                    TableViolation(
                        table="TABLES",
                        element=f"LAYER:{layer.dxf.name}",
                        rule="DANGLING_LAYER_LINETYPE",
                        details=f"Layer references linetype '{lt_ref}' which does not exist in LTYPE table",
                        severity="VIOLATION",
                    )
                )

        # Spec Deficit Audit: Check if IR layer flags and linetypes were preserved or silently dropped
        if ir_source and "layers" in ir_source:
            for ir_layer in ir_source["layers"]:
                lname = ir_layer.get("name", "0")
                if not lname:
                    lname = "0"
                if lname in doc.layers:
                    dxf_layer = doc.layers.get(lname)
                    # Audit linetype retention
                    expected_lt = ir_layer.get("linetype", "Continuous") or "Continuous"
                    if expected_lt.upper() != "CONTINUOUS" and dxf_layer.dxf.linetype == "Continuous":
                        report.table_violations.append(
                            TableViolation(
                                table="TABLES",
                                element=f"LAYER:{lname}",
                                rule="LAYER_LINETYPE_SILENTLY_DROPPED",
                                details=f"IR specified linetype '{expected_lt}' but layer has '{dxf_layer.dxf.linetype}'",
                                severity="DEFICIT",
                            )
                        )
                    # Audit lock retention
                    if ir_layer.get("is_locked", False) and not dxf_layer.is_locked():
                        report.table_violations.append(
                            TableViolation(
                                table="TABLES",
                                element=f"LAYER:{lname}",
                                rule="LAYER_FLAG_LOCKED_DROPPED",
                                details="IR specified is_locked=True but layer is not locked in DXF",
                                severity="DEFICIT",
                            )
                        )
                    # Audit freeze retention
                    if ir_layer.get("is_frozen", False) and not dxf_layer.is_frozen():
                        report.table_violations.append(
                            TableViolation(
                                table="TABLES",
                                element=f"LAYER:{lname}",
                                rule="LAYER_FLAG_FROZEN_DROPPED",
                                details="IR specified is_frozen=True but layer is not frozen in DXF",
                                severity="DEFICIT",
                            )
                        )
                    # Audit off retention
                    if ir_layer.get("is_off", False) and not dxf_layer.is_off():
                        report.table_violations.append(
                            TableViolation(
                                table="TABLES",
                                element=f"LAYER:{lname}",
                                rule="LAYER_FLAG_OFF_DROPPED",
                                details="IR specified is_off=True but layer is not off in DXF",
                                severity="DEFICIT",
                            )
                        )

        # 2. LTYPE Table
        # Rule: Mandatory linetypes ByLayer, ByBlock, Continuous
        canonical_ltypes = {lt.lower() for lt in ltype_names}
        for req_lt in ["bylayer", "byblock", "continuous"]:
            if req_lt not in canonical_ltypes:
                report.table_violations.append(
                    TableViolation(
                        table="TABLES",
                        element="LTYPE",
                        rule="MANDATORY_LINETYPE_MISSING",
                        details=f"Mandatory linetype '{req_lt}' not found in LTYPE table",
                        severity="VIOLATION",
                    )
                )

        # 3. STYLE Table
        # Rule: Mandatory 'Standard' text style
        style_names = {s.dxf.name.lower() for s in doc.styles}
        if "standard" not in style_names:
            report.table_violations.append(
                TableViolation(
                    table="TABLES",
                    element="STYLE",
                    rule="MANDATORY_STYLE_MISSING",
                    details="Mandatory text style 'Standard' not found in STYLE table",
                    severity="VIOLATION",
                )
            )

        # 4. BLOCK_RECORD Table (R13+)
        if doc.dxfversion > "AC1009":
            br_names = {br.dxf.name for br in doc.block_records}
            for req_br in ["*Model_Space", "*Paper_Space"]:
                if req_br not in br_names:
                    report.table_violations.append(
                        TableViolation(
                            table="TABLES",
                            element="BLOCK_RECORD",
                            rule="MANDATORY_BLOCK_RECORD_MISSING",
                            details=f"Mandatory block record '{req_br}' missing from BLOCK_RECORD table",
                            severity="VIOLATION",
                        )
                    )

    @classmethod
    def _audit_blocks(
        cls,
        doc: Drawing,
        report: DXFAuditReport,
        ir_source: Optional[Dict[str, Any]],
    ) -> None:
        # 1. Modelspace & Paperspace block presence
        if "*Model_Space" not in doc.blocks:
            report.table_violations.append(
                TableViolation(
                    table="BLOCKS",
                    element="*Model_Space",
                    rule="MODELSPACE_BLOCK_MISSING",
                    details="Container block '*Model_Space' is missing from doc.blocks",
                    severity="VIOLATION",
                )
            )
        if "*Paper_Space" not in doc.blocks:
            report.table_violations.append(
                TableViolation(
                    table="BLOCKS",
                    element="*Paper_Space",
                    rule="PAPERSPACE_BLOCK_MISSING",
                    details="Container block '*Paper_Space' is missing from doc.blocks",
                    severity="VIOLATION",
                )
            )

        # 2. Block definitions base point finiteness
        for blk in doc.blocks:
            bp = blk.base_point
            for idx, coord in enumerate(bp):
                if not math.isfinite(coord):
                    report.table_violations.append(
                        TableViolation(
                            table="BLOCKS",
                            element=f"BLOCK:{blk.name}",
                            rule="NON_FINITE_BLOCK_BASE_POINT",
                            details=f"Block base_point[{idx}] is non-finite: {coord}",
                            severity="VIOLATION",
                        )
                    )

        # 3. Block Reference Cycle Detection (Independent DFS)
        block_references: Dict[str, Set[str]] = {}
        for blk in doc.blocks:
            refs = set()
            for entity in blk:
                if entity.dxftype() == "INSERT":
                    refs.add(entity.dxf.name)
            block_references[blk.name] = refs

        def _dfs_cycle(node: str, visited: Set[str], stack: List[str]) -> Optional[List[str]]:
            visited.add(node)
            stack.append(node)
            for neighbor in block_references.get(node, []):
                if neighbor not in visited:
                    cycle = _dfs_cycle(neighbor, visited, stack)
                    if cycle:
                        return cycle
                elif neighbor in stack:
                    idx = stack.index(neighbor)
                    return stack[idx:] + [neighbor]
            stack.pop()
            return None

        visited_nodes: Set[str] = set()
        for bname in block_references:
            if bname not in visited_nodes:
                cycle_found = _dfs_cycle(bname, visited_nodes, [])
                if cycle_found:
                    report.table_violations.append(
                        TableViolation(
                            table="BLOCKS",
                            element=f"BLOCK:{bname}",
                            rule="CIRCULAR_BLOCK_REFERENCE_DETECTED",
                            details=f"Block reference cycle detected: {' -> '.join(cycle_found)}",
                            severity="VIOLATION",
                        )
                    )
                    break

        # 4. Attribute Retention Deficit (CV-20)
        # Verify whether component instance attributes in IR resulted in ATTRIB entities in the DXF
        if ir_source and "components" in ir_source:
            doc_attrib_tags = {
                e.dxf.tag for e in doc.entitydb.values()
                if e.dxftype() == "ATTRIB" and e.dxf.hasattr("tag")
            }
            for comp in ir_source["components"]:
                expected_attribs = comp.get("attributes", {})
                bname = comp.get("block_name", "UNKNOWN")
                for expected_tag in expected_attribs:
                    if expected_tag not in doc_attrib_tags:
                        report.table_violations.append(
                            TableViolation(
                                table="BLOCKS",
                                element=f"COMPONENT:{bname}",
                                rule="COMPONENT_ATTRIBUTE_SILENTLY_DROPPED",
                                details=f"Attribute '{expected_tag}' on component '{bname}' was not retained as ATTRIB entity in DXF",
                                severity="DEFICIT",
                            )
                        )
                        break

    @classmethod
    def _audit_entities(cls, doc: Drawing, report: DXFAuditReport) -> None:
        entitydb = doc.entitydb
        seen_handles: Set[str] = set()
        layer_names = {l.dxf.name for l in doc.layers}

        # 1. Audit Handle Uniqueness and Validity
        for handle, entity in entitydb.items():
            if not handle or not isinstance(handle, str):
                report.table_violations.append(
                    TableViolation(
                        table="ENTITIES",
                        element=str(entity),
                        rule="INVALID_HANDLE_FORMAT",
                        details=f"Entity has invalid handle: {handle}",
                        severity="VIOLATION",
                    )
                )
                continue

            try:
                handle_int = int(handle, 16)
                if handle_int <= 0:
                    report.table_violations.append(
                        TableViolation(
                            table="ENTITIES",
                            element=f"HANDLE:{handle}",
                            rule="ZERO_OR_NEGATIVE_HANDLE",
                            details=f"Entity handle must be positive hex, got {handle}",
                            severity="VIOLATION",
                        )
                    )
            except ValueError:
                report.table_violations.append(
                    TableViolation(
                        table="ENTITIES",
                        element=f"HANDLE:{handle}",
                        rule="NON_HEX_HANDLE",
                        details=f"Handle string '{handle}' is not valid hexadecimal",
                        severity="VIOLATION",
                    )
                )

            if handle in seen_handles:
                report.table_violations.append(
                    TableViolation(
                        table="ENTITIES",
                        element=f"HANDLE:{handle}",
                        rule="DUPLICATE_ENTITY_HANDLE",
                        details=f"Handle '{handle}' appears multiple times in entity database",
                        severity="VIOLATION",
                    )
                )
            seen_handles.add(handle)

        # 2. Audit Living Entities across all layouts and blocks
        all_entities = []
        for layout in doc.layouts:
            all_entities.extend(list(layout))
        for block in doc.blocks:
            all_entities.extend(list(block))

        for entity in all_entities:
            # Layer reference existence
            if entity.dxf.hasattr("layer"):
                ent_layer = entity.dxf.layer
                if ent_layer not in layer_names:
                    report.table_violations.append(
                        TableViolation(
                            table="ENTITIES",
                            element=f"{entity.dxftype()}(#{entity.dxf.handle})",
                            rule="DANGLING_ENTITY_LAYER_REF",
                            details=f"Entity references layer '{ent_layer}' which is not in doc.layers",
                            severity="VIOLATION",
                        )
                    )

            # Coordinate Finiteness Checks
            cls._check_entity_finiteness(entity, report)

    @classmethod
    def _check_entity_finiteness(cls, entity: Any, report: DXFAuditReport) -> None:
        dxftype = entity.dxftype()
        handle = entity.dxf.handle if entity.dxf.hasattr("handle") else "None"

        coord_attrs = ["start", "end", "center", "insert", "defpoint", "vtx0", "vtx1", "vtx2", "vtx3"]
        for attr in coord_attrs:
            if entity.dxf.hasattr(attr):
                val = entity.dxf.get(attr)
                if isinstance(val, (tuple, list)):
                    for idx, c in enumerate(val):
                        if not math.isfinite(c):
                            report.table_violations.append(
                                TableViolation(
                                    table="ENTITIES",
                                    element=f"{dxftype}(#{handle})",
                                    rule="NON_FINITE_COORDINATE",
                                    details=f"Attribute '{attr}[{idx}]' contains non-finite value: {c}",
                                    severity="VIOLATION",
                                )
                            )

        scalar_attrs = ["radius", "rotation", "char_height", "height", "xscale", "yscale", "zscale"]
        for sattr in scalar_attrs:
            if entity.dxf.hasattr(sattr):
                sval = entity.dxf.get(sattr)
                if isinstance(sval, (int, float)) and not math.isfinite(sval):
                    report.table_violations.append(
                        TableViolation(
                            table="ENTITIES",
                            element=f"{dxftype}(#{handle})",
                            rule="NON_FINITE_SCALAR",
                            details=f"Attribute '{sattr}' contains non-finite scalar: {sval}",
                            severity="VIOLATION",
                        )
                    )

        # Polylines: check vertex points
        if dxftype in ("LWPOLYLINE", "POLYLINE"):
            try:
                for vidx, pt in enumerate(entity.get_points()):
                    for cidx, c in enumerate(pt[:2]):
                        if not math.isfinite(c):
                            report.table_violations.append(
                                TableViolation(
                                    table="ENTITIES",
                                    element=f"{dxftype}(#{handle})",
                                    rule="NON_FINITE_VERTEX",
                                    details=f"Polyline vertex {vidx} coord {cidx} is non-finite: {c}",
                                    severity="VIOLATION",
                                )
                            )
            except Exception:
                pass


# ──────────────────────────────────────────────────────────────────────────────
# 2. Test Suite Implementations
# ──────────────────────────────────────────────────────────────────────────────

class TestBaselineDXFAudit(unittest.TestCase):
    """Audits the pre-existing sample output DXF and compiled baseline IR files."""

    def test_audit_preexisting_blueprint_output_dxf(self):
        """Audits the repo's blueprint_sample_output.dxf using ezdxf.audit.Auditor."""
        if not BASELINE_SAMPLE_DXF.exists():
            self.skipTest(f"Baseline file missing: {BASELINE_SAMPLE_DXF}")

        doc = ezdxf.readfile(str(BASELINE_SAMPLE_DXF))
        report = DXFStructuralAuditor.audit_document(doc, name="blueprint_sample_output.dxf")

        self.assertEqual(len(report.ezdxf_errors), 0, f"Errors in sample output DXF: {report.ezdxf_errors}")
        self.assertEqual(len(report.ezdxf_fixes), 0, f"Fixes in sample output DXF: {report.ezdxf_fixes}")
        # Structural hard violations
        violations = [v for v in report.table_violations if v.severity == "VIOLATION"]
        self.assertEqual(len(violations), 0, f"Structural violations in sample DXF: {violations}")

    def test_audit_compiled_blueprint_sample_ir(self):
        """Compiles blueprint_sample_ir.json and verifies zero audit errors."""
        if not BLUEPRINT_IR_PATH.exists():
            self.skipTest("blueprint_sample_ir.json missing")

        with open(BLUEPRINT_IR_PATH, encoding="utf-8") as fh:
            ir_data = json.load(fh)

        exp_units = ir_data.get("metadata", {}).get("units")
        doc = compile_ir_to_dxf(ir_data, dxf_version="R2013")
        report = DXFStructuralAuditor.audit_document(
            doc,
            name="compiled_blueprint_sample_ir",
            ir_source=ir_data,
            expected_version="R2013",
            expected_units=exp_units,
        )

        self.assertEqual(len(report.ezdxf_errors), 0, f"Auditor errors: {report.ezdxf_errors}")
        self.assertEqual(len(report.ezdxf_fixes), 0, f"Auditor fixes: {report.ezdxf_fixes}")

        violations = [v for v in report.table_violations if v.severity == "VIOLATION"]
        self.assertEqual(len(violations), 0, f"Table structural violations: {violations}")

    def test_audit_all_example_ir_files(self):
        """Compiles and audits all 8 available example IR files."""
        if not EXAMPLES_DIR.exists():
            self.skipTest(f"Examples directory not found: {EXAMPLES_DIR}")

        example_files = list(EXAMPLES_DIR.glob("*.json"))
        self.assertGreaterEqual(len(example_files), 1, "No example IR files found to test")

        for ir_file in example_files:
            with self.subTest(ir_file=ir_file.name):
                with open(ir_file, encoding="utf-8") as fh:
                    ir_data = json.load(fh)

                doc = compile_ir_to_dxf(ir_data)
                report = DXFStructuralAuditor.audit_document(
                    doc,
                    name=f"compiled_{ir_file.stem}",
                    ir_source=ir_data,
                )

                self.assertEqual(
                    len(report.ezdxf_errors), 0,
                    f"Auditor errors in {ir_file.name}: {report.ezdxf_errors}"
                )
                self.assertEqual(
                    len(report.ezdxf_fixes), 0,
                    f"Auditor fixes in {ir_file.name}: {report.ezdxf_fixes}"
                )

    def test_baseline_dxf_serialization_roundtrip_audit(self):
        """Writes compiled DXF to disk, reads it back, and verifies audit integrity."""
        if not BLUEPRINT_IR_PATH.exists():
            self.skipTest("blueprint_sample_ir.json missing")

        with tempfile.TemporaryDirectory() as tmpdir:
            out_path = Path(tmpdir) / "roundtrip_test.dxf"
            compile_ir_to_dxf(str(BLUEPRINT_IR_PATH), output_path=str(out_path))

            self.assertTrue(out_path.exists())
            self.assertGreater(out_path.stat().st_size, 1000)

            reloaded_doc = ezdxf.readfile(str(out_path))
            report = DXFStructuralAuditor.audit_document(
                reloaded_doc,
                name="roundtrip_blueprint_sample",
            )
            self.assertEqual(len(report.ezdxf_errors), 0)
            self.assertEqual(len(report.ezdxf_fixes), 0)


class TestHeaderTableAudit(unittest.TestCase):
    """Audits the DXF HEADER section ($ACADVER, $INSUNITS, $MEASUREMENT, $EXTMIN, $EXTMAX, $HANDSEED)."""

    def test_header_acadver_target_matching(self):
        """Verifies $ACADVER matches the requested target DXF version."""
        ir = {
            "format": "LAVINCI_CAD_IR_V3",
            "metadata": {"source_file": "test.dwg", "units": 4},
            "geometry_primitives": {"primitives": {"lines": []}},
        }
        for ver, acadver in [
            ("R2000", "AC1015"),
            ("R2004", "AC1018"),
            ("R2007", "AC1021"),
            ("R2010", "AC1024"),
            ("R2013", "AC1027"),
            ("R2018", "AC1032"),
        ]:
            with self.subTest(version=ver):
                doc = compile_ir_to_dxf(ir, dxf_version=ver)
                self.assertEqual(doc.dxfversion, acadver)
                self.assertEqual(doc.header.get("$ACADVER"), acadver)

    def test_header_insunits_and_measurement_mapping(self):
        """Verifies $INSUNITS and $MEASUREMENT correctly reflect IR metadata units."""
        cases = [
            ({"units": 1, "measurement_system": "Imperial"}, 1, 0),
            ({"units": 4, "measurement_system": "Metric"}, 4, 1),
            ({"units": 6, "measurement_system": "Metric"}, 6, 1),
            ({"units": 0, "measurement_system": "Imperial"}, 0, 0),
        ]
        for meta, exp_units, exp_meas in cases:
            ir = {
                "format": "LAVINCI_CAD_IR_V3",
                "metadata": meta,
                "geometry_primitives": {"primitives": {"lines": []}},
            }
            doc = compile_ir_to_dxf(ir)
            self.assertEqual(doc.header.get("$INSUNITS"), exp_units)
            self.assertEqual(doc.header.get("$MEASUREMENT"), exp_meas)

    def test_header_handseed_strictly_exceeds_max_entity_handle(self):
        """Audits that $HANDSEED in modern DXFs strictly exceeds every handle in the entitydb."""
        ir = {
            "format": "LAVINCI_CAD_IR_V3",
            "metadata": {"source_file": "test.dwg"},
            "geometry_primitives": {
                "primitives": {
                    "lines": [
                        {"start": [0, 0], "end": [1, 1], "layer": "0"},
                        {"start": [1, 1], "end": [2, 2], "layer": "0"},
                    ]
                }
            },
        }
        doc = compile_ir_to_dxf(ir, dxf_version="R2013")
        handseed_str = doc.header.get("$HANDSEED")
        self.assertIsNotNone(handseed_str)
        handseed_val = int(handseed_str, 16)

        max_handle = max(int(h, 16) for h in doc.entitydb.keys())
        self.assertGreater(
            handseed_val, max_handle,
            f"$HANDSEED ({handseed_str} = {handseed_val}) must exceed max handle ({hex(max_handle)})"
        )

    def test_header_valid_extents_finiteness(self):
        """Audits that finite extents in IR are properly recorded into $EXTMIN/$EXTMAX."""
        ir = {
            "format": "LAVINCI_CAD_IR_V3",
            "metadata": {"source_file": "test.dwg"},
            "extents": {"min": [-100.5, -50.25], "max": [200.75, 150.125]},
            "geometry_primitives": {"primitives": {"lines": []}},
        }
        doc = compile_ir_to_dxf(ir)
        report = DXFStructuralAuditor.audit_document(doc, name="finite_extents")
        extmin = doc.header.get("$EXTMIN")
        extmax = doc.header.get("$EXTMAX")
        self.assertEqual(extmin, (-100.5, -50.25, 0.0))
        self.assertEqual(extmax, (200.75, 150.125, 0.0))

        ext_viols = [v for v in report.table_violations if v.rule == "NON_FINITE_EXTENTS"]
        self.assertEqual(len(ext_viols), 0)

    def test_header_non_finite_nan_inf_extents_audit_detection(self):
        """
        Audits compiler behavior when IR contains NaN/Inf extents.
        Verifies that DXFStructuralAuditor detects and catalogs the float corruption
        which ezdxf.audit.Auditor completely overlooks.
        """
        ir = {
            "format": "LAVINCI_CAD_IR_V3",
            "metadata": {"source_file": "test.dwg"},
            "extents": {"min": [float("nan"), 0.0], "max": [float("inf"), 100.0]},
            "geometry_primitives": {"primitives": {"lines": []}},
        }
        doc = compile_ir_to_dxf(ir)
        # ezdxf auditor misses this
        ez_auditor = doc.audit()
        self.assertEqual(len(ez_auditor.errors), 0)
        self.assertEqual(len(ez_auditor.fixes), 0)

        # Our structural auditor detects the float corruption
        report = DXFStructuralAuditor.audit_document(doc, name="corrupted_nan_inf_extents")
        ext_viols = [v for v in report.table_violations if v.rule == "NON_FINITE_EXTENTS"]
        self.assertGreaterEqual(len(ext_viols), 2, "Failed to detect NaN and Inf in header extents")


class TestTablesStructuralAudit(unittest.TestCase):
    """Audits the DXF TABLES section: LAYER, LTYPE, STYLE, and BLOCK_RECORD."""

    def test_layer_table_mandatory_layer_zero(self):
        """Verifies layer '0' is registered in TABLES."""
        doc = ezdxf.new("R2013")
        report = DXFStructuralAuditor.audit_document(doc)
        zero_viols = [v for v in report.table_violations if v.rule == "MANDATORY_LAYER_ZERO_MISSING"]
        self.assertEqual(len(zero_viols), 0)
        self.assertIn("0", doc.layers)

    def test_layer_table_valid_character_names(self):
        """Audits that layer names do not contain illegal characters `<>/\\\":;?*|=`."""
        ir = {
            "format": "LAVINCI_CAD_IR_V3",
            "metadata": {"source_file": "test.dwg"},
            "layers": [
                {"name": "ARCH_WALL", "color_aci": 1},
                {"name": "ELEC_LIGHT", "color_aci": 2},
            ],
            "geometry_primitives": {"primitives": {"lines": []}},
        }
        doc = compile_ir_to_dxf(ir)
        report = DXFStructuralAuditor.audit_document(doc, name="valid_layers")
        char_viols = [v for v in report.table_violations if v.rule == "INVALID_LAYER_NAME_CHARS"]
        self.assertEqual(len(char_viols), 0)

    def test_layer_flag_and_linetype_retention_audit(self):
        """
        Audits whether layer flags (is_locked, is_frozen, is_off) and custom linetypes
        defined in IR are retained or silently dropped by the compiler (CV-19 deficit).
        """
        ir = {
            "format": "LAVINCI_CAD_IR_V3",
            "metadata": {"source_file": "test.dwg"},
            "layers": [
                {"name": "LOCKED_LAYER", "color_aci": 1, "is_locked": True, "linetype": "DASHED"},
                {"name": "FROZEN_LAYER", "color_aci": 2, "is_frozen": True, "linetype": "HIDDEN"},
                {"name": "OFF_LAYER", "color_aci": 3, "is_off": True, "linetype": "CENTER"},
            ],
            "geometry_primitives": {"primitives": {"lines": []}},
        }
        doc = compile_ir_to_dxf(ir)
        report = DXFStructuralAuditor.audit_document(doc, name="layer_flag_audit", ir_source=ir)

        deficits = [v for v in report.table_violations if v.severity == "DEFICIT"]
        rules = {v.rule for v in deficits}

        self.assertIn(
            "LAYER_LINETYPE_SILENTLY_DROPPED", rules,
            "Audit failed to detect that custom layer linetype was dropped to Continuous"
        )
        self.assertIn(
            "LAYER_FLAG_LOCKED_DROPPED", rules,
            "Audit failed to detect that layer locked flag was dropped"
        )
        self.assertIn(
            "LAYER_FLAG_FROZEN_DROPPED", rules,
            "Audit failed to detect that layer frozen flag was dropped"
        )
        self.assertIn(
            "LAYER_FLAG_OFF_DROPPED", rules,
            "Audit failed to detect that layer off flag was dropped"
        )

    def test_ltype_table_mandatory_linetypes(self):
        """Audits that ByLayer, ByBlock, and Continuous are present in the LTYPE table."""
        doc = ezdxf.new("R2013", setup=True)
        report = DXFStructuralAuditor.audit_document(doc)
        lt_viols = [v for v in report.table_violations if v.rule == "MANDATORY_LINETYPE_MISSING"]
        self.assertEqual(len(lt_viols), 0)

    def test_style_table_standard_present(self):
        """Audits that mandatory text style 'Standard' is registered."""
        doc = ezdxf.new("R2013")
        report = DXFStructuralAuditor.audit_document(doc)
        st_viols = [v for v in report.table_violations if v.rule == "MANDATORY_STYLE_MISSING"]
        self.assertEqual(len(st_viols), 0)

    def test_block_record_table_mandatory_records(self):
        """Audits that *Model_Space and *Paper_Space exist in BLOCK_RECORD table."""
        doc = ezdxf.new("R2013")
        report = DXFStructuralAuditor.audit_document(doc)
        br_viols = [v for v in report.table_violations if v.rule == "MANDATORY_BLOCK_RECORD_MISSING"]
        self.assertEqual(len(br_viols), 0)


class TestBlocksStructuralAudit(unittest.TestCase):
    """Audits BLOCKS table integrity, modelspace/paperspace containers, cycles, and attributes."""

    def test_modelspace_and_paperspace_containers_alive(self):
        """Verifies *Model_Space and *Paper_Space blocks exist and are queryable."""
        doc = ezdxf.new("R2013")
        report = DXFStructuralAuditor.audit_document(doc)
        block_viols = [v for v in report.table_violations if v.table == "BLOCKS"]
        self.assertEqual(len(block_viols), 0)
        self.assertIn("*Model_Space", doc.blocks)
        self.assertIn("*Paper_Space", doc.blocks)

    def test_block_definitions_internal_primitives_retention(self):
        """Audits that user block definitions retain their internal geometric primitives."""
        ir = {
            "format": "LAVINCI_CAD_IR_V3",
            "metadata": {"source_file": "test.dwg"},
            "block_definitions": {
                "VALVE": {
                    "base_point": [10.0, 10.0, 0.0],
                    "lines": [
                        {"start": [0, 0], "end": [5, 5], "layer": "0"},
                        {"start": [5, 5], "end": [10, 0], "layer": "0"},
                    ],
                    "circles": [{"center": [5, 2.5], "radius": 2.0, "layer": "0"}],
                }
            },
            "components": [
                {"block_name": "VALVE", "position": [100, 100, 0], "layer": "0"}
            ],
            "geometry_primitives": {"primitives": {"lines": []}},
        }
        doc = compile_ir_to_dxf(ir)
        report = DXFStructuralAuditor.audit_document(doc, name="block_primitives_test")
        self.assertEqual(len(report.ezdxf_errors), 0)
        self.assertIn("VALVE", doc.blocks)
        valve_blk = doc.blocks["VALVE"]
        self.assertEqual(valve_blk.base_point, (10.0, 10.0, 0.0))
        types = [e.dxftype() for e in valve_blk]
        self.assertIn("LINE", types)
        self.assertIn("CIRCLE", types)

    def test_nested_block_insertions(self):
        """Audits nested block references (Block A inserting Block B)."""
        doc = ezdxf.new("R2013")
        b_child = doc.blocks.new("CHILD_BLOCK")
        b_child.add_circle((0, 0), radius=5.0)

        b_parent = doc.blocks.new("PARENT_BLOCK")
        b_parent.add_blockref("CHILD_BLOCK", (10, 10))

        msp = doc.modelspace()
        msp.add_blockref("PARENT_BLOCK", (50, 50))

        report = DXFStructuralAuditor.audit_document(doc, name="nested_blocks")
        self.assertEqual(len(report.ezdxf_errors), 0)
        self.assertEqual(len(report.ezdxf_fixes), 0)

    def test_block_cycle_detection_self_reference(self):
        """
        Tests Auditor detection of self-referential block cycle (A -> A).
        Verifies AuditError.INVALID_BLOCK_REFERENCE_CYCLE (code 104) is caught.
        """
        doc = ezdxf.new("R2013")
        b = doc.blocks.new("SELF_CYCLE")
        b.add_blockref("SELF_CYCLE", (0, 0))

        report = DXFStructuralAuditor.audit_document(doc, name="self_referential_cycle")
        self.assertEqual(len(report.ezdxf_errors), 1)
        err = report.ezdxf_errors[0]
        self.assertEqual(err.code, 104)
        self.assertEqual(err.code_name, "INVALID_BLOCK_REFERENCE_CYCLE")
        self.assertIn("SELF_CYCLE", err.message)

    def test_block_cycle_detection_mutual_recursion(self):
        """
        Tests Auditor detection of mutual recursive cycle (A -> B -> A).
        Verifies both block records are flagged with code 104.
        """
        doc = ezdxf.new("R2013")
        bA = doc.blocks.new("CYCLE_A")
        bB = doc.blocks.new("CYCLE_B")
        bA.add_blockref("CYCLE_B", (0, 0))
        bB.add_blockref("CYCLE_A", (0, 0))

        report = DXFStructuralAuditor.audit_document(doc, name="mutual_recursive_cycle")
        self.assertEqual(len(report.ezdxf_errors), 2)
        codes = [e.code for e in report.ezdxf_errors]
        self.assertEqual(codes, [104, 104])

    def test_block_attribute_retention_audit(self):
        """
        Audits component instance attributes in IR to verify whether ATTRIB entities
        are generated or silently dropped on the INSERT entity (CV-20 deficit).
        """
        ir = {
            "format": "LAVINCI_CAD_IR_V3",
            "metadata": {"source_file": "test.dwg"},
            "block_definitions": {
                "PUMP": {
                    "base_point": [0, 0, 0],
                    "lines": [{"start": [0, 0], "end": [1, 1], "layer": "0"}],
                }
            },
            "components": [
                {
                    "block_name": "PUMP",
                    "position": [10.0, 20.0, 0.0],
                    "attributes": {"TAG": "P-101", "GPM": "500"},
                    "layer": "0",
                }
            ],
            "geometry_primitives": {"primitives": {"lines": []}},
        }
        doc = compile_ir_to_dxf(ir)
        report = DXFStructuralAuditor.audit_document(doc, name="block_attr_retention", ir_source=ir)

        attr_deficits = [v for v in report.table_violations if v.rule == "COMPONENT_ATTRIBUTE_SILENTLY_DROPPED"]
        self.assertGreaterEqual(
            len(attr_deficits), 1,
            "Audit failed to detect that component attributes were dropped on INSERT"
        )


class TestEntitiesStructuralAudit(unittest.TestCase):
    """Audits ENTITIES handle uniqueness, handle validity, layer references, and boundary coords."""

    def test_entity_handle_uniqueness_and_hex_validity(self):
        """Audits that all entities in doc.entitydb possess valid and unique hex handles."""
        ir = {
            "format": "LAVINCI_CAD_IR_V3",
            "metadata": {"source_file": "test.dwg"},
            "geometry_primitives": {
                "primitives": {
                    "lines": [
                        {"start": [0, 0], "end": [i, i], "layer": "0"} for i in range(1, 20)
                    ]
                }
            },
        }
        doc = compile_ir_to_dxf(ir)
        report = DXFStructuralAuditor.audit_document(doc, name="handle_audit")

        handle_viols = [
            v for v in report.table_violations
            if v.rule in ("INVALID_HANDLE_FORMAT", "ZERO_OR_NEGATIVE_HANDLE", "NON_HEX_HANDLE", "DUPLICATE_ENTITY_HANDLE")
        ]
        self.assertEqual(len(handle_viols), 0, f"Handle violations found: {handle_viols}")

    def test_entity_layer_reference_existence(self):
        """Audits that every entity references an existing layer in doc.layers."""
        ir = {
            "format": "LAVINCI_CAD_IR_V3",
            "metadata": {"source_file": "test.dwg"},
            "layers": [{"name": "WALLS", "color_aci": 1}],
            "geometry_primitives": {
                "primitives": {
                    "lines": [{"start": [0, 0], "end": [10, 10], "layer": "WALLS"}],
                    "circles": [{"center": [5, 5], "radius": 2.0, "layer": "0"}],
                }
            },
        }
        doc = compile_ir_to_dxf(ir)
        report = DXFStructuralAuditor.audit_document(doc, name="layer_ref_test")
        dangling_viols = [v for v in report.table_violations if v.rule == "DANGLING_ENTITY_LAYER_REF"]
        self.assertEqual(len(dangling_viols), 0)

    def test_boundary_coordinates_audit(self):
        """Audits compilation under boundary conditions: zero extents, negative, and large coordinates."""
        ir = {
            "format": "LAVINCI_CAD_IR_V3",
            "metadata": {"source_file": "test.dwg"},
            "extents": {"min": [-100000.0, -50000.0], "max": [1000000.0, 500000.0]},
            "geometry_primitives": {
                "primitives": {
                    "lines": [
                        {"start": [-100000.0, -50000.0], "end": [0.0, 0.0], "layer": "0"},
                        {"start": [0.0, 0.0], "end": [1000000.0, 500000.0], "layer": "0"},
                    ],
                    "circles": [{"center": [0.0, 0.0], "radius": 500.0, "layer": "0"}],
                }
            },
        }
        doc = compile_ir_to_dxf(ir)
        report = DXFStructuralAuditor.audit_document(doc, name="boundary_coords")
        self.assertEqual(len(report.ezdxf_errors), 0)
        self.assertEqual(len(report.ezdxf_fixes), 0)

        coord_viols = [v for v in report.table_violations if "NON_FINITE" in v.rule]
        self.assertEqual(len(coord_viols), 0)

    def test_multi_layout_entity_routing(self):
        """Audits entities routed across multiple spaces (Model space and Paper space layouts)."""
        ir = {
            "format": "LAVINCI_CAD_IR_V3",
            "metadata": {"source_file": "test.dwg"},
            "layouts": [
                {"name": "Layout1", "is_active": True},
            ],
            "geometry_primitives": {
                "primitives": {
                    "lines": [
                        {"start": [0, 0], "end": [10, 10], "layer": "0", "space": "Model"},
                        {"start": [0, 0], "end": [20, 20], "layer": "0", "space": "Layout1"},
                    ]
                }
            },
        }
        doc = compile_ir_to_dxf(ir)
        report = DXFStructuralAuditor.audit_document(doc, name="multi_layout")
        self.assertEqual(len(report.ezdxf_errors), 0)

        msp = doc.modelspace()
        self.assertEqual(len([e for e in msp if e.dxftype() == "LINE"]), 1)

        psp = doc.layout("Layout1")
        self.assertEqual(len([e for e in psp if e.dxftype() == "LINE"]), 1)


class TestVersionComplianceAudit(unittest.TestCase):
    """Audits DXF standards compliance across R12, R2000, R2004, R2007, R2010, R2013, R2018."""

    MODERN_VERSIONS = [
        ("R2000", "AC1015"),
        ("R2004", "AC1018"),
        ("R2007", "AC1021"),
        ("R2010", "AC1024"),
        ("R2013", "AC1027"),
        ("R2018", "AC1032"),
    ]

    def test_modern_versions_compliance(self):
        """Audits that modern DXF versions compile and pass auditor with zero errors."""
        ir = {
            "format": "LAVINCI_CAD_IR_V3",
            "metadata": {"source_file": "test.dwg", "units": 4},
            "layers": [{"name": "LAYER_1", "color_aci": 1}],
            "geometry_primitives": {
                "primitives": {
                    "lines": [{"start": [0, 0], "end": [10, 10], "layer": "LAYER_1"}],
                    "circles": [{"center": [5, 5], "radius": 2.5, "layer": "LAYER_1"}],
                    "polylines": [{"points": [[0, 0], [10, 0], [10, 10]], "layer": "LAYER_1"}],
                }
            },
        }
        for ver, exp_acadver in self.MODERN_VERSIONS:
            with self.subTest(version=ver):
                doc = compile_ir_to_dxf(ir, dxf_version=ver)
                report = DXFStructuralAuditor.audit_document(
                    doc,
                    name=f"version_test_{ver}",
                    expected_version=ver,
                )
                self.assertEqual(doc.dxfversion, exp_acadver)
                self.assertEqual(len(report.ezdxf_errors), 0)
                self.assertEqual(len(report.ezdxf_fixes), 0)

    def test_r12_clean_primitive_compilation(self):
        """Audits that R12 compiles cleanly when containing only R12-supported primitives (LINE, CIRCLE, ARC)."""
        ir = {
            "format": "LAVINCI_CAD_IR_V3",
            "metadata": {"source_file": "test.dwg", "units": 4},
            "layers": [{"name": "R12_LAYER", "color_aci": 1}],
            "geometry_primitives": {
                "primitives": {
                    "lines": [{"start": [0, 0], "end": [10, 10], "layer": "R12_LAYER"}],
                    "circles": [{"center": [5, 5], "radius": 2.5, "layer": "R12_LAYER"}],
                    "arcs": [{"center": [0, 0], "radius": 5.0, "start_angle": 0, "end_angle": 90, "layer": "R12_LAYER"}],
                }
            },
        }
        doc = compile_ir_to_dxf(ir, dxf_version="R12")
        report = DXFStructuralAuditor.audit_document(
            doc,
            name="r12_clean_primitives",
            expected_version="R12",
        )
        self.assertEqual(doc.dxfversion, "AC1009")
        self.assertEqual(len(report.ezdxf_errors), 0)
        self.assertEqual(len(report.ezdxf_fixes), 0)

    def test_r12_unsupported_features_version_error_audit(self):
        """
        Audits compiler behavior when targeting R12 with unsupported modern entities
        (LWPOLYLINE and MTEXT). Verifies DXFVersionError is raised.
        """
        # 1. Polyline in R12
        ir_poly = {
            "format": "LAVINCI_CAD_IR_V3",
            "metadata": {"source_file": "test.dwg"},
            "geometry_primitives": {
                "primitives": {
                    "lines": [],
                    "polylines": [{"points": [[0, 0], [10, 10]], "layer": "0"}],
                }
            },
        }
        with self.assertRaises(DXFVersionError) as cm_poly:
            compile_ir_to_dxf(ir_poly, dxf_version="R12")
        self.assertIn("LWPOLYLINE", str(cm_poly.exception))

        # 2. MTEXT in R12
        ir_mtext = {
            "format": "LAVINCI_CAD_IR_V3",
            "metadata": {"source_file": "test.dwg"},
            "annotations": [{"type": "MTEXT", "raw_text": "Sample", "position": [0, 0], "space": "Model"}],
            "geometry_primitives": {"primitives": {"lines": []}},
        }
        with self.assertRaises(DXFVersionError) as cm_mtext:
            compile_ir_to_dxf(ir_mtext, dxf_version="R12")
        self.assertIn("MTEXT", str(cm_mtext.exception))


class TestAuditorRuleCataloging(unittest.TestCase):
    """
    Directly exercises and catalogs specific ezdxf.audit.Auditor error and fix codes.
    Verifies that Auditor classifies and repairs them according to the specification.
    """

    def test_catalog_undefined_linetype_100(self):
        """Verifies Auditor detects UNDEFINED_LINETYPE (code 100) and repairs it."""
        doc = ezdxf.new("R2013")
        msp = doc.modelspace()
        msp.add_line((0, 0), (1, 1), dxfattribs={"linetype": "NONEXISTENT_LTYPE"})

        report = DXFStructuralAuditor.audit_document(doc, name="undefined_linetype_test")
        fixes = [f for f in report.ezdxf_fixes if f.code == 100]
        self.assertEqual(len(fixes), 1)
        self.assertEqual(fixes[0].code_name, "UNDEFINED_LINETYPE")

    def test_catalog_undefined_text_style_102(self):
        """Verifies Auditor detects UNDEFINED_TEXT_STYLE (code 102) and repairs it."""
        doc = ezdxf.new("R2013")
        msp = doc.modelspace()
        msp.add_text("Audit Text", dxfattribs={"style": "NONEXISTENT_STYLE"})

        report = DXFStructuralAuditor.audit_document(doc, name="undefined_style_test")
        fixes = [f for f in report.ezdxf_fixes if f.code == 102]
        self.assertEqual(len(fixes), 1)
        self.assertEqual(fixes[0].code_name, "UNDEFINED_TEXT_STYLE")

    def test_catalog_undefined_block_103(self):
        """Verifies Auditor detects UNDEFINED_BLOCK (code 103) and deletes dangling INSERT."""
        doc = ezdxf.new("R2013")
        msp = doc.modelspace()
        msp.add_blockref("GHOST_BLOCK", (0, 0))

        report = DXFStructuralAuditor.audit_document(doc, name="undefined_block_test")
        fixes = [f for f in report.ezdxf_fixes if f.code == 103]
        self.assertEqual(len(fixes), 1)
        self.assertEqual(fixes[0].code_name, "UNDEFINED_BLOCK")

    def test_catalog_invalid_layer_name_203(self):
        """Verifies Auditor detects INVALID_LAYER_NAME (code 203) as unfixable error."""
        doc = ezdxf.new("R2013")
        msp = doc.modelspace()
        line = msp.add_line((0, 0), (1, 1))
        line.dxf.unprotected_set("layer", "INVALID/LAYER/NAME")

        report = DXFStructuralAuditor.audit_document(doc, name="invalid_layer_name_test")
        errors = [e for e in report.ezdxf_errors if e.code == 203]
        self.assertEqual(len(errors), 1)
        self.assertEqual(errors[0].code_name, "INVALID_LAYER_NAME")

    def test_catalog_invalid_color_index_204(self):
        """Verifies Auditor detects INVALID_COLOR_INDEX (code 204) and repairs it."""
        doc = ezdxf.new("R2013")
        msp = doc.modelspace()
        line = msp.add_line((0, 0), (1, 1))
        line.dxf.unprotected_set("color", 300)  # Valid range is 0-257

        report = DXFStructuralAuditor.audit_document(doc, name="invalid_color_index_test")
        fixes = [f for f in report.ezdxf_fixes if f.code == 204]
        self.assertEqual(len(fixes), 1)
        self.assertEqual(fixes[0].code_name, "INVALID_COLOR_INDEX")

    def test_catalog_invalid_lineweight_205(self):
        """Verifies Auditor detects INVALID_LINEWEIGHT (code 205) and repairs it."""
        doc = ezdxf.new("R2013")
        msp = doc.modelspace()
        line = msp.add_line((0, 0), (1, 1))
        line.dxf.unprotected_set("lineweight", 123)  # Non-standard lineweight

        report = DXFStructuralAuditor.audit_document(doc, name="invalid_lineweight_test")
        fixes = [f for f in report.ezdxf_fixes if f.code == 205]
        self.assertEqual(len(fixes), 1)
        self.assertEqual(fixes[0].code_name, "INVALID_LINEWEIGHT")

    def test_catalog_invalid_extrusion_vector_210(self):
        """Verifies Auditor detects INVALID_EXTRUSION_VECTOR (code 210) and repairs it."""
        doc = ezdxf.new("R2013")
        circle = doc.modelspace().add_circle((0, 0), 5.0)
        circle.dxf.unprotected_set("extrusion", (0, 0, 0))

        report = DXFStructuralAuditor.audit_document(doc, name="invalid_extrusion_test")
        fixes = [f for f in report.ezdxf_fixes if f.code == 210]
        self.assertEqual(len(fixes), 1)
        self.assertEqual(fixes[0].code_name, "INVALID_EXTRUSION_VECTOR")

    def test_catalog_invalid_owner_handle_16_and_202(self):
        """Verifies Auditor detects invalid owner handle and repairs database link."""
        doc = ezdxf.new("R2013")
        line = doc.modelspace().add_line((0, 0), (1, 1))
        line.dxf.owner = "DEADBEEF"

        report = DXFStructuralAuditor.audit_document(doc, name="invalid_owner_test")
        fix_codes = {f.code for f in report.ezdxf_fixes}
        self.assertTrue(16 in fix_codes or 202 in fix_codes)


# ──────────────────────────────────────────────────────────────────────────────
# 3. Standalone Execution & Metric Summary Reporter
# ──────────────────────────────────────────────────────────────────────────────

def print_audit_summary_table():
    collector = AuditMetricsCollector.get_instance()
    stats = collector.summary_stats()

    print("\n" + "=" * 80)
    print("      CAD-IR-TO-DXF AUTOMATED STANDARDS & STRUCTURAL AUDIT REPORT")
    print("=" * 80)
    print(f"Total DXF Documents Audited:    {stats['total_dxfs_audited']}")
    print(f"Total ezdxf Auditor Errors:     {stats['total_auditor_errors']}")
    print(f"Total ezdxf Auditor Fixes:      {stats['total_auditor_fixes']}")
    print(f"Total Structural Deficits:      {stats['total_table_violations']}")
    print("-" * 80)

    if collector.error_catalog:
        print("\n[!] CATALOGED AUDIT ERRORS (UNFIXABLE CORRUPTIONS):")
        print(f"  {'Code':<6} {'Name':<32} {'Count':<6} {'Sample Message'}")
        print("  " + "-" * 76)
        for code, data in sorted(collector.error_catalog.items()):
            msg = list(data["sample_messages"])[0] if data["sample_messages"] else ""
            if len(msg) > 35:
                msg = msg[:32] + "..."
            print(f"  {code:<6} {data['code_name']:<32} {data['count']:<6} {msg}")

    if collector.fix_catalog:
        print("\n[*] CATALOGED AUDIT FIXES (AUTO-REPAIRED BY AUDITOR):")
        print(f"  {'Code':<6} {'Name':<32} {'Count':<6} {'Sample Message'}")
        print("  " + "-" * 76)
        for code, data in sorted(collector.fix_catalog.items()):
            msg = list(data["sample_messages"])[0] if data["sample_messages"] else ""
            if len(msg) > 35:
                msg = msg[:32] + "..."
            print(f"  {code:<6} {data['code_name']:<32} {data['count']:<6} {msg}")

    if collector.deficit_catalog:
        print("\n[-] TABLE CONSISTENCY & COMPLIANCE DEFICITS:")
        print(f"  {'Deficit Key':<48} {'Occurrences':<12}")
        print("  " + "-" * 60)
        for key, count in sorted(collector.deficit_catalog.items(), key=lambda x: -x[1]):
            print(f"  {key:<48} {count:<12}")

    print("=" * 80 + "\n")


def run_standalone():
    """Runs the test suite and displays the comprehensive audit report."""
    loader = unittest.TestLoader()
    suite = loader.loadTestsFromModule(sys.modules[__name__])
    runner = unittest.TextTestRunner(verbosity=2)
    result = runner.run(suite)
    print_audit_summary_table()
    return 0 if result.wasSuccessful() else 1


if __name__ == "__main__":
    sys.exit(run_standalone())

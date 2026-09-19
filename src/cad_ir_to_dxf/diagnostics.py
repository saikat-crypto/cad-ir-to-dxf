"""
diagnostics.py — Compilation Diagnostic Collector for cad-ir-to-dxf.

The CompilationDiagnostics object is threaded through the compilation
pipeline to collect non-fatal events that developers need to be aware of:

  - Layer auto-created (referenced but not declared in IR)
  - Entity dropped (zero-length, NaN coordinate, or degenerate geometry)
  - Entity suppressed (by preset filter: e.g. cnc_cam drops text/dims)
  - Block auto-vivified (INSERT referencing undefined block)
  - Layer excluded by filter rule

Each diagnostic entry has a severity (INFO / WARN / ERROR), a human-readable
message, an actionable suggestion, and the entity/layer that triggered it.

Usage in code
-------------
    diag = CompilationDiagnostics()
    doc = compile_ir_to_dxf(ir, preset="standard", diagnostics=diag)
    diag.print_report()          # pretty-print to stdout
    report = diag.to_dict()      # machine-readable dict
    diag.raise_if_errors()       # raise CompilationError if severity ERROR present

Usage via CLI
-------------
    cad-ir-to-dxf plan.json --preset cnc_cam --diagnostics
    # → prints compilation report to stderr after completion
"""

from __future__ import annotations

import sys
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional


# ──────────────────────────────────────────────────────────────────────────────
# Severity
# ──────────────────────────────────────────────────────────────────────────────

class Severity(str, Enum):
    INFO  = "INFO"    # Something was adjusted/corrected automatically
    WARN  = "WARN"    # Something was silently dropped or skipped — worth reviewing
    ERROR = "ERROR"   # A structural problem that may corrupt output


# ──────────────────────────────────────────────────────────────────────────────
# Single Diagnostic Entry
# ──────────────────────────────────────────────────────────────────────────────

@dataclass
class DiagnosticEntry:
    """A single diagnostic event recorded during compilation."""

    severity: Severity
    category: str
    """Short category tag. e.g. 'layer_auto_created', 'entity_dropped', 'entity_suppressed'."""

    message: str
    """Human-readable description of what happened."""

    suggestion: str = ""
    """Actionable suggestion for how to clean up or avoid this in the future."""

    offender: Optional[Any] = None
    """The specific layer name, entity type, block name, or value that triggered this."""

    def __str__(self) -> str:
        icon = {"INFO": "ℹ", "WARN": "⚠", "ERROR": "✖"}.get(self.severity.value, "•")
        parts = [f"  {icon} [{self.severity.value}] {self.message}"]
        if self.offender is not None:
            parts.append(f"      Entity/Value : {self.offender!r}")
        if self.suggestion:
            parts.append(f"      Suggestion   : {self.suggestion}")
        return "\n".join(parts)


# ──────────────────────────────────────────────────────────────────────────────
# Diagnostics Collector
# ──────────────────────────────────────────────────────────────────────────────

@dataclass
class CompilationDiagnostics:
    """
    Collects all non-fatal compilation events. Pass an instance to
    compile_ir_to_dxf() to receive a structured report after compilation.

    Example
    -------
    >>> diag = CompilationDiagnostics()
    >>> doc = compile_ir_to_dxf("plan.json", preset="cnc_cam", diagnostics=diag)
    >>> diag.print_report()

    Compilation Report
    ──────────────────
      ℹ [INFO] 18 TEXT/MTEXT annotation entities suppressed by preset 'cnc_cam'.
          Suggestion: Annotations are excluded from cnc_cam output to prevent ...
      ⚠ [WARN] Layer 'EQUIPMENT' was referenced by 3 entities but not declared...
    """

    entries: List[DiagnosticEntry] = field(default_factory=list)

    # ── Factory helpers called internally by the compiler ────────────────────

    def layer_auto_created(self, layer_name: str) -> None:
        self.entries.append(DiagnosticEntry(
            severity=Severity.WARN,
            category="layer_auto_created",
            message=f"Layer '{layer_name}' was referenced by an entity but not declared in the IR layers table. Auto-created with default color ACI 7.",
            suggestion=(
                f"Add a layer definition for '{layer_name}' to the 'layers' list "
                "in your IR payload to control its linetype, color, and visibility explicitly."
            ),
            offender=layer_name,
        ))

    def entity_dropped_degenerate(self, entity_type: str, layer: str, reason: str) -> None:
        self.entries.append(DiagnosticEntry(
            severity=Severity.WARN,
            category="entity_dropped",
            message=f"{entity_type} entity on layer '{layer}' was dropped: {reason}",
            suggestion=(
                "Check your CAD source file for degenerate geometry. "
                "Zero-length lines and zero-radius arcs/circles cannot be represented in DXF."
            ),
            offender={"type": entity_type, "layer": layer},
        ))

    def entity_suppressed_by_preset(self, entity_type: str, count: int, preset: str, reason: str) -> None:
        self.entries.append(DiagnosticEntry(
            severity=Severity.INFO,
            category="entity_suppressed",
            message=f"{count} {entity_type} entit{'y' if count == 1 else 'ies'} suppressed by preset '{preset}'. Reason: {reason}",
            suggestion=(
                f"This is expected behaviour for the '{preset}' preset. "
                "To retain these entities, choose a different preset (e.g. 'standard') "
                "or override via AdvancedOptions(filtering=FilteringOptions(include_annotations=True))."
            ),
            offender={"type": entity_type, "preset": preset},
        ))

    def layer_excluded_by_filter(self, layer_name: str, pattern: str, entity_count: int) -> None:
        self.entries.append(DiagnosticEntry(
            severity=Severity.INFO,
            category="layer_filtered",
            message=f"Layer '{layer_name}' matched exclude filter '{pattern}'. {entity_count} entit{'y' if entity_count == 1 else 'ies'} omitted.",
            suggestion=(
                f"To include this layer, remove '{pattern}' from the exclude_layers list "
                "in your AdvancedOptions.filtering configuration."
            ),
            offender={"layer": layer_name, "pattern": pattern},
        ))

    def block_auto_vivified(self, block_name: str) -> None:
        self.entries.append(DiagnosticEntry(
            severity=Severity.WARN,
            category="block_auto_vivified",
            message=f"Block '{block_name}' was referenced by a component INSERT but not found in block_definitions. A placeholder crosshair block was auto-created.",
            suggestion=(
                f"Add a 'block_definitions' entry for '{block_name}' in your IR payload, "
                "or check that the block name matches exactly (case-sensitive, no special characters)."
            ),
            offender=block_name,
        ))

    def paper_space_built(self, preset: str, paper_size: str, orientation: str, scale: str) -> None:
        self.entries.append(DiagnosticEntry(
            severity=Severity.INFO,
            category="paper_space_created",
            message=(
                f"PaperSpace layout 'Presentation_Sheet' created by preset '{preset}' "
                f"({paper_size}, {orientation}, scale={scale})."
            ),
            suggestion="Open the 'Presentation_Sheet' layout tab in AutoCAD/DraftSight to view the print-ready sheet.",
            offender=None,
        ))

    def ir_schema_warning(self, message: str, field: str) -> None:
        self.entries.append(DiagnosticEntry(
            severity=Severity.WARN,
            category="ir_schema",
            message=message,
            suggestion=(
                f"Check the '{field}' field in your IR payload. "
                "Use cad-extractor-ir v1.1+ to produce a valid LAVINCI_CAD_IR_V3 payload."
            ),
            offender=field,
        ))

    def custom(self, severity: Severity, category: str, message: str,
               suggestion: str = "", offender: Any = None) -> None:
        """Emit a custom diagnostic entry (for extensibility)."""
        self.entries.append(DiagnosticEntry(
            severity=severity,
            category=category,
            message=message,
            suggestion=suggestion,
            offender=offender,
        ))

    # ── Queries ──────────────────────────────────────────────────────────────

    @property
    def has_warnings(self) -> bool:
        return any(e.severity in (Severity.WARN, Severity.ERROR) for e in self.entries)

    @property
    def has_errors(self) -> bool:
        return any(e.severity == Severity.ERROR for e in self.entries)

    @property
    def count(self) -> int:
        return len(self.entries)

    def by_severity(self, severity: Severity) -> List[DiagnosticEntry]:
        return [e for e in self.entries if e.severity == severity]

    def by_category(self, category: str) -> List[DiagnosticEntry]:
        return [e for e in self.entries if e.category == category]

    # ── Output ───────────────────────────────────────────────────────────────

    def print_report(self, file=None, compact: bool = False) -> None:
        """
        Print a human-readable compilation report.

        Parameters
        ----------
        file:
            Output stream. Defaults to sys.stderr (so it doesn't pollute
            stdout when the DXF path is piped or processed by tooling).
        compact:
            If True, print only a one-line summary instead of full entries.
        """
        out = file or sys.stderr

        if not self.entries:
            print("\n✔ Compilation: No diagnostics — clean IR payload.", file=out)
            return

        info_n  = len(self.by_severity(Severity.INFO))
        warn_n  = len(self.by_severity(Severity.WARN))
        error_n = len(self.by_severity(Severity.ERROR))

        if compact:
            parts = []
            if info_n:  parts.append(f"{info_n} info")
            if warn_n:  parts.append(f"{warn_n} warning{'s' if warn_n > 1 else ''}")
            if error_n: parts.append(f"{error_n} error{'s' if error_n > 1 else ''}")
            print(f"\nCompilation diagnostics: {', '.join(parts)}", file=out)
            return

        bar = "─" * 60
        print(f"\n{bar}", file=out)
        print("  Compilation Diagnostics Report", file=out)
        print(f"  Total: {self.count} | INFO: {info_n} | WARN: {warn_n} | ERROR: {error_n}", file=out)
        print(bar, file=out)

        for entry in self.entries:
            print(str(entry), file=out)

        print(bar, file=out)
        if warn_n or error_n:
            print(
                f"  {'⚠' if not error_n else '✖'} Review the {warn_n + error_n} "
                f"warning(s)/error(s) above to improve IR payload quality.",
                file=out,
            )
        else:
            print("  ✔ Compilation succeeded with informational notes only.", file=out)
        print(bar, file=out)

    def to_dict(self) -> Dict[str, Any]:
        """Return a machine-readable dict for JSON serialization or programmatic access."""
        return {
            "total": self.count,
            "info":  len(self.by_severity(Severity.INFO)),
            "warn":  len(self.by_severity(Severity.WARN)),
            "error": len(self.by_severity(Severity.ERROR)),
            "entries": [
                {
                    "severity":   e.severity.value,
                    "category":   e.category,
                    "message":    e.message,
                    "suggestion": e.suggestion,
                    "offender":   e.offender,
                }
                for e in self.entries
            ],
        }

    def raise_if_errors(self) -> None:
        """Raise CompilationError if any ERROR-severity diagnostics are present."""
        from .exceptions import CompilationError
        error_entries = self.by_severity(Severity.ERROR)
        if error_entries:
            messages = "; ".join(e.message for e in error_entries)
            raise CompilationError(
                message=f"{len(error_entries)} compilation error(s) encountered: {messages}",
                hint="Review the diagnostics report for details on each error.",
            )

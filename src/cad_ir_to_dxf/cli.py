"""
cli.py — Command-line interface for cad-ir-to-dxf.

Usage:
    cad-ir-to-dxf blueprint_ir.json -o blueprint.dxf
    cad-ir-to-dxf blueprint_ir.json -o blueprint.dxf --preset cnc_cam
    cad-ir-to-dxf blueprint_ir.json -o blueprint.dxf --preset arch_print --paper-size ISO_A1
    cad-ir-to-dxf blueprint_ir.json -o blueprint.dxf --version R2000
    cad-ir-to-dxf blueprint_ir.json --summary
"""

import argparse
import json
import sys
from pathlib import Path

from .compiler import compile_ir_to_dxf
from .presets import PresetName


def main() -> None:
    parser = argparse.ArgumentParser(
        prog="cad-ir-to-dxf",
        description=(
            "La Vinci CAD IR -> DXF Compiler.\n"
            "Converts a LAVINCI_CAD_IR_V3 JSON file into a DXF drawing.\n"
            "\nPart of the La Vinci engineering initiative."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )

    parser.add_argument(
        "ir_file",
        metavar="IR_FILE",
        help="Path to the LAVINCI_CAD_IR_V3 JSON file.",
    )
    parser.add_argument(
        "-o", "--output",
        metavar="OUTPUT",
        default=None,
        help="Output DXF file path. Defaults to <ir_file_stem>.dxf.",
    )
    # ── Preset ──────────────────────────────────────────────────────────────
    parser.add_argument(
        "--preset",
        metavar="PRESET",
        default="standard",
        choices=[p.value for p in PresetName],
        help=(
            "Named compilation preset. "
            "Choices: standard (default), cnc_cam, arch_print, "
            "web_lightweight, bim_overlay."
        ),
    )
    # ── Backward-compat DXF version override ────────────────────────────────
    parser.add_argument(
        "--version",
        metavar="DXF_VERSION",
        default=None,
        choices=["R12", "R2000", "R2004", "R2007", "R2010", "R2013", "R2018"],
        help="Override DXF version (overrides preset default).",
    )
    # ── Quick geometry overrides ─────────────────────────────────────────────
    parser.add_argument(
        "--flatten-z",
        action="store_true",
        default=None,
        help="Force all entity Z coordinates to 0.0 (2D flat output).",
    )
    parser.add_argument(
        "--explode-blocks",
        action="store_true",
        default=None,
        help="Expand all INSERT block references into raw loose primitives.",
    )
    # ── Quick layout overrides ───────────────────────────────────────────────
    parser.add_argument(
        "--paper-size",
        metavar="PAPER_SIZE",
        default=None,
        choices=["ISO_A4", "ISO_A3", "ISO_A2", "ISO_A1", "ISO_A0", "ANSI_A", "ANSI_D", "ARCH_D"],
        help="Paper sheet size when using arch_print preset or --options layout.",
    )
    # ── Full AdvancedOptions JSON override ───────────────────────────────────
    parser.add_argument(
        "--options",
        metavar="JSON_OR_FILE",
        default=None,
        help=(
            "Advanced options as a JSON string or path to a JSON file. "
            "Example: '{\"geometry\":{\"flatten_z\":true},\"styling\":{\"color_mode\":\"aci\"}}'"
        ),
    )
    parser.add_argument(
        "--diagnostics",
        action="store_true",
        help="Print compilation diagnostics report (warnings, adjustments, dropped geometry) to stderr.",
    )
    parser.add_argument(
        "--strict",
        action="store_true",
        help="Strict mode: raise errors immediately on missing layers, bad geometry, or schema warnings.",
    )
    parser.add_argument(
        "--summary",
        action="store_true",
        help="Print a summary of the IR content without writing a DXF file.",
    )

    args = parser.parse_args()

    ir_path = Path(args.ir_file)
    if not ir_path.exists():
        print(f"[ERROR] IR file not found: {ir_path}", file=sys.stderr)
        sys.exit(1)

    if args.summary:
        with open(ir_path, encoding="utf-8") as fh:
            ir = json.load(fh)
        _print_summary(ir)
        return

    # ── Parse advanced_options from CLI ─────────────────────────────────────
    advanced_options: dict = {}

    if args.options:
        opts_str = args.options.strip()
        if Path(opts_str).exists():
            with open(opts_str, encoding="utf-8") as f:
                advanced_options = json.load(f)
        else:
            try:
                advanced_options = json.loads(opts_str)
            except json.JSONDecodeError as e:
                print(f"[ERROR] --options is not valid JSON: {e}", file=sys.stderr)
                sys.exit(1)

    # Merge quick-flag overrides into advanced_options
    if args.flatten_z:
        advanced_options.setdefault("geometry", {})["flatten_z"] = True
    if args.explode_blocks:
        advanced_options.setdefault("geometry", {})["explode_blocks"] = True
    if args.paper_size:
        advanced_options.setdefault("layout", {})["paper_size"] = args.paper_size

    output_path = args.output
    if output_path is None:
        output_path = ir_path.with_suffix(".dxf")

    preset = args.preset or "standard"
    print(f"Compiling:  {ir_path}")
    print(f"Preset:     {preset}")
    if args.version:
        print(f"DXF Ver.:   {args.version} (override)")
    if advanced_options:
        print(f"Advanced:   {json.dumps(advanced_options)}")
    print(f"Output:     {output_path}")

    from .diagnostics import CompilationDiagnostics
    from .exceptions import CadIrToDxfError

    diag = CompilationDiagnostics() if args.diagnostics else None

    try:
        doc = compile_ir_to_dxf(
            ir_source=str(ir_path),
            output_path=output_path,
            dxf_version=args.version or "R2013",
            preset=preset,
            advanced_options=advanced_options if advanced_options else None,
            diagnostics=diag,
            strict=args.strict,
        )
    except CadIrToDxfError as err:
        print(f"\n[ERROR] Compilation failed: {err.message}", file=sys.stderr)
        if err.offender is not None:
            print(f"  Offender: {err.offender!r}", file=sys.stderr)
        if err.hint:
            print(f"  Fix     : {err.hint}", file=sys.stderr)
        sys.exit(1)
    except Exception as exc:
        print(f"\n[UNEXPECTED ERROR] {exc}", file=sys.stderr)
        sys.exit(1)

    if diag is not None:
        diag.print_report()

    msp = doc.modelspace()
    entity_count = len(list(msp))
    print(f"\nDone. {entity_count} top-level entities written to model space.")
    print(f"Saved: {output_path}")


def _print_summary(ir: dict) -> None:
    """Print a human-readable summary of the IR payload."""
    fmt = ir.get("format", "UNKNOWN")
    meta = ir.get("metadata", {})
    extents = ir.get("extents", {})
    summary = ir.get("geometry_primitives", {}).get("summary", {})
    bom = ir.get("bill_of_materials", {})

    print(f"\n{'-'*52}")
    print(f"  La Vinci CAD IR Summary")
    print(f"{'-'*52}")
    print(f"  Format:           {fmt}")
    print(f"  Source:           {meta.get('source_file', 'N/A')}")
    print(f"  CAD Version:      {meta.get('cad_version', 'N/A')}")
    print(f"  Measurement:      {meta.get('measurement_system', 'N/A')}")
    w = extents.get("width", 0)
    h = extents.get("height", 0)
    print(f"  Canvas:           {w} x {h} (width x height)")
    print(f"{'-'*52}")
    print(f"  Layers:           {len(ir.get('layers', []))}")
    print(f"  Block Defs:       {summary.get('total_block_definitions', 0)}")
    print(f"  Lines:            {summary.get('total_lines', 0)}")
    print(f"  Arcs:             {summary.get('total_arcs', 0)}")
    print(f"  Circles:          {summary.get('total_circles', 0)}")
    print(f"  Polylines:        {summary.get('total_polylines', 0)}")
    print(f"  Components:       {summary.get('total_components', 0)}")
    print(f"  Annotations:      {summary.get('total_annotations', 0)}")
    print(f"  Dimensions:       {summary.get('total_dimensions', 0)}")

    if bom:
        print(f"\n  Bill of Materials ({len(bom)} component types):")
        for name, count in list(bom.items())[:10]:
            print(f"    {name:<36} {count}")
        if len(bom) > 10:
            print(f"    ... and {len(bom) - 10} more")
    print(f"{'-'*52}\n")


if __name__ == "__main__":
    main()

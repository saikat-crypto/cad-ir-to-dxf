"""
cli.py — Command-line interface for cad-ir-to-dxf.

Usage:
    cad-ir-to-dxf blueprint_ir.json -o blueprint.dxf
    cad-ir-to-dxf blueprint_ir.json -o blueprint.dxf --version R2000
    cad-ir-to-dxf blueprint_ir.json --summary
"""

import argparse
import json
import sys
from pathlib import Path

from .compiler import compile_ir_to_dxf


def main() -> None:
    parser = argparse.ArgumentParser(
        prog="cad-ir-to-dxf",
        description=(
            "La Vinci CAD IR → DXF Compiler.\n"
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
    parser.add_argument(
        "--version",
        metavar="DXF_VERSION",
        default="R2013",
        choices=["R12", "R2000", "R2004", "R2007", "R2010", "R2013", "R2018"],
        help="Target DXF format version. Default: R2013.",
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

    output_path = args.output
    if output_path is None:
        output_path = ir_path.with_suffix(".dxf")

    print(f"Compiling:  {ir_path}")
    print(f"Target:     DXF {args.version}")
    print(f"Output:     {output_path}")

    doc = compile_ir_to_dxf(
        ir_source=str(ir_path),
        output_path=output_path,
        dxf_version=args.version,
    )

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

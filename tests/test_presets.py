"""
test_presets.py — Unit tests for the Preset & AdvancedOptions Engine.

Tests cover:
  - All 5 named presets compile successfully from a real IR
  - Preset baseline values are correct
  - AdvancedOptions overrides take precedence over preset defaults
  - Layer filtering (include, exclude, wildcard)
  - flatten_z produces Z=0 entities
  - Annotation & dimension suppression (cnc_cam)
  - PaperSpace layout creation (arch_print)
  - Invalid preset name raises ValueError
"""

import unittest
from cad_ir_to_dxf import (
    compile_ir_to_dxf,
    PresetName,
    AdvancedOptions,
    VersionOptions,
    GeometryOptions,
    FilteringOptions,
    LayoutOptions,
    StylingOptions,
    get_preset_config,
)

# ──────────────────────────────────────────────────────────────────────────────
# Shared sample IR fixture
# ──────────────────────────────────────────────────────────────────────────────

def _sample_ir() -> dict:
    """Minimal LAVINCI_CAD_IR_V3 IR with layers, lines, arcs, annotations,
    components, and dimensions for thorough preset testing."""
    return {
        "format": "LAVINCI_CAD_IR_V3",
        "metadata": {
            "source_file": "test_preset.dwg",
            "units": 4,
            "measurement_system": "Metric",
            "cad_version": "R2013",
        },
        "extents": {
            "min": [0.0, 0.0],
            "max": [1000.0, 800.0],
            "width": 1000.0,
            "height": 800.0,
        },
        "layers": [
            {"name": "WALLS",     "color_aci": 7,  "linetype": "Continuous", "is_off": False, "is_frozen": False, "is_locked": False},
            {"name": "FURNITURE", "color_aci": 2,  "linetype": "Continuous", "is_off": False, "is_frozen": False, "is_locked": False},
            {"name": "DEFPOINTS", "color_aci": 15, "linetype": "Continuous", "is_off": True,  "is_frozen": False, "is_locked": False},
            {"name": "TEMP_A",    "color_aci": 5,  "linetype": "Continuous", "is_off": False, "is_frozen": False, "is_locked": False},
        ],
        "block_definitions": {
            "CHAIR": {
                "base_point": [0.0, 0.0, 0.0],
                "lines": [
                    {"start": [0, 0, 5], "end": [100, 0, 5], "layer": "FURNITURE"},
                    {"start": [100, 0, 5], "end": [100, 80, 5], "layer": "FURNITURE"},
                ],
                "arcs": [
                    {"center": [50, 40], "radius": 30, "start_angle": 0, "end_angle": 180, "layer": "FURNITURE"},
                ],
                "circles": [],
                "polylines": [],
            }
        },
        "geometry_primitives": {
            "summary": {
                "total_lines": 2, "total_arcs": 1, "total_circles": 0,
                "total_polylines": 1, "total_block_definitions": 1,
                "total_components": 1, "total_annotations": 1, "total_dimensions": 1,
            },
            "primitives": {
                "lines": [
                    {"start": [0, 0, 10], "end": [500, 0, 10], "layer": "WALLS", "color": None},
                    {"start": [500, 0, 10], "end": [500, 400, 10], "layer": "WALLS", "color": None},
                ],
                "arcs": [
                    {"center": [250, 200], "radius": 100, "start_angle": 0, "end_angle": 90, "layer": "WALLS"},
                ],
                "circles": [],
                "polylines": [
                    {"points": [[0,0],[100,0],[100,50],[0,50]], "is_closed": True, "layer": "FURNITURE"},
                ],
            }
        },
        "components": [
            {
                "block_name": "CHAIR",
                "position": [200, 300, 0],
                "rotation": 45.0,
                "scale": [1.0, 1.0, 1.0],
                "layer": "FURNITURE",
                "space": "Model",
                "attributes": {"TAG": "C-01"},
            }
        ],
        "annotations": [
            {
                "type": "MTEXT",
                "raw_text": "Conference Room",
                "clean_text": "Conference Room",
                "position": [100, 100],
                "height": 5.0,
                "layer": "WALLS",
                "space": "Model",
            }
        ],
        "dimensions": [
            {
                "text": "1000mm",
                "measurement": 1000.0,
                "text_midpoint": [500, 10],
                "text_height": 3.0,
                "layer": "WALLS",
                "space": "Model",
            }
        ],
    }


# ──────────────────────────────────────────────────────────────────────────────
# 1. Preset Resolver Tests
# ──────────────────────────────────────────────────────────────────────────────

class TestPresetResolver(unittest.TestCase):

    def test_default_preset_is_standard(self):
        cfg = get_preset_config()
        self.assertEqual(cfg.preset_name, "standard")
        self.assertEqual(cfg.dxf_version, "R2013")
        self.assertFalse(cfg.flatten_z)
        self.assertFalse(cfg.explode_blocks)
        self.assertTrue(cfg.include_annotations)
        self.assertTrue(cfg.include_dimensions)
        self.assertFalse(cfg.create_paper_space)
        self.assertEqual(cfg.color_mode, "truecolor")
        self.assertEqual(cfg.layer_prefix, "")

    def test_cnc_cam_preset(self):
        cfg = get_preset_config("cnc_cam")
        self.assertEqual(cfg.preset_name, "cnc_cam")
        self.assertEqual(cfg.dxf_version, "R12")
        self.assertTrue(cfg.flatten_z)
        self.assertTrue(cfg.explode_blocks)
        self.assertFalse(cfg.include_annotations)
        self.assertFalse(cfg.include_dimensions)
        self.assertEqual(cfg.color_mode, "aci")

    def test_arch_print_preset(self):
        cfg = get_preset_config("arch_print")
        self.assertEqual(cfg.preset_name, "arch_print")
        self.assertTrue(cfg.create_paper_space)
        self.assertEqual(cfg.dxf_version, "R2013")

    def test_web_lightweight_preset(self):
        cfg = get_preset_config("web_lightweight")
        self.assertEqual(cfg.preset_name, "web_lightweight")
        self.assertEqual(cfg.dxf_version, "R2000")
        self.assertTrue(cfg.flatten_z)
        self.assertFalse(cfg.include_dimensions)
        self.assertEqual(cfg.color_mode, "aci")

    def test_bim_overlay_preset(self):
        cfg = get_preset_config("bim_overlay")
        self.assertEqual(cfg.preset_name, "bim_overlay")
        self.assertEqual(cfg.dxf_version, "R2018")
        self.assertEqual(cfg.layer_prefix, "IR_")

    def test_preset_enum_name(self):
        cfg = get_preset_config(PresetName.CNC_CAM)
        self.assertEqual(cfg.preset_name, "cnc_cam")

    def test_invalid_preset_raises_value_error(self):
        with self.assertRaises(ValueError):
            get_preset_config("nonexistent_preset")


# ──────────────────────────────────────────────────────────────────────────────
# 2. AdvancedOptions Override Tests
# ──────────────────────────────────────────────────────────────────────────────

class TestAdvancedOptionsOverrides(unittest.TestCase):

    def test_version_override(self):
        """AdvancedOptions.version.dxf_version overrides preset."""
        opts = AdvancedOptions(version=VersionOptions(dxf_version="R2018"))
        cfg = get_preset_config("standard", opts)
        self.assertEqual(cfg.dxf_version, "R2018")

    def test_geometry_flatten_z_override(self):
        """AdvancedOptions.geometry.flatten_z overrides standard preset."""
        opts = AdvancedOptions(geometry=GeometryOptions(flatten_z=True))
        cfg = get_preset_config("standard", opts)
        self.assertTrue(cfg.flatten_z)

    def test_filtering_exclude_override(self):
        """AdvancedOptions.filtering.exclude_layers overrides preset."""
        opts = AdvancedOptions(filtering=FilteringOptions(
            exclude_layers=["FURNITURE", "TEMP*"]
        ))
        cfg = get_preset_config("standard", opts)
        self.assertIn("FURNITURE", cfg.exclude_layers)
        self.assertIn("TEMP*", cfg.exclude_layers)

    def test_filtering_annotations_false_override(self):
        """AdvancedOptions.filtering.include_annotations=False overrides standard."""
        opts = AdvancedOptions(filtering=FilteringOptions(include_annotations=False))
        cfg = get_preset_config("standard", opts)
        self.assertFalse(cfg.include_annotations)

    def test_layout_paper_size_override(self):
        """AdvancedOptions.layout.paper_size overrides arch_print default."""
        opts = AdvancedOptions(layout=LayoutOptions(paper_size="ISO_A1"))
        cfg = get_preset_config("arch_print", opts)
        self.assertEqual(cfg.paper_size, "ISO_A1")

    def test_styling_color_mode_override(self):
        """AdvancedOptions.styling.color_mode overrides standard truecolor."""
        opts = AdvancedOptions(styling=StylingOptions(color_mode="monochrome"))
        cfg = get_preset_config("standard", opts)
        self.assertEqual(cfg.color_mode, "monochrome")

    def test_layer_prefix_override(self):
        """AdvancedOptions.styling.layer_prefix can be set on any preset."""
        opts = AdvancedOptions(styling=StylingOptions(layer_prefix="CAD_"))
        cfg = get_preset_config("standard", opts)
        self.assertEqual(cfg.layer_prefix, "CAD_")

    def test_dict_override(self):
        """AdvancedOptions from a plain dict works identically to dataclass."""
        cfg = get_preset_config("standard", {"geometry": {"flatten_z": True}})
        self.assertTrue(cfg.flatten_z)

    def test_overrides_dont_mutate_preset_base(self):
        """Applying overrides must not pollute the original preset baseline."""
        get_preset_config("standard", AdvancedOptions(geometry=GeometryOptions(flatten_z=True)))
        cfg2 = get_preset_config("standard")
        self.assertFalse(cfg2.flatten_z)


# ──────────────────────────────────────────────────────────────────────────────
# 3. Compilation Smoke Tests — All 5 Presets
# ──────────────────────────────────="────────────────────────────────────────────

class TestPresetCompilationSmoke(unittest.TestCase):

    def _compile(self, preset: str, overrides=None):
        return compile_ir_to_dxf(_sample_ir(), preset=preset, advanced_options=overrides)

    def test_standard_compiles(self):
        doc = self._compile("standard")
        self.assertEqual(doc.dxfversion, "AC1027")  # R2013

    def test_cnc_cam_compiles(self):
        doc = self._compile("cnc_cam")
        self.assertEqual(doc.dxfversion, "AC1009")  # R12

    def test_arch_print_compiles(self):
        doc = self._compile("arch_print")
        self.assertEqual(doc.dxfversion, "AC1027")  # R2013
        # Should have a PaperSpace layout
        layout_names = [l.name for l in doc.layouts]
        self.assertIn("Presentation_Sheet", layout_names)

    def test_web_lightweight_compiles(self):
        doc = self._compile("web_lightweight")
        self.assertEqual(doc.dxfversion, "AC1015")  # R2000

    def test_bim_overlay_compiles(self):
        doc = self._compile("bim_overlay")
        self.assertEqual(doc.dxfversion, "AC1032")  # R2018

    def test_default_no_preset_still_works(self):
        """Calling compile_ir_to_dxf with no preset defaults to standard."""
        doc = compile_ir_to_dxf(_sample_ir())
        self.assertEqual(doc.dxfversion, "AC1027")


# ──────────────────────────────────────────────────────────────────────────────
# 4. Behavioural / Functional Tests
# ──────────────────────────────────────────────────────────────────────────────

class TestPresetBehaviourFunctional(unittest.TestCase):

    def test_flatten_z_forces_zero_z_on_lines(self):
        """In cnc_cam preset, all LINE entities in modelspace must have Z=0."""
        doc = compile_ir_to_dxf(_sample_ir(), preset="cnc_cam")
        msp = doc.modelspace()
        lines = [e for e in msp if e.dxftype() == "LINE"]
        for line in lines:
            self.assertAlmostEqual(line.dxf.start.z, 0.0, places=6,
                msg=f"Line Z not flattened: start.z={line.dxf.start.z}")
            self.assertAlmostEqual(line.dxf.end.z, 0.0, places=6,
                msg=f"Line Z not flattened: end.z={line.dxf.end.z}")

    def test_cnc_cam_no_annotations(self):
        """In cnc_cam preset, TEXT/MTEXT annotations must be suppressed."""
        doc = compile_ir_to_dxf(_sample_ir(), preset="cnc_cam")
        msp = doc.modelspace()
        text_entities = [e for e in msp if e.dxftype() in ("TEXT", "MTEXT")]
        self.assertEqual(len(text_entities), 0,
            "cnc_cam preset must suppress all annotation text entities")

    def test_cnc_cam_no_dimensions(self):
        """In cnc_cam preset, dimension MTEXT labels must be suppressed."""
        doc = compile_ir_to_dxf(_sample_ir(), preset="cnc_cam")
        msp = doc.modelspace()
        # After suppression there should be no dimension MTEXT
        dims = [e for e in msp if e.dxftype() == "MTEXT"]
        self.assertEqual(len(dims), 0,
            "cnc_cam preset must suppress dimension MTEXT labels")

    def test_standard_includes_annotations(self):
        """In standard preset, MTEXT annotations must be present."""
        doc = compile_ir_to_dxf(_sample_ir(), preset="standard")
        msp = doc.modelspace()
        text_entities = [e for e in msp if e.dxftype() in ("TEXT", "MTEXT")]
        self.assertGreater(len(text_entities), 0,
            "standard preset must include annotation/dimension text entities")

    def test_bim_overlay_layer_prefix(self):
        """In bim_overlay preset, layers (except '0' and ezdxf internals) must be prefixed with 'IR_'."""
        doc = compile_ir_to_dxf(_sample_ir(), preset="bim_overlay")
        layer_names = [layer.dxf.name for layer in doc.layers]
        # Skip '0' and ezdxf-internal layers (e.g. 'Defpoints') not sourced from IR
        ir_layer_names = {"WALLS", "FURNITURE", "DEFPOINTS", "TEMP_A"}
        for name in layer_names:
            if name == "0" or name.lower() in ("defpoints",):
                continue
            # Only check layers that originated from the IR
            base = name[len("IR_"):] if name.startswith("IR_") else name
            if base.upper() in ir_layer_names:
                self.assertTrue(name.startswith("IR_"),
                    f"Layer '{name}' in bim_overlay should be prefixed with 'IR_'")


    def test_monochrome_override_sets_layer_color_7(self):
        """Monochrome AdvancedOptions must set all layer colors to ACI 7."""
        doc = compile_ir_to_dxf(
            _sample_ir(), preset="standard",
            advanced_options={"styling": {"color_mode": "monochrome"}}
        )
        for layer in doc.layers:
            if layer.dxf.name == "0":
                continue
            # Color may be positive (on) or negative (off), but ABS must be 7
            self.assertEqual(abs(layer.dxf.color), 7,
                f"Layer '{layer.dxf.name}' should be ACI 7 in monochrome mode, got {layer.dxf.color}")

    def test_exclude_layer_filter_removes_entities(self):
        """Excluding 'FURNITURE' layer removes furniture lines from modelspace."""
        doc = compile_ir_to_dxf(
            _sample_ir(), preset="standard",
            advanced_options={"filtering": {"exclude_layers": ["FURNITURE"]}}
        )
        msp = doc.modelspace()
        # No entity should reference the FURNITURE layer
        furniture_entities = [
            e for e in msp
            if hasattr(e.dxf, "layer") and e.dxf.layer == "FURNITURE"
        ]
        self.assertEqual(len(furniture_entities), 0,
            "Entities on excluded 'FURNITURE' layer must not appear in modelspace")

    def test_wildcard_exclude_layer_filter(self):
        """Wildcard exclude 'TEMP*' must remove TEMP_A layer entities."""
        doc = compile_ir_to_dxf(
            _sample_ir(), preset="standard",
            advanced_options={"filtering": {"exclude_layers": ["TEMP*"]}}
        )
        msp = doc.modelspace()
        temp_entities = [
            e for e in msp
            if hasattr(e.dxf, "layer") and e.dxf.layer.startswith("TEMP")
        ]
        self.assertEqual(len(temp_entities), 0,
            "Entities on 'TEMP*' wildcard-excluded layer must not appear in modelspace")

    def test_dxf_version_kwarg_override(self):
        """Passing dxf_version kwarg on top of standard preset overrides version."""
        doc = compile_ir_to_dxf(_sample_ir(), dxf_version="R2000")
        self.assertEqual(doc.dxfversion, "AC1015")

    def test_arch_print_paper_space_has_border(self):
        """arch_print PaperSpace layout must contain a border LWPOLYLINE."""
        doc = compile_ir_to_dxf(_sample_ir(), preset="arch_print")
        sheet = doc.layouts.get("Presentation_Sheet")
        self.assertIsNotNone(sheet, "Presentation_Sheet layout must exist")
        polys = [e for e in sheet if e.dxftype() == "LWPOLYLINE"]
        self.assertGreater(len(polys), 0,
            "arch_print sheet layout must contain a border polyline")


if __name__ == "__main__":
    unittest.main()

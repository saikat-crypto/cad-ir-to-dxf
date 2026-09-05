"""
cad-ir-to-dxf

Compiles the La Vinci CAD Intermediate Representation (LAVINCI_CAD_IR_V3)
into industry-standard DXF files (DXF R2013 / AC1027 by default).

Part of the La Vinci engineering initiative.
"""

from .compiler import compile_ir_to_dxf

__all__ = ["compile_ir_to_dxf"]
__version__ = "1.0.0"

import sys
import FreeCAD as App
import Part
import os
from pathlib import Path

# Create a block with a hole
box = Part.makeBox(10, 10, 10)
cyl = Part.makeCylinder(2, 10)
cyl.translate(App.Vector(5, 5, 0))
shape = box.cut(cyl)

# Export to STEP
step_path = os.path.abspath("test_topology_part.step")
shape.exportStep(step_path)
print(f"Exported STEP to {step_path}")

# Import refactored script
sys.path.append(os.path.abspath("."))
try:
    from Waam_tech.llm_agent.step_to_waam_dsl import convert_step_to_waam

    out_waam = os.path.abspath("test_topology_part.waam")
    if os.path.exists(out_waam):
        os.remove(out_waam)

    convert_step_to_waam(
        step_path=step_path,
        out_path=out_waam,
        layer_height=4.0, # Will generate a couple of layers
        emit_llm=False
    )
    print(f"WAAM DSL written to {out_waam}")

    # Verify contents
    with open(out_waam, "r") as f:
        content = f.read()

    print("WAAM FILE CONTENT:")
    print(content)

    if "INNER_CONTOUR" in content:
        print("SUCCESS: Found INNER_CONTOUR in output!")
    else:
        print("FAILURE: No INNER_CONTOUR found!")
        sys.exit(1)

except Exception as e:
    import traceback
    traceback.print_exc()
    print(f"Error running conversion: {e}")
    sys.exit(1)

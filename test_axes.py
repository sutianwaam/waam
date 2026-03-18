import sys
import os
import json
from pathlib import Path

# Setup paths
waam_dir = Path(r"C:\Users\User\AppData\Roaming\FreeCAD\Mod\WAAM")
sys.path.insert(0, str(waam_dir))

def check_axes():
    # 1. find latest step file and waam_baseline.nc
    import glob
    nc_files = glob.glob(r'C:\Users\User\**\waam_baseline.nc', recursive=True)
    if not nc_files:
        print("No NC files found")
        return
    
    nc_file = max(nc_files, key=os.path.getmtime)
    print("Latest NC File:", nc_file)
    
    # Extract points from NC
    import re
    with open(nc_file, 'r') as f:
        text = f.read()
        
    xs = [float(x) for x in re.findall(r'X([-+]?\d*\.\d+|\d+)', text)]
    ys = [float(y) for y in re.findall(r'Y([-+]?\d*\.\d+|\d+)', text)]
    
    if not xs:
        print("No coords in NC")
        return
    
    nc_xmin, nc_xmax = min(xs), max(xs)
    nc_ymin, nc_ymax = min(ys), max(ys)
    
    print(f"NC Bounds:")
    print(f"  X: {nc_xmin:.3f} to {nc_xmax:.3f} (span: {nc_xmax-nc_xmin:.3f})")
    print(f"  Y: {nc_ymin:.3f} to {nc_ymax:.3f} (span: {nc_ymax-nc_ymin:.3f})")
    
    # 2. Let's find the WAAM DSL or STEP
    llm_waam = Path(nc_file).parent.parent / "dsl_waam" / "part.llm_waam"
    if llm_waam.exists():
        text = llm_waam.read_text()
        w_xs = [float(x) for x, y in re.findall(r'\((-?\d+\.\d+),\s*(-?\d+\.\d+)\)', text)]
        w_ys = [float(y) for x, y in re.findall(r'\((-?\d+\.\d+),\s*(-?\d+\.\d+)\)', text)]
        if w_xs:
            w_xmin, w_xmax = min(w_xs), max(w_xs)
            w_ymin, w_ymax = min(w_ys), max(w_ys)
            print(f"\nWAAM DSL Bounds:")
            print(f"  X: {w_xmin:.3f} to {w_xmax:.3f} (span: {w_xmax-w_xmin:.3f})")
            print(f"  Y: {w_ymin:.3f} to {w_ymax:.3f} (span: {w_ymax-w_ymin:.3f})")
            
    # 3. Look at FreeCAD bounding box if possible
    
if __name__ == "__main__":
    check_axes()

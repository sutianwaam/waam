# Run `pipeline_create_automatic_nc` From The FreeCAD Python Console

in the terminal->

1) /Applications/FreeCAD.app/Contents/Resources/bin/freecadcmd

2) import sys, importlib, FreeCAD as App; waam_root = App.getUserAppDataDir() + "Mod/WAAM"; sys.path.insert(0, waam_root) if waam_root not in sys.path else None; from Waam_tech.automatitions import pipeline_create_automatic_nc; importlib.reload(pipeline_create_automatic_nc); pipeline_create_automatic_nc.main_headless(step_path="cubo.step")
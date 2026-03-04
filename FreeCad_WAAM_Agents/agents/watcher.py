# watcher.py
import time, os

while True:
    os.system("python3 ~/FreeCAD_WAAM_Agents/agents/agentprogramerpython.py")
    os.system("python3 ~/FreeCAD_WAAM_Agents/agents/agentncwaam.py")
    time.sleep(300)  # check every 300 seconds
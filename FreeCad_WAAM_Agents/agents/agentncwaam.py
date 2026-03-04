import os
import shutil
import datetime
import requests
import json

GCODE_FOLDER = os.path.expanduser("~/FreeCAD_WAAM_Agents/GCode")
GCODE_FILE = os.path.join(GCODE_FOLDER, "waam_baseline.nc")
OPTIMIZED_FILE = os.path.join(GCODE_FOLDER, "waam_baseline_optimized.nc")
BACKUP_FOLDER = os.path.join(GCODE_FOLDER, "backup")
LOG_FILE = os.path.expanduser("~/FreeCAD_WAAM_Agents/logs/agent.log")

os.makedirs(BACKUP_FOLDER, exist_ok=True)
os.makedirs(os.path.dirname(LOG_FILE), exist_ok=True)

def log(message):
    timestamp = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    with open(LOG_FILE, "a") as f:
        f.write(f"[GCodeAgent][{timestamp}] {message}\n")
    print(message)

def call_openclaw_nc_optimizer(gcode_text: str) -> str:
    """Send the G-Code to OpenClaw API for optimization and return the result."""
    api_url = os.getenv("OPENCLAW_API_URL", "https://api.openclaw.ai/v1/chat/completions")
    api_key = os.getenv("OPENCLAW_API_KEY", "INSERT_OPENCLAW_API_KEY_HERE")
    
    log("Sending G-Code to OpenClaw AI for optimization...")
    
    prompt = (
        "You are an OpenClaw AI Agent expert in WAAM (Wire Arc Additive Manufacturing) and CNC G-Code.\n"
        "Please analyze the following G-code and improve it for printing. Focus on: \n"
        "- Optimizing toolpaths\n"
        "- Removing redundant safe Z moves\n"
        "- Improving deposition continuity\n"
        "Return strictly ONLY the optimized raw G-code without any explanations or Markdown formatting blocks.\n\n"
        f"{gcode_text}"
    )
    
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {api_key}"
    }
    payload = {
        "model": "openclaw-agent", 
        "messages": [{"role": "user", "content": prompt}],
        "temperature": 0.2
    }
    
    try:
        response = requests.post(api_url, headers=headers, json=payload, timeout=120)
        response.raise_for_status()
        
        reply = response.json()["choices"][0]["message"]["content"].strip()
        if reply.startswith("```"):
            reply = "\n".join(reply.split("\n")[1:-1])
        return reply
    except Exception as e:
        log(f"OpenClaw error: {e}")
        log("Falling back to basic optimization (removing empty lines and comments).")
        # Basic fallback processing
        lines = [line for line in gcode_text.split('\n') if line.strip() and not line.strip().startswith(";")]
        return "\n".join(lines)

if not os.path.exists(GCODE_FILE):
    log(f"No baseline NC found at {GCODE_FILE}")
else:
    # Backup original G-code
    backup_path = os.path.join(BACKUP_FOLDER, f"waam_baseline_{datetime.datetime.now().strftime('%Y%m%d%H%M%S')}.bak")
    shutil.copy2(GCODE_FILE, backup_path)
    log(f"Backed up {GCODE_FILE} -> {backup_path}")

    # Read, optimize with OpenClaw, and save
    with open(GCODE_FILE, "r") as f:
        original_gcode = f.read()

    optimized_gcode = call_openclaw_nc_optimizer(original_gcode)

    with open(OPTIMIZED_FILE, "w") as f:
        f.write(optimized_gcode)

    log(f"Optimized G-code saved to {OPTIMIZED_FILE}")
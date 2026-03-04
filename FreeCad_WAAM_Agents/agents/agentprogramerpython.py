import os
import shutil
import datetime
import requests
import json
import sys

# Path to your WAAM FreeCAD module
WAAM_FOLDER = os.path.expanduser("~/FreeCAD_WAAM_Agents/WAAM")
BACKUP_FOLDER = os.path.join(WAAM_FOLDER, "backup")
LOG_FILE = os.path.expanduser("~/FreeCAD_WAAM_Agents/logs/agent.log")

os.makedirs(BACKUP_FOLDER, exist_ok=True)
os.makedirs(os.path.dirname(LOG_FILE), exist_ok=True)

def log(message):
    timestamp = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    with open(LOG_FILE, "a") as f:
        f.write(f"[PythonAgent][{timestamp}] {message}\n")
    print(message)

def call_openclaw_code_optimizer(filename: str, py_code: str) -> str:
    """Send the python code to OpenClaw API for improvement and return the result."""
    api_url = os.getenv("OPENCLAW_API_URL", "https://api.openclaw.ai/v1/chat/completions")
    api_key = os.getenv("OPENCLAW_API_KEY", "INSERT_OPENCLAW_API_KEY_HERE")
    
    log(f"Sending {filename} to OpenClaw AI for analysis...")
    
    prompt = (
        f"You are an OpenClaw AI coding agent analyzing a FreeCAD WAAM module file named {filename}.\n"
        "Analyze the Python code for bugs, missing type hints, logic errors, or style improvements, and rewrite it.\n"
        "Ensure compatibility with PyQt5 and FreeCAD API where appropriate.\n"
        "Return ONLY the raw optimized Python code. Do not wrap it in markdown codeblocks like ```python.\n\n"
        f"```python\n{py_code}\n```"
    )
    
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {api_key}"
    }
    payload = {
        "model": "openclaw-coder", 
        "messages": [{"role": "user", "content": prompt}],
        "temperature": 0.2
    }
    
    try:
        response = requests.post(api_url, headers=headers, json=payload, timeout=120)
        response.raise_for_status()
        
        reply = response.json()["choices"][0]["message"]["content"].strip()
        # Clean up markdown tags if the agent returned them
        if reply.startswith("```"):
            reply = "\n".join(reply.split("\n")[1:-1])
        return reply
    except Exception as e:
        log(f"OpenClaw error on {filename}: {e}")
        return None

# Analyze each Python file
if not os.path.exists(WAAM_FOLDER):
    log(f"WAAM Folder not found: {WAAM_FOLDER}")
else:
    for filename in os.listdir(WAAM_FOLDER):
        if filename.endswith(".py"):
            filepath = os.path.join(WAAM_FOLDER, filename)
            
            # Read code
            with open(filepath, "r", encoding="utf-8") as f:
                original_code = f.read()
                
            # Skip if file is empty
            if not original_code.strip():
                continue
                
            # Attempt to optimize via OpenClaw
            improved_code = call_openclaw_code_optimizer(filename, original_code)
            
            if improved_code and improved_code != original_code:
                # Backup existing Python file before modifying
                backup_path = os.path.join(BACKUP_FOLDER, f"{filename}.{datetime.datetime.now().strftime('%Y%m%d%H%M%S')}.bak")
                shutil.copy2(filepath, backup_path)
                log(f"Backed up {filename} -> {backup_path}")
                
                # Write improved code
                with open(filepath, "w", encoding="utf-8") as f:
                    f.write(improved_code)
                log(f"Updated {filename} with OpenClaw enhancements.")
            else:
                log(f"No changes made to {filename}.")
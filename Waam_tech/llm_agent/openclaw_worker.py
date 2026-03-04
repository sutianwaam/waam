"""OpenClaw background worker for iterative NC refinement."""

from __future__ import annotations

import time
from pathlib import Path
from typing import Dict, Any, List, Optional, Sequence, Mapping

from PyQt5 import QtCore

from .ollama_client import run_quality_improvement_generation, check_ollama_available, check_model_available
from ..waamgen.pipeline import load_light_job, validate_gcode

class OpenClawWorker(QtCore.QThread):
    """Background thread running the OpenClaw iterative improvement loop."""

    # Signals for UI updates
    # logMessage(level, message)
    logMessage = QtCore.pyqtSignal(str, str)
    # iterationFinished(iteration, current_gcode, is_best)
    iterationFinished = QtCore.pyqtSignal(int, str, bool)
    # finished(best_gcode, final_message, error)
    workerFinished = QtCore.pyqtSignal(str, str, bool)

    def __init__(
        self,
        model: str,
        baseline_path: Path,
        ops_path: Path,
        llm_waam_path: Path,
        coverage_path: Path,
        missing_points_path: Path,
        extra_instructions: str,
        max_iterations: int = 3,
        geom_tol: float = 0.8,
        parent=None,
    ):
        super().__init__(parent)
        self.model = model
        self.baseline_path = baseline_path
        self.ops_path = ops_path
        self.llm_waam_path = llm_waam_path
        self.coverage_path = coverage_path
        self.missing_points_path = missing_points_path
        self.extra_instructions = extra_instructions
        self.max_iterations = max_iterations
        self.geom_tol = geom_tol
        self.is_running = True

        self.history: List[Mapping[str, str]] = []
        self.best_gcode: Optional[str] = None
        self.best_score: int = -1 # Higher is better (0=fails syntax, 1=syntax ok, 2=safety ok, 3=geometry ok)

    def stop(self):
        """Request the worker to stop gracefully."""
        self.is_running = False
        self.logMessage.emit("info", "Stopping agent loop at user request...")

    def _score_validation(self, report) -> int:
        if not report.syntax_ok:
            return 0
        if not report.safety_ok:
            return 1
        if not report.geometry_ok:
            return 2
        return 3

    def _run_validation(self, gcode_text: str) -> tuple[bool, str, int]:
        """Validate G-code and return (is_valid, error_msg, score)."""
        try:
            job = load_light_job(str(self.ops_path))
            report = validate_gcode(gcode_text, job, geom_tol=self.geom_tol)
            
            score = self._score_validation(report)
            
            if report.ok:
                return True, "Validation passed (Syntax/Safety/Geometry OK)", score
            else:
                errors = "\n".join(f"- {msg}" for msg in report.messages)
                return False, f"Validation failed:\n{errors}", score
                
        except Exception as exc:
            return False, f"Validation system error: {exc}", -1

    def run(self):
        self.logMessage.emit("info", f"Starting OpenClaw Agent ({self.model}) - Max {self.max_iterations} iterations")
        
        # Check LLM availability
        is_ok, msg = check_ollama_available()
        if not is_ok:
            self.workerFinished.emit("", f"Ollama Error: {msg}", True)
            return
            
        is_ok, msg = check_model_available(self.model)
        if not is_ok:
            self.workerFinished.emit("", f"Model Error: {msg}", True)
            return

        current_instructions = self.extra_instructions

        for i in range(1, self.max_iterations + 1):
            if not self.is_running:
                break
                
            self.logMessage.emit("info", f"--- Iteration {i}/{self.max_iterations} ---")
            self.logMessage.emit("debug", "Generating NC fixes...")
            
            try:
                # Add history handling to the quality improvement func if we want true memory,
                # but currently we can just feed the error back as instructions.
                if i > 1:
                    self.logMessage.emit("debug", f"Prompting LLM with feedback from previous validation.")
                
                result = run_quality_improvement_generation(
                    model=self.model,
                    baseline_path=self.baseline_path,
                    ops_path=self.ops_path,
                    llm_waam_path=self.llm_waam_path,
                    coverage_path=self.coverage_path,
                    missing_points_path=self.missing_points_path,
                    extra_instructions=current_instructions,
                )
                
                if not result.gcode:
                    self.logMessage.emit("warning", "Agent failed to return valid G-code segment.")
                    self.history.append({"role": "assistant", "content": result.raw_output[:500]})
                    self.history.append({"role": "user", "content": "You did not return a valid G-code block. Please try again and wrap your output in ```gcode ... ```."})
                    current_instructions = f"PREVIOUS ERROR: Failed to produce raw G-code.\n\nORIGINAL INSTRUCTIONS:\n{self.extra_instructions}"
                    continue
                    
                self.history.append({"role": "assistant", "content": result.parsed_json.get("summary", "Generated G-code") if result.parsed_json else "Generated G-code"})

                self.logMessage.emit("debug", "Validating generated G-code...")
                is_valid, val_msg, score = self._run_validation(result.gcode)
                
                is_best = False
                if score > self.best_score:
                    self.best_score = score
                    self.best_gcode = result.gcode
                    is_best = True
                    self.logMessage.emit("success", f"New best G-code found! (Score: {score}/3)")
                
                self.iterationFinished.emit(i, result.gcode, is_best)
                
                if is_valid:
                    self.logMessage.emit("success", "Valid NC file generated successfully! Stopping early.")
                    self.workerFinished.emit(result.gcode, "Agent succeeded.", False)
                    return
                else:
                    self.logMessage.emit("warning", val_msg)
                    self.history.append({"role": "user", "content": f"Your last G-code failed validation with these errors:\n{val_msg}\n\nPlease fix them."})
                    current_instructions = f"PREVIOUS VALIDATION FAILED:\n{val_msg}\n\nPlease correct these issues.\n\nORIGINAL INSTRUCTIONS:\n{self.extra_instructions}"
                    
            except Exception as e:
                self.logMessage.emit("error", f"Agent encountered an exception: {e}")
                self.workerFinished.emit(self.best_gcode or "", f"Fatal error: {e}", True)
                return

        final_msg = "Agent loop finished."
        if not self.best_gcode:
            final_msg = "Agent failed to produce any valid output."
            
        self.workerFinished.emit(self.best_gcode or "", final_msg, False)

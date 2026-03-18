from __future__ import annotations

from pathlib import Path
from typing import List, Optional, Sequence, Mapping

from PyQt5 import QtCore

from .ollama_client import (
    run_quality_improvement_generation,
    check_openclaw_available,
    check_ollama_available,
    list_available_models,
)
from ..waamgen.pipeline import load_light_job, validate_gcode


class OpenClawWorker(QtCore.QThread):
    """Background thread running the OpenClaw iterative improvement loop."""

    logMessage = QtCore.pyqtSignal(str, str)
    iterationFinished = QtCore.pyqtSignal(int, str, bool)
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
        history: Optional[Sequence[Mapping[str, str]]] = None,
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
        self.was_cancelled = False

        self.history: List[Mapping[str, str]] = list(history or [])
        self.best_gcode: Optional[str] = None
        # -1 = validation system error, 0 = syntax fail, 1 = safety fail, 2 = geometry fail, 3 = fully valid
        self.best_score: int = -1

    def stop(self):
        self.is_running = False
        self.was_cancelled = True
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
        try:
            job = load_light_job(str(self.ops_path))
            report = validate_gcode(gcode_text, job, geom_tol=self.geom_tol)
            score = self._score_validation(report)

            if report.ok:
                return True, "Validation passed (syntax, safety, geometry OK).", score

            errors = "\n".join(f"- {msg}" for msg in report.messages)
            return False, f"Validation failed:\n{errors}", score

        except Exception as exc:
            return False, f"Validation system error: {exc}", -1

    @staticmethod
    def _model_is_available(model_name: str, available_models: Sequence[str]) -> bool:
        chosen = (model_name or "").strip()
        if not chosen:
            return False
        return any(avail == chosen or avail.startswith(f"{chosen}:") for avail in available_models)

    def _select_effective_model(self) -> tuple[Optional[str], Optional[str]]:
        """Return (effective_model, error_message)."""
        effective_model = (self.model or "").strip()

        openclaw_ok, openclaw_msg = check_openclaw_available(effective_model)
        if openclaw_ok:
            self.logMessage.emit("info", f"Primary backend: {openclaw_msg}")
            self.logMessage.emit("info", "Fallback backend: Ollama")
            return effective_model, None

        self.logMessage.emit("info", "Primary backend unavailable: OpenClaw API is not configured.")

        is_ok, msg = check_ollama_available()
        if not is_ok:
            return None, f"Ollama Error: {msg}"

        available_models = list_available_models()
        if not available_models:
            return None, (
                "Model Error: No Ollama models are installed.\n\n"
                "Install one first, for example:\n"
                "  ollama pull gpt-oss:latest"
            )

        if not self._model_is_available(effective_model, available_models):
            fallback = available_models[0]
            if effective_model:
                self.logMessage.emit(
                    "warning",
                    f"Model '{effective_model}' is not installed in Ollama. Falling back to '{fallback}'.",
                )
            else:
                self.logMessage.emit("warning", f"No model selected. Falling back to '{fallback}'.")
            effective_model = fallback

        return effective_model, None

    def run(self):
        self.logMessage.emit(
            "info",
            f"Starting OpenClaw Agent ({self.model or 'auto'}) - Max {self.max_iterations} iterations",
        )

        effective_model, error = self._select_effective_model()
        if error:
            self.workerFinished.emit("", error, True)
            return

        for i in range(1, self.max_iterations + 1):
            if not self.is_running:
                break

            self.logMessage.emit("info", f"--- Iteration {i}/{self.max_iterations} ---")
            self.logMessage.emit("debug", "Generating NC fixes...")

            try:
                result = run_quality_improvement_generation(
                    model=effective_model,
                    baseline_path=self.baseline_path,
                    ops_path=self.ops_path,
                    llm_waam_path=self.llm_waam_path,
                    coverage_path=self.coverage_path,
                    missing_points_path=self.missing_points_path,
                    extra_instructions=self.extra_instructions,
                    history=self.history,
                )
            except Exception as exc:
                self.logMessage.emit("error", f"Generation failed: {exc}")
                self.workerFinished.emit(self.best_gcode or "", f"Fatal generation error: {exc}", True)
                return

            if not self.is_running:
                break

            if not result.gcode:
                self.logMessage.emit("warning", "Agent failed to return valid G-code segment.")
                raw_output = (result.raw_output or "")[:500]
                self.history.append({"role": "assistant", "content": raw_output})
                self.history.append({
                    "role": "user",
                    "content": "You did not return a valid G-code block. Please try again and wrap your output in ```gcode ... ```.",
                })
                continue

            summary = "Generated G-code"
            if getattr(result, "parsed_json", None):
                summary = result.parsed_json.get("summary", summary)
            elif result.gcode:
                summary = result.gcode
            self.history.append({"role": "assistant", "content": summary})

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

            self.logMessage.emit("warning", val_msg)
            self.history.append({
                "role": "user",
                "content": (
                    "Your last G-code failed validation with these errors:\n"
                    f"{val_msg}\n\nPlease fix them."
                ),
            })

        if self.was_cancelled:
            self.workerFinished.emit(
                self.best_gcode or "",
                "Agent loop cancelled by user.",
                False,
            )
            return

        if self.best_gcode:
            self.workerFinished.emit(self.best_gcode, "Agent loop finished. Returning best candidate found.", False)
        else:
            self.workerFinished.emit("", "Agent failed to produce any valid output.", False)

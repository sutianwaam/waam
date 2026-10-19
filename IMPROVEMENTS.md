# WAAM pipeline hardening — verification log




## 0. Things to improve


Commit or at least checkpoint the working tree — this is the biggest actual risk right now. git status shows 28 modified files (1320+/693- lines) plus entirely untracked new dirs (tests/ at 6.9MB, tools/, .github/, IMPROVEMENTS.md, and now reslice.py). None of it is in git history. If two processes are editing this repo concurrently with nothing committed, a crash, disk issue, or an overlapping write is a real way to lose work silently.

Nothing can currently verify any of this. requirements-dev.txt and pytest.ini exist, but pytest isn't installed in either Python I found on this machine (python3 or ~/.local/bin/python3.11). All the hardening described in IMPROVEMENTS.md (67 tests, golden fixtures, regression tools) can't actually be run right now to confirm the new reslice.py wiring or anything else is correct.

The new reslice capability is only in the seed template, not the live workspace. _default_helpers_source() in loader.py is what gets written into brand-new per-part workspaces — the workspaces that already exist (runtime/parts/body/..., crankcase/..., etc.) and the current runtime/active/helpers.py mirror still have the old helpers without it. It won't take effect on your active part until that workspace is regenerated or manually synced.

Coverage gating is no longer a stub: `_run_coverage_gate` now compares WCS-space material voxels from the FreeCAD shape against deposition voxels from the generated NC when `coverage_threshold` is passed.

Archive retention policy is declared but not enforced. _prune_archive_snapshots in loader.py exists and is unit-tested, but nothing in the real export path calls it with dry_run=False — runtime/archive/ will keep growing indefinitely despite the "keep last 5" policy recorded in every manifest.



=======================================



This documents what was checked, what was found, and what changed. Every
claim below was verified by running the affected tool, not by reading the
code. Run IDs refer to the local `Waam_tech/output/` artifacts already
present at the start of this work (not committed to git — see "Reference
artifacts are not committed" below).

## 1. Determinism claim: empirically verified

**Claim (AGENTS.md/README):** slice-first pipeline is deterministic — same
part + same WCS + same config → same trajectory.

**Test performed:**

```
python -c "from Waam_tech.waamgen.pipeline import generate_nc; \
  generate_nc('Waam_tech/output/operations_mesh/waam_slice_plan.json', \
              output_path='<scratch>/run1.nc')"
diff <scratch>/run1.nc Waam_tech/output/nc_files/waam_baseline.nc   # byte-identical
# ran a second time into run2.nc
diff <scratch>/run1.nc <scratch>/run2.nc                            # byte-identical
```

**Result:** PASS. Baseline NC regeneration from the stored plan JSON is
byte-identical to the locally stored `waam_baseline.nc`, and running twice
produces identical bytes. No timestamps, dict ordering, or randomness leak
into the NC body.

## 2. Reference artifacts are not committed to git

`Waam_tech/output/**` (including `waam_baseline.nc`,
`waam_baseline_experimental.nc`, `waam_slice_plan.json`,
`standard_waam.json`) is gitignored and was **not** in git history at the
start of this work (`git ls-files` confirms). The methodology brief assumed
these were "committed references"; they are locally-generated artifacts on
this machine only. To make golden-artifact regression tests actually run in
CI (a fresh clone has none of these files), frozen copies were checked into
`tests/fixtures/golden/` instead. `Waam_tech/output/` was never modified by
this work except by tests writing into their own `tmp_path` fixtures (one
early manual smoke test wrote into the real `Waam_tech/output/waamgen_attempts/`
before this was noticed — content was byte-identical since generation is
deterministic, so no data was altered, but this confirmed `generate_nc()`'s
default `attempt_dir` lands inside the input's own output tree unless a
caller passes `attempt_dir` explicitly. All test/tool code in this repo now
always passes an explicit `attempt_dir`).

## 3. Found: stale experimental reference (real self-inconsistency)

Regenerating `waam_baseline_experimental.nc` from the *current*
`waam_slice_plan.json` through the active experimental planner did **not**
match the locally stored `waam_baseline_experimental.nc` (524,892 bytes
regenerated vs. 210,975 bytes stored). Root cause: `waam_slice_plan.json`
and `waam_baseline.nc` were regenerated together on 2026-05-23, but the
experimental export was last run on 2026-05-03 against an older plan — nothing
recorded which plan produced which NC, so the drift was silent. This is
exactly the class of problem the new stage manifest / NC provenance header
(sections 5–6 below) is meant to make detectable instead of silent. The
golden experimental fixture in `tests/fixtures/golden/` was generated fresh
against the *current* plan so it is an internally consistent pair.

## 4. Fixed: deprecated `datetime.utcnow()`

`Waam_tech/slicing/slice_plan.py` was the only production module still
using deprecated `datetime.utcnow()` (all experimental-runtime helper
modules already used `datetime.now(timezone.utc)`). Verified via:

```
grep -rn "utcnow" --include="*.py" Waam_tech | grep -v __pycache__
```

Fixed to `datetime.now(timezone.utc).isoformat(...).replace("+00:00", "Z")`,
producing the same `...Z` suffix format as before.

## 5. Fixed: validator/generator self-inconsistency (`%` line)

Running the project's own validator (`waamgen.pipeline.validate_gcode`)
against NC text the project's own generator (`ir_to_gcode`) had just
produced from the *same* plan **failed** with:

```
Unsupported or unsafe command: %
```

The generator always emits a leading and trailing `%` (LinuxCNC
program-boundary marker), but the validator's `allowed_prefixes` tuple never
included `"%"`. Since `generate_nc()` calls `validate_gcode()` internally as
part of its iterate/validate loop, this meant the internal validation step
always reported `syntax_ok=False` — a self-inconsistency that never blocked
production only because `max_iters=1` in both call sites, so the "last
attempt becomes the deliverable" fallback always fired. Fixed by adding
`"%"` to `allowed_prefixes`. Verified before/after:

```
python -m Waam_tech.waamgen.pipeline validate --gcode tests/fixtures/golden/waam_baseline.nc \
  --ref tests/fixtures/golden/waam_slice_plan.json
# before: [FAIL] syntax=False safety=True geometry=True
# after:  [PASS] syntax=True  safety=True geometry=True
```



## 6. Fixed: `numpy` array truthiness bug in the new regression tool

While writing `tools/waam_regress.py`, a pytest run (with `numpy` installed,
matching the real FreeCAD runtime) failed with
`ValueError: The truth value of an array with more than one element is ambiguous` on `if cand_points and ref_points:` — `nc.parser.parse_nc_text`
returns `numpy.ndarray` when numpy is importable. Fixed to
`if len(cand_points) and len(ref_points):`. This was caught by the test
suite itself before being shipped, not by manual inspection.

## 7. Pipeline checkpoints added

- **Checkpoint A (after slicing):** `write_slice_plan_for_document()` now
embeds `metadata.stage_manifest` (input STEP hash when available, WCS
name/source/warnings, config hash, layer/path counts, deposition start +
source, generator version) into the plan JSON, and writes a sidecar
`<plan>.manifest.json` next to it. New keys only — fully backward
compatible with plans that lack them.
- **Checkpoint B (before NC generation):** `waamgen/plan_schema.py` validates
required keys, non-empty WCS, non-empty layers/paths, and monotonic Z
(warning, not fatal) before `load_light_job()` proceeds. Errors are
prefixed `[stage: slice_plan -> waamgen]` so triage can route immediately.
Exposed via `python -m Waam_tech.waamgen.pipeline validate-plan --plan <path>`.
- **Checkpoint C (after NC generation):** NC header comments now record
`(SOURCE PLAN: <filename> sha256:<16 hex>)`, `(CONFIG HASH: sha256:<16 hex>)`
(when the plan carries one), and `(GENERATOR: waamgen/1)` — all inside
`()` comments, so `nc.parser` (which strips comments) is unaffected and
byte-identity tests against pre-checkpoint fixtures still pass on the
functional/semantic comparison in `tools/waam_regress.py` (verified: the
provenance-header NC differs byte-for-byte from the old golden but
`tools/waam_regress.py` reports `[PASS]`).



## 8. Regression tooling

- `tools/waam_regress.py candidate.nc reference.nc` — byte identity fast
path, then move-by-move XYZ equivalence, torch on/off sequencing,
deposition length, and bbox comparison, all within `--tol` (default
0.05mm). Exit 0/2. Verified to PASS on identical/equivalent files and
FAIL (exit 2) on a deliberately shifted coordinate.
- `tools/plan_regress.py candidate.json reference.json` — semantic diff of
layer count, per-layer Z, path count, path geometry (within tolerance),
and named process parameters, ignoring timestamps/absolute paths. Exit
0/2. Verified to PASS on identical plans and FAIL on a shifted path point.



## 9. Test suite (`tests/`, no FreeCAD required)

52 tests, all passing (`python -m pytest -q`):

- Golden-artifact byte-identity for both baseline and experimental NC
(`test_golden_artifacts.py`), against frozen fixtures in
`tests/fixtures/golden/`.
- Repeatability (`test_repeatability.py`): two runs from the same plan
produce identical bytes.
- Validator pass-through (`test_validator_pass_through.py`): the
generator's own output passes its own validator (this is the test that
would have caught finding #5 immediately had it existed beforehand).
- Unit tests for `nc/parser.py`, `nc/contour_clip.py`, `nc/support_trim.py`,
`core/config_loader.py`, `waamgen/plan_schema.py`, and the pure-Python
half of deposition-start extraction (`waamgen.pipeline._start_point_from_metadata`).
- `test_production_experimental_boundary.py`: static assertion that
`waamgen/pipeline.py` has zero source-level coupling to the experimental
package, that the experimental planner's output filename is never
`waam_baseline.nc`, and that `generate_nc()` only writes inside its
explicit `output_path`/`attempt_dir`.
- `test_regress_tools.py`: smoke-tests both `tools/` scripts pass on
equivalent input and fail on mutated input.
- `test_experimental_hot_reload.py`: verifies that editing
`Waam_tech/experimental/runtime/active/planner.py` is picked up on the
next experimental export within the same process (no stale module
caching), and that this never touches the real repo's experimental
workspace state. See section 11.

CI: `.github/workflows/tests.yml` runs the suite on Python 3.11 and 3.12,
then regenerates the baseline NC from the golden plan and runs
`tools/waam_regress.py` against it as an end-to-end smoke check.

## 10. WCS / deposition-start UX

- `metadata.wcs` in the plan JSON now carries `source`
(`stored_marker` / `path_job` / `identity_fallback`) and `warnings` from
`core/wcs_resolver.py` (previously only printed to the FreeCAD console,
never persisted).
- `SlicePlanArtifacts` gained `wcs_source`, `wcs_warnings`,
`deposition_start_source`, and `manifest_path` fields.
- `ui/ui_workflow.py` (Slice Planner dialog) and `ui/pipeline_runner.py`
(Run Full Pipeline) now show the resolved WCS source and deposition-start
source in their completion dialogs, and switch to a `QMessageBox.warning`
listing the specific resolver warnings when the WCS is an identity
fallback or otherwise flagged — instead of silently succeeding.
- Not testable headlessly: `core/wcs_resolver.py` requires a live FreeCAD
`App.Placement`/`App.Vector`. The already-existing warning logic there
(identity-placement detection, non-finite coordinate detection,
degenerate-rotation detection) was read and confirmed correct by
inspection but has no unit test in this change — that would require a
FreeCAD-free placement abstraction, which is out of scope for a minimal,
surgical change.
- The pure-Python half (`waamgen.pipeline._start_point_from_metadata`) is
unit tested in `tests/test_deposition_start_metadata.py`.
- The "too few layers for WCS bbox height" warning (AGENTS.md pitfall) was
already implemented as a hard `RuntimeError` in `slice_plan.py`
(`_expected_dense_layer_count` check) before this work — confirmed by
reading and left unchanged.



## 11. Production / experimental boundary

- `Waam_tech/waamgen/pipeline.py` (the baseline NC generator) has zero
references to `experimental` anywhere in its source — verified by a grep
in this document's section 4 methodology and enforced going forward by
`test_waamgen_pipeline_module_has_no_experimental_coupling`.
`RuntimeActivePlanner.output_filename` is asserted to always be
`waam_baseline_experimental.nc`, never `waam_baseline.nc`.
- Traced the real dispatch path: `registry.get_experimental_planner("runtime_active")`
returns a `RuntimeActiveExperimentalPlanner` (`experimental/runtime/loader.py`),
not the `RuntimeActivePlanner` class directly. Its `export_nc()`:
  1. `ensure_active_runtime()` promotes any edits sitting in
     `runtime/active/{planner,helpers,notes}.py` into the durable
     per-part workspace (`runtime/parts/<part_key>/<features_signature>/`),
     archiving the previous version first if the active part changed.
  2. `_load_active_runtime_planner()` explicitly evicts any previously
     imported `<workspace>.planner` / `<workspace>.helpers` modules from
     `sys.modules`, then re-imports both from disk via
     `importlib.util.spec_from_file_location` before instantiating the
     planner — so an edit made mid-session (e.g. by an agent driven with
     natural language) is picked up on the very next "Run Pipeline +
     Experimental NC" click, with no FreeCAD restart required.
  3. `finalize_runtime_export()` mirrors the (now-updated) workspace files
     back into `runtime/active/` and updates the manifest.

  Each dynamically-loaded module's `NOTES_PATH`/`ACTIVE_ROOT` resolves via
  its own `__file__`, set to wherever it was actually loaded from (the real
  `Waam_tech/experimental/` tree in production, or an isolated
  `experimental_root` override in tests) — not a hardcoded path, as an
  earlier draft of this document incorrectly stated. Both
  `RuntimeActiveExperimentalPlanner` and every loader function that touches
  the workspace accept an `experimental_root` override; that is what makes
  isolated testing possible without touching a contributor's real
  `runtime/parts/`, `runtime/archive/`, `ACTIVE_PART.json`, or
  `runtime/active/notes.md`.

  Verified end to end in `tests/test_experimental_hot_reload.py`: seeded a
  clean-template planner in an isolated root, ran `export_nc()`, edited
  `runtime/active/planner.py` on disk (simulating an OpenClaw edit), called
  `export_nc()` again on the *same planner instance in the same process*,
  and confirmed the new logic's output showed up — with a second test
  confirming the real repo's `runtime/active/notes.md` is byte-identical
  before and after. This is the test coverage for the workflow you
  described: OpenClaw edits `Waam_tech/experimental/` only, and clicking
  "Run Pipeline + Experimental NC" reflects the new conditions in the NC.

  Separately, `tests/test_golden_artifacts.py`'s experimental golden-artifact
  test calls the lower-level pure functions
  (`_rewrite_layer_contour_then_fill` + `default_export_nc`) directly,
  purely to avoid the manifest/archive bookkeeping side effects on every CI
  run; this was verified to reproduce byte-identical output to the full
  `RuntimeActiveExperimentalPlanner.export_nc()` path.



## 12. Hygiene

- No committed `__pycache__/`, `.DS_Store`, or `.pytest_cache` were found in
git (`git ls-files | grep -E '__pycache__|\.DS_Store|\.pytest_cache'`
returned nothing) — already clean, nothing to remove.



## 13. Not done / explicitly out of scope

- No changes to deterministic planning/geometry logic anywhere — every
change here is additive metadata, a new validation checkpoint, new tests,
new tools, or UI-layer surfacing of data that was already computed.
- The zip packaging step from the original brief was not produced; ask if
you want the working tree archived.



## 14. Mejoras implementadas para trayectorias WAAM

- **Autoescalado geométrico de proceso:** `slice_plan.py` ahora puede
ajustar automáticamente parámetros de deposición y de trayectoria a partir
de las dimensiones reales de la geometría en WCS local (`width`, `depth`,
`height`, `max_span`, diagonales). Esto permite que el `bead_width_mm`, el
`layer_height_mm`, el `overlap_pct` y otros parámetros se adapten al tamaño
de la pieza en vez de quedar fijos para todos los casos.
- **Trazabilidad del autoescalado:** el resultado del autoescalado queda
registrado dentro del plan (`metadata.slice_diagnostics` y
`metadata.stage_manifest.auto_scale`), incluyendo qué variable actuó como
driver, su valor, el factor de interpolación y los valores finales
aplicados. Así se puede verificar después por qué una pieza usó una
separación entre cordones o una altura de capa concreta.
- **Control desde la UI:** el diálogo de `Slice Planner` ahora expone un
checkbox para activar o desactivar `auto_scale` sin editar el JSON a mano,
y el override se inyecta correctamente dentro de `process.auto_scale`.

- **Modo de deposición `hatch_only`:** se cambió el flujo para permitir
generar capas sólo con relleno longitudinal, sin hacer primero el contorno
exterior que envolvía la pieza. En la configuración estándar el patrón
activo pasa a ser `hatch_only`, con orientación fija longitudinal.
- **Trayectoria continua tipo snake:** cuando una región se rellena con
hatch, el planner ya no emite sólo segmentos independientes; ahora los
ordena como una secuencia continua `INFILL -> LINK -> INFILL`, conservando
la lógica de avance tipo serpiente dentro de la capa.
- **Huecos y vacíos respetados:** los segmentos de hatch siguen recortándose
contra el contorno y contra los agujeros interiores. Si entre dos pasadas
aparece un vacío, el sistema inserta un path `LINK` con `skip_deposition`,
de modo que la antorcha se desplaza hasta la siguiente pasada sin aportar
material dentro del hueco.

- **Generación NC compatible con `LINK`:** `waamgen/pipeline.py` ahora
entiende esos paths intermedios. Durante un `LINK` apaga la antorcha,
mantiene la continuidad del recorrido y vuelve a encender la deposición al
entrar en la siguiente pasada útil. Con esto la trayectoria sigue siendo
continua en movimiento, pero no continua en aporte cuando hay un vacío.
- **Trim, parches y validación geométrica adaptados:** las rutas `LINK` ya
no se tratan como trayectorias de deposición en soporte, geometry patch ni
validación. Eso evita falsos positivos y evita que un tramo de travel se
considere material depositado.
- **Robustez adicional en WCS:** durante la verificación apareció una
inconsistencia menor en el generador de G-code: algunas partes aceptaban
`metadata.wcs` como string y otras como objeto. Se corrigió para aceptar
ambos formatos de forma consistente.

- **Verificación local realizada:** se comprobó con un plan sintético mínimo
`INFILL -> LINK -> INFILL` que el NC resultante hace exactamente esta
secuencia: antorcha encendida en la pasada, antorcha apagada en el puente
del vacío, travel hasta la siguiente línea y nueva activación de
deposición. La validación devolvió `syntax_ok=True`, `safety_ok=True` y
`geometry_ok=True`.

- **Limitación actual conocida:** cuando existe un vacío, el `LINK` lo cruza
en travel recto con la antorcha apagada. Eso evita depositar material en el
hueco, pero todavía no implementa un rodeo siguiendo el contorno del vacío.

## 15. Verification of feedback points 1-4 (this session)

All four items from the prior feedback pass were checked by reading the
diff and re-running the affected tools, not just by inspection.

- **Point 1 (auto-scale default consistency):** Fixed correctly.
`config_loader.py` gained a single shared `DEFAULT_AUTO_SCALE_ENABLED` +
`auto_scale_enabled_from_config()`; both `ui_wcs.py` and `ui_workflow.py`
now read from it instead of hardcoding `True`/`False` independently. Bonus
find while reviewing: the old `ui_wcs.py` override dict had
`"auto_scale": {...}` at the *top level* instead of nested under
`"process"`, so it was silently ignored by `_apply_auto_scale()` regardless
of the checkbox — that placement bug is now fixed too. New tests in
`test_config_loader.py` (`test_auto_scale_enabled_uses_shared_default_when_missing`,
`test_shipped_auto_scale_default_matches_shared_ui_default`, etc.) cover it.
- **Point 2 (inert process parameters):** Fixed correctly. Wire diameter,
voltage, current, torch config, and max interpass temp all now carry a
tooltip stating they're recorded for traceability only and are not
transmitted to the machine or emitted in NC; same for the heat-input
display. No functional wiring was added, which is the right call — these
correspond to machine-side synergic-curve settings in a real WAAM cell,
not toolpath parameters.
- **Point 3 (silent slicing failures):** Fixed correctly and thoroughly.
`slice_shape_at_z()` / `generate_layer_slices()` now take an optional
`diagnostics` collector recording every skipped Z (reason, exception
type/message, wire/contour counts), wired all the way through
`write_slice_plan_for_document()` into `metadata.slice_diagnostics.skipped_z_*`
in the plan JSON plus a console warning. Backward compatible (parameter
defaults to `None`, no-op).
- **Point 4 (knowledge/promotion evidence gaps):** Fixed correctly and
thoughtfully. Added a `## Review checkpoint` (status: iterating/validated/
rejected + reason/evidence/next action) to the lesson template, an
explicit "Evidence Rule" in `promoted/README.md`, and backfilled both
existing promoted strategies with real numbers from archived runs —
while explicitly noting that backfilled legacy evidence isn't the same as
a `review.status: validated` run. `experimental/runtime/loader.py` and
`layer_by_layer_scaffold.py` wire this review state through
`manifest.json`/`notes.md`/`prompt_context.json` generation. Also declares
an `archive_policy` (keep last 5 per part, keep validated/promoted
evidence) — note this is currently a **declared policy surfaced in
metadata, not an enforced one**: nothing yet deletes old
`runtime/archive/` snapshots based on it.

### Bug found and fixed: stale `waam_baseline_experimental.nc` golden fixture

Running the full test suite this session initially failed with an
`ImportError` (`test_golden_artifacts.py` importing
`_rewrite_layer_contour_then_fill` from
`Waam_tech.experimental.runtime.active.planner`) because that module is a
live mirror of whichever part was last run through the experimental
pipeline (currently "body"/EngineBlock, not "pieza2") — exactly the kind
of unstable dependency `tests/test_experimental_hot_reload.py` exists to
warn about. Fixed by freezing the exact planner/helpers pair that produced
the golden NC into `tests/fixtures/golden/experimental_planner/` and
loading it via `importlib.util.spec_from_file_location` (the same
technique `loader.py` itself uses), so the test no longer depends on live,
unversioned state.

After fixing the import, the test still failed on content: byte-identical
frozen code run twice on the same frozen input produced identical,
reproducible output (verified directly) — but that output didn't match
the stored `waam_baseline_experimental.nc` fixture. Root cause: that
fixture was built last session from a `cp` of the *live*
`Waam_tech/output/operations_mesh/waam_slice_plan.json`, taken at a
different moment than the `cp` that froze `tests/fixtures/golden/waam_slice_plan.json`
itself — the live file was almost certainly overwritten in between (most
likely by this project's own real FreeCAD usage running concurrently),
producing an internally-inconsistent fixture pair. This was a bug in the
test fixture, not in any code change. Fixed by regenerating
`waam_baseline_experimental.nc` from `tests/fixtures/golden/waam_slice_plan.json`
itself (the same file `test_baseline_nc_matches_golden` already validates),
so both golden NC fixtures are now self-consistently derived from the one
frozen plan. Sanity-checked the regenerated NC (parses cleanly, 64,307
points, valid torch/gas sequencing) before committing it as the new
reference.

### New coverage added: LINK-path continuity

The new INFILL -> LINK -> INFILL continuity feature (`_append_hatch_snake_paths`
in `slice_plan.py`, consumed by `_layer_has_skip_paths()` /
`_emit_ordered_path_sequence()` in `pipeline.py`) had no automated
regression coverage — the golden fixture predates the feature (zero LINK
paths in it), and its prior verification was a manual one-off synthetic
check. Added `tests/test_link_path_continuity.py`, which builds a minimal
synthetic INFILL/LINK/INFILL plan and asserts: exactly two torch-on events
(one per deposition pass), the LINK crossing turns the torch off before
the next pass turns it back on, `validate_gcode()` reports
`syntax_ok`/`safety_ok`/`geometry_ok` all `True`, and no move parsed as a
deposition move falls inside the LINK's void-crossing region.

**Result: 67 tests pass** (`python -m pytest -q`), including the two new
ones above. Determinism re-verified: two independent `generate_nc()` runs
from the golden plan are byte-identical
(`tools/waam_regress.py` reports `[PASS]`).

## 16. Coverage gate implementation

The exposed `coverage_threshold` path in `waamgen.pipeline.generate_nc()` no
longer crashes through a placeholder. The gate now:

- opens the requested FreeCAD document or uses the active document,
- resolves the WAAM WCS and voxelizes the selected solid in WCS-local space,
- parses NC deposition moves with the configured torch on/off codes,
- voxelizes deposited bead volume using bead width, layer height, and sample step,
- returns pass/fail against the requested coverage percentage.

Added `tests/test_coverage_gate.py` for pure deposition voxelization and a
fake-shape gate pass/fail check. Also fixed parameterized torch-code parsing
(`M62 P0` / `M63 P0`) so torch-off `G1` travel does not get counted as
deposition during coverage analysis.

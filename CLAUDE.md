# CLAUDE.md — PowderLine

> ### ⚠️ Multi-engine refactor in progress (v0.2.0)
> **The architecture described below is the pre-refactor v0.1.1 baseline and is being replaced — do not treat it as the target.** Active on branch `refactor/multi-engine`. During the refactor the authoritative sources are, in order:
> 1. `_dev/dossiers/multi-engine/05-master-plan.md` — decision log (§3) + invariants (§4) + orchestration guidance (§6)
> 2. the active `_dev/dossiers/multi-engine/subplans/re-NN-*.md`
> 3. `_dev/dossiers/multi-engine/execution-runbook.md` (§8: sandbox environment and lessons) and the tail of `_dev/dossiers/multi-engine/execution-log.md`
>
> `00-pre-planning-findings.md` is a historical map of v0.1.0: its file:line anchors predate the move into `gateways/`.
>
> **Ask, don't assume (master plan A25):** if a decision isn't in the decision log or the active subplan, **stop and ask the maintainer — do not infer** — and record the answer in the dossier before proceeding.
>
> **Frozen:** GSAS-II setup/execution, now in `src/powderline/gateways/gsasii/{helpers,project,setters,executors,extractors,constraints}.py` (code-hashed), is move-only. Additive or input-side edits go only through the signed-off ledger `_dev/dossiers/multi-engine/tasks/re04-frozen-edits.md`.
>
> **Done so far (re/01–re/06 merged):** every engine lives under `src/powderline/gateways/{gsasii,topas,easydiffraction}/`, discovered through entry points. Native schemas exist next to the legacy path: core `schema_core`, plus `gsasii.rietveld`/`gsasii.spf`, `topas.rietveld`/`topas.spf` and `easydiffraction.rietveld`, each `1.0.0`. **Known-stale below, still to change:** the 0.26.0 schema, the `engine=` argument and the legacy translation paths are removed in re/07; the env split (core-only default env, `gsasii`/`easydiffraction` envs, the `easydiff` rename) comes in re/08; the public API changes in re/07.
>
> _This banner is removed in `re/10` (docs migration), which rewrites this file for the new architecture._

Automated powder X-ray diffraction refinement driven from a JSON "recipe". PowderLine
is the application layer over interchangeable refinement engines: a recipe (schema
**0.26.0**) describes the refinement, and `powderline.run()` drives GSAS-II by default —
or TOPAS / easydiffraction via `engine=` — and returns standardized tables.
Example-driven: real refinement cases drive schema evolution.

## Architecture (`src/powderline/`)

Core engine:
- `kicker.py` — the refinement engine: recipe loading/validation, GSAS-II project setup,
  all parameter setters, execution, and output extraction. Intentionally monolithic.
- `schema.py` — Pydantic models; `EXPECTED_SCHEMA_VERSION = "0.26.0"`,
  `schema_name ∈ {GSASII_Rietveld, GSASII_SPF}`. Schema changelog: `docs/SCHEMA_HISTORY.md`.
- `constraints.py` — per-parameter refine-flag → GSAS-II lumped-flag / Hold-constraint
  translation (the schema-0.26 refine-semantics); imported by `kicker.py`.
- `gsas_server.py` / `gsas_client.py` — persistent FastAPI server keeping GSAS-II in memory +
  HTTP client with in-process subprocess fallback (backends for `run()` `server`/`auto` modes).
- `config_loader.py`, `mp_interface.py`, `mp_simulate.py`, `simulation_builder.py`,
  `pattern_exporter.py` — Materials Project simulation pipeline (`pixi run mp-simulate`).

TOPAS v7 INP generation (`src/powderline/topas/`, **imports zero GSAS-II**):
- `conversions.py` — pure unit math + TOPAS equation-string builders.
- `symmetry.py` — gemmi-backed cell rules + site-symmetry DOF analysis.
- `writer.py` — recipe → `.inp` + `.xye` (`render_topas` pure; `write_topas_inp`
  does I/O). Handles `GSASII_Rietveld` (incl. `Uaniso` u11..u23) and `GSASII_SPF`
  (single-peak fitting). **Permissive**: TOPAS does not arbitrate recipe
  correctness, so symmetry-breaking refine flags **warn + emit**, never raise;
  hard errors are reserved for untranslatable input (unknown schema/space group,
  rhombohedral `:R`, SPF `use_instrument_profile`). Emits a structured
  `<base>_results.csv` `out` block + a `param_index` for the round-trip.
  `errors.py` holds `TopasTranslationError`.
- `roundtrip.py` — parse TOPAS `<base>_results.csv` (single source; no `.OUT`
  fallback) → standardized `refined_parameters.csv` + `<phase>_unit_cell_report.csv`
  + `fit_profile.txt` + `<phase>_peak_list_report.csv` (GSAS-II schema).
- `runner.py` — discover `tc.exe` (`--topas-dir`/`--topas-version`/config) and run
  it with an injectable subprocess (Windows-only execution, Linux-testable).
- `kicker.py` — end-to-end CLI: `pixi run topas-kicker <recipe.json>` generates
  INP+xye, runs TOPAS if found, and parses results into standardized tables;
  degrades to generate-only when tc.exe is absent. Flags: `--output DIR`,
  `--run`/`--no-run`, `--topas-dir`/`--topas-version`, `--validate-only`,
  `--parse-results <results.csv>`.
- `engine.py` (TOPAS adapter) + `src/powderline/engine.py` (dispatcher) — power
  `powderline.run(recipe, output_dir, engine="gsasii"|"topas"|"easydiffraction")`.
  `engine="topas"` returns the same result dict as GSAS-II (plus
  `rwp`/`r_exp`/`gof`) and imports no GSAS-II; `engine="gsasii"` (default) is a
  transparent pass-through to the GSAS-II `kicker.run` (imported lazily).

easydiffraction engine (`src/powderline/easydiff/`, **imports zero GSAS-II**;
requires the optional `easydiffraction` pixi environment — easydiffraction needs
Python ≥3.12):
- `conversions.py` — GSAS-II↔easydiffraction unit/convention math (U/V/W σ²
  centideg² ↔ Caglioti FWHM² deg², X/Y/Zero centideg↔deg, Rwp fraction↔percent,
  lowercase datablock slugs, fit-range/weight masking).
- `policy.py` — the pre-flight "honesty rule": fixed-unmappable recipe features
  are dropped with a recorded warning; anything *flagged for refinement* that
  can't be represented raises `errors.EasyDiffractionTranslationError`. Also
  validates space groups via `powderline.symmetry.cell_constraints`. Powers
  `validate_only` with **no easydiffraction import**.
- `builder.py` — recipe dict → easydiffraction `Project` + a manifest mapping
  every freed parameter back to GSAS-II naming/units (`scale_to_recipe`); cell
  symmetry enforced via gemmi `cell_constraints` (library does NOT police it).
- `engine.py` — adapter: build → lmfit fit (or `calculate()` for simulation) →
  same standardized result dict + report files as the other engines. v1 refines
  cell/scale/wavelength/Chebyshev-background/U-V-W-X-Y/zero; atom-level flags,
  Kα doublets, refined Z/polarization/axial-divergence/background-peaks and SPF
  are rejected loudly. Nonzero SH/L auto-selects the TCH profile with fixed FCJ
  asymmetry (CrysFML calculator; hkl lists harvested post-fit via CrysPy). Rwp is
  still NOT comparable to GSAS-II (no background peaks; profile details differ):
  LaB6 9.63% vs 6.01% (head-to-head demo lives in the private PowderLine-devkit,
  `dossiers/easydiffraction/example_engine_comparison/`).

The 6-name public API is in `src/powderline/__init__.py` `__all__`:
`run`, `validate`, `load_recipe_asset`, `validate_simulation_mode_parameters`,
`RecipeModel`, `GSASClient`.

## Key commands (pixi)

- `pixi run kicker <recipe.json>` — run a refinement (`--validate-only`, `--verbose`,
  `--output DIR`, `--use-server`/`--no-server`).
- `pixi run topas-kicker <recipe.json>` — generate a TOPAS v7 `.inp` + `.xye`
  (`--output DIR`, `--validate-only`, `--verbose`); no GSAS-II required.
- `pixi install -e easydiffraction` then `pixi run -e easydiffraction python ...` — the
  optional easydiffraction environment (Python ≥3.12 + easydiffraction + GSAS-II;
  the default env is untouched). The GSAS-II-vs-easydiffraction head-to-head demo
  is a dev artifact and lives in the private PowderLine-devkit
  (`dossiers/easydiffraction/example_engine_comparison/`).
- `pixi run test` — full pytest suite (run from the repo root).
- `pixi run gsas-server start|status|stop|restart` — manage the persistent GSAS-II server.
- `pixi run mp-simulate --material-id mp-2680` — simulate a pattern from
  Materials Project (`--formula`, `--wavelength`, `--no-server`, `--keep-recipe`).
- `pixi run docs` / `pixi run docs-clean` — build / clean the Sphinx HTML docs.
- `pixi reinstall powderline` — required after editing `[project.entry-points]` in
  `pyproject.toml`: entry-point metadata is written at install time, and the gateway
  registry (`powderline.registry`, group `powderline.gateways`) finds nothing until
  the editable package is re-installed.
- `pixi run update-code-hash` — regenerate `src/powderline/_code_hash.json` after
  editing `kicker.py` (guarded by `tests/test_code_hash.py`).

## GSAS-II / environment notes

- `gsas-ii` is built as a **conda package via `pixi-build`** from the
  `tacaswell/GSAS-II@enh/pixi_pkg` fork (adds the conda recipe); it currently
  resolves to **v5.7.9** and installs on all platforms. Don't bump the rev
  casually: GSAS-II (or scipy/BLAS) changes can shift refinement results, so
  re-run the regression suite when you do. Because exact refined values drift
  at the last digit across builds/OSes, `refined_parameters.csv` and the
  unit-cell reports are compared within tolerance (see
  `docs/regression-tolerance.md`; `tests/subprocess_utils.py`
  `refined_params_match` / `unit_cell_match`), and peak lists by row count.
  The TOPAS path imports zero GSAS-II even where GSAS-II is installed — that
  guarantee is enforced by subprocess tests that block the `GSASII` import.
- Platforms: `linux-64`, `win-64`, `osx-arm64`. **All functionality must work
  on Linux and Windows** (sole exception: TOPAS *execution* needs `tc.exe` on
  win-64; TOPAS input generation runs everywhere). Avoid Unix-only shell
  syntax in pixi tasks — see `docs/cross-platform-guide.md`.
- After editing engine code, restart the GSAS-II server
  (`pixi run gsas-server restart`) — it caches loaded code. After editing
  `kicker.py`, also run `pixi run update-code-hash`.
- **Testing caveat**: some `test_esd_extraction` / `test_example_*_regression` cases require
  a working GSAS-II refinement to produce output files; they can fail for environment
  reasons unrelated to a given change. When checking a change, compare against the baseline
  rather than assuming a failure is new.

## Conventions

- Refinement parameters are 4-element lists: `[value, refine_flag, min, max]`.
- Refinement output uses fixed filenames (`dummy.gpx`, `dummy.lst`, `refined_parameters.csv`,
  `<phase>_unit_cell_report.csv`, …). `unit_cell_data`/`peak_list_data` from `run()` are dicts
  keyed by phase name.
- Known, deliberately-deferred behaviors are registered with stable `KI-NN` ids in
  `docs/known_issues.md` — check it before "fixing" a quirk.
- Don't commit secrets: `.powderline_config.yaml` is gitignored.
- `examples/**/output/` **is** tracked (regression references) — do not gitignore it.
- `_dev/` is a reserved, gitignored path (maintainers' private workspace). Never
  `git add` it or anything under it.

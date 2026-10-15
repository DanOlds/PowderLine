# Changelog

All notable changes to the PowderLine **package** are recorded here. The format
follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/); PowderLine
versions follow [Semantic Versioning](https://semver.org/)
<!-- master plan A12/A16: SemVer + root CHANGELOG; v0.1.1 = pre-refactor tag, v0.2.0 = the refactor -->.

The recipe **schemas** are versioned separately from the package (core schema +
one schema per engine) and have their own changelog,
[`docs/SCHEMA_HISTORY.md`](docs/SCHEMA_HISTORY.md). Where a bullet below cites
"(row N, A…)" it refers to the maintainers' deliberate-behaviour-changes table
(row N) and decision-log entry (A…); "(P-NN)" is a provisional decision taken
while the maintainer was away, to be confirmed.

## 0.2.0 — unreleased

<!-- The version in src/powderline/__init__.py, CITATION.cff and pixi.toml is
     set to 0.2.0 at the release step (G4), together with the date below. -->

PowderLine 0.2.0 is a **clean break** from the unified recipe schema 0.26.0
(A4, A14): one recipe now targets one engine, in that engine's own
conventions, and nothing is translated between engines. Existing 0.26.0
recipes do not validate; see the
[migration guide](docs/migration-0.26-to-1.0.md).

### Added

**Schemas and versioning**
- **Core schema 1.0.0** (`src/powderline/schema_core.py`): the shared top level
  (`schema_name`, `core_schema_version`, `engine_schema_version`, `metadata`,
  `payload`) and the structural models every engine uses — `xrd_data`,
  `fit_range`, Chebyshev background, parameter lists, and one **phase block**
  per phase (`space_group`, `unit_cell`, `atoms`) whose every structural
  quantity is a refinable parameter and whose flags, values and bounds are
  checked against the symmetry (A93; `SCHEMA_HISTORY.md` "Core schema").
- **Engine schemas 1.0.0** on core 1.0.0: `gsasii.rietveld` / `gsasii.spf`,
  `topas.rietveld` / `topas.spf` (TOPAS 6), `easydiffraction.rietveld`
  (`SCHEMA_HISTORY.md`, one section per engine).
- **Declared compatibility** (A40, A65): core declares the core-schema
  versions it accepts; each gateway declares `engine_schema_version`,
  `supported_engine_schemas` and `requires_core_schema`. `validate()` enforces
  the declarations; `powderline.support_matrix()` lists them. See
  [`docs/support-matrix.md`](docs/support-matrix.md).
- **`scripts/convert_recipe_026.py`**: converts a 0.26.0 `GSASII_Rietveld` /
  `GSASII_SPF` recipe to `gsasii.rietveld` / `gsasii.spf`, writing out
  explicitly what v0.1.1 effectively refined and reporting every change; it
  stops, listing every problem, where a refinement cannot be stated (row 21,
  A132). No converter exists for TOPAS or easydiffraction (A137).
- **Structured warnings** (`warnings: [{code, message, field_path}]`) in
  results and validation output (A23), e.g. `gsasii_broadening_default_applied`,
  `gsasii_parameters_not_varied` (row 20, A128), `uaniso_not_positive_definite`
  (A118), `topas_parameter_at_limit`, `easydiffraction_parameter_at_limit`,
  `*_phase_contributes_nothing`. Each entry may carry a `level` (P-34):
  `"warning"` (absent means this) or `"info"`; the CLI prints warnings and
  counts info entries (listed with `--verbose`). Info-level
  `symmetry_value_derived` records every coordinate, cell value or Uij the
  validated model holds in its exact symmetry-derived form because it was
  stated within tolerance of it (stated and held values in the message);
  an exactly stated value gets none, so the note is evidence of a symmetry
  relation, not a statement of one. gsasii adds
  `gsasii_background_default_applied` and
  `gsasii_refinement_cycles_default_applied` when those blocks are left out
  (P-33), alongside the size/strain warning. `topas_phase_contributes_nothing`
  and `easydiffraction_phase_contributes_nothing` are level `info`.
- **Exceptions** in `powderline.exceptions`: `SymmetryError`,
  `GatewayNotInstalledError`, `EngineNotAvailableError`, `EngineVersionError`,
  `EngineExecutionError` (A23; row 1, A56/A57/A61).

**Results (all engines)**
- Uniform fit statistics computed once in core: `rwp`, `r_exp`, `gof`,
  `chi2_red`, in percent / dimensionless, over the points inside the fit
  window with positive weight (A23, A55, A125). The engine's own values, plus
  `parameters_requested` / `parameters_varied` and (gsasii Rietveld)
  `converged`, sit under `engine_details` (A45). Every native result carries
  `simulation_mode` (bool); a statistic without degrees of freedom is `None`,
  never NaN (row 20, A129).

**gsasii**
- New validation errors, each at the field it concerns, where v0.1.1 ran
  anyway or failed at run time: SH/L below 0.002 (row 8, A91); background
  peaks below GSAS-II's evaluation floors (intensity 0.1, σ² 0.01 centideg²,
  γ 0.1 centideg) and Peak List peaks below σ²/γ 0.001, or with a `null`
  entry (row 9, A86/A90); any refine flag in a simulation
  (`refinement_cycles` 1) (row 10, A88); a refined strict subset of a
  symmetry tie group or GSAS-II cell group, or a refined symmetry-fixed
  parameter (row 11, A95); a stated `Multiplicity` that differs from the
  derived one (row 12, A101); a non-canonical space-group spelling, origin
  choice 1, or one of the 9 settings GSAS-II cannot express (row 13, A105);
  `scale` < 0 or absent (row 14, A96); wavelength ≤ 0, crystallite size ≤ 0, a
  Peak List peak outside the fit window, and an SPF refine flag on the width
  source not in use (row 19, A126/A127); a `fit_range` holding no weighted
  point (row 24, A149).
- A run **fails with a message** when GSAS-II reads an atom's site with the
  wrong multiplicity or cannot name it while a coordinate/`Uaniso` is refined
  (row 17, A117), and when the refinement diverges (non-finite calculated
  pattern) (row 18, A130).

**topas**
- Native `topas.*` schemas (hand-derived fixtures in `tests/data/topas/native/`;
  A137): TOPAS's own keywords, macro parameters and units; a required
  `engine_version` checked against the gateway's declared support set and,
  at run time, against the installed version the user declares (A144, A145).
  Bounds replace TOPAS's default limits side by side (A138). Results report
  TOPAS's own statistics under `engine_details`, and the background column
  comes from a second background-only run (A152).

**easydiffraction**
- Native `easydiffraction.rietveld` schema (fixtures in
  `tests/data/easydiffraction/native/`): easydiffraction's own names and units,
  a stated `calculator` (`cryspy` | `crysfml`), per-atom refinement including
  anisotropic ADPs with CrysPy, physical limits as bounds (row 26, A157–A162,
  A169).

**Examples**
- Per-engine recipes for the two reference cases, `examples/<case>/topas/input.json`
  and `examples/<case>/easydiffraction/input.json` (LaB6, LaB6 single peak
  fitting, DRX_33), byte-for-byte copies of the native fixtures, each with a
  README; per-engine templates `examples/example_template/{topas,easydiffraction}/input.json`
  (P-02, P-20, provisional).

**Project**
- This `CHANGELOG.md` (A12/A16), the migration guide
  [`docs/migration-0.26-to-1.0.md`](docs/migration-0.26-to-1.0.md), and the
  new documentation pages: recipe schemas, fields and features (tables
  generated from the schemas by `scripts/gen_schema_field_tables.py`, P-27),
  input guidance, engine behaviour notes (from the engine-behaviour register,
  A79), cross-engine comparison (A173; `scripts/cross_engine_comparison.py`),
  support matrix (generated from `powderline.support_matrix()`), policies.
- The `powderline` console script (`[project.scripts]`), installed with the
  package (`pixi reinstall powderline`).

### Changed

- **Failures raise; results carry `status` and `converged`** (re/12). `powderline.run()`
  and every gateway raise `EngineExecutionError` (the engine ran and failed:
  `diverged`, `unusable_site`, `peak_fit_failed`, `engine_crashed`, `output_missing`,
  `output_malformed`) or the new `ExecutionBackendError` (the engine did not run:
  `server_unavailable`, `server_disabled`, `output_not_visible`,
  `communication_failed`, `timeout`, `backend_error`), each with `.reason` and
  `.result` (the failed envelope, listing the files written before the failure,
  which are kept). gsasii no longer returns `success: False` dicts (ledger R30).
  Every returned result has `status` (`"ok"`, or `"generated"` for a TOPAS run
  without a declared `tc.exe`, on which the CLI now exits 0), `converged`
  (`true`/`false`/`null`) and `envelope_version` (`"1"`, the GSAS-II server's wire
  contract); `success` is a deprecated always-`true` alias (removed in 0.3.0);
  the top-level `error` and `traceback` keys are **removed** -- a consumer
  reading `result["error"]` now gets `KeyError`. A timed-out server request is
  a `timeout` failure and no longer falls back in-process into the same output
  directory. `GSASClient.run_simulation` (unused) is removed. All five
  exception classes pickle (`ProcessPoolExecutor`).
- **TOPAS runs only from a declared location** (P-35): `--topas-dir` or
  `topas.dir` in `.powderline_config.yaml`. The former sweep of
  `C:/TOPAS8 … 5` and the `C:/TOPAS<version>` guess are gone, because TOPAS
  carries no readable version and a guessed directory could silently run a
  different TOPAS than the recipe's `engine_version` (A145). With no
  directory declared a run is generate-only and says so; a real run records
  the executable in `engine_details.tc_path`.

**Statistics and fit window (all engines)**
- **`rwp` means something different.** On a native run it is PowderLine's
  uniform Rwp over the fit window (zero-weight points excluded), not the
  engine's own value; GSAS-II's wR moves to `engine_details`. For LaB6 the two
  agree to the fourth decimal (6.5345 vs 6.5346); for a simulation GSAS-II
  reports its 100 % cap while the uniform Rwp compares the simulated pattern
  with the stated observation (row 16, A23/A125/A129/A131).
- **The fit window is exactly min ≤ 2θ ≤ max on the data points.** GSAS-II
  gets the first and last weighted points inside `fit_range` as its limits.
  With a maximum between data points v0.1.1 also fitted the next point above
  it, so `Nobs` drops by one and refined values move slightly (LaB6: scale
  5e-6 relative, U 7e-4, highest Chebyshev term 1 %); without a `fit_range`
  zero-weight edge points are no longer counted (row 23, A149/A150/A154).

**gsasii**
- **Size/strain left out** now means 10 µm / 0 microstrain, isotropic, fixed,
  with a structured warning — no sample broadening — instead of GSAS-II's own
  1 µm / 1000 microstrain (row 6, A87/A107/A119).
- **Background left out** now means one fixed Chebyshev term 0.0 (no
  background) instead of GSAS-II's constant 1.0 (row 7, D1/A119).
- **Space groups** must be gemmi's canonical extended Hermann–Mauguin name
  (`"P m -3 m"`, `"C 1 2/m 1"`, `"F d -3 m:2"`); the converter rewrites old
  symbols as GSAS-II read them (row 13, A105).
- **The LaB6 examples** (`example_LaB6`, `example_LaB6_simulation`) lose their
  single background peak at the example migration: its γ = 1e-5 is below
  GSAS-II's floor and no variant of it converges; the regression references
  lose the four `background_peak_0_*` rows (P-01, provisional).
- **`mp-simulate`** writes SH/L 0.002 (the gsasii floor) instead of 0.001
  (P-05, provisional), emits a `gsasii.rietveld` 1.0.0 recipe and ships with
  the GSAS-II gateway (`powderline.gateways.gsasii.mp_simulate`; pixi task
  `pixi run -e gsasii mp-simulate`). Its structures are now reduced to the
  symmetry-unique sites (v0.1.1 handed GSAS-II every atom of the conventional
  cell, so a site was counted *multiplicity* times) and two-origin groups are
  moved to origin choice 2; the simulated grid includes `max_2theta` (row 31,
  P-17, provisional).
- **The regression references** under `examples/*/output/` were regenerated
  once, on the native path, after the legacy and native paths were shown to
  agree exactly on every example once the deliberate changes above are pinned
  (row 30). The four DRX_33 refinements now report `converged: false` after
  their five cycles (they always ran that way; now it is said).

**easydiffraction**
- **Native results differ from the v0.1.1 easydiffraction path**: data reach
  the engine at full precision; the instrument terms are the engine's own,
  derived correctly (polarization p = 1 − P, Lorentzian X/Y swapped, FCJ
  S/L = H/L = SH/L/2); the calculator is stated, not inferred from SH/L;
  easydiffraction is pinned at 0.21.1; anisotropic ADPs are used (CrysPy).
  LaB6 Rwp: v0.1.1 9.63 %, native 6.77 % (CrysFML) / 6.81 % (CrysPy)
  (row 26, A116/A158–A162/A169).

**Symmetry helpers (core)**
- `powderline.symmetry` (formerly `powderline.topas.symmetry`) raises
  `powderline.exceptions.SymmetryError` instead of `TopasTranslationError`
  (row 1, A56/A57/A61); `resolve_space_group`, `site_dof` and `adp_dof` accept
  rhombohedral `:R`, while `cell_constraints` refuses it with a "not
  implemented" message (row 2, A62); trigonal/hexagonal sites are classified
  correctly, so TOPAS-writer warnings for those groups change wording or
  appear/disappear (emitted INP values unchanged) (row 3, A74).
- **gemmi is pinned exactly** (`==0.7.5`); the locked version is unchanged
  (row 5, A110).

**Packaging**
- `powderline.topas` → `powderline.gateways.topas` and `powderline.easydiff` →
  `powderline.gateways.easydiffraction`, **no shims**; an existing checkout
  needs `pixi reinstall powderline` for the entry points (rows 22 and 25,
  A136).
- **pixi environments** (A31, A42; P-21, provisional): the **default
  environment is core-only** (schema layer, registry, the engine-free CLI; no
  engine). Engines live in named environments: `gsasii` (GSAS-II via
  pixi-build, the server, `mp-simulate`), `easydiffraction` (renamed from
  `easydiff`; Python ≥ 3.12, no GSAS-II), `all` (every engine) and `dev` (all
  plus docs/test tooling). `pixi install -e gsasii` installs an engine;
  `pixi run -e gsasii kicker <recipe>` runs in it; engine-bound tasks
  (`gsas-server`, `mp-simulate`, `ui`) live in their engine's feature, so
  pixi asks for `-e` when the task is ambiguous; `pixi run test` and
  `pixi run docs` resolve to `dev`. A `gsasii.*` recipe validates in the
  default environment but a run there raises `EngineNotAvailableError`
  naming the environment and the install command. After the re-lock, every
  environment needs its own editable install (`pixi reinstall powderline
  --all`; KI-13).

**Public API and CLI** (P-06, P-11–P-15, provisional)
- `powderline.__all__` is `run`, `validate`, `ValidationResult`,
  `load_recipe_asset`, `support_matrix`, and the exceptions `PowderLineError`,
  `GatewayNotInstalledError`, `EngineNotAvailableError`, `EngineVersionError`,
  `EngineExecutionError`. `import powderline` imports no engine.
- `run(recipe, output_dir, *, verbose=False, validate_only=False,
  **engine_options)` dispatches on the recipe's `schema_name`; engine options
  are the gateway's explicit keywords (gsasii `execution_mode`; topas
  `topas_dir`, `topas_version`; easydiffraction none), an unknown one is a
  `TypeError`. There is no `engine=` keyword.
- `validate(recipe, *, verbose=False)` is engine-free and returns a frozen
  `ValidationResult(recipe=<the gateway's validated model>, warnings=[...])`;
  an invalid recipe raises pydantic's `ValidationError` listing every problem
  at its field path.
- One CLI, `powderline <recipe.json> [--output DIR] [--validate-only]
  [--verbose] [--use-server | --no-server] [--topas-dir DIR]
  [--topas-version V]`, serving every schema; an engine flag given for a
  recipe of another engine exits 1; `--validate-only` writes nothing. The
  pixi task `kicker` runs the same `main`.
- `GSASClient` lives at `powderline.gateways.gsasii.client`; the GSAS-II
  `run` body is `powderline.gateways.gsasii.gateway.run`.

### Removed

- **The unified 0.26.0 schema** (`schema_name` `GSASII_Rietveld` /
  `GSASII_SPF`, `schema_version`) and the cross-engine translation layer
  (`powderline.schema`, the legacy GSAS-II executor, the TOPAS writer /
  round-trip / `topas-kicker`, the easydiffraction builder / policy): a 0.26.0
  recipe is no longer accepted by any engine (A4, A14; row 32). From
  `powderline.validate()`, `powderline.run()` and the CLI it fails at
  `schema_name` with "does not name an engine: expected `<engine>.<workflow>`";
  the message names the expected form, not the converter — the
  [migration guide](docs/migration-0.26-to-1.0.md) does.
- `refinement_controls.refinement_algorithm` (v0.1.1 accepted and ignored it)
  and the `null`-valued `broadening` / `corrections` / phase
  `parameterization` forms that crashed v0.1.1 at run time: the gsasii
  instrument and phase blocks are explicit (row 15, D17/A92/A99).
- Parameter bounds in gsasii recipes: gsasii 1.0.0 exposes none (GSAS-II
  clamps after the fit); a 4-element list is a validation error and the
  converter reports every bound it drops (A84).
- `payload.asset_path` (not a gateway input, A50) and the second GSAS-II
  instrument-parameter dict `Iparm2` (never used) — both dropped by the
  converter.
- The legacy TOPAS 7.25 goldens `examples/*/output/topas/` (P-02,
  provisional; TOPAS 6 goldens live in `tests/data/topas/native/golden/`).
- From the public namespace (P-06, row 32): the `engine=` argument of
  `run()` (the recipe names its engine); `validate_simulation_mode_parameters`
  (the rule is in the gsasii schema: a refine flag in a one-cycle recipe is a
  validation error); `RecipeModel` and `powderline.schema` (the gateways'
  models come back from `validate().recipe`); `GSASClient` (now
  `powderline.gateways.gsasii.client.GSASClient`); the `powderline.engine`
  dispatcher module; the re/01 shims `powderline.constraints`,
  `powderline.gsas_client`, `powderline.gsas_server` (import
  `powderline.gateways.gsasii.{constraints,client,server}`); the
  `topas-kicker` console script and pixi task (the one CLI serves `topas.*`
  recipes).
- The `easydiff` pixi environment name (now `easydiffraction`) and GSAS-II
  from the default environment (now the `gsasii` environment).

### Fixed

- `powderline.symmetry.cell_constraints` handles the mirror-only monoclinic
  groups Pm, Pc, Cm, Cc (space groups 6–9) in every setting; before, all 41
  settings failed with "could not determine the monoclinic unique axis"
  (row 4, A102).
- Site-symmetry classification for trigonal and hexagonal groups (the
  column-only test was wrong when rotations are not orthogonal in fractional
  coordinates; checked against GSAS-II for all 230 groups) (row 3, A74).
- A gsasii run no longer computes 4× the intensity silently for some 2-fold
  sites in R32, R-3m, R-3c, nor crashes with a `KeyError` on a site GSAS-II
  cannot name: both are errors naming an equivalent position to state
  (row 17, A117, EB-40/EB-05).
- A diverged gsasii refinement no longer returns success with Rwp 100 % and a
  NaN profile (row 18, A130).
- The v0.1.1 easydiffraction path's unit conversions (polarization sign,
  Lorentzian X/Y, FCJ S/L and H/L) and its 4-decimal 2θ rounding (row 26,
  EB-72/EB-75/EB-76).
- Recipe files are read as UTF-8 (BOM tolerated); malformed JSON/YAML or a
  non-mapping document is a `ValueError` naming the file, and the CLI reports
  it as "Cannot read recipe" instead of a traceback. `--verbose` prints the
  traceback of an unexpected error.
- `.powderline_config.yaml` is read as UTF-8; a malformed file is an error
  (previously: defaults / "TOPAS not configured" silently).
- Documentation corrected to the code: TOPAS 6 (not v7), no default engine,
  nothing translated between engines; a fit window needs two weighted points;
  a gsasii simulation writes no `refined_parameters.csv`; example commands
  name the `gsasii` env; the getting-started recipe validates (and is tested).

### Known limitations carried into 0.2.0

- **Charged scattering types** (`"Fe3+"`) are still rejected, never reduced to
  the neutral atom; v0.1.1 passed them to GSAS-II unchecked. Supporting them
  correctly needs per-engine verification of the notation (P-03, provisional;
  A63 re-dated).
- **TOPAS 7** is out of scope: `topas.*` 1.0.0 supports TOPAS 6 only (P-07,
  A143).

## 0.1.2 — 2026-10-10

Tagged on `main` after the refactor branch was cut; merged into the 0.2.0
branch on 2026-10-14 (the server and client live at
`src/powderline/gateways/gsasii/server.py` / `client.py` there; the
known-issue entry is `KI-14` on 0.2.0 because `KI-13` was already taken).
<!-- git: b38c73e, tag v0.1.2 -->

### Fixed
- GSAS-II server made per-user and token-authenticated: per-user state
  directory, loopback bind on an ephemeral port, bearer token on every route,
  `POWDERLINE_NO_SERVER` to disable the server (#10).

## 0.1.1 — 2026-09-24

The pre-refactor baseline (A16); unified recipe schema 0.26.0.
<!-- git: fb90228, tag v0.1.1; 43 commits since v0.1.0 -->

### Added
- easydiffraction engine (`engine="easydiffraction"`, optional `easydiff` pixi
  environment) with the SH/L → Thompson-Cox-Hastings/FCJ profile mapping (#2).
- Documentation published on GitHub Pages (#4–#7).

### Fixed
- GSAS-II's refinement failure message is surfaced instead of a silent
  no-Rwp result; `run()` normalisation no longer crashes on null tables from
  server failure responses (#3).

## 0.1.0 — 2026-08-17

Initial public release.

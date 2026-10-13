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

<!-- DRAFT (re/10 workstream D). Sections marked "TODO re/07 P-06" are
     placeholders for the public API / CLI, which is still being finalised. -->

## 0.2.0 — unreleased

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
  `*_phase_contributes_nothing`.
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

**Project**
- This `CHANGELOG.md` (A12/A16) and the migration guide
  [`docs/migration-0.26-to-1.0.md`](docs/migration-0.26-to-1.0.md).

### Changed

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
- **`mp-simulate`** writes SH/L 0.002 (the gsasii floor) instead of 0.0 (P-05,
  provisional).

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
- <!-- TODO re/08: pixi environment names (`easydiff` → `easydiffraction`,
  `pixi install -e gsasii`, core-only default env) — state the final names
  once re/08 lands. -->

**Public API and CLI**
- <!-- TODO re/07 P-06: final API/CLI -->
  *Placeholder.* The public namespace (`powderline.__all__`), the `run()`
  signature, the single CLI entry point and the location of `GSASClient` are
  being finalised in re/07 (P-06). This bullet is rewritten when that work
  lands.

### Removed

- **The unified 0.26.0 schema** (`schema_name` `GSASII_Rietveld` /
  `GSASII_SPF`, `schema_version`) and the cross-engine translation layer: a
  0.26.0 recipe is no longer accepted by any engine (A4, A14).
  <!-- TODO re/07: confirm the exact removal commit and whether any error
  message points 0.26.0 recipes at the converter. -->
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
- <!-- TODO re/07 P-06: final API/CLI --> *Placeholder:* `engine=` argument of
  `run()`, `validate_simulation_mode_parameters`, `RecipeModel` and
  `GSASClient` in the `powderline` namespace, the `topas-kicker` entry point.

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

### Known limitations carried into 0.2.0

- **Charged scattering types** (`"Fe3+"`) are still rejected, never reduced to
  the neutral atom; v0.1.1 passed them to GSAS-II unchecked. Supporting them
  correctly needs per-engine verification of the notation (P-03, provisional;
  A63 re-dated).
- **TOPAS 7** is out of scope: `topas.*` 1.0.0 supports TOPAS 6 only (P-07,
  A143).

## 0.1.2 — 2026-10-10

Tagged on `main` after the refactor branch was cut; not part of the 0.2.0
branch history at the time of this draft.
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

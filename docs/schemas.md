# Recipe schemas

A PowderLine recipe is a JSON document that names **one engine and one
workflow** in its `schema_name` and is written in that engine's own
conventions, names and units. There is no translation between engines: a
`topas.*` recipe holds what goes into the TOPAS input file, an
`easydiffraction.*` recipe holds easydiffraction's own parameters, a
`gsasii.*` recipe holds GSAS-II's. The structural part (space group, unit cell,
atoms) is the same in every engine, so it can be moved between recipes; the
instrument, background and controls are the engine's.

Every recipe states two schema versions, and PowderLine **declares** which
versions it accepts (see [the support matrix](support-matrix.md)):

| Key | Meaning | Today |
|---|---|---|
| `schema_name` | `<engine>.<workflow>` | one of the five below |
| `core_schema_version` | the shared top level and structural models (`xrd_data`, `fit_range`, the phase block, parameter lists, `metadata`) | `"1.0.0"` |
| `engine_schema_version` | that engine's payload (instrument, background, controls, engine-specific phase fields) | `"1.0.0"` for every engine |

Validation never needs an engine: `powderline <recipe> --validate-only` (or
`powderline.validate(recipe)`) works in the core-only default environment for
every schema. A **run** needs the engine's environment, named in the table.

## The five schemas

| `schema_name` | Engine (supported version) | Workflow | Parameter shape | Run in | Template |
|---|---|---|---|---|---|
| `gsasii.rietveld` | GSAS-II 5.7.9 | Rietveld refinement, one or more phases | `[value, refine_flag]` (no bounds) | `pixi run -e gsasii kicker <recipe>` | `examples/example_template/input.json` |
| `gsasii.spf` | GSAS-II 5.7.9 | single peak fitting (GSAS-II Peak List), no phases | `[value, refine_flag]` | `pixi run -e gsasii kicker <recipe>` | derive from `examples/example_LaB6_singlepeakfit/input.json` |
| `topas.rietveld` | TOPAS 6 (`engine_version: "6"`) | Rietveld refinement | `[value, refine_flag, min, max]` | `pixi run kicker <recipe> --topas-version 6` on a Windows machine with `tc.exe` | `examples/example_template/topas/input.json` |
| `topas.spf` | TOPAS 6 | single peak fitting (`xo_Is` peaks), no phases | `[value, refine_flag, min, max]` | as above | derive from `examples/example_LaB6_singlepeakfit/topas/input.json` |
| `easydiffraction.rietveld` | easydiffraction 0.21.1 | Rietveld refinement; calculator `cryspy` or `crysfml` | `[value, refine_flag, min, max]` | `pixi run -e easydiffraction kicker <recipe>` | `examples/example_template/easydiffraction/input.json` |

easydiffraction has no single-peak-fitting workflow. The templates are
fill-in-the-blanks skeletons that the CLI refuses to run (`template` in the
path): copy one into your own directory, fill the blanks (data arrays,
wavelength, element symbols), and validate the copy.

Field-by-field tables, with units, are on the [fields page](fields.md). The
schema changelog is [SCHEMA_HISTORY](SCHEMA_HISTORY.md).

## Minimum inputs

Every recipe has the frame `schema_name`, `core_schema_version`,
`engine_schema_version`, an optional free-form `metadata` block (echoed into
results, never read; at most 1 MiB), and `payload`. Unknown keys are errors
at every level. Inside `payload`:

### `gsasii.rietveld`

Required: `xrd_data` (`tth`, `Itth`, `Itth_weights`), `instrument` with all
four blocks (`radiation {type: "PXC", wavelength}`, `geometry {bank, azimuth}`,
`corrections {zero_shift, polarization, axial_divergence}`,
`broadening {U, V, W, X, Y, Z}`), at least one phase in `phases` (each with
`space_group`, `unit_cell`, `atoms` and `scale`), and `refinement_controls`
(`refinement_cycles`, default 5; `1` is a simulation in which nothing may be
refined). Optional: `fit_range`, `background` (left out: no background),
`peak_broadening` per phase (left out: 10 µm / 0 microstrain, fixed, with a
warning), `instrument.description`.

### `gsasii.spf`

Required: `xrd_data`, `instrument` (as above), `single_peaks` (four lists of
the same length: `positions`, `intensities`, `pv_gaussian_sigma_sq`,
`pv_lorentzian_gamma`) and `refinement_controls` with
`single_peak_fitting_mode.use_instrument_profile`. No `phases`. Optional:
`fit_range`, `background`.

### `topas.rietveld`

Required at the top level: `engine_version` (`"6"`). In the payload:
`xrd_data`, `instrument` with `radiation` (`ymin_on_ymax`, `la`, `lo`, `lh`)
and `broadening` (`peak_type` and exactly that peak type's `parameters`),
at least one phase (`space_group`, `unit_cell`, `atoms`, `scale`), and
`refinement_controls` with every control stated (`iters`,
`chi2_convergence_criteria`, `x_calculation_step`; `iters: 0` is a
simulation). Nothing has a default: `fit_range`, `background`,
`instrument.corrections`, `instrument.geometry` and the phases'
`peak_broadening` are optional, and a block left out is **not modelled**.

### `topas.spf`

As `topas.rietveld`, with `single_peaks` (a list of peaks, each `xo`, `I`
and optionally `gauss_fwhm`, `lor_fwhm`) instead of `phases`.

### `easydiffraction.rietveld`

Required: `xrd_data`, `instrument` with `radiation {setup_wavelength}`,
`corrections {calib_twotheta_offset}` and `broadening` (`peak_type` and its
`parameters`), at least one phase (`space_group`, `unit_cell`, `atoms`,
`scale`), and `refinement_controls` with every control stated
(`calculator`, `minimizer`, `max_iterations`, `chi_square_change_tolerance`,
`parameter_change_tolerance`, `gradient_tolerance`). A recipe with no refine
flag set is a simulation. Optional: `fit_range`, `background`,
`instrument.polarization`, `instrument.absorption`, a Kα2 line in
`radiation`; left out means not modelled.

## Outputs

### The result dict (`powderline.run()`)

Every engine returns the same keys. Tables are pandas DataFrames; per-phase
tables are dicts keyed by phase name.

| Key | Meaning |
|---|---|
| `success`, `error`, `traceback` | outcome; `error`/`traceback` are `None` on success |
| `run_id` | UUID4 of this run |
| `rwp`, `r_exp`, `gof`, `chi2_red` | PowderLine's **uniform** statistics over the fit window (percent / dimensionless); `None` where undefined |
| `simulation_mode` | `true` when nothing was refined |
| `engine_details` | the engine's own statistics and counts: `parameters_requested` (from the recipe), `parameters_varied` (what the engine varied), `n_points`, the engine's own Rwp/GoF, and per engine `converged` (gsasii Rietveld), `calculator` / `peak_type` (easydiffraction), `engine_version` |
| `warnings` | structured warnings `{code, message, field_path}` from validation and from the run |
| `elapsed_time`, `method` | wall-clock seconds; which execution path ran (`server` / `subprocess` for gsasii; `topas`, `easydiffraction`, or their `_simulation` variants) |
| `output_files` | paths written to the output directory |
| `fit_profile` | `two_theta, y_obs, y_weights, y_calc, y_diff, y_bkg, q_values, d_spacings` |
| `unit_cell_data` | per phase: `parameter, value, esd` |
| `peak_list_data` | per phase: reflection list (`h, k, l, …, d_spacing, 2theta, …`; columns are the engine's) |
| `refined_parameters` | 9 columns: `parameter_name, descriptive_name, phase_name, phase_idx, atom_name, atom_idx, value, esd, category` |
| `spf_peaks`, `spf_convergence_diagnostics` | single peak fitting only (empty otherwise; the gsasii and topas column sets differ) |

With `validate_only=True` the result is a slim summary (`success`,
`schema_name`, `core_schema_version`, `engine_schema_version`, `phases`,
`simulation_mode`, `warnings`, and the engine's controls such as
`refinement_cycles`, `iters` or `calculator`); nothing is written.

### Standardized report files

Written by every engine into the output directory, with fixed names:
`refined_parameters.csv`, `<phase>_unit_cell_report.csv`,
`<phase>_peak_list_report.csv` (not with the CrysFML calculator, which reports
no reflection list), `fit_profile.txt`. Single peak fitting adds a peak report
(`single_peaks_report.txt` for gsasii, `single_peaks_report.csv` for topas;
gsasii also writes `peak_convergence_diagnostics.txt` when a peak did not
converge).

### Native artifacts

Kept alongside the standardized reports, always:

- **gsasii**: `dummy.gpx` (the GSAS-II project, reopenable in the GUI) and
  `dummy.lst` (GSAS-II's refinement log with its own Rwp and parameter
  tables; the regression suite reads it).
- **topas**: `<base>.inp` and `<base>.xye` (what TOPAS was given), and what
  TOPAS wrote: `<base>_results.csv`, `<base>_profile.txt`,
  `<base>_p<n>_peaks.txt` per phase, plus the background-only run
  `<base>_bkg.inp` / `<base>_bkg_profile.txt` that provides the `y_bkg`
  column (TOPAS 6 cannot output the fit's background). `<base>` is the
  example directory's name when the output directory is `.../<name>/output`,
  else `topas`. Without `tc.exe` a run writes the `.inp` and `.xye` only and
  reports failure.
- **easydiffraction**: none beyond the standardized reports; the data and
  structure are handed to the library in memory.

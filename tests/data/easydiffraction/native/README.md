# Native `easydiffraction.rietveld` fixtures (A137, A157 B8)

Hand-derived from the GSAS-II examples by the maintainer's script
`PowderLine-devkit/dossiers/multi-engine/probes/re06/make_native_fixtures.py`
(run from the PowderLine root; it needs only `json`, `math` and `examples/`).
Do not edit the JSON by hand: change the script and re-run it.

| Fixture | Calculator | Peak type | What it exercises |
|---|---|---|---|
| `lab6_crysfml_tch.json` | CrysFML | `cwl-thompson-cox-hastings` | LaB6 with FCJ axial asymmetry (the legacy path's choice for SH/L != 0); 13 parameters refined |
| `lab6_cryspy_pv.json` | CrysPy | `cwl-pseudo-voigt` | LaB6 without asymmetry; 13 parameters refined |
| `lab6_slots_simulation.json` | CrysPy | `cwl-pseudo-voigt-berar-baldinozzi-asymmetry` | every instrument slot stated (Kα2 pair, polarization, displacement, transparency, absorption, `cutoff_fwhm`); nothing refined, no `fit_range`, no `background`: a calculation only (A129). Not a physical model |
| `drx33_two_phase_cryspy.json` | CrysPy | `cwl-pseudo-voigt` | two phases from `example_DRX_33`: cubic DRX_33 (`F m -3 m`, four cations sharing 4a) and monoclinic Li4MgWO6 (`C 1 2/m 1`, gemmi's canonical name; easydiffraction `C 2/m` + `coord_system_code` `b1`); 18 parameters refined |

The LaB6 fixtures take `xrd_data`, the instrument description and the
instrument values from `examples/example_LaB6_easydiff/input.json`
(GSAS-II `initialization[0]`); DRX_33 takes its data and structures from
`examples/example_DRX_33/input.json`, whose instrument parameters equal LaB6's
(asserted by the script). The example's size/strain broadening has no
easydiffraction counterpart: U, V, W, X, Y are refined instead, with starting
values from a first CrysPy fit (2026-10-09, Rwp 7.25 %) rounded to 4 digits so
the test fit is short.

## Derivations (GSAS-II -> easydiffraction, native units A4)

| Quantity | GSAS-II | easydiffraction | Rule |
|---|---|---|---|
| Gaussian U, V, W | σ² in centideg² | `broad_gauss_u/v/w`: FWHM² in deg² | multiply by G = 8 ln 2 · 1e-4 (FWHM² = 8 ln 2 σ²; centideg² -> deg²) |
| Lorentzian X, Y | γ = X / cos θ + Y tan θ, centideg | `broad_lorentz_x/y`: FWHM = X tan θ + Y / cos θ, deg | **the roles swap** (EB-76, A171): `broad_lorentz_x` = GSAS-II Y / 100, `broad_lorentz_y` = GSAS-II X / 100 |
| Polarization P | (1 − P) cos² 2θ + P at azimuth 0 | `setup_polarization_coefficient` p: 1 − p + p cos² 2θ_m cos² 2θ | **p = 1 − P** (EB-75); `setup_monochromator_twotheta` 0 |
| Zero | centideg | `calib_twotheta_offset`, deg 2θ | divide by 100 |
| Wavelength | Å | `setup_wavelength`, Å | as is |
| SH/L (axial divergence) | (S + H) / L, floored at 0.002 by GSAS-II (EB-37, A91) | `asym_fcj_1` = S/L, `asym_fcj_2` = H/L | the floor 0.002 split S = H (assumed): each = SH/L / 2 = 0.001 |
| Background | Chebyshev coefficients | `background.chebyshev` | as is (the experiment's, A189) |
| Scale | phase scale | `phases.<name>.scale` | starting values near the refined ones |

The slots-simulation extras (`setup_wavelength_2` 0.1669 with ratio 0.5,
displacement 0.001, transparency −0.0005, `mu_r` 0.3, Bérar–Baldinozzi
`asym_beba_*`, `cutoff_fwhm` 20) are arbitrary values that reach the engine
(A160); they model nothing physical.

## Goldens

`golden/` (when present) is written by the devkit's `make_goldens.py` with
easydiffraction installed: the built project's parameters per fixture and, for a
run, the statistics, refined values, ESDs and unit cell, with the versions of
easydiffraction, its calculators, lmfit, numpy and scipy (A179). Regenerate only
on a deliberate engine change (A44).

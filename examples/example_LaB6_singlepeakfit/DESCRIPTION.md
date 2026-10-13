# LaB6 Single Peak Fitting with File-less Payload

## Scientific Purpose

This example demonstrates **single peak fitting** capabilities in PowderLine using LaB6 calibrant data (NIST SRM 660c).
Unlike traditional Rietveld refinement that fits peaks based on crystal structure, single peak fitting refines individual peaks directly
with control over position, intensity, and pseudo-Voigt width parameters (Gaussian sigma_sq and Lorentzian gamma).

This approach is useful for:
- Fitting peaks without crystal structure constraints (e.g., amorphous materials, unknown phases)
- Refining individual peak shapes independently of instrument parameters
- Detailed peak shape analysis and width determination

The example uses both **background single peaks** (fitted as part of the background model) and **top-level single peaks** (fitted in the Peak List).
With `use_instrument_profile: false`, the peak widths are refined directly rather than being derived from instrument parameters (U, V, W, X, Y, Z).

## Input Parameters

- **Data source**: File-less payload carrying all necessary information including the diffraction data, instrument parameters, and structure information
- **Phases**: None - this is pure single peak fitting without phase structure
- **Background**:
  - Chebyshev polynomial with 6 coefficients (refined)
  - 1 background single peak (position, intensity, sigma, gamma all refined)
- **Single peaks (Peak List)**:
  - 36 peaks at different 2θ positions
  - All parameters refined: positions, intensities, Gaussian sigma_sq, Lorentzian gamma
  - `use_instrument_profile: false` - peak widths refined independently
- **Instrument parameters**: Provided but not refined in this example

## Expected Behavior

- All single peak parameters should refine successfully
- Output includes `single_peaks_report.txt` with refined peak parameters plus calculated:
  - FWHM values (Gaussian, Lorentzian, pseudo-Voigt)
  - Integral breadths (Gaussian, Lorentzian, pseudo-Voigt)
- Typical runtime: Less than 10 seconds
- Normal warnings: Potential warnings about correlated variables

## Expected Output Files

- `single_peaks_report.txt`: Tab-separated file with 10 columns per peak:
  - position_2theta, intensity, sigma, gamma
  - fwhm_gaussian, fwhm_lorentzian, fwhm_pseudovoigt
  - integral_breadth_gaussian, integral_breadth_lorentzian, integral_breadth_pseudovoigt
- Standard outputs: GPX project file, LST log file, fit_profile.txt

## Schema Features Demonstrated

This example demonstrates single peak fitting capabilities:

- **Top-level single_peaks field**: Direct peak fitting without crystal structure (distinct from background.single_peaks)
- **Independent peak width refinement**: `use_instrument_profile: false` allows sigma and gamma refinement per peak
- **No `phases` block**: No crystal structure used (pure phenomenological peak fitting)
- **Schema name `gsasii.spf`**: the GSAS-II single peak fitting workflow
- **Single peak fitting mode**: `refinement_controls.refinement_cycles` controls iteration count for peak-only refinement
- **Coexisting peak models**: Background single peaks AND top-level single peaks in same recipe
- **Advanced peak metrics**: Calculated FWHM and integral breadths (Gaussian, Lorentzian, pseudo-Voigt)

## Schema

`gsasii.spf` 1.0.0 (core 1.0.0). Every parameter is `[value, refine_flag]`; the
Peak List (`payload.single_peaks`) and the background peaks
(`payload.background.single_peaks`) both state `pv_gaussian_sigma_sq`
(centideg², the value always was σ²) and `pv_lorentzian_gamma` (centideg).
With `use_instrument_profile: false` a refine flag on the instrument's U..Z is
an error (GSAS-II would ignore it silently). Converted from the schema-0.26.0
recipe with `scripts/convert_recipe_026.py` (SH/L 0.0 → 0.002, A91).

## Output Files

This single peak fitting analysis produces:

- **dummy.gpx** - GSAS-II project file (reopenable in GUI)
- **single_peaks_report.txt** - Fitted peak parameters (position, intensity, FWHM, area) for each peak
- **peak_convergence_diagnostics.txt** - Per-peak convergence tracking across refinement cycles
- **fit_profile.txt** - Observed/calculated/background/difference intensities

**Note**: Single peak fitting does not produce `refined_parameters.csv`, human-readable refinement log (.lst), unit cell reports, or peak list reports because there is no structural model - only empirical peak shapes are fitted.


## Known Issues

None.

## Per-engine recipes

`topas/input.json` (`topas.spf`, TOPAS 6) describes the same 36-peak fit in
TOPAS's terms; see `topas/README.md`. Only the GSAS-II recipe has committed
reference output.

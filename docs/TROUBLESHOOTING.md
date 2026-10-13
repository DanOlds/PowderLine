# PowderLine Troubleshooting Guide

Common errors and solutions when working with PowderLine.

## Template File Errors

**Error:**
```
❌ Error: This appears to be a template file (filename or path contains 'template')
Please use one of the real examples in examples/ as a starting point.
```

**Cause:** Trying to run a template file instead of a real refinement example.

**Solution:**
- Copy a real example from `examples/example_LaB6/` as reference
- Rename to remove "template" from filename
- Fill in all required fields with actual values
- Check for placeholder text like `"XXXX"`, `"TODO"`, `"REPLACE_ME"`

**Why it matters:** Templates are incomplete and will fail during refinement. They're meant as documentation, not runnable examples.

---

## Missing Required Fields

**Error:**
```
❌ Recipe validation failed:
1 validation error for RecipeModel
xrd_data
  Field required [type=missing, input_value={...}, input_type=dict]
```

**Cause:** Required field missing from recipe JSON.

**Solution:**
Check that all required fields are present:
- `schema_name` (must be `"GSASII_Rietveld"` or `"GSASII_SPF"`)
- `schema_version` (must be `"0.26.0"`)
- `payload` (contains all recipe fields):
  - `payload.xrd_data` (embedded array object with tth, Itth, Itth_weights)
  - `payload.instrument` (instrument configuration with initialization and optional parameterization)
  - `payload.phases` (required for Rietveld refinement)
  - `payload.background` (optional: background model definition. If omitted, no background is used)

**Example:**
```json
{
  "schema_name": "GSASII_Rietveld",
  "schema_version": "0.26.0",
  "payload": {
    "xrd_data": {
      "tth": [10.0, 10.1, 10.2, 10.3],
      "Itth": [1234.5, 1230.2, 1228.9, 1225.1],
      "Itth_weights": [1.0, 1.0, 1.0, 1.0]
    },
    "instrument": {
      "description": "Debye-Scherrer diffractometer",
      "initialization": [
        {
          "Azimuth": [0.0, 0.0, false],
          "Bank": [1, 1, false],
          "Lam": [0.1665, 0.1665, false],
          "Polariz.": [0.99, 0.99, false],
          "SH/L": [0.0, 0.0, false],
          "Type": ["PXC", "PXC", false],
          "U": [0, 0, false],
          "V": [0, 0, false],
          "W": [0, 0, false],
          "X": [0, 0, false],
          "Y": [0, 0, false],
          "Z": [0.0, 0.0, false],
          "Zero": [0.0, 0.0, false]
        },
        {}
      ]
    },
    "refinement_controls": {
      "refinement_cycles": 5
    },
    "phases": { "...": "..." }
  }
}
```

---

## Schema Version and Migration Errors

Schema **0.25 introduced breaking changes** and the current supported version is
**0.26.0**. Recipes written for schema 0.24 or earlier trigger the validation errors
below. Each entry gives the error message and the immediate fix. For the full
version-by-version migration walkthrough — complete before/after recipes, the migration
checklist, and field-by-field history — see [docs/SCHEMA_HISTORY.md](SCHEMA_HISTORY.md).

**Currently Supported Version:** `"0.26.0"` only.

**Note:** PowderLine validates schema version to ensure recipe compatibility with the current codebase. This prevents subtle bugs from using outdated recipe structures.

### Missing schema_version Field

**Error:**
```
❌ Recipe validation failed:
1 validation error for RecipeModel
schema_version
  Field required [type=missing, input_value={...}, input_type=dict]
```

**Cause:** The required `schema_version` field is missing from the recipe.

**Solution:**
Add `"schema_version": "0.26.0"` at the top level of your recipe JSON:

```javascript
{
  "schema_name": "GSASII_Rietveld",
  "schema_version": "0.26.0",
  "payload": {
    ...
  }
}
```

**Note:** Schema version is now a required field (as of schema 0.25). All recipes must explicitly declare their version.

---

### Schema Version Mismatch

**Error:**
```
❌ Recipe validation failed:
1 validation error for RecipeModel
schema_version
  Value error, Schema version mismatch: recipe uses '0.24', but code expects '0.26.0'.
  See docs/SCHEMA_HISTORY.md for migration guide from older versions.
```

**Cause:** Recipe uses an older or unsupported schema version.

**Solution:** Update to schema 0.26.0 by making the following changes:
- Add `"schema_name"` field (`"GSASII_Rietveld"` or `"GSASII_SPF"`)
- Update `"schema_version"` to `"0.26.0"`
- Wrap all refinement data in a `"payload"` object
- Remove `"sample_name"` and `"recipe_description"` fields
- Remove `"strategy"` from `refinement_controls`; use `schema_name` instead

The per-error sections below cover each of these individually. See
[docs/SCHEMA_HISTORY.md](SCHEMA_HISTORY.md) for the full migration reference.

---

### Missing schema_name Field

**Error:**
```
❌ Recipe validation failed:
1 validation error for RecipeModel
schema_name
  Field required [type=missing, input_value={...}, input_type=dict]
```

**Cause:** Schema 0.25 added the required `schema_name` field at the top level. It does **not** replace `schema_version` — both fields are required.

**Solution:**
Add `schema_name` at the top level (keeping `schema_version`):
```javascript
{
  "schema_name": "GSASII_Rietveld",  // Required: "GSASII_Rietveld" or "GSASII_SPF"
  "schema_version": "0.26.0",
  "payload": {
    "xrd_data": {...},
    ...
  }
}
```

**Schema Options:**
- `"GSASII_Rietveld"` - Full Rietveld refinement with structural phases
- `"GSASII_SPF"` - Single peak fitting without structure

---

### Missing payload Field

**Error:**
```
❌ Recipe validation failed:
1 validation error for RecipeModel
payload
  Field required [type=missing]
```

**Cause:** Schema 0.25 requires all recipe fields to be wrapped in a `payload` object.

**Solution:**
Wrap all recipe fields inside `payload`:
```javascript
// OLD (Schema 0.24):
{
  "schema_version": "0.24",
  "xrd_data": {...},
  "instrument": {...},
  "phases": {...}
}

// NEW (Schema 0.26.0):
{
  "schema_name": "GSASII_Rietveld",
  "schema_version": "0.26.0",
  "payload": {
    "xrd_data": {...},
    "instrument": {...},
    "phases": {...},
    "refinement_controls": {...}  // Now required
  }
}
```

---

### Missing refinement_controls Field

**Error:**
```
❌ Recipe validation failed:
1 validation error for PayloadModel
refinement_controls
  Field required [type=missing, input_value={...}, input_type=dict]
```

**Cause:** Schema 0.25 makes `refinement_controls` required (was optional in 0.24).

**Solution:**
Add `refinement_controls` inside `payload` with `refinement_cycles`:
```javascript
{
  "schema_name": "GSASII_Rietveld",
  "schema_version": "0.26.0",
  "payload": {
    "xrd_data": {...},
    "refinement_controls": {
      "refinement_cycles": 5
    },
    ...
  }
}
```

**Note:** The refinement workflow is determined by `schema_name` (either `"GSASII_Rietveld"` for full Rietveld refinement or `"GSASII_SPF"` for single peak fitting).

---

### Old-Style Recipe (Fields at Top Level)

**Error:**
```
❌ Recipe validation failed:
2 validation errors for RecipeModel
schema_name
  Field required [type=missing, input_value={...}, input_type=dict]
payload
  Field required [type=missing, input_value={...}, input_type=dict]
```

**Cause:** The recipe uses the pre-0.25 flat layout: refinement fields (`xrd_data`, `instrument`, `phases`, ...) sit at the top level instead of inside a `payload` object, and `schema_name` is absent. Note that the schema does **not** reject unknown fields (`RecipeModel` and `PayloadModel` use `extra='allow'` for schema evolution), so the leftover top-level fields themselves produce no error — the failure you actually see is the missing `payload` (and `schema_name`).

**Solution:**
Add `schema_name`, update `schema_version`, and move all recipe fields inside `payload`:
```javascript
// OLD (Schema 0.24) - fields at top level:
{
  "schema_version": "0.24",
  "xrd_data": {...},
  "instrument": {...},
  "phases": {...}
}

// NEW (Schema 0.26.0) - fields inside payload:
{
  "schema_name": "GSASII_Rietveld",
  "schema_version": "0.26.0",
  "payload": {
    "xrd_data": {...},
    "instrument": {...},
    "phases": {...},
    "refinement_controls": {...}
  }
}
```

---

### Removed Fields (`sample_name`, `recipe_description`) Are Silently Ignored

Schema 0.25 removed the `sample_name` and `recipe_description` fields; output files now use fixed names (`dummy.gpx`, `dummy.lst`). Because the schema allows extra fields, a recipe that still contains them validates **without any error** — the fields are simply ignored. Remove them anyway to keep recipes honest.

**Note:** Sample names should be managed externally via directory names (e.g., `example_LaB6/`) or documentation files (e.g., `DESCRIPTION.md`).

**Validation:**
```bash
pixi run kicker --validate-only path/to/input.json
```

For the complete migration example, before/after recipes, and the migration checklist, see [docs/SCHEMA_HISTORY.md](SCHEMA_HISTORY.md).

---

## Asset File Not Found

**This error applies to older file-based schemas only.**

PowderLine uses **embedded data arrays** — XRD data, instrument parameters, and crystal
structures are all embedded directly in the recipe JSON. There are no external `.chi`,
`.instprm`, or CIF files to reference. If you see a path-related error, you are likely
using a recipe from schema 0.24 or earlier.

**Solution:** Migrate to schema 0.26.0. All data should be embedded under the `payload`
key as arrays. See [SCHEMA_HISTORY.md](SCHEMA_HISTORY.md) for migration guidance.

---

## GSAS-II Import Error

**Error:**
```
ModuleNotFoundError: No module named 'GSASII'
```

**Cause:** GSAS-II not installed or not in Python path.

**Solution:**
1. **Check pixi environment is active:**
   ```bash
   pixi shell  # Activate environment
   ```

2. **Verify GSAS-II installation:**
   ```bash
   python -c "import GSASII; print(GSASII.__file__)"
   ```

3. **Always use pixi run commands** (required):
   ```bash
   pixi run kicker input.json  # Automatically activates environment
   ```

4. **Check pixi.toml has the GSAS-II dependency:**
   ```toml
   [dependencies]
   GSAS-II = { git = "https://github.com/tacaswell/GSAS-II", rev = "enh/pixi_pkg" }
   ```

---

## easydiffraction Engine Not Installed

**Error:**
```
ImportError: easydiffraction is not installed. Install PowderLine's optional
engine environment: `pixi install -e easydiffraction` ...
```

**Cause:** `engine="easydiffraction"` was requested but the optional `easydiffraction`
pixi environment is not installed (easydiffraction needs Python ≥3.12, so it
lives in its own environment; the default environment is unaffected).

**Solution:**
```bash
pixi install -e easydiffraction
pixi run -e easydiffraction python your_script.py
```

---

## easydiffraction Rejects My Recipe (EasyDiffractionTranslationError)

**Error:** `EasyDiffractionTranslationError: ... is flagged for refinement but ...`

**Cause:** This is intentional, not a bug. The easydiffraction engine refuses —
rather than silently ignores — refinement flags it cannot represent: atom-level
flags (coordinates, occupancies, ADPs), Kα₁/Kα₂ two-wavelength recipes, and
refined Z / polarization / axial divergence / background peaks. Fixed
(non-refined) unmappable values are dropped with a recorded warning instead.

**Solution:** Set the offending refine flags to `false` (see the message for
which parameter), or run the recipe with `engine="gsasii"`, which supports all
recipe features. `examples/example_LaB6_easydiff/` shows a stock recipe adapted
for this engine.

Related: **Rwp from easydiffraction is much higher than GSAS-II on the same
data.** Some gap is expected — SH/L axial-divergence asymmetry IS modeled (fixed
FCJ via the Thompson-Cox-Hastings profile when SH/L is nonzero), but background
peaks are not, and profile details differ. LaB6 reference: 9.63% vs 6.01%, both
below the nominal noise floor (χ² < 1: integrated-detector weights overestimate
σ). Compare lattice parameters, not Rwp, across engines.

---

## Histogram Creation Failed

**Error:**
```
❌ Failed to create histogram from XRD data:
   [GSAS-II error message]

Check that:
  • XRD data file is in correct format (two-column: 2θ, intensity)
  • Instrument parameter file is compatible with data
  • File paths are correct
```

**Cause:** Incompatible XRD data or instrument configuration.

**Solution:**
1. **Check XRD data format** (embedded arrays in JSON payload):
   ```javascript
   "xrd_data": {
     "tth": [10.0, 10.1, 10.2, ...],
     "Itth": [1234.5, 1230.2, 1228.9, ...],
     "Itth_weights": [1.0, 1.0, 1.0, ...]
   ```

2. **Verify instrument parameterization block** (embedded in JSON payload):
   - The `instrument.initialization` array must be a valid GSAS-II instrument parameter
     structure matching the diffractometer type (PXC for constant-wavelength synchrotron,
     TOF for time-of-flight, etc.)
   - Copy the initialization block from a working example as a starting point:
     `examples/example_LaB6/input.json` (synchrotron, 0.1665 Å) or
     `examples/example_DRX_33/input.json` (synchrotron, multi-phase)
   - Type (TOF vs CW) must match the collected data

3. **Check wavelength compatibility:**
   - Synchrotron data typically 0.1-1.5 Å
   - Lab Cu Kα: ~1.54 Å
   - Neutron: ~1-2 Å

4. **Try with known-good example:**
   ```bash
   pixi run kicker examples/example_LaB6/input.json
   ```
   If this works, problem is in your recipe structure.

---

## Refinement Failed

**Error:**
```
❌ Refinement execution failed:
   [GSAS-II error message]

Possible causes:
  • Invalid parameter values or ranges
  • Too many parameters refined simultaneously
  • Poor initial model
```

**Cause:** Refinement diverged or hit numerical issues.

**Solution:**
1. **Check `dummy.lst` file** for clues:
   ```bash
   cat output/dummy.lst | grep -A 5 "Error"
   ```

2. **Reduce refinement complexity:**
   - Start with fewer refined parameters
   - Fix problematic parameters
   - Example: refine background first, then add scale, then add unit cell

3. **Check parameter correlations** in .lst file:
   ```
   ** Warning: parameters 0 and 1 are 99.8% correlated **
   ```
   - High correlation (>95%) may indicate over-parameterization
   - Try reducing number of background terms
   - Or fix some parameters

4. **Validate input first:**
   ```bash
   pixi run kicker --validate-only input.json
   ```

5. **Try with verbose output:**
   ```bash
   pixi run kicker --verbose input.json
   ```

---

## Output File Permission Error

**Error:**
```
❌ Failed to write output files:
   [Errno 13] Permission denied: 'output/fit_profile.txt'

Check that:
  • Output directory has write permissions
  • Sufficient disk space available
  • Output files are not open in another program
```

**Cause:** Cannot write to output directory.

**Solution:**
1. **Check directory permissions:**
   ```bash
   ls -ld examples/example_LaB6/output/
   chmod 755 examples/example_LaB6/output/
   ```

2. **Check disk space:**
   ```bash
   df -h
   ```

3. **Close files in other programs:**
   - Excel/LibreOffice with CSV files open
   - Text editors with .lst files open

4. **Check output directory exists:**
   - PowderLine creates it automatically, but filesystem errors could prevent this
   - Try creating manually: `mkdir -p examples/example_LaB6/output/`

---

## Pydantic Validation Errors

**Error:**
```
❌ Recipe validation failed:
2 validation errors for RecipeModel
instrument.parameterization.broadening.U
  Input should be a valid list [type=list_type, ...]
phases.0.parameterization.unit_cell.a
  Field required [type=missing, ...]
```

**Cause:** Recipe structure doesn't match Pydantic schema.

**Solution:**
1. **Check error field path:**
   - `instrument.parameterization.broadening.U` means U is in wrong format
   - `phases.0` means first phase (0-indexed)

2. **Verify parameter format is `[value, refine_flag, min, max]`:**
   ```json
   "U": [0.01, true, null, null]  // Correct
   "U": 0.01                       // Wrong - not a list
   "U": [0.01]                     // Wrong - missing elements
   ```

   3. Check nesting matches the payload structure:
   ```javascript
   {
     "schema_name": "GSASII_Rietveld",
     "payload": {
       "refinement_controls": {
         "refinement_cycles": 5
       },
       "instrument": {
         "description": "...",
         "initialization": [{"Type": [...], "Lam": [...], ...}, {}],
         "parameterization": {
           "broadening": {
             "U": [0.01, true, null, null]
           }
         }
       }
     }
   }
   ```

4. **Use validation-only mode to test:**
   ```bash
   pixi run kicker --validate-only input.json
   ```

5. **Reference working example:**
   ```bash
   cat examples/example_LaB6/input.json
   ```

---

## Parameter Correlation Warnings

**Warning in .lst file:**
```
** Warning: parameters 0 and 1 are 99.8% correlated **
```

**Is this a problem?**
Not necessarily! High correlation is common and often acceptable.

**When it's okay:**
- Rwp is good (<15% typically)
- Parameters are physically reasonable
- Refinement converged
- Example: background coefficients are often highly correlated

**When to worry:**
- Correlation >99.9% (essentially identical effects)
- Parameters have unphysical values (negative sizes, unreasonable unit cell)
- Refinement didn't converge
- Large parameter uncertainties

**Solutions if problematic:**
1. **Reduce background terms:**
   ```json
   "num_coefficients": 6  // Try 3-4 instead
   ```

2. **Fix some parameters:**
   ```json
   "U": [0.01, false, null, null]  // Hold fixed
   ```

3. **Expand fit range:**
   ```json
   "fit_range": [5.0, 80.0]  // Include more data
   ```

4. **Refine sequentially:**
   - First refinement: background only
   - Second: background + scale
   - Third: background + scale + instrument

---

## Slow Refinement Performance

**Problem:** Refinements taking 1+ seconds when they should be faster.

**Cause:** Using subprocess mode instead of server mode.

**Solution:**

1. **Check which mode you're using:**
   - Look at the end of the output for "🚀 server mode" or "🐢 subprocess mode"

2. **Start the server for faster performance:**
   ```bash
   pixi run gsas-server start
   pixi run gsas-server status  # Verify it's running
   ```

3. **Force server mode explicitly:**
   ```bash
   pixi run kicker input.json --use-server
   ```

4. **Check server logs if it's not working** — `powderline_gsas_server.log` lives in the platform temp directory (e.g. `/tmp` on Linux, `%TEMP%` on Windows):
   ```bash
   tail -f /tmp/powderline_gsas_server.log   # Linux
   ```

**Performance comparison (post-fix):**
- Server mode: 0.1-0.3s per simulation (10x+ faster)
- Subprocess mode: 1-2s per simulation (GSAS-II loaded every time)

**Note on connection reliability:** The client automatically retries connections up to
3 times with exponential backoff (0.5s, 1s, 2s delays) to handle transient network issues.
If you see "Server communication failed" errors, check the server logs.

---

## Server Not Starting

**Error:**
```
❌ Server mode failed: Server not available and fallback disabled
```

**Cause:** Server process failed to start or port already in use.

**Solution:**

1. **Check if server is already running:**
   ```bash
   pixi run gsas-server status
   ```

2. **Stop any hung server processes:**
   ```bash
   pixi run gsas-server stop
   # Or manually kill:
   pkill -f gsas_server.py
   ```

3. **Check if the port is in use:**
   ```bash
   # See if anything is listening on port 19471:
   ss -tlnp | grep 19471
   ```

4. **Use a different port if 19471 is unavailable:**
   ```bash
   # Set custom port via environment variable
   export POWDERLINE_SERVER_PORT=19472
   pixi run gsas-server start

   # Client will automatically detect the port
   pixi run kicker input.json  # No changes needed
   ```

   The server writes its port to `powderline_gsas_server.port` in the platform temp
   directory (e.g. `/tmp` on Linux, `%TEMP%` on Windows), which the client reads
   automatically. No code changes needed when using a custom port.

5. **Start server in foreground to see errors:**
   ```bash
   pixi shell
   python src/powderline/gsas_server.py start
   # Watch for any error messages
   ```

6. **Use subprocess mode as workaround:**
   ```bash
   pixi run kicker input.json --no-server
   ```

---

## Server Reports Success but Output Files Are Missing

**Problem:** A run completes ("Simulation complete ... via server") but the
expected output files never appear — e.g. `fit_profile.txt not found`, or a
warning that "server output files are not visible to this process".

**Cause:** The GSAS-II server is reachable over localhost but has a
**different filesystem view** than your shell. Two known ways this happens:
the server was started on another node of a cluster, or inside a
sandbox/container with a private `/tmp` (e.g. an AI-agent session). The server
writes its output where only *it* can see it and truthfully reports success.

**Symptoms:**
- The client prints the "different filesystem view" warning and re-runs
  in-process (this automatic fallback is the built-in fix)
- With `execution_mode='server'` (no fallback), a structured error naming the
  filesystem-view mismatch

The check is freshness-based: `fit_profile.txt` must have been (re)written by
the run just submitted, so a stale file from a previous run into the same
output directory does not hide the problem. (mp-simulate's `.chi` export is
immune either way — it is built from the in-band result data.)

**Solution:**

1. **Restart the server from your own shell:**
   ```bash
   pixi run gsas-server restart
   ```
2. **Or skip the server for this run:**
   ```bash
   pixi run kicker input.json --no-server
   pixi run mp-simulate --material-id mp-2680 --no-server --output patterns/
   ```

---

## Environment Issues

**Problem:** Mixing conda environments or system Python.

**Solution:**
Always use pixi for consistency:
```bash
# DON'T mix approaches
conda activate some-env  # DON'T
python src/powderline/kicker.py input.json  # DON'T

# DO use pixi exclusively
pixi run kicker input.json  # DO
pixi run test              # DO
pixi shell                 # DO (then run commands)
```

---

## Single Peak Fitting Issues

### Aphysical Peak Parameters

**Warning in `peak_convergence_diagnostics.txt`:**
```
Peak 5 at 2θ=45.234° - Status: negative_gamma_warning
  sigma_sq: 0.0234, gamma: -0.0012
  Warning: Negative/zero Lorentzian gamma detected
```

**What it means:**
GSAS-II refined a peak to have physically impossible parameters:
- **Negative σ²** (sigma squared / variance) - peak width cannot be negative
- **Negative γ** (Lorentzian gamma / HWHM) - peak width cannot be negative
- **Zero values** - infinitely sharp peak (unphysical)

**Common Causes:**
1. **Poor initial guesses:**
   - Initial position far from true peak
   - Initial intensity much too high/low
   - Initial widths unreasonable

2. **Peak overlap:**
   - Two peaks too close together
   - GSAS-II fitting one peak trying to capture both
   - Results in aphysical parameters for compensation

3. **Background interference:**
   - Peak on steep background slope
   - Background model inadequate
   - Peak confused with background feature

4. **Weak/noisy peaks:**
   - Low signal-to-noise ratio
   - Peak barely above background
   - Refinement unstable

**When to Worry:**
- Multiple peaks flagged (systematic problem)
- Large negative values (< -0.01)
- Rwp poor despite aphysical parameters
- Negative values for well-separated strong peaks

**When it's Okay:**
- Single weak peak flagged
- Very small negative value (≈ -0.001) due to numerical noise
- Rwp good and other peaks converged normally
- Peak at edge of fitted range

**Solutions:**

1. **Improve initial guesses:**
   ```json
   "single_peaks": {
     "positions": [[21.5, true, null, null]],  // Adjust to observed peak
     "intensities": [[150.0, true, null, null]], // Estimate from data
     "pv_gaussian_sigma_sq": [[0.01, true, null, null]], // Reasonable width
     "pv_lorentzian_gamma": [[0.05, true, null, null]]
   }
   ```

2. **Use instrument profile constraints:**
   ```json
   "refinement_controls": {
     "refinement_cycles": 5,
     "single_peak_fitting_mode": {
       "use_instrument_profile": true  // Constrain widths to U,V,W,X,Y,Z
     }
   }
   ```
   This prevents aphysical values by constraining widths to instrument parameters.

3. **Remove problematic peaks:**
   - If peak is poorly resolved, consider removing it
   - Focus on well-separated, strong peaks first
   - Add complex peaks later once background is well-characterized

4. **Improve background model:**
   ```json
   "background": {
     "chebyshev": {
       "num_coefficients": 8  // Try more terms (was 4)
     }
   }
   ```

5. **Adjust refinement cycles:**
   ```json
   "refinement_controls": {
     "refinement_cycles": 10  // More cycles for difficult peaks
   }
   ```
   Additional refinement cycles can help convergence.

**Checking Results:**
- Open `single_peaks_report.txt` - check `converged` column (should be `true`)
- Open `peak_convergence_diagnostics.txt` - lists only problematic peaks
- Inspect `fit_profile.txt` visually - do peaks match observed data?
- Check Rwp - should be reasonable (<15% typically)

---

### Peak Fitting Convergence Failures

**Error:**
```
Peak 10 at 2θ=67.890° - Status: NaN_failed
  sigma_sq: nan, gamma: nan
  Warning: Peak refinement failed - NaN values detected
```

**Cause:** Peak refinement did not converge, produced NaN (Not a Number).

**Common Causes:**
- Initial position outside data range
- Peak at edge of fitted region with poor constraint
- Numerical instability from extreme parameter values
- Insufficient data points around peak

**Solutions:**

1. **Check fit range covers peak:**
   ```json
   "fit_range": [5.0, 90.0]  // Ensure peak at 67.89° is inside
   ```

2. **Check data quality near peak:**
   - Are there actual data points at that 2θ?
   - Is signal-to-noise adequate?
   - Visual inspection of XRD pattern

3. **Remove edge peaks:**
   - Peaks within 1-2° of fit range boundaries often problematic
   - Focus on central region peaks first

4. **Adjust initial parameters:**
   ```json
   // Move initial position to visible peak maximum
   "positions": [[67.5, true, null, null]]  // Was 67.89, try 67.5
   ```

5. **Increase refinement cycles:**
   ```json
   "refinement_controls": {
     "refinement_cycles": 10  // Try more cycles (was 5)
   }
   ```

---

### Schema Selection Guidance

**Question:** Which schema should I use for my refinement?

**Decision Tree:**

1. **Do you have crystal structure information (embedded structure dict with space_group, unit_cell, atoms)?**
   - **No** → Use `"schema_name": "GSASII_SPF"` (Single Peak Fitting)
   - **Yes** → Use `"schema_name": "GSASII_Rietveld"` (Full Rietveld refinement)

**Schema Options:**

| Schema Name | Purpose | Requirements | Output |
|-------------|---------|--------------|--------|
| `GSASII_Rietveld` | Full structural refinement | Crystal structure (embedded structure dict), XRD pattern | Refined unit cell, atomic positions, fit statistics, peak lists |
| `GSASII_SPF` | Individual peak fitting | XRD pattern, peak positions | Peak positions, widths, intensities (no structure) |

**When to Use Each Schema:**

**Use `GSASII_Rietveld` when:**
- You know the crystal structure (have embedded structural parameters)
- You want to refine unit cell parameters, atomic positions, or thermal parameters
- You need quantitative phase analysis
- You want to extract reflection lists (hkl indices)
- Traditional Rietveld refinement is your goal

**Use `GSASII_SPF` when:**
- Crystal structure is unknown or not relevant
- You only need peak positions, widths, and intensities
- Performing peak indexing as preliminary step
- Characterizing peak broadening or strain
- No structural model is available

**Example Recipes:**

```javascript
// Rietveld refinement (structural analysis)
{
  "schema_name": "GSASII_Rietveld",
  "schema_version": "0.26.0",
  "payload": {
    "xrd_data": {...},
    "instrument": {...},
    "phases": {...},  // Required for Rietveld
    "background": {...},
    "refinement_controls": {
      "refinement_cycles": 5
    }
  }
}

// Single peak fitting (no structure)
{
  "schema_name": "GSASII_SPF",
  "schema_version": "0.26.0",
  "payload": {
    "xrd_data": {...},
    "instrument": {...},
    "fit_range": [10.0, 90.0],
    "background": {...},
    "single_peaks": {
      "positions": [[20.5, true, null, null], [35.2, true, null, null]],
      "intensities": [[1000, true, null, null], [500, true, null, null]],
      "pv_gaussian_sigma_sq": [[0.1, true, null, null], [0.1, true, null, null]],
      "pv_lorentzian_gamma": [[10, true, null, null], [10, true, null, null]]
    },
    "refinement_controls": {
      "refinement_cycles": 5,
      "single_peak_fitting_mode": {
        "use_instrument_profile": false
      }
    }
  }
}
```

**Important Notes:**
- **Sequential/iterative strategies removed:** Schema 0.25 removed the sequential and iterative workflow options (not planned for future development). Each schema performs a single refinement type.
- **No workflow customization:** The `strategy` field no longer exists. Refinement behavior is entirely determined by `schema_name`.
- **Separate runs for multi-stage workflows:** If you need peak fitting followed by Rietveld refinement, run two separate PowderLine jobs (one with `GSASII_SPF`, then one with `GSASII_Rietveld`).

---

## For contributors

The material in this section is for people working **on** PowderLine — modifying its
source, updating regression references, or running the development server — not for people
who are only running refinements. If you are just using PowderLine, everything you need is
in the sections above.

### PowderLine Module Import Error

**Error:**
```
ModuleNotFoundError: No module named 'powderline.schema'
```

**Cause:** PYTHONPATH doesn't include `src/` directory.

**Solution:**
1. **Use pixi run commands** (automatically sets PYTHONPATH):
   ```bash
   pixi run kicker input.json
   ```

2. **Or set PYTHONPATH manually:**
   ```bash
   export PYTHONPATH=$PWD/src:$PYTHONPATH
   python src/powderline/kicker.py input.json
   ```

3. **Check pixi.toml kicker task has PYTHONPATH:**
   ```toml
   [tasks]
   kicker = { cmd = "python src/powderline/kicker.py", env = { PYTHONPATH = "$PWD/src:$PYTHONPATH" } }
   ```

---

### Test Failures

**Error:**
```
FAILED tests/test_example_LaB6_regression.py::test_example_LaB6_regression
AssertionError: Final Rwp mismatch: expected 6.502%, got 6.505%
```

**Cause:** Refinement results changed from reference.

**Solution:**
1. **Determine if change is expected:**
   - Did you modify refinement parameters?
   - Did GSAS-II version change?
   - Is difference significant (>0.1% Rwp)?

2. **Check `dummy.lst` for details:**
   ```bash
   diff output/dummy.lst examples/example_LaB6/output/dummy.lst
   ```

3. **Update reference if change is intentional:**
   ```bash
   # Run refinement
   pixi run kicker examples/example_LaB6/input.json

   # Copy new outputs as reference
   cp output/* examples/example_LaB6/output/

   # Commit updated references
   git add examples/example_LaB6/output/
   git commit -m "Update example_LaB6 reference after <reason>"
   ```

4. **If unexpected, investigate:**
   - Check for GSAS-II updates: `pixi list | grep gsas-ii`
   - Look for code changes that might affect refinement
   - Compare parameter values in .lst files

---

### Stale Server (Old Code Running)

**Problem:** Tests fail or code changes don't take effect, but code looks correct.

**Cause:** A long-running server started before your code changes still has the old code loaded in memory. Python caches imported modules for performance - the server doesn't automatically reload code when files change.

**Symptoms:**
- Tests pass right after a server restart (or a `pixi run kicker ... --no-server` run works) but fail against a long-running server
- Code changes don't seem to have any effect
- Errors mention bugs you've already fixed

**Solution:**

1. **Kill all running servers:**
   ```bash
   pkill -9 -f "gsas_server|powderline.*server"
   ```

2. **Verify no servers are running:**
   ```bash
   ps aux | grep -E "gsas|powderline" | grep -v grep
   # Should show no results
   ```

3. **Run tests again:**
   ```bash
   pixi run test
   # Or for specific tests:
   pixi run pytest tests/test_schema.py -v
   ```

**Best Practices:**

- **During development:** Restart the server after code changes (`pixi run gsas-server restart`), or use the kicker CLI's `--no-server` flag
- **For integration tests:** Restart (or stop) the server before running the test suite to ensure deterministic behavior
- **Production use:** Server caching is beneficial (4-6x speedup) - just restart after deployments

**Why this happens:**

When the server starts, it imports `kicker.py` and GSAS-II libraries into memory. These stay loaded for performance (first refinement ~12s, subsequent ~2-3s). If you edit the source code, the server still has the old version in Python's `sys.modules` cache. The server would need to be restarted to pick up changes.

**Development workflow:**

```bash
# Make code changes...
vim src/powderline/kicker.py

# Kill old server
pkill -f gsas_server

# Tests will start fresh server or use subprocess mode
pixi run test
```

---

## Getting More Help

1. **Enable verbose output:**
   ```bash
   pixi run kicker --verbose input.json
   ```

2. **Check GSAS-II log** in the `.lst` file (PowderLine uses a fixed filename):
   ```bash
   cat output/dummy.lst
   ```

3. **Validate before running:**
   ```bash
   pixi run kicker --validate-only input.json
   ```

4. **Compare with working example:**
   ```bash
   diff -u examples/example_LaB6/input.json my_example/input.json
   ```

5. **Open an issue on GitHub** with:
   - Error message (full output)
   - Recipe JSON file
   - PowderLine commit: `git log --oneline -1`
   - GSAS-II version: `pixi list | grep gsas-ii`

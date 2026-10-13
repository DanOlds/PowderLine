# Simulating Patterns from Materials Project

## Overview

The `mp-simulate` tool fetches crystal structures from the Materials Project database and generates simulated powder diffraction patterns. It builds a `gsasii.rietveld` recipe (schema 1.0.0, a simulation: `refinement_cycles: 1`, nothing refined) and runs it through **GSAS-II**, so it lives in the GSAS-II gateway and its pixi task in the `gsasii` environment (`pixi run -e gsasii mp-simulate ...`). An engine-agnostic simulation tool with its own configuration is a future item.

## Requirements

1. **Materials Project API Key**: Free registration at https://next-gen.materialsproject.org/api
2. **Configuration**: Copy `.powderline_config.yaml.example` to `.powderline_config.yaml` and add your API key, or set the `MP_API_KEY` environment variable (the config file takes precedence)

## Quick Start

### Setup Config File

```bash
# Copy example config
cp .powderline_config.yaml.example .powderline_config.yaml

# Edit and add your API key
nano .powderline_config.yaml
```

### Generate Patterns

```bash
# By Materials Project ID
pixi run -e gsasii mp-simulate --material-id mp-2680 --output patterns/

# By chemical formula: lists all matching polymorphs and simulates the most
# stable one (lowest energy above hull)
pixi run -e gsasii mp-simulate --formula LaB6 --output patterns/

# With custom wavelength
pixi run -e gsasii mp-simulate --material-id mp-2680 --wavelength 1.54 --output patterns/

# Keep intermediate files for inspection
pixi run -e gsasii mp-simulate --material-id mp-2680 --keep-recipe --keep-output --output patterns/
```

## Output Files

After running `mp-simulate`, you'll get:

- **`{material_id}_{formula}_simulated.chi`**: Simulated diffraction pattern (2θ vs intensity)
- **`{material_id}_{formula}_recipe.json`** (if `--keep-recipe`): the full `gsasii.rietveld` recipe used (validates as written; every default stated, canonical space-group name, the conventional cell reduced to its symmetry-unique sites, two-origin groups in origin choice 2)
- **`{material_id}_{formula}_full_output/`** (if `--keep-output`): Complete GSAS-II output

## Example: LaB6 (mp-2680)

```bash
pixi run -e gsasii mp-simulate --material-id mp-2680 --output patterns/
```

**Output:**
- Material: LaB6 (Lanthanum Hexaboride)
- Space group: Pm-3m
- Crystal system: Cubic
- Wavelength: 0.4133 Å (30 keV synchrotron, default)

## Example: Al2O3 (Corundum)

```bash
pixi run -e gsasii mp-simulate --material-id mp-1143 --output patterns/
```

**Output:**
- Material: Al2O3 (Corundum)
- Space group: R-3c
- Crystal system: Trigonal
- Wavelength: 0.4133 Å (default)

## Customization

### Override Wavelength

Default is 30 keV synchrotron (0.4133 Å). Common alternatives:

```bash
# Cu Kα lab source (1.54056 Å)
pixi run -e gsasii mp-simulate --material-id mp-2680 --wavelength 1.54056 --output output/

# Mo Kα lab source (0.7107 Å)
pixi run -e gsasii mp-simulate --material-id mp-2680 --wavelength 0.7107 --output output/

# 15 keV synchrotron (0.8266 Å)
pixi run -e gsasii mp-simulate --material-id mp-2680 --wavelength 0.8266 --output output/
```

### Modify Simulation Parameters

Simulations run through the **GSAS-II engine** — all config parameters follow
GSAS-II conventions and units (not TOPAS, which differs for polarization,
axial divergence, and instrumental broadening). Edit `.powderline_config.yaml`
to change:

- `instrument_defaults`: wavelength (Å), polarization, zero shift (centideg
  2θ), axial divergence (SH/L; at least 0.002, GSAS-II's own floor — the
  default), TCH instrumental broadening (U–Z, GSAS-II units)
- `phase_defaults`: scale; per-phase peak broadening — model, crystallite
  size (**microns**), microstrain (**Δd/d × 10⁻⁶**), and the LG mixing term
  (`LG_eta`, 1 = Lorentzian, 0 = Gaussian) for size and strain independently;
  plus fallback Uiso (Å²) and occupancy, applied **only** when the structure
  source doesn't provide them (a warning names the affected atoms)
- `data_range`: 2θ range and step size
- `background`: Chebyshev coefficients

See `.powderline_config.yaml.example` for the full annotated layout.

## Troubleshooting

### API Key Not Found

```
❌ No Materials Project API key found!
```

**Solution**: 
1. Get API key: https://next-gen.materialsproject.org/api
2. Add to `.powderline_config.yaml` or set the `MP_API_KEY` environment variable
3. Keep the config file private (it's in .gitignore)

### Material Not Found

```
❌ Material not found: mp-XXXX
```

**Solution**: Verify material ID on Materials Project website. Try searching by formula instead:
```bash
pixi run -e gsasii mp-simulate --formula LaB6 --output output/
```

### Server Reported Success but No Pattern Was Saved

A warning that the server "did not write output files visible to this
process" (the `.chi` itself is exported from the in-band result data, so it
is still produced — but `--keep-output` files may be missing).

**Cause**: the persistent GSAS-II server is reachable over localhost but has a
different filesystem view (it was started on another node, or inside a
sandbox/container with a private `/tmp`). The client now detects this, warns,
and automatically re-runs in-process.

**Solution**: restart the server from your own shell, or skip it entirely:
```bash
pixi run -e gsasii gsas-server restart
pixi run -e gsasii mp-simulate --material-id mp-2680 --no-server --output output/
```

### Simulation Failed

```
❌ Simulation failed!
```

**Solution**: Run with `--verbose` to see detailed GSAS-II output:
```bash
pixi run -e gsasii mp-simulate --material-id mp-2680 --verbose --output output/
```

Common causes:
- Invalid structure data from MP
- A space-group setting GSAS-II cannot use (the recipe builder states origin
  choice 2 and the canonical name; a setting GSAS-II refuses is reported at
  validation)
- A structure whose listed atoms do not reproduce the symmetry orbits (the
  builder refuses it rather than emit a wrong pattern)
- A configured value the gsasii schema rejects (e.g. SH/L below 0.002)

### Disordered Structures

Structures with partial site occupancies (e.g., solid solutions) are
supported: occupancies from Materials Project are carried into the simulation
and a note is printed when the fetched structure is disordered.

## Notes

- **Deterministic output**: Simulation mode (`refinement_cycles: 1`) with all parameters locked produces identical patterns every run; the result says `simulation_mode: true`, and its `rwp` (against the placeholder observations) means nothing
- **Conventional cell**: fetched structures are standardized to the conventional unit cell so the space-group setting matches the emitted lattice parameters
- **No experimental data**: These are calculated patterns only, not fits to observed data
- **Instrument simulation**: Default parameters simulate high-resolution synchrotron data
- **CHI format**: Standard 2-column ASCII format compatible with most XRD software

## Citation

If you use Materials Project data, please cite:

> A. Jain et al., "Commentary: The Materials Project: A materials genome approach to accelerating materials innovation", 
> APL Materials 1, 011002 (2013). DOI: 10.1063/1.4812323

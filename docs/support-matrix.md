# Schema and engine support matrix

<!-- DRAFT (re/10 workstream D). Model: master plan A40 (compatibility is
     declared, never inferred from version arithmetic), A65 (declarations as
     capabilities() keys; support_matrix() in powderline.compat), A44 (engine
     versions pinned and checked at run time), A144/A145 (TOPAS declares its
     version in the recipe; the installed version is declared by the user),
     A157 B2 (easydiffraction recipes carry no engine version), A110 (gemmi
     pinned exactly). Source code: src/powderline/compat.py (module docstring,
     support_matrix()). -->

## How compatibility is decided

PowderLine never works out from version numbers whether a recipe is
acceptable. Every acceptance is **declared** in code, and this page is
generated from those declarations (A40, A65):

- **Core** (`powderline.schema_core`) declares `CORE_SCHEMA_VERSION`, the
  version new recipes are written against, and `SUPPORTED_CORE_SCHEMAS`, a
  [PEP 440](https://peps.python.org/pep-0440/#version-specifiers) specifier
  of the core-schema versions this release accepts.
- **Each gateway** declares, as keys of its `capabilities()`:
  `engine_schema_version` (what new recipes for that engine are written
  against), `supported_engine_schemas` (the engine-schema versions it
  accepts) and `requires_core_schema` (the core versions that engine schema
  needs — a single specifier, or a mapping when different engine-schema
  versions need different core versions).
- A gateway that declares none of these is listed as **undeclared** and its
  recipes are rejected; a gateway that declares only some of them is a
  configuration error, raised by name.

`validate()` checks a recipe's `core_schema_version` against core's
declaration, its `engine_schema_version` against the gateway's, and the pair
against `requires_core_schema`, in that order; the error names the versions
and the PowderLine release that produced it. Widening a declaration is a
deliberate act with a compatibility run behind it, never a range
(the subplan's `">=1.0,<2"` was superseded by `==1.0.0`, A81).

**Engine versions** are declared and checked differently per engine:

| Engine | How the version is fixed | Checked |
|---|---|---|
| GSAS-II | pinned by pixi; the gateway declares `engine_version_spec` | at run time against the installed package (A44) |
| easydiffraction | pinned by pixi (`==0.21.1`); recipes carry no engine version (A157 B2) | at run time against the installed package (A44) |
| TOPAS | cannot be pinned: a `topas.*` recipe states `engine_version` (`"6"`; 1.0.0 supports TOPAS 6 only, `supported_engine_versions == "==6"`, A143/A144) | at run time against the version **the user declares** (`topas_version` argument or `.powderline_config.yaml` `topas.version`), since `tc.exe` carries no version PowderLine could read (A145) |

gemmi, which provides the canonical space-group names core validates against,
is pinned exactly (`==0.7.5`); widening it means re-running the symmetry
tests and regenerating `tests/data/canonical_space_groups.txt` (A110).

## The matrix

Regenerate with (from the repository root, in an environment where the
gateways are installed; the call imports no engine):

```bash
python -c "import powderline, json; print(json.dumps(powderline.support_matrix(), indent=1))"
```

<!-- TODO re/10: replace the snapshot below with the output at the release
     commit (after re/07 removes the GSASII_* workflow names and re/08 sets
     the package version), via a build-time script so the table cannot drift. -->

**Snapshot** at commit `f87575d` (branch `refactor/multi-engine`, package
version still `0.1.1`), run 2026-10-13. Two things in it are transitional and
disappear in re/07: the 0.26.0 workflow names (`GSASII_Rietveld`,
`GSASII_SPF`) still served by every gateway, and the TOPAS
`engine_version_spec` `>=7,<8`, which belongs to the 0.26.0 translation path
(the native `topas.*` schema's TOPAS 6 declaration is a schema-module
constant, not yet a matrix column).

| Gateway | PowderLine | Core schema (current / accepted) | Engine schema (current / accepted) | Requires core | Engine version | Workflows |
|---|---|---|---|---|---|---|
| `gsasii` | 0.1.1 | 1.0.0 / `==1.0.0` | 1.0.0 / `==1.0.0` | `==1.0.0` | `==5.7.9` | `gsasii.rietveld`, `gsasii.spf` (+ `GSASII_Rietveld`, `GSASII_SPF` until re/07) |
| `topas` | 0.1.1 | 1.0.0 / `==1.0.0` | 1.0.0 / `==1.0.0` | `==1.0.0` | `>=7,<8` (0.26.0 path; native: TOPAS `==6`) | `topas.rietveld`, `topas.spf` (+ `GSASII_Rietveld`, `GSASII_SPF` until re/07) |
| `easydiffraction` | 0.1.1 | 1.0.0 / `==1.0.0` | 1.0.0 / `==1.0.0` | `==1.0.0` | `==0.21.1` | `easydiffraction.rietveld` (+ `GSASII_Rietveld` until re/07) |

All three gateways are `declared: true`.

Raw output of the command above (the fields of each row are exactly the
`support_matrix()` keys):

```json
[
 {
  "gateway": "easydiffraction",
  "powderline_version": "0.1.1",
  "core_schema_version": "1.0.0",
  "supported_core_schemas": "==1.0.0",
  "workflows": ["easydiffraction.rietveld", "GSASII_Rietveld"],
  "engine_version_spec": "==0.21.1",
  "declared": true,
  "engine_schema_version": "1.0.0",
  "supported_engine_schemas": "==1.0.0",
  "requires_core_schema": "==1.0.0"
 },
 {
  "gateway": "gsasii",
  "powderline_version": "0.1.1",
  "core_schema_version": "1.0.0",
  "supported_core_schemas": "==1.0.0",
  "workflows": ["gsasii.rietveld", "gsasii.spf", "GSASII_Rietveld", "GSASII_SPF"],
  "engine_version_spec": "==5.7.9",
  "declared": true,
  "engine_schema_version": "1.0.0",
  "supported_engine_schemas": "==1.0.0",
  "requires_core_schema": "==1.0.0"
 },
 {
  "gateway": "topas",
  "powderline_version": "0.1.1",
  "core_schema_version": "1.0.0",
  "supported_core_schemas": "==1.0.0",
  "workflows": ["topas.rietveld", "topas.spf", "GSASII_Rietveld", "GSASII_SPF"],
  "engine_version_spec": ">=7,<8",
  "declared": true,
  "engine_schema_version": "1.0.0",
  "supported_engine_schemas": "==1.0.0",
  "requires_core_schema": "==1.0.0"
 }
]
```

## Reading the matrix

- A recipe with `core_schema_version: "1.0.0"` and
  `engine_schema_version: "1.0.0"` validates on every gateway listed.
- A recipe stating any other version pair is rejected with a message naming
  the accepted specifiers — even `1.0.1`, until a release declares it.
- Which engine schema is *meant* is read from `schema_name`
  (`<engine>.<workflow>`), never from the versions.
- The schema changelog, [SCHEMA_HISTORY](SCHEMA_HISTORY.md), records every
  declaration change; the package changelog, [CHANGELOG](changelog.md),
  records the release that made it.

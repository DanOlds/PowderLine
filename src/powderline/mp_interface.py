"""Materials Project API interface for fetching crystal structures.

This module provides a wrapper around the mp-api client for retrieving
crystal structure data from the Materials Project database.
"""

from typing import Any, Dict, List, Optional
from pathlib import Path

from ._status import CHECK

try:
    from mp_api.client import MPRester
    try:
        from mp_api.client.core.exceptions import MPRestError
    except ImportError:
        # MPRestError has lived in different modules across mp-api releases
        from mp_api.client.core.client import MPRestError
    from pymatgen.symmetry.analyzer import SpacegroupAnalyzer
    MP_API_AVAILABLE = True
except ImportError:
    MP_API_AVAILABLE = False
    MPRester = None
    MPRestError = None
    SpacegroupAnalyzer = None


class MPInterfaceError(Exception):
    """Base error for Materials Project interface failures."""


class MPAuthError(MPInterfaceError):
    """Missing, malformed, or rejected Materials Project API key."""


class MPNotFoundError(MPInterfaceError):
    """No Materials Project entry matched the query."""


class MPConnectionError(MPInterfaceError):
    """Network failure while talking to the Materials Project API."""


def _map_error(e: Exception, context: str) -> MPInterfaceError:
    """Translate mp-api/requests exceptions into the MPInterfaceError taxonomy."""
    if isinstance(e, MPInterfaceError):
        return e
    try:
        import requests
        if isinstance(e, (requests.exceptions.ConnectionError,
                          requests.exceptions.Timeout)):
            return MPConnectionError(f"{context}: network error: {e}")
    except ImportError:
        pass
    if MPRestError is not None and isinstance(e, MPRestError):
        msg = str(e)
        if '401' in msg or '403' in msg or 'api key' in msg.lower():
            return MPAuthError(
                f"{context}: authentication rejected by the Materials Project "
                f"API — check your API key ({msg})"
            )
        return MPInterfaceError(f"{context}: {msg}")
    return MPInterfaceError(f"{context}: {e}")


class MPInterface:
    """Interface to Materials Project API for structure retrieval.

    Can be used as a context manager to ensure the underlying HTTP session
    is closed::

        with MPInterface(api_key=key) as mp:
            data = mp.get_structure("mp-2680")
    """

    # Fields requested from the summary endpoint for a structure fetch
    STRUCTURE_FIELDS = [
        "material_id", "formula_pretty", "structure", "symmetry",
        "energy_above_hull", "theoretical", "nelements",
    ]
    # Fields requested for a formula search
    SEARCH_FIELDS = [
        "material_id", "formula_pretty", "symmetry",
        "energy_above_hull", "theoretical",
    ]

    def __init__(self, api_key: Optional[str] = None):
        """Initialize Materials Project interface.

        Args:
            api_key: Materials Project API key (required). Key resolution
                    from .powderline_config.yaml or the MP_API_KEY
                    environment variable is handled by ConfigLoader.

        Raises:
            ImportError: If mp-api is not installed
            MPAuthError: If no API key provided or it looks invalid
        """
        if not MP_API_AVAILABLE:
            raise ImportError(
                "mp-api not installed. Install with: pixi install -e gsasii"
            )

        if not api_key:
            raise MPAuthError(
                "Materials Project API key required. "
                "Set in .powderline_config.yaml, the MP_API_KEY environment "
                "variable, or pass to constructor."
            )

        # Check for placeholder value
        if api_key == "YOUR_API_KEY_HERE":
            raise MPAuthError(
                "API key is still placeholder value. "
                "Replace YOUR_API_KEY_HERE with your actual API key from "
                "https://next-gen.materialsproject.org/api"
            )

        # Basic format check (next-gen MP API keys are 32-char strings)
        if len(api_key) < 20:
            raise MPAuthError(
                f"API key appears invalid (too short: {len(api_key)} chars). "
                "Next-gen Materials Project API keys are 32 characters; get "
                "one at https://next-gen.materialsproject.org/api"
            )

        self.api_key = api_key
        self.client = MPRester(api_key)

    def close(self) -> None:
        """Close the underlying HTTP session (best-effort)."""
        try:
            self.client.session.close()
        except AttributeError:
            pass

    def __enter__(self) -> "MPInterface":
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> bool:
        self.close()
        return False

    def get_structure(self, material_id: str) -> Dict[str, Any]:
        """Fetch structure data for a material from Materials Project.

        The structure returned by MP is the final computed cell, which is
        often a primitive setting; it is standardized to the conventional
        cell here so that the reported space-group symbol matches the
        emitted lattice parameters and fractional coordinates.

        Args:
            material_id: Materials Project ID (e.g., 'mp-1234' or '1234')

        Returns:
            Dictionary containing structure information:
                - material_id: canonical ID string from the database (e.g.
                  'mp-1234'; can differ from the requested ID when an entry
                  has been merged/deprecated)
                - cif: CIF string of the conventional cell
                - formula: reduced chemical formula
                - space_group: H-M symbol for the conventional cell
                - unit_cell: conventional cell parameters (a..gamma, volume)
                - sites: list of per-species site dicts
                  {element, x, y, z, occupancy, label}; disordered sites
                  produce one entry per co-occupying species
                - is_ordered: False when any site has partial occupancy
                - metadata: space_group_number, crystal_system, nelements,
                  num_atoms, energy_above_hull, theoretical, plus
                  material_id/formula/space_group duplicated for convenience

        Raises:
            MPNotFoundError: If material not found
            MPAuthError: If the API rejects the key
            MPConnectionError: On network failure
            MPInterfaceError: On any other API/processing failure
        """
        material_id = str(material_id)
        if not material_id.startswith('mp-'):
            # Try to add prefix if user forgot
            material_id = f'mp-{material_id}'

        try:
            docs = self.client.materials.summary.search(
                material_ids=[material_id],
                fields=self.STRUCTURE_FIELDS,
            )

            if not docs:
                raise MPNotFoundError(f"Material not found: {material_id}")

            doc = docs[0]

            # The database's canonical ID can differ from the requested one
            # when an entry has been merged/deprecated; report what we got.
            material_id = str(getattr(doc, 'material_id', material_id))

            # Standardize to the conventional cell so the space-group symbol
            # and the emitted cell agree (MP structures are often primitive).
            # symprec=0.1 is the tolerance MP itself uses for its displayed
            # symmetry (absorbs DFT relaxation noise; pymatgen's stricter
            # 0.01 default can report a subgroup for near-symmetric cells).
            conventional = SpacegroupAnalyzer(
                doc.structure, symprec=0.1
            ).get_conventional_standard_structure()
            space_group, space_group_number = conventional.get_space_group_info()
            # spglib writes screw axes with underscores (I4_1/amd); GSAS-II's
            # StandardizeSpcName needs them stripped (I41/amd)
            space_group = space_group.replace('_', '')

            cif_string = conventional.to(fmt="cif")
            formula = doc.formula_pretty

            lattice = conventional.lattice
            unit_cell = {
                'a': lattice.a,
                'b': lattice.b,
                'c': lattice.c,
                'alpha': lattice.alpha,
                'beta': lattice.beta,
                'gamma': lattice.gamma,
                'volume': lattice.volume
            }

            # One entry per species at each site; partial occupancies carried
            # through. Labels are per-element counters (unique even for
            # co-occupied sites: Fe1 and Ni1 at the same coordinates).
            sites: List[Dict[str, Any]] = []
            element_counts: Dict[str, int] = {}
            for site in conventional.sites:
                for species, occupancy in site.species.items():
                    element = species.symbol
                    element_counts[element] = element_counts.get(element, 0) + 1
                    sites.append({
                        'element': element,
                        'x': float(site.frac_coords[0]),
                        'y': float(site.frac_coords[1]),
                        'z': float(site.frac_coords[2]),
                        'occupancy': float(occupancy),
                        'label': f"{element}{element_counts[element]}"
                    })

            symmetry = getattr(doc, 'symmetry', None)
            crystal_system = (
                str(symmetry.crystal_system).lower()
                if symmetry is not None and symmetry.crystal_system is not None
                else None
            )
            energy_above_hull = getattr(doc, 'energy_above_hull', None)
            theoretical = getattr(doc, 'theoretical', None)
            nelements = getattr(doc, 'nelements', None)

            return {
                'material_id': material_id,
                'cif': cif_string,
                'formula': formula,
                'space_group': space_group,
                'unit_cell': unit_cell,
                'sites': sites,
                'is_ordered': conventional.is_ordered,
                'metadata': {
                    'material_id': material_id,
                    'formula': formula,
                    'space_group': space_group,
                    'space_group_number': space_group_number,
                    'crystal_system': crystal_system,
                    'nelements': nelements,
                    'num_atoms': conventional.composition.num_atoms,
                    'energy_above_hull': (
                        float(energy_above_hull)
                        if energy_above_hull is not None else None
                    ),
                    'theoretical': theoretical,
                }
            }

        except MPInterfaceError:
            raise
        except Exception as e:
            raise _map_error(
                e, f"Failed to fetch structure for {material_id}"
            ) from e

    def search_by_formula(self, formula: str) -> List[Dict[str, Any]]:
        """Search Materials Project by chemical formula.

        Args:
            formula: Chemical formula (e.g., 'LaB6', 'Al2O3')

        Returns:
            List of dictionaries with material_id (str), formula,
            space_group, energy_above_hull (eV/atom, may be None), and
            theoretical, sorted by energy_above_hull ascending (None last) —
            the first entry is the most stable polymorph.

        Raises:
            MPAuthError: If the API rejects the key
            MPConnectionError: On network failure
            MPInterfaceError: On any other API failure
        """
        try:
            docs = self.client.materials.summary.search(
                formula=formula,
                fields=self.SEARCH_FIELDS,
            )

            results = []
            for doc in docs:
                symmetry = getattr(doc, 'symmetry', None)
                e_hull = getattr(doc, 'energy_above_hull', None)
                results.append({
                    'material_id': str(doc.material_id),
                    'formula': doc.formula_pretty,
                    'space_group': (
                        str(symmetry.symbol)
                        if symmetry is not None and symmetry.symbol is not None
                        else None
                    ),
                    'energy_above_hull': (
                        float(e_hull) if e_hull is not None else None
                    ),
                    'theoretical': getattr(doc, 'theoretical', None),
                })

            results.sort(key=lambda r: (
                r['energy_above_hull'] is None,
                r['energy_above_hull'] if r['energy_above_hull'] is not None
                else 0.0
            ))
            return results

        except MPInterfaceError:
            raise
        except Exception as e:
            raise _map_error(
                e, f"Search failed for formula '{formula}'"
            ) from e

    def save_structure_to_cif(self, material_id: str, output_path: Path) -> None:
        """Fetch structure and save as CIF file.

        Args:
            material_id: Materials Project ID
            output_path: Path to save CIF file

        Raises:
            Same taxonomy as get_structure (MPNotFoundError, MPAuthError,
            MPConnectionError, MPInterfaceError).
        """
        structure_data = self.get_structure(material_id)

        output_path = Path(output_path)
        output_path.parent.mkdir(parents=True, exist_ok=True)

        with open(output_path, 'w', encoding='utf-8') as f:
            f.write(structure_data['cif'])

        print(f"{CHECK} Saved CIF to: {output_path}")

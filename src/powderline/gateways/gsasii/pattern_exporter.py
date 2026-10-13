"""Export simulated diffraction patterns to standard formats.

This module extracts calculated intensities from GSAS-II output and
exports them to formats like CHI (2-column ASCII).
"""

from pathlib import Path
import pandas as pd
import numpy as np
from typing import Optional

from powderline._status import CHECK


class PatternExporter:
    """Export simulated patterns from PowderLine/GSAS-II output."""
    
    @staticmethod
    def extract_calculated_pattern(fit_profile_path: Path) -> tuple[np.ndarray, np.ndarray]:
        """Extract calculated pattern (y_calc) from fit_profile.txt.
        
        Args:
            fit_profile_path: Path to fit_profile.txt from PowderLine output
        
        Returns:
            Tuple of (two_theta, y_calc) arrays
        
        Raises:
            FileNotFoundError: If fit_profile.txt doesn't exist
            ValueError: If file format is incorrect
        """
        if not fit_profile_path.exists():
            raise FileNotFoundError(f"fit_profile.txt not found: {fit_profile_path}")
        
        try:
            df = pd.read_csv(fit_profile_path, sep='\t')
            
            # Extract two_theta and y_calc columns
            two_theta = df['two_theta'].values
            y_calc = df['y_calc'].values
            
            return two_theta, y_calc
        
        except Exception as e:
            raise ValueError(f"Failed to parse fit_profile.txt: {e}")

    @staticmethod
    def extract_calculated_pattern_from_data(
        fit_profile_data: dict
    ) -> tuple[np.ndarray, np.ndarray]:
        """Extract calculated pattern from an in-band fit_profile dict.

        This is the column-oriented dict returned by
        ``GSASClient.submit_simulation`` / ``powderline.run`` under the
        ``fit_profile`` key — no filesystem access needed.

        Args:
            fit_profile_data: dict with at least 'two_theta' and 'y_calc'
                column lists

        Returns:
            Tuple of (two_theta, y_calc) arrays

        Raises:
            ValueError: If the dict is empty or missing required columns
        """
        if not fit_profile_data:
            raise ValueError(
                "fit_profile data is empty — the refinement result carried "
                "no profile (a stale GSAS-II server may be running: "
                "pixi run gsas-server restart)"
            )
        missing = [c for c in ('two_theta', 'y_calc')
                   if c not in fit_profile_data]
        if missing:
            raise ValueError(
                f"fit_profile data is missing column(s): {', '.join(missing)}"
            )
        return (np.asarray(fit_profile_data['two_theta'], dtype=float),
                np.asarray(fit_profile_data['y_calc'], dtype=float))

    @staticmethod
    def export_to_chi(
        two_theta: np.ndarray,
        intensity: np.ndarray,
        output_path: Path,
        metadata: Optional[dict] = None
    ) -> None:
        """Export pattern to CHI format (2-column ASCII).
        
        CHI format:
        # Optional header comments
        2theta1  intensity1
        2theta2  intensity2
        ...
        
        Args:
            two_theta: Two-theta values (degrees)
            intensity: Intensity values
            output_path: Path to save CHI file
            metadata: Optional metadata dictionary for header
        
        Raises:
            ValueError: If arrays have different lengths or contain invalid values
        """
        # Validate inputs
        if len(two_theta) != len(intensity):
            raise ValueError(
                f"Array length mismatch: two_theta has {len(two_theta)} points, "
                f"intensity has {len(intensity)} points"
            )
        
        if len(two_theta) == 0:
            raise ValueError("Cannot export empty pattern (no data points)")
        
        # Check for invalid values
        if np.any(np.isnan(two_theta)) or np.any(np.isnan(intensity)):
            raise ValueError("Arrays contain NaN values")
        
        if np.any(np.isinf(two_theta)) or np.any(np.isinf(intensity)):
            raise ValueError("Arrays contain infinite values")
        
        output_path = Path(output_path)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        
        with open(output_path, 'w', encoding='utf-8') as f:
            # Write header comments
            if metadata:
                f.write(f"# Simulated diffraction pattern\n")
                f.write(f"# Material: {metadata.get('formula', 'Unknown')}\n")
                f.write(f"# Material ID: {metadata.get('material_id', 'Unknown')}\n")
                f.write(f"# Wavelength: {metadata.get('wavelength', 'Unknown')} Å\n")
                f.write(f"# 2θ range: {two_theta[0]:.2f} - {two_theta[-1]:.2f}°\n")
                f.write(f"# Data points: {len(two_theta)}\n")
                f.write(f"#\n")
            
            # Write data
            for tth, intensity_val in zip(two_theta, intensity):
                f.write(f"{tth:.6f}  {intensity_val:.6f}\n")
        
        print(f"{CHECK} Exported pattern to: {output_path}")
    
    @staticmethod
    def export_simulation_results(
        output_dir: Path,
        formula: str,
        material_id: str,
        wavelength: float,
        fit_profile_data: dict
    ) -> Path:
        """Export simulation results from the in-band fit_profile data.

        Uses the fit_profile dict carried in the refinement result rather
        than re-reading fit_profile.txt from disk, so the export does not
        depend on the filesystem state of the output directory.

        Args:
            output_dir: Directory to write the CHI file into
            formula: Chemical formula
            material_id: Materials Project ID
            wavelength: Wavelength used in simulation
            fit_profile_data: column-oriented fit_profile dict from the
                refinement result (``result['fit_profile']``)

        Returns:
            Path to exported CHI file
        """
        # Extract calculated pattern (in-band; no disk read)
        two_theta, y_calc = PatternExporter.extract_calculated_pattern_from_data(
            fit_profile_data)
        
        # Prepare output filename
        chi_filename = f"{material_id}_{formula}_simulated.chi"
        chi_path = output_dir / chi_filename
        
        # Export to CHI
        metadata = {
            'formula': formula,
            'material_id': material_id,
            'wavelength': wavelength
        }
        PatternExporter.export_to_chi(two_theta, y_calc, chi_path, metadata)
        
        return chi_path

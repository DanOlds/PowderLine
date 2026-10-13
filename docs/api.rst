API Reference
=============

This page provides detailed documentation for PowderLine's Python API.

Public API
----------

The names in ``powderline.__all__`` (v0.2.0, P-06): ``run`` and ``validate``
dispatch on the recipe's ``schema_name`` through the gateway registry
(``powderline.registry``); ``validate`` is engine-free and returns a
``ValidationResult`` (the gateway's validated model plus the structured
warnings); ``load_recipe_asset`` reads a recipe file; ``support_matrix`` lists
the installed gateways; the exceptions are ``PowderLineError`` and its
subclasses. The recipe schemas are the engine-free ``schema`` module of each
gateway (``powderline.gateways.gsasii.schema``, ``...topas.schema``,
``...easydiffraction.schema``) over ``powderline.schema_core``.

.. TODO re/10: autodoc the public API and the schema modules here (the
   docs migration); until then this page keeps the GSAS-II helper entries.

Command Line
------------

.. autofunction:: powderline.kicker.is_template_file

GSAS-II Gateway Helpers
-----------------------

Output Generation
~~~~~~~~~~~~~~~~~

Functions for exporting refinement results with uncertainty quantification
(``powderline.gateways.gsasii.extractors``).

.. autofunction:: powderline.gateways.gsasii.extractors.export_refined_parameters_csv

.. autofunction:: powderline.gateways.gsasii.extractors.calculate_cell_esds_from_A_matrix

.. autofunction:: powderline.gateways.gsasii.extractors.extract_refined_params_from_project

.. note::

   These helpers document the GSAS-II engine's extraction path. ESDs (estimated
   standard deviations) are read from GSAS-II's covariance matrix
   (``proj.data['Covariance']``). Unit cell ESDs require conversion from the reciprocal
   metric tensor (A-matrix) to direct lattice parameters via ``calculate_cell_esds_from_A_matrix()``.

Post-Refinement Extraction Helpers (Private)
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

The four private ``_extract_*`` helpers encapsulate the post-refinement data
extraction and file-writing logic. They are not part of the public API but are
documented here for maintainers.

.. autofunction:: powderline.gateways.gsasii.extractors._extract_fit_profile

.. autofunction:: powderline.gateways.gsasii.extractors._extract_spf_peak_report

.. autofunction:: powderline.gateways.gsasii.extractors._extract_phase_reports

.. autofunction:: powderline.gateways.gsasii.extractors._extract_refined_parameters

Background Functions
~~~~~~~~~~~~~~~~~~~~

.. autofunction:: powderline.gateways.gsasii.setters.set_chebyshev_background

.. autofunction:: powderline.gateways.gsasii.setters.set_single_peak_background

Fit Range
~~~~~~~~~

.. autofunction:: powderline.gateways.gsasii.setters.set_fit_range_hist


Types
-----

Refinement Parameter Format
~~~~~~~~~~~~~~~~~~~~~~~~~~~~

Throughout the native schemas, a refinable parameter is a two-element list::

    [value, refine_flag]

Where:

- ``value`` (float): Current parameter value
- ``refine_flag`` (bool): True to refine, False to hold fixed

Engines that support bounds take a four-element ``[value, refine_flag, min, max]``
(``BoundedRefinableParameter``); ``min``/``max`` may be ``null``.

Example::

    "wavelength": [0.45236, false]  # Fixed at 0.45236 Å
    "scale": [1.0, true]            # Refined starting from 1.0

Phase vs Histogram Parameters
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

- **Phase parameters**: Belong to crystal structure (unit cell, atoms)
- **Histogram parameters**: Belong to measurement (scale, broadening, background)

In multi-phase refinements, phase parameters are phase-specific,
while histogram parameters are shared across all phases (except scale factors,
which are phase-histogram pairs). Multi-phase refinement is fully supported.

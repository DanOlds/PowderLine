"""PowderLine exception hierarchy and the structured-warning shape (engine-free).

Every PowderLine-raised error derives from :class:`PowderLineError`. The
gateway errors split along the schema/runtime layers (master plan A23, A5/A8):

- :class:`GatewayNotInstalledError` — no gateway of that name is registered at
  all (an out-of-tree plugin that is not installed). The only case where
  ``validate()`` cannot run.
- :class:`EngineNotAvailableError` — the gateway is present but its engine
  dependency is missing from this environment. Raised at ``run()`` time only,
  never by ``validate()``/``validate_only``. Also an :class:`ImportError`, so
  callers that caught the underlying import failure keep working (A52).
- :class:`EngineVersionError` — the installed engine is outside the gateway's
  supported ``ENGINE_VERSION_SPEC``. A hard error (A44).
- :class:`EngineExecutionError` — the engine ran and failed.
- :class:`SymmetryError` — core symmetry helpers (``powderline.symmetry``)
  cannot interpret a space group or site. Gateways convert it to their own
  error type (A61).

An invalid recipe raises pydantic's own ``ValidationError``, which lists every
problem with its field location. Recipe checks live in the schema models
(core + engine), so there is no PowderLine wrapper for it (A70).

This module must never import an engine (enforced by an import-block test).
"""

from __future__ import annotations

from typing import Optional, TypedDict


class PowderLineError(Exception):
    """Base class for all PowderLine errors."""


class SymmetryError(PowderLineError, ValueError):
    """A space group or atomic site cannot be interpreted (``powderline.symmetry``).

    A ``ValueError`` so that, raised inside a pydantic validator, it is reported
    as a validation error with the field location. Gateways that call the
    symmetry helpers outside validation convert it to their own error type
    (A56, A61).
    """


class GatewayNotInstalledError(PowderLineError):
    """No gateway of this name is registered (the gateway package is absent)."""

    def __init__(self, message: str, *, gateway: str) -> None:
        super().__init__(message)
        self.gateway = gateway


class EngineNotAvailableError(PowderLineError, ImportError):
    """The gateway is present, but its engine dependency is not installed here."""

    def __init__(self, *, gateway: str, dependency: str, env: str,
                 install_command: str) -> None:
        super().__init__(
            f"the {gateway!r} gateway needs {dependency}, which is not installed "
            f"in this environment; use the {env!r} pixi environment "
            f"(install it with: {install_command}; run with: pixi run -e {env} ...)"
        )
        self.gateway = gateway
        self.dependency = dependency
        self.env = env
        self.install_command = install_command


class EngineVersionError(PowderLineError):
    """The installed engine version is outside the gateway's supported range."""

    def __init__(self, *, gateway: str, engine: str, installed: str, supported: str,
                 message: Optional[str] = None) -> None:
        super().__init__(
            message or f"the {gateway!r} gateway supports {engine} {supported}, but {installed} "
            "is installed; install a supported version"
        )
        self.gateway = gateway
        self.engine = engine
        self.installed = installed
        self.supported = supported


class EngineExecutionError(PowderLineError):
    """The engine ran and failed."""


class StructuredWarning(TypedDict):
    """One entry of the ``warnings`` list in results and ``validate()`` output.

    ``field_path`` is the dotted recipe path the warning refers to, or ``None``
    when it is not tied to one field. Plumbed into results per gateway in
    re/04-06.
    """

    code: str
    message: str
    field_path: Optional[str]

"""Client library for GSAS-II server communication.

Provides a simple API for submitting simulations to the GSAS-II server.
Automatically falls back to in-process execution when the server is not
running, or when a "successful" server run produced output files that are
not visible to this process (server with a divergent filesystem view, e.g.
on another node or inside a sandbox with a private /tmp).
"""

import subprocess
import sys
import os
from pathlib import Path
from typing import Dict, Any
import tempfile

HOST = "127.0.0.1"
DEFAULT_PORT = 19471
PORT = int(os.environ.get('POWDERLINE_SERVER_PORT', DEFAULT_PORT))
PORT_FILE = Path(tempfile.gettempdir()) / 'powderline_gsas_server.port'


class GSASClient:
    """Client for communicating with GSAS-II server."""

    def __init__(self, fallback_to_subprocess: bool = True):
        """Initialize client.

        Args:
            fallback_to_subprocess: If True, use subprocess when server unavailable
        """
        self.fallback_to_subprocess = fallback_to_subprocess

    def _get_port(self) -> int:
        """Read the server port from the port file."""
        if PORT_FILE.exists():
            try:
                return int(PORT_FILE.read_text().strip())
            except (ValueError, OSError):
                pass
        return PORT

    def is_server_available(self) -> bool:
        """Check if server is running and accepting connections."""
        import httpx

        port = self._get_port()
        try:
            resp = httpx.get(f"http://{HOST}:{port}/health", timeout=2.0)
            return resp.status_code == 200
        except (httpx.ConnectError, httpx.TimeoutException, OSError):
            return False

    @staticmethod
    def _stat_fit_profile(output_dir: Path) -> tuple | None:
        """(mtime_ns, size) of output_dir/fit_profile.txt, or None if absent."""
        try:
            st = (Path(output_dir) / 'fit_profile.txt').stat()
        except OSError:
            return None
        return (st.st_mtime_ns, st.st_size)

    def _server_output_visible(self, result: Dict[str, Any], output_dir: Path,
                               stat_before: tuple | None) -> bool:
        """Check that a 'successful' server run materialized its output files
        where THIS process can see them.

        A GSAS-II server can be reachable over localhost yet have a different
        filesystem view — e.g. started on another node of a cluster, or inside
        a sandbox/container with a private /tmp. Such a server reports success
        while its output files are unreachable. run_refinement always
        (re)writes fit_profile.txt on success, so the file must exist AND its
        (mtime, size) must differ from before the run — a stale file left by a
        previous run into the same output_dir does not count. Costs two stat
        calls per server run; nothing is read, written, or deleted.
        """
        if not result.get('success'):
            return True  # error paths need no file check
        stat_after = self._stat_fit_profile(output_dir)
        return stat_after is not None and stat_after != stat_before

    _INVISIBLE_OUTPUT_ERROR = (
        "GSAS-II server reported success but did not write output files "
        "visible to this process — the server has a different filesystem view "
        "(started on another node, or inside a sandbox/container with a "
        "private /tmp). Restart it with: pixi run gsas-server restart"
    )

    def _handle_invisible_server_output(self) -> Dict[str, Any] | None:
        """Warn about a server with a divergent filesystem view.

        Returns None when in-process fallback is enabled (caller falls
        through to it), or a structured error result when fallback is
        disabled (execution_mode='server').
        """
        print("   WARNING: GSAS-II server reported success but did not write "
              "output files visible to this process.")
        print("   The server appears to have a different filesystem view "
              "(started on another node, or inside a sandbox/container with a "
              "private /tmp).")
        if self.fallback_to_subprocess:
            print("   Falling back to in-process execution. To fix the server: "
                  "pixi run gsas-server restart")
            return None
        print("   In-process fallback is disabled. To fix the server: "
              "pixi run gsas-server restart")
        return {
            'success': False,
            'error': self._INVISIBLE_OUTPUT_ERROR,
            'method': 'server',
        }

    def _submit_to_server_guarded(self, recipe_dict: dict,
                                  output_dir: Path) -> Dict[str, Any] | None:
        """Submit to the server with the output-visibility guard applied.

        Returns the result to hand to the caller, or None when the caller
        should fall through to the in-process fallback (invisible output
        with fallback enabled). Exceptions from the server round-trip
        propagate unchanged.
        """
        stat_before = self._stat_fit_profile(output_dir)
        result = self._submit_to_server(recipe_dict, output_dir)
        result['method'] = 'server'
        if self._server_output_visible(result, output_dir, stat_before):
            return result
        return self._handle_invisible_server_output()

    def submit_simulation(self, recipe, output_dir: Path,
                         verbose: bool = False, auto_start_server: bool = True,
                         use_server: bool = True) -> Dict[str, Any]:
        """Submit a simulation request.

        Args:
            recipe: Recipe to run. Accepts:
                - Path: path to a JSON/YAML recipe file (loaded automatically)
                - dict: pre-loaded recipe dictionary
                - a validated recipe model (``GsasiiRietveldRecipe`` / ``GsasiiSpfRecipe``)
            output_dir: Directory for output files
            verbose: If True, print detailed output
            auto_start_server: If True, start server automatically if not running
            use_server: If False, skip the server entirely and run in-process

        Returns:
            Dictionary with 'success', 'output_files', 'elapsed_time',
            and optional 'error', 'method' fields

        Note:
            A server response only counts if its output files were freshly
            written where this process can see them (see
            _server_output_visible — a stale fit_profile.txt from a previous
            run does not count); otherwise the run falls back to in-process
            execution with a warning — or, when fallback_to_subprocess is
            False, returns a structured error explaining the filesystem-view
            mismatch.
        """
        result = self._dispatch(recipe, output_dir, verbose, auto_start_server, use_server)
        return _with_validation_warnings(recipe, result)

    def _dispatch(self, recipe, output_dir: Path, verbose: bool, auto_start_server: bool,
                  use_server: bool) -> Dict[str, Any]:
        """Run the recipe on the server or in this process (see :meth:`submit_simulation`)."""
        recipe_dict = self._normalize_recipe(recipe)

        if not use_server:
            if not verbose:
                print("   Using in-process mode (use_server=False)")
        # Try server first
        elif self.is_server_available():
            if not verbose:
                print(f"   Using GSAS-II server mode")
            try:
                result = self._submit_to_server_guarded(recipe_dict, output_dir)
                if result is not None:
                    return result
                # fall through to in-process fallback below
            except Exception as e:
                if not self.fallback_to_subprocess:
                    return {
                        'success': False,
                        'error': f"Server communication failed: {e}",
                        'method': 'server'
                    }

                if verbose:
                    print(f"Server communication failed: {e}")
                    print("   Falling back to subprocess mode...")

        # Try to start server if not available and auto_start_server enabled
        elif auto_start_server:
            if verbose:
                print("   Attempting to start server automatically...")
            if self._start_server_background(verbose):
                import time
                time.sleep(1)

                # Try again
                if self.is_server_available():
                    if not verbose:
                        print(f"   Using GSAS-II server mode")
                    try:
                        result = self._submit_to_server_guarded(recipe_dict, output_dir)
                        if result is not None:
                            return result
                        # fall through to in-process fallback below
                    except Exception as e:
                        if verbose:
                            print(f"Failed to connect to auto-started server: {e}")
                        if not verbose:
                            print(f"   Using subprocess mode (start server with: pixi run gsas-server start)")
                else:
                    if verbose:
                        print(f"   Server started but not responding")
                    if not verbose:
                        print(f"   Using subprocess mode (start server with: pixi run gsas-server start)")
            else:
                if not verbose:
                    print(f"   Using subprocess mode (start server with: pixi run gsas-server start)")
        else:
            if not verbose:
                print(f"   Using subprocess mode (start server with: pixi run gsas-server start)")

        # Fallback to subprocess
        if self.fallback_to_subprocess:
            result = self._submit_via_subprocess(recipe_dict, output_dir, verbose)
            result['method'] = 'subprocess'
            return result
        elif not use_server:
            # Explicitly requested state, kept honest: both execution paths
            # were disabled by the caller, so nothing ran.
            return {
                'success': False,
                'error': 'No execution path enabled: use_server=False and '
                         'fallback_to_subprocess=False — enable one of them.',
                'method': 'none'
            }
        else:
            return {
                'success': False,
                'error': 'Server not available and fallback disabled',
                'method': 'none'
            }

    @staticmethod
    def _normalize_recipe(recipe) -> dict:
        """Normalize recipe input to a plain JSON-serializable dict.

        Accepts Path (loaded from disk), dict (used as-is), or a validated
        recipe model (serialized via .model_dump). Raises TypeError for unsupported inputs.
        """
        if isinstance(recipe, Path):
            import json
            import yaml
            ext = recipe.suffix.lower()
            text = recipe.read_text()
            if ext == '.json':
                return json.loads(text)
            if ext in {'.yaml', '.yml', '.txt'}:
                return yaml.safe_load(text) or {}
            raise ValueError(f"Unsupported recipe format: {recipe}")
        if isinstance(recipe, dict):
            return recipe
        if hasattr(recipe, 'model_dump'):
            return recipe.model_dump(mode='json')
        raise TypeError(
            f"recipe must be a Path, dict, or validated recipe model, got {type(recipe).__name__}"
        )

    def _start_server_background(self, verbose: bool = False) -> bool:
        """Start server in background process.

        Returns:
            True if server was started, False if already running or failed
        """
        import subprocess
        import os
        import time

        # Check if already running
        if self.is_server_available():
            return True

        try:
            # Build command to start server
            src_dir = Path(__file__).parents[3]
            server_script = Path(__file__).parent / 'server.py'

            # Prepare environment
            env = os.environ.copy()
            env['PYTHONPATH'] = str(src_dir) + os.pathsep + env.get('PYTHONPATH', '')

            # Use subprocess to start server in background
            try:
                # Try with preexec_fn (Unix only)
                proc = subprocess.Popen(
                    [sys.executable, str(server_script), 'start'],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    stdin=subprocess.DEVNULL,
                    env=env,
                    preexec_fn=os.setpgrp,  # Detach from parent process group
                    start_new_session=False
                )
            except (TypeError, AttributeError):
                # Fallback for Windows or if preexec_fn not available
                proc = subprocess.Popen(
                    [sys.executable, str(server_script), 'start'],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    stdin=subprocess.DEVNULL,
                    env=env,
                    creationflags=subprocess.CREATE_NEW_PROCESS_GROUP if sys.platform == 'win32' else 0
                )

            # Give server time to start
            for attempt in range(30):  # Wait up to 3 seconds
                time.sleep(0.1)
                if self.is_server_available():
                    if verbose:
                        print("   Server started in background")
                    return True

            if verbose:
                print(f"   Server started (PID {proc.pid}) but not responding yet")
            return True  # Started even if not responding immediately

        except Exception as e:
            if verbose:
                print(f"   Could not auto-start server: {e}")
            return False

    def _submit_to_server(self, recipe_dict: dict, output_dir: Path) -> Dict[str, Any]:
        """Submit simulation to server via HTTP with retry logic.

        Retries up to 3 times on connection errors to handle transient network issues.
        """
        import httpx
        import time

        port = self._get_port()
        payload = {
            'recipe_data': recipe_dict,
            'output_dir': str(output_dir.absolute()),
        }

        # Retry logic for connection errors
        max_retries = 3
        retry_delay = 0.5  # seconds

        last_error = None
        for attempt in range(max_retries):
            try:
                resp = httpx.post(
                    f"http://{HOST}:{port}/simulate",
                    json=payload,
                    timeout=300.0,
                )
                resp.raise_for_status()
                return resp.json()
            except httpx.ConnectError as e:
                last_error = e
                if attempt < max_retries - 1:
                    time.sleep(retry_delay)
                    retry_delay *= 2  # Exponential backoff
                    continue
            except httpx.HTTPStatusError as e:
                # Don't retry on HTTP errors (4xx, 5xx)
                raise
            except httpx.TimeoutException as e:
                # Don't retry on timeout (simulation is running)
                raise

        # If we exhausted all retries, raise the last error
        raise last_error

    def _submit_via_subprocess(self, recipe_dict: dict, output_dir: Path,
                               verbose: bool) -> Dict[str, Any]:
        """Run simulation in-process (fallback mode when server is unavailable).

        **Important**: Despite the method name, this does NOT spawn an OS subprocess.
        It calls ``run_refinement()`` directly in the current process. The name is
        retained for API stability. If hard crash-isolation is needed in a future
        phase, this method can be upgraded to use ``multiprocessing`` internally
        without changing any callers.

        Contrast with ``_start_server_background()``, which *does* spawn an OS
        subprocess to launch the FastAPI server.

        Calls run_refinement() directly in the current process, returning the
        same structured result dict as server mode (fit_profile, unit_cell_data,
        peak_list_data, refined_parameters, rwp, etc.).
        """
        import time
        from powderline.gateways.gsasii.routing import model_from_request, run_refinement
        from pydantic import ValidationError

        output_dir.mkdir(parents=True, exist_ok=True)

        try:
            recipe = model_from_request(recipe_dict)
        except ValidationError as e:
            error_lines = [
                f"{' -> '.join(str(loc) for loc in err['loc'])}: {err['msg']}"
                for err in e.errors()
            ]
            return {
                'success': False,
                'error': f"Recipe validation failed:\n" + "\n".join(error_lines),
                'method': 'subprocess'
            }
        except Exception as e:
            return {
                'success': False,
                'error': f"Failed to parse recipe: {str(e)}",
                'method': 'subprocess'
            }

        start_time = time.time()
        try:
            result = run_refinement(recipe, output_dir, verbose=verbose, method='subprocess')
        except Exception as e:
            elapsed = time.time() - start_time
            return {
                'success': False,
                'error': str(e),
                'elapsed_time': elapsed,
                'method': 'subprocess'
            }

        return result


# Convenience function for simple use cases
def run_simulation(recipe, output_dir: Path,
                  verbose: bool = False, auto_start_server: bool = True) -> Dict[str, Any]:
    """Run a simulation using the server if available and usable, else in-process.

    This is a convenience wrapper around GSASClient.submit_simulation(),
    including its output-visibility guard (a server whose files are not
    visible to this process triggers an in-process rerun).
    With auto_start_server=True (default), the server is automatically started
    if not running, providing fast performance for first and subsequent uses.

    Args:
        recipe: Recipe to run. Accepts Path, dict, or a validated recipe model.
        output_dir: Directory for output files
        verbose: If True, print detailed output
        auto_start_server: If True, automatically start server in background

    Returns:
        Dictionary with 'success', 'output_files', 'elapsed_time', 'method'

    Example:
        >>> result = run_simulation(Path('recipe.json'), Path('output/'))
        >>> result = run_simulation(recipe_dict, Path('output/'))
        >>> if result['success']:
        ...     print(f"Completed in {result['elapsed_time']:.1f}s via {result['method']}")
    """
    client = GSASClient()
    return client.submit_simulation(recipe, output_dir, verbose, auto_start_server)


def _with_validation_warnings(recipe, result: Dict[str, Any]) -> Dict[str, Any]:
    """Prepend a native ``gsasii.*`` recipe's validation warnings to the run's ``warnings`` (A115).

    They are collected from the recipe as given (the model the caller validated,
    or the dict / file validated here), so a default the recipe left out is
    reported even though the server receives the dumped model, which states it.
    A recipe that does not validate (the run already says why) gets none.
    """
    from pydantic import ValidationError
    from powderline.gateways.gsasii.schema import validate_recipe
    from powderline.schema_core import collect_warnings

    if isinstance(recipe, Path):
        try:
            recipe = GSASClient._normalize_recipe(recipe)
        except (OSError, ValueError):
            return result
    try:
        found = collect_warnings(validate_recipe(recipe))
    except ValidationError:
        return result
    result['warnings'] = found + list(result.get('warnings') or [])
    return result

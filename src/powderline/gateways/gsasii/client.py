"""Client library for GSAS-II server communication.

Provides a simple API for submitting simulations to the GSAS-II server.
Automatically falls back to in-process execution when the server is not
running, or when a "successful" server run produced output files that are
not visible to this process (server with a divergent filesystem view, e.g.
on another node or inside a sandbox with a private /tmp).

Every outcome is an *envelope* (:mod:`powderline.results`): ``ok`` from the
server or the in-process run, ``failed`` for a backend problem (server
unavailable, disabled, timed out, output not visible) or an engine failure
relayed from the server. The client never raises for an application outcome;
the gsasii gateway raises from the envelope (re/12).

Only the calling user's own server is ever used: its port and secret token
are read from the user's private state directory. The token itself is never
sent; every request is signed with it and every response must carry a valid
signature, so a listener that does not hold the token (e.g. another user's
process on the port of a crashed server) is never trusted — it learns nothing
it could replay and cannot fake a result. ``POWDERLINE_NO_SERVER`` disables
server use entirely (see :func:`powderline.gateways.gsasii.server.server_disabled`).
"""

import json
import secrets
import subprocess
import sys
import os
from pathlib import Path
from typing import Dict, Any, Optional

from powderline import results as envelopes
from powderline.exceptions import EngineExecutionError
from powderline.gateways.gsasii import server as gsas_server
from powderline.gateways.gsasii.server import (
    HOST,
    NO_SERVER_ENV,
    NO_SERVER_MESSAGE,
    NONCE_HEADER,
    RESPONSE_SIGNATURE_HEADER,
    SIGNATURE_HEADER,
    TIMESTAMP_HEADER,
    ServerStateError,
    read_endpoint,
    server_disabled,
    sign_request,
    sign_response,
)


class ServerIdentityError(RuntimeError):
    """A response was not signed with this user's server token."""


class GSASClient:
    """Client for communicating with GSAS-II server."""

    def __init__(self, fallback_to_subprocess: bool = True):
        """Initialize client.

        Args:
            fallback_to_subprocess: If True, use subprocess when server unavailable
        """
        self.fallback_to_subprocess = fallback_to_subprocess
        self._state_warning_shown = False

    def _endpoint(self) -> Optional[Dict[str, Any]]:
        """This user's live server endpoint (``pid``/``port``/``token``), or None.

        An endpoint whose recorded server process is gone is stale (the server
        crashed without cleaning up) and is ignored.
        """
        try:
            endpoint = read_endpoint()
        except ServerStateError as e:
            if not self._state_warning_shown:
                print(f"   WARNING: {e}")
                self._state_warning_shown = True
            return None
        if endpoint is not None and not gsas_server._pid_alive(endpoint['pid']):
            return None
        return endpoint

    @staticmethod
    def _signed_request(endpoint: Dict[str, Any], method: str, path: str,
                        payload: Optional[dict] = None, timeout: float = 2.0):
        """Send a request signed with the endpoint's token; verify the response.

        The token never leaves this process: the request carries a fresh
        nonce, a timestamp and an HMAC, and the response must be signed over
        that nonce, its status and its body.

        Raises:
            ServerIdentityError: If the response is not signed by the token holder.
            httpx.HTTPError: On connection/transport errors.
        """
        import httpx
        import time

        body = b'' if payload is None else json.dumps(payload).encode()
        nonce = secrets.token_hex(16)
        timestamp = str(int(time.time()))
        token = endpoint['token']
        headers = {
            NONCE_HEADER: nonce,
            TIMESTAMP_HEADER: timestamp,
            SIGNATURE_HEADER: sign_request(token, method, path, b'', nonce, timestamp, body),
        }
        url = f"http://{HOST}:{endpoint['port']}{path}"
        # trust_env=False: loopback only, never route via a proxy
        if method == 'GET':
            resp = httpx.get(url, headers=headers, timeout=timeout, trust_env=False)
        else:
            headers['Content-Type'] = 'application/json'
            resp = httpx.post(url, content=body, headers=headers, timeout=timeout,
                              trust_env=False)
        got = resp.headers.get(RESPONSE_SIGNATURE_HEADER, '')
        expected = sign_response(token, nonce, resp.status_code, resp.content)
        if not secrets.compare_digest(got.encode('utf-8', 'replace'), expected.encode()):
            raise ServerIdentityError(
                f"The listener on port {endpoint['port']} did not prove it is your "
                f"GSAS-II server (HTTP {resp.status_code}); not using it.")
        return resp

    def is_server_available(self) -> bool:
        """Check that THIS user's server is up and holds this user's token.

        ``/health`` is sent as a signed request and its response must be
        signed with the token, so a listener that does not know the token
        (e.g. another user's process on a recycled port) is never treated as
        available. Always False when ``POWDERLINE_NO_SERVER`` is set.
        """
        import httpx

        if server_disabled():
            return False
        endpoint = self._endpoint()
        if endpoint is None:
            return False
        try:
            resp = self._signed_request(endpoint, 'GET', '/health', timeout=2.0)
        except (httpx.HTTPError, OSError, ServerIdentityError):
            return False
        return resp.status_code == 200

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
        if result.get('status') == 'failed':
            return True  # a failed envelope needs no file check and is returned as-is
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
        return envelopes.failure_envelope("backend", "output_not_visible", self._INVISIBLE_OUTPUT_ERROR,
                                          method="server")

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

            With ``POWDERLINE_NO_SERVER`` set, the server is never probed or
            started: the run goes in-process, or — when fallback is disabled
            (``execution_mode='server'``) — returns a structured error.
        """
        result = self._dispatch(recipe, output_dir, verbose, auto_start_server, use_server)
        return _with_validation_warnings(recipe, result)

    def _dispatch(self, recipe, output_dir: Path, verbose: bool, auto_start_server: bool,
                  use_server: bool) -> Dict[str, Any]:
        """Run the recipe on the server or in this process (see :meth:`submit_simulation`)."""
        import httpx

        recipe_dict = self._normalize_recipe(recipe)

        if use_server and server_disabled():
            if not self.fallback_to_subprocess:
                return envelopes.failure_envelope("backend", "server_disabled", NO_SERVER_MESSAGE, method="none")
            print(f"   Using in-process mode ({NO_SERVER_ENV} is set)")
        elif not use_server:
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
            except httpx.TimeoutException as e:
                # Never fall back after a request was accepted: the server may still be writing into output_dir.
                return envelopes.failure_envelope(
                    "backend", "timeout",
                    f"the GSAS-II server did not answer within the request timeout ({e}); not falling back "
                    f"in-process because the server may still be writing into {output_dir}", method="server")
            except Exception as e:
                if not self.fallback_to_subprocess:
                    return envelopes.failure_envelope("backend", "communication_failed",
                                                      f"Server communication failed: {e}", method="server")

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
            return envelopes.failure_envelope(
                "backend", "server_unavailable",
                'No execution path enabled: use_server=False and fallback_to_subprocess=False — enable one of them.',
                method="none")
        else:
            return envelopes.failure_envelope("backend", "server_unavailable",
                                              "Server not available and fallback disabled", method="none")

    @staticmethod
    def _normalize_recipe(recipe) -> dict:
        """Normalize recipe input to a plain JSON-serializable dict.

        Accepts Path (loaded from disk), dict (used as-is), or a validated
        recipe model (serialized via .model_dump). Raises TypeError for unsupported inputs.
        """
        if isinstance(recipe, Path):
            from powderline.recipes import load_recipe_asset  # engine-free; the one loader (review item 28)
            return load_recipe_asset(recipe)
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

        if server_disabled():
            return False
        try:
            gsas_server.state_dir()  # a server could not start with an unsafe state dir
        except ServerStateError as e:
            if verbose:
                print(f"   Could not auto-start server: {e}")
            return False

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

        payload = {
            'recipe_data': recipe_dict,
            'output_dir': str(output_dir.absolute()),
        }

        # Retry logic for connection errors
        max_retries = 3
        retry_delay = 0.5  # seconds

        last_error = None
        for attempt in range(max_retries):
            # Re-read each attempt: a restarted server has a new port and token.
            endpoint = self._endpoint()
            try:
                if endpoint is None:
                    raise httpx.ConnectError("No GSAS-II server endpoint for this user")
                resp = self._signed_request(endpoint, 'POST', '/simulate',
                                            payload, timeout=300.0)
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
            return envelopes.failure_envelope("backend", "backend_error",
                                              "Recipe validation failed:\n" + "\n".join(error_lines),
                                              method="subprocess")
        except Exception as e:
            return envelopes.failure_envelope("backend", "backend_error", f"Failed to parse recipe: {str(e)}",
                                              method="subprocess")

        start_time = time.time()
        files = lambda: sorted(str(f) for f in output_dir.glob('*') if f.is_file())  # noqa: E731
        try:
            result = run_refinement(recipe, output_dir, verbose=verbose, method='subprocess')
        except EngineExecutionError as e:  # R30: PowderLine's own failure (divergence, site, peak fit)
            return e.result or envelopes.failure_envelope("engine", e.reason, str(e), method="subprocess",
                                                          elapsed=time.time() - start_time, output_files=files())
        except Exception as e:
            import traceback
            return envelopes.failure_envelope("engine", "engine_crashed", str(e), method="subprocess",
                                              elapsed=time.time() - start_time, traceback=traceback.format_exc(),
                                              output_files=files())
        if result.get('success') is False and 'status' not in result:  # a frozen catch-all's early exit
            return envelopes.legacy_failure(result)
        return envelopes.with_status_fields(result, status="ok",
                                            converged=(result.get('engine_details') or {}).get('converged'))


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

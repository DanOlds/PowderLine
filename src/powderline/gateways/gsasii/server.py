"""GSAS-II persistent server for fast simulations.

This server keeps GSAS-II loaded in memory to eliminate startup overhead.
Listens on an HTTP port via FastAPI and processes simulation requests sequentially.

Security notes:
    - The server binds exclusively to ``127.0.0.1`` (loopback). It is not
      accessible from remote hosts and requires no authentication by design.
    - No rate limiting is applied. For a shared or multi-user environment,
      add rate limiting via a reverse proxy or a middleware library such as
      ``slowapi`` before exposing this server to untrusted callers.
    - ``output_dir`` paths are sent by the client and used as-is; path
      traversal is only exploitable by a local user who already has file-system
      access equivalent to the server process.
    - The server resolves ``output_dir`` in ITS OWN filesystem view. A client
      on another node — or a server started inside a sandbox/container with a
      private /tmp — will never see the output files even though the run
      succeeds. ``GSASClient._server_output_visible`` guards clients against
      this and falls back to in-process execution.

Performance:
- First simulation: ~12s (server startup + GSAS-II import)
- Subsequent simulations: ~2-3s each (no startup overhead)
- 4-6x speedup for batch operations

Usage:
    # Start server (daemon mode)
    pixi run gsas-server start

    # Stop server
    pixi run gsas-server stop

    # Check status
    pixi run gsas-server status

    # View logs
    tail -f /tmp/powderline_gsas_server.log

    # Restart server
    pixi run gsas-server restart
"""

import json
import sys
import os
import signal
import time
import subprocess
import logging
from pathlib import Path
from typing import Dict, Any, Optional, List
import tempfile
import atexit
from datetime import datetime

from pydantic import BaseModel

# Server configuration
HOST = "127.0.0.1"
DEFAULT_PORT = 19471
PORT = int(os.environ.get('POWDERLINE_SERVER_PORT', DEFAULT_PORT))
PORT_FILE = Path(tempfile.gettempdir()) / 'powderline_gsas_server.port'
PID_FILE = Path(tempfile.gettempdir()) / 'powderline_gsas_server.pid'
LOG_FILE = Path(tempfile.gettempdir()) / 'powderline_gsas_server.log'


# --- Pydantic request/response models ---

class SimulationRequest(BaseModel):
    recipe_data: dict
    output_dir: str
    verbose: bool = False


class SimulationResponse(BaseModel):
    success: bool
    run_id: Optional[str] = None
    rwp: Optional[float] = None
    elapsed_time: Optional[float] = None
    method: Optional[str] = None
    error: Optional[str] = None
    traceback: Optional[str] = None
    output_files: Optional[List[str]] = None
    fit_profile: Optional[dict] = None
    unit_cell_data: Optional[dict] = None
    peak_list_data: Optional[dict] = None
    refined_parameters: Optional[list] = None
    spf_peaks: Optional[dict] = None
    spf_convergence_diagnostics: Optional[dict] = None
    # native gsasii.* runs (re/04): standard fit statistics, GSAS-II's own values, structured warnings
    r_exp: Optional[float] = None
    gof: Optional[float] = None
    chi2_red: Optional[float] = None
    simulation_mode: Optional[bool] = None
    engine_details: Optional[dict] = None
    warnings: Optional[list] = None


# --- Logging ---

def setup_logging(log_file: Path) -> logging.Logger:
    """Set up logging to both console and file."""
    logger = logging.getLogger('powderline.server')
    logger.setLevel(logging.DEBUG)

    # File handler (always log everything)
    file_handler = logging.FileHandler(log_file, mode='a')
    file_handler.setLevel(logging.DEBUG)

    # Console handler (info level for cleaner output)
    console_handler = logging.StreamHandler()
    console_handler.setLevel(logging.INFO)

    # Formatter with timestamps
    formatter = logging.Formatter(
        '%(asctime)s - %(levelname)s - %(message)s',
        datefmt='%Y-%m-%d %H:%M:%S'
    )
    file_handler.setFormatter(formatter)
    console_handler.setFormatter(formatter)

    logger.addHandler(file_handler)
    logger.addHandler(console_handler)

    return logger


# --- Server class ---

class GSASServer:
    """Persistent GSAS-II server for fast simulations."""

    def __init__(self, logger: logging.Logger = None):
        """Initialize server (GSAS-II import happens here).

        Args:
            logger: Optional logger instance. If not provided, one will be created.
        """
        self.running = False
        self.request_count = 0
        self.start_time = None

        # Set up logging
        self.logger = logger or setup_logging(LOG_FILE)

        # Import GSAS-II (this is the slow part we want to do once)
        self.logger.info("Loading GSAS-II libraries...")
        import_start = time.time()

        # Set PYTHONPATH to include src directory
        src_dir = Path(__file__).parents[3]
        if str(src_dir) not in sys.path:
            sys.path.insert(0, str(src_dir))

        try:
            # Import the GSAS-II executors (they import GSAS-II at module top)
            # once, so every request finds it loaded
            from powderline.gateways.gsasii import executors
            self.runtime = executors
            import_time = time.time() - import_start
            self.logger.info(f"GSAS-II loaded in {import_time:.1f}s")
        except ImportError as e:
            self.logger.error(f"Failed to import GSAS-II: {e}")
            sys.exit(1)

    def create_app(self):
        """Build and return the FastAPI application."""
        from fastapi import FastAPI

        app = FastAPI(title="PowderLine GSAS-II Server")
        server = self  # capture for closures

        @app.post("/simulate", response_model=SimulationResponse)
        def simulate(req: SimulationRequest):
            server.request_count += 1
            schema_name = req.recipe_data.get('schema_name', 'unknown') if isinstance(req.recipe_data, dict) else 'unknown'
            server.logger.info(f"Request #{server.request_count}: schema={schema_name}")

            result = server._run_simulation(req.model_dump())

            elapsed = result.get('elapsed_time', 0)
            run_id = result.get('run_id', 'n/a')
            if result.get('success'):
                server.logger.info(f"Request #{server.request_count} (run_id={run_id}) completed in {elapsed:.1f}s")
            else:
                server.logger.warning(
                    f"Request #{server.request_count} (run_id={run_id}) failed: {result.get('error', 'Unknown error')}"
                )
                if result.get('traceback'):
                    server.logger.debug(
                        f"Request #{server.request_count} traceback:\n{result['traceback']}"
                    )
            return SimulationResponse(**result)

        @app.get("/health")
        def health():
            uptime = (datetime.now() - server.start_time).total_seconds() if server.start_time else 0
            return {
                "status": "ok",
                "pid": os.getpid(),
                "uptime_seconds": uptime,
                "request_count": server.request_count,
            }

        return app

    def start(self):
        """Start the server with uvicorn."""
        import uvicorn

        self.start_time = datetime.now()
        self.logger.info("=" * 60)
        self.logger.info("GSAS-II Server Starting")
        self.logger.info("=" * 60)

        # Write PID and port files
        PID_FILE.write_text(str(os.getpid()))
        PORT_FILE.write_text(str(PORT))

        # Register cleanup
        atexit.register(self.cleanup)
        signal.signal(signal.SIGTERM, self._signal_handler)
        signal.signal(signal.SIGINT, self._signal_handler)

        self.running = True
        self.logger.info(f"GSAS-II server started")
        self.logger.info(f"   URL: http://{HOST}:{PORT}")
        self.logger.info(f"   Log file: {LOG_FILE}")
        self.logger.info(f"   PID: {os.getpid()}")
        self.logger.info(f"   Started: {self.start_time.strftime('%Y-%m-%d %H:%M:%S')}")
        self.logger.info(f"   Listening for simulation requests...")
        self.logger.info("-" * 60)

        app = self.create_app()
        uvicorn.run(app, host=HOST, port=PORT, log_level="warning")

    def _run_simulation(self, request: Dict[str, Any]) -> Dict[str, Any]:
        """Run a GSAS-II simulation with the loaded GSAS-II runtime.

        Args:
            request: Dictionary with 'recipe_data' (inline recipe dict), 'output_dir',
                     and optional 'verbose'

        Returns:
            Dictionary with keys: 'success', 'run_id', 'rwp', 'elapsed_time',
            'method', 'output_files', 'fit_profile' (dict), 'unit_cell_data' (dict),
            'peak_list_data' (dict), 'refined_parameters' (list), 'spf_peaks' (dict),
            'spf_convergence_diagnostics' (dict), and optional 'error', 'traceback'.
        """
        from powderline.gateways.gsasii.routing import model_from_request, run_refinement
        from pydantic import ValidationError

        recipe_dict = request.get('recipe_data')
        output_dir = Path(request['output_dir'])
        verbose = request.get('verbose', False)

        if recipe_dict is None:
            return {
                'success': False,
                'error': 'Missing recipe_data in request',
                'method': 'server'
            }

        output_dir.mkdir(parents=True, exist_ok=True)

        # Validate recipe
        try:
            recipe = model_from_request(recipe_dict)
        except ValidationError as e:
            error_lines = [f"{' -> '.join(str(loc) for loc in err['loc'])}: {err['msg']}"
                          for err in e.errors()]
            return {
                'success': False,
                'error': f"Recipe validation failed:\n" + "\n".join(error_lines),
                'method': 'server'
            }
        except Exception as e:
            return {
                'success': False,
                'error': f"Failed to parse recipe: {str(e)}",
                'method': 'server'
            }

        # Run refinement directly using loaded GSAS-II libraries
        result = run_refinement(recipe, output_dir, verbose=verbose, method='server')

        return result

    def _signal_handler(self, signum, frame):
        """Handle shutdown signals."""
        self.logger.info(f"Received signal {signum}, shutting down...")
        self.running = False
        self.cleanup()
        sys.exit(0)

    def cleanup(self):
        """Clean up port file and PID file."""
        if PORT_FILE.exists():
            PORT_FILE.unlink()
        if PID_FILE.exists():
            PID_FILE.unlink()

        if self.start_time:
            uptime = datetime.now() - self.start_time
            self.logger.info(f"Server stopped")
            self.logger.info(f"   Total requests: {self.request_count}")
            self.logger.info(f"   Uptime: {uptime}")
            self.logger.info("=" * 60)


def _pid_alive(pid: int) -> bool:
    """Return True if a process with ``pid`` exists (cross-platform).

    ``os.kill(pid, 0)`` is a valid liveness probe on POSIX but not on Windows,
    where ``os.kill`` terminates the target (or raises) rather than checking it;
    use the Win32 API there instead.
    """
    if os.name == "nt":
        import ctypes
        from ctypes import wintypes

        PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
        STILL_ACTIVE = 259
        kernel32 = ctypes.windll.kernel32
        handle = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
        if not handle:
            return False
        try:
            code = wintypes.DWORD()
            if not kernel32.GetExitCodeProcess(handle, ctypes.byref(code)):
                return False
            return code.value == STILL_ACTIVE
        finally:
            kernel32.CloseHandle(handle)
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def is_server_running() -> bool:
    """Check if server is currently running."""
    if not PID_FILE.exists():
        return False

    try:
        pid = int(PID_FILE.read_text())
    except ValueError:
        PID_FILE.unlink()
        return False

    if _pid_alive(pid):
        return True
    # PID file exists but the process does not
    PID_FILE.unlink()
    return False


def stop_server() -> bool:
    """Stop the running server."""
    if not is_server_running():
        print("Server is not running")
        return False

    pid = int(PID_FILE.read_text())
    print(f"Stopping server (PID {pid})...")

    try:
        os.kill(pid, signal.SIGTERM)

        # Wait for graceful shutdown
        for _ in range(50):  # 5 seconds timeout
            time.sleep(0.1)
            if not is_server_running():
                print("Server stopped")
                return True

        # Force kill if still running. SIGKILL does not exist on Windows;
        # there os.kill(pid, SIGTERM) maps to TerminateProcess (a hard kill),
        # which is the correct escalation.
        print("Forcing shutdown...")
        force_signal = getattr(signal, "SIGKILL", signal.SIGTERM)
        os.kill(pid, force_signal)
        time.sleep(0.5)

        if not is_server_running():
            print("Server stopped (forced)")
            return True
        else:
            print("Failed to stop server")
            return False

    except ProcessLookupError:
        print("Server process not found")
        PID_FILE.unlink()
        return False


def get_server_info() -> Optional[Dict[str, Any]]:
    """Get information about the running server.

    Returns:
        Dictionary with server info (pid, url, log_file, uptime) or None if not running
    """
    if not is_server_running():
        return None

    try:
        pid = int(PID_FILE.read_text())
        port = int(PORT_FILE.read_text()) if PORT_FILE.exists() else PORT

        # Try to get process info
        import psutil
        proc = psutil.Process(pid)
        uptime = datetime.now() - datetime.fromtimestamp(proc.create_time())

        return {
            'pid': pid,
            'url': f"http://{HOST}:{port}",
            'log_file': str(LOG_FILE),
            'uptime': str(uptime).split('.')[0],  # Remove microseconds
            'cpu_percent': proc.cpu_percent(interval=0.1),
            'memory_mb': proc.memory_info().rss / 1024 / 1024,
        }
    except Exception:
        # psutil not available, return basic info
        port = int(PORT_FILE.read_text()) if PORT_FILE.exists() else PORT
        return {
            'pid': pid,
            'url': f"http://{HOST}:{port}",
            'log_file': str(LOG_FILE),
        }


def main():
    """Main entry point for server management."""
    import argparse

    parser = argparse.ArgumentParser(description="GSAS-II persistent server")
    subparsers = parser.add_subparsers(dest='action', help='Server action')

    subparsers.add_parser('start', help='Start the server')
    subparsers.add_parser('stop', help='Stop the server')
    subparsers.add_parser('status', help='Show server status')
    subparsers.add_parser('restart', help='Restart the server')
    subparsers.add_parser('logs', help='Show recent server logs')

    args = parser.parse_args()
    action = args.action or 'status'  # Default to status if no action

    if action == 'start':
        if is_server_running():
            print("Server is already running")
            print(f"   PID: {PID_FILE.read_text()}")
            port = int(PORT_FILE.read_text()) if PORT_FILE.exists() else PORT
            print(f"   URL: http://{HOST}:{port}")
            print(f"\n   To view logs: tail -f {LOG_FILE}")
            sys.exit(1)

        print("Starting GSAS-II server...")
        logger = setup_logging(LOG_FILE)
        server = GSASServer(logger)
        server.start()

    elif action == 'stop':
        stop_server()

    elif action == 'status':
        info = get_server_info()
        if info:
            print(f"Server is running")
            print(f"   PID: {info['pid']}")
            print(f"   URL: {info['url']}")
            print(f"   Log file: {info['log_file']}")
            if 'uptime' in info:
                print(f"   Uptime: {info['uptime']}")
            if 'cpu_percent' in info:
                print(f"   CPU: {info['cpu_percent']:.1f}%")
            if 'memory_mb' in info:
                print(f"   Memory: {info['memory_mb']:.1f} MB")
            print(f"\n   To view logs: tail -f {info['log_file']}")
        else:
            print("Server is not running")
            print(f"\n   To start: pixi run gsas-server start")
            print(f"   Log file: {LOG_FILE}")
            sys.exit(1)

    elif action == 'restart':
        if is_server_running():
            stop_server()
            time.sleep(1)

        print("Starting GSAS-II server...")
        logger = setup_logging(LOG_FILE)
        server = GSASServer(logger)
        server.start()

    elif action == 'logs':
        if not LOG_FILE.exists():
            print(f"Log file not created yet: {LOG_FILE}")
            sys.exit(0)

        print(f"Recent logs from {LOG_FILE}:")
        print("   (showing last 50 lines)")
        print("-" * 60)

        try:
            # Show last 50 lines
            with open(LOG_FILE, 'r') as f:
                lines = f.readlines()
                for line in lines[-50:]:
                    print(line.rstrip())
        except Exception as e:
            print(f"Failed to read log file: {e}")
            sys.exit(1)

        print("-" * 60)
        print(f"   To follow logs in real-time: tail -f {LOG_FILE}")


if __name__ == '__main__':
    main()

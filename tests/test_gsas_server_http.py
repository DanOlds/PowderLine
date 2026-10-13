"""Tests for GSAS-II server HTTP endpoints.

These tests verify that the FastAPI server exposes the correct HTTP API
and handles requests properly.

NOTE: These are integration tests that require a running server.
They can be run in two ways:

1. Manual server start (recommended):
   ```bash
   pixi run gsas-server start
   pixi run pytest tests/test_gsas_server_http.py -v
   pixi run gsas-server stop
   ```

2. Auto-start in fixture (slower, may be flaky):
   Just run pytest and the fixture will attempt to start a test server.
"""

import pytest
import time
import subprocess
import sys
from pathlib import Path
import tempfile
import os


def is_server_running(port: int = 19471) -> bool:
    """Check if server is running on given port."""
    import httpx
    try:
        resp = httpx.get(f"http://127.0.0.1:{port}/health", timeout=1.0)
        return resp.status_code == 200
    except (httpx.ConnectError, httpx.TimeoutException, OSError):
        return False


@pytest.fixture(scope="module")
def server_port():
    """Use default port or find available port for testing."""
    # First try default port (19471)
    if is_server_running(19471):
        yield 19471
        return

    # If default port not available, try to find a free port
    import socket
    sock = socket.socket()
    sock.bind(('', 0))
    port = sock.getsockname()[1]
    sock.close()
    yield port


@pytest.fixture(scope="module")
def test_server(server_port):
    """Ensure test server is running on the specified port.

    If server is already running on the port, use it.
    Otherwise, attempt to start one for testing.
    """
    # Check if already running
    if is_server_running(server_port):
        yield server_port
        return

    # Try to start test server
    src_dir = Path(__file__).parent.parent / 'src'
    server_script = src_dir / 'powderline' / 'gateways' / 'gsasii' / 'server.py'

    if not server_script.exists():
        pytest.skip(f"Server script not found: {server_script}")

    # Set custom port via environment
    env = os.environ.copy()
    env['PYTHONPATH'] = str(src_dir) + os.pathsep + env.get('PYTHONPATH', '')
    env['POWDERLINE_SERVER_PORT'] = str(server_port)

    # Start server process
    proc = subprocess.Popen(
        [sys.executable, str(server_script), 'start'],
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL
    )

    # Wait for server to start (up to 15 seconds for GSAS-II import)
    started = False
    for attempt in range(150):
        time.sleep(0.1)
        if is_server_running(server_port):
            started = True
            break
        # Check if process died
        if proc.poll() is not None:
            pytest.skip(f"Test server exited unexpectedly (exit code: {proc.returncode})")

    if not started:
        proc.kill()
        pytest.skip(f"Test server failed to start within 15 seconds on port {server_port}")

    yield server_port

    # Cleanup - only kill if we started it
    proc.terminate()
    try:
        proc.wait(timeout=5)
    except subprocess.TimeoutExpired:
        proc.kill()


def test_health_endpoint(test_server):
    """Test /health endpoint returns correct format."""
    import httpx

    port = test_server
    resp = httpx.get(f"http://127.0.0.1:{port}/health", timeout=2.0)

    assert resp.status_code == 200, "Health endpoint should return 200"

    data = resp.json()
    assert "status" in data, "Response should contain 'status' field"
    assert data["status"] == "ok", "Status should be 'ok'"
    assert "pid" in data, "Response should contain 'pid' field"
    assert "uptime_seconds" in data, "Response should contain 'uptime_seconds' field"
    assert "request_count" in data, "Response should contain 'request_count' field"

    assert isinstance(data["pid"], int), "PID should be integer"
    assert data["pid"] > 0, "PID should be positive"
    assert data["uptime_seconds"] >= 0, "Uptime should be non-negative"
    assert data["request_count"] >= 0, "Request count should be non-negative"


def test_health_endpoint_format(test_server):
    """Verify health endpoint returns proper JSON structure."""
    import httpx

    port = test_server
    resp = httpx.get(f"http://127.0.0.1:{port}/health", timeout=2.0)

    # Verify response is valid JSON
    data = resp.json()

    # Verify all expected fields are present
    required_fields = {"status", "pid", "uptime_seconds", "request_count"}
    assert required_fields.issubset(data.keys()), \
        f"Missing required fields: {required_fields - data.keys()}"


def test_simulate_endpoint_missing_fields(test_server):
    """Test /simulate endpoint validates required fields."""
    import httpx

    port = test_server

    # Missing recipe_data
    resp = httpx.post(
        f"http://127.0.0.1:{port}/simulate",
        json={"output_dir": "/tmp/test"},
        timeout=5.0
    )
    assert resp.status_code == 422, "Should return validation error for missing recipe_data"

    # Missing output_dir
    resp = httpx.post(
        f"http://127.0.0.1:{port}/simulate",
        json={"recipe_data": {}},
        timeout=5.0
    )
    assert resp.status_code == 422, "Should return validation error for missing output_dir"

    # Empty request
    resp = httpx.post(
        f"http://127.0.0.1:{port}/simulate",
        json={},
        timeout=5.0
    )
    assert resp.status_code == 422, "Should return validation error for empty request"


def test_simulate_endpoint_invalid_recipe(test_server):
    """Test /simulate endpoint handles invalid recipe_data gracefully."""
    import httpx

    port = test_server

    # Invalid (empty) recipe_data — server should return 200 with success=False
    with tempfile.TemporaryDirectory() as tmpdir:
        resp = httpx.post(
            f"http://127.0.0.1:{port}/simulate",
            json={
                "recipe_data": {},
                "output_dir": tmpdir
            },
            timeout=5.0
        )

        assert resp.status_code == 200, "Should return 200 even for errors (error in response body)"
        data = resp.json()

        assert "success" in data, "Response should contain 'success' field"
        assert data["success"] is False, "Should indicate failure"
        assert "error" in data, "Response should contain 'error' field"
        assert "recipe" in data["error"].lower() or "validation" in data["error"].lower(), \
            "Error message should mention recipe validation failure"


def test_simulate_endpoint_optional_verbose(test_server):
    """Test /simulate endpoint accepts optional verbose parameter."""
    import httpx

    port = test_server

    with tempfile.TemporaryDirectory() as tmpdir:
        # Request with verbose=true — should accept and return 200 (even if recipe invalid)
        resp = httpx.post(
            f"http://127.0.0.1:{port}/simulate",
            json={
                "recipe_data": {},
                "output_dir": tmpdir,
                "verbose": True
            },
            timeout=5.0
        )

        assert resp.status_code == 200, "Should accept verbose parameter"

        # Request with verbose=false
        resp = httpx.post(
            f"http://127.0.0.1:{port}/simulate",
            json={
                "recipe_data": {},
                "output_dir": tmpdir,
                "verbose": False
            },
            timeout=5.0
        )

        assert resp.status_code == 200, "Should accept verbose parameter"


def test_health_endpoint_performance(test_server):
    """Test that health endpoint responds quickly."""
    import httpx

    port = test_server

    start = time.time()
    resp = httpx.get(f"http://127.0.0.1:{port}/health", timeout=2.0)
    elapsed = time.time() - start

    assert resp.status_code == 200
    assert elapsed < 0.1, f"Health check should be fast, took {elapsed:.2f}s"


def test_multiple_health_checks(test_server):
    """Test that multiple health checks work correctly."""
    import httpx

    port = test_server

    # Multiple rapid health checks
    for _ in range(10):
        resp = httpx.get(f"http://127.0.0.1:{port}/health", timeout=2.0)
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "ok"


def test_server_increments_request_count(test_server):
    """Test that server tracks request count correctly."""
    import httpx

    port = test_server

    # Get initial count (might not be 0 due to startup checks)
    resp = httpx.get(f"http://127.0.0.1:{port}/health", timeout=2.0)
    initial_count = resp.json()["request_count"]

    # Make a simulation request (will fail validation but should still increment counter)
    with tempfile.TemporaryDirectory() as tmpdir:
        httpx.post(
            f"http://127.0.0.1:{port}/simulate",
            json={
                "recipe_data": {},
                "output_dir": tmpdir
            },
            timeout=5.0
        )

    # Check count increased
    resp = httpx.get(f"http://127.0.0.1:{port}/health", timeout=2.0)
    new_count = resp.json()["request_count"]

    assert new_count > initial_count, "Request count should increment after simulation request"

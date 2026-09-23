"""Run all Python tests with the real, isolated Go provider-setting handler.

Execute inside the read-only Python test container with --network none. Mount
the cross-compiled cmd/server test binary at /test-server. This never runs
Market's main(), strategies, scheduler, broker, or model clients.
"""
import os
import subprocess
import time
import urllib.request

import pytest


def main():
    os.environ["BACKEND_URL"] = "http://127.0.0.1:18080"
    child = subprocess.Popen(
        ["/test-server", "-test.run=^TestProviderHTTPFixture$", "-test.timeout=120s"],
        env={"PROVIDER_HTTP_TEST": "1"},
    )
    try:
        deadline = time.monotonic() + 10
        while True:
            if child.poll() is not None:
                raise RuntimeError("Isolated provider fixture exited before readiness")
            try:
                with urllib.request.urlopen(os.environ["BACKEND_URL"] + "/healthz", timeout=0.5) as response:
                    if response.status == 200:
                        break
            except OSError:
                if time.monotonic() >= deadline:
                    raise RuntimeError("Isolated provider fixture did not become ready") from None
                time.sleep(0.05)
        return pytest.main(["ai-brain/tests", "tests", "-q", "-rs", "-p", "no:cacheprovider"])
    finally:
        child.terminate()
        try:
            child.wait(timeout=5)
        except subprocess.TimeoutExpired:
            child.kill()
            child.wait()


if __name__ == "__main__":
    raise SystemExit(main())

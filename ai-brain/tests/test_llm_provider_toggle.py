"""
Tests for the LLM provider toggle feature.

Unit tests: router.py use_aws flag routing logic.
E2E tests:  /api/llm-provider endpoint (requires backend running on BACKEND_URL).
"""
import os
import sys
import json
import unittest
from unittest.mock import MagicMock, patch

# Ensure ai-brain is on the path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))


# ── Unit tests: LLMRouter.use_aws flag ────────────────────────────────────────

class TestLLMRouterUseAwsFlag(unittest.TestCase):

    def _make_router(self):
        """Create an LLMRouter with mocked clients."""
        from agents.router import LLMRouter, Complexity
        router = LLMRouter.__new__(LLMRouter)
        # Manually init without hitting real clients
        router.ollama_model  = "qwen3:4b"
        router.ollama_host   = "http://127.0.0.1:11434"
        router.bedrock_model = "anthropic.claude-3-5-sonnet-20241022-v2:0"
        router.aws_region    = "us-east-1"
        router.use_aws       = True
        router._ollama_client = MagicMock()
        router._ollama_client.chat.return_value = MagicMock(
            message=MagicMock(content='{"action":"mock_ollama"}')
        )
        router._bedrock = MagicMock()
        router._bedrock.invoke.return_value = MagicMock(
            content='{"action":"mock_bedrock"}'
        )
        return router, Complexity

    def test_default_use_aws_is_true(self):
        router, _ = self._make_router()
        self.assertTrue(router.use_aws)

    def test_high_complexity_uses_bedrock_when_aws_on(self):
        router, Complexity = self._make_router()
        router.use_aws = True
        router.complete("sys", "user", complexity=Complexity.HIGH)
        router._bedrock.invoke.assert_called_once()
        router._ollama_client.chat.assert_not_called()

    def test_high_complexity_falls_back_to_ollama_when_aws_off(self):
        router, Complexity = self._make_router()
        router.use_aws = False
        router.complete("sys", "user", complexity=Complexity.HIGH)
        router._ollama_client.chat.assert_called_once()
        router._bedrock.invoke.assert_not_called()

    def test_low_complexity_always_uses_ollama(self):
        router, Complexity = self._make_router()
        for aws_state in (True, False):
            router.use_aws = aws_state
            router._ollama_client.reset_mock()
            router._bedrock.reset_mock()
            router.complete("sys", "user", complexity=Complexity.LOW)
            router._ollama_client.chat.assert_called_once()
            router._bedrock.invoke.assert_not_called()

    def test_model_tag_bedrock_when_aws_on(self):
        router, Complexity = self._make_router()
        router.use_aws = True
        tag = router.model_tag(Complexity.HIGH)
        self.assertTrue(tag.startswith("bedrock/"), f"Expected bedrock/ prefix, got: {tag}")

    def test_model_tag_ollama_when_aws_off(self):
        router, Complexity = self._make_router()
        router.use_aws = False
        tag = router.model_tag(Complexity.HIGH)
        self.assertTrue(tag.startswith("ollama/"), f"Expected ollama/ prefix, got: {tag}")


# ── E2E tests: /api/llm-provider HTTP endpoint ────────────────────────────────

try:
    import httpx
    _httpx_available = True
except ImportError:
    _httpx_available = False

BACKEND_URL = os.getenv("BACKEND_URL", "http://localhost:8080")


@unittest.skipUnless(_httpx_available, "httpx not installed")
class TestLLMProviderEndpointE2E(unittest.TestCase):
    """
    These tests require the Go backend to be running.
    Skip automatically if the backend is unreachable.
    """

    @classmethod
    def setUpClass(cls):
        try:
            resp = httpx.get(f"{BACKEND_URL}/healthz", timeout=2)
            cls.backend_up = resp.status_code == 200
        except Exception:
            cls.backend_up = False
        if not cls.backend_up:
            raise unittest.SkipTest(f"Backend not reachable at {BACKEND_URL}")

    def _get(self):
        return httpx.get(f"{BACKEND_URL}/api/llm-provider", timeout=5)

    def _set(self, provider: str):
        return httpx.post(
            f"{BACKEND_URL}/api/llm-provider",
            json={"provider": provider},
            timeout=5,
        )

    def test_get_returns_valid_provider(self):
        resp = self._get()
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertIn("provider", data)
        self.assertIn(data["provider"], ("aws", "local"))

    def test_set_local_and_read_back(self):
        resp = self._set("local")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()["provider"], "local")

        resp = self._get()
        self.assertEqual(resp.json()["provider"], "local")

    def test_set_aws_and_read_back(self):
        resp = self._set("aws")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()["provider"], "aws")

        resp = self._get()
        self.assertEqual(resp.json()["provider"], "aws")

    def test_invalid_provider_rejected(self):
        resp = self._set("gcp")
        self.assertEqual(resp.status_code, 400)

    def test_toggle_cycle_local_aws_local(self):
        self._set("local")
        self.assertEqual(self._get().json()["provider"], "local")
        self._set("aws")
        self.assertEqual(self._get().json()["provider"], "aws")
        self._set("local")
        self.assertEqual(self._get().json()["provider"], "local")

    def tearDown(self):
        # Reset to local after each test (saves money)
        self._set("local")


if __name__ == "__main__":
    unittest.main(verbosity=2)

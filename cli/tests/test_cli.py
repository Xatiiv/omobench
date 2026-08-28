from __future__ import annotations

import unittest
from pathlib import Path

from omobench.cli import build_guidellm_command, redact_endpoint


class GuideLLMCommandTests(unittest.TestCase):
    def test_chat_small_is_a_fixed_single_stream_workload(self) -> None:
        command = build_guidellm_command(
            endpoint="http://localhost:8000/v1",
            model="Qwen/Qwen3-4B",
            profile_id="chat-small-v1",
            raw_output_path=Path("/tmp/benchmarks.json"),
        )
        self.assertIn("kind=synchronous,warmup=0.1,cooldown=0.1", command)
        self.assertIn("kind=synthetic_text,prompt_tokens=1024,output_tokens=256", command)
        self.assertIn("kind=max_requests,count=50", command)
        self.assertIn("kind=generative,sample_size=0", command)

    def test_throughput_is_fixed_at_sixteen_streams(self) -> None:
        command = build_guidellm_command(
            endpoint="http://localhost:8000/v1",
            model="Qwen/Qwen3-4B",
            profile_id="throughput-v1",
            raw_output_path=Path("/tmp/benchmarks.json"),
        )
        self.assertIn(
            "kind=concurrent,streams=16,max_concurrency=16,warmup=0.1,cooldown=0.1",
            command,
        )
        self.assertIn("kind=max_requests,count=160", command)

    def test_local_endpoint_is_redacted_before_evidence_is_written(self) -> None:
        value = "server=http://localhost:8000/v1 and again http://localhost:8000/v1"
        self.assertEqual(
            redact_endpoint(value, "http://localhost:8000/v1"),
            "server=<LOCAL_ENDPOINT> and again <LOCAL_ENDPOINT>",
        )


if __name__ == "__main__":
    unittest.main()

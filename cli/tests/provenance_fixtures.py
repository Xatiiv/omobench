"""Synthetic evidence for the serving-provenance tests. Never submitted.

Every byte here is made up. The report is the same canonical GuideLLM fixture
the existing packaging tests use, so one shape stays authoritative; what this
module adds is the two arrangements that shape can arrive in -- one host that
both served and measured, and a Windows host serving while a WSL2 runner
measures through the bridge.
"""
from __future__ import annotations

import json
from pathlib import Path

from test_cli import build_staged_run_directory

SYNTHETIC = "SYNTHETIC FIXTURE - not a measurement of any real system"

# What the bridge itself writes into environment.json while a run executes.
BRIDGE_PROVENANCE = {
    "kind": "windows-stdio-http1-v1",
    "windows_port": 1234,
    "runner_host": "WSL2",
    "serving_host": "Windows loopback, hardware not independently verified",
    "protocol": "HTTP/1.1, one request per connection",
    "overhead": "Included in measured latency and throughput, not subtracted",
    "publication": "local-only",
}

# A WSL2 runner's own snapshot. It knows nothing about the GPU that served the
# model, which is exactly why the bridged case needs a separate declaration.
WSL_ENVIRONMENT = {
    "omobench_cli_version": "0.1.0",
    "python": "3.12.3",
    "operating_system": "Linux-6.6.87.2-microsoft-standard-WSL2-x86_64-with-glibc2.39",
    "cpu": "x86_64",
    "ram_gb": 31.2,
    "guidellm": "guidellm version: 0.7.3",
    "gpus": [],
}

SINGLE_HOST_ENVIRONMENT = {
    **WSL_ENVIRONMENT,
    "operating_system": "Linux-6.8.0-generic-x86_64-with-glibc2.39",
    "cpu": "AMD Ryzen 9 7950X",
    "ram_gb": 64.0,
    "gpus": [{"vendor": "nvidia", "model": "NVIDIA GeForce RTX 4090",
              "memory_gb": 24.0, "driver_version": "560.35"}],
}


def bridged_run(root: Path, *, report: dict | None = None) -> Path:
    """A finished run whose server was on Windows and whose runner was in WSL."""
    return build_staged_run_directory(
        root, environment={**WSL_ENVIRONMENT, "transport": dict(BRIDGE_PROVENANCE)},
        report=report)


def single_host_run(root: Path, *, report: dict | None = None) -> Path:
    """A finished run where one machine both served and measured."""
    return build_staged_run_directory(root, environment=dict(SINGLE_HOST_ENVIRONMENT),
                                      report=report)


def bridged_declaration(**overrides) -> dict:
    """The AMD desktop arrangement: LM Studio on Windows, the runner in WSL."""
    declaration = {
        "serving": {
            "relationship": "windows-host-wsl-runner",
            "host": {"os": "Windows 11 Pro 10.0.26200", "cpu": "AMD Ryzen 7 9800X3D",
                     "ram_gb": 64.0},
            "accelerators": [{"vendor": "amd", "model": "AMD Radeon RX 9070",
                              "memory_gb": 16.0, "count": 1, "role": "inference"}],
            "placement": {"mode": "gpu-only"},
            "runtime": {
                "application": {"name": "LM Studio", "version": "0.3.31"},
                "engine": {"name": "llama.cpp", "version": "b10621"},
                "settings": [{"name": "context_length", "value": "8192"},
                             {"name": "gpu_layers", "value": "49"}],
            },
        },
        "served_model_id": "deepseek-r1-distill-qwen-14b",
        "tokenizer": {"source": "deepseek-ai/DeepSeek-R1-Distill-Qwen-14B",
                      "kind": "huggingface", "loaded": "loaded-before-run",
                      "identity": {"class": "LlamaTokenizerFast", "vocabulary_size": 152064}},
        "catalog_status": "catalogued",
    }
    declaration.update(overrides)
    return declaration


def single_host_declaration(**overrides) -> dict:
    declaration = {
        "serving": {
            "relationship": "same-host",
            "accelerators": [{"vendor": "nvidia", "model": "NVIDIA GeForce RTX 4090",
                              "memory_gb": 24.0, "count": 1, "role": "inference"}],
            "placement": {"mode": "gpu-only"},
            "runtime": {"engine": {"name": "vllm", "version": "0.11.0"}},
        },
        "catalog_status": "catalogued",
    }
    declaration.update(overrides)
    return declaration


def write_declaration(path: Path, declaration: dict) -> str:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(declaration, indent=2) + "\n", encoding="utf-8")
    return str(path)

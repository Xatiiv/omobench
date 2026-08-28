export type DemoResult = {
  framework: string;
  hardware: string;
  id: string;
  model: string;
  outputTokensPerSecond: string;
  profile: string;
  quantization: string;
  ttft: string;
};

// These rows exist only to exercise the comparison interface locally. They are
// intentionally not stored under results/ and must never be treated as evidence.
export const demoResults: DemoResult[] = [
  {
    id: "qwen3-4b-example-gpu-a-vllm-chat-small",
    model: "Qwen3-4B",
    hardware: "Example GPU A · 24 GB",
    framework: "vLLM",
    quantization: "BF16",
    ttft: "218 ms",
    outputTokensPerSecond: "91.4",
    profile: "chat-small-v1",
  },
  {
    id: "qwen3-4b-example-gpu-b-sglang-chat-small",
    model: "Qwen3-4B",
    hardware: "Example GPU B · 16 GB",
    framework: "SGLang",
    quantization: "BF16",
    ttft: "246 ms",
    outputTokensPerSecond: "84.7",
    profile: "chat-small-v1",
  },
  {
    id: "qwen3-4b-example-local-gpu-llama-cpp-chat-small",
    model: "Qwen3-4B",
    hardware: "Example local GPU · 8 GB",
    framework: "llama.cpp",
    quantization: "Q4_K_M",
    ttft: "522 ms",
    outputTokensPerSecond: "31.2",
    profile: "chat-small-v1",
  },
  {
    id: "llama-3-1-8b-example-gpu-a-vllm-chat-small",
    model: "Llama 3.1 8B Instruct",
    hardware: "Example GPU A · 24 GB",
    framework: "vLLM",
    quantization: "BF16",
    ttft: "265 ms",
    outputTokensPerSecond: "80.3",
    profile: "chat-small-v1",
  },
  {
    id: "qwen3-32b-example-gpu-cluster-sglang-chat-long",
    model: "Qwen3-32B",
    hardware: "Example GPU cluster · 160 GB",
    framework: "SGLang",
    quantization: "BF16",
    ttft: "426 ms",
    outputTokensPerSecond: "73.6",
    profile: "chat-long-v1",
  },
  {
    id: "mistral-small-example-gpu-cluster-vllm-throughput",
    model: "Mistral Small 3.1 24B",
    hardware: "Example GPU cluster · 160 GB",
    framework: "vLLM",
    quantization: "BF16",
    ttft: "310 ms",
    outputTokensPerSecond: "267.5",
    profile: "throughput-v1",
  },
];

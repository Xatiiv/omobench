export type DemoResult = {
  framework: string;
  hardware: string;
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
    model: "Qwen3-4B",
    hardware: "Example GPU A · 24 GB",
    framework: "vLLM",
    quantization: "BF16",
    ttft: "218 ms",
    outputTokensPerSecond: "91.4",
    profile: "chat-small-v1",
  },
  {
    model: "Qwen3-4B",
    hardware: "Example GPU B · 16 GB",
    framework: "SGLang",
    quantization: "BF16",
    ttft: "246 ms",
    outputTokensPerSecond: "84.7",
    profile: "chat-small-v1",
  },
  {
    model: "Qwen3-4B",
    hardware: "Example local GPU · 8 GB",
    framework: "llama.cpp",
    quantization: "Q4_K_M",
    ttft: "522 ms",
    outputTokensPerSecond: "31.2",
    profile: "chat-small-v1",
  },
];

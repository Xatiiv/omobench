# Reproduce this local staging run

GuideLLM version: `0.7.3`

Replace `<LOCAL_ENDPOINT>` with the local OpenAI-compatible endpoint.

```bash
guidellm run --backend kind=openai_http,target=<LOCAL_ENDPOINT>,model=Qwen/Qwen3-0.6B,request_format=/v1/chat/completions --data kind=synthetic_text,prompt_tokens=1024,output_tokens=256 --profile kind=synchronous,warmup=0.1,cooldown=0.1 --constraint kind=max_requests,count=50 --constraint kind=max_duration,seconds=900 --constraint kind=max_error_rate,rate=0.01 --seed kind=static,value=20260828 --metrics kind=generative,sample_size=0 --output kind=json,path=/mnt/c/Users/vitta/Code/omobench/.omobench-runs/run-49f0510010c1/guidellm-benchmarks.json --disable-console-interactive
```

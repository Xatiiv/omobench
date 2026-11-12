# Reproduce this local staging run

GuideLLM version: `0.7.3`

Transport: experimental Windows stdio bridge, HTTP/1.1, local reports only. Its latency is included. Reopen through the updated companion and select Windows bridge with Windows port 1234; the private ephemeral endpoint below cannot be reused directly.

Server model identifier: `gemma-4-e4b-uncensored-hauhaucs-aggressive`

Tokenizer source (explicit override): `google/gemma-4-e4b-it`

Replace `<LOCAL_ENDPOINT>` with the local OpenAI-compatible endpoint.

```bash
guidellm run --backend kind=openai_http,target=<LOCAL_ENDPOINT>,model=gemma-4-e4b-uncensored-hauhaucs-aggressive,request_format=/v1/chat/completions,follow_redirects=false,timeout=300.0,http2=false --tokenizer kind=huggingface_auto,model=google/gemma-4-e4b-it --data kind=synthetic_text,prompt_tokens=1024,output_tokens=256 --profile kind=synchronous,warmup.value=4,warmup.mode=requests,cooldown.value=4,cooldown.mode=requests --constraint kind=max_requests,count=60 --constraint kind=max_duration,seconds=900 --constraint kind=max_error_rate,rate=0.01 --seed kind=static,value=20260828 --metrics kind=generative,sample_size=0 --output kind=json,path=<RUN_DIRECTORY>/guidellm-benchmarks.json --disable-console-interactive
```

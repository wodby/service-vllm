# vLLM on Wodby

What Wodby sets up for vLLM on this service. It runs the official `vllm/vllm-openai` image, which serves one Hugging Face model through an OpenAI-compatible API on port 8000.

## Access

- Other services in the environment reach the API at `http://<vLLM service name>:8000/v1`.
- The service token `api_key` is passed as `VLLM_API_KEY`. Clients send it as a Bearer token. It protects the `/v1` routes; `/health`, `/metrics` and some other routes answer without it, so the service is meant to stay on a private network.
- Clients name the model by the "API model name" setting, not by the Hugging Face repository name.

## Settings

The model is chosen with settings on the service. They become `LLM_*` variables that the manifest passes to vLLM as command arguments; they are not variables vLLM reads by itself, so setting them has an effect only through these settings.

| Setting | Variable | Argument |
| --- | --- | --- |
| Hugging Face model | `LLM_MODEL_ID` | model |
| Model revision | `LLM_MODEL_REVISION` | `--revision` |
| API model name | `LLM_SERVED_MODEL` | `--served-model-name` |
| Maximum context length | `LLM_MAX_MODEL_LEN` | `--max-model-len` |
| GPUs used by each replica | `LLM_TENSOR_PARALLEL_SIZE` | `--tensor-parallel-size` |
| Fraction of GPU memory used by vLLM | `LLM_GPU_MEMORY_UTILIZATION` | `--gpu-memory-utilization` |
| Hugging Face token (optional, secret) | `HF_TOKEN` | read by the model download |

The model and its revision are changed together. The GPU count of the container must equal "GPUs used by each replica". Generation defaults are vLLM's own (`--generation-config vllm`), not the model repository's.

## Resources and updates

- Each replica requests one `nvidia.com/gpu` by default and runs only on Linux amd64 nodes.
- An update stops a replica before it starts the replacement, so that no extra GPU is needed. A single replica is unavailable during an update.
- Startup, which includes the model download, may take up to 29 minutes before the pod is considered failed.

## Data

Model files are downloaded into a cache at `/cache` (`HF_HOME`, `VLLM_CACHE_ROOT`). By default it is per-pod and limited to 20 GiB: it survives a container restart, is lost when the pod is replaced, and every replica downloads its own copy. The optional `model-cache` volume makes `/cache` persistent, so the model is not downloaded again after a pod is replaced; its claim attaches to one node, so it suits a single replica.

## Check the result

- `/health` on port 8000 answers when the model is loaded.
- `/v1/models` with the Bearer token lists the served model name.

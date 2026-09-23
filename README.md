# vLLM service

Run a Hugging Face model with the official vLLM image and an OpenAI-compatible
API. This service reuses Wodby's generic stateless chart; it does not build a
custom image or require KServe.

Status: initial 0.1.0 preview release. GPU runtime validation is still required
before production use.

## Requirements

- Wodby support for container GPU requests.
- Linux amd64 nodes with a GPU supported by vLLM 0.29.0's CUDA 13 image and a
  compatible NVIDIA driver/runtime. A machine having a GPU is not sufficient.
- A device plugin exposing `nvidia.com/gpu`. Do not install a second plugin or
  GPU Operator over an existing installation without checking compatibility.
- Enough allocatable GPU, CPU, RAM and local disk for every replica.
- Private-network app access, provided by the [vLLM stack](https://github.com/wodby/stack-vllm).

The service does not provision machines, install GPU drivers/operators, or
purchase external GPU capacity. AMD, CPU-only inference, MIG configuration,
multi-node tensor parallelism and GPU sharing are not part of this preset.

## Configure a model

The default is the small [Qwen3-0.6B model](https://huggingface.co/Qwen/Qwen3-0.6B),
pinned to a model commit for repeatable downloads. It is a smoke-test model,
not a recommended production quality or throughput target.

Change the model and revision settings together. Use an immutable model commit
for reproducibility. Set the optional secret Hugging Face token for a private
or gated model, and accept the model's license separately. Remote model code
execution is not enabled. Check vLLM's supported architectures before selecting
another model.

Clients use the served model name, `model` by default, independently of the
Hugging Face repository name. Context length defaults to 4096 tokens. The
service uses vLLM generation defaults rather than downloading a model-specific
generation configuration; callers can send sampling parameters explicitly.

The `LLM_*` environment variables are settings consumed by the container
argument list using Kubernetes expansion, without a shell. They are not vLLM
environment-variable conventions. Invalid numeric settings are rejected by
vLLM at startup.

## GPU count, replicas and updates

Each replica requests one `nvidia.com/gpu` by default. For single-node tensor
parallelism, change **both** the container GPU count and the tensor-parallel
setting to the same positive integer. A replica cannot combine GPUs across
nodes. Scaling to three replicas at two GPUs per replica requires six GPUs.
The chart defaults request two CPUs and 4 GiB RAM, with an 8 GiB memory limit.
Increase these values when changing to a larger model.

The device request does not select a GPU model or guarantee VRAM. Verify the
node's hardware and use node selectors/affinity when the cluster has mixed
GPU models. Kubernetes sharing configurations may change what one device
request represents. GPU-memory utilization is a vLLM process setting, not a
hardware reservation.

Updates allow one unavailable replica and no surge, so replacement does not
require an extra idle GPU. A single replica has downtime during updates.
Startup has a 29-minute probe budget within a 30-minute rollout deadline;
large downloads or slow nodes may need longer. Shutdown gets two minutes,
but requests longer than that may be interrupted. CPU-based autoscaling is
not a substitute for queue/token-aware scaling.

## Cache and security

Model and compilation files use a bounded 20 GiB per-pod ephemeral cache.
The cache survives container restarts within the same pod, but is lost when
the pod is replaced. Replicas download their own copies. Size local disk and
cache limits for the model; durable/shared model caching is not included yet.
`/dev/shm` uses a bounded memory-backed volume and counts toward pod memory.

Wodby generates the secret `api_key` token and supplies it as `VLLM_API_KEY`.
Clients send it as a Bearer token to `/v1` routes. The optional `HF_TOKEN`
setting is also secret. Neither secret is placed in command arguments.

**Keep the entire app private.** vLLM's API key protects inference routes,
not every utility or metrics route. Do not expose port 8000 directly to the
Internet. `/health` is unauthenticated for pod probes. `/metrics` is available
inside the private network; this service does not configure scraping or
GPU/DCGM dashboards.

The manifest declares a routable HTTP port so Application Access can select it.
The stack's required private-network policy suppresses its public route from
app creation. Reusing this service in another stack without that policy can
expose the server. Do not remove the policy without a separate secure gateway.

## Validation

Run `uv run --with pyyaml python -m unittest discover -s tests -v`. Tests pull
and render the pinned public Helm chart without connecting to a cluster.
They check selectors, GPU requests/limits, replicas, service-account mapping,
settings, probes and zero-surge rollout behavior. These tests do not load a
model or prove GPU/driver compatibility.

Before release, validate the service manifest against a Wodby environment
supporting GPU requests, then perform an authorized GPU smoke test: successful
startup, authenticated inference, streaming, model change, restart, scale and
GPU-exhaustion behavior. No such deployment is performed by this repository's
tests.

References: [vLLM Docker deployment](https://docs.vllm.ai/en/stable/deployment/docker/),
[GPU installation requirements](https://docs.vllm.ai/en/stable/getting_started/installation/gpu/).

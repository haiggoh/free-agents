# NVIDIA GPUs and CPU-only Linux (since 0.25.0)

Local inference on Linux or WSL2, as a platform variant of the Apple Silicon local lane. Moved here
from the README in 0.29.2, with the tier boundaries corrected against the code.

> **Test status.** Everything below is built and unit-tested with a faked `nvidia-smi`. The NVIDIA
> path has **not yet been run on a real GPU** (see the 0.25.x CHANGELOG entries). Treat it as a
> working design waiting on its first live qualification, not as a proven lane.

## Tiers

`bin/local-llm-hotswap.sh` picks the tier from `LA_VRAM_GB` (set by `la-hw-detect.sh`):

| Tier | Selected when | Strategy |
|---|---|---|
| **A** | CUDA and VRAM ≥ 16 GB | vLLM, fully VRAM-resident |
| **B** | CUDA and VRAM below 16 GB | AWQ/EXL2-named model dirs → vLLM with `--swap-space`; anything else (GGUF) → `llama-server` with a computed `-ngl` |
| **C** | no CUDA | `llama-server` in CPU mode |

Earlier docs labelled Tier B "≤ 8 GB". The code has no 8 GB boundary: a 12 GB card is Tier B. The
`-ngl` heuristic targets about 7.5 GB of layers, so on a 12–15 GB card it leaves VRAM unused. Set
`LA_LLAMA_NGL` to override it.

Two claims are narrower than they used to read:

- **`--swap-space`** is vLLM's CPU swap space for KV-cache blocks of pre-empted sequences. The
  launcher sizes it as system RAM − VRAM − 10 GB (minimum 4 GB). It does not offload model weights
  and it is not CUDA unified memory.
- **EXL2.** The launcher routes a directory whose name contains `exl2` to vLLM, but upstream vLLM
  does not load EXL2 weights. Use AWQ (or GGUF through llama.cpp) until this is qualified.

## Hardware detection (`bin/la-hw-detect.sh`)

Detects the OS (macOS; Linux including WSL2; it also recognises native Windows, Cygwin, MinGW and
MSYS, which the launchers do **not** support), the accelerator (Apple Silicon MLX, NVIDIA CUDA,
AMD ROCm, Intel, CPU-only), VRAM and driver/CUDA versions, CPU flags, RAM and swap, and
container/VM context. It exports `LA_*` variables for the other scripts. Only the MLX and CUDA
accelerators have serving backends here; ROCm and Intel are detected, not served.

## Backends

```bash
./install/install-backend.sh --all                # on Linux + CUDA: adds vllm-cuda and llama-cpp-cuda
./install/install-backend.sh --backend llama-cpp  # CPU only
./install/manage-cuda-backend.py list             # installed CUDA backends
./install/manage-cuda-backend.py install vllm     # vLLM with CUDA wheels matched to the host
./install/manage-cuda-backend.py switch vllm      # set the active backend (LA_CUDA_BIN)
```

Side-by-side venvs make a rollback a `switch`. The PyTorch wheel index is chosen from the host CUDA
version, and the smoke test requires `torch.cuda.is_available()` plus a real allocation.

| Script | Purpose |
|---|---|
| `la-vram-preflight.sh` | VRAM capacity check and tier recommendation |
| `la-evict.sh` | Emergency memory recovery; understands `/proc` and CUDA backends |
| `la-reboot.sh` | Restart a crashed server on the same port and argv from `/proc/PID/cmdline` |

## Models

The catalogue's 11th field, `hardware_target` (`mlx` | `cuda` | `any`), lets the downloader offer
only what this machine can serve. The shipped CUDA examples are `qwen2.5-7b-awq` (Qwen2.5 7B
Instruct AWQ, vLLM) and `nemotron-3-ultra-550b-gguf` (llama.cpp, about 300 GB).

To fetch a Hugging Face repo that is not in any catalogue:

```bash
./install/download-models.sh --hf-repo Qwen/Qwen2.5-7B-Instruct-AWQ --alias qwen-awq --include "*.safetensors"
```

`--include` is required, so one quantisation is fetched rather than a whole multi-format repo. The
row goes through the same engine as catalogue rows (dedupe, disk check, resume).

#!/usr/bin/env bash
# 数字人 P0 实测：GPU 机公共变量与函数（被其它脚本 source，不单独执行）
set -euo pipefail

BENCH_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# 代码与权重目录：三套模型权重约 145GB，加 conda 环境与输出建议磁盘 ≥ 250GB
WORK_DIR="${WORK_DIR:-/root/avatar-bench}"
INPUT_DIR="${INPUT_DIR:-$BENCH_DIR/inputs}"
RESULT_DIR="${RESULT_DIR:-$BENCH_DIR/results}"
CONDA_HOME="${CONDA_HOME:-$HOME/miniforge3}"
NGPU="${NGPU:-$( (nvidia-smi -L 2>/dev/null || true) | wc -l | tr -d ' ')}"
# 单卡显存（MiB）低于 40GB 时自动走低显存模式（如 RTX 3090/4090 24GB）
GPU_MEM_MIB="$( (nvidia-smi --query-gpu=memory.total --format=csv,noheader,nounits 2>/dev/null || true) | head -1 | tr -d ' ')"
if [[ -z "${LOW_VRAM:-}" ]]; then
  LOW_VRAM=0
  [[ -n "$GPU_MEM_MIB" && "$GPU_MEM_MIB" -lt 40000 ]] && LOW_VRAM=1
fi

# 国内网络：USE_CN_MIRROR=1 时 apt/pip/conda/HF 全部走国内镜像
#   权重优先从 ModelScope 下载（国内通常比 hf-mirror 快且稳定），ModelScope 没有的仓库再走 hf-mirror
if [[ "${USE_CN_MIRROR:-0}" == "1" ]]; then
  export USE_CN_MIRROR USE_HF_MIRROR=1
  export PIP_INDEX_URL="${PIP_INDEX_URL:-https://mirrors.aliyun.com/pypi/simple/}"
  export UV_DEFAULT_INDEX="$PIP_INDEX_URL"
fi
export UV_HTTP_TIMEOUT="${UV_HTTP_TIMEOUT:-300}"

# 安装分阶段：SETUP_STAGE=env 只装环境，=weights 只下权重，默认两者都做（便于两条线并行）
want_env() { [[ "${SETUP_STAGE:-all}" != "weights" ]]; }
want_weights() { [[ "${SETUP_STAGE:-all}" != "env" ]]; }

# pip install 优先用 uv（并发下载、自动重试，大包不易卡在慢节点上），失败再退回原生 pip
pip() {
  if [[ "${1:-}" == "install" ]] && command -v uv >/dev/null 2>&1; then
    shift
    uv pip install --python "$(command -v python)" "$@" && return 0
    log "uv 安装失败，改用 pip 重试：$*"
    command pip install "$@"
  else
    command pip "$@"
  fi
}
# 国内云机器：USE_HF_MIRROR=1 走 hf-mirror 镜像下载权重
if [[ "${USE_HF_MIRROR:-0}" == "1" ]]; then
  export HF_ENDPOINT="${HF_ENDPOINT:-https://hf-mirror.com}"
fi

# 带时间戳的日志
log() { echo "[$(date +%H:%M:%S)] $*"; }

# 激活指定 conda 环境（脚本内 set -u 下 conda 脚本会读未定义变量，临时关闭）
conda_on() {
  set +u
  # shellcheck disable=SC1091
  source "$CONDA_HOME/etc/profile.d/conda.sh"
  conda activate "$1"
  set -u
}

# 若环境不存在则按给定 python 版本创建
conda_ensure() {
  local name="$1" py="$2"
  set +u
  # shellcheck disable=SC1091
  source "$CONDA_HOME/etc/profile.d/conda.sh"
  if ! conda env list | awk '{print $1}' | grep -qx "$name"; then
    conda create -y -n "$name" "python=$py"
  fi
  set -u
}

# 安装指定 CUDA 版本的 torch 系列包
# 用法：pip_torch <cu121|cu124> 包...
# 国内镜像下直接走 PyPI 镜像：PyPI 上 torch 2.4.x 默认即 cu121、2.6.x 默认即 cu124
pip_torch() {
  local cu="$1"
  shift
  if [[ "${USE_CN_MIRROR:-0}" == "1" ]]; then
    pip install "$@"
  else
    pip install "$@" --index-url "https://download.pytorch.org/whl/$cu"
  fi
}

# 安装 flash-attn 2.7.4.post1：优先直接装 GitHub 官方预编译 wheel（无需 nvcc），失败再源码编译
# 用法：pip_flash_attn <torch 主次版本，如 2.6>
pip_flash_attn() {
  local tv="$1" py
  py="$(python -c 'import sys; print(f"cp{sys.version_info[0]}{sys.version_info[1]}")')"
  local name="flash_attn-2.7.4.post1+cu12torch${tv}cxx11abiFALSE-${py}-${py}-linux_x86_64.whl"
  local url="https://github.com/Dao-AILab/flash-attention/releases/download/v2.7.4.post1/$name"
  local dest="$WORK_DIR/wheels/$name" p
  mkdir -p "$WORK_DIR/wheels"
  # 国内直连 GitHub 常只有几十 KB/s，先试加速镜像；低于 50KB/s 持续 60 秒即换下一个源，支持断点续传
  local prefixes=("")
  [[ "${USE_CN_MIRROR:-0}" == "1" ]] && prefixes=("https://ghfast.top/" "https://gh-proxy.com/" "")
  if [[ ! -f "$dest" ]]; then
    for p in "${prefixes[@]}"; do
      curl -fL --retry 3 -C - --speed-limit 51200 --speed-time 60 -o "$dest.part" "$p$url" && mv "$dest.part" "$dest" && break
    done
  fi
  if [[ -f "$dest" ]] && pip install "$dest"; then return 0; fi
  MAX_JOBS="${MAX_JOBS:-8}" pip install flash_attn==2.7.4.post1 --no-build-isolation
}

# 下载 HuggingFace 仓库到本地目录
# 用法：hf_get <repo> <dir> [--revision REV] [文件模式...]
hf_get() {
  local repo="$1" dir="$2"
  shift 2
  (
    conda_on bench-tools
    HF_HUB_ENABLE_HF_TRANSFER=1 python - "$repo" "$dir" "$@" <<'PY'
import os
import sys

repo, local_dir, *rest = sys.argv[1:]
revision, patterns = None, []
it = iter(rest)
for arg in it:
    if arg == "--revision":
        revision = next(it)
    else:
        patterns.append(arg)

# 国内网络先试 ModelScope（同名仓库，主分支；HF 的 PR 分支文件在 ModelScope 主分支上已有）
if os.environ.get("USE_CN_MIRROR") == "1":
    try:
        from modelscope import snapshot_download as ms_download
        ms_download(repo, local_dir=local_dir, allow_patterns=patterns or None, max_workers=8)
        print(f"ok(modelscope) {repo} -> {local_dir}")
        sys.exit(0)
    except Exception as exc:  # noqa: BLE001 - 仓库不存在或网络错误都回退 HF
        print(f"ModelScope 下载失败，改用 HuggingFace：{type(exc).__name__}: {exc}")

from huggingface_hub import snapshot_download

snapshot_download(repo, local_dir=local_dir, revision=revision, allow_patterns=patterns or None)
print(f"ok {repo} -> {local_dir}")
PY
  )
}

# 后台每秒采样各卡显存，写入 CSV（index, memory.used MiB）
gpu_mon_start() {
  nvidia-smi --query-gpu=index,memory.used --format=csv,noheader,nounits -lms 1000 >"$1" &
  GPU_MON_PID=$!
}

# 停止显存采样
gpu_mon_stop() {
  kill "${GPU_MON_PID:-0}" 2>/dev/null || true
  wait "${GPU_MON_PID:-0}" 2>/dev/null || true
}

# 读取媒体时长（秒，浮点）
media_seconds() {
  ffprobe -v error -show_entries format=duration -of csv=p=0 "$1"
}

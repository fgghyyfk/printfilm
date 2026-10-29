#!/usr/bin/env bash
# 启动数字人 GPU 推理服务（前台运行）。前提：已用 bench/setup_all.sh 装好 LongCat、InfiniteTalk 环境与权重。
#
# 用法：
#   AVATAR_API_KEY=<随机串> bash start.sh                  # 默认监听 0.0.0.0:8780，默认引擎 infinitetalk
#   AVATAR_PRELOAD=1 AVATAR_DEFAULT_ENGINE=longcat bash start.sh   # 启动即预加载 LongCat
#
# 环境变量：AVATAR_PORT、AVATAR_DATA_DIR（任务与成片目录，默认 server/data）、AVATAR_KEEP_SECONDS（成片保留，默认 3 天）
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
BENCH="$HERE/../bench"
source "$BENCH/lib.sh"   # WORK_DIR、CONDA_HOME、LOW_VRAM 等与安装脚本一致

# 低显存补丁：每次启动从官方原版重打，代码更新后也不会叠加
conda_on longcat-video
python3 "$BENCH/patch_longcat_lowvram.py" "$WORK_DIR/LongCat-Video"
python3 "$BENCH/patch_infinitetalk_lowmem.py" "$WORK_DIR/InfiniteTalk"

export WORK_DIR CONDA_HOME AVATAR_LOW_VRAM="$LOW_VRAM"
# WSL2 下 cudaMallocAsync 能用到更多显存；原生 Linux 用 expandable_segments（见 bench/README）
if grep -qi microsoft /proc/version 2>/dev/null; then
  export PYTORCH_CUDA_ALLOC_CONF=backend:cudaMallocAsync
else
  export PYTORCH_CUDA_ALLOC_CONF=expandable_segments:True
fi
[[ -z "${AVATAR_API_KEY:-}" ]] && log "警告：未设置 AVATAR_API_KEY，接口不鉴权，只能在内网使用"

# 服务本身跑在轻量的 bench-tools 环境里，模型在各自环境的工作进程里
conda_on bench-tools
python -c "import fastapi, uvicorn, multipart" 2>/dev/null || pip install fastapi uvicorn python-multipart
log "数字人服务启动：端口 ${AVATAR_PORT:-8780}，低显存模式=$LOW_VRAM，默认引擎 ${AVATAR_DEFAULT_ENGINE:-infinitetalk}"
cd "$HERE"
exec uvicorn server:app --host 0.0.0.0 --port "${AVATAR_PORT:-8780}" --workers 1

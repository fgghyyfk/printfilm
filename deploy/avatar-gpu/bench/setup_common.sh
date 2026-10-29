#!/usr/bin/env bash
# 第 1 步：GPU 机基础环境（系统包、Miniforge、权重下载工具）。Ubuntu 22.04 + NVIDIA 驱动已就绪。
source "$(dirname "$0")/lib.sh"

log "检查 GPU"
nvidia-smi -L || { echo "未检测到 NVIDIA GPU，停止"; exit 1; }

log "安装系统包（ffmpeg、git、字体、OpenCV 依赖）"
SUDO=""
[[ "$(id -u)" != "0" ]] && SUDO="sudo"
if [[ "${USE_CN_MIRROR:-0}" == "1" ]]; then
  log "apt 改用阿里云镜像"
  $SUDO sed -i -E 's#https?://(archive|security)\.ubuntu\.com#https://mirrors.aliyun.com#g' /etc/apt/sources.list
fi
$SUDO apt-get update -y
$SUDO apt-get install -y ffmpeg git git-lfs curl wget tmux libgl1 libglib2.0-0 libsndfile1 fonts-dejavu-core build-essential

if [[ ! -x "$CONDA_HOME/bin/conda" ]]; then
  log "安装 Miniforge 到 $CONDA_HOME（默认 conda-forge 源，无需接受 ToS）"
  curl -fsSL -o /tmp/miniforge.sh \
    "https://github.com/conda-forge/miniforge/releases/latest/download/Miniforge3-Linux-$(uname -m).sh"
  bash /tmp/miniforge.sh -b -p "$CONDA_HOME"
fi

if [[ "${USE_CN_MIRROR:-0}" == "1" && ! -f "$HOME/.condarc" ]]; then
  log "conda-forge 改用清华镜像"
  printf 'channels:\n  - conda-forge\ncustom_channels:\n  conda-forge: https://mirrors.tuna.tsinghua.edu.cn/anaconda/cloud\n' >"$HOME/.condarc"
fi

log "创建权重下载环境 bench-tools"
conda_ensure bench-tools 3.11
(
  conda_on bench-tools
  command pip install -U "huggingface_hub>=0.30" hf_transfer "modelscope>=1.20" uv
  # uv 放到系统 PATH，供各模型环境的 pip 包装函数使用
  $SUDO ln -sf "$(command -v uv)" /usr/local/bin/uv
)

mkdir -p "$WORK_DIR" "$RESULT_DIR"
log "完成。磁盘剩余："
df -h "$WORK_DIR" | tail -1

#!/usr/bin/env bash
# 第 2 步（可与其它 setup 并行）：InfiniteTalk 代码、依赖与权重。命令取自官方 README。
source "$(dirname "$0")/lib.sh"

REPO_DIR="$WORK_DIR/InfiniteTalk"
if [[ ! -d "$REPO_DIR/.git" ]]; then
  git clone https://github.com/MeiGen-AI/InfiniteTalk "$REPO_DIR"
fi

if want_env; then
log "创建环境 multitalk（torch 2.4.1 + cu121）"
conda_ensure multitalk 3.10
(
  conda_on multitalk
  cd "$REPO_DIR"
  pip_torch cu121 torch==2.4.1 torchvision==0.19.1 torchaudio==2.4.1
  pip_torch cu121 -U xformers==0.0.28
  pip install "misaki[en]" ninja psutil packaging wheel
  pip_flash_attn 2.4
  pip install -r requirements.txt
  # 官方清单只写下限，会装到需要 torch>=2.5 的新版 diffusers/transformers/xfuser（torch 2.4 下 import 失败）；锁到 InfiniteTalk 发布时期的版本
  pip install "diffusers==0.34.0" "transformers==4.51.3" "peft==0.15.2" "xfuser==0.4.1"
  conda install -y -c conda-forge librosa ffmpeg
)
# 低内存补丁：官方把 7 个 fp32 分片（约 65GB）一次性读进内存再加载，改为逐片加载
python3 "$BENCH_DIR/patch_infinitetalk_lowmem.py" "$REPO_DIR"
fi

if want_weights; then
W="$REPO_DIR/weights"
log "下载权重（Wan2.1-I2V-14B-480P 基座约 80GB，耗时最长）"
hf_get Wan-AI/Wan2.1-I2V-14B-480P "$W/Wan2.1-I2V-14B-480P"
hf_get TencentGameMate/chinese-wav2vec2-base "$W/chinese-wav2vec2-base"
hf_get TencentGameMate/chinese-wav2vec2-base "$W/chinese-wav2vec2-base" --revision refs/pr/1 model.safetensors
# 整仓 168GB（多为量化版），单人口播只需 single/ 下 9.9GB
hf_get MeiGen-AI/InfiniteTalk "$W/InfiniteTalk" "single/*"
# 4 步蒸馏 LoRA（lightx2v），用于「快速档」对比，贴近量产速度
hf_get Kijai/WanVideo_comfy "$W/lora" Wan21_T2V_14B_lightx2v_cfg_step_distill_lora_rank32.safetensors
fi

log "InfiniteTalk 就绪：$REPO_DIR"

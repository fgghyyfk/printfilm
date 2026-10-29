#!/usr/bin/env bash
# 第 2 步（可与其它 setup 并行）：LongCat-Video-Avatar 1.5 代码、依赖与权重。命令取自官方 HF 模型卡。
source "$(dirname "$0")/lib.sh"

REPO_DIR="$WORK_DIR/LongCat-Video"
if [[ ! -d "$REPO_DIR/.git" ]]; then
  git clone --single-branch --branch main https://github.com/meituan-longcat/LongCat-Video "$REPO_DIR"
fi

if want_env; then
log "创建环境 longcat-video（torch 2.6 + cu124）"
conda_ensure longcat-video 3.10
(
  conda_on longcat-video
  cd "$REPO_DIR"
  pip_torch cu124 torch==2.6.0 torchvision==0.21.0 torchaudio==2.6.0
  pip install ninja psutil packaging wheel
  pip_flash_attn 2.6
  pip install -r requirements.txt
  conda install -y -c conda-forge librosa ffmpeg
  # 过滤国内镜像没有、推理也用不到的包：libsndfile1 是系统库（apt 已装），tritonserverclient 代码未引用
  grep -vE '^(libsndfile1|tritonserverclient)' requirements_avatar.txt >/tmp/requirements_avatar.txt
  pip install -r /tmp/requirements_avatar.txt
  # 官方清单没列 accelerate，缺它时 INT8 DiT 加载走慢路径（实测单卡 3090 要 15 分钟）
  pip install accelerate
)
fi

if want_weights; then
log "下载权重（只取 Avatar 推理用到的部分，省约 110GB）"
# 基座仓库只用 tokenizer / 文本编码器 / VAE / scheduler，跳过 54GB 的 dit 与 lora
hf_get meituan-longcat/LongCat-Video "$REPO_DIR/weights/LongCat-Video" \
  "tokenizer/*" "text_encoder/*" "vae/*" "scheduler/*" "*.json"
# Avatar 1.5：默认只下 INT8 DiT；LONGCAT_BF16=1 时再加 32GB 的 bf16 版（配合 run_bench 的 LONGCAT_INT8=0）
AVATAR_PATTERNS=("base_model_int8/*" "lora/*" "scheduler/*" "vocal_separator/*" "*.json"
  "whisper-large-v3/*.json" "whisper-large-v3/*.txt" "whisper-large-v3/model.safetensors")
[[ "${LONGCAT_BF16:-0}" == "1" ]] && AVATAR_PATTERNS+=("base_model/*")
hf_get meituan-longcat/LongCat-Video-Avatar-1.5 "$REPO_DIR/weights/LongCat-Video-Avatar-1.5" "${AVATAR_PATTERNS[@]}"
fi

log "LongCat 就绪：$REPO_DIR"

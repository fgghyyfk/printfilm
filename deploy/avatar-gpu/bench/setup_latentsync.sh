#!/usr/bin/env bash
# 第 2 步（可与其它 setup 并行）：LatentSync 1.6 + SyncNet 评分权重。评分脚本 score.py 也跑在这个环境里。
source "$(dirname "$0")/lib.sh"

REPO_DIR="$WORK_DIR/LatentSync"
if [[ ! -d "$REPO_DIR/.git" ]]; then
  git clone https://github.com/bytedance/LatentSync "$REPO_DIR"
fi

if want_env; then
log "创建环境 latentsync（官方 requirements：torch 2.5.1 + insightface + onnxruntime-gpu）"
conda_ensure latentsync 3.10.13
(
  conda_on latentsync
  cd "$REPO_DIR"
  conda install -y -c conda-forge ffmpeg
  pip install -r requirements.txt
)
log "预下载 insightface 人脸识别模型（人脸一致性评分用）"
(
  conda_on latentsync
  python -c "from insightface.app import FaceAnalysis; FaceAnalysis(name='buffalo_l').prepare(ctx_id=-1)"
)
fi

if want_weights; then
log "下载权重（推理 unet + whisper，评分用 syncnet + 人脸检测）"
hf_get ByteDance/LatentSync-1.6 "$REPO_DIR/checkpoints" \
  latentsync_unet.pt whisper/tiny.pt auxiliary/syncnet_v2.model auxiliary/sfd_face.pth
fi

log "LatentSync 就绪：$REPO_DIR"

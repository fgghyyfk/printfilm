#!/usr/bin/env bash
# 第 3 步：依次跑各模型用例，记录耗时与显存峰值，输出统一放 results/<用例>/output.mp4
#
# 用法：
#   LIMIT_SEC=10 bash run_bench.sh            # 冒烟：只取前 10 秒音频，先确认每个模型能跑通
#   bash run_bench.sh                          # 默认用例、全长音频
#   CASES="longcat_i2v_720p infinitetalk_i2v_480" bash run_bench.sh   # 指定用例
#
# 可选用例：
#   longcat_i2v_480p / longcat_i2v_720p         LongCat 1.5 照片→视频（8 步蒸馏 + INT8）
#   infinitetalk_i2v_480_fast / _720_fast       InfiniteTalk 照片→视频，lightx2v 4 步（量产速度）
#   infinitetalk_i2v_480                        InfiniteTalk 照片→视频，官方 40 步（画质上限，很慢）
#   infinitetalk_v2v_480_fast                   InfiniteTalk 底片→配音视频（需 inputs/base.mp4）
#   latentsync_v2v                              LatentSync 1.6 只改嘴型（需 inputs/base.mp4）
source "$(dirname "$0")/lib.sh"

CASES="${CASES:-longcat_i2v_480p infinitetalk_i2v_480_fast infinitetalk_v2v_480_fast latentsync_v2v}"
LIMIT_SEC="${LIMIT_SEC:-0}"
LONGCAT_INT8="${LONGCAT_INT8:-1}"
FPS=25

# 准备本轮共用素材（按 LIMIT_SEC 截短音频与底片）
RUN_IN="$RESULT_DIR/_inputs"
mkdir -p "$RUN_IN"
[[ -f "$INPUT_DIR/audio.wav" ]] || { echo "缺少 inputs/audio.wav，请先在本地跑 prepare_inputs.py 并上传"; exit 1; }
TRIM=()
[[ "$LIMIT_SEC" != "0" ]] && TRIM=(-t "$LIMIT_SEC")
ffmpeg -loglevel error -y -i "$INPUT_DIR/audio.wav" "${TRIM[@]}" "$RUN_IN/audio.wav"
if [[ -f "$INPUT_DIR/base.mp4" ]]; then
  ffmpeg -loglevel error -y -i "$INPUT_DIR/base.mp4" "${TRIM[@]}" -c:v libx264 -crf 18 -an "$RUN_IN/base.mp4"
fi
AUDIO_SEC="$(media_seconds "$RUN_IN/audio.wav")"
PROMPT="$(tr -d '\n' <"$INPUT_DIR/prompt.txt")"
log "音频 ${AUDIO_SEC}s，GPU ${NGPU} 张（单卡 ${GPU_MEM_MIB}MiB，低显存模式=${LOW_VRAM}），用例：$CASES"

# 低显存模式的显存分配器：WSL2 下 expandable_segments 会让分配提前失败，
# 默认分配器按 8MB 小块只能分到约 14.6GB，cudaMallocAsync 稳定在约 18.3GB（3090 实测），所以 WSL 用后者
lowvram_alloc_conf() {
  if grep -qi microsoft /proc/version 2>/dev/null; then
    export PYTORCH_CUDA_ALLOC_CONF=backend:cudaMallocAsync
  else
    export PYTORCH_CUDA_ALLOC_CONF=expandable_segments:True
  fi
}

# 写模型输入 JSON（prompt + 条件图/视频 + 音频）
write_json() {
  local dest="$1" key="$2" cond="$3"
  python3 - "$dest" "$key" "$cond" "$RUN_IN/audio.wav" "$PROMPT" <<'PY'
import json, sys
dest, key, cond, audio, prompt = sys.argv[1:]
json.dump({"prompt": prompt, key: cond, "cond_audio": {"person1": audio}}, open(dest, "w"), ensure_ascii=False)
PY
}

# LongCat 1.5：照片→视频 + 分段续写。v1.5 为 25fps，首段 93 帧，每段续写 80 帧
case_longcat() {
  local res="$1" out="$2"
  [[ -f "$INPUT_DIR/portrait.png" ]] || { echo "缺少 portrait.png"; return 2; }
  conda_on longcat-video
  cd "$WORK_DIR/LongCat-Video"
  local segs
  segs="$(python3 -c "import math; d=$AUDIO_SEC; print(max(1, math.ceil((d - 93/25) / (80/25)) + 1))")"
  write_json "$out/input.json" cond_image "$INPUT_DIR/portrait.png"
  local int8=()
  [[ "$LONGCAT_INT8" == "1" ]] && int8=(--use_int8)
  echo "num_segments=$segs"
  if [[ "$LOW_VRAM" == "1" ]]; then
    # 低显存：文本编码器留在 CPU（见 patch_longcat_lowvram.py）
    python3 "$BENCH_DIR/patch_longcat_lowvram.py" "$PWD"
    # INT8 DiT 本身 15GB：默认按层换入，前 LONGCAT_RESIDENT_BLOCKS 层常驻（共 48 层，显存紧就调小）
    export LONGCAT_TE_CPU=1 LONGCAT_DIT_OFFLOAD="${LONGCAT_DIT_OFFLOAD:-1}"
    export LONGCAT_RESIDENT_BLOCKS="${LONGCAT_RESIDENT_BLOCKS:-4}"
    lowvram_alloc_conf
  fi
  torchrun --nproc_per_node="$NGPU" run_demo_avatar_single_audio_to_video.py \
    --context_parallel_size="$NGPU" \
    --checkpoint_dir=./weights/LongCat-Video-Avatar-1.5 \
    --stage_1=ai2v --input_json="$out/input.json" --output_dir="$out/raw" \
    --resolution "$res" --num_segments="$segs" --ref_img_index=10 --mask_frame_range=3 \
    --use_distill --model_type avatar-v1.5 "${int8[@]}"
  # 每段续写都会存一版累计视频，取最新且非临时文件
  local last
  last="$(find "$out/raw" -maxdepth 1 -name '*.mp4' ! -name '*-temp.mp4' -printf '%T@ %p\n' | sort -n | tail -1 | cut -d' ' -f2-)"
  cp "$last" "$out/output.mp4"
}

# InfiniteTalk：mode=i2v 用照片、v2v 用底片；fast=1 走 lightx2v 4 步 LoRA
case_infinitetalk() {
  local mode="$1" res="$2" fast="$3" out="$4"
  local cond="$INPUT_DIR/portrait.png"
  [[ "$mode" == "v2v" ]] && cond="$RUN_IN/base.mp4"
  [[ -f "$cond" ]] || { echo "缺少条件素材 $cond"; return 2; }
  conda_on multitalk
  cd "$WORK_DIR/InfiniteTalk"
  write_json "$out/input.json" cond_video "$cond"
  local maxf
  maxf="$(python3 -c "import math; print(math.ceil($AUDIO_SEC * $FPS) + 25)")"
  local args=(
    --ckpt_dir weights/Wan2.1-I2V-14B-480P
    --wav2vec_dir weights/chinese-wav2vec2-base
    --infinitetalk_dir weights/InfiniteTalk/single/infinitetalk.safetensors
    --input_json "$out/input.json" --size "infinitetalk-$res"
    --mode streaming --motion_frame 9 --max_frame_num "$maxf"
    --audio_save_dir "$out/audio_tmp" --save_file "$out/output"
  )
  if [[ "$fast" == "1" ]]; then
    args+=(--lora_dir weights/lora/Wan21_T2V_14B_lightx2v_cfg_step_distill_lora_rank32.safetensors
      --lora_scale 1.0 --sample_text_guide_scale 1.0 --sample_audio_guide_scale 2.0
      --sample_steps 4 --sample_shift 2)
  else
    args+=(--sample_steps 40)
  fi
  # 低显存：DiT 参数按需从内存搬入显存（官方低显存开关，速度明显变慢），T5 文本编码器留 CPU
  if [[ "$LOW_VRAM" == "1" ]]; then
    args+=(--num_persistent_param_in_dit "${INFINITETALK_PERSIST:-0}" --t5_cpu)
    lowvram_alloc_conf
  fi
  if [[ "$NGPU" -gt 1 ]]; then
    torchrun --nproc_per_node="$NGPU" --standalone generate_infinitetalk.py \
      "${args[@]}" --dit_fsdp --t5_fsdp --ulysses_size="$NGPU"
  else
    python generate_infinitetalk.py "${args[@]}"
  fi
}

# LatentSync 1.6：底片只改嘴型（官方 inference.sh 参数）
case_latentsync() {
  local out="$1"
  [[ -f "$RUN_IN/base.mp4" ]] || { echo "缺少 base.mp4"; return 2; }
  conda_on latentsync
  cd "$WORK_DIR/LatentSync"
  python -m scripts.inference \
    --unet_config_path configs/unet/stage2_512.yaml \
    --inference_ckpt_path checkpoints/latentsync_unet.pt \
    --inference_steps 20 --guidance_scale 1.5 --enable_deepcache \
    --video_path "$RUN_IN/base.mp4" --audio_path "$RUN_IN/audio.wav" \
    --video_out_path "$out/output.mp4" --temp_dir "$out/tmp"
}

# 用例名 → 具体函数
dispatch() {
  local name="$1" out="$2"
  case "$name" in
    longcat_i2v_480p) case_longcat 480p "$out" ;;
    longcat_i2v_720p) case_longcat 720p "$out" ;;
    infinitetalk_i2v_480) case_infinitetalk i2v 480 0 "$out" ;;
    infinitetalk_i2v_720) case_infinitetalk i2v 720 0 "$out" ;;
    infinitetalk_i2v_480_fast) case_infinitetalk i2v 480 1 "$out" ;;
    infinitetalk_i2v_720_fast) case_infinitetalk i2v 720 1 "$out" ;;
    infinitetalk_v2v_480_fast) case_infinitetalk v2v 480 1 "$out" ;;
    latentsync_v2v) case_latentsync "$out" ;;
    *) echo "未知用例 $name"; return 2 ;;
  esac
}

# 跑单个用例：子 shell 隔离 conda 环境，记录耗时、退出码与各卡显存峰值
run_case() {
  local name="$1" out="$RESULT_DIR/$1"
  rm -rf "$out" && mkdir -p "$out"
  log "▶ $name（日志 $out/run.log）"
  gpu_mon_start "$out/gpu.csv"
  local t0 t1 rc=0
  t0="$(date +%s)"
  # 不能写成 ( … ) || rc=$?：bash 在 || 左侧会忽略子 shell 里的 set -e，中途失败也会继续跑并误报成功
  set +e
  (dispatch "$name" "$out") >"$out/run.log" 2>&1
  rc=$?
  set -e
  t1="$(date +%s)"
  gpu_mon_stop
  python3 - "$out" "$name" "$rc" "$((t1 - t0))" "$NGPU" "$AUDIO_SEC" <<'PY'
import csv, json, os, sys
out, name, rc, secs, ngpu, audio_sec = sys.argv[1:]
peak = {}
with open(os.path.join(out, "gpu.csv")) as f:
    for row in csv.reader(f):
        if len(row) == 2 and row[1].strip().isdigit():
            idx, mib = row[0].strip(), int(row[1])
            peak[idx] = max(peak.get(idx, 0), mib)
meta = {
    "case": name, "exit_code": int(rc), "wall_seconds": int(secs), "ngpu": int(ngpu),
    "audio_seconds": float(audio_sec), "peak_vram_mib": peak,
    "has_output": os.path.exists(os.path.join(out, "output.mp4")),
}
json.dump(meta, open(os.path.join(out, "meta.json"), "w"), indent=2)
print(json.dumps(meta, ensure_ascii=False))
PY
  if [[ "$rc" != "0" ]]; then
    log "✗ $name 失败（退出码 $rc），最后 20 行日志："
    tail -20 "$out/run.log"
  fi
}

for c in $CASES; do
  run_case "$c"
done
log "全部完成。下一步：bash score.sh"

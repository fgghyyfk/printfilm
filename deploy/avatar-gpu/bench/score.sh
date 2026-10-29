#!/usr/bin/env bash
# 第 4 步：评分并生成 results/report.md 与 results/compare_grid.mp4
# 用法：GPU_PRICE=8 bash score.sh      # GPU_PRICE = 每卡每小时价格（元），可选
source "$(dirname "$0")/lib.sh"

conda_on latentsync
PRICE_ARGS=()
[[ -n "${GPU_PRICE:-}" ]] && PRICE_ARGS=(--gpu-price "$GPU_PRICE")
python "$BENCH_DIR/score.py" --results "$RESULT_DIR" --latentsync-dir "$WORK_DIR/LatentSync" "${PRICE_ARGS[@]}" "$@"

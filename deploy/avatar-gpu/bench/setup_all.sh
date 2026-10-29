#!/usr/bin/env bash
# shellcheck disable=SC2030,SC2031  # 两条线各自在子 shell 里设置 SETUP_STAGE，属预期
# 一键安装：基础环境装完后，「装各模型环境」与「下载全部权重」两条线并行，节省总时长
# 日志：env.log（环境）、weights.log（权重）；两条线都成功时 setup_all 退出码为 0
source "$(dirname "$0")/lib.sh"
cd "$BENCH_DIR"

bash setup_common.sh

# 先串行克隆代码，避免两条线同时克隆同一仓库
mkdir -p "$WORK_DIR"
[[ -d "$WORK_DIR/LongCat-Video/.git" ]] ||
  git clone --single-branch --branch main https://github.com/meituan-longcat/LongCat-Video "$WORK_DIR/LongCat-Video"
[[ -d "$WORK_DIR/LatentSync/.git" ]] || git clone https://github.com/bytedance/LatentSync "$WORK_DIR/LatentSync"
[[ -d "$WORK_DIR/InfiniteTalk/.git" ]] || git clone https://github.com/MeiGen-AI/InfiniteTalk "$WORK_DIR/InfiniteTalk"

log "并行：权重下载 → weights.log，环境安装 → env.log"
(
  export SETUP_STAGE=weights
  bash setup_longcat.sh && bash setup_latentsync.sh && bash setup_infinitetalk.sh && echo WEIGHTS_DONE
) >weights.log 2>&1 &
wpid=$!
(
  export SETUP_STAGE=env
  bash setup_longcat.sh && bash setup_latentsync.sh && bash setup_infinitetalk.sh && echo ENV_DONE
) >env.log 2>&1 || erc=$?
wait "$wpid" || wrc=$?
log "环境退出码 ${erc:-0}，权重退出码 ${wrc:-0}"
[[ "${erc:-0}" == "0" && "${wrc:-0}" == "0" ]]

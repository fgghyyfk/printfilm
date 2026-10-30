#!/bin/sh
# 在终端里手动录入火山引擎豆包语音合成的 API Key（输入不回显），存到 ~/.config/kaikou/volc_tts.key（仅本人可读）。
# Key 获取：火山引擎控制台 → 豆包语音 → API Key 管理 https://console.volcengine.com/speech/new/setting/apikeys
set -eu
dir="$HOME/.config/kaikou"
mkdir -p "$dir"
chmod 700 "$dir"
printf "请粘贴火山引擎语音 API Key（输入不显示），回车结束: "
stty -echo
IFS= read -r key
stty echo
printf "\n"
if [ -z "$key" ]; then
  echo "没有输入，已取消。"
  exit 1
fi
umask 077
printf "%s" "$key" > "$dir/volc_tts.key"
chmod 600 "$dir/volc_tts.key"
echo "已保存到 $dir/volc_tts.key（长度 ${#key}），可以回到对话里告诉 Claude「好了」。"

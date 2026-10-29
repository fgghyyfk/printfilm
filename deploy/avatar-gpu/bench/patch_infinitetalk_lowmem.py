"""InfiniteTalk 低内存补丁：Wan2.1 基座权重改为逐个分片加载。

官方代码先把 7 个 fp32 分片（约 65GB）全部读进一个字典再加载，加上 bf16 模型本身约 28GB，
内存峰值超过 90GB。改成每读一片就加载并释放，峰值约 28GB + 单片 10GB，64GB 内存的机器也能跑。
加载结果与原来一致（最后核对所有参数都已加载）。

另：--t5_cpu 时 T5 在 CPU 上用 bf16 计算会退化成近乎单线程的慢路径（3090 机器上 70 分钟没算完一句提示词），
改为编码前转 fp32、结果转回 DiT 精度，几十秒完成；同时修正官方 CPU 分支返回列表导致 DiT 报错的问题。
每次运行都先用 git 还原官方原版再重打，可重复执行。

用法：python patch_infinitetalk_lowmem.py <InfiniteTalk 仓库目录>
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

REL = "wan/multitalk.py"
# (原文, 替换后)；原文必须在文件中恰好出现一次
PATCHES = [
    (
        "                merged_state_dict = {}\n"
        "                for weight_file in weight_files:\n"
        "                    sd = load_file(weight_file)\n"
        "                    merged_state_dict.update(sd)\n"
        "                self.model.load_state_dict(merged_state_dict)\n",
        "                import gc  # 低内存补丁：逐片加载，峰值只多占一个分片\n"
        "                pending = set(self.model.state_dict().keys())\n"
        "                for weight_file in weight_files:\n"
        "                    sd = load_file(weight_file)\n"
        "                    self.model.load_state_dict(sd, strict=False)\n"
        "                    pending -= set(sd.keys())\n"
        "                    del sd\n"
        "                    gc.collect()\n"
        "                if pending:\n"
        "                    raise RuntimeError(f'missing weights: {sorted(pending)[:5]}')\n",
    ),
    (
        "            context = self.text_encoder([input_prompt], torch.device('cpu'))\n"
        "            context_null = self.text_encoder([n_prompt], torch.device('cpu'))\n"
        "            context = [t.to(self.device) for t in context]\n"
        "            context_null = [t.to(self.device) for t in context_null]\n",
        # 官方 CPU 分支返回的是列表，DiT 需要和 GPU 分支一样的单个张量，否则报 'list' object has no attribute 'dtype'
        "            self.text_encoder.model.float()  # 低内存补丁：CPU 上 bf16 极慢，改 fp32 计算\n"
        "            context, context_null = self.text_encoder([input_prompt, n_prompt], torch.device('cpu'))\n"
        "            context = context.to(self.device, self.param_dtype)\n"
        "            context_null = context_null.to(self.device, self.param_dtype)\n",
    ),
]

def apply(repo: Path) -> None:
    """先用 git 还原官方原版，再逐个打补丁；找不到原文则报错退出。"""
    subprocess.run(["git", "-C", str(repo), "checkout", "--", REL], check=True)
    path = repo / REL
    text = path.read_text(encoding="utf-8")
    for old, new in PATCHES:
        if text.count(old) != 1:
            sys.exit(f"补丁失败：{REL} 中未唯一找到原文，InfiniteTalk 代码可能已更新，请人工检查")
        text = text.replace(old, new)
        print(f"已打补丁：{REL}（{new.strip().splitlines()[0][:40]}…）")
    path.write_text(text, encoding="utf-8")


if __name__ == "__main__":
    if len(sys.argv) != 2:
        sys.exit(__doc__)
    apply(Path(sys.argv[1]))

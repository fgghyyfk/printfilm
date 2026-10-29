"""LongCat-Video 低显存补丁：文本编码器（umT5-xxl，约 11GB）和 whisper（约 6GB）留在 CPU 上计算。

官方 demo 会把文本编码器、INT8 DiT、whisper、VAE 全部放进显存，合计约 35GB，24GB 单卡放不下。
WSL2 下显存按小块分配有额外开销，3090 实测只能用到约 18GB。
打补丁后显存里只保留 DiT + VAE；提示词和音频特征改在 CPU 上算，多花几十秒。
每次运行都先还原官方原版再重打，可重复执行；只有设置环境变量 LONGCAT_TE_CPU=1 时才生效，不影响多卡大显存机器。

用法：python patch_longcat_lowvram.py <LongCat-Video 仓库目录>
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

# (相对路径, 原文, 替换后)；原文必须在文件中恰好出现一次
PATCHES = [
    (
        "longcat_video/pipeline_longcat_video_avatar.py",
        "prompt_embeds = self.text_encoder(text_input_ids.to(device), mask.to(device)).last_hidden_state",
        "te_dev = self.text_encoder.device  # LONGCAT_TE_CPU 补丁：输入跟随编码器所在设备\n"
        "        prompt_embeds = self.text_encoder(text_input_ids.to(te_dev), mask.to(te_dev)).last_hidden_state",
    ),
    (
        "longcat_video/pipeline_longcat_video_avatar.py",
        "        if self.text_encoder is not None:\n"
        "            self.text_encoder = self.text_encoder.to(device, non_blocking=True)",
        "        if self.text_encoder is not None and not getattr(self, 'keep_te_cpu', False):  # LONGCAT_TE_CPU 补丁\n"
        "            self.text_encoder = self.text_encoder.to(device, non_blocking=True)",
    ),
    (
        "run_demo_avatar_single_audio_to_video.py",
        "    pipe.to(local_rank)\n",
        "    if os.environ.get('LONGCAT_TE_CPU') == '1':  # 低显存补丁：文本编码器留 CPU，fp32 计算更快\n"
        "        pipe.keep_te_cpu = True\n"
        "        pipe.text_encoder.to(torch.float32)\n"
        "    pipe.to(local_rank)\n",
    ),
    # whisper-large-v3 整模型（编码器 + 解码器，fp32 约 6GB）只在开头提一次音频特征，留在 CPU 上
    (
        "run_demo_avatar_single_audio_to_video.py",
        "audio_encoder = get_audio_encoder(audio_model_checkpoint_path, model_type).to(local_rank)",
        "audio_encoder = get_audio_encoder(audio_model_checkpoint_path, model_type).to(\n"
        "        'cpu' if os.environ.get('LONGCAT_TE_CPU') == '1' else local_rank)  # 低显存补丁：whisper 留 CPU",
    ),
    (
        "longcat_video/pipeline_longcat_video_avatar.py",
        "                audio_features[:, :, i: i + ENC_CHUNK].to(device),",
        "                audio_features[:, :, i: i + ENC_CHUNK].to(self.audio_encoder.device),  # 低显存补丁",
    ),
    (
        "longcat_video/pipeline_longcat_video_avatar.py",
        "        audio_emb = torch.stack([feat0, feat1, feat2, feat3, feat4], dim=2)[0]   # [T, 5, D]\n"
        "        return audio_emb\n",
        "        audio_emb = torch.stack([feat0, feat1, feat2, feat3, feat4], dim=2)[0]   # [T, 5, D]\n"
        "        return audio_emb.to(device)  # 低显存补丁：编码器在 CPU 时结果搬回目标设备\n",
    ),
    # INT8 DiT 本身 15GB，WSL 下放不进 18GB 可用显存：LONGCAT_DIT_OFFLOAD=1 时改为按层换入
    (
        "longcat_video/pipeline_longcat_video_avatar.py",
        "            self.dit = self.dit.to(device, non_blocking=True)\n",
        "            if os.environ.get('LONGCAT_DIT_OFFLOAD') == '1':  # 低显存补丁：DiT 按层换入显存\n"
        "                from longcat_video.block_offload import offload_dit_blocks\n"
        "                offload_dit_blocks(self.dit, device, int(os.environ.get('LONGCAT_RESIDENT_BLOCKS', '4')))\n"
        "            else:\n"
        "                self.dit = self.dit.to(device, non_blocking=True)\n",
    ),
    # 续写段要缓存前一段的 KV，单卡显存放不下：低显存时改存内存
    (
        "run_demo_avatar_single_audio_to_video.py",
        "            offload_kv_cache=False,\n",
        "            offload_kv_cache=os.environ.get('LONGCAT_TE_CPU') == '1',  # 低显存补丁：KV 缓存放内存\n",
    ),
]

# 新增文件：DiT 按层换入显存的实现（写入仓库，内容变化时覆盖）
NEW_FILES = {
    "longcat_video/block_offload.py": '''"""DiT 按层换入显存（低显存补丁生成，勿手改）。

前 resident 个 transformer block 常驻显存，其余 block 的权重留在内存里，
每次前向前把该层权重拷进显存、算完立即释放；DiT 的其它部分（嵌入、输出层）整体放进显存。
"""
import torch


def offload_dit_blocks(dit, device, resident=16):
    """把 DiT 非 block 部分和前 resident 个 block 放进显存，其余 block 注册按需换入的钩子。"""
    for name, child in dit.named_children():
        if name != "blocks":
            child.to(device)
    for t in list(dit.parameters(recurse=False)) + list(dit.buffers(recurse=False)):
        t.data = t.data.to(device)
    for i, blk in enumerate(dit.blocks):
        if i < resident:
            blk.to(device)
            continue
        # 不用锁页内存：WSL2 下锁 10GB 要一个多小时，还会挤占显卡共享内存导致后续 OOM
        tensors = list(blk.parameters()) + list(blk.buffers())
        cpu = [t.data for t in tensors]

        def pre(_m, _args, tensors=tensors, cpu=cpu):
            for t, c in zip(tensors, cpu):
                t.data = c.to(device, non_blocking=True)

        def post(_m, _args, _out, tensors=tensors, cpu=cpu):
            for t, c in zip(tensors, cpu):
                t.data = c

        blk.register_forward_pre_hook(pre)
        blk.register_forward_hook(post)
    print(f"[offload] DiT {len(dit.blocks)} 层中前 {resident} 层常驻显存，其余按层换入")
''',
}


def apply(repo: Path) -> None:
    """先用 git 把被改文件还原成官方原版，再逐个打补丁；找不到原文则报错退出。

    每次都从原版重打，补丁内容调整后也不会叠加出错。
    """
    files = sorted({rel for rel, _, _ in PATCHES})
    subprocess.run(["git", "-C", str(repo), "checkout", "--", *files], check=True)
    for rel, old, new in PATCHES:
        path = repo / rel
        text = path.read_text(encoding="utf-8")
        if text.count(old) != 1:
            sys.exit(f"补丁失败：{rel} 中未唯一找到原文，LongCat 代码可能已更新，请人工检查")
        path.write_text(text.replace(old, new), encoding="utf-8")
        print(f"已打补丁：{rel}")
    for rel, content in NEW_FILES.items():
        path = repo / rel
        if not path.exists() or path.read_text(encoding="utf-8") != content:
            path.write_text(content, encoding="utf-8")
            print(f"已写入：{rel}")


if __name__ == "__main__":
    if len(sys.argv) != 2:
        sys.exit(__doc__)
    apply(Path(sys.argv[1]))

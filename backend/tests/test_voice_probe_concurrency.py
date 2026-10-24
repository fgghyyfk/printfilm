"""音频探测的离线并发回归：不同分集共用项目目录时不得互相覆盖。"""
import asyncio
from unittest.mock import AsyncMock

import pytest

from app.services.drama import voice_reference_audio as audio


@pytest.mark.asyncio
@pytest.mark.parametrize("urls", [("first", "second"), ("first", "first")])
async def test_parallel_voice_probes_keep_independent_files(monkeypatch, tmp_path, urls):
    """模拟两次下载都落盘后才探测，确保时长来自各自请求的文件。"""
    paths = []
    both_downloaded = asyncio.Event()

    async def download(url, *, dest):
        """写入可识别的样本，等待另一下载完成以稳定复现共享路径错误。"""
        dest.write_text(url, encoding="utf-8")
        paths.append(dest)
        if len(paths) == 2:
            both_downloaded.set()
        await both_downloaded.wait()
        return dest

    monkeypatch.setattr(audio, "resolve_voice_audio_local_path", download)
    monkeypatch.setattr(audio, "probe_duration", lambda path: {"first": 2.5, "second": 7.5}[path.read_text(encoding="utf-8")])
    result = await asyncio.gather(*(audio.probe_voice_url_duration(url, work_dir=tmp_path) for url in urls))
    assert result == [{"first": 2.5, "second": 7.5}[url] for url in urls]
    assert len(set(paths)) == 2
    assert all(not path.exists() for path in paths)


@pytest.mark.asyncio
@pytest.mark.parametrize("unreadable", [False, True])
async def test_probe_cleans_only_its_temporary_file_on_failure(monkeypatch, tmp_path, unreadable):
    """探测或解析失败仍清理本次文件，保留项目中的原始音频。"""
    source = tmp_path / "original.mp3"
    source.write_bytes(b"source")
    paths = []

    async def download(_url, *, dest):
        """模拟已经写入临时文件后返回失败或交给探测器。"""
        dest.write_bytes(b"temporary")
        paths.append(dest)
        return None if unreadable else dest

    def failed_probe(_path):
        """模拟外部探测进程失败。"""
        raise RuntimeError("probe failed")

    monkeypatch.setattr(audio, "resolve_voice_audio_local_path", download)
    monkeypatch.setattr(audio, "probe_duration", failed_probe)
    if unreadable:
        assert await audio.probe_voice_url_duration("sample", work_dir=tmp_path) is None
    else:
        with pytest.raises(RuntimeError, match="probe failed"):
            await audio.probe_voice_url_duration("sample", work_dir=tmp_path)
    assert source.read_bytes() == b"source"
    assert all(not path.exists() for path in paths)


@pytest.mark.asyncio
async def test_body_validation_still_reuses_one_probe_per_url(monkeypatch, tmp_path):
    """同次提交中的重复参考音仍按 URL 复用时长，避免重复下载。"""
    probe = AsyncMock(return_value=2.5)
    monkeypatch.setattr(audio, "probe_voice_url_duration", probe)
    monkeypatch.setattr(audio.storage_svc, "project_dir", lambda _id: tmp_path)
    item = {"type": "audio_url", "audio_url": {"url": "https://media.example.com/voice.mp3"}, "role": "reference_audio"}
    await audio.validate_seedance_body_reference_audios({"content": [item, item]}, project_id=17)
    probe.assert_awaited_once()

"""真实 FFmpeg 校验整集顺序、音频与画质；OSS 只使用测试文件替身。"""
import os
from pathlib import Path
import shutil
import struct
from types import SimpleNamespace

import pytest

from app.marketplace import drama_media as media


@pytest.fixture
def local_media(tmp_path, monkeypatch):
    static = tmp_path / "static"
    static.mkdir()
    cache = tmp_path / "private-cache"
    cache.mkdir()
    monkeypatch.setattr(media, "STATIC_ROOT", static)
    monkeypatch.setattr(media, "cache_root", lambda: cache)
    paths = list(Path("C:/soft/ffmpeg").glob("*/bin/ffmpeg.exe"))
    ffmpeg = shutil.which("ffmpeg") or (str(paths[0]) if paths else "ffmpeg")
    ffprobe = str(Path(ffmpeg).with_name("ffprobe.exe" if ffmpeg.endswith(".exe") else "ffprobe"))
    settings = SimpleNamespace(public_base_url="http://localhost:8000", oss_folder="kepu", oss_bucket="test-bucket",
        oss_endpoint="oss-cn-hangzhou.aliyuncs.com", oss_public_base="https://media.example.test/assets",
        ffmpeg_path=ffmpeg, ffprobe_path=ffprobe)
    monkeypatch.setattr(media, "get_settings", lambda: settings)
    uploaded = tmp_path / "uploaded.mp4"
    def upload(key, source, content_type):
        assert key.startswith("marketplace/")
        assert content_type == "video/mp4"
        assert cache in source.parents
        shutil.copyfile(source, uploaded)
    monkeypatch.setattr(media.storage, "upload", upload)
    return static, cache, uploaded, settings


def test_local_stamp_and_version_changes_when_same_length_file_changes(local_media):
    static, _cache, _uploaded, _settings = local_media
    source = static / "demo.mp4"
    source.write_bytes(b"first")
    before = media.source_stamp("/static/demo.mp4")
    old_stat = source.stat()
    source.write_bytes(b"other")
    os.utime(source, ns=(old_stat.st_atime_ns, old_stat.st_mtime_ns))
    after = media.source_stamp("/static/demo.mp4")
    assert before["size_bytes"] == after["size_bytes"]
    assert before["mtime_ns"] == after["mtime_ns"]
    assert media.stamp_version(before) != media.stamp_version(after)


@pytest.mark.parametrize("locator", ["/static/../secret.mp4", "https://evil.test/kepu/generated/p1/video.mp4",
    "https://test-bucket.oss-cn-hangzhou.aliyuncs.com.evil.test/kepu/generated/a.mp4", "kepu/secrets/a.mp4",
    "marketplace/works/1/a.mp4", "https://media.example.test/not-assets/kepu/generated/a.mp4"])
def test_source_locator_rejects_untrusted_paths(local_media, locator):
    with pytest.raises(ValueError, match="MARKETPLACE_SOURCE_NOT_READY"):
        media.source_stamp(locator)


def test_absolute_file_outside_static_is_rejected(local_media, tmp_path):
    outside = tmp_path / "outside.mp4"
    outside.write_bytes(b"not permitted")
    with pytest.raises(ValueError, match="MARKETPLACE_SOURCE_NOT_READY"):
        media.source_stamp(str(outside))


def test_oss_stamp_ignores_temporary_signed_query(local_media, monkeypatch):
    monkeypatch.setattr(media.storage, "source_metadata", lambda key: {"etag": "ABCDEF", "size_bytes": 50})
    first = media.source_stamp("https://test-bucket.oss-cn-hangzhou.aliyuncs.com/kepu/generated/p1/a.mp4?Expires=1")
    second = media.source_stamp("https://test-bucket.oss-cn-hangzhou.aliyuncs.com/kepu/generated/p1/a.mp4?Expires=2")
    assert first["key"] == "kepu/generated/p1/a.mp4"
    assert media.stamp_version(first) == media.stamp_version(second)
    cdn = media.source_stamp("https://media.example.test/assets/kepu/generated/p1/a.mp4?signature=hidden")
    assert media.stamp_version(first) == media.stamp_version(cdn)


@pytest.mark.asyncio
async def test_source_change_is_rejected_before_upload(local_media):
    static, cache, uploaded, _settings = local_media
    source = static / "demo.mp4"
    source.write_bytes(b"first")
    stamp = media.source_stamp("/static/demo.mp4")
    source.write_bytes(b"new source")
    snapshot = {"schema_version": 2, "clips": [{"id": 1, "sort_order": 0, "locator": "/static/demo.mp4", "stamp": stamp}]}
    with pytest.raises(ValueError, match="MARKETPLACE_SOURCE_CHANGED"):
        await media.prepare_drama_video(snapshot, "marketplace/test/episode.mp4")
    assert not uploaded.exists()
    assert not list(cache.iterdir())


async def _clip(settings, path, color, size="160x90", duration=0.6, audio=False):
    if not Path(settings.ffmpeg_path).is_file() and not shutil.which(settings.ffmpeg_path):
        pytest.skip("FFmpeg is not available")
    args = [settings.ffmpeg_path, "-y", "-v", "error", "-f", "lavfi", "-i", f"color=c={color}:s={size}:r=10:d={duration}"]
    if audio:
        args += ["-f", "lavfi", "-i", f"sine=frequency=1000:sample_rate=48000:duration={duration}", "-c:a", "aac", "-ar", "48000", "-ac", "2"]
    else:
        args += ["-an"]
    await media.command(*args, "-c:v", "libx264", "-pix_fmt", "yuv420p", "-r", "10", "-g", "10", "-crf", "18",
        "-video_track_timescale", "90000", "-t", str(duration), str(path), timeout=30)


def _snapshot(static, paths):
    return {"schema_version": 2, "clips": [{"id": i + 1, "sort_order": i,
        "locator": "/static/" + path.relative_to(static).as_posix(),
        "stamp": media.source_stamp("/static/" + path.relative_to(static).as_posix())} for i, path in enumerate(paths)]}


async def _pixel(settings, path, time):
    raw = await media.command(settings.ffmpeg_path, "-v", "error", "-ss", str(time), "-i", str(path),
        "-vf", "scale=1:1", "-frames:v", "1", "-pix_fmt", "rgb24", "-f", "rawvideo", "-", timeout=30)
    return tuple(raw[:3])


@pytest.mark.asyncio
async def test_three_color_full_episode_preserves_order_and_duration(local_media, monkeypatch):
    static, cache, uploaded, settings = local_media
    paths = [static / f"clip-{i}.mp4" for i in range(3)]
    for path, color, duration in zip(paths, ("red", "lime", "blue"), (0.6, 0.7, 0.8)):
        await _clip(settings, path, color, duration=duration)
    async def forbidden(*_args):
        raise AssertionError("compatible clips must use lossless concatenation")
    monkeypatch.setattr(media, "_encode", forbidden)
    phases = []
    async def progress(phase):
        phases.append(phase)
    result = await media.prepare_drama_video(_snapshot(static, paths), "marketplace/test/full.mp4", progress)
    assert result["duration_seconds"] == pytest.approx(2.1, abs=0.15)
    assert result["fingerprint"] == media.fingerprint(uploaded)
    assert result["size_bytes"] == uploaded.stat().st_size
    assert phases == ["copying", "composing", "validating"]
    for index, time in enumerate((0.2, 0.9, 1.7)):
        pixel = await _pixel(settings, uploaded, time)
        assert pixel[index] > 180 and all(value < 60 for other, value in enumerate(pixel) if other != index)
    assert not list(cache.iterdir())


@pytest.mark.asyncio
async def test_mixed_sizes_and_audio_use_highest_source_resolution(local_media):
    static, cache, uploaded, settings = local_media
    paths = [static / f"clip-{i}.mp4" for i in range(3)]
    for path, color, size, duration, audio in zip(paths, ("red", "lime", "blue"), ("160x90", "320x180", "160x90"),
                                               (0.6, 0.7, 0.8), (False, True, False)):
        await _clip(settings, path, color, size=size, duration=duration, audio=audio)
    result = await media.prepare_drama_video(_snapshot(static, paths), "marketplace/test/full.mp4")
    info = await media._probe(uploaded)
    assert (info["width"], info["height"]) == (320, 180)
    assert info["audio"]
    assert result["duration_seconds"] == pytest.approx(2.1, abs=0.15)
    for index, time in enumerate((0.2, 0.9, 1.7)):
        pixel = await _pixel(settings, uploaded, time)
        assert pixel[index] > 180 and all(value < 60 for other, value in enumerate(pixel) if other != index)
    volumes = []
    for time in (0.2, 0.9, 1.7):
        raw = await media.command(settings.ffmpeg_path, "-v", "error", "-ss", str(time), "-i", str(uploaded),
            "-t", "0.1", "-vn", "-ac", "1", "-f", "f32le", "-", timeout=30)
        samples = struct.unpack("<" + "f" * (len(raw) // 4), raw)
        volumes.append(max(abs(value) for value in samples))
    assert volumes[1] > 0.05
    assert volumes[0] < 0.005 and volumes[2] < 0.005
    assert not list(cache.iterdir())


@pytest.mark.asyncio
async def test_single_clip_is_not_reencoded(local_media, monkeypatch):
    static, _cache, uploaded, settings = local_media
    source = static / "single.mp4"
    await _clip(settings, source, "red", size="320x180")
    async def forbidden(*_args):
        raise AssertionError("single clip must not be encoded")
    monkeypatch.setattr(media, "_encode", forbidden)
    await media.prepare_drama_video(_snapshot(static, [source]), "marketplace/test/single.mp4")
    before, after = await media._probe(source), await media._probe(uploaded)
    assert before["video"]["extradata"] == after["video"]["extradata"]
    assert (after["width"], after["height"]) == (320, 180)


@pytest.mark.asyncio
async def test_different_aspect_ratios_fail_without_upload(local_media):
    static, cache, uploaded, settings = local_media
    paths = [static / "portrait.mp4", static / "landscape.mp4"]
    await _clip(settings, paths[0], "red", size="90x160")
    await _clip(settings, paths[1], "blue", size="160x90")
    with pytest.raises(ValueError, match="MARKETPLACE_SOURCE_INCOMPATIBLE"):
        await media.prepare_drama_video(_snapshot(static, paths), "marketplace/test/invalid.mp4")
    assert not uploaded.exists()
    assert not list(cache.iterdir())


@pytest.mark.asyncio
async def test_cover_uses_private_copy_and_validates_image(local_media, monkeypatch):
    static, cache, _uploaded, settings = local_media
    source = static / "cover.png"
    await media.command(settings.ffmpeg_path, "-y", "-v", "error", "-f", "lavfi", "-i", "color=c=blue:s=120x80",
        "-frames:v", "1", str(source), timeout=30)
    captured = {}
    def upload(key, path, content_type):
        captured.update(key=key, content_type=content_type, size=path.stat().st_size)
        assert cache in path.parents
    monkeypatch.setattr(media.storage, "upload", upload)
    stamp = media.source_stamp("/static/cover.png")
    result = await media.prepare_drama_cover("/static/cover.png", "marketplace/test/cover.png", stamp)
    assert (result["width"], result["height"]) == (120, 80)
    assert captured["content_type"] == "image/png"
    assert captured["size"] == result["size_bytes"]
    assert not list(cache.iterdir())


@pytest.mark.asyncio
async def test_invalid_cover_does_not_upload(local_media):
    static, cache, uploaded, _settings = local_media
    (static / "fake.png").write_text("not an image", encoding="utf-8")
    with pytest.raises(ValueError, match="MARKETPLACE_SOURCE_NOT_READY"):
        await media.prepare_drama_cover("/static/fake.png", "marketplace/test/cover.png")
    assert not uploaded.exists()
    assert not list(cache.iterdir())

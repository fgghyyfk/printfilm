"""画布视频下载中断后只重试下载，不重新生成。"""

from app.services.drama.asset_video import (
    MAX_ASSET_VIDEO_DOWNLOAD_ATTEMPTS,
    asset_video_download_retry_delay_sec,
    asset_video_download_retry_payload,
)
from app.services.storage import RetryableMediaDownloadError, is_retryable_download_error


def test_download_retry_payload_keeps_provider_task_and_counts():
    updated = asset_video_download_retry_payload(
        {"prompt": "镜头", "download_attempts": 1},
        provider_task_id="vid_123",
    )
    assert updated is not None
    assert updated["provider_task_id"] == "vid_123"
    assert updated["download_attempts"] == 2
    assert updated["prompt"] == "镜头"


def test_download_retry_payload_stops_after_cap():
    updated = asset_video_download_retry_payload(
        {"download_attempts": MAX_ASSET_VIDEO_DOWNLOAD_ATTEMPTS},
        provider_task_id="vid_123",
    )
    assert updated is None


def test_download_retry_payload_rejects_blank_task_id():
    assert asset_video_download_retry_payload({}, provider_task_id="  ") is None


def test_download_retry_delay_is_bounded():
    assert asset_video_download_retry_delay_sec(1) == 20
    assert asset_video_download_retry_delay_sec(20) == 180


def test_retryable_download_error_carries_provider_task_id():
    err = RetryableMediaDownloadError("peer closed connection", provider_task_id="vid_9")
    assert err.provider_task_id == "vid_9"
    assert err.counts_attempt is True
    assert is_retryable_download_error(err)
    assert is_retryable_download_error(RuntimeError("incomplete download: got 1 expected 9"))
    assert is_retryable_download_error(RuntimeError("Connection reset by peer"))
    wrapped = RuntimeError("download failed")
    wrapped.__cause__ = RetryableMediaDownloadError("reset", provider_task_id="vid_9", counts_attempt=False)
    assert is_retryable_download_error(wrapped)

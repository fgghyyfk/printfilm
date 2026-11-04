"""具体音轨的真实来源；与费用记账分离，不从当前配置推断旧音频来源。"""
import hashlib


def audio_metadata(result, *, text: str, voice: str, audio_url: str | None = None) -> dict:
    return {
        "audio_url": audio_url or result.url,
        "provider": result.provider,
        "model": result.model,
        "is_fallback": result.is_fallback or result.provider == "edge",
        "fingerprint": hashlib.sha256(f"{voice}\0{text}".encode()).hexdigest(),
    }

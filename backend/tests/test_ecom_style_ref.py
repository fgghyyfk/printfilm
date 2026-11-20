"""风格复刻：参考图里的商品不交给生图模型（纯单测）。"""
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

from PIL import Image, ImageDraw, ImageStat

from app.services.ecom_jobs import jobs, style_ref


def _images(tmp_path: Path) -> tuple[Path, Path]:
    product = tmp_path / "product.png"
    Image.new("RGB", (400, 600), "white").save(product)
    reference = tmp_path / "poster.png"
    poster = Image.new("RGB", (600, 800), (20, 120, 90))
    ImageDraw.Draw(poster).rectangle((200, 200, 400, 600), fill=(240, 240, 240))
    poster.save(reference)
    return product, reference


def _style_job(tmp_path: Path, product: Path, reference: Path):
    item = SimpleNamespace(idx=0, prompt="旧提示词 图2 为设计风格参考", size="1776x2368", ratio="3:4", inputs=[
        {"role": "product", "path": str(product), "url": "/static/generated/p0/ecom/u1/jobs/j9/product.png"},
        {"role": "style", "path": str(reference), "url": "/static/generated/p0/ecom/u1/jobs/j9/poster.png"},
    ])
    job = SimpleNamespace(kind="style", user_id=1, id=9, params={"mode": "single", "count": 1, "notes": ""})
    return job, item


# 读图成功：只发产品图，风格以文字进提示词
async def test_style_job_sends_only_product_with_brief(tmp_path, monkeypatch):
    product, reference = _images(tmp_path)
    job, item = _style_job(tmp_path, product, reference)
    gen = AsyncMock(return_value=SimpleNamespace(local_url="/static/x.png", remote_url=None))
    monkeypatch.setattr("app.services.ark.get_ark", lambda: SimpleNamespace(gen_image=gen))
    monkeypatch.setattr(jobs, "job_dir", lambda uid, jid: tmp_path)
    monkeypatch.setattr(jobs, "get_settings", lambda: SimpleNamespace(ark_mock=False))
    monkeypatch.setattr(style_ref, "style_brief", AsyncMock(return_value="墨绿背景，主体居中偏下，侧逆光"))
    monkeypatch.setattr(jobs.storage, "ensure_local_media", AsyncMock(return_value=tmp_path / "out.png"))
    await jobs.generate_image(None, job, item)
    args, kwargs = gen.call_args
    assert args[2] == [jobs.storage.to_public_url(item.inputs[0]["url"])]
    assert "style_ref_urls" not in kwargs
    assert "墨绿背景" in args[0] and "唯一的商品" in args[0] and "设计风格参考" not in args[0]


# 读图失败：退回强模糊色板，不发原参考图
async def test_style_job_falls_back_to_blurred_reference(tmp_path, monkeypatch):
    product, reference = _images(tmp_path)
    job, item = _style_job(tmp_path, product, reference)
    gen = AsyncMock(return_value=SimpleNamespace(local_url="/static/x.png", remote_url=None))
    monkeypatch.setattr("app.services.ark.get_ark", lambda: SimpleNamespace(gen_image=gen))
    monkeypatch.setattr(jobs, "job_dir", lambda uid, jid: tmp_path)
    monkeypatch.setattr(jobs, "get_settings", lambda: SimpleNamespace(ark_mock=False))
    monkeypatch.setattr(style_ref, "style_brief", AsyncMock(return_value=""))
    monkeypatch.setattr(jobs.storage, "publish_local", lambda path, **kw: "/static/generated/blur.jpg")
    monkeypatch.setattr(jobs.storage, "ensure_local_media", AsyncMock(return_value=tmp_path / "out.png"))
    await jobs.generate_image(None, job, item)
    args, kwargs = gen.call_args
    styles = kwargs["style_ref_urls"]
    assert len(styles) == 1 and styles[0].endswith("/static/generated/blur.jpg")
    assert (tmp_path / "style_blur_0.jpg").is_file()
    assert "模糊的风格色板" in args[0]


# 模糊后认不出原图轮廓：边缘强度降到原图一成以下，整体色调保留
def test_blurred_reference_erases_detail(tmp_path):
    from PIL import ImageFilter

    _, reference = _images(tmp_path)
    out = style_ref.blurred_reference(reference, tmp_path / "blur.jpg")
    with Image.open(reference) as a, Image.open(out) as b:
        a, b = a.convert("RGB").resize(b.size), b.convert("RGB")
        edges = lambda im: im.convert("L").filter(ImageFilter.FIND_EDGES).crop((8, 8, im.width - 8, im.height - 8)).getextrema()[1]
        assert edges(b) < edges(a) * 0.1
        assert abs(ImageStat.Stat(a).mean[1] - ImageStat.Stat(b).mean[1]) < 12


# 视觉返回解析：取 brief、过滤极限词、截断；坏 JSON 返回空串
def test_clean_brief():
    assert style_ref._clean_brief('{"brief": "柔光、米白背景"}') == "柔光、米白背景"
    assert style_ref._clean_brief("not json") == ""
    assert len(style_ref._clean_brief('{"brief": "' + "光" * 999 + '"}')) == style_ref.BRIEF_MAX_CHARS


# 同一参考图第二次直接读缓存，不再调视觉模型
async def test_style_brief_cached(tmp_path, monkeypatch):
    _, reference = _images(tmp_path)
    vision = AsyncMock(return_value='{"brief": "暖光木桌"}')
    monkeypatch.setattr("app.services.llm_client.chat_completions_vision", vision)
    assert await style_ref.style_brief(tmp_path, reference) == "暖光木桌"
    assert await style_ref.style_brief(tmp_path, reference) == "暖光木桌"
    assert vision.await_count == 1

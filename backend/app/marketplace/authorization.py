"""本平台作品账号授权；仅作用于下游，不自动转让或授予买家权利。"""
import asyncio
import hashlib
from pathlib import Path
import tempfile
from typing import Annotated, Literal
import uuid

from fastapi import APIRouter, Depends, Request
from pydantic import Field, model_validator
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from . import storage
from .config import get_marketplace_settings
from .dependencies import Actor, DB, storage_ready
from .models import Authorization
from .schemas import Input
from .service import data, event, fail, now

VERSION = "printfilm-marketplace-2026-10-v1"
TITLE = "PrintFilm 生成作品展示与撮合授权函"
BODY = """一、适用作品：本人以本账号提交至数字资产市场并声明为 PrintFilm 生成的作品。本授权函可被本账号后续作品引用，但每件作品的来源、权属、素材及授权范围仍由平台逐件核验。
二、权属声明：本人为作品权利人或有权代表权利人提交作品，保证提供的生成记录、创作贡献和素材许可真实。音乐、字体、肖像、改编及第三方素材须具有相应使用权；本函不构成著作权登记证书，也不证明作品绝无侵权。
三、授权范围：本人非独家授权平台在审核通过、本人选择上架期间，存储及处理作品，制作压缩水印预览，在市场展示作品介绍和预览，并协助联系潜在需求方。投稿完整水印审片仅向相应需求方及审核人员开放。
四、交易边界：本函不转让作品版权，不授予平台或买家自动购买、再许可或原片下载权。具体价格、用途、期限、地域、合同、收款和原片交付由相关方在线下另行确认。选择候选或联系客服不构成成交。
五、下架与争议：本人可下架作品或撤回投稿以停止新展示和新审片链接。已签发的短时链接最长五分钟后失效。平台收到有初步证据的投诉时可暂停展示、播放和咨询，要求补充材料并保留处置记录。
六、签署与记录：本人已完整阅读并理解以上内容，自愿以真实姓名及手写签名确认。平台保存条款版本、全文摘要、签署时间、账号、签署图像及必要的访问记录，供授权核验与争议处理使用。条款更新后需重新签署；旧记录留档。"""

Point = tuple[Annotated[float, Field(ge=0, le=800, allow_inf_nan=False)], Annotated[float, Field(ge=0, le=240, allow_inf_nan=False)]]
Stroke = Annotated[list[Point], Field(min_length=2, max_length=1000)]


class SignInput(Input):
    template_version: str = Field(max_length=60)
    signer_name: str = Field(min_length=2, max_length=80)
    accepted_terms: Literal[True]
    strokes: list[Stroke] = Field(min_length=1, max_length=40)

    @model_validator(mode="after")
    def handwritten(self):
        points = [p for stroke in self.strokes for p in stroke]
        distance = sum(((b[0]-a[0])**2 + (b[1]-a[1])**2)**0.5 for stroke in self.strokes for a, b in zip(stroke, stroke[1:]))
        if len(points) < 12 or distance < 80 or max(p[0] for p in points)-min(p[0] for p in points) < 20:
            raise ValueError("请在签名区完整手写签名")
        return self


router = APIRouter()


async def current(db, owner_id: int):
    return (await db.execute(select(Authorization).where(Authorization.owner_id == owner_id, Authorization.template_version == VERSION))).scalar_one_or_none()


def summary(item):
    return data(item, "id", "template_version", "signer_name", "created_at", "body_hash") if item else None


@storage.transport
def render_and_store(body: SignInput, signed_at: str, owner_id: int) -> tuple[str, str]:
    """服务端合成全文和签名，先完成私有 OSS 写入再登记签署成功。"""
    from PIL import Image, ImageDraw, ImageFont
    font = ImageFont.truetype(get_marketplace_settings().watermark_font, 26)
    lines = []
    for paragraph in [TITLE, f"版本：{VERSION}", *BODY.splitlines(), f"签署人：{body.signer_name}", f"账号：{owner_id}  签署时间（UTC）：{signed_at}", "手写签名："]:
        line = ""
        for char in paragraph:
            if font.getlength(line + char) > 1000:
                lines.append(line)
                line = ""
            line += char
        lines.extend([line, ""])
    signature = Image.new("RGB", (800, 240), "white")
    draw = ImageDraw.Draw(signature)
    for stroke in body.strokes:
        draw.line(stroke, fill="black", width=3, joint="curve")
    letter = Image.new("RGB", (1120, 100 + len(lines)*40 + 260), "white")
    draw = ImageDraw.Draw(letter)
    for i, line in enumerate(lines):
        draw.text((60, 50 + i*40), line, font=font, fill="black")
    letter.paste(signature, (60, 50 + len(lines)*40))
    prefix = f"marketplace/authorizations/u{owner_id}/{uuid.uuid4().hex}"
    with tempfile.TemporaryDirectory(prefix="printfilm-authorization-") as folder:
        for name, image in (("signature", signature), ("letter", letter)):
            path = Path(folder) / f"{name}.png"
            image.save(path)
            storage.upload(f"{prefix}/{name}.png", path, "image/png")
    return f"{prefix}/signature.png", f"{prefix}/letter.png"


@router.get("/authorization", response_model=dict)
async def status(db: DB, user: Actor):
    return {"template": {"version": VERSION, "title": TITLE, "body": BODY}, "current": summary(await current(db, user.id))}


@router.post("/authorization", response_model=dict, dependencies=[Depends(storage_ready)])
async def sign(body: SignInput, request: Request, db: DB, user: Actor):
    if body.template_version != VERSION:
        fail("授权条款已更新，请刷新后重新阅读并签署", 409)
    existing = await current(db, user.id)
    if existing:
        return summary(existing)
    signed_at = now()
    signature_key, letter_key = await asyncio.to_thread(render_and_store, body, signed_at.isoformat(), user.id)
    item = Authorization(owner_id=user.id, template_version=VERSION, signer_name=body.signer_name,
                         body=BODY, body_hash=hashlib.sha256(BODY.encode()).hexdigest(), signature_key=signature_key,
                         letter_key=letter_key, created_at=signed_at, client_ip=(request.client.host if request.client else "")[:64],
                         user_agent=request.headers.get("user-agent", "")[:512])
    db.add(item)
    try:
        await db.flush()
        event(db, user.id, "authorization", item.id, "signed", version=1)
        await db.commit()
    except IntegrityError:
        await db.rollback()
        existing = await current(db, user.id)
        if existing:
            return summary(existing)
        raise
    return summary(item)


@router.get("/authorizations/{record_id}/letter", response_model=dict, dependencies=[Depends(storage_ready)])
async def letter(record_id: int, db: DB, user: Actor):
    item = await db.get(Authorization, record_id)
    if not item or (item.owner_id != user.id and user.role != "admin"):
        fail("授权函不存在或无权查看", 404)
    url = await asyncio.to_thread(storage.signed, item.letter_key, "image/png")
    event(db, user.id, "authorization", item.id, "read")
    await db.commit()
    return {"url": url, "expires_in": get_marketplace_settings().signed_url_seconds}

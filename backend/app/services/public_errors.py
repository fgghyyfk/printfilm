"""公开诊断使用固定中文；原始异常只供日志，不能直接作为用户文案。"""
from dataclasses import dataclass
import re


class InsufficientBalanceError(ValueError):
    """仅由本站钱包校验产生，不能用上游的同名文案推断用户余额。"""


@dataclass(frozen=True)
class PublicError:
    """HTTP、任务和进度共用的稳定错误类别。"""
    code: str
    message: str
    status: int = 500


# 本地校验的完整固定文案；不接受前缀匹配或“包含中文即安全”。
_LOCAL_MESSAGES = frozenset({
    "图片识字超时，请减少图片后重试",
    "请先配置视觉模型", "翻译结果格式错误，请重试", "请先完成套图生成", "翻译结果不支持裂变",
    "请至少生成两张成功图片", "请重新选择生成选项后再试",
    "参数错误", "未知工具", "不支持的生图工具", "不支持的视频工具", "请填写提示词",
    "请填写视频脚本", "请上传参考图", "请上传商品图", "请上传源视频或首帧图",
    "拼接至少上传 2 张图片", "单个文件不能超过 40MB", "商品图不能超过 10MB",
    "商品图仅支持 png / jpg / webp", "仅支持 png / jpg / webp / gif / mp4 / mov / webm",
    "图片无法识别，请换一张", "请填写品名", "已开始生成，不能再修改方案",
    "没有需要生成的图片", "这张图正在生成中", "这张图还没有底图，请先生成",
    "还没有生成好的图片", "本集还没有可拼接的分镜视频", "缺少首帧图，无法生成视频",
    "内容已在别处更新，请刷新", "分镜已变更，请使用当前分镜重新生成",
    "用户取消", "任务已取消", "已提交取消请求", "检测到任务执行中断，已安排恢复",
    "所选分镜不属于本集或已移除，请刷新后重新选择", "所选分镜尚无视频，请刷新后重试",
})


# 固定模板同时保证新错误与历史重复投影保持同一类别。
_FIXED_ERRORS = {
    'billing_review_resolved': ('费用已核对，本地任务已结束；原上游任务及已有媒体保留', 200),
    'tool_result_restore_pending': ('任务已完成，结果链接待恢复，请稍后查看或联系客服，请勿重复生成', 502),
    'billing_review_pending': ('本次调用的结果和费用待核对，请勿重复生成，请联系客服', 500),
    'upstream_account_unavailable': ('平台模型账户暂不可用，请联系管理员处理', 502),
    'upstream_auth_unavailable': ('模型服务认证暂不可用，请联系管理员处理', 502),
    'upstream_timeout': ('上游响应超时，请先查看任务状态，避免重复提交', 504),
    'upstream_submit_unknown': ('发送请求超时，结果尚未确认，请先查看任务状态', 504),
    'upstream_unavailable': ('暂时无法连接生成服务，请稍后查看任务状态', 502),
    'reference_image_privacy': ('参考图疑似真人肖像，请更换为动漫或插画形象后重试', 400),
    'text_moderation': ('分镜文案未通过内容审核，请修改后重试', 400),
    'reference_image_format': ('参考图格式无效，请使用可访问的 PNG、JPG 或 WEBP 图片', 400),
    'reference_audio_short': ('参考音频过短，请上传或生成至少 1.8 秒的试听音频', 400),
    'reference_audio_long': ('参考音频过长，请截短至 30 秒以内', 400),
    'reference_audio_unavailable': ('参考音频无法读取，请检查音色绑定或重新上传试听音频', 400),
    'media_download_failed': ('成片下载中断，请查看原任务恢复进度，避免重新生成', 502),
    'model_unavailable': ('当前模型渠道未开通，请更换模型或联系管理员', 502),
    'compose_interrupted': ('合成被系统中断，请使用重新拼接恢复', 500),
    'compose_failed': ('视频合成失败，请稍后重新拼接或联系客服', 500),
    'recovery_unsupported': ('任务中断，已有上游任务号已保留，请联系客服核对', 500),
    'previous_fragment_failed': ('上一镜未成功，无法衔接尾帧，请先修复上一镜', 400),
    'fragment_changed': ('分镜已更新，旧任务已失效，请使用当前分镜重新生成', 409),
    'generation_retry_limit': ('内部自动重试超过上限，请检查参考素材后再操作', 500),
    'image_rate_limited': ('图片生成过于频繁或已有任务进行中，请稍后再试', 429),
    'invalid_input': ('输入参数有误，请检查后重试', 400),
    'not_found': ('请求的内容不存在', 404),
    'upstream_failed': ('生成服务未能完成请求，请查看任务记录或联系客服', 502),
}


def _fixed(code: str) -> PublicError:
    """从唯一固定模板构造公开诊断。"""
    message, status = _FIXED_ERRORS[code]
    return PublicError(code, message, status)


def public_error(error, *, code: str | None = None, fallback: str = "生成失败，请稍后查看任务状态或联系客服") -> PublicError:
    """只识别已知类别并重建文案，不把异常正文或动态上游标签拼回响应。"""
    text = str(error or "").strip()
    name = type(error).__name__
    if code in _FIXED_ERRORS:
        return _fixed(code)
    for known_code, (message, _) in _FIXED_ERRORS.items():
        if text == message:
            return _fixed(known_code)
    if isinstance(error, InsufficientBalanceError) or code == "insufficient_balance":
        # 金额只允许本站校验产生的有界数字文案，其余使用固定提示。
        safe = text if isinstance(error, InsufficientBalanceError) and re.fullmatch(
            r"余额不足：[\d\s¥.,：，（）()\u4e00-\u9fff]+", text,
        ) else "余额不足，请先充值后再继续"
        return PublicError("insufficient_balance", safe, 402)
    if code in {"tts_billing_pending", "TTSBillingPending", "billing_review_pending"} or name in {"TTSBillingPending", "VideoBillingPending"} or re.search(
        r"费用待核对|费用.*待核对|音色合成请求中断", text,
    ):
        return _fixed("billing_review_pending")
    if re.search(r"Credits insufficient|AccountOverdue|上游.*(?:账户欠费|积分不足)|Kie.*积分|积分不足", text, re.I):
        return _fixed("upstream_account_unavailable")
    response = getattr(error, "response", None)
    upstream_status = getattr(response, "status_code", None)
    if upstream_status in {401, 403} or re.search(r"(?:upstream|Seedream|Seedance|上游).*?(?:error|HTTP|状态码)\s*[:=]?\s*40[13]", text, re.I):
        return _fixed("upstream_auth_unavailable")
    if name == "ReadTimeout" or re.search(r"ReadTimeout|等待上游超时|响应超时|poll.*timeout|轮询超时", text, re.I):
        return _fixed("upstream_timeout")
    if name == "WriteTimeout" or re.search(r"WriteTimeout|发送请求超时", text, re.I):
        return _fixed("upstream_submit_unknown")
    if name in {"ConnectError", "ConnectTimeout", "PoolTimeout", "NetworkError", "ProxyError", "TimeoutException"} or re.search(
        r"ConnectError|ConnectTimeout|无法连接上游|网络错误|connection refused", text, re.I,
    ):
        return _fixed("upstream_unavailable")
    if re.search(r"PrivacyInformation|InputImageSensitive|SensitiveContentDetected|real person|参考图疑似真人", text, re.I):
        return _fixed("reference_image_privacy")
    if re.search(r"InputTextSensitive|text.*sensitive|文案未通过.*审核", text, re.I):
        return _fixed("text_moderation")
    if re.search(r"invalid image format|File type not supported|参考图格式(?:无效|不支持)|不支持 SVG", text, re.I):
        return _fixed("reference_image_format")
    if re.search(r"参考音频过短|audio duration.*(?:1\.8|greater than or equal)", text, re.I):
        return _fixed("reference_audio_short")
    if re.search(r"参考音频过长", text):
        return _fixed("reference_audio_long")
    if re.search(r"参考音频.*(?:无法下载|无法读取|无法校验|地址为空)|无法读取参考音频|resource download failed.*audio|audio_url", text, re.I):
        return _fixed("reference_audio_unavailable")
    if re.search(r"RemoteProtocolError|peer closed|complete message body|incomplete download|成片下载中断|无法下载分镜视频", text, re.I):
        return _fixed("media_download_failed")
    if re.search(r"model_not_found|无可用渠道|未开通|distributor", text, re.I):
        return _fixed("model_unavailable")
    if re.search(r"FFmpeg.*(?:signal 15|被系统中断)|合成被中断", text, re.I):
        return _fixed("compose_interrupted")
    if re.search(r"未找到 ffmpeg|FFmpeg.*(?:失败|error)|ffmpeg.*(?:failed|error)", text, re.I):
        return _fixed("compose_failed")
    recovery = re.search(r"任务执行中断已达 (\d{1,6}) 次", text)
    if recovery:
        return PublicError("recovery_limit", f"任务执行中断已达 {int(recovery[1])} 次，已停止自动恢复，请查看任务记录")
    if re.search(r"已有上游任务暂不支持安全恢复", text):
        return _fixed("recovery_unsupported")
    if re.search(r"上一镜失败|无法衔接尾帧", text):
        return _fixed("previous_fragment_failed")
    if re.search(r"分镜已变更|分镜上下文丢失|stale_fragment_ref", text):
        return _fixed("fragment_changed")
    if re.search(r"内部自动重试超过上限|重试超过上限|超过重试上限", text):
        return _fixed("generation_retry_limit")
    if name == "ImageRateLimited":
        return _fixed("image_rate_limited")
    if text in _LOCAL_MESSAGES:
        return PublicError("invalid_input" if isinstance(error, ValueError) else "task_notice", text, 400)
    if isinstance(error, ValueError):
        return _fixed("invalid_input")
    if isinstance(error, LookupError):
        return _fixed("not_found")
    if upstream_status is not None or re.search(r"Seedance create error|Seedream error|上游.*失败", text, re.I):
        return _fixed("upstream_failed")
    return PublicError("generation_failed", fallback)


def public_message(value, *, code: str | None = None) -> str | None:
    """供历史输出使用；空诊断仍为空，不能根据文本猜测用户钱包不足。"""
    return public_error(value, code=code).message if value else value


def public_http_exception(error, *, fallback_status: int | None = None):
    """新HTTP错误保留字符串detail，并附加跨端可读的稳定错误码。"""
    from fastapi import HTTPException

    mapped = public_error(error)
    status = fallback_status if fallback_status is not None and mapped.code == "generation_failed" else mapped.status
    return HTTPException(status, mapped.message, headers={"X-Error-Code": mapped.code})


def public_drama_params(value):
    """仅复制漫剧已知诊断键，保留画面、脚本及其他业务JSON。"""
    if not isinstance(value, dict):
        return value
    output = dict(value)
    for key in ("summary_error", "episode_content_error", "episode_optimize_error", "fragment_plan_error", "assets_seed_error"):
        if output.get(key):
            output[key] = public_message(output[key])
    if isinstance(output.get("assets_seed_llm_errors"), list):
        output["assets_seed_llm_errors"] = [public_message(item) for item in output["assets_seed_llm_errors"]]
    if isinstance(output.get("generation"), dict):
        output["generation"] = public_task_payload(output["generation"])
        if output["generation"].get("status") == "failed" and output["generation"].get("message"):
            output["generation"]["message"] = public_message(output["generation"]["message"])
    return output


def public_task_payload(value):
    """仅处理任务结果和诊断日志的明确错误位置，不遍历用户正文。"""
    if not isinstance(value, dict):
        return value
    output = dict(value)
    for key in ("error", "error_message", "root_error"):
        if output.get(key):
            raw = output[key]
            output[key] = public_message(raw)
    response = output.get("response")
    if isinstance(response, dict) and isinstance(response.get("body"), dict):
        body = dict(response["body"])
        if body.get("error") or (isinstance(response.get("status_code"), int) and response["status_code"] >= 400):
            for key in ("error", "message", "detail"):
                if body.get(key):
                    raw = body[key]
                    body[key] = public_message(raw)
            output["response"] = {**response, "body": body}
    return output


def public_task_output(value: dict) -> dict:
    """用户任务API的诊断副本；共享模型及管理员留痕不改变。"""
    output = dict(value)
    if output.get("error_message"):
        mapped = public_error(output["error_message"], code=output.get("error_code"))
        output.update(error_message=mapped.message, error_code=mapped.code)
    output["result_payload"] = public_task_payload(output.get("result_payload"))
    steps = []
    for step in output.get("steps", []):
        step = dict(step)
        if step.get("error_message"):
            mapped = public_error(step["error_message"], code=step.get("error_code"))
            step.update(error_message=mapped.message, error_code=mapped.code)
        step["output_payload"] = public_task_payload(step.get("output_payload"))
        steps.append(step)
    output["steps"] = steps
    events = []
    for event in output.get("events", []):
        event = dict(event)
        if str(event.get("event_type", "")).rsplit(".", 1)[-1] in {
            "failed", "cancelled", "awaiting_review", "recovered", "requeued", "cancel_requested",
        }:
            event["message"] = public_message(event.get("message"))
        event["payload"] = public_task_payload(event.get("payload"))
        # 失败留痕可含HTML、纯文本或任意debug字段；用户不需要原响应体/响应头。
        payload = event.get("payload")
        if str(event.get("event_type", "")).startswith("upstream.request.") and isinstance(payload, dict):
            response = payload.get("response")
            if isinstance(response, dict) and (
                str(event.get("event_type", "")).endswith("failed")
                or payload.get("error")
                or isinstance(response.get("status_code"), int) and response["status_code"] >= 400
            ):
                payload = {**payload, "response": {**response, "body": public_message(response.get("body") or payload.get("error")), "headers": {}}}
                event["payload"] = payload
        events.append(event)
    output["events"] = events
    return output

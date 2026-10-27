"""接口文档的中文总览、分组及 FastAPI 公共元数据。"""

from fastapi import FastAPI
from fastapi.openapi.utils import get_openapi


# 仅在生成文档时转换显示分组，路由和业务调用保持原有定义。
TAG_DESCRIPTIONS = {
    "auth": ("账号与认证", "注册、登录、个人资料、密码修改与密码找回。受保护接口使用站内登录 JWT。"),
    "capabilities": ("平台能力", "查询当前平台启用的功能与模型能力，供客户端决定可用入口。"),
    "templates": ("创作模板", "查询可用模板、分类和模板详情。"),
    "projects": ("科普项目与作品", "科普项目、分镜、分阶段生成、合成、导出及已发布作品。"),
    "tasks": ("任务与生成记录", "查询生成记录、任务进度与执行结果，并请求取消任务。"),
    "billing": ("个人钱包与计费", "查询余额、价格、用量、充值订单和额度提醒，以及接收支付回调。金额以字段注明的单位为准，*_fen 为人民币分。"),
    "tools": ("独立生成工具", "图片、视频、音频生成工具及工具执行记录。"),
    "drama": ("漫剧创作", "漫剧项目、剧本、分集、分镜、资产、画布和生成任务。团队内容写入受编辑租约保护。"),
    "drama-assets": ("个人媒体库", "查询和复用个人空间中的媒体资产。"),
    "teams": ("团队与协作", "团队资料、成员、预算和内容编辑租约。"),
    "team-invitations": ("团队邀请", "查询和接受团队邀请。"),
    "team-billing": ("团队用量与额度", "团队用量、成员预算及历史钱包记录；当前团队创作由所有者个人钱包付费。"),
    "team-content": ("团队创作内容", "团队项目、共享媒体资产及内容迁入。"),
    "admin": ("后台管理", "需要管理员角色。提供全站统计、用户、任务、内容、模板及系统设置管理。"),
    "admin-finance": ("后台财务", "需要管理员角色。查询每日扣费、Token 用量和调用汇总。"),
    "下游能力": ("市场能力", "查询数字资产市场开关及存储、支付配置状态。"),
    "下游作品与招募": ("数字资产市场", "作品与剧集发布、招募、版权、素材上传、预览及交易；需启用市场功能。"),
    "下游运营": ("市场管理", "需要管理员角色且已启用市场功能，提供内容审核、版权核验和运营配置。"),
    "系统状态": ("系统状态", "查询应用配置摘要和进程内任务运行状态。"),
}

OPENAPI_TAGS = [
    {"name": name, "description": description}
    for name, description in TAG_DESCRIPTIONS.values()
]

API_DESCRIPTION = """
PRINTFILM AI 视频创作平台接口，覆盖科普短视频、漫剧、独立生成工具、团队协作和数字资产市场。

### 认证与权限

通过 `POST /api/auth/login` 获取 `access_token`，在右上角 **Authorize** 中填入令牌，
或发送 `Authorization: Bearer <access_token>`。管理员接口要求账号 `role=admin`。
公开浏览接口和支付回调的权限以各接口说明为准。

### 团队空间与编辑

支持空间切换的列表和创建接口可通过 `X-Team-Id` 请求头或 `team_id` 查询参数选择团队，
两者同时提供时必须一致。不选择时使用个人空间；具体资源按数据库中的真实归属授权。
团队漫剧写操作通常需要先获取编辑租约，再传入 `X-Edit-Session`、`X-Edit-Token`、
`X-Edit-Version`，详见团队编辑权限接口和仓库 `docs/API.md`。

### 请求与返回约定

- JSON 请求使用 `Content-Type: application/json`；上传和流式接口按各自说明调用。
- 分页通常使用 `page`（从 1 开始）、`page_size`，返回 `items` 与 `meta`；部分接口直接返回列表或使用平铺分页字段，以响应结构为准。
- `*_fen` 为人民币分，时长字段以各自注明的秒或毫秒为准。
- 异步生成的提交成功表示任务已受理，最终结果请查询任务、生成记录或相应业务状态接口。
- 错误响应使用 `detail`，可能是中文字符串、结构化对象或校验错误数组。
- 常见状态包括 400（业务参数错误）、401（未登录或令牌失效）、403（无权限）、404（资源不存在）、
  409（资源状态或版本冲突）、410（入口停用）、422（参数校验失败）、423（资源占用）、428（缺少编辑租约）。具体以接口实现为准。

在线调试：`/docs`；阅读文档：`/redoc`；机器可读定义：`/openapi.json`。
""".strip()

# 对缺少专用文案的通用参数提供统一说明，不修改类型、必填性或默认值。
PARAMETER_DESCRIPTIONS = {
    "page": "页码，从 1 开始；服务端实际返回的页码以响应中的分页字段为准。",
    "page_size": "每页条数，默认值和最大值见参数约束。",
    "project_id": "项目编号，须为当前账号有权访问的项目。",
    "drama_project_id": "漫剧项目编号。",
    "task_id": "统一任务平台的任务编号。",
    "task_run_id": "统一任务平台的任务编号。",
    "asset_id": "资产编号。",
    "episode_id": "分集编号。",
    "fragment_id": "分镜编号。",
    "team_id": "团队编号，访问权限以当前账号的团队身份为准。",
    "user_id": "用户编号。",
    "template_id": "创作模板编号。",
    "q": "搜索关键词，匹配范围见接口说明。",
    "X-Team-Id": "团队空间编号；未提供时使用个人空间，资源详情仍按实际归属授权。",
}


def build_openapi(app: FastAPI) -> dict:
    """生成并缓存中文文档，仅补充展示元数据。"""
    if app.openapi_schema is not None:
        return app.openapi_schema
    schema = get_openapi(
        title=app.title,
        version=app.version,
        openapi_version=app.openapi_version,
        summary=app.summary,
        description=app.description,
        routes=app.routes,
        tags=app.openapi_tags,
        servers=app.servers,
        separate_input_output_schemas=app.separate_input_output_schemas,
    )
    for path_item in schema["paths"].values():
        for method, operation in path_item.items():
            if method not in {"get", "post", "put", "patch", "delete", "head", "options", "trace"}:
                continue
            operation["tags"] = [
                TAG_DESCRIPTIONS.get(tag, (tag, ""))[0]
                for tag in operation.get("tags", [])
            ]
            for parameter in operation.get("parameters", []):
                description = PARAMETER_DESCRIPTIONS.get(parameter["name"])
                if description and not parameter.get("description"):
                    parameter["description"] = description
            for response in operation.get("responses", {}).values():
                if response.get("description") == "Successful Response":
                    response["description"] = "请求成功"
                elif response.get("description") == "Validation Error":
                    response["description"] = "请求参数校验失败，detail 列出字段位置、错误原因和错误类型。"
    schemas = schema.get("components", {}).get("schemas", {})
    for name, description in {
        "HTTPValidationError": "请求参数校验错误响应。",
        "ValidationError": "单个字段的校验错误；loc 为字段位置，msg 为错误原因，type 为错误类型。",
    }.items():
        if name in schemas:
            schemas[name].setdefault("description", description)
    app.openapi_schema = schema
    return schema

"""历史空模板兼容响应序列化，同时保留新建项目的输入校验。"""

from datetime import UTC, datetime
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from app.schemas import AdminProjectDetailOut, AdminProjectOut, ProjectCreate, ProjectListItem, ProjectOut


@pytest.mark.parametrize("schema", [ProjectListItem, ProjectOut, AdminProjectOut, AdminProjectDetailOut])
@pytest.mark.parametrize("template_id", [None, "test"])
def test_project_responses_normalize_legacy_null_template(schema, template_id):
    """列表、详情和管理端均兼容空模板，且不改写原始 ORM 属性。"""
    project = SimpleNamespace(
        id=1, user_id=7, title="历史项目", template_id=template_id,
        source_type="theme", source_text="主题", status="DRAFT", progress=0,
        error_msg=None, cover_url=None, final_video_url=None, ref_image_url=None,
        resolution_mode="preview", pipeline_mode="full", output_ratio="", voice_id="",
        created_at=datetime(2026, 10, 24, tzinfo=UTC),
        updated_at=datetime(2026, 10, 24, tzinfo=UTC),
    )

    response = schema.model_validate(project)

    assert response.model_dump(mode="json")["template_id"] == (template_id or "")
    assert project.template_id == template_id


def test_create_project_still_rejects_null_template():
    """历史响应兼容不放宽新建项目的模板要求。"""
    with pytest.raises(ValidationError) as exc:
        ProjectCreate(template_id=None, title="新项目", source_text="主题")
    assert any(error["loc"] == ("template_id",) for error in exc.value.errors())

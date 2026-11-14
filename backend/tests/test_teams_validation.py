"""团队核心输入边界与所有权权威判断，不连接数据库。"""
import pytest
from pydantic import ValidationError

from app.models_teams import Team, TeamInvitation, TeamMember
from app.schemas_teams import TeamCreate, TeamInvitationToken, TeamMemberPatch, TeamUpdate
from app.services.teams.access import member_role
from app.services.teams.invitations import hash_token, invitation_frontend_base


def test_teams_inputs_trim_text_and_reject_unknown_or_empty_patches():
    """资料统一去空格；不允许 null 擦除资料或未知字段静默通过。"""
    assert TeamCreate(name=" 工作室 ").name == "工作室"
    assert TeamUpdate(description=" 简介 ").description == "简介"
    for body in ({}, {"name": None}, {"color": "#123"}, {"unexpected": "value"}):
        with pytest.raises(ValidationError):
            TeamUpdate.model_validate(body)
    for body in ({}, {"role": None}, {"role": "owner"}, {"monthly_limit_fen": "100"}, {"monthly_limit_fen": True}):
        with pytest.raises(ValidationError):
            TeamMemberPatch.model_validate(body)
    assert TeamMemberPatch(monthly_limit_fen=None).model_dump(exclude_unset=True) == {"monthly_limit_fen": None}
    assert TeamMemberPatch(monthly_limit_fen=0).monthly_limit_fen == 0


def test_teams_owner_field_overrides_stale_member_owner_marker():
    """owner_user_id 不匹配时，残留 owner 标记不授予所有者权限。"""
    team = Team(owner_user_id=2)
    assert member_role(team, TeamMember(user_id=1, role="owner")) == "member"
    assert member_role(team, TeamMember(user_id=1, role="admin")) == "admin"
    assert member_role(team, TeamMember(user_id=2, role="member")) == "owner"


def test_teams_tokens_are_bounded_and_only_hash_is_mapped():
    """数据库中无明文 token 列；摘要长度固定且输入令牌长度受限。"""
    token = "a" * 43
    digest = hash_token(token)
    assert len(digest) == 64 and digest != token
    assert hash_token(token) == digest
    assert "token" not in TeamInvitation.__table__.columns
    for invalid in ("", "short", "a" * 257):
        with pytest.raises(ValidationError):
            TeamInvitationToken(token=invalid)


def test_teams_invite_links_only_use_allowlisted_frontend_origins(monkeypatch):
    """浏览器本地源生成可打开的前端链接，任意或畸形 Origin 不进入邀请邮件。"""
    from app.config import get_settings

    settings = get_settings()
    monkeypatch.setattr(settings, "public_base_url", "https://www.printfilm.com")
    monkeypatch.setattr(settings, "cors_origins", "http://127.0.0.1:5173,https://www.printfilm.com")
    assert invitation_frontend_base("http://127.0.0.1:5173") == "http://127.0.0.1:5173"
    for origin in (None, "https://evil.example", "http://127.0.0.1:5173/teams", "null", "http://127.0.0.1:5173@evil.example", "http://["):
        assert invitation_frontend_base(origin) == "https://www.printfilm.com"

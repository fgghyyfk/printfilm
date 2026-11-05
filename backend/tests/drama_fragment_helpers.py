"""任务恢复测试共用的建数工具。"""
from uuid import uuid4

from app.models import Project, ProjectStatus, Shot, Template
from app.models_drama import DramaEpisode, DramaEpisodeFragment, DramaProject
from tests.conftest import make_user


async def seed_project(db, *, status=ProjectStatus.DONE):
    """创建含旧成片和一镜素材的归属项目。"""
    user = await make_user(db)
    tpl = Template(id=f"recovery-{uuid4().hex}", name="恢复模板", style_prefix="x")
    db.add(tpl)
    await db.flush()
    project = Project(user_id=user.id, template_id=tpl.id, source_text="测试", status=status,
                      final_video_url="/static/old.mp4", error_msg="旧错误")
    db.add(project)
    await db.flush()
    shot = Shot(project_id=project.id, shot_no=1, image_url="/static/old.png", narration="测试")
    db.add(shot)
    await db.commit()
    return user, project, shot


async def make_episode(db):
    """创建只属于本用例的两镜分集。"""
    user = await make_user(db)
    project = DramaProject(user_id=user.id, title="test")
    db.add(project)
    await db.flush()
    episode = DramaEpisode(project_id=project.id, name="test", params={"episodeNumber": 1})
    db.add(episode)
    await db.flush()
    rows = [
        DramaEpisodeFragment(episode_id=episode.id, sort_order=i, content=f"原稿{i}", params={"user_set": True})
        for i in range(2)
    ]
    db.add_all(rows)
    await db.flush()
    return user, project, episode, rows

"""成片转存的纯函数校验，不连接数据库。"""
from app.marketplace.handoff import object_key


def test_object_key_from_same_bucket_url():
    key = object_key(
        "https://reelbay-pri.oss-cn-hangzhou.aliyuncs.com/kepu/generated/p1/final.mp4?Expires=1",
        bucket="reelbay-pri",
        public_base="https://reelbay-pri.oss-cn-hangzhou.aliyuncs.com",
        folder="kepu",
    )
    assert key == "kepu/generated/p1/final.mp4"


def test_object_key_ignores_local_static_and_marketplace_prefix():
    assert object_key("/static/generated/p1/final.mp4", bucket="b", public_base="https://b.example", folder="kepu") is None
    assert object_key("marketplace/works/u1/2/a.mp4", bucket="b", public_base="https://b.example", folder="kepu") is None


def test_object_key_accepts_raw_object_key():
    assert object_key("kepu/generated/p8/final.mp4", bucket="b", public_base="https://b.example", folder="kepu") == "kepu/generated/p8/final.mp4"

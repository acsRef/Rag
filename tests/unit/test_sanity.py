"""环境护栏冒烟：证明 unit 测试跑在哨兵配置下。"""

from app.config import settings


def test_credentials_are_sentinels():
    assert settings.minimax_api_key == "test-not-real"
    assert settings.siliconflow_api_key == "test-not-real"
    # 不得指向开发库（integration conftest 可能把它改指 ragent_test，那也合法）
    assert not settings.database_url.endswith("/ragent")

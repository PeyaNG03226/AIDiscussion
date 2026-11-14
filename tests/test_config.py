"""配置层测试。"""

from pathlib import Path

from aidiscussion.config import Settings


def make_settings(**overrides: object) -> Settings:
    """构造不读取 .env 的配置，保证测试结果只受显式参数影响。"""
    return Settings(_env_file=None, **overrides)  # type: ignore[arg-type]


def test_defaults() -> None:
    settings = make_settings()

    assert settings.app_name == "AIDiscussion"
    assert settings.llm_model == "deepseek-chat"
    assert settings.has_llm_key is False


def test_env_override(monkeypatch) -> None:
    monkeypatch.setenv("AIDISCUSSION_LLM_MODEL", "qwen-plus")
    monkeypatch.setenv("AIDISCUSSION_LLM_API_KEY", "sk-test-1234567890")

    settings = make_settings()

    assert settings.llm_model == "qwen-plus"
    assert settings.has_llm_key is True


def test_masked_key_hides_middle() -> None:
    settings = make_settings(llm_api_key="sk-test-1234567890")

    masked = settings.masked_llm_key()

    assert masked == "sk-t...7890"
    assert "123456" not in masked


def test_masked_key_when_missing() -> None:
    assert make_settings().masked_llm_key() == "<未配置>"


def test_ensure_data_dir(tmp_path: Path) -> None:
    target = tmp_path / "data"
    settings = make_settings(data_dir=target)

    assert settings.ensure_data_dir() == target
    assert target.is_dir()

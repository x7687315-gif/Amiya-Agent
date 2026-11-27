import pytest

from config import ConfigError, load_settings


def test_missing_key_raises(monkeypatch):
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    with pytest.raises(ConfigError):
        load_settings()


def test_loads_with_key(monkeypatch):
    monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-test-123")
    monkeypatch.delenv("DEEPSEEK_MODEL", raising=False)
    monkeypatch.delenv("DEEPSEEK_THINKING", raising=False)
    s = load_settings()
    assert s.api_key == "sk-test-123"
    assert s.model == "deepseek-flash"  # 2.x 模型迁移（裁决 C，可回滚）
    assert s.deepseek_thinking == "off"
    assert s.history_limit == 20


# ----- 持久化必须同步 os.environ（2026-09-06 审查 CRITICAL-1 修复） -----
# 背景：load_dotenv() 只在 config 模块导入时执行一次；运行时换肤/拖栏后
# _rebuild_ui_async 经 load_settings()（读 os.getenv）重读配置——若 persist 只
# 写 .env 文件不更新环境变量，重建会拿到**进程启动时**的过期皮肤/栏宽，
# 「点击立即生效」实际从未生效（注释宣称的"含刚写入的 UI_SKIN"不成立）。


def test_persist_ui_skin_syncs_environ_for_runtime_rebuild(monkeypatch, tmp_path):
    from config import load_settings, persist_ui_skin

    monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-test")
    monkeypatch.setenv("UI_SKIN", "starry")  # 进程启动时的旧值；teardown 自动还原
    env_path = tmp_path / ".env"
    env_path.write_text("UI_SKIN=starry\n", encoding="utf-8")

    persist_ui_skin("warm", env_path=env_path)

    assert env_path.read_text(encoding="utf-8") == "UI_SKIN=warm\n"  # 文件已更新
    assert load_settings().ui_skin == "warm"  # 重读配置必须拿到新皮肤


def test_persist_ui_layout_syncs_environ(monkeypatch, tmp_path):
    from config import load_settings, persist_ui_layout

    monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-test")
    monkeypatch.setenv("UI_LEFT_WIDTH", "260")
    monkeypatch.setenv("UI_RIGHT_WIDTH", "300")
    env_path = tmp_path / ".env"
    env_path.write_text(
        "UI_LEFT_WIDTH=260\nUI_RIGHT_WIDTH=300\n", encoding="utf-8"
    )

    persist_ui_layout(320, 380, env_path=env_path)

    s = load_settings()
    assert s.ui_left_width == 320
    assert s.ui_right_width == 380


# ----- Phase 8：.env 原子写 -----

def test_atomic_write_replaces_content_and_leaves_no_temp(tmp_path):
    from config import _atomic_write_text

    path = tmp_path / ".env"
    path.write_text("OLD=1\n", encoding="utf-8")

    _atomic_write_text(path, "NEW=2\n")

    assert path.read_text(encoding="utf-8") == "NEW=2\n"
    assert not (tmp_path / ".env.tmp").exists()  # 临时文件不残留


def test_atomic_write_preserves_content_on_replace_failure(monkeypatch, tmp_path):
    """写盘失败（模拟 replace 抛错）时旧文件必须完好——这正是原子写的意义。"""
    import config

    path = tmp_path / ".env"
    path.write_text("SECRET=keep-me\n", encoding="utf-8")

    def boom(src, dst):
        raise OSError("disk full")

    monkeypatch.setattr(config.os, "replace", boom, raising=False)
    with pytest.raises(OSError):
        config._atomic_write_text(path, "SECRET=lost\n")

    assert path.read_text(encoding="utf-8") == "SECRET=keep-me\n"
    assert not (tmp_path / ".env.tmp").exists()

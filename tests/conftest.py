"""UI 测试的通用夹具：把 ft.Control.update 桩成空操作，便于在离线（无 page）环境构造/驱动控件。"""
from __future__ import annotations

import flet as ft
import pytest


@pytest.fixture(autouse=True)
def _noop_control_update(monkeypatch):
    monkeypatch.setattr(ft.Control, "update", lambda self, *a, **k: None)

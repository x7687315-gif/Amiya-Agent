"""出站消息序列校验（协议测试的离线层）。"""
from __future__ import annotations

from core.protocol_guard import (
    CONSECUTIVE_ASSISTANT,
    EMPTY_MESSAGES,
    FIRST_NOT_USER,
    UNKNOWN_ROLE,
    validate_messages,
)


def test_valid_sequence_passes():
    assert validate_messages([
        {"role": "system", "content": "s"},
        {"role": "user", "content": "u1"},
        {"role": "assistant", "content": "a1"},
        {"role": "user", "content": "u2"},
    ]) == []


def test_first_non_system_must_be_user():
    """服务端工具请求对这条最严格（2026-11-27 实机 400 的根因）。"""
    issues = validate_messages([
        {"role": "assistant", "content": "a0"},
        {"role": "user", "content": "u1"},
    ])
    assert FIRST_NOT_USER in issues


def test_consecutive_assistant_detected():
    issues = validate_messages([
        {"role": "user", "content": "u"},
        {"role": "assistant", "content": "a1"},
        {"role": "assistant", "content": "a2"},
    ])
    assert CONSECUTIVE_ASSISTANT in issues


def test_consecutive_alerts_are_deduplicated():
    issues = validate_messages([
        {"role": "user", "content": "u"},
        {"role": "assistant", "content": "a1"},
        {"role": "assistant", "content": "a2"},
        {"role": "assistant", "content": "a3"},
    ])
    assert issues.count(CONSECUTIVE_ASSISTANT) == 1


def test_empty_messages():
    assert validate_messages([]) == [EMPTY_MESSAGES]


def test_unknown_role_flagged():
    issues = validate_messages([
        {"role": "user", "content": "u"},
        {"role": "weird", "content": "x"},
    ])
    assert UNKNOWN_ROLE in issues


def test_system_only_is_first_not_user():
    assert FIRST_NOT_USER in validate_messages([{"role": "system", "content": "s"}])

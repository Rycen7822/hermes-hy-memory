from __future__ import annotations

from capture import build_capture_messages


def _contents(messages):
    return [message["content"] for message in messages or []]


def test_capture_filters_assistant_dated_task_narrative():
    messages = build_capture_messages(
        "Please continue the implementation.",
        "On 2026-05-31, the user requested an upgrade plan and I completed the draft.",
    )

    assert messages == [{"role": "user", "content": "Please continue the implementation."}]


def test_capture_filters_phase_logs_and_commit_artifacts():
    phase_messages = build_capture_messages(
        "Proceed.",
        "Phase 2 completed; commit SHA abcdef1234567890 was verified and PR #42 is ready.",
    )

    assert phase_messages == [{"role": "user", "content": "Proceed."}]


def test_capture_retains_durable_preference_facts():
    messages = build_capture_messages(
        "Remember that I prefer concise status reports with gate results.",
        "The user prefers concise status reports with gate results.",
    )

    contents = _contents(messages)
    assert "Remember that I prefer concise status reports with gate results." in contents
    assert "The user prefers concise status reports with gate results." in contents

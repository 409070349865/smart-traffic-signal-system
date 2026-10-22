import pytest

from app.config import load_settings


def test_defaults_from_yaml():
    s = load_settings()
    assert s.preprocessing.frame_skip >= 1
    assert s.video.source
    assert "prototype" in s.safety_notice.lower()


def test_env_overrides_yaml(monkeypatch):
    monkeypatch.setenv("VIDEO_SOURCE", "sample_data/other.mp4")
    monkeypatch.setenv("FRAME_SKIP", "5")
    monkeypatch.setenv("VIDEO_LOOP", "true")
    s = load_settings()
    assert s.video.source == "sample_data/other.mp4"
    assert s.preprocessing.frame_skip == 5
    assert s.video.loop is True


def test_invalid_env_value_rejected(monkeypatch):
    monkeypatch.setenv("FRAME_SKIP", "abc")
    with pytest.raises(ValueError):
        load_settings()


def test_out_of_range_value_rejected(monkeypatch):
    monkeypatch.setenv("FRAME_SKIP", "0")
    with pytest.raises(ValueError):
        load_settings()

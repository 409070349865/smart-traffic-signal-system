from pathlib import Path

import cv2
import numpy as np
import pytest

from app.vision.video_input import (ReadStatus, SourceType, VideoInput,
                                    VideoSourceError, classify_source)


@pytest.fixture()
def video_file(tmp_path: Path) -> Path:
    path = tmp_path / "clip.avi"
    w = cv2.VideoWriter(str(path), cv2.VideoWriter_fourcc(*"MJPG"), 10, (160, 120))
    assert w.isOpened()
    for i in range(12):
        frame = np.full((120, 160, 3), i * 10, np.uint8)
        w.write(frame)
    w.release()
    return path


def test_classify_source():
    assert classify_source(0) is SourceType.WEBCAM
    assert classify_source("1") is SourceType.WEBCAM
    assert classify_source("rtsp://cam/stream") is SourceType.STREAM
    assert classify_source("http://cam/video") is SourceType.STREAM
    assert classify_source("sample_data/a.mp4") is SourceType.FILE


def test_read_all_frames_then_end(video_file):
    with VideoInput(video_file) as v:
        assert v.get_resolution() == (160, 120)
        assert v.get_fps() == pytest.approx(10, abs=0.5)
        assert v.get_frame_count() == 12
        n = 0
        while True:
            status, frame = v.read_frame()
            if status is ReadStatus.END_OF_VIDEO:
                assert frame is None
                break
            assert status is ReadStatus.OK and frame.shape == (120, 160, 3)
            n += 1
        assert n == 12


def test_loop_restarts(video_file):
    with VideoInput(video_file, loop=True) as v:
        for _ in range(30):                      # more than 12 frames
            status, _ = v.read_frame()
            assert status is ReadStatus.OK


def test_missing_file():
    with pytest.raises(VideoSourceError, match="not found"):
        VideoInput("does/not/exist.mp4").open_video()


def test_unsupported_extension(tmp_path):
    f = tmp_path / "notes.txt"
    f.write_text("hi")
    with pytest.raises(VideoSourceError, match="Unsupported"):
        VideoInput(f).open_video()


def test_garbage_file_with_valid_extension(tmp_path):
    f = tmp_path / "bad.mp4"
    f.write_bytes(b"this is not a video" * 50)
    with pytest.raises(VideoSourceError):
        VideoInput(f).open_video()


def test_read_before_open_and_after_release(video_file):
    v = VideoInput(video_file)
    assert v.read_frame() == (ReadStatus.NOT_OPENED, None)
    v.open_video()
    v.release()
    assert v.read_frame()[0] is ReadStatus.NOT_OPENED
    assert v.get_fps() == 0.0 and v.get_resolution() == (0, 0)


def test_corrupted_frames_are_skipped_then_reported(video_file):
    class FakeCap:
        def isOpened(self): return True
        def read(self): return True, np.zeros((0, 0, 3), np.uint8)   # empty frame
        def get(self, prop): return 100 if prop == cv2.CAP_PROP_FRAME_COUNT else 0
        def release(self): pass

    v = VideoInput(video_file, max_consecutive_bad_frames=3)
    v._cap = FakeCap()
    assert v.read_frame() == (ReadStatus.CORRUPTED, None)


def test_live_source_disconnect_and_reconnect_failure():
    class DeadCap:
        def isOpened(self): return True
        def read(self): return False, None
        def get(self, prop): return 0
        def release(self): pass

    v = VideoInput("rtsp://127.0.0.1:1/none", max_reconnect_attempts=1,
                   reconnect_delay_s=0)
    v._cap = DeadCap()
    status, frame = v.read_frame()
    assert status is ReadStatus.DISCONNECTED and frame is None


def test_unavailable_webcam():
    with pytest.raises(VideoSourceError):
        VideoInput(99).open_video()


def test_credentials_hidden_in_logs_name():
    v = VideoInput("rtsp://user:secret@10.0.0.5/stream")
    assert "secret" not in v._safe_name()

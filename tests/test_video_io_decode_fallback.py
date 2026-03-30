import unittest
from types import SimpleNamespace
from unittest.mock import patch

from cleaningcar import video_io


class _DummyCapture:
    def __init__(self, opened):
        self._opened = opened

    def isOpened(self):
        return self._opened

    def release(self):
        return None


class VideoIoDecodeFallbackTests(unittest.TestCase):
    @staticmethod
    def _args(hw_decode=True):
        return SimpleNamespace(
            hw_decode=hw_decode,
            _config={"video": {"rtsp_latency_ms": 200, "rtsp_appsink_max_buffers": 1}},
        )

    @patch("cleaningcar.video_io.cv2.VideoCapture")
    def test_create_video_reader_reports_hardware_decode_success(self, capture_ctor):
        capture_ctor.return_value = _DummyCapture(True)

        cap, meta = video_io.create_video_reader("rtsp://camera", self._args(hw_decode=True))

        self.assertIsNotNone(cap)
        self.assertEqual(meta["decode_mode"], "hw")
        self.assertFalse(meta["fallback_used"])
        self.assertEqual(meta["source_kind"], "rtsp")

    @patch("cleaningcar.video_io.cv2.VideoCapture")
    def test_create_video_reader_falls_back_to_software_decode(self, capture_ctor):
        capture_ctor.side_effect = [_DummyCapture(False), _DummyCapture(True)]

        cap, meta = video_io.create_video_reader("rtsp://camera", self._args(hw_decode=True))

        self.assertIsNotNone(cap)
        self.assertEqual(meta["decode_mode"], "sw")
        self.assertTrue(meta["fallback_used"])
        self.assertTrue(meta["fallback_reason"])

    @patch("cleaningcar.video_io.cv2.VideoCapture")
    def test_create_video_reader_reports_total_failure_after_fallback(self, capture_ctor):
        capture_ctor.side_effect = [_DummyCapture(False), _DummyCapture(False)]

        cap, meta = video_io.create_video_reader("rtsp://camera", self._args(hw_decode=True))

        self.assertIsNone(cap)
        self.assertEqual(meta["decode_mode"], "sw")
        self.assertTrue(meta["fallback_used"])
        self.assertTrue(meta["fallback_reason"])


if __name__ == "__main__":
    unittest.main()

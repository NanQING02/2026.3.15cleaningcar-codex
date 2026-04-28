import queue
import sys
import types
import unittest
from types import SimpleNamespace

import numpy as np


def _install_fake_rknn():
    if "rknnlite.api" in sys.modules:
        return
    pkg = types.ModuleType("rknnlite")
    api = types.ModuleType("rknnlite.api")

    class _RKNNLite:
        pass

    api.RKNNLite = _RKNNLite
    pkg.api = api
    sys.modules["rknnlite"] = pkg
    sys.modules["rknnlite.api"] = api


_install_fake_rknn()

from cleaningcar.constants import LICENSE_CLASS  # noqa: E402
from cleaningcar.worker import DetectWorker  # noqa: E402


class _FakeRK:
    @staticmethod
    def inference(inputs, data_format):
        return [np.array([1.0], dtype=np.float32)]


class _FailingRK:
    @staticmethod
    def inference(inputs, data_format):
        raise RuntimeError("boom")


class _FakePostprocessor:
    @staticmethod
    def prepare(frame):
        return np.zeros((1, 16, 16, 3), dtype=np.uint8), {"dummy": 1}

    @staticmethod
    def postprocess(outputs):
        boxes = np.array([[0.0, 0.0, 20.0, 20.0]], dtype=np.float32)
        classes = np.array([0], dtype=np.int32)
        scores = np.array([0.95], dtype=np.float32)
        return boxes, classes, scores

    @staticmethod
    def map_boxes_to_original(boxes, lb_info):
        return boxes


class _FakeDualLpr:
    @staticmethod
    def infer_frame(frame, conf_thresh, iou_thresh):
        return [
            {
                "box": [1.0, 2.0, 11.0, 12.0],
                "score": 0.9,
                "text": "ABC1234",
                "plate_color": "blue",
                "plate_color_conf": 0.88,
                "plate_type": "single",
                "landmarks": [[1, 2], [11, 2], [11, 12], [1, 12]],
            }
        ]


class WorkerPlateColorConfTests(unittest.TestCase):
    def _build_worker(self, rk=None):
        worker = DetectWorker.__new__(DetectWorker)
        worker.idx = 0
        worker.args = SimpleNamespace(no_draw=True, conf=0.25, iou=0.5)
        worker.core_mask = None
        worker.task_q = queue.Queue()
        worker.result_q = queue.Queue()
        worker.detect_mask = None
        worker.rk = rk or _FakeRK()
        worker.detector_postprocessor = _FakePostprocessor()
        worker.dual_lpr = _FakeDualLpr()
        worker.plate_infer_stride = 1
        worker.frames = 0
        worker.infer_time = 0.0
        return worker

    def test_worker_passthrough_plate_color_conf_to_det_payload(self):
        worker = self._build_worker()

        frame = np.zeros((48, 48, 3), dtype=np.uint8)
        worker.task_q.put((0, frame))
        worker.task_q.put(None)

        worker.run()

        result = worker.result_q.get_nowait()
        self.assertIsNotNone(result)
        _, _, _, _, det_payload = result
        plate_items = [item for item in det_payload if int(item.get("cls", -1)) == int(LICENSE_CLASS)]
        self.assertEqual(len(plate_items), 1)
        self.assertAlmostEqual(float(plate_items[0]["plate_color_conf"]), 0.88, places=6)

    def test_worker_reports_empty_result_and_finishes_queue_when_frame_fails(self):
        worker = self._build_worker(rk=_FailingRK())
        frame = np.zeros((48, 48, 3), dtype=np.uint8)
        worker.task_q.put((12, frame, 123.0))
        worker.task_q.put(None)

        worker.run()

        result = worker.result_q.get_nowait()
        self.assertIsNotNone(result)
        frame_idx, capture_ts, frame_out, rows, det_payload = result
        self.assertEqual(frame_idx, 12)
        self.assertEqual(capture_ts, 123.0)
        self.assertIs(frame_out, frame)
        self.assertEqual(rows, [])
        self.assertEqual(det_payload, [])
        self.assertIsNone(worker.result_q.get_nowait())
        self.assertEqual(worker.task_q.unfinished_tasks, 0)


if __name__ == "__main__":
    unittest.main()

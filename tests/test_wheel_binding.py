import json
import tempfile
import time
import unittest
from pathlib import Path

import numpy as np

from cleaningcar.events import EventManager
from cleaningcar.runtime_config import load_config
from cleaningcar.wheel import WheelResultCache, resolve_wheel_class_name, resolve_wheel_settings


class _DummyZoneManager:
    @staticmethod
    def update_track(track_id, anchor_point, frame_idx):
        return None, {"enter_a": False, "exit_a": False, "enter_b": False, "exit_b": False}

    @staticmethod
    def drop_track(track_id):
        return None

    @staticmethod
    def resolve_direction(state):
        return 0, ""


class WheelBindingTests(unittest.TestCase):
    def _manager(self, wheel_provider=None):
        temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(temp_dir.cleanup)
        config = {
            "logic": {
                "default_plate_color": "",
                "default_plate_color_conf": 0.0,
            },
            "shadow_pool": {
                "max_candidates": 20,
                "max_age_frames": 30,
            },
            "event_capture_dir": temp_dir.name,
            "event_output_dir": temp_dir.name,
            "lane_name": "lane-a",
        }
        return EventManager(
            config,
            fps=25.0,
            frame_size=(128, 128),
            zone_manager=_DummyZoneManager(),
            uploader=object(),
            wheel_result_provider=wheel_provider,
        )

    @staticmethod
    def _type5_event():
        return {
            "id": "evt-1",
            "type": 5,
            "captureTime": "2026-04-28 12:00:00",
            "captureImage": "",
            "plateNumber": "鲁A12345",
            "videoDuration": 12.5,
        }

    @staticmethod
    def _track_state():
        return {
            "plate_conf_history": [0.98],
            "vehicle_conf_history": [0.91],
            "wash_end_time": "2026-04-28 12:00:00",
            "last_frame_idx": 30,
            "wash_duration": 7.3,
        }

    def test_class_id_maps_to_expected_bucket_names(self):
        class_names = ["0-25", "25-50", "50-75", "75-100"]

        self.assertEqual(resolve_wheel_class_name(class_names, 0), "0-25")
        self.assertEqual(resolve_wheel_class_name(class_names, 1), "25-50")
        self.assertEqual(resolve_wheel_class_name(class_names, 2), "50-75")
        self.assertEqual(resolve_wheel_class_name(class_names, 3), "75-100")

    def test_edge_detection_does_not_update_cache_but_center_detection_does(self):
        cache = WheelResultCache(bind_window_seconds=30.0, image_quality=80)
        frame = np.full((100, 100, 3), 180, dtype=np.uint8)
        classes = np.array([3], dtype=np.int64)
        scores = np.array([0.93], dtype=np.float32)

        updated = cache.update_from_detections(
            side="left",
            frame=frame,
            capture_ts=1000.0,
            boxes=np.array([[0.0, 40.0, 18.0, 60.0]], dtype=np.float32),
            classes=classes,
            scores=scores,
            center_min_margin_ratio=0.15,
            class_names=["0-25", "25-50", "50-75", "75-100"],
        )
        self.assertFalse(updated)
        self.assertEqual(cache.get_recent_results(now_ts=1000.0), [])

        updated = cache.update_from_detections(
            side="left",
            frame=frame,
            capture_ts=1001.0,
            boxes=np.array([[40.0, 40.0, 60.0, 60.0]], dtype=np.float32),
            classes=classes,
            scores=scores,
            center_min_margin_ratio=0.15,
            class_names=["0-25", "25-50", "50-75", "75-100"],
        )
        self.assertTrue(updated)

        results = cache.get_recent_results(now_ts=1001.0)
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["side"], "left")
        self.assertEqual(results[0]["className"], "75-100")
        self.assertTrue(results[0]["imageBase64"])

    def test_type5_payload_only_attaches_simplified_wheel_fields(self):
        cache = WheelResultCache(bind_window_seconds=30.0, image_quality=80)
        frame = np.full((80, 80, 3), 120, dtype=np.uint8)
        now_ts = time.time()
        cache.update_from_detections(
            side="left",
            frame=frame,
            capture_ts=now_ts,
            boxes=np.array([[28.0, 28.0, 52.0, 52.0]], dtype=np.float32),
            classes=np.array([2], dtype=np.int64),
            scores=np.array([0.88], dtype=np.float32),
            center_min_margin_ratio=0.15,
            class_names=["0-25", "25-50", "50-75", "75-100"],
        )

        manager = self._manager(wheel_provider=cache)
        payload = manager._build_api_payload(self._type5_event(), self._track_state(), frame_idx=30)

        self.assertIn("wheelResults", payload)
        self.assertEqual(len(payload["wheelResults"]), 1)
        self.assertEqual(
            set(payload["wheelResults"][0].keys()),
            {"side", "captureTime", "imageBase64", "className"},
        )
        self.assertEqual(payload["wheelResults"][0]["side"], "left")
        self.assertEqual(payload["wheelResults"][0]["className"], "50-75")

    def test_type5_payload_omits_wheel_results_when_provider_absent(self):
        manager = self._manager(wheel_provider=None)

        payload = manager._build_api_payload(self._type5_event(), self._track_state(), frame_idx=30)

        self.assertNotIn("wheelResults", payload)

    def test_runtime_config_includes_wheel_defaults(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "config.json"
            path.write_text(
                json.dumps(
                    {
                        "system": {"device_id": "wheel-test"},
                        "video": {"source": "demo.mp4"},
                        "logic": {"lane_name": "lane"},
                        "zones": {
                            "zone_a_detection": [[0.0, 0.0], [1.0, 0.0], [1.0, 1.0]],
                            "zone_b_wash": [[0.0, 0.0], [1.0, 0.0], [1.0, 1.0]],
                            "flow_vector": {"start": [0.0, 0.0], "end": [1.0, 1.0]},
                        },
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )

            config = load_config(str(path))

        self.assertIn("wheel", config)
        self.assertFalse(config["wheel"]["enabled"])
        self.assertEqual(config["wheel"]["classes"], ["0-25", "25-50", "50-75", "75-100"])
        self.assertEqual(config["wheel"]["target_fps"], 5.0)

    def test_wheel_model_path_prefers_project_root_when_relative_path_exists(self):
        settings = resolve_wheel_settings(
            {"wheel": {"model": "models/wheel/2026.4.28CRwheelfp.rknn"}},
            base_dir=Path(__file__).resolve().parent,
        )

        self.assertTrue(str(settings["model_path"]).endswith("models/wheel/2026.4.28CRwheelfp.rknn"))
        self.assertTrue(settings["model_path"].exists())


if __name__ == "__main__":
    unittest.main()

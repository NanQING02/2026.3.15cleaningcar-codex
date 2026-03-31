import json
import tempfile
import unittest
from pathlib import Path

from config_manager import ConfigManager
from web.config_tiers import CONFIG_FIELD_REGISTRY


class GlobalVideoCleanupTests(unittest.TestCase):
    def test_config_manager_strips_obsolete_global_video_fields(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "config.json"
            payload = {
                "system": {"device_id": "cam-a"},
                "video": {"source": "demo.mp4", "save_video": "demo-out.mp4"},
                "logic": {"enable_global_video": True},
                "zones": {
                    "zone_a_detection": [[0.0, 0.0], [1.0, 0.0], [1.0, 1.0]],
                    "zone_b_wash": [[0.0, 0.0], [1.0, 0.0], [1.0, 1.0]],
                    "flow_vector": {"start": [0.0, 0.0], "end": [1.0, 1.0]},
                },
            }
            path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")

            manager = ConfigManager(path)

            self.assertNotIn("save_video", manager.video)
            self.assertNotIn("enable_global_video", manager.logic)

    def test_web_config_registry_hides_global_video_fields(self):
        field_paths = {item["path"] for item in CONFIG_FIELD_REGISTRY}

        self.assertNotIn("video.save_video", field_paths)
        self.assertNotIn("logic.enable_global_video", field_paths)

    def test_config_manager_strips_obsolete_per_id_recording_tuning_fields(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "config.json"
            payload = {
                "system": {"device_id": "cam-a"},
                "video": {"source": "demo.mp4"},
                "logic": {
                    "per_id_downscale_ratio": 0.5,
                    "per_id_frame_stride": 2,
                    "per_id_target_width": 1280,
                    "per_id_target_height": 720,
                    "per_id_auto_adapt": True,
                    "per_id_auto_cpu_high": 80,
                    "per_id_auto_cpu_low": 40,
                    "per_id_max_frame_stride": 4,
                },
                "zones": {
                    "zone_a_detection": [[0.0, 0.0], [1.0, 0.0], [1.0, 1.0]],
                    "zone_b_wash": [[0.0, 0.0], [1.0, 0.0], [1.0, 1.0]],
                    "flow_vector": {"start": [0.0, 0.0], "end": [1.0, 1.0]},
                },
            }
            path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")

            manager = ConfigManager(path)

            self.assertNotIn("per_id_downscale_ratio", manager.logic)
            self.assertNotIn("per_id_frame_stride", manager.logic)
            self.assertNotIn("per_id_target_width", manager.logic)
            self.assertNotIn("per_id_target_height", manager.logic)
            self.assertNotIn("per_id_auto_adapt", manager.logic)
            self.assertNotIn("per_id_auto_cpu_high", manager.logic)
            self.assertNotIn("per_id_auto_cpu_low", manager.logic)
            self.assertNotIn("per_id_max_frame_stride", manager.logic)

    def test_web_config_registry_hides_per_id_recording_tuning_fields(self):
        field_paths = {item["path"] for item in CONFIG_FIELD_REGISTRY}

        self.assertNotIn("logic.per_id_downscale_ratio", field_paths)
        self.assertNotIn("logic.per_id_frame_stride", field_paths)
        self.assertNotIn("logic.per_id_auto_adapt", field_paths)
        self.assertNotIn("logic.per_id_auto_cpu_high", field_paths)
        self.assertNotIn("logic.per_id_auto_cpu_low", field_paths)
        self.assertNotIn("logic.per_id_max_frame_stride", field_paths)


if __name__ == "__main__":
    unittest.main()

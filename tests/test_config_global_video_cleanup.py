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


if __name__ == "__main__":
    unittest.main()

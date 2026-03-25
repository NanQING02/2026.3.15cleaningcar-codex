import json
import tempfile
import unittest
from pathlib import Path

from fastapi import HTTPException

from web import server, state


class WebConfigNamespaceTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.base = Path(self._tmp.name)
        self.config_dir = self.base / "configs"
        self.config_dir.mkdir(parents=True, exist_ok=True)
        self.original_config_path = state.CONFIG_PATH

    def tearDown(self):
        state.set_config_path(self.original_config_path)

    def write_config(self, name: str, device_id: str, lane_name: str = "") -> Path:
        payload = {
            "system": {"device_id": device_id},
            "logic": {"lane_name": lane_name or name},
            "video": {"source": "demo.mp4"},
        }
        path = self.config_dir / name
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        return path

    def test_validate_device_id_unique_rejects_duplicates(self):
        target = self.write_config("config_main.json", "camera-a")
        self.write_config("config_other.json", "camera-a")
        with self.assertRaises(HTTPException) as ctx:
            server._validate_device_id_unique(target, {"system": {"device_id": "camera-a"}})
        self.assertEqual(ctx.exception.status_code, 400)
        self.assertIn("device_id", str(ctx.exception.detail))

    def test_list_config_files_returns_device_ids(self):
        active = self.write_config("config.json", "camera-a")
        self.write_config("config_b.json", "camera-b")
        state.set_config_path(active)

        payload = server.list_config_files()

        self.assertEqual(payload["active"], "config.json")
        self.assertEqual(payload["device_ids"]["config.json"], "camera-a")
        self.assertEqual(payload["device_ids"]["config_b.json"], "camera-b")

    def test_read_config_uses_key(self):
        active = self.write_config("config.json", "camera-a", lane_name="A")
        self.write_config("config_b.json", "camera-b", lane_name="B")
        state.set_config_path(active)

        payload = server.read_config("config_b.json")

        self.assertEqual(payload["system"]["device_id"], "camera-b")
        self.assertEqual(payload["logic"]["lane_name"], "B")


if __name__ == "__main__":
    unittest.main()

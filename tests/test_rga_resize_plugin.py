import unittest

from future_modules.acceleration import rga_resize_plugin


class RgaResizePluginTests(unittest.TestCase):
    def test_bgr_stride_aligns_to_16_bytes(self):
        self.assertEqual(rga_resize_plugin._align_bgr888_stride(76), 80)
        self.assertEqual(rga_resize_plugin._align_bgr888_stride(84), 96)
        self.assertEqual(rga_resize_plugin._align_bgr888_stride(128), 128)


if __name__ == "__main__":
    unittest.main()

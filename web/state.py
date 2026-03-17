from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CONFIG_PATH = ROOT / 'config.json'
TEMPLATE_PATH = ROOT / 'web' / 'templates' / 'zone_editor.html'
RUN_SCRIPT = ROOT / 'run_zone_detect.py'


class FrameCache:
    def __init__(self):
        self.data = None
        self.size = (960, 540)

    def clear(self):
        self.data = None


FRAME_CACHE = FrameCache()


def set_config_path(path):
    global CONFIG_PATH
    CONFIG_PATH = Path(path).resolve()
    return CONFIG_PATH

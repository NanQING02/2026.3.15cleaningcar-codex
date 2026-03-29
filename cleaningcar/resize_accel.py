import cv2

try:
    from future_modules.acceleration import rga_resize_plugin as _rga_plugin
except Exception:
    _rga_plugin = None

if _rga_plugin is not None:
    _rga_resize = getattr(_rga_plugin, "rga_resize", None)
    _RGA_READY = bool(getattr(_rga_plugin, "RGA_OK", False) and callable(_rga_resize))
else:
    _rga_resize = None
    _RGA_READY = False


def resize_backend_name():
    return "rga" if _RGA_READY else "cv2"


def resize_bgr(image, target_size, interpolation=cv2.INTER_LINEAR):
    if image is None:
        return None
    tw, th = int(target_size[0]), int(target_size[1])
    if tw <= 0 or th <= 0:
        raise ValueError(f"invalid target_size: {target_size}")
    h, w = image.shape[:2]
    if w == tw and h == th:
        return image
    if (
        _RGA_READY
        and interpolation == cv2.INTER_LINEAR
        and image.ndim == 3
        and image.shape[2] == 3
    ):
        try:
            return _rga_resize(image, (tw, th))
        except Exception:
            pass
    return cv2.resize(image, (tw, th), interpolation=interpolation)

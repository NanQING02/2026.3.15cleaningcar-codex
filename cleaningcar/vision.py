import cv2
import numpy as np

from .constants import PROJECT, REG_MAX

RGA_RESIZE_FUNC = None
try:
    from rga_resize_plugin import rga_resize
    RGA_RESIZE_FUNC = rga_resize
except Exception:
    RGA_RESIZE_FUNC = None

def _is_normalized(points):
    if not points:
        return False
    return all(0.0 <= p[0] <= 1.0 and 0.0 <= p[1] <= 1.0 for p in points)


def scale_polygon(points, width, height):
    if not points:
        return []
    if _is_normalized(points):
        return [(float(x) * width, float(y) * height) for x, y in points]
    return [(float(x), float(y)) for x, y in points]


def scale_point(point, width, height):
    if not point:
        return (0.0, 0.0)
    x, y = point
    if 0.0 <= x <= 1.0 and 0.0 <= y <= 1.0:
        return float(x) * width, float(y) * height
    return float(x), float(y)


def get_anchor_point(box, offset_ratio=0.0):
    if not box:
        return None
    x1, y1, x2, y2 = box
    cx = 0.5 * (x1 + x2)
    height = max(1.0, (y2 - y1))
    offset = float(offset_ratio)
    if offset < 0.0:
        offset = 0.0
    elif offset > 0.95:
        offset = 0.95
    cy = y2 - offset * height
    return (float(cx), float(cy))


def letterbox(im, new_shape=640, color=(114, 114, 114)):
    shape = im.shape[:2]
    if isinstance(new_shape, int):
        new_shape = (new_shape, new_shape)
    r = min(new_shape[0] / shape[0], new_shape[1] / shape[1])
    ratio = (r, r)
    new_unpad = (int(round(shape[1] * r)), int(round(shape[0] * r)))
    dw = new_shape[1] - new_unpad[0]
    dh = new_shape[0] - new_unpad[1]
    dw /= 2
    dh /= 2
    if shape[::-1] != new_unpad:
        im = resize_for_letterbox(im, new_unpad)
    top, bottom = int(round(dh - 0.1)), int(round(dh + 0.1))
    left, right = int(round(dw - 0.1)), int(round(dw + 0.1))
    im = cv2.copyMakeBorder(im, top, bottom, left, right, cv2.BORDER_CONSTANT, value=color)
    return im, ratio, (dw, dh)


def resize_for_letterbox(im, new_unpad):
    if RGA_RESIZE_FUNC is not None:
        try:
            return RGA_RESIZE_FUNC(im, new_unpad)
        except Exception as exc:
            print(f'[rga-resize] failed, fallback to cv2: {exc}')
    return cv2.resize(im, new_unpad, interpolation=cv2.INTER_LINEAR)


def draw_metrics_overlay(frame, lines):
    if frame is None or not lines:
        return
    h, w = frame.shape[:2]
    scale = max(0.45, min(w, h) / 960.0 * 0.6)
    thickness = max(1, int(scale * 2))
    line_gap = max(14, int(18 * scale))
    y = h - 10
    for text in reversed(lines):
        cv2.putText(frame, text, (10, y), cv2.FONT_HERSHEY_SIMPLEX, scale, (255, 255, 255), thickness, cv2.LINE_AA)
        y -= line_gap


def sigmoid(x):
    return 1.0 / (1.0 + np.exp(-x))


def softmax(x, axis):
    x = x - np.max(x, axis=axis, keepdims=True)
    ex = np.exp(x)
    return ex / np.sum(ex, axis=axis, keepdims=True)


def decode_scale(reg, cls, obj, stride):
    B, _, H, W = reg.shape
    reg = reg.reshape(B, 4, REG_MAX, H, W)
    reg = softmax(reg, axis=2)
    reg = (reg * PROJECT.reshape(1, 1, REG_MAX, 1, 1)).sum(axis=2)
    reg = reg * stride
    gy, gx = np.meshgrid(np.arange(H), np.arange(W), indexing='ij')
    gx = (gx + 0.5) * stride
    gy = (gy + 0.5) * stride
    x1 = gx - reg[:, 0]
    y1 = gy - reg[:, 1]
    x2 = gx + reg[:, 2]
    y2 = gy + reg[:, 3]
    boxes = np.stack([x1, y1, x2, y2], axis=-1).reshape(-1, 4)
    cls = sigmoid(cls)
    obj = sigmoid(obj)
    cls = cls.reshape(B, cls.shape[1], -1)
    obj = obj.reshape(B, 1, -1)
    scores = (cls * obj).transpose(0, 2, 1).reshape(-1, cls.shape[1])
    cls_ids = np.argmax(scores, axis=1)
    cls_scores = scores[np.arange(scores.shape[0]), cls_ids]
    cls_flat = cls.transpose(0, 2, 1).reshape(-1, cls.shape[1])
    cls_probs = cls_flat[np.arange(cls_flat.shape[0]), cls_ids]
    return boxes, cls_ids, cls_scores, cls_probs


def nms(boxes, scores, thresh, max_det):
    if boxes.size == 0:
        return np.array([], dtype=int)
    order = scores.argsort()[::-1]
    keep = []
    x1, y1, x2, y2 = boxes[:, 0], boxes[:, 1], boxes[:, 2], boxes[:, 3]
    areas = (x2 - x1) * (y2 - y1)
    while order.size > 0 and len(keep) < max_det:
        i = order[0]
        keep.append(i)
        if order.size == 1:
            break
        xx1 = np.maximum(x1[i], x1[order[1:]])
        yy1 = np.maximum(y1[i], y1[order[1:]])
        xx2 = np.minimum(x2[i], x2[order[1:]])
        yy2 = np.minimum(y2[i], y2[order[1:]])
        w = np.maximum(0.0, xx2 - xx1)
        h = np.maximum(0.0, yy2 - yy1)
        inter = w * h
        denom = areas[i] + areas[order[1:]] - inter + 1e-6
        ovr = inter / denom
        inds = np.where(ovr <= thresh)[0]
        order = order[inds + 1]
    return np.array(keep, dtype=int)


def scale_boxes(boxes, ratio, pad, shape):
    dw, dh = pad
    boxes[:, [0, 2]] -= dw
    boxes[:, [1, 3]] -= dh
    boxes[:, [0, 2]] /= ratio[0]
    boxes[:, [1, 3]] /= ratio[1]
    boxes[:, 0::2] = boxes[:, 0::2].clip(0, shape[1] - 1)
    boxes[:, 1::2] = boxes[:, 1::2].clip(0, shape[0] - 1)
    return boxes


def box_iou(boxA, boxB):
    xA = max(boxA[0], boxB[0])
    yA = max(boxA[1], boxB[1])
    xB = min(boxA[2], boxB[2])
    yB = min(boxA[3], boxB[3])
    interW = max(0.0, xB - xA)
    interH = max(0.0, yB - yA)
    inter = interW * interH
    if inter <= 0:
        return 0.0
    areaA = (boxA[2] - boxA[0]) * (boxA[3] - boxA[1]) + 1e-6
    areaB = (boxB[2] - boxB[0]) * (boxB[3] - boxB[1]) + 1e-6
    return inter / (areaA + areaB - inter)


def draw_line(frame, line_pts, color, label=None):
    if not line_pts or len(line_pts) < 2:
        return
    p1 = tuple(map(int, line_pts[0]))
    p2 = tuple(map(int, line_pts[1]))
    cv2.line(frame, p1, p2, color, 2)
    for p in [p1, p2]:
        cv2.circle(frame, p, 4, color, -1)
    if label:
        cv2.putText(frame, label, (p1[0], max(0, p1[1] - 10)), cv2.FONT_HERSHEY_SIMPLEX, 0.6, color, 2, cv2.LINE_AA)


def box_iou(boxA, boxB):
    xA = max(boxA[0], boxB[0])
    yA = max(boxA[1], boxB[1])
    xB = min(boxA[2], boxB[2])
    yB = min(boxA[3], boxB[3])
    interW = max(0.0, xB - xA)
    interH = max(0.0, yB - yA)
    inter = interW * interH
    if inter <= 0:
        return 0.0
    areaA = (boxA[2] - boxA[0]) * (boxA[3] - boxA[1]) + 1e-6
    areaB = (boxB[2] - boxB[0]) * (boxB[3] - boxB[1]) + 1e-6
    return inter / (areaA + areaB - inter)


def point_in_box(pt, box):
    if box is None or pt is None:
        return False
    x, y = pt
    return (box[0] <= x <= box[2]) and (box[1] <= y <= box[3])

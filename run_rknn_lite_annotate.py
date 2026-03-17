#!/usr/bin/env python3
import argparse
import json
import math
from dataclasses import dataclass
from pathlib import Path

import cv2
import numpy as np
import torch
from rknnlite.api import RKNNLite


@dataclass
class LetterboxInfo:
    orig_h: int
    orig_w: int
    new_h: int
    new_w: int
    scale: float
    dw: float
    dh: float


def letterbox(image, new_shape=(640, 640), pad_color=(0, 0, 0)):
    shape = image.shape[:2]
    if isinstance(new_shape, int):
        new_shape = (new_shape, new_shape)

    scale = min(new_shape[0] / shape[0], new_shape[1] / shape[1])
    new_unpad = (int(round(shape[1] * scale)), int(round(shape[0] * scale)))
    dw = new_shape[1] - new_unpad[0]
    dh = new_shape[0] - new_unpad[1]
    dw /= 2
    dh /= 2

    if shape[::-1] != new_unpad:
        image = cv2.resize(image, new_unpad, interpolation=cv2.INTER_LINEAR)

    top = int(round(dh - 0.1))
    bottom = int(round(dh + 0.1))
    left = int(round(dw - 0.1))
    right = int(round(dw + 0.1))
    image = cv2.copyMakeBorder(image, top, bottom, left, right, cv2.BORDER_CONSTANT, value=pad_color)
    info = LetterboxInfo(shape[0], shape[1], new_shape[0], new_shape[1], scale, dw, dh)
    return image, info


def deletterbox_boxes(boxes, info: LetterboxInfo):
    if boxes is None or len(boxes) == 0:
        return boxes
    boxes = boxes.copy()
    boxes[:, 0] = np.clip((boxes[:, 0] - info.dw) / info.scale, 0, info.orig_w)
    boxes[:, 1] = np.clip((boxes[:, 1] - info.dh) / info.scale, 0, info.orig_h)
    boxes[:, 2] = np.clip((boxes[:, 2] - info.dw) / info.scale, 0, info.orig_w)
    boxes[:, 3] = np.clip((boxes[:, 3] - info.dh) / info.scale, 0, info.orig_h)
    return boxes


def nms_boxes(boxes, scores, nms_thresh):
    x1 = boxes[:, 0]
    y1 = boxes[:, 1]
    x2 = boxes[:, 2]
    y2 = boxes[:, 3]

    areas = (x2 - x1) * (y2 - y1)
    order = scores.argsort()[::-1]
    keep = []

    while order.size > 0:
        index = order[0]
        keep.append(index)
        xx1 = np.maximum(x1[index], x1[order[1:]])
        yy1 = np.maximum(y1[index], y1[order[1:]])
        xx2 = np.minimum(x2[index], x2[order[1:]])
        yy2 = np.minimum(y2[index], y2[order[1:]])

        w = np.maximum(0.0, xx2 - xx1 + 1e-5)
        h = np.maximum(0.0, yy2 - yy1 + 1e-5)
        inter = w * h
        iou = inter / (areas[index] + areas[order[1:]] - inter)
        inds = np.where(iou <= nms_thresh)[0]
        order = order[inds + 1]

    return np.array(keep, dtype=np.int64)


def dfl(position):
    x = torch.tensor(position)
    n, c, h, w = x.shape
    part_num = 4
    mc = c // part_num
    y = x.reshape(n, part_num, mc, h, w)
    y = y.softmax(2)
    acc = torch.arange(mc).float().reshape(1, 1, mc, 1, 1)
    y = (y * acc).sum(2)
    return y.numpy()


def box_process(position, img_size):
    grid_h, grid_w = position.shape[2:4]
    col, row = np.meshgrid(np.arange(0, grid_w), np.arange(0, grid_h))
    col = col.reshape(1, 1, grid_h, grid_w)
    row = row.reshape(1, 1, grid_h, grid_w)
    grid = np.concatenate((col, row), axis=1)
    stride = np.array([img_size[1] // grid_h, img_size[0] // grid_w]).reshape(1, 2, 1, 1)
    position = dfl(position)
    box_xy = grid + 0.5 - position[:, 0:2, :, :]
    box_xy2 = grid + 0.5 + position[:, 2:4, :, :]
    return np.concatenate((box_xy * stride, box_xy2 * stride), axis=1)


def flatten_spatial(values):
    channels = values.shape[1]
    values = values.transpose(0, 2, 3, 1)
    return values.reshape(-1, channels)


def post_process(outputs, img_size=(640, 640), obj_thresh=0.25, nms_thresh=0.45):
    boxes = []
    class_confidences = []
    default_branch = 3
    pair_per_branch = len(outputs) // default_branch

    for branch_index in range(default_branch):
        offset = pair_per_branch * branch_index
        boxes.append(box_process(outputs[offset], img_size))
        class_confidences.append(outputs[offset + 1])

    boxes = np.concatenate([flatten_spatial(item) for item in boxes])
    class_confidences = np.concatenate([flatten_spatial(item) for item in class_confidences])
    scores = np.max(class_confidences, axis=-1)
    classes = np.argmax(class_confidences, axis=-1)
    keep = np.where(scores >= obj_thresh)[0]
    if keep.size == 0:
        return None, None, None

    boxes = boxes[keep]
    classes = classes[keep]
    scores = scores[keep]

    kept_boxes = []
    kept_classes = []
    kept_scores = []
    for class_id in sorted(set(classes.tolist())):
        inds = np.where(classes == class_id)[0]
        class_boxes = boxes[inds]
        class_scores = scores[inds]
        keep_inds = nms_boxes(class_boxes, class_scores, nms_thresh)
        if len(keep_inds):
            kept_boxes.append(class_boxes[keep_inds])
            kept_classes.append(classes[inds][keep_inds])
            kept_scores.append(class_scores[keep_inds])

    if not kept_boxes:
        return None, None, None

    return np.concatenate(kept_boxes), np.concatenate(kept_classes), np.concatenate(kept_scores)


def draw_detections(frame, boxes, classes, scores, names):
    out = frame.copy()
    if boxes is None:
        return out
    for box, class_id, score in zip(boxes, classes, scores):
        x1, y1, x2, y2 = [int(round(v)) for v in box.tolist()]
        label = names.get(int(class_id), str(class_id))
        cv2.rectangle(out, (x1, y1), (x2, y2), (0, 255, 0), 2)
        cv2.putText(out, f"{label} {score:.2f}", (x1, max(20, y1 - 6)), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 0, 255), 2)
    return out


def video_writer(path, cap):
    width = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
    height = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
    fps = cap.get(cv2.CAP_PROP_FPS)
    if not fps or math.isnan(fps) or fps <= 0:
        fps = 25.0
    return cv2.VideoWriter(str(path), cv2.VideoWriter_fourcc(*"mp4v"), fps, (width, height))


def run_video(rknn, video_path, output_path, names, obj_thresh, nms_thresh, max_frames=None):
    cap = cv2.VideoCapture(str(video_path))
    if not cap.isOpened():
        raise RuntimeError(f"cannot open video: {video_path}")
    writer = video_writer(output_path, cap)

    frame_count = 0
    det_frames = 0
    det_boxes = 0
    conf_sum = 0.0

    while True:
        ok, frame = cap.read()
        if not ok:
            break
        frame_count += 1

        image, info = letterbox(frame, (640, 640), pad_color=(0, 0, 0))
        image = cv2.cvtColor(image, cv2.COLOR_BGR2RGB)
        outputs = rknn.inference(inputs=[image[None, ...]], data_format=["nhwc"])
        boxes, classes, scores = post_process(outputs, img_size=(640, 640), obj_thresh=obj_thresh, nms_thresh=nms_thresh)
        if boxes is not None:
            boxes = deletterbox_boxes(boxes, info)
            det_frames += 1
            det_boxes += int(len(boxes))
            conf_sum += float(np.sum(scores))

        writer.write(draw_detections(frame, boxes, classes, scores, names))
        if frame_count % 30 == 0:
            print(f"[{video_path.name}] frame {frame_count}")
        if max_frames and frame_count >= max_frames:
            break

    cap.release()
    writer.release()
    return {
        "video": str(video_path),
        "model": "rknn_lite.demo.pad0",
        "frames": frame_count,
        "detected_frames": det_frames,
        "total_boxes": det_boxes,
        "avg_boxes_per_frame": (det_boxes / frame_count) if frame_count else 0.0,
        "avg_score_per_box": (conf_sum / det_boxes) if det_boxes else 0.0,
        "output": str(output_path),
        "obj_thresh": obj_thresh,
        "nms_thresh": nms_thresh,
    }


def parse_class_names(text):
    default_names = {
        0: "car",
        1: "blue truck",
        2: "yellow truck",
        3: "dump truck",
        4: "wuxiao",
        5: "wheel",
        6: "cleaning table",
        7: "manual",
        8: "license",
    }
    if not text:
        return default_names
    data = json.loads(text)
    return {int(k): str(v) for k, v in data.items()}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--model", required=True, help="RKNN model path")
    parser.add_argument("--videos", nargs="+", required=True, help="video paths")
    parser.add_argument("--output-dir", required=True, help="output directory")
    parser.add_argument("--obj-thresh", type=float, default=0.25)
    parser.add_argument("--nms-thresh", type=float, default=0.45)
    parser.add_argument("--max-frames", type=int, default=None)
    parser.add_argument("--class-names-json", default=None, help='JSON string, e.g. {"0":"car","1":"truck"}')
    args = parser.parse_args()

    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    names = parse_class_names(args.class_names_json)

    rknn = RKNNLite(verbose=False)
    ret = rknn.load_rknn(str(args.model))
    if ret != 0:
        raise RuntimeError(f"load_rknn failed: {ret}")
    ret = rknn.init_runtime()
    if ret != 0:
        raise RuntimeError(f"init_runtime failed: {ret}")

    report = {"class_names": names, "videos": []}
    try:
        for item in args.videos:
            video_path = Path(item)
            out_path = output_dir / f"{video_path.stem.replace(' ', '_')}.rknn.demo.pad0.mp4"
            stats = run_video(rknn, video_path, out_path, names, args.obj_thresh, args.nms_thresh, args.max_frames)
            report["videos"].append(stats)
            print(f"done {video_path} -> {out_path}")
    finally:
        rknn.release()

    report_path = output_dir / "report.json"
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"report saved to {report_path}")


if __name__ == "__main__":
    main()

from __future__ import annotations

import base64
import threading
import time
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Dict, Optional, Sequence

import cv2
import numpy as np

from .fp_detect import FpModelPostprocessor
from .log_throttle import WindowedLogThrottle
from .video_io import create_video_reader, parse_core_mask

DEFAULT_WHEEL_CLASSES = ["0-25", "25-50", "50-75", "75-100"]
PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_WHEEL_MODEL_PATH = (PROJECT_ROOT / "models" / "wheel" / "2026.4.28CRwheelfp.rknn").resolve()
DEFAULT_CENTER_MIN_MARGIN_RATIO = 0.25
WHEEL_SIDES = ("left", "right")


def _safe_float(value, default):
    try:
        return float(value)
    except (TypeError, ValueError):
        return float(default)


def _safe_bool(value, default=False):
    if isinstance(value, bool):
        return value
    if value is None:
        return bool(default)
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "on"}
    return bool(value)


def _resolve_wheel_model_path(model_value, base_dir=None):
    raw = str(model_value or "").strip()
    if not raw:
        return DEFAULT_WHEEL_MODEL_PATH
    candidate = Path(raw).expanduser()
    if candidate.is_absolute():
        return candidate.resolve()
    if base_dir:
        from_base_dir = (Path(base_dir) / candidate).resolve()
        if from_base_dir.exists():
            return from_base_dir
    from_project_root = (PROJECT_ROOT / candidate).resolve()
    if from_project_root.exists():
        return from_project_root
    if base_dir:
        return (Path(base_dir) / candidate).resolve()
    return from_project_root


def resolve_wheel_class_name(class_names: Sequence[str], class_id: int) -> str:
    try:
        index = int(class_id)
    except (TypeError, ValueError):
        return ""
    if 0 <= index < len(class_names):
        return str(class_names[index])
    return str(index)


def resolve_wheel_settings(config, base_dir=None):
    raw = (config or {}).get("wheel", {}) or {}
    classes = raw.get("classes")
    if isinstance(classes, (list, tuple)):
        class_names = [str(item).strip() for item in classes if str(item).strip()]
    else:
        class_names = []
    if not class_names:
        class_names = list(DEFAULT_WHEEL_CLASSES)

    model_value = str(raw.get("model") or DEFAULT_WHEEL_MODEL_PATH)
    model_path = _resolve_wheel_model_path(model_value, base_dir=base_dir)

    target_fps = _safe_float(raw.get("target_fps", 5.0), 5.0)
    if target_fps <= 0.0:
        target_fps = 5.0

    center_min_margin_ratio = _safe_float(
        raw.get("center_min_margin_ratio", DEFAULT_CENTER_MIN_MARGIN_RATIO),
        DEFAULT_CENTER_MIN_MARGIN_RATIO,
    )
    center_min_margin_ratio = min(max(center_min_margin_ratio, 0.0), 0.49)

    bind_window_seconds = _safe_float(raw.get("bind_window_seconds", 30.0), 30.0)
    if bind_window_seconds <= 0.0:
        bind_window_seconds = 30.0

    conf_thresh = _safe_float(raw.get("conf_thresh", 0.25), 0.25)
    if conf_thresh < 0.0:
        conf_thresh = 0.25

    nms_thresh = _safe_float(raw.get("nms_thresh", 0.45), 0.45)
    if nms_thresh <= 0.0:
        nms_thresh = 0.45
    imgsz = None
    if raw.get("imgsz") is not None:
        try:
            imgsz = max(64, int(raw.get("imgsz")))
        except (TypeError, ValueError):
            imgsz = None

    return {
        "enabled": _safe_bool(raw.get("enabled", False)),
        "left_source": str(raw.get("left_source", "") or "").strip(),
        "right_source": str(raw.get("right_source", "") or "").strip(),
        "model": str(model_path),
        "model_path": Path(model_path),
        "classes": class_names,
        "target_fps": target_fps,
        "center_min_margin_ratio": center_min_margin_ratio,
        "bind_window_seconds": bind_window_seconds,
        "conf_thresh": conf_thresh,
        "nms_thresh": nms_thresh,
        "imgsz": imgsz,
        "core_mask": str(raw.get("core_mask", "") or "").strip(),
    }


def select_centered_detection(
    boxes,
    classes,
    scores,
    frame_shape,
    center_min_margin_ratio=DEFAULT_CENTER_MIN_MARGIN_RATIO,
    class_names=None,
):
    if boxes is None or classes is None or scores is None:
        return None
    boxes = np.asarray(boxes)
    classes = np.asarray(classes)
    scores = np.asarray(scores)
    if boxes.size == 0 or classes.size == 0 or scores.size == 0:
        return None
    if len(frame_shape) < 2:
        return None

    frame_h = int(frame_shape[0])
    frame_w = int(frame_shape[1])
    if frame_h <= 0 or frame_w <= 0:
        return None

    margin = min(max(float(center_min_margin_ratio or 0.0), 0.0), 0.49)
    safe_x1 = frame_w * margin
    safe_x2 = frame_w * (1.0 - margin)
    safe_y1 = frame_h * margin
    safe_y2 = frame_h * (1.0 - margin)
    frame_cx = frame_w * 0.5
    frame_cy = frame_h * 0.5
    class_names = list(class_names or DEFAULT_WHEEL_CLASSES)

    best_key = None
    best_item = None
    for box, cls_id, score in zip(boxes, classes, scores):
        if len(box) != 4:
            continue
        x1, y1, x2, y2 = [float(v) for v in box[:4]]
        center_x = (x1 + x2) * 0.5
        center_y = (y1 + y2) * 0.5
        if center_x < safe_x1 or center_x > safe_x2 or center_y < safe_y1 or center_y > safe_y2:
            continue
        distance = (center_x - frame_cx) ** 2 + (center_y - frame_cy) ** 2
        candidate_key = (distance, -float(score))
        if best_key is not None and candidate_key >= best_key:
            continue
        best_key = candidate_key
        best_item = {
            "box": [int(round(x1)), int(round(y1)), int(round(x2)), int(round(y2))],
            "classId": int(cls_id),
            "score": float(score),
            "className": resolve_wheel_class_name(class_names, cls_id),
            "centerDistance": float(distance),
            "centerPoint": [float(center_x), float(center_y)],
        }
    return best_item


def _encode_frame_jpeg_bytes(frame, image_quality=85):
    if frame is None:
        return b""
    quality = int(min(max(int(image_quality), 1), 100))
    ok, encoded = cv2.imencode(".jpg", frame, [int(cv2.IMWRITE_JPEG_QUALITY), quality])
    if not ok:
        return b""
    return encoded.tobytes()


def _jpeg_bytes_to_base64(data):
    if not data:
        return ""
    return base64.b64encode(data).decode("utf-8")


class WheelResultCache:
    def __init__(self, bind_window_seconds=30.0, image_quality=85):
        self.bind_window_seconds = max(1.0, float(bind_window_seconds))
        self.image_quality = int(min(max(int(image_quality), 1), 100))
        self._entries: Dict[str, list] = {}
        self._lock = threading.Lock()
        self._next_entry_id = 1

    def _prune_entries(self, side, cutoff_ts):
        entries = list(self._entries.get(side) or [])
        if not entries:
            self._entries.pop(side, None)
            return []
        kept = [entry for entry in entries if float(entry.get("capture_ts", 0.0) or 0.0) >= cutoff_ts]
        if kept:
            self._entries[side] = kept
        else:
            self._entries.pop(side, None)
        return kept

    def update_from_detections(
        self,
        side,
        frame,
        capture_ts,
        boxes,
        classes,
        scores,
        center_min_margin_ratio=DEFAULT_CENTER_MIN_MARGIN_RATIO,
        class_names=None,
    ):
        side = str(side or "").strip().lower()
        if side not in WHEEL_SIDES or frame is None:
            return False
        candidate = select_centered_detection(
            boxes=boxes,
            classes=classes,
            scores=scores,
            frame_shape=getattr(frame, "shape", ()),
            center_min_margin_ratio=center_min_margin_ratio,
            class_names=class_names or DEFAULT_WHEEL_CLASSES,
        )
        if not candidate:
            return False

        image_jpeg = _encode_frame_jpeg_bytes(frame, image_quality=self.image_quality)
        if not image_jpeg:
            return False

        capture_ts = time.time() if capture_ts is None else float(capture_ts)
        entry = {
            "entryId": int(self._next_entry_id),
            "side": side,
            "captureTime": datetime.fromtimestamp(capture_ts).strftime("%Y-%m-%d %H:%M:%S"),
            "imageJpegBytes": image_jpeg,
            "className": candidate["className"],
            "score": float(candidate.get("score", 0.0) or 0.0),
            "centerDistance": float(candidate.get("centerDistance", 0.0) or 0.0),
            "centerPoint": list(candidate.get("centerPoint") or []),
            "capture_ts": capture_ts,
            "claimedTrackId": 0,
        }
        with self._lock:
            self._next_entry_id += 1
            cutoff_ts = capture_ts - self.bind_window_seconds
            entries = self._prune_entries(side, cutoff_ts)
            entries.append(entry)
            self._entries[side] = entries
        return True

    def get_recent_result_entries(self, now_ts=None, reference_ts=None, track_id=None):
        now_ref_ts = time.time() if now_ts is None else float(now_ts)
        match_ref_ts = now_ref_ts if reference_ts is None else float(reference_ts)
        track_id = int(track_id or 0)
        results = []
        with self._lock:
            for side in WHEEL_SIDES:
                entries = self._prune_entries(side, now_ref_ts - self.bind_window_seconds)
                if not entries:
                    continue
                candidates = []
                for entry in entries:
                    capture_ts = float(entry.get("capture_ts", 0.0) or 0.0)
                    if abs(match_ref_ts - capture_ts) > self.bind_window_seconds:
                        continue
                    claimed_track_id = int(entry.get("claimedTrackId", 0) or 0)
                    if claimed_track_id > 0 and claimed_track_id != track_id:
                        continue
                    time_delta = abs(match_ref_ts - capture_ts)
                    candidate_key = (
                        float(entry.get("centerDistance", 0.0) or 0.0),
                        -float(entry.get("score", 0.0) or 0.0),
                        time_delta,
                        -capture_ts,
                    )
                    candidates.append((candidate_key, entry))
                if not candidates:
                    continue
                _, entry = min(candidates, key=lambda item: item[0])
                results.append(dict(entry))
        return results

    def claim_result_entry(self, track_id, entry_id):
        track_id = int(track_id or 0)
        entry_id = int(entry_id or 0)
        if track_id <= 0 or entry_id <= 0:
            return False
        with self._lock:
            for side in WHEEL_SIDES:
                entries = self._entries.get(side) or []
                for entry in entries:
                    if int(entry.get("entryId", 0) or 0) != entry_id:
                        continue
                    owner = int(entry.get("claimedTrackId", 0) or 0)
                    if owner > 0 and owner != track_id:
                        return False
                    entry["claimedTrackId"] = track_id
                    return True
        return False

    def get_recent_results(self, now_ts=None, reference_ts=None):
        results = []
        for entry in self.get_recent_result_entries(now_ts=now_ts, reference_ts=reference_ts):
            side = str(entry.get("side") or "").strip().lower()
            if side not in WHEEL_SIDES:
                continue
            results.append(
                {
                    "side": side,
                    "captureTime": str(entry.get("captureTime") or ""),
                    "imageBase64": _jpeg_bytes_to_base64(entry.get("imageJpegBytes", b"")),
                    "className": str(entry.get("className") or ""),
                }
            )
        return results


class _LatestFrameSlot:
    def __init__(self):
        self._cond = threading.Condition()
        self._seq = 0
        self._item = None

    def put(self, item):
        with self._cond:
            self._seq += 1
            self._item = item
            self._cond.notify_all()

    def peek(self):
        with self._cond:
            return self._seq, self._item

    def wait_for_update(self, last_seq, timeout=0.2):
        deadline = None if timeout is None else (time.time() + max(0.0, float(timeout)))
        with self._cond:
            while self._seq <= last_seq:
                if deadline is None:
                    self._cond.wait()
                    continue
                remaining = deadline - time.time()
                if remaining <= 0.0:
                    break
                self._cond.wait(timeout=remaining)
            return self._seq, self._item


class WheelReaderThread(threading.Thread):
    def __init__(
        self,
        side,
        source,
        reader_args,
        frame_slot,
        stop_event,
        reader_fail_threshold=5,
        reconnect_delay=2.0,
    ):
        super().__init__(daemon=True)
        self.side = str(side)
        self.source = str(source)
        self.reader_args = reader_args
        self.frame_slot = frame_slot
        self.stop_event = stop_event
        self.reader_fail_threshold = max(1, int(reader_fail_threshold))
        self.reconnect_delay = max(0.2, float(reconnect_delay))
        self.frames = 0
        self._log_throttle = WindowedLogThrottle()

    def _log(self, key, message, window_seconds=10.0):
        self._log_throttle.log(key=key, message=message, window_seconds=window_seconds, emit=print)

    def run(self):
        cap = None
        consecutive_fails = 0
        reconnect_count = 0
        try:
            while not self.stop_event.is_set():
                if cap is None or not hasattr(cap, "isOpened") or not cap.isOpened():
                    cap, decode_meta = create_video_reader(self.source, self.reader_args)
                    if cap is None or not hasattr(cap, "isOpened") or not cap.isOpened():
                        self._log(
                            key=f"wheel.reader.open.{self.side}",
                            message=f"[wheel:{self.side}] reader open failed, source={self.source}",
                            window_seconds=10.0,
                        )
                        try:
                            if cap is not None:
                                cap.release()
                        except Exception:
                            pass
                        cap = None
                        if self.stop_event.wait(self.reconnect_delay):
                            break
                        continue
                    mode = str((decode_meta or {}).get("decode_mode") or "sw")
                    backend = str((decode_meta or {}).get("decode_backend") or "software")
                    print(f"[wheel:{self.side}] reader opened mode={mode} backend={backend}")
                    consecutive_fails = 0

                ok, frame = cap.read()
                if not ok or frame is None:
                    consecutive_fails += 1
                    if consecutive_fails < self.reader_fail_threshold:
                        if self.stop_event.wait(0.05):
                            break
                        continue
                    reconnect_count += 1
                    self._log(
                        key=f"wheel.reader.reconnect.{self.side}",
                        message=f"[wheel:{self.side}] reader stalled, reconnect #{reconnect_count}",
                        window_seconds=10.0,
                    )
                    try:
                        cap.release()
                    except Exception:
                        pass
                    cap = None
                    consecutive_fails = 0
                    if self.stop_event.wait(self.reconnect_delay):
                        break
                    continue

                consecutive_fails = 0
                self.frames += 1
                self.frame_slot.put((frame, time.time()))
        finally:
            try:
                if cap is not None:
                    cap.release()
            except Exception:
                pass


class WheelProcessorThread(threading.Thread):
    def __init__(
        self,
        side,
        model_path,
        class_names,
        frame_slot,
        result_cache,
        stop_event,
        target_fps=5.0,
        center_min_margin_ratio=0.15,
        imgsz=640,
        conf_thresh=0.25,
        nms_thresh=0.45,
        core_mask=None,
    ):
        super().__init__(daemon=True)
        self.side = str(side)
        self.model_path = Path(model_path)
        self.class_names = list(class_names or DEFAULT_WHEEL_CLASSES)
        self.frame_slot = frame_slot
        self.result_cache = result_cache
        self.stop_event = stop_event
        self.target_interval = 1.0 / max(float(target_fps or 0.0), 0.1)
        self.center_min_margin_ratio = min(max(float(center_min_margin_ratio or 0.0), 0.0), 0.49)
        self.imgsz = max(64, int(imgsz))
        self.conf_thresh = max(0.0, float(conf_thresh))
        self.nms_thresh = max(0.01, float(nms_thresh))
        self.core_mask = core_mask
        self.frames = 0
        self.infer_time = 0.0
        self.center_hits = 0
        self._output_mode = "6"
        self._postprocessor = self._build_postprocessor(self._output_mode)
        self._log_throttle = WindowedLogThrottle()

    def _build_postprocessor(self, output_mode):
        return FpModelPostprocessor(
            img_size=(self.imgsz, self.imgsz),
            obj_thresh=self.conf_thresh,
            nms_thresh=self.nms_thresh,
            output_mode=output_mode,
            num_classes=len(self.class_names),
        )

    def _ensure_output_mode(self, outputs):
        expected = "9" if len(outputs) == 9 else "6"
        if expected == self._output_mode:
            return
        self._output_mode = expected
        self._postprocessor = self._build_postprocessor(expected)
        print(f"[wheel:{self.side}] fp_postprocess_mode={self._postprocessor.describe_mode()}")

    def _log(self, key, message, window_seconds=10.0):
        self._log_throttle.log(key=key, message=message, window_seconds=window_seconds, emit=print)

    @staticmethod
    def _build_runtime():
        from rknnlite.api import RKNNLite

        return RKNNLite()

    def run(self):
        if not self.model_path.exists():
            print(f"[wheel:{self.side}] model missing: {self.model_path}")
            return

        rk = None
        try:
            rk = self._build_runtime()
            if rk.load_rknn(str(self.model_path)) != 0:
                raise RuntimeError("load_rknn failed")
            init_kwargs = {}
            if self.core_mask is not None:
                init_kwargs["core_mask"] = self.core_mask
            if rk.init_runtime(**init_kwargs) != 0:
                raise RuntimeError("init_runtime failed")
        except Exception as exc:
            print(f"[wheel:{self.side}] runtime init failed: {exc}")
            try:
                if rk is not None:
                    rk.release()
            except Exception:
                pass
            return

        last_seq = 0
        next_infer_ts = 0.0
        try:
            while not self.stop_event.is_set():
                now = time.time()
                if next_infer_ts > now:
                    if self.stop_event.wait(min(next_infer_ts - now, 0.05)):
                        break
                    continue

                seq, item = self.frame_slot.peek()
                if item is None or seq == last_seq:
                    self.frame_slot.wait_for_update(last_seq, timeout=0.2)
                    continue

                last_seq = seq
                frame, capture_ts = item
                started = time.time()
                next_infer_ts = started + self.target_interval
                try:
                    img_input, lb_info = self._postprocessor.prepare(frame)
                    outputs = rk.inference(inputs=[img_input], data_format=["nhwc"])
                    if not outputs:
                        continue
                    self._ensure_output_mode(outputs)
                    boxes, classes, scores = self._postprocessor.postprocess(outputs)
                    if boxes is None or classes is None or scores is None:
                        continue
                    boxes = self._postprocessor.map_boxes_to_original(boxes, lb_info)
                    if self.result_cache.update_from_detections(
                        side=self.side,
                        frame=frame,
                        capture_ts=capture_ts,
                        boxes=boxes,
                        classes=classes,
                        scores=scores,
                        center_min_margin_ratio=self.center_min_margin_ratio,
                        class_names=self.class_names,
                    ):
                        self.center_hits += 1
                except Exception as exc:
                    self._log(
                        key=f"wheel.processor.{self.side}",
                        message=f"[wheel:{self.side}] inference failed: {exc}",
                        window_seconds=10.0,
                    )
                finally:
                    self.frames += 1
                    self.infer_time += max(0.0, time.time() - started)
        finally:
            try:
                rk.release()
            except Exception:
                pass


class WheelDetectionService:
    def __init__(self, config, base_dir=None, hw_decode=False, imgsz=640, image_quality=85):
        self.config = config or {}
        self.settings = resolve_wheel_settings(self.config, base_dir=base_dir)
        self.enabled = bool(self.settings.get("enabled"))
        self.result_cache = WheelResultCache(
            bind_window_seconds=self.settings["bind_window_seconds"],
            image_quality=image_quality,
        )
        self.stop_event = threading.Event()
        self.reader_args = SimpleNamespace(
            hw_decode=bool(hw_decode),
            _config=self.config,
        )
        self.reader_fail_threshold = max(1, int((self.config or {}).get("reader_fail_threshold", 5)))
        self.reader_reconnect_delay = max(0.2, float((self.config or {}).get("reader_reconnect_delay", 2.0)))
        configured_imgsz = self.settings.get("imgsz")
        self.imgsz = max(64, int(configured_imgsz if configured_imgsz else imgsz))
        self.core_mask = parse_core_mask(self.settings.get("core_mask"))
        self.streams = {}
        self.active_sides = []

    def start(self):
        if not self.enabled:
            return False

        model_path = Path(self.settings["model_path"])
        if not model_path.exists():
            print(f"[wheel] enabled but model missing: {model_path}")
            return False

        for side in WHEEL_SIDES:
            source = str(self.settings.get(f"{side}_source", "") or "").strip()
            if not source:
                continue
            frame_slot = _LatestFrameSlot()
            reader = WheelReaderThread(
                side=side,
                source=source,
                reader_args=self.reader_args,
                frame_slot=frame_slot,
                stop_event=self.stop_event,
                reader_fail_threshold=self.reader_fail_threshold,
                reconnect_delay=self.reader_reconnect_delay,
            )
            processor = WheelProcessorThread(
                side=side,
                model_path=model_path,
                class_names=self.settings["classes"],
                frame_slot=frame_slot,
                result_cache=self.result_cache,
                stop_event=self.stop_event,
                target_fps=self.settings["target_fps"],
                center_min_margin_ratio=self.settings["center_min_margin_ratio"],
                imgsz=self.imgsz,
                conf_thresh=self.settings["conf_thresh"],
                nms_thresh=self.settings["nms_thresh"],
                core_mask=self.core_mask,
            )
            self.streams[side] = {
                "reader": reader,
                "processor": processor,
            }
            reader.start()
            processor.start()
            self.active_sides.append(side)

        if not self.active_sides:
            print("[wheel] enabled but no left/right source configured, skip sidechain")
            return False

        print(
            f"[wheel] enabled sides={','.join(self.active_sides)} "
            f"target_fps={self.settings['target_fps']:.2f} "
            f"imgsz={self.imgsz} core_mask={self.core_mask}"
        )
        return True

    def stop(self):
        self.stop_event.set()
        for side in list(self.streams.keys()):
            stream = self.streams.get(side) or {}
            for key in ("reader", "processor"):
                worker = stream.get(key)
                if worker is None:
                    continue
                try:
                    worker.join(timeout=1.0)
                except Exception:
                    pass

    def get_recent_results(self, now_ts=None):
        return self.result_cache.get_recent_results(now_ts=now_ts)

    def get_recent_result_entries(self, now_ts=None, reference_ts=None, track_id=None):
        return self.result_cache.get_recent_result_entries(
            now_ts=now_ts,
            reference_ts=reference_ts,
            track_id=track_id,
        )

    def claim_result_entry(self, track_id, entry_id):
        return self.result_cache.claim_result_entry(track_id, entry_id)

    def snapshot_stats(self):
        stats = {}
        for side, stream in self.streams.items():
            reader = stream.get("reader")
            processor = stream.get("processor")
            stats[side] = {
                "decode_frames": int(getattr(reader, "frames", 0) or 0),
                "infer_frames": int(getattr(processor, "frames", 0) or 0),
                "infer_time": float(getattr(processor, "infer_time", 0.0) or 0.0),
                "center_hits": int(getattr(processor, "center_hits", 0) or 0),
            }
        return stats

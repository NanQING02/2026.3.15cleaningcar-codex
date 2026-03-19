import csv
import json
from collections import deque
from pathlib import Path
from typing import List, Optional

import cv2
from fastapi import HTTPException

from config_manager import ConfigError, ConfigManager

from . import state

def _resolve_path(path_str: Optional[str], default: Optional[Path] = None) -> Path:
    if path_str:
        candidate = Path(path_str)
    elif default is not None:
        candidate = Path(default)
    else:
        candidate = state.ROOT
    if not candidate.is_absolute():
        candidate = (state.ROOT / candidate).resolve()
    return candidate


def _event_log_path(cfg: ConfigManager) -> Path:
    base_dir = cfg.data.get("event_output_dir")
    if not base_dir:
        base_dir = cfg.data.get("system", {}).get("event_output_dir")
    base = _resolve_path(base_dir or (state.ROOT / "events"))
    return base / "event_log.csv"


def _detection_csv_path(cfg: ConfigManager) -> Optional[Path]:
    csv_path = cfg.video.get("csv")
    if not csv_path:
        return None
    return _resolve_path(csv_path)


def _debug_frame_path(cfg: ConfigManager) -> Optional[Path]:
    dbg_path = cfg.video.get("debug_frame_path")
    if not dbg_path:
        return None
    return _resolve_path(dbg_path)


def _per_id_video_root(cfg: ConfigManager) -> Path:
    logic = cfg.data.get("logic", {}) or {}
    base_dir = logic.get("per_id_video_dir") or cfg.data.get("per_id_video_dir")
    if isinstance(base_dir, str):
        base_dir = base_dir.strip()
    base = _resolve_path(base_dir or (state.ROOT / "video_result" / "per_id"))
    return base


def _events_root(cfg: ConfigManager) -> Path:
    base_dir = cfg.data.get("event_output_dir")
    if not base_dir:
        base_dir = cfg.data.get("system", {}).get("event_output_dir")
    base = _resolve_path(base_dir or (state.ROOT / "events"))
    return base


def _read_csv_tail(path: Path, limit: int):
    if not path.exists():
        return []
    rows = deque(maxlen=max(1, limit))
    try:
        with path.open("r", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            for row in reader:
                rows.append(row)
    except Exception:
        return []
    return list(rows)


def _load_config():
    try:
        return ConfigManager(state.CONFIG_PATH)
    except ConfigError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


def _inference_log_dir() -> Path:
    return state.ROOT / "logs" / "inference"


def _capture_frame(force=False):
    if state.FRAME_CACHE.data is not None and not force:
        return
    cfg = _load_config()
    src = cfg.video.get("source")
    if not src:
        raise HTTPException(status_code=400, detail="video.source 未配置")
    cap = cv2.VideoCapture(src)
    if not cap.isOpened():
        raise HTTPException(status_code=500, detail=f"无法打开视频源: {src}")
    ret, frame = cap.read()
    cap.release()
    if not ret or frame is None:
        raise HTTPException(status_code=500, detail="视频源无法读取帧")
    success, buf = cv2.imencode(".jpg", frame)
    if not success:
        raise HTTPException(status_code=500, detail="帧编码失败")
    state.FRAME_CACHE.data = buf.tobytes()
    state.FRAME_CACHE.size = (frame.shape[1], frame.shape[0])

def _available_config_files() -> List[Path]:
    base = state.CONFIG_PATH.parent
    files = list(base.glob("config*.json"))
    return sorted(files)

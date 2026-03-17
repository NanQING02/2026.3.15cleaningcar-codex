import argparse
import os
from pathlib import Path

from utils.disk_manager import DiskCleaner

from .constants import ENABLE_DISK_CLEANER, PLATE_EXPAND_DEFAULT
from .pipeline import process_video
from .runtime_config import (
    apply_class_thresholds_from_config,
    apply_cli_overrides,
    load_config,
)
from .video_io import _collect_storage_directories

def parse_args():
    ap = argparse.ArgumentParser(description='Multithread RKNN detector demo.')
    ap.add_argument('--model', default='1.18.fp.rknn')
    ap.add_argument('--video', help='Single video file to process.')
    ap.add_argument('--video_dir', help='Directory of videos to process sequentially.')
    ap.add_argument('--imgsz', type=int, default=640)
    ap.add_argument('--conf', type=float, default=0.30)
    ap.add_argument('--iou', type=float, default=0.45)
    ap.add_argument('--max_det', type=int, default=300)
    ap.add_argument('--workers', type=int, default=2, help='Number of inference workers.')
    ap.add_argument('--queue_size', type=int, default=32)
    ap.add_argument('--core_mask', default='all', help="Which NPU cores to use: e.g. '0-2', '0,2', '1', 'all', 'auto'.")
    ap.add_argument('--hw_decode', action='store_true', help='Use GStreamer + mpp hardware decode when available.')
    ap.add_argument('--save_video', help='Output annotated video path.')
    ap.add_argument('--csv', help='CSV path, append per detection.')
    ap.add_argument('--output_dir', help='When batch processing, auto-save mp4/csv into this directory using video stem names.')
    ap.add_argument('--no_draw', action='store_true', help='Do not draw boxes on frames.')
    ap.add_argument('--monitor_interval', type=float, default=0.0, help='Seconds between resource logs (0 disables).')
    ap.add_argument('--limit', type=int, default=0, help='Optional frame limit for quick tests.')
    ap.add_argument('--lpr_model', default='lprnet.rknn', help='Path to license plate recognition RKNN.')
    ap.add_argument('--plate_detect_model', default='plate_detect.rknn',
                    help='Path to plate detection RKNN used by the dual-model LPR pipeline.')
    ap.add_argument('--plate_rec_model', default='plate_rec_color.rknn',
                    help='Path to plate text/color RKNN used by the dual-model LPR pipeline.')
    ap.add_argument('--model_variant', choices=['auto', 'legacy', 'fp'], default='auto',
                    help='Detection model postprocess mode. auto infers from model filename and outputs.')
    ap.add_argument('--plate_expand', type=float, default=PLATE_EXPAND_DEFAULT, help='Extra ratio padding for plate crops.')
    ap.add_argument('--plate_lock_frames', type=int, default=5, help='Frames required before plate text is locked.')
    ap.add_argument('--config', help='YAML config describing ROI/event logic.')
    ap.add_argument('--camera', help='当配置包含多个 camera 条目时，指定要运行的 key。')
    ap.add_argument('--debug_rois', action='store_true', help='Visualize stage lines on output frames.')
    ap.add_argument('--debug_tracks', action='store_true', help='Overlay per-track state info on frames.')
    ap.add_argument('--source_mode', choices=['auto', 'camera', 'file'], default='auto',
                    help='数据源类型：camera 为实时流（可自动重连），file 为本地视频（读到末尾即停止）。')
    ap.add_argument('--event_log', nargs='?', const='auto',
                    help='Optional path for事件日志; 不加参数时使用默认 events/event_log.csv。')
    ap.add_argument('--roi_setup', action='store_true', help='Launch ROI editor before running detection.')
    ap.add_argument('--api_url', help='远端车辆冲洗事件上报接口 URL (POST)。')
    ap.add_argument('--api_token', help='用于 HTTP Authorization: Bearer 的 Token。')
    ap.add_argument('--capture_mode', choices=['path', 'base64'], default='path',
                    help='事件截图在上报时的字段格式：文件路径或Base64。')
    ap.add_argument('--lane', help='覆盖事件上报中的 lane 字段。')
    ap.add_argument('--detect_roi_only', action='store_true', help='仅在配置的 detect_roi 多边形内进行检测。')
    defaults = ap.parse_args(args=[])
    args = ap.parse_args()
    setattr(args, '_defaults', defaults)
    return args

def iter_videos(args):
    if args.video:
        yield args.video
    if args.video_dir:
        for name in sorted(os.listdir(args.video_dir)):
            if name.lower().endswith(('.mp4', '.mov', '.avi', '.mkv')):
                yield os.path.join(args.video_dir, name)


def main():
    args = parse_args()
    config_path = getattr(args, 'config', None)
    config_dir = None
    if config_path:
        try:
            config_dir = Path(config_path).expanduser().resolve().parent
        except Exception:
            config_dir = Path.cwd()
    try:
        config = load_config(config_path)
    except ValueError as exc:
        print(exc)
        return
    apply_cli_overrides(args, config)
    apply_class_thresholds_from_config(config)
    setattr(args, '_config', config)
    setattr(args, '_config_dir', config_dir or Path.cwd())
    videos = list(iter_videos(args))
    if not videos:
        print('No videos specified.')
        return
    disk_cleaner = None
    storage_cfg = config.get('storage', {}) or {}
    disk_dirs = _collect_storage_directories(config, config_dir) if ENABLE_DISK_CLEANER else []
    if disk_dirs:
        try:
            threshold = float(storage_cfg.get('disk_threshold', 65.0))
            target = float(storage_cfg.get('disk_target', max(5.0, threshold - 10.0)))
            if target >= threshold:
                target = max(5.0, threshold - 10.0)
            interval = int(storage_cfg.get('clean_interval_seconds', 600))
            root_candidate = disk_dirs[0]
            root = Path(root_candidate.anchor or str(root_candidate))
            disk_cleaner = DiskCleaner(root, disk_dirs, threshold=threshold, target=target,
                                       interval_seconds=max(60, interval))
            disk_cleaner.start()
        except Exception as exc:
            disk_cleaner = None
            print(f'[disk-cleaner] init failed: {exc}')
    try:
        for path in videos:
            process_video(path, args)
    finally:
        if disk_cleaner:
            disk_cleaner.stop()

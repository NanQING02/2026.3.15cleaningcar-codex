import os
import subprocess
import time
from pathlib import Path

import cv2

from .constants import FFMPEG_PIX_BYTES

class FfmpegH264Writer:
    def __init__(self, path, width, height, fps):
        self.path = str(path)
        p = Path(self.path)
        if p.suffix:
            temp_name = p.stem + '_temp' + p.suffix
        else:
            temp_name = p.name + '_temp'
        self._output_path = str(p.with_name(temp_name))
        self.width = int(width)
        self.height = int(height)
        self.fps = float(fps)
        self.proc = None
        self.stdin = None
        self.encoder = None
        self._opened = False
        self._frames_total = 0
        self._frames_since_log = 0
        self._start_time = time.time()
        self._last_log_time = self._start_time
        self._log_interval = 10.0
        self._start()

    def _build_cmd(self, encoder):
        base = [
            'ffmpeg',
            '-y',
            '-f',
            'rawvideo',
            '-pix_fmt',
            'bgr24',
            '-s',
            f'{self.width}x{self.height}',
            '-r',
            f'{self.fps}',
            '-i',
            '-',
            '-an',
        ]
        if encoder in ('h264_rkmpp', 'h264_v4l2m2m', 'h264_omx'):
            opts = [
                '-c:v',
                encoder,
                '-pix_fmt',
                'yuv420p',
            ]
        else:
            opts = [
                '-c:v',
                'libx264',
                '-profile:v',
                'baseline',
                '-level',
                '3.1',
                '-preset',
                'veryfast',
                '-crf',
                '28',
                '-pix_fmt',
                'yuv420p',
            ]
        tail = [
            '-movflags',
            '+faststart',
            self._output_path,
        ]
        return base + opts + tail

    def _try_start(self, encoder):
        cmd = self._build_cmd(encoder)
        try:
            self.proc = subprocess.Popen(
                cmd,
                stdin=subprocess.PIPE,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            self.stdin = self.proc.stdin
            self.encoder = encoder
            time.sleep(0.2)
            if self.proc.poll() is not None:
                self.proc = None
                self.stdin = None
                self.encoder = None
                self._opened = False
                print(f'[per-id-video] encoder {encoder} exited immediately for {self.path}, falling back')
                return False
            self._opened = True
            print(f'[per-id-video] using ffmpeg encoder={encoder} path={self.path}')
            return True
        except Exception as exc:
            self.proc = None
            self.stdin = None
            self.encoder = None
            self._opened = False
            print(f'[per-id-video] failed to start ffmpeg encoder {encoder} for {self.path}: {exc}')
            return False

    def _start(self):
        # Prefer hardware encoders first to reduce CPU usage; fallback to libx264.
        for enc in ('h264_rkmpp', 'h264_v4l2m2m', 'h264_omx', 'libx264'):
            if self._try_start(enc):
                return
        print(f'[per-id-video] no available H.264 encoder for {self.path}')

    def is_opened(self):
        if not self._opened or not self.proc or not self.stdin:
            return False
        if self.proc.poll() is not None:
            return False
        return True

    def write(self, frame):
        if not self.is_opened():
            return
        if frame is None:
            return
        try:
            self.stdin.write(frame.tobytes())
            self._frames_total += 1
            self._frames_since_log += 1
            now = time.time()
            if self._log_interval > 0 and now - self._last_log_time >= self._log_interval:
                elapsed = now - self._last_log_time
                fps = self._frames_since_log / max(elapsed, 1e-6)
                print(f'[per-id-video] encoder={self.encoder} fps={fps:.2f} window={elapsed:.1f}s total_frames={self._frames_total} path={self.path}')
                self._frames_since_log = 0
                self._last_log_time = now
        except Exception as exc:
            print(f'[per-id-video] write failed for {self.path}: {exc}')
            self.release()

    def release(self):
        finalized = False
        if self.stdin:
            try:
                self.stdin.close()
            except Exception:
                pass
            self.stdin = None
        exit_code = None
        if self.proc:
            try:
                self.proc.wait(timeout=60.0)
                exit_code = self.proc.returncode
            except Exception:
                try:
                    self.proc.kill()
                except Exception:
                    pass
            self.proc = None
        self._opened = False
        if getattr(self, '_output_path', None) and self.path:
            try:
                if os.path.exists(self._output_path):
                    if exit_code is None or exit_code != 0:
                        print(f'[per-id-video] ffmpeg exit code {exit_code} for {self._output_path}, not renaming')
                    else:
                        os.replace(self._output_path, self.path)
                        print(f'[per-id-video] finalized video: {self.path}')
                        finalized = True
            except Exception as exc:
                print(f'[per-id-video] rename failed {self._output_path} -> {self.path}: {exc}')
        return finalized

def parse_core_mask(text: str):
    if text is None:
        return None
    s = str(text).strip().lower()
    if s in ('', 'auto', 'default'):
        return None
    if s == 'all':
        return 0b111
    bits = 0
    for part in s.split(','):
        part = part.strip()
        if not part:
            continue
        if '-' in part:
            a, b = part.split('-', 1)
            try:
                a = int(a); b = int(b)
            except ValueError:
                continue
            for k in range(min(a, b), max(a, b) + 1):
                if 0 <= k <= 2:
                    bits |= (1 << k)
        else:
            try:
                k = int(part, 0)
            except ValueError:
                continue
            if 0 <= k <= 2:
                bits |= (1 << k)
    return bits or None


def open_video_capture(src, hw_decode=False, rtsp_latency_ms=200, rtsp_appsink_max_buffers=1):
    if hw_decode and isinstance(src, str):
        pipelines = []
        if src.startswith(('rtsp://', 'rtsps://')):
            rtsp_latency_ms = max(0, int(rtsp_latency_ms))
            rtsp_appsink_max_buffers = max(1, int(rtsp_appsink_max_buffers))
            pipelines.append((
                f"rtspsrc location=\"{src}\" latency={rtsp_latency_ms} protocols=tcp ! "
                "rtph264depay ! h264parse ! mppvideodec ! videoconvert ! "
                f"video/x-raw,format=BGR ! appsink sync=false drop=true max-buffers={rtsp_appsink_max_buffers}",
                '[reader] Using GStreamer+mpp RTSP TCP pipeline for {src}',
            ))
        elif not src.startswith(('http://', 'https://')):
            pipelines.append((
                f"filesrc location=\"{src}\" ! qtdemux ! h264parse ! mppvideodec ! "
                "videoconvert ! video/x-raw,format=BGR ! appsink",
                '[reader] Using GStreamer+mpp file pipeline for {src}',
            ))
        for pipeline, msg in pipelines:
            cap = cv2.VideoCapture(pipeline, cv2.CAP_GSTREAMER)
            if cap.isOpened():
                print(msg.format(src=src))
                return cap
        print(f'[reader] hardware decode pipeline open failed, source={src}')
        return None
    if isinstance(src, str) and src.startswith(('rtsp://', 'rtsps://')):
        if 'OPENCV_FFMPEG_CAPTURE_OPTIONS' not in os.environ:
            os.environ['OPENCV_FFMPEG_CAPTURE_OPTIONS'] = 'rtsp_transport;tcp'
        cap = cv2.VideoCapture(src, cv2.CAP_FFMPEG)
        if cap.isOpened():
            try:
                cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)
            except Exception:
                pass
            print(f'[reader] Using OpenCV FFmpeg RTSP TCP capture for {src}')
        return cap
    return cv2.VideoCapture(src)


def _source_kind_for_decode(path):
    if not isinstance(path, str):
        return 'other'
    text = path.strip()
    lower = text.lower()
    if lower.startswith(('rtsp://', 'rtsps://')):
        return 'rtsp'
    if '://' in lower:
        return 'other'
    try:
        candidate = Path(text).expanduser()
        if candidate.is_file():
            return 'file'
    except OSError:
        pass
    return 'file'


def _is_capture_opened(cap):
    if cap is None or not hasattr(cap, 'isOpened'):
        return False
    try:
        return bool(cap.isOpened())
    except Exception:
        return False


def _safe_release_capture(cap):
    if cap is None or not hasattr(cap, 'release'):
        return
    try:
        cap.release()
    except Exception:
        pass


def create_video_reader(path, args):
    """Create capture and return (capture, decode_meta)."""

    def _safe_int(value, default):
        try:
            return int(value)
        except (TypeError, ValueError):
            return int(default)

    hw = bool(getattr(args, 'hw_decode', False))
    config = getattr(args, '_config', {}) or {}
    video_cfg = config.get('video', {}) or {}
    rtsp_latency_ms = _safe_int(video_cfg.get('rtsp_latency_ms', 200), 200)
    rtsp_appsink_max_buffers = _safe_int(video_cfg.get('rtsp_appsink_max_buffers', 1), 1)
    decode_meta = {
        'decode_mode': 'sw',
        'fallback_used': False,
        'fallback_reason': '',
        'source_kind': _source_kind_for_decode(path),
    }

    if hw:
        cap_hw = open_video_capture(
            path,
            hw_decode=True,
            rtsp_latency_ms=rtsp_latency_ms,
            rtsp_appsink_max_buffers=rtsp_appsink_max_buffers,
        )
        if _is_capture_opened(cap_hw):
            decode_meta['decode_mode'] = 'hw'
            return cap_hw, decode_meta
        _safe_release_capture(cap_hw)
        decode_meta['fallback_used'] = True
        decode_meta['fallback_reason'] = 'hw_open_failed'

    cap_sw = open_video_capture(
        path,
        hw_decode=False,
        rtsp_latency_ms=rtsp_latency_ms,
        rtsp_appsink_max_buffers=rtsp_appsink_max_buffers,
    )
    if _is_capture_opened(cap_sw):
        decode_meta['decode_mode'] = 'sw'
        return cap_sw, decode_meta
    _safe_release_capture(cap_sw)
    decode_meta['decode_mode'] = 'sw'
    return None, decode_meta


def finalize_per_id_recording(writer, track_id, track_state, event_manager):
    if writer is None:
        return False
    finalized = False
    try:
        finalized = bool(writer.release())
    except Exception:
        finalized = False
    if not finalized:
        return False

    state = track_state or {}
    output_path = Path(getattr(writer, 'path', '') or '')
    keep_video = bool(state.get('type2_qualified'))
    if not keep_video:
        if output_path:
            try:
                output_path.unlink(missing_ok=True)
            except Exception:
                pass
        return False

    if output_path and not output_path.exists():
        return False

    frame_idx = state.get('record_stop_frame')
    if frame_idx is None:
        frame_idx = state.get('last_frame_idx', 0)
    frame = state.get('last_frame')
    try:
        event_manager.emit_event(track_id, 6, frame_idx, frame, {}, state)
    except Exception:
        return False
    return True


def detect_source_mode(path, override='auto', base_dir=None):
    mode = (override or 'auto').lower()
    if isinstance(path, str):
        lower = path.lower().strip()
        if lower.startswith(('rtsp://', 'rtsp:', 'rtmp://', 'rtp://', 'rtsps://', 'http://', 'https://')):
            return 'camera'
        try:
            candidate = Path(path).expanduser()
            if not candidate.is_absolute():
                root = Path(base_dir) if base_dir is not None else Path.cwd()
                candidate = (root / candidate).resolve()
            if candidate.is_file():
                return 'file'
        except OSError:
            pass
    if mode == 'file':
        return 'file'
    return 'camera'


def _resolve_runtime_path(path_value, base_dir):
    if path_value in (None, ''):
        return None
    if isinstance(path_value, Path):
        path = path_value
    else:
        path = Path(str(path_value))
    path = path.expanduser()
    if not path.is_absolute():
        base = base_dir if base_dir else Path.cwd()
        path = base / path
    return path.resolve()


def _collect_storage_directories(config, base_dir):
    directories = []

    def push(value, treat_as_file=False):
        resolved = _resolve_runtime_path(value, base_dir)
        if not resolved:
            return
        directories.append(resolved.parent if treat_as_file else resolved)

    video_cfg = config.get('video', {}) or {}
    push(video_cfg.get('save_video'), treat_as_file=True)
    base = base_dir if base_dir else Path.cwd()
    directories.append((base / 'video_result').resolve())

    push(config.get('event_capture_dir'))
    directories.append((base / 'captures').resolve())

    push(config.get('event_output_dir'))
    directories.append((base / 'events').resolve())

    push(video_cfg.get('csv'), treat_as_file=True)
    debug_frame = video_cfg.get('debug_frame_path')
    if debug_frame:
        debug_path = _resolve_runtime_path(debug_frame, base_dir)
        if debug_path:
            directories.append(debug_path.parent if debug_path.suffix else debug_path)
    unique = []
    seen = set()
    for directory in directories:
        if not directory:
            continue
        resolved = Path(directory).resolve()
        key = str(resolved)
        if key in seen:
            continue
        seen.add(key)
        unique.append(resolved)
    return unique

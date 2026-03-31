import os
import subprocess
import time
from contextlib import contextmanager
from pathlib import Path

import cv2

FFMPEG_HW_ENCODERS = ('h264_rkmpp', 'h264_v4l2m2m', 'h264_omx')
FFMPEG_SW_ENCODERS = ('libx264',)
GSTREAMER_HW_ENCODERS = ('mpph264enc', 'v4l2h264enc', 'omxh264enc')
FFMPEG_HW_DECODER_CANDIDATES = (
    'h264_rkmpp',
    'hevc_rkmpp',
    'mjpeg_rkmpp',
    'mpeg2_rkmpp',
    'vp8_rkmpp',
    'vp9_rkmpp',
)


class FfmpegH264Writer:
    def __init__(self, path, width, height, fps, encoders=None):
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
        self.backend = 'ffmpeg'
        self._encoders = tuple(encoders or (FFMPEG_HW_ENCODERS + FFMPEG_SW_ENCODERS))
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
        self.proc = None
        self.stdin = None
        self.encoder = None
        self._opened = False
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
        for enc in self._encoders:
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


class GstreamerH264Writer:
    def __init__(self, path, width, height, fps, encoders=None):
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
        self.encoder = None
        self.backend = 'gstreamer'
        self._encoders = tuple(encoders or GSTREAMER_HW_ENCODERS)
        self._writer = None
        self._opened = False
        self._frames_total = 0
        self._frames_since_log = 0
        self._start_time = time.time()
        self._last_log_time = self._start_time
        self._log_interval = 10.0
        self._start()

    def _build_pipeline(self, encoder):
        out_path = self._output_path.replace('\\', '/').replace('"', '\\"')
        return (
            'appsrc '
            f'caps=video/x-raw,format=BGR,width={self.width},height={self.height},framerate={max(int(round(self.fps)), 1)}/1 '
            '! videoconvert '
            f'! {encoder} '
            '! h264parse ! qtmux faststart=true '
            f'! filesink location="{out_path}" sync=false'
        )

    def _try_start(self, encoder):
        self._writer = None
        self.encoder = None
        self._opened = False
        pipeline = self._build_pipeline(encoder)
        try:
            writer = cv2.VideoWriter(
                pipeline,
                cv2.CAP_GSTREAMER,
                0,
                self.fps,
                (self.width, self.height),
                True,
            )
            if not writer.isOpened():
                writer.release()
                print(f'[per-id-video] gstreamer encoder {encoder} not available for {self.path}, falling back')
                return False
            self._writer = writer
            self.encoder = encoder
            self._opened = True
            print(f'[per-id-video] using gstreamer encoder={encoder} path={self.path}')
            return True
        except Exception as exc:
            self._writer = None
            self.encoder = None
            self._opened = False
            print(f'[per-id-video] failed to start gstreamer encoder {encoder} for {self.path}: {exc}')
            return False

    def _start(self):
        for enc in self._encoders:
            if self._try_start(enc):
                return
        print(f'[per-id-video] no available GStreamer H.264 encoder for {self.path}')

    def is_opened(self):
        return bool(self._opened and self._writer is not None and self._writer.isOpened())

    def write(self, frame):
        if not self.is_opened() or frame is None:
            return
        try:
            self._writer.write(frame)
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
        if self._writer is not None:
            try:
                self._writer.release()
            except Exception:
                pass
            self._writer = None
        self._opened = False
        if getattr(self, '_output_path', None) and self.path:
            try:
                if os.path.exists(self._output_path):
                    os.replace(self._output_path, self.path)
                    print(f'[per-id-video] finalized video: {self.path}')
                    finalized = True
            except Exception as exc:
                print(f'[per-id-video] rename failed {self._output_path} -> {self.path}: {exc}')
        return finalized


def _safe_release_writer(writer):
    if writer is None or not hasattr(writer, 'release'):
        return
    try:
        writer.release()
    except Exception:
        pass


def create_h264_video_writer(path, width, height, fps):
    attempt_order = []
    meta = {
        'writer_mode': 'sw',
        'writer_backend': 'none',
        'fallback_used': False,
        'fallback_reason': '',
        'attempt_order': attempt_order,
    }

    attempt_order.append('ffmpeg_hw')
    writer = FfmpegH264Writer(path, width, height, fps, encoders=FFMPEG_HW_ENCODERS)
    if writer.is_opened():
        meta['writer_mode'] = 'hw'
        meta['writer_backend'] = 'ffmpeg'
        return writer, meta
    _safe_release_writer(writer)

    attempt_order.append('gstreamer_hw')
    writer = GstreamerH264Writer(path, width, height, fps, encoders=GSTREAMER_HW_ENCODERS)
    if writer.is_opened():
        meta['writer_mode'] = 'hw'
        meta['writer_backend'] = 'gstreamer'
        meta['fallback_used'] = True
        meta['fallback_reason'] = 'ffmpeg_hw_open_failed'
        return writer, meta
    _safe_release_writer(writer)

    attempt_order.append('ffmpeg_sw')
    writer = FfmpegH264Writer(path, width, height, fps, encoders=FFMPEG_SW_ENCODERS)
    if writer.is_opened():
        meta['writer_mode'] = 'sw'
        meta['writer_backend'] = 'ffmpeg'
        meta['fallback_used'] = True
        meta['fallback_reason'] = 'hw_writer_open_failed'
        return writer, meta
    _safe_release_writer(writer)

    meta['writer_backend'] = 'ffmpeg'
    meta['fallback_used'] = True
    meta['fallback_reason'] = 'all_writer_open_failed'
    return None, meta

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


def _format_ffmpeg_capture_options(options):
    chunks = []
    for key, value in options:
        if value in (None, ''):
            continue
        chunks.append(f'{key};{value}')
    return '|'.join(chunks)


def _merge_ffmpeg_capture_options(extra):
    current = str(os.environ.get('OPENCV_FFMPEG_CAPTURE_OPTIONS', '') or '').strip()
    if current and extra:
        return f'{current}|{extra}'
    return extra or current


@contextmanager
def _temporary_env_var(name, value):
    had_original = name in os.environ
    original = os.environ.get(name)
    if value:
        os.environ[name] = value
    else:
        os.environ.pop(name, None)
    try:
        yield
    finally:
        if had_original:
            os.environ[name] = original
        else:
            os.environ.pop(name, None)


def _open_ffmpeg_capture(src, option_text=''):
    merged_options = _merge_ffmpeg_capture_options(option_text)
    with _temporary_env_var('OPENCV_FFMPEG_CAPTURE_OPTIONS', merged_options):
        return cv2.VideoCapture(src, cv2.CAP_FFMPEG)


def _ordered_ffmpeg_hw_decoders(src):
    if not isinstance(src, str):
        return FFMPEG_HW_DECODER_CANDIDATES
    lower = src.lower()
    if '265' in lower or 'hevc' in lower:
        return ('hevc_rkmpp',) + tuple(dec for dec in FFMPEG_HW_DECODER_CANDIDATES if dec != 'hevc_rkmpp')
    return FFMPEG_HW_DECODER_CANDIDATES


def _open_ffmpeg_hardware_capture(src, rtsp_latency_ms=200, rtsp_appsink_max_buffers=1):
    if not isinstance(src, str):
        return None
    base_options = []
    if src.startswith(('rtsp://', 'rtsps://')):
        base_options.append(('rtsp_transport', 'tcp'))
    for decoder in _ordered_ffmpeg_hw_decoders(src):
        option_text = _format_ffmpeg_capture_options(base_options + [
            ('hw_decoders_any', 'rkmpp'),
            ('video_codec', decoder),
        ])
        cap = _open_ffmpeg_capture(src, option_text=option_text)
        if _is_capture_opened(cap):
            try:
                cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)
            except Exception:
                pass
            print(f'[reader] Using FFmpeg hardware decoder {decoder} for {src}')
            return cap
        _safe_release_capture(cap)
    print(f'[reader] ffmpeg hardware decode open failed, source={src}')
    return None


def _open_gstreamer_hardware_capture(src, rtsp_latency_ms=200, rtsp_appsink_max_buffers=1):
    if not isinstance(src, str):
        return None
    pipelines = []
    if src.startswith(('rtsp://', 'rtsps://')):
        rtsp_latency_ms = max(0, int(rtsp_latency_ms))
        rtsp_appsink_max_buffers = max(1, int(rtsp_appsink_max_buffers))
        pipelines.append((
            f'rtspsrc location="{src}" latency={rtsp_latency_ms} protocols=tcp ! '
            'rtph264depay ! h264parse ! mppvideodec ! videoconvert ! '
            f'video/x-raw,format=BGR ! appsink sync=false drop=true max-buffers={rtsp_appsink_max_buffers}',
            '[reader] Using GStreamer+mpp RTSP TCP pipeline for {src}',
        ))
    elif not src.startswith(('http://', 'https://')):
        pipelines.append((
            f'filesrc location="{src}" ! qtdemux ! h264parse ! mppvideodec ! '
            'videoconvert ! video/x-raw,format=BGR ! appsink',
            '[reader] Using GStreamer+mpp file pipeline for {src}',
        ))
    for pipeline, msg in pipelines:
        cap = cv2.VideoCapture(pipeline, cv2.CAP_GSTREAMER)
        if _is_capture_opened(cap):
            print(msg.format(src=src))
            return cap
        _safe_release_capture(cap)
    print(f'[reader] gstreamer hardware decode open failed, source={src}')
    return None


def _open_software_capture(src, rtsp_latency_ms=200, rtsp_appsink_max_buffers=1):
    if isinstance(src, str) and src.startswith(('rtsp://', 'rtsps://')):
        cap = _open_ffmpeg_capture(src, option_text='rtsp_transport;tcp')
        if _is_capture_opened(cap):
            try:
                cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)
            except Exception:
                pass
            print(f'[reader] Using OpenCV FFmpeg RTSP TCP capture for {src}')
        return cap
    return cv2.VideoCapture(src)


def open_video_capture(src, hw_decode=False, rtsp_latency_ms=200, rtsp_appsink_max_buffers=1):
    if hw_decode:
        cap = _open_ffmpeg_hardware_capture(
            src,
            rtsp_latency_ms=rtsp_latency_ms,
            rtsp_appsink_max_buffers=rtsp_appsink_max_buffers,
        )
        if _is_capture_opened(cap):
            return cap
        _safe_release_capture(cap)
        cap = _open_gstreamer_hardware_capture(
            src,
            rtsp_latency_ms=rtsp_latency_ms,
            rtsp_appsink_max_buffers=rtsp_appsink_max_buffers,
        )
        if _is_capture_opened(cap):
            return cap
        _safe_release_capture(cap)
        return None
    return _open_software_capture(
        src,
        rtsp_latency_ms=rtsp_latency_ms,
        rtsp_appsink_max_buffers=rtsp_appsink_max_buffers,
    )


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
        'decode_backend': 'software',
        'fallback_used': False,
        'fallback_reason': '',
        'source_kind': _source_kind_for_decode(path),
        'attempt_order': [],
    }

    attempt_order = decode_meta['attempt_order']
    if hw:
        attempt_order.append('ffmpeg_hw')
        cap_hw = _open_ffmpeg_hardware_capture(
            path,
            rtsp_latency_ms=rtsp_latency_ms,
            rtsp_appsink_max_buffers=rtsp_appsink_max_buffers,
        )
        if _is_capture_opened(cap_hw):
            decode_meta['decode_mode'] = 'hw'
            decode_meta['decode_backend'] = 'ffmpeg'
            return cap_hw, decode_meta
        _safe_release_capture(cap_hw)
        attempt_order.append('gstreamer_hw')
        cap_hw = _open_gstreamer_hardware_capture(
            path,
            rtsp_latency_ms=rtsp_latency_ms,
            rtsp_appsink_max_buffers=rtsp_appsink_max_buffers,
        )
        if _is_capture_opened(cap_hw):
            decode_meta['decode_mode'] = 'hw'
            decode_meta['decode_backend'] = 'gstreamer'
            decode_meta['fallback_used'] = True
            decode_meta['fallback_reason'] = 'ffmpeg_hw_open_failed'
            return cap_hw, decode_meta
        _safe_release_capture(cap_hw)

    attempt_order.append('software')
    cap_sw = _open_software_capture(
        path,
        rtsp_latency_ms=rtsp_latency_ms,
        rtsp_appsink_max_buffers=rtsp_appsink_max_buffers,
    )
    if _is_capture_opened(cap_sw):
        decode_meta['decode_mode'] = 'sw'
        decode_meta['decode_backend'] = 'software'
        if hw:
            decode_meta['fallback_used'] = True
            decode_meta['fallback_reason'] = 'hw_open_failed'
        return cap_sw, decode_meta
    _safe_release_capture(cap_sw)
    decode_meta['decode_mode'] = 'sw'
    decode_meta['decode_backend'] = 'software'
    if hw:
        decode_meta['fallback_used'] = True
        decode_meta['fallback_reason'] = 'hw_open_failed'
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

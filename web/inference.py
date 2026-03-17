import subprocess
import sys
import threading
import time
from collections import deque
from datetime import datetime
from pathlib import Path
from typing import Dict, Optional

from fastapi import HTTPException

from config_manager import ConfigError, ConfigManager

from . import state

class InferenceManager:
    def __init__(self, script_path: Path, config_path: Path):
        self.script_path = Path(script_path)
        self.config_path = Path(config_path)
        self.process: Optional[subprocess.Popen] = None
        self.single_shot = False
        self.lock = threading.Lock()
        self.desired = False
        self.auto_restart = True
        self.restart_count = 0
        self.last_start: Optional[float] = None
        self.last_exit: Optional[Dict[str, float]] = None
        self.log_buffer = deque(maxlen=800)
        self.log_lock = threading.Lock()
        self.shutdown = threading.Event()
        self.monitor_thread = threading.Thread(target=self._monitor_loop, daemon=True)
        self.monitor_thread.start()

    def _append_log(self, message: str):
        ts = time.time()
        stamp = datetime.fromtimestamp(ts).strftime('%Y-%m-%d %H:%M:%S')
        line = f'{stamp} {message}'
        print(line)
        with self.log_lock:
            self.log_buffer.append((ts, message))

    def _capture_output(self, proc: subprocess.Popen, log_path: Optional[Path] = None):
        if not proc.stdout:
            return
        f = None
        if log_path is not None:
            try:
                log_path.parent.mkdir(parents=True, exist_ok=True)
                f = log_path.open("a", encoding="utf-8")
            except Exception:
                f = None
        try:
            for raw in proc.stdout:
                if not raw:
                    break
                line = raw.rstrip()
                self._append_log(f'[infer] {line}')
                if f is not None:
                    try:
                        f.write(line + "\n")
                    except Exception:
                        pass
        finally:
            try:
                if proc.stdout:
                    proc.stdout.close()
            except Exception:
                pass
            if f is not None:
                try:
                    f.close()
                except Exception:
                    pass

    def _launch_locked(self):
        if not self.script_path.exists():
            raise RuntimeError('run_zone_detect.py not found')
        self.single_shot = self._detect_single_shot()
        if self.single_shot:
            self._append_log('[guardian] file source detected，本次推理完成后不会自动重启')
        cmd = [sys.executable, str(self.script_path), "--config", str(self.config_path)]
        proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, bufsize=1)
        self.process = proc
        self.restart_count += 1
        self.last_start = time.time()
        log_dir = ROOT / "logs" / "inference"
        log_name = datetime.fromtimestamp(self.last_start).strftime("infer_%Y%m%d_%H%M%S.log")
        log_path = log_dir / log_name
        threading.Thread(target=self._capture_output, args=(proc, log_path), daemon=True).start()
        self._append_log(f'[guardian] started inference pid={proc.pid} log={log_path}')

    def _terminate_locked(self):
        if not self.process:
            return
        proc = self.process
        self._append_log(f'[guardian] stopping pid={proc.pid}')
        try:
            proc.terminate()
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()
        finally:
            code = proc.poll()
            self.last_exit = {'time': time.time(), 'code': code if code is not None else -1}
            self.process = None

    def start(self):
        with self.lock:
            self.desired = True
            if not self.process or self.process.poll() is not None:
                self._launch_locked()
        return self.status()

    def stop(self):
        with self.lock:
            self.desired = False
            self.auto_restart = False
            self._terminate_locked()
        return self.status()

    def restart(self):
        with self.lock:
            self.desired = True
            self._terminate_locked()
            self._launch_locked()
        return self.status()

    def set_auto_restart(self, enabled: bool):
        with self.lock:
            self.auto_restart = bool(enabled)
        self._append_log(f'[guardian] auto_restart set to {enabled}')

    def status(self):
        with self.lock:
            running = bool(self.process and self.process.poll() is None)
            pid = self.process.pid if running else None
            last_start = self.last_start
            last_exit = self.last_exit
            restart_count = self.restart_count
            auto_restart = self.auto_restart
        status = {
            'running': running,
            'pid': pid,
            'last_start': last_start,
            'last_exit': last_exit,
            'restart_count': restart_count,
            'auto_restart': auto_restart,
            'single_shot': self.single_shot,
        }
        return status

    def logs(self, limit: int = 200):
        limit = max(1, min(1000, int(limit)))
        with self.log_lock:
            items = list(self.log_buffer)[-limit:]
        result = []
        for ts, line in items:
            result.append({'timestamp': datetime.fromtimestamp(ts).isoformat(timespec='seconds'), 'line': line})
        return result

    def _detect_single_shot(self) -> bool:
        try:
            cfg = ConfigManager(self.config_path)
        except ConfigError as exc:
            self._append_log(f'[guardian] config error: {exc}')
            return False
        video_cfg = cfg.video
        source = str(video_cfg.get('source', '')).strip()
        if not source:
            return False
        lowered = source.lower()
        if lowered.startswith(('rtsp://', 'rtmp://', 'rtp://', 'rtsps://', 'http://', 'https://')):
            return False
        mode = str(video_cfg.get('source_mode', '')).lower()
        if mode == 'file':
            return True
        if mode == 'camera':
            return False
        candidate = Path(source).expanduser()
        return candidate.is_file()

    def _monitor_loop(self):
        while not self.shutdown.is_set():
            with self.lock:
                desired = self.desired
                auto_restart = self.auto_restart
            if desired:
                should_launch = False
                with self.lock:
                    if not self.process:
                        should_launch = True
                    else:
                        code = self.process.poll()
                        if code is not None:
                            self._append_log(f'[guardian] process exited with code {code}')
                            self.last_exit = {'time': time.time(), 'code': code}
                            self.process = None
                            if self.single_shot:
                                should_launch = False
                                self.desired = False
                                self._append_log('[guardian] 单次文件源运行完成，等待手动启动')
                                self.single_shot = False
                            else:
                                should_launch = auto_restart
                if should_launch:
                    try:
                        with self.lock:
                            self._launch_locked()
                    except Exception as exc:
                        self._append_log(f'[guardian] failed to start inference: {exc}')
                        time.sleep(5)
            else:
                with self.lock:
                    if self.process:
                        self._terminate_locked()
            time.sleep(1)

    def shutdown_manager(self):
        self.shutdown.set()
        with self.lock:
            self.desired = False
            self.auto_restart = False
            self._terminate_locked()
        self._append_log('[guardian] shutdown complete')

    def set_config_path(self, path: Path):
        with self.lock:
            self.config_path = Path(path)


INFERENCE_MANAGERS: Dict[str, InferenceManager] = {}


def _resolve_config_path_for_key(key: str) -> Path:
    key = str(key or "").strip()
    base = state.CONFIG_PATH.parent
    if not key:
        candidate = state.CONFIG_PATH
    else:
        candidate = (base / key).resolve()
    if not candidate.exists():
        raise HTTPException(status_code=404, detail="配置文件不存在")
    if candidate.suffix.lower() != ".json":
        raise HTTPException(status_code=400, detail="仅支持 JSON 配置")
    return candidate


def _get_inference_manager_for_key(key: str) -> InferenceManager:
    cfg_path = _resolve_config_path_for_key(key)
    mgr = INFERENCE_MANAGERS.get(key)
    if mgr is None:
        mgr = InferenceManager(state.RUN_SCRIPT, cfg_path)
        INFERENCE_MANAGERS[key] = mgr
    else:
        mgr.script_path = state.RUN_SCRIPT
        mgr.config_path = cfg_path
    return mgr


def _get_default_inference_manager() -> InferenceManager:
    return _get_inference_manager_for_key(state.CONFIG_PATH.name)


def startup_default_manager():
    return _get_default_inference_manager()


def shutdown_all_inference_managers():
    for mgr in list(INFERENCE_MANAGERS.values()):
        try:
            mgr.shutdown_manager()
        except Exception as exc:
            print(f'[server] shutdown: failed to shutdown manager: {exc}')

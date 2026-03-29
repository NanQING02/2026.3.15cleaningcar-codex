import base64
import json
import threading
import time
import urllib.request
from collections import deque
from datetime import datetime
from math import hypot
from pathlib import Path

import cv2

from utils.upload_queue import SQLiteUploadQueue

from .constants import VEHICLE_LABEL_CN
from .plate import normalize_plate_text
from .resize_accel import resize_bgr
from .vision import box_iou, get_anchor_point

YELLOW_TRUCK_LABEL = 'yellow truck'
NON_YELLOW_OVERRIDE_LABELS = frozenset(label for label in VEHICLE_LABEL_CN.keys() if label != YELLOW_TRUCK_LABEL)
NON_YELLOW_OVERRIDE_SECONDS = 2.0
NON_YELLOW_HIGH_CONFIDENCE = 0.85
NON_YELLOW_HIGH_CONF_STREAK = 3

class EventUploader:
    def __init__(self, url=None, token=None, timeout=8.0, queue_path=None,
                 max_retries=10, base_delay=1.0, max_delay=60.0):
        self.url = (url or '').strip()
        self.token = token
        self.timeout = timeout
        self.max_retries = max(1, int(max_retries))
        self.base_delay = max(0.5, float(base_delay))
        self.max_delay = max(self.base_delay, float(max_delay))
        self.db = None
        self.thread = None
        self._stop = threading.Event()
        self._wake = threading.Event()
        if self.url:
            db_path = Path(queue_path) if queue_path else (Path('./events') / 'upload_queue.db')
            self.db = SQLiteUploadQueue(db_path)
            self.thread = threading.Thread(target=self._worker, daemon=True)
            self.thread.start()

    def enqueue(self, payload):
        if not self.db or payload is None:
            return
        self.db.enqueue(payload)
        self._wake.set()

    def _worker(self):
        while not self._stop.is_set():
            job = self.db.next_job() if self.db else None
            if not job:
                self._wake.wait(timeout=1.0)
                self._wake.clear()
                continue
            job_id, payload, retries = job
            try:
                self._send(payload)
            except Exception as exc:
                delay = min(self.base_delay * (2 ** retries), self.max_delay)
                if retries + 1 > self.max_retries:
                    print(f'[uploader] drop event after {retries} retries: {exc}')
                    if self.db:
                        self.db.mark_success(job_id)
                else:
                    print(f'[uploader] failed to send event (retry in {delay:.1f}s): {exc}')
                    if self.db:
                        self.db.mark_failure(job_id, retries + 1, delay)
                continue
            if self.db:
                self.db.mark_success(job_id)

    def _send(self, data):
        body = json.dumps(data).encode('utf-8')
        req = urllib.request.Request(self.url, data=body, method='POST')
        req.add_header('Content-Type', 'application/json')
        if self.token:
            req.add_header('Authorization', f'Bearer {self.token}')
        with urllib.request.urlopen(req, timeout=self.timeout) as resp:
            resp.read()

    def close(self):
        if not self.thread:
            if self.db:
                self.db.close()
            return
        self._stop.set()
        self._wake.set()
        self.thread.join(timeout=2.0)
        if self.db:
            pending = self.db.pending()
            if pending:
                print(f'[uploader] pending events retained in queue ({pending})')
            self.db.close()


class EventManager:
    def __init__(self, config, fps, frame_size, zone_manager, event_log_path=None, uploader=None, capture_mode='path'):
        self.config = config
        self.logic = config.get('logic', {})
        self.zone_mgr = zone_manager
        self.fps = fps
        self.frame_w, self.frame_h = frame_size
        self.camera_id = config.get('camera_id', 'CAM')
        self.capture_dir = Path(config.get('event_capture_dir', './captures'))
        self.capture_dir.mkdir(parents=True, exist_ok=True)
        self.events_dir = Path(config.get('event_output_dir', './events'))
        self.events_dir.mkdir(parents=True, exist_ok=True)
        self.tracks = {}
        self.timeout_frames = int(config.get('track_timeout_frames', 60))
        self.base_time = datetime.now()
        self.stationary_min_frames = int(config.get('stationary_min_frames', 0))
        self.stationary_speed_thresh = float(config.get('stationary_speed_thresh', 8.0))
        self.min_water_hit_frames_for_wash = int(self.logic.get('min_water_hit_frames_for_wash', 30))
        self.water_window_size = int(self.logic.get('water_window_size', 20))
        self.water_window_min_hits = int(self.logic.get('water_window_min_hits', 3))
        self.vehicle_shrink_ratio = float(config.get('vehicle_shrink_ratio', 0.35))
        self.vehicle_lock_min_votes = int(config.get('vehicle_lock_min_votes', 80))
        self.vehicle_lock_on_confirm = bool(config.get('vehicle_lock_on_confirm', True))
        shadow_cfg = config.get('shadow_pool', {})
        self.shadow_max = int(shadow_cfg.get('max_candidates', 50))
        self.shadow_max_age = int(shadow_cfg.get('max_age_frames', 120))
        self.shadow_pool = {}
        self.event_log_path = Path(event_log_path) if event_log_path else None
        if self.event_log_path:
            self.event_log_path.parent.mkdir(parents=True, exist_ok=True)
            if not self.event_log_path.exists():
                with self.event_log_path.open('w', encoding='utf-8') as f:
                    f.write('camera_id,track_id,type,capture_time,frame_idx,stationary_frames,'
                            'wash_duration,plate,vehicle,direction_code,direction_label,plate_is_guess,anchor_dwell_frames\n')
        self.uploader = uploader
        self.capture_mode = capture_mode
        self.lane_name = config.get('lane_name', '冲洗')
        self.default_plate_color = config.get('default_plate_color', '')
        self.default_plate_color_conf = float(config.get('default_plate_color_conf', 0.0))
        self.default_cleanliness = int(config.get('default_cleanliness', 0))
        self.anchor_offset_ratio = float(self.logic.get('anchor_offset_ratio', 0.0))
        if self.anchor_offset_ratio < 0.0:
            self.anchor_offset_ratio = 0.0
        elif self.anchor_offset_ratio > 0.95:
            self.anchor_offset_ratio = 0.95
        self.zone_b_anchor_min_frames = int(self.logic.get('zone_b_anchor_min_frames', 0))
        if self.zone_b_anchor_min_frames < 0:
            self.zone_b_anchor_min_frames = 0
        self.min_type5_zone_a_dwell = int(self.logic.get('min_zone_a_dwell_frames_for_type5', 0))
        if self.min_type5_zone_a_dwell < 0:
            self.min_type5_zone_a_dwell = 0
        self.min_type1_track_frames = int(self.logic.get('min_track_frames_for_type1', 10))
        if self.min_type1_track_frames < 10:
            self.min_type1_track_frames = 10
        self.wash_dwell_offset = 0.0
        self.min_type4_zone_b_dwell = int(self.logic.get('min_zone_b_dwell_frames_for_type4', 60))
        if self.min_type4_zone_b_dwell < 0:
            self.min_type4_zone_b_dwell = 0
        self.allowed_events = {1, 2, 3, 4, 5, 6}
        self.disable_plate_only_events = True
        self.single_lifecycle_events = True
        self.require_vehicle_type_for_events = bool(self.logic.get('require_vehicle_type_for_events', False))
        self.max_per_id_video_seconds = 600.0
        self.per_id_video_tail_seconds = 5.0
        self.pending_events = {}
        self.upload_buffer = {}
        self.upload_qualified = set()
        self.upload_log_full = None
        self.upload_log_sent = None
        if self.uploader:
            self.upload_log_full = self.events_dir / 'upload_log_full.csv'
            self.upload_log_sent = self.events_dir / 'upload_log_sent.csv'
            if not self.upload_log_full.exists():
                with self.upload_log_full.open('w', encoding='utf-8') as f:
                    f.write('capture_time,id,type,sent,payload\n')
            if not self.upload_log_sent.exists():
                with self.upload_log_sent.open('w', encoding='utf-8') as f:
                    f.write('capture_time,id,type,payload\n')
        self.frame_timing = {}

    def record_frame_timing(self, frame_idx, capture_ts, infer_ts):
        if capture_ts is None or infer_ts is None:
            return
        try:
            self.frame_timing[int(frame_idx)] = (float(capture_ts), float(infer_ts))
        except Exception:
            return

    def frame_timestamp(self, frame_idx):
        return datetime.now().strftime('%Y-%m-%d %H:%M:%S')

    def update_track(self, track_id, plate_box, vehicle_box, plate_text, frame_idx, frame,
                     water_boxes, water_active, is_plate, vehicle_label, vehicle_conf,
                     plate_conf, confirmed, cleaning_label='', anchor_point=None, plate_is_guess=False,
                     plate_color='', plate_color_conf=None, plate_type=''):
        if track_id <= 0:
            return
        st = self.tracks.setdefault(track_id, {
            'events': set(),
            'stationary_frames': 0,
            'speed_buf': deque(maxlen=6),
            'wash_duration': 0.0,
            'washing': False,
            'washing_candidate': False,
            'washing_confirmed': False,
            'water_detected': False,
            'water_hit_frames': 0,
            'water_window': deque(maxlen=20),
            'effective_wash_frames': 0,
            'last_frame_idx': frame_idx,
            'last_frame': None,
            'plate_text': '',
            'plate_is_guess': False,
            'plate_color': '',
            'plate_color_conf': 0.0,
            'plate_type': '',
            'vehicle_cls': '',
            'last_plate_box': None,
            'last_vehicle_box': None,
            'confirmed': False,
            'plate_conf_history': [],
            'vehicle_conf_history': [],
            'wash_start_time': None,
            'wash_end_time': None,
            'lane': self.lane_name,
            'last_cleaning': '',
            'vehicle_cls_frozen': False,
            'class_counts': {},
            'vehicle_cls_locked': '',
            'vehicle_non_yellow_recent': deque(),
            'vehicle_high_conf_label': '',
            'vehicle_high_conf_count': 0,
            'last_vehicle_label': '',
            'zone_state': None,
            'last_anchor': None,
            'last_type3_frame': -1,
            'last_type4_frame': -1,
            'zone_b_enter_frame': -1,
            'zone_b_dwell_frames': 0,
            'zone_a_enter_frame': -1,
            'zone_a_dwell_frames': 0,
            'track_frame_count': 0,
            'abnormal_reasons': set(),
            'type2_qualified': False,
            'type2_qualified_frame': -1,
        })
        if self.single_lifecycle_events and st.get('closed'):
            st['last_frame_idx'] = frame_idx
            return
        st['track_frame_count'] = st.get('track_frame_count', 0) + 1
        st['last_frame_idx'] = frame_idx
        if frame is not None:
            st['last_frame'] = frame.copy()
        freeze_label = st.get('vehicle_cls_frozen', False)
        if vehicle_box is not None and st.get('last_vehicle_box') is not None:
            prev = st['last_vehicle_box']
            prev_area = max(1.0, (prev[2] - prev[0]) * (prev[3] - prev[1]))
            new_area = max(1.0, (vehicle_box[2] - vehicle_box[0]) * (vehicle_box[3] - vehicle_box[1]))
            if new_area < prev_area * self.vehicle_shrink_ratio:
                freeze_label = True
        counts = st.get('class_counts') or {}
        override_label = self._get_non_yellow_vehicle_override(st, vehicle_label, vehicle_conf, frame_idx)
        if override_label:
            counts = self._apply_non_yellow_vehicle_override(st, counts, override_label)
            freeze_label = True
        elif vehicle_label and not freeze_label:
            counts[vehicle_label] = counts.get(vehicle_label, 0) + 1
            st['class_counts'] = counts
            locked, locked_count = max(counts.items(), key=lambda kv: (kv[1], kv[0]))
            st['vehicle_cls_locked'] = locked
            st['vehicle_cls'] = locked
            if locked_count >= self.vehicle_lock_min_votes:
                st['vehicle_cls_frozen'] = True
        elif vehicle_label and st.get('vehicle_cls_locked') and vehicle_label == st['vehicle_cls_locked']:
            counts[vehicle_label] = counts.get(vehicle_label, 0) + 1
            st['class_counts'] = counts
        elif not st.get('vehicle_cls') and st.get('vehicle_cls_locked'):
            st['vehicle_cls'] = st['vehicle_cls_locked']
        if vehicle_label:
            st['last_vehicle_label'] = vehicle_label
            if not st.get('vehicle_cls'):
                st['vehicle_cls'] = vehicle_label
        if freeze_label and st.get('vehicle_cls_locked'):
            st['vehicle_cls_frozen'] = True
        if vehicle_box is not None:
            st['last_vehicle_box'] = vehicle_box
        if plate_box is not None:
            st['last_plate_box'] = plate_box
        normalized_plate = normalize_plate_text(plate_text)
        if normalized_plate:
            st['plate_text'] = normalized_plate
            st['plate_is_guess'] = bool(plate_is_guess)
            self._add_shadow_candidate(track_id, normalized_plate, plate_conf, frame_idx)
        elif plate_conf and plate_conf > 0.0:
            self._add_shadow_candidate(track_id, plate_text, plate_conf, frame_idx)
        if plate_color:
            st['plate_color'] = str(plate_color)
            if plate_color_conf is not None:
                try:
                    st['plate_color_conf'] = float(plate_color_conf)
                except (TypeError, ValueError):
                    pass
        if plate_type:
            st['plate_type'] = str(plate_type)
        if confirmed:
            st['confirmed'] = True
            if self.vehicle_lock_on_confirm and st.get('vehicle_cls_locked'):
                st['vehicle_cls_frozen'] = True
        if vehicle_conf is not None:
            history = st.get('vehicle_conf_history') or []
            history.append(float(vehicle_conf))
            if len(history) > 60:
                history.pop(0)
            st['vehicle_conf_history'] = history
        if plate_conf is not None:
            history = st.get('plate_conf_history') or []
            history.append(float(plate_conf))
            if len(history) > 60:
                history.pop(0)
            st['plate_conf_history'] = history
        if cleaning_label:
            st['last_cleaning'] = cleaning_label
        ref_box = vehicle_box or plate_box or st.get('last_vehicle_box') or st.get('last_plate_box')
        prev_box = st.get('last_vehicle_box')
        dist = 0.0
        if ref_box is not None and prev_box is not None:
            cx = 0.5 * (ref_box[0] + ref_box[2])
            cy = 0.5 * (ref_box[1] + ref_box[3])
            px = 0.5 * (prev_box[0] + prev_box[2])
            py = 0.5 * (prev_box[1] + prev_box[3])
            dist = hypot(cx - px, cy - py)
        st['speed_buf'].append(dist)
        avg_speed = sum(st['speed_buf']) / max(len(st['speed_buf']), 1)
        speed_thresh = self.stationary_speed_thresh
        if vehicle_box is None and plate_box is not None:
            speed_thresh *= 1.5
        if avg_speed <= speed_thresh:
            st['stationary_frames'] = min(st['stationary_frames'] + 1, 100000)
        else:
            st['stationary_frames'] = max(st['stationary_frames'] - 1, 0)

        if anchor_point is None:
            anchor_point = get_anchor_point(vehicle_box or plate_box or st.get('last_vehicle_box') or st.get('last_plate_box'),
                                            self.anchor_offset_ratio)
        zone_flags = {'enter_a': False, 'exit_a': False, 'enter_b': False, 'exit_b': False}
        zone_state = st.get('zone_state')
        if anchor_point:
            st['last_anchor'] = anchor_point
        zone_state, zone_flags = self.zone_mgr.update_track(track_id, anchor_point, frame_idx)
        st['zone_state'] = zone_state

        timestamp = self.frame_timestamp(frame_idx)
        inside_a = bool(zone_state and zone_state.inside_a)
        inside_b = bool(zone_state and zone_state.inside_b)
        if zone_flags.get('enter_a'):
            st['zone_a_enter_frame'] = frame_idx
            st['zone_a_dwell_frames'] = 0
        enter_a_frame = st.get('zone_a_enter_frame', -1)
        zone_a_elapsed = 0
        if inside_a:
            if enter_a_frame < 0:
                st['zone_a_enter_frame'] = frame_idx
                enter_a_frame = frame_idx
            zone_a_elapsed = frame_idx - enter_a_frame
            st['zone_a_dwell_frames'] = zone_a_elapsed
        else:
            if enter_a_frame >= 0:
                zone_a_elapsed = frame_idx - enter_a_frame
                st['zone_a_dwell_frames'] = zone_a_elapsed
            st['zone_a_enter_frame'] = -1
        if zone_flags.get('enter_b'):
            st['zone_b_enter_frame'] = frame_idx
            st['zone_b_dwell_frames'] = 0
            st['water_detected'] = False
            st['wash_duration'] = 0.0
            st['water_hit_frames'] = 0
            win = st.get('water_window')
            if isinstance(win, deque):
                win.clear()
            else:
                win = deque(maxlen=self.water_window_size)
            st['water_window'] = win
            st['effective_wash_frames'] = 0
        enter_frame = st.get('zone_b_enter_frame', -1)
        anchor_elapsed = 0
        if inside_b:
            if enter_frame < 0:
                st['zone_b_enter_frame'] = frame_idx
                enter_frame = frame_idx
            anchor_elapsed = frame_idx - enter_frame
            st['zone_b_dwell_frames'] = anchor_elapsed
        else:
            if enter_frame >= 0:
                anchor_elapsed = frame_idx - enter_frame
                st['zone_b_dwell_frames'] = anchor_elapsed
            st['zone_b_enter_frame'] = -1
        meets_anchor_delay = (not inside_b) or (anchor_elapsed >= self.zone_b_anchor_min_frames)
        stable_inside_b = bool(inside_b and meets_anchor_delay)
        event_enabled = bool(
            st.get('zone_a_dwell_frames', 0) > 0
            or inside_a
            or zone_flags.get('enter_a')
            or zone_flags.get('exit_a')
        )
        st['washing_candidate'] = stable_inside_b
        type2_ready = bool(event_enabled and zone_flags.get('enter_b'))
        if type2_ready:
            st['type2_qualified'] = True
            if st.get('type2_qualified_frame', -1) < 0:
                st['type2_qualified_frame'] = frame_idx

        water_in_b = bool(st.get('type2_qualified') and water_boxes)
        if water_in_b:
            st['water_detected'] = True
            st['water_hit_frames'] = st.get('water_hit_frames', 0) + 1
            st['effective_wash_frames'] = st.get('effective_wash_frames', 0) + 1
        washing_now = bool(water_in_b)

        if self.disable_plate_only_events and is_plate and (vehicle_box is None and st.get('last_vehicle_box') is None):
            return
        can_type1 = True
        if self.min_type1_track_frames > 0:
            if st.get('zone_a_dwell_frames', 0) < self.min_type1_track_frames:
                can_type1 = False
        if bool(zone_state and zone_state.inside_a) and 1 not in st['events'] and 1 in self.allowed_events and can_type1:
            self.emit_event(track_id, 1, frame_idx, frame, {'captureTime': timestamp}, st)
            st['events'].add(1)
        if type2_ready and 2 not in st['events'] and 2 in self.allowed_events:
            if 1 in self.allowed_events and 1 not in st['events']:
                backfill_type1 = True
                if self.min_type1_track_frames > 0:
                    if st.get('zone_a_dwell_frames', 0) < self.min_type1_track_frames:
                        backfill_type1 = False
                if backfill_type1:
                    self.emit_event(track_id, 1, frame_idx, frame, {'captureTime': timestamp}, st)
                    st['events'].add(1)
            self.emit_event(track_id, 2, frame_idx, frame, {'captureTime': timestamp}, st)
            st['events'].add(2)
        if water_in_b and 3 not in st['events'] and 3 in self.allowed_events:
            st['washing_confirmed'] = True
            st['wash_start_time'] = timestamp
            self.emit_event(track_id, 3, frame_idx, frame, {
                'captureTime': timestamp,
                'washStartTime': timestamp,
            }, st)
            st['events'].add(3)
        st['wash_duration'] = self._compute_effective_wash_duration(st, frame_idx)
        can_type4 = False
        if zone_flags.get('exit_b'):
            if st.get('zone_b_dwell_frames', 0) >= self.min_type4_zone_b_dwell:
                can_type4 = True
        if event_enabled and st.get('type2_qualified') and can_type4 and 4 not in st['events'] and 4 in self.allowed_events:
            st['wash_end_time'] = st.get('wash_end_time') or timestamp
            duration_val = self._compute_effective_wash_duration(st, frame_idx)
            st['wash_duration'] = duration_val
            self.emit_event(track_id, 4, frame_idx, frame, {
                'captureTime': timestamp,
                'washDuration': round(duration_val, 2),
            }, st)
            st['events'].add(4)
        can_type5 = self._can_emit_type5(st)
        if zone_flags.get('exit_a') and 5 in self.allowed_events and 5 not in st['events'] and can_type5:
            st['wash_end_time'] = st.get('wash_end_time') or timestamp
            duration_val = self._compute_effective_wash_duration(st, frame_idx)
            st['wash_duration'] = duration_val
            self._mark_type5_abnormal_reasons(st)
            self.emit_event(track_id, 5, frame_idx, frame, {
                'captureTime': timestamp,
                'washDuration': round(duration_val, 2),
            }, st)
            st['events'].add(5)
            if self.single_lifecycle_events:
                st['closed'] = True

        st['washing'] = washing_now
        if not st['washing']:
            st['washing_confirmed'] = False
        st['debug'] = {
            'state': 'washing' if st.get('washing') else 'idle',
            'stationary': st['stationary_frames'],
            'speed': round(avg_speed, 1),
            'wash_duration': round(st.get('wash_duration', 0.0), 1),
            'water': bool(washing_now),
            'plate': st.get('plate_text', ''),
            'zone_a': bool(zone_state and zone_state.inside_a),
            'zone_b': bool(zone_state and zone_state.inside_b),
            'zone_a_elapsed': zone_a_elapsed,
            'zone_b_elapsed': anchor_elapsed,
            'water_detected': bool(st.get('water_detected')),
        }

        elapsed_seconds = st.get('track_frame_count', 0) / max(self.fps, 1e-6)
        if elapsed_seconds >= self.max_per_id_video_seconds and not st.get('closed'):
            reasons = st.get('abnormal_reasons')
            if reasons is None:
                reasons = set()
                st['abnormal_reasons'] = reasons
            if 'OVER_10_MINUTES' not in reasons:
                reasons.add('OVER_10_MINUTES')
            if st.get('record_start_frame') is not None and st.get('record_stop_frame') is None:
                extra_frames = self._record_tail_frames()
                last_idx = st.get('last_frame_idx', frame_idx)
                stop_frame = last_idx + extra_frames
                prev_stop = st.get('record_stop_frame')
                if prev_stop is None or stop_frame > prev_stop:
                    st['record_stop_frame'] = stop_frame
            if self.uploader:
                track_key = f'{self.camera_id}_{track_id}'
                buffer = self.upload_buffer.pop(track_key, [])
                if buffer:
                    updated = []
                    for p in buffer:
                        payload = dict(p)
                        payload['isAbnormal'] = True
                        old_reason = str(payload.get('abnormalReason') or '').strip()
                        if old_reason:
                            parts = set(r for r in old_reason.split('|') if r)
                        else:
                            parts = set()
                        parts.add('OVER_10_MINUTES')
                        payload['abnormalReason'] = '|'.join(sorted(parts))
                        updated.append(payload)
                    self.upload_qualified.add(track_key)
                    for payload in updated:
                        sent_now = False
                        try:
                            self.uploader.enqueue(payload)
                            sent_now = True
                        except Exception:
                            sent_now = False
                        if self.upload_log_sent and sent_now:
                            try:
                                text = json.dumps(payload, ensure_ascii=False)
                                with self.upload_log_sent.open('a', encoding='utf-8') as f:
                                    f.write(f"{self.frame_timestamp(st.get('last_frame_idx', frame_idx))},{track_key},0,{text}\n")
                            except Exception:
                                pass
                        if self.upload_log_full:
                            try:
                                text = json.dumps(payload, ensure_ascii=False)
                                with self.upload_log_full.open('a', encoding='utf-8') as f:
                                    f.write(f"{self.frame_timestamp(st.get('last_frame_idx', frame_idx))},{track_key},0,1,{text}\n")
                            except Exception:
                                pass
                else:
                    self.upload_qualified.add(track_key)
            st['closed'] = True

    def flush_inactive(self, active_ids, frame_idx, on_track_timeout=None):
        active_ids = active_ids or set()
        to_remove = []
        for tid, st in self.tracks.items():
            if tid in active_ids:
                continue
            if frame_idx - st.get('last_frame_idx', frame_idx) >= self.timeout_frames:
                event_enabled = bool(st.get('zone_a_dwell_frames', 0) > 0)
                if (
                    4 not in st['events']
                    and 4 in self.allowed_events
                    and event_enabled
                    and st.get('type2_qualified')
                    and st.get('zone_b_dwell_frames', 0) > 0
                ):
                    last_frame = st.get('last_frame_idx', frame_idx)
                    st['wash_end_time'] = st.get('wash_end_time') or self.frame_timestamp(last_frame)
                    duration_val = self._compute_effective_wash_duration(st, last_frame)
                    st['wash_duration'] = duration_val
                    self.emit_event(tid, 4, last_frame, st.get('last_frame'), {
                        'captureTime': self.frame_timestamp(last_frame),
                        'washDuration': round(duration_val, 2),
                    }, st)
                    st['events'].add(4)
                can_type5 = self._can_emit_type5(st)
                if 5 not in st['events'] and 5 in self.allowed_events and can_type5 and event_enabled:
                    timestamp = self.frame_timestamp(st.get('last_frame_idx', frame_idx))
                    st['wash_end_time'] = st.get('wash_end_time') or timestamp
                    duration_val = self._compute_effective_wash_duration(st, st.get('last_frame_idx', frame_idx))
                    st['wash_duration'] = duration_val
                    extras = {
                        'captureTime': timestamp,
                        'washDuration': round(duration_val, 2),
                    }
                    self._mark_type5_abnormal_reasons(st)
                    self.emit_event(tid, 5, st.get('last_frame_idx', frame_idx), st.get('last_frame'), extras, st)
                    st['events'].add(5)
                if st.get('record_start_frame') is not None and st.get('record_stop_frame') is None:
                    extra_frames = self._record_tail_frames()
                    last_idx = st.get('last_frame_idx', frame_idx)
                    st['record_stop_frame'] = last_idx + extra_frames
                if self.single_lifecycle_events and 5 in st['events']:
                    st['closed'] = True
                if on_track_timeout is not None:
                    try:
                        on_track_timeout(tid, st)
                    except Exception:
                        pass
                to_remove.append(tid)
        for tid in to_remove:
            self.shadow_pool.pop(tid, None)
            self.zone_mgr.drop_track(tid)
            self.pending_events.pop(tid, None)
            key = f'{self.camera_id}_{tid}'
            self.upload_buffer.pop(key, None)
            self.tracks.pop(tid, None)

    def emit_event(self, track_id, event_type, frame_idx, frame, payload, track_state):
        if event_type not in self.allowed_events:
            return
        vehicle_type = payload.get('vehicleType') or self._resolve_vehicle_type(track_state)
        if self.require_vehicle_type_for_events and event_type in (3, 4, 5):
            if not (vehicle_type and str(vehicle_type).strip()):
                pending = self.pending_events.setdefault(track_id, [])
                pending.append({
                    'event_type': event_type,
                    'frame_idx': frame_idx,
                    'frame': frame.copy() if frame is not None else None,
                    'payload': dict(payload),
                })
                return
        if self.require_vehicle_type_for_events and vehicle_type and str(vehicle_type).strip():
            self._flush_pending_events(track_id, vehicle_type, track_state)
        self._emit_event_core(track_id, event_type, frame_idx, frame, payload, track_state, vehicle_type)

    def _emit_event_core(self, track_id, event_type, frame_idx, frame, payload, track_state, vehicle_type):
        anchor_dwell = 0
        dbg = track_state.get('debug', {})
        if dbg:
            anchor_dwell = int(dbg.get('zone_b_elapsed', 0) or 0)
        capture_time = payload.get('captureTime') or self.frame_timestamp(frame_idx)
        try:
            track_state['last_event_capture_time'] = capture_time
        except Exception:
            pass
        if event_type == 1:
            prev_type1_time = track_state.get('type1_capture_time')
            if not prev_type1_time:
                track_state['type1_capture_time'] = capture_time
                try:
                    dt = datetime.strptime(capture_time, "%Y-%m-%d %H:%M:%S")
                except Exception:
                    dt = datetime.now()
                ts_str = dt.strftime("%Y%m%d%H%M")
                device_name = self.config.get('system', {}).get('device_id') or self.camera_id
                session_id = f"{device_name}-{ts_str}-{track_id}"
                track_state['session_id'] = session_id
        session_id = track_state.get('session_id')
        if not session_id:
            base_time = track_state.get('type1_capture_time') or capture_time
            try:
                dt = datetime.strptime(base_time, "%Y-%m-%d %H:%M:%S")
            except Exception:
                dt = datetime.now()
            ts_str = dt.strftime("%Y%m%d%H%M")
            device_name = self.config.get('system', {}).get('device_id') or self.camera_id
            session_id = f"{device_name}-{ts_str}-{track_id}"
            track_state['session_id'] = session_id
        if event_type == 1:
            prev_start = track_state.get('record_start_frame')
            if prev_start is None or frame_idx < prev_start:
                track_state['record_start_frame'] = frame_idx
        if track_state.get('record_start_frame') is None:
            track_state['record_start_frame'] = frame_idx
        if event_type == 5:
            extra_frames = self._record_tail_frames()
            stop_frame = frame_idx + extra_frames
            prev_stop = track_state.get('record_stop_frame')
            if prev_stop is None or stop_frame > prev_stop:
                track_state['record_stop_frame'] = stop_frame
        try:
            track_state[f'last_event_t{event_type}_capture_time'] = capture_time
        except Exception:
            pass
        capture_path = self._save_event_capture(event_type, track_id, frame_idx, frame)
        event = {
            'id': session_id,
            'trackId': track_id,
            'type': event_type,
            'captureTime': capture_time,
            'plateNumber': payload.get('plateNumber') or '',
            'vehicleType': vehicle_type,
            'washDuration': payload.get('washDuration', 0.0),
            'washStartTime': payload.get('washStartTime', ''),
            'captureImage': capture_path,
            'lane': self.lane_name,
            'anchorDwellFrames': anchor_dwell,
        }
        reasons = track_state.get('abnormal_reasons') if track_state else None
        if reasons:
            if isinstance(reasons, set):
                reasons_list = sorted(reasons)
            else:
                reasons_list = sorted(str(x) for x in reasons if x)
            if reasons_list:
                event['isAbnormal'] = True
                event['abnormalReason'] = '|'.join(reasons_list)
        plate_text, is_guess = self._resolve_plate_with_shadow(track_id, track_state, frame_idx)
        if not event['plateNumber']:
            event['plateNumber'] = plate_text
        if not event['plateNumber']:
            event['isAbnormal'] = True
            reason = 'PLATE_MISSING'
            if 'abnormalReason' in event and event['abnormalReason']:
                parts = set(str(x).strip() for x in str(event['abnormalReason']).split('|') if x)
                parts.add(reason)
                event['abnormalReason'] = '|'.join(sorted(parts))
            else:
                event['abnormalReason'] = reason
        event['plateIsGuess'] = bool(is_guess and event['plateNumber'])
        dir_code = 0
        dir_label = ''
        if event_type == 5:
            dir_code, dir_label = self._resolve_direction(track_state)
            event['direction'] = dir_code
            event['directionLabel'] = dir_label
        plate_color, plate_color_conf = self._infer_plate_color(track_state)
        event['plateColor'] = plate_color
        event['plateColorConfidence'] = plate_color_conf
        if event_type == 5:
            event['washEndTime'] = track_state.get('wash_end_time') or event['captureTime']
            event['videoEndTime'] = self.frame_timestamp(track_state.get('last_frame_idx', frame_idx))
            event['totalWashDuration'] = round(track_state.get('wash_duration', 0.0), 2)
            video_duration = 0.0
            type1_time = track_state.get('type1_capture_time')
            if type1_time:
                try:
                    dt1 = datetime.strptime(type1_time, "%Y-%m-%d %H:%M:%S")
                    dt5 = datetime.strptime(event['captureTime'], "%Y-%m-%d %H:%M:%S")
                    delta = (dt5 - dt1).total_seconds()
                    if delta > 0:
                        video_duration = round(delta, 2)
                except Exception:
                    video_duration = 0.0
            event['videoDuration'] = video_duration
            event['cleanliness'] = self.default_cleanliness
        if track_state.get('wash_start_time') and not event.get('washStartTime'):
            event['washStartTime'] = track_state.get('wash_start_time')
        capture_ts_val = None
        infer_ts_val = None
        if hasattr(self, 'frame_timing'):
            t = self.frame_timing.get(int(frame_idx))
            if t:
                capture_ts_val, infer_ts_val = t
        if capture_ts_val is not None and infer_ts_val is not None:
            now_ts = time.time()
            decode_to_infer = max(0.0, infer_ts_val - capture_ts_val)
            infer_to_event = max(0.0, now_ts - infer_ts_val)
            total_latency = max(0.0, now_ts - capture_ts_val)
            try:
                cap_str = datetime.fromtimestamp(capture_ts_val).strftime("%H:%M:%S.%f")[:-3]
                infer_str = datetime.fromtimestamp(infer_ts_val).strftime("%H:%M:%S.%f")[:-3]
                event_str = datetime.fromtimestamp(now_ts).strftime("%H:%M:%S.%f")[:-3]
                print(f"[latency] 帧={frame_idx} 轨迹={track_id} 类型={event_type} 捕获={cap_str} 推理完成={infer_str} 告警发送={event_str} 解码→推理={decode_to_infer*1000:.1f}ms 推理→告警={infer_to_event*1000:.1f}ms 总时延={total_latency*1000:.1f}ms")
            except Exception:
                pass
        event_path = self.events_dir / f'{self.camera_id}_{track_id}_t{event_type}_{frame_idx}.json'
        try:
            with event_path.open('w', encoding='utf-8') as f:
                json.dump(event, f, ensure_ascii=False, indent=2)
        except Exception:
            pass
        print(f"[EVENT] cam={self.camera_id} track={track_id} type={event_type} time={event['captureTime']}")
        if self.event_log_path:
            try:
                with self.event_log_path.open('a', encoding='utf-8') as f:
                    f.write(f"{self.camera_id},{track_id},{event_type},{event['captureTime']},{frame_idx},"
                            f"{track_state.get('stationary_frames',0)},{round(track_state.get('wash_duration',0.0),2)},"
                            f"{event['plateNumber']},{event['vehicleType']},{dir_code},{dir_label},"
                            f"{int(event['plateIsGuess'])},{anchor_dwell}\n")
            except Exception:
                pass
        if self.uploader:
            api_payload = self._build_api_payload(event, track_state, frame_idx)
            if api_payload:
                track_key = event['id']
                sent_now = False
                is_abnormal = bool(api_payload.get('isAbnormal'))
                if event_type == 1:
                    buffer = self.upload_buffer.setdefault(track_key, [])
                    buffer.append(api_payload)
                elif event_type == 2:
                    buffer = self.upload_buffer.pop(track_key, [])
                    buffer.append(api_payload)
                    self.upload_qualified.add(track_key)
                    for p in buffer:
                        try:
                            self.uploader.enqueue(p)
                        except Exception:
                            continue
                        sent_now = True
                        if self.upload_log_sent:
                            try:
                                text = json.dumps(p, ensure_ascii=False)
                                with self.upload_log_sent.open('a', encoding='utf-8') as f:
                                    f.write(f"{event['captureTime']},{track_key},{event_type},{text}\n")
                            except Exception:
                                pass
                elif track_key in self.upload_qualified:
                    try:
                        self.uploader.enqueue(api_payload)
                        sent_now = True
                        if self.upload_log_sent:
                            try:
                                text = json.dumps(api_payload, ensure_ascii=False)
                                with self.upload_log_sent.open('a', encoding='utf-8') as f:
                                    f.write(f"{event['captureTime']},{track_key},{event_type},{text}\n")
                            except Exception:
                                pass
                    except Exception:
                        sent_now = False
                else:
                    buffer = self.upload_buffer.setdefault(track_key, [])
                    buffer.append(api_payload)
                if self.upload_log_full:
                    try:
                        text = json.dumps(api_payload, ensure_ascii=False)
                        with self.upload_log_full.open('a', encoding='utf-8') as f:
                            f.write(f"{event['captureTime']},{track_key},{event_type},{int(sent_now)},{text}\n")
                    except Exception:
                        pass
        if event_type == 3:
            track_state['last_type3_frame'] = frame_idx
        elif event_type == 4:
            track_state['last_type4_frame'] = frame_idx

    def _flush_pending_events(self, track_id, vehicle_type, track_state):
        entries = self.pending_events.pop(track_id, None)
        if not entries:
            return
        for entry in entries:
            et = entry.get('event_type')
            fi = entry.get('frame_idx')
            fr = entry.get('frame')
            payload = dict(entry.get('payload') or {})
            if not payload.get('vehicleType'):
                payload['vehicleType'] = vehicle_type
            self._emit_event_core(track_id, et, fi, fr, payload, track_state, vehicle_type)

    def _add_shadow_candidate(self, track_id, text, conf, frame_idx):
        text = normalize_plate_text(text)
        if not text:
            return
        pool = self.shadow_pool.setdefault(track_id, deque())
        pool.append({
            'text': text,
            'conf': float(conf) if conf is not None else 0.5,
            'frame': frame_idx,
        })
        while len(pool) > self.shadow_max:
            pool.popleft()
        while pool and frame_idx - pool[0]['frame'] > self.shadow_max_age:
            pool.popleft()

    def _resolve_plate_with_shadow(self, track_id, track_state, frame_idx):
        text = track_state.get('plate_text', '')
        if text:
            return text, bool(track_state.get('plate_is_guess', False))
        pool = self.shadow_pool.get(track_id)
        if not pool:
            return '', False
        total = len(pool)
        best_text = ''
        best_score = 0.0
        unique = set(entry['text'] for entry in pool if entry['text'])
        for candidate in unique:
            conf_sum = 0.0
            count = 0
            for entry in pool:
                if entry['text'] != candidate:
                    continue
                decay = max(0.2, 1.0 - (frame_idx - entry['frame']) / max(self.shadow_max_age, 1))
                conf_sum += (entry['conf'] or 0.5) * decay
                count += 1
            if count == 0:
                continue
            score = (conf_sum / count) * (count / total)
            if score > best_score:
                best_score = score
                best_text = candidate
        return best_text, bool(best_text)

    def _compute_effective_wash_duration(self, track_state, frame_idx):
        frames = track_state.get('effective_wash_frames', 0)
        if frames <= 0:
            return 0.0
        seconds = frames / max(self.fps, 1e-6)
        return max(0.0, seconds)

    def _record_tail_frames(self):
        return int(max(self.fps, 1.0) * self.per_id_video_tail_seconds)

    def _can_emit_type5(self, track_state):
        if not track_state.get('type2_qualified'):
            return False
        if self.min_type5_zone_a_dwell > 0:
            if track_state.get('zone_a_dwell_frames', 0) < self.min_type5_zone_a_dwell:
                return False
        return True

    def _mark_type5_abnormal_reasons(self, track_state):
        reasons = track_state.get('abnormal_reasons')
        if reasons is None:
            reasons = set()
            track_state['abnormal_reasons'] = reasons
        if 2 not in track_state.get('events', set()):
            reasons.add('MISSING_TYPE2')
        if track_state.get('water_detected') and 3 not in track_state.get('events', set()):
            reasons.add('MISSING_TYPE3')
        if track_state.get('type2_qualified') and 4 not in track_state.get('events', set()):
            reasons.add('MISSING_TYPE4')
        return reasons

    def _avg(self, values):
        if not values:
            return 0.0
        return float(sum(values) / len(values))

    def _non_yellow_override_window_frames(self):
        return max(1, int(round(max(self.fps, 1.0) * NON_YELLOW_OVERRIDE_SECONDS)))

    def _get_non_yellow_vehicle_override(self, track_state, vehicle_label, vehicle_conf, frame_idx):
        recent = track_state.get('vehicle_non_yellow_recent')
        if not isinstance(recent, deque):
            recent = deque()
            track_state['vehicle_non_yellow_recent'] = recent

        current_locked = track_state.get('vehicle_cls_locked') or track_state.get('vehicle_cls') or ''
        if current_locked != YELLOW_TRUCK_LABEL:
            recent.clear()
            track_state['vehicle_high_conf_label'] = ''
            track_state['vehicle_high_conf_count'] = 0
            return ''

        window_frames = self._non_yellow_override_window_frames()
        min_frame = frame_idx - window_frames + 1
        while recent and recent[0].get('frame', -1) < min_frame:
            recent.popleft()

        label = (vehicle_label or '').strip()
        try:
            conf_val = float(vehicle_conf if vehicle_conf is not None else 0.0)
        except (TypeError, ValueError):
            conf_val = 0.0

        if label in NON_YELLOW_OVERRIDE_LABELS:
            recent.append({'frame': frame_idx, 'label': label})
            if conf_val >= NON_YELLOW_HIGH_CONFIDENCE:
                if track_state.get('vehicle_high_conf_label') == label:
                    track_state['vehicle_high_conf_count'] = int(track_state.get('vehicle_high_conf_count', 0)) + 1
                else:
                    track_state['vehicle_high_conf_label'] = label
                    track_state['vehicle_high_conf_count'] = 1
            else:
                track_state['vehicle_high_conf_label'] = ''
                track_state['vehicle_high_conf_count'] = 0
        else:
            track_state['vehicle_high_conf_label'] = ''
            track_state['vehicle_high_conf_count'] = 0

        streak_label = track_state.get('vehicle_high_conf_label', '')
        streak_count = int(track_state.get('vehicle_high_conf_count', 0) or 0)
        if streak_label in NON_YELLOW_OVERRIDE_LABELS and streak_count >= NON_YELLOW_HIGH_CONF_STREAK:
            return streak_label

        label_counts = {}
        for entry in recent:
            entry_label = entry.get('label', '')
            if entry_label in NON_YELLOW_OVERRIDE_LABELS:
                label_counts[entry_label] = label_counts.get(entry_label, 0) + 1
        if not label_counts:
            return ''
        best_label, best_count = max(label_counts.items(), key=lambda kv: (kv[1], kv[0]))
        if best_count >= window_frames:
            return best_label
        return ''

    def _apply_non_yellow_vehicle_override(self, track_state, counts, override_label):
        counts = dict(counts or {})
        yellow_count = int(counts.get(YELLOW_TRUCK_LABEL, 0) or 0)
        counts[override_label] = max(int(counts.get(override_label, 0) or 0), yellow_count + 1, self.vehicle_lock_min_votes)
        track_state['class_counts'] = counts
        track_state['vehicle_cls_locked'] = override_label
        track_state['vehicle_cls'] = override_label
        track_state['vehicle_cls_frozen'] = True
        track_state['vehicle_high_conf_label'] = ''
        track_state['vehicle_high_conf_count'] = 0
        recent = track_state.get('vehicle_non_yellow_recent')
        if isinstance(recent, deque):
            recent.clear()
        return counts

    def _infer_plate_color(self, track_state):
        tracked_color = (track_state.get('plate_color') or '').strip()
        if tracked_color:
            try:
                tracked_conf = float(track_state.get('plate_color_conf', 0.0) or 0.0)
            except (TypeError, ValueError):
                tracked_conf = 0.0
            return tracked_color, tracked_conf
        vehicle = (track_state.get('vehicle_cls') or '').lower()
        plate = track_state.get('plate_text', '') or ''
        length = len(plate)
        if vehicle == 'car':
            if length == 8:
                return '绿色', 0.9
            return '蓝色', 0.9
        if vehicle == 'blue truck':
            return '蓝色', 0.9
        if vehicle in ('yellow truck', 'dump truck'):
            return '黄色', 0.9
        if vehicle == 'wuxiao':
            return '蓝色', 0.9
        if self.default_plate_color:
            return self.default_plate_color, self.default_plate_color_conf
        return '', 0.0

    def _resolve_vehicle_type(self, track_state, fallback=''):
        if not track_state:
            return fallback or ''
        locked = track_state.get('vehicle_cls_locked', '')
        if locked:
            return locked
        current = track_state.get('vehicle_cls', '')
        if current:
            return current
        counts = track_state.get('class_counts') or {}
        if counts:
            locked = max(counts.items(), key=lambda kv: (kv[1], kv[0]))[0]
            if locked:
                return locked
        return fallback or track_state.get('last_vehicle_label', '') or ''

    def get_locked_vehicle(self, track_id):
        st = self.tracks.get(track_id)
        if not st:
            return ''
        return st.get('vehicle_cls_locked') or st.get('vehicle_cls', '')

    def get_track_debug(self, track_id):
        st = self.tracks.get(track_id)
        if not st:
            return None
        dbg = st.get('debug', {})
        return {
            'state': dbg.get('state', 'idle'),
            'stationary': dbg.get('stationary', 0),
            'speed': dbg.get('speed', 0.0),
            'water': dbg.get('water', False),
            'wash_duration': dbg.get('wash_duration', 0.0),
            'plate': dbg.get('plate', ''),
            'zone_a': dbg.get('zone_a', False),
            'zone_b': dbg.get('zone_b', False),
        }

    def _resolve_vehicle_type(self, track_state, fallback=''):
        if not track_state:
            return fallback or ''
        locked = track_state.get('vehicle_cls_locked', '')
        if locked:
            return locked
        current = track_state.get('vehicle_cls', '')
        if current:
            return current
        counts = track_state.get('class_counts') or {}
        if counts:
            locked = max(counts.items(), key=lambda kv: (kv[1], kv[0]))[0]
            if locked:
                return locked
        return fallback or track_state.get('last_vehicle_label', '') or ''

    def _prepare_capture_image(self, capture_path):
        if not capture_path:
            return ''
        capture_file = Path(capture_path)
        if not capture_file.exists():
            return ''
        if self.capture_mode == 'base64':
            try:
                with capture_file.open('rb') as f:
                    return base64.b64encode(f.read()).decode('utf-8')
            except Exception:
                return ''
        return str(capture_file)

    def _log_capture_failure(self, event_type, track_id, frame_idx, capture_path, frame,
                             imwrite_ret=None, error=''):
        frame_shape = ''
        if frame is not None:
            try:
                h, w = frame.shape[:2]
                frame_shape = f'{w}x{h}'
            except Exception:
                frame_shape = 'unavailable'
        print(
            '[event-capture] failed '
            f'type={event_type} track={track_id} frame={frame_idx} '
            f'path={capture_path or ""} frame_none={frame is None} '
            f'frame_shape={frame_shape or "none"} imwrite={imwrite_ret} error={error or ""}'
        )

    def _save_event_capture(self, event_type, track_id, frame_idx, frame):
        if frame is None:
            self._log_capture_failure(event_type, track_id, frame_idx, '', frame, error='frame is None')
            return ''
        capture_file = self.capture_dir / f'{self.camera_id}_{track_id}_t{event_type}_{frame_idx}.jpg'
        capture_path = str(capture_file)
        try:
            h, w = frame.shape[:2]
            target_w, target_h = 1920, 1080
            if w != target_w or h != target_h:
                frame_to_save = resize_bgr(frame, (target_w, target_h))
            else:
                frame_to_save = frame
            params = [int(cv2.IMWRITE_JPEG_QUALITY), 85]
            imwrite_ret = bool(cv2.imwrite(capture_path, frame_to_save, params))
            if not imwrite_ret:
                try:
                    capture_file.unlink(missing_ok=True)
                except Exception:
                    pass
                self._log_capture_failure(event_type, track_id, frame_idx, capture_path, frame, imwrite_ret=False)
                return ''
            if not capture_file.exists() or capture_file.stat().st_size <= 0:
                try:
                    capture_file.unlink(missing_ok=True)
                except Exception:
                    pass
                self._log_capture_failure(
                    event_type,
                    track_id,
                    frame_idx,
                    capture_path,
                    frame,
                    imwrite_ret=True,
                    error='file missing after write',
                )
                return ''
            return capture_path
        except Exception as exc:
            try:
                capture_file.unlink(missing_ok=True)
            except Exception:
                pass
            self._log_capture_failure(
                event_type,
                track_id,
                frame_idx,
                capture_path,
                frame,
                error=str(exc),
            )
            return ''

    def _build_api_payload(self, event, track_state, frame_idx):
        if not self.uploader:
            return None
        evt_type = event['type']
        if evt_type == 6:
            return {
                'id': event['id'],
                'type': evt_type,
                'lane': self.lane_name,
            }
        plate_conf = round(self._avg(track_state.get('plate_conf_history')), 3)
        vehicle_conf = round(self._avg(track_state.get('vehicle_conf_history')), 3)
        raw_vehicle_type = event.get('vehicleType') or self._resolve_vehicle_type(track_state)
        vehicle_type_cn = VEHICLE_LABEL_CN.get(raw_vehicle_type, raw_vehicle_type or '')
        capture_time = event['captureTime']
        capture_image = self._prepare_capture_image(event.get('captureImage'))
        lane = self.lane_name
        plate_number = event.get('plateNumber', '')
        plate_color = event.get('plateColor', self.default_plate_color)
        plate_color_conf = event.get('plateColorConfidence', self.default_plate_color_conf)
        plate_is_guess = event.get('plateIsGuess', False)
        reasons = track_state.get('abnormal_reasons') if track_state else None
        wash_start_time = track_state.get('wash_start_time') if track_state else None
        dir_code = 0
        dir_label = ''
        wash_end_time = None
        video_end_time = None
        total_wash_duration = None
        cleanliness = None
        video_duration = None
        if evt_type == 5:
            dir_code, dir_label = self._resolve_direction(track_state)
            wash_end_time = track_state.get('wash_end_time') or capture_time
            video_end_time = self.frame_timestamp(track_state.get('last_frame_idx', frame_idx))
            total_wash_duration = round(track_state.get('wash_duration', 0.0), 2)
            cleanliness = self.default_cleanliness
            video_duration = event.get('videoDuration')
        payload = {}
        payload['id'] = event['id']
        payload['type'] = evt_type
        payload['captureTime'] = capture_time
        payload['captureImage'] = capture_image
        if evt_type == 1:
            payload['lane'] = lane
            payload['plateNumber'] = plate_number
            payload['plateConfidence'] = plate_conf
            payload['plateColor'] = plate_color
            payload['plateColorConfidence'] = plate_color_conf
            payload['vehicleType'] = vehicle_type_cn
            payload['vehicleTypeConfidence'] = vehicle_conf
            payload['plateIsGuess'] = plate_is_guess
            if wash_start_time:
                payload['washStartTime'] = wash_start_time
        elif evt_type in (2, 3, 4):
            payload['lane'] = lane
            payload['plateNumber'] = plate_number
            payload['plateConfidence'] = plate_conf
            payload['plateColor'] = plate_color
            payload['plateColorConfidence'] = plate_color_conf
            payload['vehicleType'] = vehicle_type_cn
            payload['vehicleTypeConfidence'] = vehicle_conf
            payload['plateIsGuess'] = plate_is_guess
            if wash_start_time:
                payload['washStartTime'] = wash_start_time
        elif evt_type == 5:
            payload['washEndTime'] = wash_end_time
            payload['videoEndTime'] = video_end_time
            payload['totalWashDuration'] = total_wash_duration
            payload['cleanliness'] = cleanliness
            payload['videoDuration'] = video_duration
            payload['plateNumber'] = plate_number
            payload['plateConfidence'] = plate_conf
            payload['plateColor'] = plate_color
            payload['plateColorConfidence'] = plate_color_conf
            payload['vehicleType'] = vehicle_type_cn
            payload['vehicleTypeConfidence'] = vehicle_conf
            payload['lane'] = lane
            payload['plateIsGuess'] = plate_is_guess
            if wash_start_time:
                payload['washStartTime'] = wash_start_time
            payload['direction'] = dir_code
            payload['directionLabel'] = dir_label
        else:
            payload['lane'] = lane
            payload['plateNumber'] = plate_number
            payload['plateConfidence'] = plate_conf
            payload['plateColor'] = plate_color
            payload['plateColorConfidence'] = plate_color_conf
            payload['vehicleType'] = vehicle_type_cn
            payload['vehicleTypeConfidence'] = vehicle_conf
            payload['plateIsGuess'] = plate_is_guess
            if wash_start_time:
                payload['washStartTime'] = wash_start_time
        if reasons:
            if isinstance(reasons, set):
                reasons_list = sorted(reasons)
            else:
                reasons_list = sorted(str(x) for x in reasons if x)
            if reasons_list:
                payload['isAbnormal'] = True
                payload['abnormalReason'] = '|'.join(reasons_list)
        return payload

    def _resolve_direction(self, track_state):
        state = None
        if track_state:
            state = track_state.get('zone_state')
        return self.zone_mgr.resolve_direction(state)

    def water_contact(self, box, water_boxes):
        if not water_boxes or box is None:
            return False
        for wb in water_boxes:
            if box_iou(box, wb) >= 0.02:
                return True
        return False

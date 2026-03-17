import csv
import os
import threading
import time
from datetime import datetime
from pathlib import Path
from queue import Queue

import cv2
import numpy as np

from zone_manager import ZoneManager, polygon_mask

from .constants import (
    CAR_PLATE_CACHE_TTL,
    CLASS_COLORS,
    CLASS_NAMES,
    LICENSE_CLASS,
    PLATE_CAR_LINK_IOU,
    REPORT_MIN_FRAMES,
    VEHICLE_CLASS_IDS,
    WATER_CLASS_IDS,
    localize_vehicle,
    select_box_color,
)
from .events import EventManager, EventUploader
from .monitoring import monitor_loop
from .plate import PlateTextTracker
from .runtime_config import load_config
from .tracking import VehicleTracker
from .video_io import (
    FfmpegH264Writer,
    _resolve_runtime_path,
    create_video_reader,
    detect_source_mode,
    parse_core_mask,
)
from .vision import box_iou, get_anchor_point, point_in_box, scale_point, scale_polygon
from .worker import DetectWorker

def process_video(path, args):
    cap = create_video_reader(path, args)
    if cap is None or not hasattr(cap, 'isOpened') or not cap.isOpened():
        print(f'failed to open {path}')
        return
    fps = None
    if hasattr(cap, 'get'):
        try:
            fps = cap.get(cv2.CAP_PROP_FPS)
        except AttributeError:
            fps = None
    if not fps:
        fps = getattr(cap, 'fps', None)
    if not fps:
        fps = 25.0
    width = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
    height = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
    config = getattr(args, '_config', load_config(None))
    video_cfg = config.get('video', {})
    logic_cfg = config.get('logic', {})
    system_cfg = config.get('system', {})
    reader_fail_threshold = max(1, int(config.get('reader_fail_threshold', 5)))
    reader_reconnect_delay = max(0.0, float(config.get('reader_reconnect_delay', 2.0)))
    reader_max_reconnect = max(0, int(config.get('reader_max_reconnect', 0)))
    source_mode = detect_source_mode(path, getattr(args, 'source_mode', 'auto'))
    is_file_input = (source_mode == 'file')
    if is_file_input:
        reader_max_reconnect = 0
    else:
        source_mode = 'camera'
    print(f'[reader] source_mode={source_mode}')
    consecutive_fails = 0
    reconnect_count = 0

    base_dir = getattr(args, '_config_dir', Path.cwd())
    metrics_path_conf = system_cfg.get('metrics_path', '/dev/shm/cleaningcar_metrics.json')
    metrics_path = None
    if metrics_path_conf:
        metrics_path = _resolve_runtime_path(metrics_path_conf, base_dir)
    zones_cfg = config.get('zones', {})
    logic_cfg = config.get('logic', {})
    anchor_offset_ratio = float(logic_cfg.get('anchor_offset_ratio', 0.0))
    if anchor_offset_ratio < 0.0:
        anchor_offset_ratio = 0.0
    elif anchor_offset_ratio > 0.95:
        anchor_offset_ratio = 0.95
    zone_b_anchor_min_frames = int(logic_cfg.get('zone_b_anchor_min_frames', 0))
    if zone_b_anchor_min_frames < 0:
        zone_b_anchor_min_frames = 0
    zone_a_pts = scale_polygon(zones_cfg.get('zone_a_detection', []), width, height)
    zone_b_pts = scale_polygon(zones_cfg.get('zone_b_wash', []), width, height)
    flow_vec = zones_cfg.get('flow_vector', {})
    flow_start = scale_point(flow_vec.get('start', (0.0, 0.0)), width, height)
    flow_end = scale_point(flow_vec.get('end', (0.0, 1.0)), width, height)
    zone_mgr = ZoneManager(
        zone_a_pts,
        zone_b_pts,
        (flow_start, flow_end),
        entry_hysteresis=int(logic_cfg.get('zone_b_entry_hysteresis', 3)),
        exit_hysteresis=int(logic_cfg.get('zone_b_exit_hysteresis', 3)),
    )
    detect_mask = None
    if logic_cfg.get('zone_a_mask_enable', True) and len(zone_a_pts) >= 3:
        detect_mask = polygon_mask(zone_a_pts, (height, width))
        print('zone_a_mask: 启用，仅在Zone A内检测')

    def dual_anchor_for(box):
        if not box:
            return None, None
        head = get_anchor_point(box, anchor_offset_ratio)
        if not head:
            return None, None
        fx = flow_end[0] - flow_start[0]
        fy = flow_end[1] - flow_start[1]
        norm = (fx * fx + fy * fy) ** 0.5
        if norm <= 1e-6:
            return head, head
        height = max(1.0, (box[3] - box[1]))
        shift = 0.3 * height
        ux = fx / norm
        uy = fy / norm
        tail = (head[0] - ux * shift, head[1] - uy * shift)
        return head, tail

    def anchor_point_for(box):
        head, tail = dual_anchor_for(box)
        return tail or head

    output_dir = getattr(args, 'output_dir', None)
    if output_dir:
        os.makedirs(output_dir, exist_ok=True)
    video_writer = None
    csv_writer = None
    csv_f = None
    plate_lock_frames = int(getattr(args, 'plate_lock_frames', 2))
    plate_tracker = PlateTextTracker(
        lock_frames=plate_lock_frames,
        max_age=max(int(config.get('track_timeout_frames', 60)) * 2, plate_lock_frames * 6)
    )
    vehicle_iou_thresh = float(config.get('vehicle_iou_threshold', 0.3))
    if vehicle_iou_thresh < 0.0:
        vehicle_iou_thresh = 0.0
    elif vehicle_iou_thresh > 1.0:
        vehicle_iou_thresh = 1.0
    vehicle_center_gate_ratio = float(config.get('vehicle_center_gate_ratio', 0.0) or 0.0)
    if vehicle_center_gate_ratio < 0.0:
        vehicle_center_gate_ratio = 0.0
    vehicle_tracker = VehicleTracker(
        iou_thresh=vehicle_iou_thresh,
        max_age=int(config.get('track_max_age', 60)),
        center_gate_ratio=vehicle_center_gate_ratio,
    )
    default_event_log = os.path.join(config.get('event_output_dir', './events'), 'event_log.csv')
    event_log_arg = getattr(args, 'event_log', None)
    event_log_path = default_event_log if (not event_log_arg or event_log_arg == 'auto') else event_log_arg
    uploader = None
    if getattr(args, 'api_url', None):
        queue_db = Path(config.get('event_output_dir', './events')) / 'upload_queue.db'
        uploader = EventUploader(args.api_url, getattr(args, 'api_token', None), queue_path=queue_db)
    capture_mode = getattr(args, 'capture_mode', 'path')
    event_manager = EventManager(config, fps, (width, height), zone_mgr, event_log_path, uploader=uploader,
                                 capture_mode=capture_mode)
    if args.csv or output_dir:
        csv_path = args.csv
        if csv_path and os.path.isdir(csv_path):
            csv_path = os.path.join(csv_path, Path(path).stem + '.csv')
        if not csv_path and output_dir:
            csv_path = os.path.join(output_dir, Path(path).stem + '.csv')
        csv_dir = os.path.dirname(csv_path)
        if csv_dir:
            os.makedirs(csv_dir, exist_ok=True)
        csv_f = open(csv_path, 'w', newline='', encoding='utf-8')
        csv_writer = csv.writer(csv_f)
        csv_writer.writerow(['frame', 'class', 'score', 'x1', 'y1', 'x2', 'y2', 'track_id', 'text', 'raw_text'])

    debug_overlay_flag = bool(logic_cfg.get('debug_overlay', False))
    debug_tracks_cfg = bool(logic_cfg.get('debug_track_state', False))
    debug_anchor_points = bool(logic_cfg.get('debug_anchor_points', False) or debug_overlay_flag)
    debug_water_boxes = bool(logic_cfg.get('debug_water_boxes', False) or debug_overlay_flag)
    debug_rois = getattr(args, 'debug_rois', False) or debug_overlay_flag
    debug_tracks = getattr(args, 'debug_tracks', False) or debug_tracks_cfg or debug_overlay_flag

    debug_frame_path_conf = str(video_cfg.get('debug_frame_path', '') or '').strip()
    debug_frame_file = None
    if not debug_frame_path_conf:
        shm = Path('/dev/shm')
        if shm.exists() and os.access(shm, os.W_OK):
            debug_frame_path_conf = str(shm / 'cleaningcar_debug.jpg')
    if debug_frame_path_conf:
        debug_frame_file = _resolve_runtime_path(debug_frame_path_conf, base_dir)
        if debug_frame_file.is_dir():
            debug_frame_file = debug_frame_file / 'latest.jpg'
        try:
            debug_frame_file.parent.mkdir(parents=True, exist_ok=True)
        except Exception:
            pass
    debug_frame_interval = max(1, int(video_cfg.get('debug_frame_interval', 30)))

    task_q = Queue(maxsize=args.queue_size)
    result_q = Queue()
    core_mask = parse_core_mask(args.core_mask)
    workers = [DetectWorker(i, args, core_mask, task_q, result_q, detect_mask) for i in range(args.workers)]
    for w in workers:
        w.start()

    monitor_stop = threading.Event()
    monitor_thread = None
    if args.monitor_interval > 0:
        monitor_thread = threading.Thread(target=monitor_loop, args=(args.monitor_interval, monitor_stop), daemon=True)
        monitor_thread.start()

    start = time.time()
    total_frames = 0
    next_frame_to_write = 0
    pending = {}
    finished_workers = 0
    reader_log_interval = float(config.get('reader_fps_log_interval', 10.0))
    reader_log_last_time = start
    reader_log_frames = 0
    worker_last_frames = [0 for _ in workers]
    worker_last_infer = [0.0 for _ in workers]

    car_plate_cache = {}
    car_plate_cache_ttl = int(config.get('car_plate_cache_ttl', CAR_PLATE_CACHE_TTL))
    alias_confirm = {}
    alias_timeout = int(config.get('track_timeout_frames', 60))
    enable_per_id_video = bool(logic_cfg.get('enable_per_id_video', False))
    per_id_video_dir = Path(logic_cfg.get('per_id_video_dir', './video_result/per_id'))
    per_id_video_dir.mkdir(parents=True, exist_ok=True)
    per_id_writers = {}
    per_id_downscale_ratio = float(logic_cfg.get('per_id_downscale_ratio', 1.0) or 1.0)
    if per_id_downscale_ratio <= 0.0:
        per_id_downscale_ratio = 1.0
    per_id_target_width = width
    per_id_target_height = height
    if per_id_downscale_ratio < 0.999:
        per_id_target_width = max(1, int(width * per_id_downscale_ratio))
        per_id_target_height = max(1, int(height * per_id_downscale_ratio))
    target_w = int(logic_cfg.get('per_id_target_width', 1920) or 1920)
    target_h = int(logic_cfg.get('per_id_target_height', 1080) or 1080)
    per_id_target_width = target_w
    per_id_target_height = target_h
    per_id_output_fps = float(logic_cfg.get('per_id_fps', 20.0) or 20.0)
    per_id_frame_stride = int(logic_cfg.get('per_id_frame_stride', 1) or 1)
    if per_id_frame_stride < 1:
        per_id_frame_stride = 1

    def close_per_id_writer(track_id, track_state):
        writer = per_id_writers.pop(track_id, None)
        if writer is None:
            return
        finalized = False
        try:
            finalized = bool(writer.release())
        except Exception:
            finalized = False
        if not finalized:
            return
        if not track_state:
            track_state = {}
        frame_idx = track_state.get('record_stop_frame')
        if frame_idx is None:
            frame_idx = track_state.get('last_frame_idx', 0)
        frame = track_state.get('last_frame')
        try:
            event_manager.emit_event(track_id, 6, frame_idx, frame, {}, track_state)
        except Exception:
            return

    def finalize_per_id_for_track(track_id, track_state):
        close_per_id_writer(track_id, track_state)

    def mark_alias_confirm(alias_id, has_plate_text, frame_idx, require_text):
        if alias_id <= 0:
            return False
        state = alias_confirm.setdefault(alias_id, {'frames': 0, 'confirmed': False, 'last_seen': frame_idx})
        state['frames'] += 1
        state['last_seen'] = frame_idx
        if not state['confirmed']:
            if has_plate_text:
                state['confirmed'] = True
            elif not require_text and state['frames'] >= REPORT_MIN_FRAMES:
                state['confirmed'] = True
        return state['confirmed']

    def cleanup_alias_confirm(frame_idx):
        for aid in list(alias_confirm.keys()):
            state = alias_confirm[aid]
            if (frame_idx - state.get('last_seen', frame_idx)) > alias_timeout or aid not in event_manager.tracks:
                alias_confirm.pop(aid, None)

    def annotate_locked_label(track_id, det_ref, rows_ref, frame_img):
        if det_ref is None or det_ref.get('cls') not in VEHICLE_CLASS_IDS:
            return
        locked = event_manager.get_locked_vehicle(track_id)
        row_idx = det_ref.get('row_idx', -1)
        if row_idx is not None and 0 <= row_idx < len(rows_ref):
            if locked:
                rows_ref[row_idx][1] = locked
        label_now = det_ref.get('label')
        mismatch = bool(locked and label_now and locked != label_now)
        if mismatch:
            return
        if not args.no_draw and frame_img is not None:
            x1, y1, x2, y2 = det_ref['box']
            color = select_box_color(label_now or locked or '')
            cv2.rectangle(frame_img, (x1, y1), (x2, y2), color, 2)
            disp_label = locked or label_now or ''
            if disp_label:
                text_cn = localize_vehicle(disp_label)
                cv2.putText(frame_img, text_cn, (x1, max(0, y1 - 18)),
                            cv2.FONT_HERSHEY_SIMPLEX, 0.75, (255, 255, 255), 2, cv2.LINE_AA)
            if debug_anchor_points:
                anchor_pt = anchor_point_for(det_ref['box'])
                if anchor_pt:
                    ax, ay = int(anchor_pt[0]), int(anchor_pt[1])
                    cv2.circle(frame_img, (ax, ay), 4, (255, 140, 0), -1)
                    cv2.putText(frame_img, f'A{track_id}', (ax + 4, ay - 4),
                                cv2.FONT_HERSHEY_SIMPLEX, 0.45, (255, 255, 255), 1, cv2.LINE_AA)

    def drain_results(block=True):
        nonlocal next_frame_to_write, finished_workers, car_plate_cache, per_id_writers, enable_per_id_video
        try:
            item = result_q.get(block=block, timeout=1 if block else 0)
        except Exception:
            return False
        if item is None:
            finished_workers += 1
        else:
            if len(item) == 4:
                idx, frame_out, rows, det_payload = item
                capture_ts = None
            else:
                idx, capture_ts, frame_out, rows, det_payload = item
            pending[idx] = (frame_out, rows, det_payload, capture_ts)
            while next_frame_to_write in pending:
                frame_out, rows, det_payload, capture_ts = pending.pop(next_frame_to_write)
                if capture_ts is not None:
                    try:
                        event_manager.record_frame_timing(next_frame_to_write, capture_ts, time.time())
                    except Exception:
                        pass
                vehicle_dets = []
                vehicle_payload_refs = []
                car_boxes = {}
                water_boxes = []
                cleaning_label = ''
                if det_payload:
                    for det in det_payload:
                        if det.get('cls') in VEHICLE_CLASS_IDS:
                            vehicle_dets.append({'box': det['box'], 'score': det['score'], 'cls': det['cls'], 'row_idx': det.get('row_idx')})
                            vehicle_payload_refs.append(det)
                        elif det.get('cls') in WATER_CLASS_IDS:
                            water_boxes.append(det['box'])
                            name = CLASS_NAMES[det['cls']]
                            if name == 'manual':
                                cleaning_label = 'manual'
                            elif not cleaning_label:
                                cleaning_label = name
                if debug_water_boxes and not args.no_draw and water_boxes and frame_out is not None:
                    for wb in water_boxes:
                        wx1, wy1, wx2, wy2 = wb
                        cv2.rectangle(frame_out, (wx1, wy1), (wx2, wy2), CLASS_COLORS.get('water', (0, 160, 255)), 2)
                        cv2.putText(frame_out, 'Water', (wx1, max(0, wy1 - 6)),
                                    cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 160, 255), 1, cv2.LINE_AA)
                assignments = vehicle_tracker.update(next_frame_to_write, vehicle_dets) if vehicle_dets else []
                for det_ref, track_id in zip(vehicle_payload_refs, assignments):
                    det_ref['track_id'] = track_id
                    car_boxes[track_id] = det_ref['box']
                    row_idx = det_ref.get('row_idx', -1)
                    if row_idx is not None and 0 <= row_idx < len(rows):
                        rows[row_idx][7] = track_id
                    if track_id > 0 and video_writer:
                        x1, y1, _, _ = det_ref['box']
                        cv2.putText(frame_out, f'CID:{track_id}', (x1, y1 - 5),
                                    cv2.FONT_HERSHEY_SIMPLEX, 0.4, (255, 255, 0), 1, cv2.LINE_AA)
                license_dets = [d for d in det_payload if d.get('cls') == LICENSE_CLASS] if det_payload else []
                if license_dets and car_boxes:
                    for det in license_dets:
                        plate_box = det['box']
                        px1, py1, px2, py2 = plate_box
                        pcenter = ((px1 + px2) * 0.5, (py1 + py2) * 0.5)
                        best_id = None
                        best_score = 0.0
                        for car_id, cbox in car_boxes.items():
                            score = box_iou(plate_box, cbox)
                            if point_in_box(pcenter, cbox):
                                score = max(score, 1.0)
                            if score > best_score:
                                best_score = score
                                best_id = car_id
                        if best_id is not None and (best_score >= PLATE_CAR_LINK_IOU or point_in_box(pcenter, car_boxes[best_id])):
                            det['car_track'] = best_id
                            det['vehicle_box'] = car_boxes[best_id]
                car_to_plate = {}
                plate_to_car = {}
                plate_track_info = {}
                updates = plate_tracker.update(next_frame_to_write, license_dets)
                for det, upd in zip(license_dets, updates):
                    plate_id = upd.get('track_id', -1)
                    text_val = upd.get('text', '')
                    is_guess = bool(upd.get('is_guess', False))
                    row_idx = det.get('row_idx', -1)
                    if row_idx is not None and 0 <= row_idx < len(rows):
                        rows[row_idx][-1] = det.get('text', '')
                        if text_val:
                            rows[row_idx][-2] = text_val
                        track_val = det.get('car_track', -1)
                        rows[row_idx][7] = track_val if track_val > 0 else plate_id
                    if plate_id > 0:
                        plate_track_info[plate_id] = {
                            'box': det['box'],
                            'text': text_val,
                            'is_guess': is_guess,
                            'vehicle_box': det.get('vehicle_box'),
                            'score': float(det.get('score', 0.0)),
                            'plate_color': det.get('plate_color', ''),
                            'plate_color_conf': det.get('plate_color_conf'),
                            'plate_type': det.get('plate_type', ''),
                        }
                        car_id = det.get('car_track', -1)
                        if car_id > 0:
                            car_to_plate[car_id] = plate_id
                            plate_to_car[plate_id] = car_id
                        if car_id > 0 and det.get('vehicle_box') is not None:
                            car_boxes.setdefault(car_id, det['vehicle_box'])
                        if car_id > 0:
                            car_plate_cache[car_id] = {'plate_id': plate_id, 'age': 0}
                        if video_writer and text_val:
                            x1, y1, _, _ = det['box']
                            cv2.putText(frame_out, f'PID:{plate_id} {text_val}', (x1, y1 - 10),
                                        cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 200, 255), 1, cv2.LINE_AA)
                alias_seen = set()
                plates_with_updates = set()
                for plate_id, info in plate_track_info.items():
                    car_id = plate_to_car.get(plate_id, -1)
                    track_key = car_id if car_id > 0 else plate_id
                    vehicle_box = info.get('vehicle_box')
                    if vehicle_box is None:
                        if car_id > 0 and car_id in car_boxes:
                            vehicle_box = car_boxes[car_id]
                        else:
                            for cid, p_id in car_to_plate.items():
                                if p_id == plate_id and cid in car_boxes:
                                    vehicle_box = car_boxes[cid]
                                    break
                    anchor_pt = anchor_point_for(vehicle_box or info['box'])
                    confirmed_alias = mark_alias_confirm(track_key, bool(info.get('text')), next_frame_to_write, True)
                    event_manager.update_track(
                        track_key,
                        info['box'],
                        vehicle_box,
                        info.get('text', ''),
                        next_frame_to_write,
                        frame_out,
                        water_boxes,
                        bool(water_boxes),
                        is_plate=True,
                        vehicle_label=None,
                        vehicle_conf=None,
                        plate_conf=info.get('score'),
                        confirmed=confirmed_alias,
                        cleaning_label=cleaning_label,
                        anchor_point=anchor_pt,
                        plate_is_guess=bool(info.get('is_guess', False)),
                        plate_color=info.get('plate_color', ''),
                        plate_color_conf=info.get('plate_color_conf'),
                        plate_type=info.get('plate_type', ''),
                    )
                    alias_seen.add(track_key)
                    plates_with_updates.add(track_key)
                active_car_ids = set()
                for det_ref in vehicle_payload_refs:
                    car_id = det_ref.get('track_id', -1)
                    if car_id <= 0:
                        continue
                    active_car_ids.add(car_id)
                    alias_plate_id = car_to_plate.get(car_id)
                    if alias_plate_id:
                        row_idx = det_ref.get('row_idx', -1)
                        if row_idx is not None and 0 <= row_idx < len(rows):
                            rows[row_idx][7] = car_id
                        car_plate_cache[car_id] = {'plate_id': alias_plate_id, 'age': 0}
                        if car_id not in plates_with_updates:
                            known_text = ''
                            track_state = event_manager.tracks.get(car_id)
                            if track_state:
                                known_text = track_state.get('plate_text', '')
                            confirmed_alias = mark_alias_confirm(car_id, bool(known_text), next_frame_to_write, True)
                            anchor_pt = anchor_point_for(det_ref['box'])
                            event_manager.update_track(
                                car_id,
                                None,
                                det_ref['box'],
                                '',
                                next_frame_to_write,
                                frame_out,
                                water_boxes,
                                bool(water_boxes),
                                is_plate=True,
                                vehicle_label=det_ref.get('label', ''),
                                vehicle_conf=det_ref.get('score'),
                                plate_conf=None,
                                confirmed=confirmed_alias,
                                cleaning_label=cleaning_label,
                                anchor_point=anchor_pt,
                            )
                        annotate_locked_label(car_id, det_ref, rows, frame_out)
                        alias_seen.add(car_id)
                        continue
                    cache_entry = car_plate_cache.get(car_id)
                    if cache_entry and cache_entry.get('age', 0) <= car_plate_cache_ttl:
                        cache_entry['age'] = cache_entry.get('age', 0) + 1
                        row_idx = det_ref.get('row_idx', -1)
                        if row_idx is not None and 0 <= row_idx < len(rows):
                            rows[row_idx][7] = car_id
                        known_text = ''
                        track_state = event_manager.tracks.get(car_id)
                        if track_state:
                            known_text = track_state.get('plate_text', '')
                        confirmed_alias = mark_alias_confirm(car_id, bool(known_text), next_frame_to_write, True)
                        anchor_pt = anchor_point_for(det_ref['box'])
                        event_manager.update_track(
                            car_id,
                            None,
                            det_ref['box'],
                            '',
                            next_frame_to_write,
                            frame_out,
                            water_boxes,
                            bool(water_boxes),
                            is_plate=True,
                            vehicle_label=det_ref.get('label', ''),
                            vehicle_conf=det_ref.get('score'),
                            plate_conf=None,
                            confirmed=confirmed_alias,
                            cleaning_label=cleaning_label,
                            anchor_point=anchor_pt,
                        )
                        alias_seen.add(car_id)
                        continue
                    fallback_id = car_id
                    row_idx = det_ref.get('row_idx', -1)
                    if row_idx is not None and 0 <= row_idx < len(rows):
                        rows[row_idx][7] = fallback_id
                    confirmed_alias = mark_alias_confirm(fallback_id, False, next_frame_to_write, True)
                    anchor_pt = anchor_point_for(det_ref['box'])
                    event_manager.update_track(
                        fallback_id,
                        None,
                        det_ref['box'],
                        '',
                        next_frame_to_write,
                        frame_out,
                        water_boxes,
                        bool(water_boxes),
                        is_plate=False,
                        vehicle_label=det_ref.get('label', ''),
                        vehicle_conf=det_ref.get('score'),
                        plate_conf=None,
                        confirmed=confirmed_alias,
                        cleaning_label=cleaning_label,
                        anchor_point=anchor_pt,
                    )
                    annotate_locked_label(fallback_id, det_ref, rows, frame_out)
                    alias_seen.add(fallback_id)
                for car_id in list(car_plate_cache.keys()):
                    if car_id in active_car_ids:
                        continue
                    car_plate_cache[car_id]['age'] = car_plate_cache[car_id].get('age', 0) + 1
                    if car_plate_cache[car_id]['age'] > car_plate_cache_ttl:
                        car_plate_cache.pop(car_id, None)
                if debug_tracks:
                    for det_ref in vehicle_payload_refs:
                        car_id = det_ref.get('track_id', -1)
                        if car_id <= 0:
                            continue
                        info = event_manager.get_track_debug(car_id)
                        if not info:
                            continue
                        x1, y1, _, _ = det_ref['box']
                        text = (f"ID:{car_id} {info['state']} sf:{info['stationary']} "
                                f"spd:{info['speed']:.1f} water:{'Y' if info['water'] else 'N'} "
                                f"dur:{info['wash_duration']:.1f} zb:{info.get('zone_b_elapsed',0)}")
                        cv2.putText(frame_out, text, (x1, max(0, y1 - 25)),
                                    cv2.FONT_HERSHEY_SIMPLEX, 0.45, (0, 200, 255), 1, cv2.LINE_AA)
                if debug_rois:
                    if len(zone_a_pts) >= 3:
                        roi_np = np.array(zone_a_pts, dtype=np.int32)
                        cv2.polylines(frame_out, [roi_np], True, (0, 200, 0), 2, cv2.LINE_AA)
                        anchor = tuple(map(int, roi_np[0]))
                        cv2.putText(frame_out, 'Zone A', (anchor[0], max(0, anchor[1] - 8)),
                                    cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 200, 0), 2, cv2.LINE_AA)
                    if len(zone_b_pts) >= 3:
                        roi_np = np.array(zone_b_pts, dtype=np.int32)
                        cv2.polylines(frame_out, [roi_np], True, (0, 0, 255), 2, cv2.LINE_AA)
                        anchor = tuple(map(int, roi_np[0]))
                        cv2.putText(frame_out, 'Zone B', (anchor[0], max(0, anchor[1] - 8)),
                                    cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 0, 255), 2, cv2.LINE_AA)
                    flow_start_int = tuple(map(int, flow_start))
                    flow_end_int = tuple(map(int, flow_end))
                    cv2.arrowedLine(frame_out, flow_start_int, flow_end_int, (255, 0, 0), 2, tipLength=0.08)
                if debug_frame_file and frame_out is not None and (next_frame_to_write % debug_frame_interval == 0):
                    try:
                        ts_now = datetime.now()
                        ts_str = ts_now.strftime("%Y-%m-%d %H:%M:%S")
                        latency_ms = None
                        if capture_ts is not None:
                            try:
                                latency_ms = int((ts_now.timestamp() - float(capture_ts)) * 1000.0)
                            except Exception:
                                latency_ms = None
                        text = ts_str
                        if latency_ms is not None and latency_ms >= 0:
                            text = f"{ts_str} Δ{latency_ms}ms"
                        h_dbg, w_dbg = frame_out.shape[:2]
                        margin = 10
                        base = min(w_dbg, h_dbg)
                        scale = max(0.5, base / 960.0 * 0.7)
                        thick_outline = max(2, int(scale * 3))
                        thick_text = max(1, int(scale * 1.5))
                        cv2.putText(
                            frame_out,
                            text,
                            (margin, h_dbg - margin),
                            cv2.FONT_HERSHEY_SIMPLEX,
                            scale,
                            (0, 0, 0),
                            thick_outline,
                            cv2.LINE_AA,
                        )
                        cv2.putText(
                            frame_out,
                            text,
                            (margin, h_dbg - margin),
                            cv2.FONT_HERSHEY_SIMPLEX,
                            scale,
                            (255, 255, 255),
                            thick_text,
                            cv2.LINE_AA,
                        )
                        cv2.imwrite(str(debug_frame_file), frame_out)
                    except Exception:
                        pass
                if enable_per_id_video and frame_out is not None:
                    for tid, st in event_manager.tracks.items():
                        start_f = st.get('record_start_frame')
                        stop_f = st.get('record_stop_frame')
                        if start_f is None:
                            continue
                        if stop_f is not None and next_frame_to_write > stop_f:
                            close_per_id_writer(tid, st)
                            continue
                        if next_frame_to_write < start_f:
                            continue
                        writer = per_id_writers.get(tid)
                        if writer is None:
                            st_capture_time = st.get('type1_capture_time')
                            if not st_capture_time:
                                for ev_type in (5, 4, 3, 2, 1):
                                    ev_key = f'last_event_t{ev_type}_capture_time'
                                    val = st.get(ev_key)
                                    if val:
                                        st_capture_time = val
                                        break
                            if not st_capture_time:
                                st_capture_time = event_manager.frame_timestamp(start_f)
                            try:
                                dt = datetime.strptime(st_capture_time, "%Y-%m-%d %H:%M:%S")
                            except Exception:
                                dt = datetime.now()
                            session_id = st.get('session_id')
                            if not session_id:
                                ts_str = dt.strftime("%Y%m%d%H%M")
                                device_name = config.get('system', {}).get('device_id') or event_manager.camera_id
                                session_id = f"{device_name}-{ts_str}-{tid}"
                            fname = f"{session_id}.mp4"
                            date_dir = dt.strftime('%Y%m%d')
                            hour_dir = dt.strftime('%H')
                            base_dir = per_id_video_dir / date_dir / hour_dir
                            base_dir.mkdir(parents=True, exist_ok=True)
                            path = base_dir / fname
                            writer_obj = FfmpegH264Writer(str(path), per_id_target_width, per_id_target_height, per_id_output_fps)
                            if writer_obj.is_opened():
                                per_id_writers[tid] = writer_obj
                                writer = writer_obj
                            else:
                                print(f"[per-id-video] H.264 writer init failed, per-id video disabled for this run: {path}")
                                enable_per_id_video = False
                                writer = None
                                break
                        if writer is not None:
                            frame_to_write = frame_out
                            if frame_to_write is not None:
                                h, w = frame_to_write.shape[:2]
                                if w != per_id_target_width or h != per_id_target_height:
                                    frame_to_write = cv2.resize(frame_to_write, (per_id_target_width, per_id_target_height))
                                if next_frame_to_write % per_id_frame_stride == 0:
                                    writer.write(frame_to_write)
                if csv_writer and rows:
                    csv_writer.writerows(rows)
                next_frame_to_write += 1
                event_manager.flush_inactive(alias_seen, next_frame_to_write, finalize_per_id_for_track)
                cleanup_alias_confirm(next_frame_to_write)
            return True

    frame_limit = args.limit if args.limit and args.limit > 0 else None

    while True:
        if frame_limit is not None and total_frames >= frame_limit:
            break
        ret, frame = cap.read()
        if not ret or frame is None:
            consecutive_fails += 1
            if is_file_input:
                print('[reader] local file reached EOF or failed, stopping.')
                break
            if consecutive_fails < reader_fail_threshold:
                time.sleep(0.05)
                continue
            reconnect_count += 1
            consecutive_fails = 0
            print(f'[reader] capture stalled, reconnect attempt #{reconnect_count}')
            try:
                cap.release()
            except Exception:
                pass
            time.sleep(reader_reconnect_delay)
            cap = create_video_reader(path, args)
            if cap and hasattr(cap, 'isOpened') and cap.isOpened():
                continue
            print('[reader] reconnect failed.')
            if reader_max_reconnect and reconnect_count >= reader_max_reconnect:
                print('[reader] max reconnect attempts reached, aborting stream.')
                break
            time.sleep(reader_reconnect_delay)
            continue
        consecutive_fails = 0
        capture_ts = time.time()
        task_q.put((total_frames, frame, capture_ts))
        total_frames += 1
        reader_log_frames += 1
        now = time.time()
        if reader_log_interval > 0 and now - reader_log_last_time >= reader_log_interval:
            elapsed_window = now - reader_log_last_time
            decode_fps_window = reader_log_frames / max(elapsed_window, 1e-6)
            pipeline_frames_window = 0
            worker_msgs = []
            for i, w in enumerate(workers):
                frames_delta = max(0, w.frames - worker_last_frames[i])
                infer_delta = max(0.0, w.infer_time - worker_last_infer[i])
                worker_last_frames[i] = w.frames
                worker_last_infer[i] = w.infer_time
                if frames_delta > 0 and elapsed_window > 0:
                    worker_fps = frames_delta / elapsed_window
                else:
                    worker_fps = 0.0
                if frames_delta > 0 and infer_delta > 0.0:
                    infer_ms = infer_delta * 1000.0 / frames_delta
                else:
                    infer_ms = 0.0
                pipeline_frames_window += frames_delta
                worker_msgs.append(f'w{i}:{worker_fps:.2f}fps/{infer_ms:.1f}ms')
            pipeline_fps_window = pipeline_frames_window / max(elapsed_window, 1e-6) if pipeline_frames_window > 0 else 0.0
            workers_str = ', '.join(worker_msgs)
            print(f'[perf] 解码FPS={decode_fps_window:.2f} 管线FPS={pipeline_fps_window:.2f} 窗口={elapsed_window:.1f}s 总帧数={total_frames} 工人[{workers_str}]')
            reader_log_frames = 0
            reader_log_last_time = now
        while result_q.qsize() > args.queue_size // 2:
            drain_results(block=False)

    for _ in workers:
        task_q.put(None)
    task_q.join()
    while finished_workers < len(workers):
        drain_results(block=True)

    if enable_per_id_video and per_id_writers:
        for tid in list(per_id_writers.keys()):
            track_state = event_manager.tracks.get(tid) or {}
            close_per_id_writer(tid, track_state)

    if video_writer:
        video_writer.release()
    if csv_f:
        csv_f.close()
    cap.release()
    monitor_stop.set()
    if monitor_thread:
        monitor_thread.join(timeout=0.5)
    event_manager.flush_inactive(set(), total_frames + int(config.get('track_timeout_frames', 60)) + 1, finalize_per_id_for_track)
    cleanup_alias_confirm(total_frames + alias_timeout + 1)
    if uploader:
        uploader.close()

    elapsed = time.time() - start
    if total_frames:
        print(f'Video {path}: frames={total_frames} elapsed={elapsed:.2f}s ({total_frames/elapsed:.2f} FPS)')
    agg_frames = sum(w.frames for w in workers)
    agg_infer = sum(w.infer_time for w in workers)
    if agg_frames:
        print(f'Average inference {agg_infer/agg_frames*1000:.2f} ms over {agg_frames} frames')
    for w in workers:
        if w.frames:
            rate = w.frames / elapsed
            print(f'Worker {w.idx}: frames={w.frames} infer_ms={w.infer_time*1000/w.frames:.2f} throughput={rate:.2f} FPS')

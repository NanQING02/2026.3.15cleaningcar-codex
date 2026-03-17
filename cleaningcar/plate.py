from collections import Counter

import cv2
import numpy as np

from .constants import (
    ALNUM,
    CLAHE,
    LPR_BLANK,
    LPR_CHARS,
    PLATE_REGEX,
    PLATE_REGEX_NE,
    PLATE_SIZE,
    PROVINCE_CHARS,
)

def extract_plate_patch(frame, box, expand_ratio):
    h, w = frame.shape[:2]
    x1, y1, x2, y2 = box
    cx = 0.5 * (x1 + x2)
    cy = 0.5 * (y1 + y2)
    bw = (x2 - x1)
    bh = (y2 - y1)
    bw = max(bw, 4)
    bh = max(bh, 4)
    bw *= (1.0 + expand_ratio)
    bh *= (1.0 + expand_ratio * 0.5)
    nx1 = max(0, int(cx - bw / 2))
    ny1 = max(0, int(cy - bh / 2))
    nx2 = min(w - 1, int(cx + bw / 2))
    ny2 = min(h - 1, int(cy + bh / 2))
    if nx2 <= nx1 or ny2 <= ny1:
        return None
    return frame[ny1:ny2, nx1:nx2]


def enhance_plate_patch(patch):
    if patch is None or patch.size == 0:
        return None
    # Rotate if taller than wide (rare but possible after crop)
    if patch.shape[0] > patch.shape[1] * 1.2:
        patch = cv2.rotate(patch, cv2.ROTATE_90_CLOCKWISE)
    patch = cv2.resize(patch, PLATE_SIZE, interpolation=cv2.INTER_LINEAR)
    lab = cv2.cvtColor(patch, cv2.COLOR_BGR2LAB)
    l, a, b = cv2.split(lab)
    l = CLAHE.apply(l)
    lab = cv2.merge((l, a, b))
    patch = cv2.cvtColor(lab, cv2.COLOR_LAB2BGR)
    patch = cv2.convertScaleAbs(patch, alpha=1.15, beta=5)
    return patch


def decode_lpr_output(pred):
    if pred is None:
        return ''
    arr = pred
    if arr.ndim == 3:
        if arr.shape[1] != len(LPR_CHARS):
            arr = np.transpose(arr, (0, 2, 1))
        arr = arr[0]
    elif arr.ndim == 2:
        arr = arr
    else:
        return ''
    prev = LPR_BLANK
    chars = []
    for t in range(arr.shape[1]):
        c = int(np.argmax(arr[:, t]))
        if c == LPR_BLANK:
            prev = c
            continue
        if c != prev:
            chars.append(LPR_CHARS[c])
        prev = c
    return ''.join(chars)


def normalize_plate_text(text):
    if not text:
        return ''
    text = text.upper().replace('·', '').replace('.', '').replace('•', '').replace(' ', '')
    filtered = ''.join(ch for ch in text if ch in ALNUM or ch in PROVINCE_CHARS)
    return filtered


def is_valid_plate(text):
    if not text:
        return False
    if PLATE_REGEX.match(text):
        return True
    if PLATE_REGEX_NE.match(text):
        return True
    return False


class PlateTextTracker:
    def __init__(self, lock_frames=5, iou_thresh=0.4, max_age=30):
        self.lock_frames = max(1, lock_frames)
        self.iou_thresh = iou_thresh
        self.max_age = max_age
        self.tracks = {}
        self.next_id = 1

    def _iou(self, boxA, boxB):
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

    def update(self, frame_idx, detections):
        results = []
        det_boxes = [np.array(det['box'], dtype=float) for det in detections]
        det_texts = [normalize_plate_text(det.get('text', '')) for det in detections]
        track_ids = list(self.tracks.keys())
        iou_matrix = None
        if track_ids and det_boxes:
            iou_matrix = np.zeros((len(track_ids), len(det_boxes)), dtype=np.float32)
            for ti, tid in enumerate(track_ids):
                tbox = self.tracks[tid]['box']
                for di, dbox in enumerate(det_boxes):
                    iou_matrix[ti, di] = self._iou(tbox, dbox)
        assigned_tracks = {}
        assigned_dets = set()
        # direct assignments when detection already has track id
        for det_idx, det in enumerate(detections):
            tid = det.get('track_id', -1)
            if tid and tid > 0:
                assigned_dets.add(det_idx)
                assigned_tracks[tid] = det_idx
                if tid not in self.tracks:
                    self.tracks[tid] = {
                        'box': det_boxes[det_idx],
                        'last_seen': frame_idx,
                        'age': 0,
                        'history': [],
                        'locked': '',
                    }
                    if tid >= self.next_id:
                        self.next_id = tid + 1
                if iou_matrix is not None and tid in track_ids:
                    ti = track_ids.index(tid)
                    iou_matrix[ti, :] = -1
                    iou_matrix[:, det_idx] = -1
        if iou_matrix is not None:
            while True:
                idx = np.unravel_index(np.argmax(iou_matrix), iou_matrix.shape)
                max_iou = iou_matrix[idx]
                if max_iou < self.iou_thresh:
                    break
                ti, di = idx
                tid = track_ids[ti]
                assigned_tracks[tid] = di
                assigned_dets.add(di)
                iou_matrix[ti, :] = -1
                iou_matrix[:, di] = -1
        # update matched tracks
        for tid, det_idx in assigned_tracks.items():
            det_box = det_boxes[det_idx]
            det_text = det_texts[det_idx]
            track = self.tracks[tid]
            track['box'] = det_box
            track['last_seen'] = frame_idx
            track['age'] = 0
            if is_valid_plate(det_text):
                track['history'].append(det_text)
                if len(track['history']) > 30:
                    track['history'].pop(0)
                counts = Counter(track['history'])
                best_text, cnt = counts.most_common(1)[0]
                if cnt >= self.lock_frames:
                    track['locked'] = best_text
        # create new tracks for unmatched dets
        for di, det_box in enumerate(det_boxes):
            if di in assigned_dets:
                continue
            tid = self.next_id
            self.next_id += 1
            det_text = det_texts[di]
            history = []
            locked = ''
            if is_valid_plate(det_text):
                history.append(det_text)
            self.tracks[tid] = {
                'box': det_box,
                'last_seen': frame_idx,
                'age': 0,
                'history': history,
                'locked': locked,
            }
            assigned_tracks[tid] = di
        # age unmatched tracks
        to_delete = []
        for tid, track in self.tracks.items():
            if tid in assigned_tracks:
                continue
            track['age'] += 1
            if track['age'] > self.max_age:
                to_delete.append(tid)
        for tid in to_delete:
            self.tracks.pop(tid, None)
        # prepare results
        for det_idx, det in enumerate(detections):
            tid = None
            for track_id, d_idx in assigned_tracks.items():
                if d_idx == det_idx:
                    tid = track_id
                    break
            if tid is None:
                results.append({'track_id': -1, 'text': normalize_plate_text(det.get('text', '')), 'is_guess': False})
                continue
            track = self.tracks.get(tid)
            text = track.get('locked') or ''
            is_guess = False
            if not text:
                history = track.get('history') or []
                if history:
                    counts = Counter(history)
                    text, _ = counts.most_common(1)[0]
                    is_guess = True
            results.append({'track_id': tid, 'text': text, 'is_guess': is_guess})
        return results

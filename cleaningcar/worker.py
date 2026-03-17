import threading
import time
from pathlib import Path

import cv2
import numpy as np
from rknnlite.api import RKNNLite

from .constants import (
    CLASS_NAMES,
    CLASS_THRESH,
    LICENSE_CLASS,
    PLATE_EXPAND_DEFAULT,
    STRIDES,
    VEHICLE_LABEL_CN,
    select_box_color,
)
from .fp_detect import FpModelPostprocessor
from .plate_lpr import DualPlateRecognizer
from .plate import decode_lpr_output, enhance_plate_patch, extract_plate_patch
from .vision import box_iou, decode_scale, letterbox, nms, point_in_box, scale_boxes

class DetectWorker(threading.Thread):
    def __init__(self, idx, args, core_mask, task_q, result_q, detect_mask=None):
        super().__init__(daemon=True)
        self.idx = idx
        self.args = args
        self.core_mask = core_mask
        self.task_q = task_q
        self.result_q = result_q
        self.detect_mask = detect_mask
        self.model_variant = self._resolve_model_variant(getattr(args, 'model_variant', 'auto'), args.model)
        self.fp_postprocessor = None
        if self.model_variant == 'fp':
            min_conf = min([float(args.conf)] + [float(v) for v in CLASS_THRESH.values()])
            self.fp_postprocessor = FpModelPostprocessor(
                img_size=(int(args.imgsz), int(args.imgsz)),
                obj_thresh=min_conf,
                nms_thresh=float(args.iou),
            )
        self.rk = RKNNLite()
        if self.rk.load_rknn(args.model) != 0:
            raise RuntimeError('load_rknn failed')
        init_kwargs = {}
        if core_mask is not None:
            init_kwargs['core_mask'] = core_mask
        if self.rk.init_runtime(**init_kwargs) != 0:
            raise RuntimeError('init_runtime failed')
        self.frames = 0
        self.infer_time = 0.0
        self.stop = False
        self.lpr = None
        self.dual_lpr = None
        self.plate_expand = max(0.0, getattr(args, 'plate_expand', PLATE_EXPAND_DEFAULT))
        plate_detect_path = getattr(args, 'plate_detect_model', None)
        plate_rec_path = getattr(args, 'plate_rec_model', None)
        if plate_detect_path and plate_rec_path:
            try:
                self.dual_lpr = DualPlateRecognizer(
                    detect_model_path=str(plate_detect_path),
                    rec_model_path=str(plate_rec_path),
                    verbose=False,
                )
                print(
                    f'Worker {idx}: dual plate models loaded '
                    f'({plate_detect_path}, {plate_rec_path})'
                )
            except Exception as exc:
                self.dual_lpr = None
                print(f'Worker {idx}: failed to init dual plate models: {exc}')
        lpr_path = getattr(args, 'lpr_model', None)
        if lpr_path:
            lpr_path = Path(lpr_path)
            if lpr_path.exists():
                lpr = RKNNLite()
                if lpr.load_rknn(str(lpr_path)) == 0 and lpr.init_runtime() == 0:
                    self.lpr = lpr
                    print(f'Worker {idx}: LPR model loaded from {lpr_path}')
                else:
                    print(f'Worker {idx}: failed to init LPR model {lpr_path}')
            else:
                print(f'Worker {idx}: LPR model not found at {lpr_path}')

    @staticmethod
    def _resolve_model_variant(override, model_path):
        mode = str(override or 'auto').strip().lower()
        if mode in ('legacy', 'fp'):
            return mode
        name = Path(str(model_path or '')).name.lower()
        if '.fp' in name or name.endswith('_fp.rknn') or '1.18.fp' in name:
            return 'fp'
        return 'legacy'

    @staticmethod
    def _plate_overlap_with_dual(box, dual_results):
        if not dual_results:
            return False
        x1, y1, x2, y2 = box
        center = (0.5 * (x1 + x2), 0.5 * (y1 + y2))
        for item in dual_results:
            dual_box = item.get('box')
            if not dual_box or len(dual_box) != 4:
                continue
            if box_iou(box, dual_box) >= 0.3:
                return True
            if point_in_box(center, dual_box):
                return True
        return False

    def run(self):
        while True:
            item = self.task_q.get()
            if item is None:
                self.task_q.task_done()
                break
            if len(item) == 2:
                frame_idx, frame = item
                capture_ts = None
            else:
                frame_idx, frame, capture_ts = item
            proc_frame = frame
            if self.detect_mask is not None:
                proc_frame = cv2.bitwise_and(frame, frame, mask=self.detect_mask)
            ratio = None
            pad = None
            if self.fp_postprocessor is not None:
                img_input, lb_info = self.fp_postprocessor.prepare(proc_frame)
            else:
                lb_img, ratio, pad = letterbox(proc_frame, self.args.imgsz)
                img = cv2.cvtColor(lb_img, cv2.COLOR_BGR2RGB).astype(np.uint8)
                img_input = np.expand_dims(img, 0)
            t0 = time.time()
            outputs = self.rk.inference(inputs=[img_input], data_format=['nhwc'])
            infer_time = time.time() - t0
            self.infer_time += infer_time
            self.frames += 1
            if self.fp_postprocessor is not None:
                if not outputs:
                    self.result_q.put((frame_idx, capture_ts, frame, [], []))
                    self.task_q.task_done()
                    continue
                boxes, classes, scores = self.fp_postprocessor.postprocess(outputs)
                if boxes is None or classes is None or scores is None:
                    self.result_q.put((frame_idx, capture_ts, frame, [], []))
                    self.task_q.task_done()
                    continue
                boxes = self.fp_postprocessor.map_boxes_to_original(boxes, lb_info)
                cls_probs = scores.copy()
                per_class_conf = np.array([CLASS_THRESH.get(int(c), self.args.conf) for c in classes], dtype=np.float32)
                keep = scores >= per_class_conf
                if not np.any(keep):
                    self.result_q.put((frame_idx, capture_ts, frame, [], []))
                    self.task_q.task_done()
                    continue
                boxes = boxes[keep]
                scores = scores[keep]
                classes = classes[keep]
                cls_probs = cls_probs[keep]
            else:
                if not outputs or len(outputs) != 9:
                    self.result_q.put((frame_idx, capture_ts, frame, [], []))
                    self.task_q.task_done()
                    continue
                boxes_list = []
                scores_list = []
                classes_list = []
                cls_prob_list = []
                for i, stride in enumerate(STRIDES):
                    reg = outputs[i * 3 + 0]
                    cls = outputs[i * 3 + 1]
                    obj = outputs[i * 3 + 2]
                    boxes, cls_ids, cls_scores, cls_probs = decode_scale(reg, cls, obj, stride)
                    per_class_conf = np.array([CLASS_THRESH.get(int(c), self.args.conf) for c in cls_ids])
                    keep = cls_scores >= per_class_conf
                    if not np.any(keep):
                        continue
                    boxes_list.append(boxes[keep])
                    scores_list.append(cls_scores[keep])
                    classes_list.append(cls_ids[keep])
                    cls_prob_list.append(cls_probs[keep])
                if not boxes_list:
                    self.result_q.put((frame_idx, capture_ts, frame, [], []))
                    self.task_q.task_done()
                    continue
                boxes = np.concatenate(boxes_list, axis=0)
                scores = np.concatenate(scores_list, axis=0)
                classes = np.concatenate(classes_list, axis=0)
                cls_probs = np.concatenate(cls_prob_list, axis=0)
                boxes = scale_boxes(boxes, ratio, pad, frame.shape)
                keep = nms(boxes, scores, self.args.iou, self.args.max_det)
                boxes = boxes[keep]
                scores = scores[keep]
                classes = classes[keep]
                cls_probs = cls_probs[keep]

            csv_rows = []
            det_payload = []
            base_frame = frame
            draw_frame = frame if self.args.no_draw else frame.copy()
            dual_plate_results = []
            if self.dual_lpr is not None:
                try:
                    dual_plate_results = self.dual_lpr.infer_frame(
                        base_frame,
                        conf_thresh=min(float(self.args.conf), 0.3),
                        iou_thresh=float(self.args.iou),
                    )
                except Exception as exc:
                    dual_plate_results = []
                    if frame_idx == 0 or frame_idx % 300 == 0:
                        print(f'Worker {self.idx}: dual plate inference failed: {exc}')
            for box, score, cls_id, cls_prob in zip(boxes, scores, classes, cls_probs):
                x1, y1, x2, y2 = box.astype(int)
                if cls_id == LICENSE_CLASS and self._plate_overlap_with_dual((x1, y1, x2, y2), dual_plate_results):
                    continue
                plate_text = ''
                plate_color = ''
                plate_color_conf = None
                plate_type = ''
                if cls_id == LICENSE_CLASS and self.lpr is not None:
                    plate_text = self.recognize_plate(base_frame, (x1, y1, x2, y2))
                label_name = CLASS_NAMES[cls_id]
                label = f'{label_name} {cls_prob:.2f}'
                draw_now = True
                if label_name in VEHICLE_LABEL_CN:
                    draw_now = False  # 延后到跟踪阶段，根据锁定车型决定是否绘制
                if draw_now and not self.args.no_draw:
                    color = select_box_color(label_name)
                    cv2.rectangle(draw_frame, (x1, y1), (x2, y2), color, 2)
                    cv2.putText(draw_frame, label, (x1, max(0, y1 - 12)),
                                cv2.FONT_HERSHEY_SIMPLEX, 0.75, (255, 255, 255), 2, cv2.LINE_AA)
                csv_rows.append([frame_idx, label_name, f'{score:.4f}', x1, y1, x2, y2, -1, '', plate_text])
                det_payload.append({
                    'cls': int(cls_id),
                    'score': float(score),
                    'box': [int(x1), int(y1), int(x2), int(y2)],
                    'text': plate_text,
                    'plate_color': plate_color,
                    'plate_color_conf': plate_color_conf,
                    'plate_type': plate_type,
                    'row_idx': len(csv_rows) - 1,
                    'label': label_name,
                })
            for item in dual_plate_results:
                box = item.get('box') or []
                if len(box) != 4:
                    continue
                x1, y1, x2, y2 = [int(round(v)) for v in box]
                label_name = CLASS_NAMES[LICENSE_CLASS]
                score = float(item.get('score', 0.0))
                plate_text = str(item.get('text', '') or '')
                plate_color = str(item.get('plate_color', '') or '')
                plate_type = str(item.get('plate_type', '') or '')
                label = f'{label_name} {score:.2f}'
                if plate_text:
                    label = f'{label} {plate_text}'
                if plate_color:
                    label = f'{label} {plate_color}'
                if not self.args.no_draw:
                    color = select_box_color(label_name)
                    cv2.rectangle(draw_frame, (x1, y1), (x2, y2), color, 2)
                    cv2.putText(draw_frame, label, (x1, max(0, y1 - 12)),
                                cv2.FONT_HERSHEY_SIMPLEX, 0.75, (255, 255, 255), 2, cv2.LINE_AA)
                    for pt in item.get('landmarks', []) or []:
                        if not isinstance(pt, (list, tuple)) or len(pt) != 2:
                            continue
                        cv2.circle(draw_frame, (int(round(pt[0])), int(round(pt[1]))), 3, (0, 255, 255), -1)
                csv_rows.append([frame_idx, label_name, f'{score:.4f}', x1, y1, x2, y2, -1, plate_text, plate_text])
                det_payload.append({
                    'cls': int(LICENSE_CLASS),
                    'score': score,
                    'box': [x1, y1, x2, y2],
                    'text': plate_text,
                    'plate_color': plate_color,
                    'plate_color_conf': None,
                    'plate_type': plate_type,
                    'landmarks': item.get('landmarks'),
                    'row_idx': len(csv_rows) - 1,
                    'label': label_name,
                })
            self.result_q.put((frame_idx, capture_ts, draw_frame, csv_rows, det_payload))
            self.task_q.task_done()
        self.result_q.put(None)

    def recognize_plate(self, frame, box):
        try:
            crop = extract_plate_patch(frame, box, self.plate_expand)
            patch = enhance_plate_patch(crop)
            if patch is None:
                return ''
            outputs = self.lpr.inference(inputs=[np.expand_dims(patch, 0)], data_format=['nhwc'])
            if not outputs:
                return ''
            text = decode_lpr_output(outputs[0])
            return text
        except Exception:
            return ''

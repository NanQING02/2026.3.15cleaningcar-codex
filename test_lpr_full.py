#!/usr/bin/env python3
"""
Chinese License Plate Recognition - RKNN RK3588 完整推理脚本
基于: https://github.com/we0091234/Chinese_license_plate_detection_recognition

功能:
- 车牌检测 (plate_detect.rknn)
- 4角点透视矫正
- 双层车牌拼接处理
- 车牌识别+颜色 (plate_rec_color.rknn)

用法:
    python test_lpr_full.py --images ./images --models ./models --output ./result
"""

import os
import sys
import cv2
import numpy as np
import argparse
import copy
from typing import List, Tuple, Dict
import time

# 车牌字符集 (78类)
PLATE_NAME = r"#京沪津渝冀晋蒙辽吉黑苏浙皖闽赣鲁豫鄂湘粤桂琼川贵云藏陕甘青宁新学警港澳挂使领民航危0123456789ABCDEFGHJKLMNPQRSTUVWXYZ险品"

# 颜色列表
PLATE_COLOR_LIST = ['黑色', '蓝色', '绿色', '白色', '黄色']

# 识别模型归一化参数
MEAN_VALUE = 0.588
STD_VALUE = 0.193


def my_letter_box(img, size=(640, 640)):
    """Letterbox预处理"""
    h, w, c = img.shape
    r = min(size[0] / h, size[1] / w)
    new_h, new_w = int(h * r), int(w * r)
    top = int((size[0] - new_h) / 2)
    left = int((size[1] - new_w) / 2)
    bottom = size[0] - new_h - top
    right = size[1] - new_w - left
    img_resize = cv2.resize(img, (new_w, new_h))
    img = cv2.copyMakeBorder(img_resize, top, bottom, left, right,
                              borderType=cv2.BORDER_CONSTANT, value=(114, 114, 114))
    return img, r, left, top


def xywh2xyxy(boxes):
    """xywh转xyxy格式"""
    xywh = copy.deepcopy(boxes)
    xywh[:, 0] = boxes[:, 0] - boxes[:, 2] / 2
    xywh[:, 1] = boxes[:, 1] - boxes[:, 3] / 2
    xywh[:, 2] = boxes[:, 0] + boxes[:, 2] / 2
    xywh[:, 3] = boxes[:, 1] + boxes[:, 3] / 2
    return xywh


def my_nms(boxes, iou_thresh):
    """NMS非极大值抑制"""
    index = np.argsort(boxes[:, 4])[::-1]
    keep = []
    while index.size > 0:
        i = index[0]
        keep.append(i)
        x1 = np.maximum(boxes[i, 0], boxes[index[1:], 0])
        y1 = np.maximum(boxes[i, 1], boxes[index[1:], 1])
        x2 = np.minimum(boxes[i, 2], boxes[index[1:], 2])
        y2 = np.minimum(boxes[i, 3], boxes[index[1:], 3])
        w = np.maximum(0, x2 - x1)
        h = np.maximum(0, y2 - y1)
        inter_area = w * h
        union_area = ((boxes[i, 2] - boxes[i, 0]) * (boxes[i, 3] - boxes[i, 1]) +
                      (boxes[index[1:], 2] - boxes[index[1:], 0]) * 
                      (boxes[index[1:], 3] - boxes[index[1:], 1]))
        iou = inter_area / (union_area - inter_area + 1e-6)
        idx = np.where(iou <= iou_thresh)[0]
        index = index[idx + 1]
    return keep


def restore_box(boxes, r, left, top):
    """还原坐标到原图"""
    boxes[:, [0, 2, 5, 7, 9, 11]] -= left
    boxes[:, [1, 3, 6, 8, 10, 12]] -= top
    boxes[:, [0, 2, 5, 7, 9, 11]] /= r
    boxes[:, [1, 3, 6, 8, 10, 12]] /= r
    return boxes


def order_points(pts):
    """排序4角点：左上→右上→右下→左下"""
    rect = np.zeros((4, 2), dtype="float32")
    s = pts.sum(axis=1)
    rect[0] = pts[np.argmin(s)]  # 左上
    rect[2] = pts[np.argmax(s)]  # 右下
    diff = np.diff(pts, axis=1)
    rect[1] = pts[np.argmin(diff)]  # 右上
    rect[3] = pts[np.argmax(diff)]  # 左下
    return rect


def four_point_transform(image, pts):
    """透视变换矫正"""
    rect = order_points(pts)
    (tl, tr, br, bl) = rect
    
    widthA = np.sqrt(((br[0] - bl[0]) ** 2) + ((br[1] - bl[1]) ** 2))
    widthB = np.sqrt(((tr[0] - tl[0]) ** 2) + ((tr[1] - tl[1]) ** 2))
    maxWidth = max(int(widthA), int(widthB))
    
    heightA = np.sqrt(((tr[0] - br[0]) ** 2) + ((tr[1] - br[1]) ** 2))
    heightB = np.sqrt(((tl[0] - bl[0]) ** 2) + ((tl[1] - bl[1]) ** 2))
    maxHeight = max(int(heightA), int(heightB))
    
    dst = np.array([
        [0, 0],
        [maxWidth - 1, 0],
        [maxWidth - 1, maxHeight - 1],
        [0, maxHeight - 1]], dtype="float32")
    
    M = cv2.getPerspectiveTransform(rect, dst)
    warped = cv2.warpPerspective(image, M, (maxWidth, maxHeight))
    return warped


def get_split_merge(img):
    """双层车牌拼接处理"""
    h, w, c = img.shape
    img_upper = img[0:int(5/12*h), :]
    img_lower = img[int(1/3*h):, :]
    img_upper = cv2.resize(img_upper, (img_lower.shape[1], img_lower.shape[0]))
    new_img = np.hstack((img_upper, img_lower))
    return new_img


def decode_plate(preds):
    """CTC解码"""
    pre = 0
    new_preds = []
    for i in range(len(preds)):
        if preds[i] != 0 and preds[i] != pre:
            new_preds.append(preds[i])
        pre = preds[i]
    plate = ""
    for i in new_preds:
        plate += PLATE_NAME[int(i)]
    return plate


class LPRRecognizer:
    """车牌识别器 - 支持RKNN和ONNX"""
    
    def __init__(self, detect_model: str, rec_model: str, 
                 use_rknn: bool = True, debug: bool = False):
        self.debug = debug
        self.use_rknn = use_rknn
        
        if use_rknn:
            self._init_rknn(detect_model, rec_model)
        else:
            self._init_onnx(detect_model, rec_model)
    
    def _init_rknn(self, detect_model, rec_model):
        """初始化RKNN模型"""
        from rknnlite.api import RKNNLite
        
        print("加载RKNN模型...")
        
        # 检测模型
        self.detector = RKNNLite()
        if self.detector.load_rknn(detect_model) != 0:
            raise RuntimeError(f"加载检测模型失败: {detect_model}")
        if self.detector.init_runtime() != 0:
            raise RuntimeError("初始化检测模型失败")
        print(f"  ✓ 检测模型: {detect_model}")
        
        # 识别模型
        self.recognizer = RKNNLite()
        if self.recognizer.load_rknn(rec_model) != 0:
            raise RuntimeError(f"加载识别模型失败: {rec_model}")
        if self.recognizer.init_runtime() != 0:
            raise RuntimeError("初始化识别模型失败")
        print(f"  ✓ 识别模型: {rec_model}")
    
    def _init_onnx(self, detect_model, rec_model):
        """初始化ONNX模型"""
        import onnxruntime as ort
        
        print("加载ONNX模型...")
        self.detector = ort.InferenceSession(detect_model)
        print(f"  ✓ 检测模型: {detect_model}")
        
        self.recognizer = ort.InferenceSession(rec_model)
        print(f"  ✓ 识别模型: {rec_model}")
    
    def detect(self, img, conf_thresh=0.3, iou_thresh=0.5):
        """检测车牌"""
        h0, w0 = img.shape[:2]
        
        # 预处理
        img_letterbox, r, left, top = my_letter_box(img, (640, 640))
        
        # 推理
        if self.use_rknn:
            # RKNN: 传入原始uint8图片，NHWC格式
            # RKNN config已配置mean/std会自动做归一化
            # 需要BGR→RGB
            img_rgb = cv2.cvtColor(img_letterbox, cv2.COLOR_BGR2RGB)
            img_input = np.expand_dims(img_rgb, axis=0)  # [1, 640, 640, 3] NHWC
            
            if self.debug:
                print(f"  RKNN输入: shape={img_input.shape}, dtype={img_input.dtype}, range=[{img_input.min()}, {img_input.max()}]")
            
            outputs = self.detector.inference(inputs=[img_input])
            dets = outputs[0]
        else:
            # ONNX: BGR→RGB, /255归一化, HWC→CHW→NCHW
            img_input = img_letterbox[:, :, ::-1].transpose(2, 0, 1).copy().astype(np.float32)
            img_input = img_input / 255.0
            img_input = img_input.reshape(1, *img_input.shape)
            
            input_name = self.detector.get_inputs()[0].name
            outputs = self.detector.run(None, {input_name: img_input})
            dets = outputs[0]
        
        if self.debug:
            print(f"  检测输出: shape={dets.shape}, range=[{dets.min():.2f}, {dets.max():.2f}]")
        
        # 后处理
        if len(dets.shape) == 3:
            dets = dets[0]  # 去掉batch维度
        
        # 过滤低置信度
        choice = dets[:, 4] > conf_thresh
        dets = dets[choice]
        
        if len(dets) == 0:
            return []
        
        # 类别置信度 = obj_conf * cls_conf
        dets[:, 13:15] *= dets[:, 4:5]
        
        # xywh → xyxy
        boxes = xywh2xyxy(dets[:, :4])
        
        # 取分数最高的类别
        score = np.max(dets[:, 13:15], axis=-1, keepdims=True)
        index = np.argmax(dets[:, 13:15], axis=-1).reshape(-1, 1)
        
        # 组合: [x1,y1,x2,y2, score, pt1x,pt1y,...,pt4y, cls_idx]
        output = np.concatenate((boxes, score, dets[:, 5:13], index), axis=1)
        
        # NMS
        keep = my_nms(output, iou_thresh)
        output = output[keep]
        
        # 还原坐标
        output = restore_box(output, r, left, top)
        
        if self.debug:
            print(f"  检测到 {len(output)} 个车牌")
        
        return output
    
    def recognize(self, plate_img):
        """识别车牌"""
        # 预处理
        img = cv2.resize(plate_img, (168, 48))
        
        # 推理
        if self.use_rknn:
            # RKNN: 传入原始uint8图片，NHWC格式
            # RKNN config已配置mean=(149.94), std=(49.215)会自动做归一化
            # 保持BGR格式
            img_input = np.expand_dims(img, axis=0)  # [1, 48, 168, 3] NHWC
            
            if self.debug:
                print(f"    REC输入: shape={img_input.shape}, dtype={img_input.dtype}")
            
            outputs = self.recognizer.inference(inputs=[img_input])
        else:
            # ONNX: 需要手动归一化
            img_norm = img.astype(np.float32)
            img_norm = (img_norm / 255.0 - MEAN_VALUE) / STD_VALUE
            img_norm = img_norm.transpose(2, 0, 1)  # HWC → CHW，保持BGR
            img_norm = img_norm.reshape(1, *img_norm.shape)
            
            input_name = self.recognizer.get_inputs()[0].name
            outputs = self.recognizer.run(None, {input_name: img_norm})
        
        # 解析输出
        y_plate = outputs[0]  # [1, 21, 78]
        y_color = outputs[1]  # [1, 5]
        
        # 字符解码
        index = np.argmax(y_plate, axis=-1)
        plate_no = decode_plate(index[0])
        
        # 颜色解码
        color_idx = np.argmax(y_color)
        plate_color = PLATE_COLOR_LIST[color_idx]
        
        return plate_no, plate_color
    
    def process(self, img, conf_thresh=0.3, iou_thresh=0.5):
        """完整处理流程，带耗时统计"""
        results = []
        timing = {
            'detect': 0,
            'postprocess': 0,
            'perspective': 0,
            'recognize': 0,
        }
        
        # 1. 检测
        t0 = time.time()
        detections = self.detect(img, conf_thresh, iou_thresh)
        timing['detect'] = (time.time() - t0) * 1000
        
        for det in detections:
            result = {}
            
            # 解析检测结果 (后处理)
            t1 = time.time()
            rect = det[:4].tolist()
            landmarks = det[5:13].reshape(4, 2)
            label = int(det[-1])  # 0=单层, 1=双层
            score = det[4]
            timing['postprocess'] += (time.time() - t1) * 1000
            
            # 2. 透视矫正
            t2 = time.time()
            roi_img = four_point_transform(img, landmarks)
            
            if roi_img.size == 0:
                continue
            
            # 3. 双层车牌处理
            if label == 1:
                roi_img = get_split_merge(roi_img)
            timing['perspective'] += (time.time() - t2) * 1000
            
            # 4. 识别
            t3 = time.time()
            plate_no, plate_color = self.recognize(roi_img)
            timing['recognize'] += (time.time() - t3) * 1000
            
            result['rect'] = rect
            result['landmarks'] = landmarks.tolist()
            result['plate_no'] = plate_no
            result['plate_color'] = plate_color
            result['score'] = float(score)
            result['plate_type'] = '双层' if label == 1 else '单层'
            
            results.append(result)
        
        return results, timing


def main():
    parser = argparse.ArgumentParser(description='LPR车牌识别测试')
    parser.add_argument('--images', type=str, required=True, help='图片目录或单张图片')
    parser.add_argument('--models', type=str, default='./models', help='模型目录')
    parser.add_argument('--output', type=str, default='./result', help='输出目录')
    parser.add_argument('--onnx', action='store_true', help='使用ONNX模式')
    parser.add_argument('--debug', action='store_true', help='调试模式')
    parser.add_argument('--conf', type=float, default=0.3, help='置信度阈值')
    
    args = parser.parse_args()
    
    # 确定模型路径
    if args.onnx:
        detect_model = os.path.join(args.models, 'plate_detect.onnx')
        rec_model = os.path.join(args.models, 'plate_rec_color.onnx')
    else:
        detect_model = os.path.join(args.models, 'plate_detect.rknn')
        rec_model = os.path.join(args.models, 'plate_rec_color.rknn')
    
    # 初始化
    lpr = LPRRecognizer(detect_model, rec_model, 
                        use_rknn=not args.onnx, debug=args.debug)
    
    # 获取图片列表
    if os.path.isfile(args.images):
        images = [args.images]
    else:
        exts = ['.jpg', '.jpeg', '.png', '.bmp']
        images = []
        for f in os.listdir(args.images):
            if any(f.lower().endswith(ext) for ext in exts):
                images.append(os.path.join(args.images, f))
    
    os.makedirs(args.output, exist_ok=True)
    
    print(f"\n处理 {len(images)} 张图片...")
    print("=" * 60)
    
    total_time = 0
    total_timing = {'detect': 0, 'postprocess': 0, 'perspective': 0, 'recognize': 0}
    
    for img_path in sorted(images):
        name = os.path.basename(img_path)
        print(f"\n[{name}]")
        
        img = cv2.imread(img_path)
        if img is None:
            print("  读取失败")
            continue
        
        start = time.time()
        results, timing = lpr.process(img, conf_thresh=args.conf)
        elapsed = time.time() - start
        total_time += elapsed
        
        # 累加各部分耗时
        for k in total_timing:
            total_timing[k] += timing[k]
        
        print(f"  检测到 {len(results)} 个车牌 ({elapsed*1000:.1f}ms)")
        if args.debug:
            print(f"    耗时: 检测={timing['detect']:.1f}ms, 透视={timing['perspective']:.1f}ms, 识别={timing['recognize']:.1f}ms")
        
        for i, r in enumerate(results):
            print(f"    [{i}] {r['plate_no']} ({r['plate_color']}, {r['plate_type']}) conf={r['score']:.3f}")
            
            # 绘制结果
            rect = r['rect']
            cv2.rectangle(img, (int(rect[0]), int(rect[1])), 
                         (int(rect[2]), int(rect[3])), (0, 255, 0), 2)
            
            # 绘制关键点
            colors = [(255, 0, 0), (0, 255, 0), (0, 0, 255), (255, 255, 0)]
            for j, pt in enumerate(r['landmarks']):
                cv2.circle(img, (int(pt[0]), int(pt[1])), 5, colors[j], -1)
        
        # 保存结果
        save_path = os.path.join(args.output, name)
        cv2.imwrite(save_path, img)
    
    print("\n" + "=" * 60)
    print(f"完成! 平均耗时: {total_time/len(images)*1000:.1f}ms/张")
    print(f"\n各部分平均耗时:")
    print(f"  检测推理:  {total_timing['detect']/len(images):.1f}ms")
    print(f"  透视矫正:  {total_timing['perspective']/len(images):.1f}ms") 
    print(f"  识别推理:  {total_timing['recognize']/len(images):.1f}ms")
    print(f"  后处理:    {total_timing['postprocess']/len(images):.2f}ms")


if __name__ == '__main__':
    main()

# models 目录说明

本目录只保留当前运行主链路实际使用的模型。

## 当前运行模型

- `detection/best.rknn`
  - 当前主检测模型
  - 运行时后处理支持 `video.fp_output_mode=6|9`
- `plate/plate_detect.rknn`
  - 车牌检测模型
- `plate/plate_rec_color.rknn`
  - 车牌字符和颜色识别模型

## 约束

- 历史模型不要继续放在这里
- 如需保留旧模型，请移动到 `future_modules/legacy_models/`

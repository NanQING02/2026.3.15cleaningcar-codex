# CleaningCar RKNN 项目说明

当前仓库已经整理为可直接部署到 RKNN 板端的运行项目，主链路包含检测、双模型车牌识别、事件输出、截图、单车视频、Web 管理和 guardian 守护。

## 当前实际使用的模型

- 检测模型：`models/detection/best.rknn`
- 车牌检测模型：`models/plate/plate_detect.rknn`
- 车牌识别/颜色模型：`models/plate/plate_rec_color.rknn`

## 主入口

- 主检测入口：`python run_zone_detect.py --config configs/config.json`
- Web 入口：`python -m web.server --config configs/config.json --host 0.0.0.0 --port 8000`

## 核心入口文件说明

### `run_zone_detect.py`

作用：

- 主检测程序启动壳层
- 将 CLI 入口转发到 `cleaningcar.cli`

调用关系：

1. `run_zone_detect.py`
2. `cleaningcar/cli.py`
3. `cleaningcar/pipeline.py`

### `config_manager.py`

作用：

- 读取并校验 `configs/*.json`
- 给 `system`、`video`、`logic`、`storage` 等配置补默认值
- 作为 Web 配置编辑与运行时配置归一化的基础层

接入关系：

- `cleaningcar/runtime_config.py` 会基于它生成运行配置
- `web/helpers.py`、`web/inference.py` 会直接依赖它读取配置

### `zone_manager.py`

作用：

- 管理 Zone A、Zone B、流向向量和区域状态
- 提供 ROI 内外、进出区和方向判定

接入关系：

- `cleaningcar/pipeline.py` 创建 `ZoneManager`
- `cleaningcar/events.py` 依赖它做事件状态判定

## 本轮已修复

- 事件截图链路
  - `cleaningcar/events.py` 已补 `cv2` 导入
  - 事件截图只有在真实落盘成功后才写入 `captureImage`
  - 失败时会输出明确日志：事件类型、轨迹 ID、路径、帧状态、`imwrite` 返回值、异常
- 车牌识别字符表
  - 以 `test_lpr_full.py` 基准字符表统一项目内部定义
  - `cleaningcar/plate_lpr.py` 和 `cleaningcar/constants.py` 现已统一
  - `cleaningcar/plate.py` 的规范化与合法性校验同步收口
- 中文显示
  - 新增 `cleaningcar/text_render.py`
  - 车型中文、车牌省份汉字、车牌颜色文字改为 Pillow 渲染
  - 主字体固定读取 `fonts/platech.ttf`
  - 项目字体缺失时会按固定系统字体顺序兜底

## 当前关键能力

- RKNN FP 检测主链路
- 双模型车牌识别主链路
- 事件 JSON、事件截图、启动截图、手动截图
- 单车视频输出
- 推理心跳、卡死检测、自动重启
- 启动标志文件和启动截图
- Web 端启动、停止、重启、状态查看、截图保留

## FP 检测后处理切换

项目支持两种 FP 后处理模式，配置项为 `video.fp_output_mode`：

- `6`
  - 默认模式
  - 即使模型给出 9 个输出，也只使用每尺度 `box + class`
- `9`
  - 使用每尺度 `box + class + score`

命令行可临时覆盖：

```bash
python run_zone_detect.py --config configs/config.json --fp_output_mode 6
python run_zone_detect.py --config configs/config.json --fp_output_mode 9
```

## 本地文件视频策略

- 本地文件视频会优先被识别为 `file` 源，即使旧配置里写了 `source_mode: camera`
- 默认只跑一遍，读到 EOF 后退出
- 只有在 Web 面板手动勾选“自动重启”后，才会循环重新开始
- Web 面板点击“停止”不会清空当前自动重启勾选状态

## 快速启动

### 方式 1：脚本启动

```bash
chmod +x install_runtime_venv.sh
./install_runtime_venv.sh
```

默认访问：

```text
http://<设备IP>:8000/
```

### 方式 2：手动启动 Web

```bash
source venv-gst/bin/activate
python -m web.server --config configs/config.json --host 0.0.0.0 --port 8000
```

### 方式 3：直接启动主程序

```bash
source venv-gst/bin/activate
python run_zone_detect.py --config configs/config.json
```

## 关键输出目录

- 事件 JSON：`events/<配置名>/`
- 事件截图：`captures/<配置名>/`
- 启动截图：`captures/startup/`
- 手动截图：`captures/manual/`
- 单车视频：`video_result/per_id/`
- 推理日志：`logs/inference/`
- Web 启动日志：`web_server_8000.log`
- 启动标志：由 `system.startup_flag_path` 控制
- 心跳文件：由 `system.heartbeat_path` 控制
- 命令目录：由 `system.command_dir` 控制

## 部署提醒

- 板端部署时必须一并带上 `fonts/platech.ttf`
- `requirements.txt` 新增了 `Pillow`
- `captureImage` 现在只代表“截图文件真实存在”

## 推荐优先阅读

- `cleaningcar/README.md`
- `docs/项目功能与使用说明.md`
- `docs/项目解耦报告.md`
- `docs/项目整理报告.md`
- `docs/板端验收清单.md`
- `configs/README.md`

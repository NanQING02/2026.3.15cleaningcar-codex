# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## 项目概述

清洗车 RKNN 检测系统：基于 RK3588 NPU 的车辆冲洗检测、车牌识别、车轮旁路检测系统。使用 RKNN Lite 做边缘推理，部署在 RK3588 板端。

## 常用命令

```bash
# 启动 Web 管理界面（对外唯一入口，首次会自动安装 venv-gst）
./start_web_server.sh start|stop|restart|status

# 直接启动主检测程序
source venv-gst/bin/activate
python run_zone_detect.py --config configs/config.json

# 切换 FP 后处理模式（6=box+class, 9=box+class+score）
python run_zone_detect.py --config configs/config.json --fp_output_mode 9

# 运行测试
python -m pytest tests/ -v

# 运行单个测试文件
python -m pytest tests/test_events_plate_locking.py -v

# 运行单个测试用例
python -m pytest tests/test_events_plate_locking.py::EventManagerPlateLockingTests::test_locked_text_is_not_overwritten_by_single_wrong_frame -v
```

## 主链路调用关系

```
run_zone_detect.py → cleaningcar/cli.py → cleaningcar/pipeline.py
```

`pipeline.py` 是总调度中心，串联以下模块：

1. **video_io.py** — 视频输入输出，硬件加速解码/编码（FFmpeg rkmpp → GStreamer mpp → 软件回退）
2. **worker.py** — RKNN 推理工作线程（检测 + 双模型车牌识别）
3. **tracking.py** — ByteTrack 车辆跟踪
4. **events.py** — 事件状态机、JSON 生成、截图、HTTP 上报
5. **runtime_signals.py** — 心跳、启动标志、启动截图
6. **wheel.py** — 左右车轮 RTSP 旁路检测（`wheel.enabled=true` 时启用）

推荐阅读顺序：`cli.py` → `pipeline.py` → `worker.py` → `fp_detect.py` → `plate_lpr.py` → `plate.py` → `events.py`

## 关键架构要点

- **视频解码**：三级回退链 `FFmpeg 硬解 → GStreamer+mpp 硬解 → 软件解码`，配置项 `video.hw_decode`
- **视频编码**：三级回退链 `FFmpeg 硬编 → GStreamer 硬编 → FFmpeg libx264`
- **多线程**：主线程读流+跟踪，N 个 worker 线程并行推理（`video.workers` 控制），异步线程单车视频录制
- **事件类型**：type=1~6，其中 type=5（完整冲洗周期）会附加 `wheelResults`
- **车牌锁定**：单车生命周期内多数投票锁定车牌文本和颜色，锁定后不再轻易切换
- **车轮结果归属**：同侧短时间连续命中的车轮结果整串归属同一辆车，避免拆分到后车
- **配置驱动**：`configs/config.json` 是核心，`config_manager.py` 做校验和默认值补充
- **多实例隔离**：`system.device_id` 是命名空间主键，运行时文件按 device_id 自动隔离到 `/dev/shm/cleaningcar_runtime/<device_id>/`

## 模型文件

- `models/detection/best.rknn` — FP 检测
- `models/plate/plate_detect.rknn` — 车牌检测
- `models/plate/plate_rec_color.rknn` — 车牌字符+颜色识别
- `models/wheel/2026.4.28CRwheelfp.rknn` — 车轮检测（仅 wheel.enabled 时）

## Web 管理界面

FastAPI 应用位于 `web/`，主文件 `web/server.py`。功能包括配置管理（用户/开发者分层）、实时调试帧获取、进程守护监控。启动命令通过 `start_web_server.sh` 统一管理。

## 重要业务约束

- 当前只保留双模型车牌链路，旧单模型 LPR 在 `future_modules/legacy_lpr/`
- 全局视频保存已删除，只保留 `logic.enable_per_id_video` 单车视频
- 运行产物清理（`storage_cleanup.py`）当前默认禁用
- 事件截图默认优先原图；调试帧单独输出带绘制画面，两者不共用
- `captureImage` 字段只代表截图文件真实落盘成功
- 板端部署必须带上 `fonts/platech.ttf`
- 多路部署时每份配置的 `device_id` 必须唯一

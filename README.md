# CleaningCar RKNN 项目说明

当前仓库已经收敛为可直接部署到 RKNN 板端的运行项目，主链路包含：

- FP 检测主链路
- 双模型车牌识别主链路
- 事件 JSON、事件截图、启动截图、手动截图
- 单车视频输出
- Web 管理与 guardian 守护
- 启动信号、心跳检测、异常重启
- 运行产物清理旧实现（当前默认禁用）

## 当前实际使用的模型

- 检测模型：`models/detection/best.rknn`
- 车牌检测模型：`models/plate/plate_detect.rknn`
- 车牌识别/颜色模型：`models/plate/plate_rec_color.rknn`
- 车轮旁路模型：`models/wheel/2026.4.28CRwheelfp.rknn`（仅 `wheel.enabled=true` 时使用）

## 当前对外交付入口

- `start_web_server.sh`
  - 对外唯一入口
  - 首次执行 `start` 或 `restart` 时会自动安装或修复 `venv-gst/`
  - 环境已就绪时直接拉起 Web，不重复安装
  - 支持 `start|stop|restart|status`
  - 后台启动时会写 `web_server_<port>.pid` 和 `web_server_<port>.log`
- `install_runtime_venv.sh`
  - 内部运行环境安装 helper
  - 由 `start_web_server.sh` 自动调用
  - 甲方一般不需要手工执行

## 核心入口文件说明

### `run_zone_detect.py`

- 主检测程序启动壳层
- 将 CLI 入口转发到 `cleaningcar/cli.py`

主调用关系：

1. `run_zone_detect.py`
2. `cleaningcar/cli.py`
3. `cleaningcar/pipeline.py`

### `config_manager.py`

- 读取并校验 `configs/*.json`
- 给 `system`、`video`、`logic` 等配置补默认值
- 统一处理 `logic.per_id_video_dir` 等运行口径

### `zone_manager.py`

- 管理 Zone A、Zone B、流向向量和区域状态
- 提供 ROI 内外、进出区和方向判定

## 当前运行口径

- 当前只保留双模型车牌链路，旧单模型 LPR 不再参与主链路
- `video.fp_output_mode` 支持 `6` 和 `9` 两种后处理模式，默认 `6`
- 当 `video.hw_decode=true` 时，读流顺序为：`FFmpeg 硬解 -> GStreamer+mpp 硬解 -> 软件解码`
- 单车视频写出顺序为：`FFmpeg 硬编 -> GStreamer 硬编 -> FFmpeg libx264`
- 本地文件视频默认只跑一遍，读到 EOF 后退出；只有手动勾选自动重启才会循环
- `logic.per_id_video_dir` 留空、空白或不可写时，统一回退到 `video_result/per_id/`
- 全局视频保存功能已彻底删除，当前只保留 `logic.enable_per_id_video`
- 运行产物清理当前默认禁用，旧实现仅保留在代码中备用

## 当前默认配置快照

以 `configs/config.json` 为准，当前仓库默认口径大致如下：

- 视频源：RTSP，`video.source_mode=camera`
- 解码：`video.hw_decode=true`
- 推理并发：`video.workers=2`，`video.core_mask=0-1`
- 画面叠加：`logic.no_draw=true`
- 调试帧：`video.debug_frame_path=off`
- 车牌副链路降频：`logic.plate_infer_stride=2`
- 单车视频：`logic.enable_per_id_video=true`
- 检测 CSV：`video.csv=./video_result/test.csv`
- 事件截图上报格式：`system.api.capture_mode=base64`
- 车轮旁路：默认关闭；开启后走 `wheel.left_source/right_source`

说明：

- 上述只是当前仓库默认值，运行时仍以实际配置文件和 Web 保存结果为准
- 若板端缺少 `ffmpeg rkmpp`，程序会先退到 `GStreamer+mpp`；若 `mppvideodec` 也不可用，再退到软件解码
- 涉及性能、正确性和旁路开销时，优先同时对照 `configs/config.json` 与 `cleaningcar/pipeline.py`

## 主链路速览

当前最重要的主链路是：

1. `run_zone_detect.py`
2. `cleaningcar/cli.py`
3. `cleaningcar/pipeline.py`
4. `cleaningcar/video_io.py`
5. `cleaningcar/worker.py`
6. `cleaningcar/tracking.py` + `cleaningcar/plate.py` + `zone_manager.py`
7. `cleaningcar/events.py`

如果只是为了快速理解“拉流 -> 解码 -> 推理 -> 跟踪/结果组装 -> 上报”，建议先看：

- `cleaningcar/pipeline.py`
- `cleaningcar/video_io.py`
- `cleaningcar/worker.py`
- `cleaningcar/tracking.py`
- `cleaningcar/plate.py`
- `zone_manager.py`
- `cleaningcar/events.py`

## 快速启动

### 1. 一键启动 Web

```bash
chmod +x start_web_server.sh
./start_web_server.sh start
```

说明：

- 第一次执行会自动安装或修复运行环境，然后拉起 Web
- 后续执行检测到 `venv-gst` 可用时会直接启动，不重复安装

常用动作：

```bash
./start_web_server.sh status
./start_web_server.sh restart
./start_web_server.sh stop
```

### 2. 甲方自行配置 `systemd` 守护（可选）

仓库不再提供自动安装脚本。如果甲方需要开机守护，建议先手工执行一次 `./start_web_server.sh start`，确认环境已经装好且 Web 可以正常启动，再自行创建 `/etc/systemd/system/cleaningcar-web.service`：

```bash
cat <<'EOF' | sudo tee /etc/systemd/system/cleaningcar-web.service >/dev/null
[Unit]
Description=CleaningCar Web Service
After=network.target

[Service]
Type=simple
User=<运行用户>
Group=<运行组>
WorkingDirectory=/path/to/cleaningcar-delivery
Environment=PYTHONUNBUFFERED=1
ExecStart=/path/to/cleaningcar-delivery/venv-gst/bin/python -m web.server --config /path/to/cleaningcar-delivery/configs/config.json --host 0.0.0.0 --port 8000
Restart=on-failure
RestartSec=3

[Install]
WantedBy=multi-user.target
EOF

sudo systemctl daemon-reload
sudo systemctl enable --now cleaningcar-web
systemctl status cleaningcar-web --no-pager
```

说明：

- `systemctl stop cleaningcar-web` 属于人工停止，不会自动重启
- 直接 `kill` / `kill -9` Web 主进程会被 `systemd` 视为异常退出并拉起
- `ExecStart` 请按实际交付目录、配置文件路径和运行用户替换

### 3. 直接启动主程序

```bash
source venv-gst/bin/activate
python run_zone_detect.py --config configs/config.json
```

### 4. 切换 FP 后处理模式

```bash
python run_zone_detect.py --config configs/config.json --fp_output_mode 6
python run_zone_detect.py --config configs/config.json --fp_output_mode 9
```

## 关键输出目录

- 事件 JSON：`events/<配置名>/`
- 事件截图：`captures/<配置名>/`
- 启动截图：`captures/startup/`
- 手动截图：`captures/manual/`
- 单车视频：`video_result/per_id/`
- 推理日志：`logs/inference/`
- Web 日志：`web_server_8000.log`
- 心跳文件：由 `system.heartbeat_path` 控制
- 启动标志：由 `system.startup_flag_path` 控制
- 命令目录：由 `system.command_dir` 控制

## 部署提醒

- 板端部署时必须一并带上 `fonts/platech.ttf`
- `requirements.txt` 已包含 `Pillow`
- `captureImage` 现在只代表“截图文件真实存在”
- 手工停 Web 请使用 `./start_web_server.sh stop`
- 若甲方自行配置 `systemd`，人工停服务请使用 `systemctl stop cleaningcar-web`
- 当前不对外开放清理策略配置项；`storage_cleanup.py` 仍保留旧实现，但运行期默认禁用

## 推荐阅读顺序

建议其他 agent 或后续接手者按下面顺序建立上下文：

1. `README.md`
2. `cleaningcar/README.md`
3. `configs/README.md`
4. `cleaningcar/pipeline.py`
5. `cleaningcar/video_io.py`
6. `cleaningcar/worker.py`
7. `cleaningcar/tracking.py`
8. `cleaningcar/plate.py`
9. `zone_manager.py`
10. `cleaningcar/events.py`
11. `docs/README.md`
12. `docs/总览说明/项目功能与使用说明.md`
13. `docs/部署验收/运行路径说明.md`
14. `docs/部署验收/心跳与截图对接说明.md`
15. `docs/部署验收/板端验收清单.md`

补充说明：

- `docs/总览说明/` 是当前项目总览的主入口
- `docs/部署验收/` 里同时包含“当前仍有效”的部署文档和“带时间/环境前提”的专项文档
- 遇到 `甲方设备MPP环境说明.md`、`甲方RK3588板端*.md`、`部署前性能评估与低风险优化建议.md` 这类文档时，要先看文首说明，再判断是不是当前场景

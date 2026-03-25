# CleaningCar RKNN 项目说明

当前仓库已经收敛为可直接部署到 RKNN 板端的运行项目，主链路包含：

- FP 检测主链路
- 双模型车牌识别主链路
- 事件 JSON、事件截图、启动截图、手动截图
- 单车视频输出
- Web 管理与 guardian 守护
- 启动信号、心跳检测、异常重启
- 运行产物清理

## 当前实际使用的模型

- 检测模型：`models/detection/best.rknn`
- 车牌检测模型：`models/plate/plate_detect.rknn`
- 车牌识别/颜色模型：`models/plate/plate_rec_color.rknn`

## 根目录只保留 4 个脚本

- `install_runtime_venv.sh`
  - 安装或重建 `venv-gst/`
  - 安装系统依赖、Python 依赖、RKNN/RGA 运行库
  - 只安装环境，不启动 Web
- `start_web_server.sh`
  - 手工管理 Web 服务
  - 支持 `start|stop|restart|status`
  - 后台启动时会写 `web_server_<port>.pid` 和 `web_server_<port>.log`
- `uninstall_runtime_venv.sh`
  - 停止手工 Web
  - 删除 `venv-gst/`
  - 若已安装 `cleaningcar-web.service`，会一并停用并移除
- `install_cleaningcar_systemd.sh`
  - 安装并启用 `cleaningcar-web.service`
  - 开机只启动 Web，不自动启动推理
  - 安装前会先停止当前手工 Web，避免端口冲突

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
- 给 `system`、`video`、`logic`、`storage` 等配置补默认值
- 统一处理 `logic.per_id_video_dir`、`storage.*` 清理策略等运行口径

### `zone_manager.py`

- 管理 Zone A、Zone B、流向向量和区域状态
- 提供 ROI 内外、进出区和方向判定

## 当前运行口径

- 当前只保留双模型车牌链路，旧单模型 LPR 不再参与主链路
- `video.fp_output_mode` 支持 `6` 和 `9` 两种后处理模式，默认 `6`
- 本地文件视频默认只跑一遍，读到 EOF 后退出；只有手动勾选自动重启才会循环
- `logic.per_id_video_dir` 留空、空白或不可写时，统一回退到 `video_result/per_id/`
- 截图与单车视频已经接入按天数 + 按数量的周期清理

## 快速启动

### 1. 安装运行环境

```bash
chmod +x install_runtime_venv.sh
./install_runtime_venv.sh
```

### 2. 手工启动 Web

建议先激活虚拟环境，后续排查命令会更一致；脚本本身也会直接使用 `venv-gst/bin/python`。

```bash
source venv-gst/bin/activate
chmod +x start_web_server.sh
./start_web_server.sh start
```

常用动作：

```bash
./start_web_server.sh status
./start_web_server.sh restart
./start_web_server.sh stop
```

### 3. 安装 `systemd` Web 服务

```bash
chmod +x install_cleaningcar_systemd.sh
./install_cleaningcar_systemd.sh
systemctl status cleaningcar-web --no-pager
```

说明：

- `systemctl stop cleaningcar-web` 属于人工停止，不会自动重启
- 直接 `kill` / `kill -9` Web 主进程会被 `systemd` 视为异常退出并拉起

### 4. 卸载运行环境

```bash
chmod +x uninstall_runtime_venv.sh
./uninstall_runtime_venv.sh
```

### 5. 直接启动主程序

```bash
source venv-gst/bin/activate
python run_zone_detect.py --config configs/config.json
```

### 6. 切换 FP 后处理模式

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
- 使用 `systemd` 时，人工停服务请使用 `systemctl stop cleaningcar-web`
- 当前清理策略配置项：
  - `storage.clean_interval_seconds`
  - `storage.capture_keep_days`
  - `storage.capture_keep_count`
  - `storage.per_id_video_keep_days`
  - `storage.per_id_video_keep_count`

## 推荐阅读顺序

- `cleaningcar/README.md`
- `configs/README.md`
- `docs/README.md`
- `docs/总览说明/项目功能与使用说明.md`
- `docs/部署验收/环境目录说明.md`
- `docs/部署验收/运行路径说明.md`
- `docs/部署验收/心跳与截图对接说明.md`
- `docs/部署验收/板端验收清单.md`

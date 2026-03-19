# CleaningCar RKNN 项目说明

当前项目已经整理为可运行的主链路，核心组成如下：

- 检测模型：`models/detection/best.rknn`
- 车牌双模型：`models/plate/plate_detect.rknn` + `models/plate/plate_rec_color.rknn`
- 主程序入口：`python run_zone_detect.py --config configs/config.json`
- Web 入口：`python -m web.server --config configs/config.json --host 0.0.0.0 --port 8000`

## 核心入口文件说明

### `run_zone_detect.py`

作用：

- 主检测程序入口
- 不承载业务逻辑本身，只负责把运行入口交给 `cleaningcar.cli`

接入关系：

1. `run_zone_detect.py`
2. `cleaningcar/cli.py`
3. `cleaningcar/pipeline.py`

你可以把它理解成“启动壳层”。

### `config_manager.py`

作用：

- 负责读取和校验 `configs/*.json`
- 负责给 `system`、`video`、`logic`、`storage` 等配置补默认值
- 是 Web 端配置编辑和运行配置合法性的基础

接入关系：

- `cleaningcar/runtime_config.py` 会基于它生成主程序运行时配置
- `web/helpers.py`、`web/inference.py` 也会直接使用它读取配置

你可以把它理解成“配置规范层”。

### `zone_manager.py`

作用：

- 负责 ROI 区域和流向相关判定
- 管理 Zone A、Zone B、流向向量和区域内外关系

接入关系：

- `cleaningcar/pipeline.py` 中会创建 `ZoneManager`
- `cleaningcar/events.py` 依赖它做区域状态和事件判定

你可以把它理解成“区域规则层”。

## 运行脚本说明

### `install_runtime_venv.sh`

作用：

- 准备虚拟环境
- 安装项目依赖
- 启动 Web 服务

### `stop_runtime_venv.sh`

作用：

- 停止当前项目启动的 Web 服务
- 按 pid 文件和端口占用情况回收 `web.server`
- 释放 `8000` 或指定端口，避免重复启动冲突

是否建议删除：

- 当前不建议删除
- 它是和 `install_runtime_venv.sh` 配套的停止脚本
- 如果你后面完全改成 `systemd` 或其他外部服务管理方式，再考虑删除或归档

## 当前关键能力

- 推理心跳、卡死检测和自动重启
- 启动标志文件、启动截图和启动日志信号
- 手动保留截图命令和 Web 触发接口
- FP 检测模型后处理可切换 `6` 输出模式或 `9` 输出模式

## FP 检测后处理切换

项目现在支持两种 FP 检测后处理方式，切换项为 `video.fp_output_mode`：

- `6`
  - 默认模式
  - 即使模型实际给出 `9` 个输出，也只使用每个尺度的 `box + class`
  - 忽略 `score` 分支，便于与旧后处理稳定对比
- `9`
  - 使用每个尺度的 `box + class + score`
  - 用于和完整 9 输出解析方式做效果对比

也可以临时通过命令行覆盖：

```bash
python run_zone_detect.py --config configs/config.json --fp_output_mode 6
python run_zone_detect.py --config configs/config.json --fp_output_mode 9
```

## 推荐先看

- `cleaningcar/README.md`
- `docs/项目功能与使用说明.md`
- `docs/项目解耦报告.md`
- `docs/后续需求实施方案.md`
- `docs/运行路径说明.md`
- `configs/README.md`

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

## 关键输出位置

- 推理日志：`logs/inference/`
- 事件输出：`events/<配置名>/`
- 事件截图：`captures/<配置名>/`
- 启动截图：`captures/startup/`
- 手动截图：`captures/manual/`
- 心跳文件：由 `system.heartbeat_path` 控制
- 启动标志：由 `system.startup_flag_path` 控制
- 命令目录：由 `system.command_dir` 控制

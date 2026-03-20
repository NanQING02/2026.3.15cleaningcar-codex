# configs 目录说明

本目录存放项目运行配置。

## 当前配置文件

- `config.json`
  - 默认主配置
- `config_绕行.json`
  - 备用 / 绕行配置

## 重要配置项

### `video.fp_output_mode`

FP 检测模型输出解析模式：

- `6`
  - 默认值
  - 即使模型有 `9` 个输出，也只取 `box + class`
- `9`
  - 使用 `box + class + score`

### 其他常用配置

- `video.source`
- `video.source_mode`
- `video.workers`
- `video.core_mask`
- `video.debug_frame_path`
- `system.command_dir`
- `system.heartbeat_path`
- `system.startup_flag_path`
- `system.startup_capture_dir`
- `system.manual_capture_dir`

## 使用方式

主程序：

```bash
python run_zone_detect.py --config configs/config.json
```

切换后处理模式：

```bash
python run_zone_detect.py --config configs/config.json --fp_output_mode 6
python run_zone_detect.py --config configs/config.json --fp_output_mode 9
```

Web 服务：

```bash
source venv-gst/bin/activate
python -m web.server --config configs/config.json --host 0.0.0.0 --port 8000
```

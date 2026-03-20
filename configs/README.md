# configs 目录说明

`configs/` 用于存放项目运行配置。当前建议以 `configs/config.json` 作为正式主配置，其他配置只保留为备份或场景切换。

## 当前配置文件

- `config.json`
  - 默认主配置
- `config_绕行.json`
  - 备用 / 绕行配置

## 重点配置项

### `video.source` / `video.source_mode`

- `video.source`：视频源地址，可以是 RTSP、摄像头索引或本地文件
- `video.source_mode`：建议优先用 `auto`
- 当输入被识别为本地文件时，会按文件源处理，默认跑完一遍后退出

### `video.fp_output_mode`

FP 检测模型后处理模式：

- `6`
  - 默认值
  - 即使模型给出 `9` 个输出，也只使用每个尺度的 `box + class`
- `9`
  - 使用每个尺度的 `box + class + score`

### `logic.per_id_video_dir`

- 单车视频输出目录
- 留空、只填空白、或目录不可写时，会自动回退到 `video_result/per_id/`

### `system.startup_capture_dir` / `system.manual_capture_dir`

- `system.startup_capture_dir`
  - 自动启动截图目录
  - 主流程首次真正产出结果后自动写入
- `system.manual_capture_dir`
  - 手动保留截图目录
  - 对应 Web 的 `POST /snapshot/keep`

### `system.heartbeat_path` / `system.startup_flag_path` / `system.command_dir`

- `system.heartbeat_path`
  - 推理心跳文件
- `system.startup_flag_path`
  - 启动成功标志文件
- `system.command_dir`
  - 运行期命令目录，手动截图等命令会从这里消费

### `storage.*`

当前已接入运行产物清理：

- `storage.clean_interval_seconds`
  - 周期清理间隔
- `storage.capture_keep_days`
  - 事件/启动/手动截图保留天数
- `storage.capture_keep_count`
  - 事件/启动/手动截图保留数量
- `storage.per_id_video_keep_days`
  - 单车视频保留天数
- `storage.per_id_video_keep_count`
  - 单车视频保留数量

## 常用启动命令

```bash
source venv-gst/bin/activate
python run_zone_detect.py --config configs/config.json
```

临时切换 FP 后处理：

```bash
python run_zone_detect.py --config configs/config.json --fp_output_mode 6
python run_zone_detect.py --config configs/config.json --fp_output_mode 9
```

手工启动 Web：

```bash
source venv-gst/bin/activate
./start_web_server.sh start
```

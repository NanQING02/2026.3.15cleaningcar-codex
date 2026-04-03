# configs 目录说明

`configs/` 用于存放项目运行配置。当前建议以 `configs/config.json` 作为正式主配置，其他配置只保留为备份或场景切换。

## 当前配置文件

- `config.json`
  - 默认主配置
- `config_绕行.json`
  - 备用 / 绕行配置

## 重点配置项

### `system.device_id`

- 当前多路运行时命名空间的主键
- 默认运行时文件会按它自动隔离
- 多路部署时，**每一份配置的 `device_id` 必须唯一**

### `video.source` / `video.source_mode`

- `video.source`：视频源地址，可以是 RTSP、摄像头索引或本地文件
- `video.source_mode`：建议优先用 `auto`
- 当输入被识别为本地文件时，会按文件源处理，默认跑完一遍后退出

### `video.hw_decode`

- `true` 时，读流按 `FFmpeg 硬解 -> GStreamer+mpp 硬解 -> 软件解码` 依次尝试
- 板端若缺少 `ffmpeg rkmpp`，程序会自动退到 `mppvideodec`
- 若两级硬解都不可用，才会退到软件解码

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

### `logic.enable_per_id_video`

- 当前只保留单车视频留存开关 `logic.enable_per_id_video`
- 单车视频写出顺序为 `FFmpeg 硬编 -> GStreamer 硬编 -> FFmpeg libx264`
- 旧的全局视频保存字段 `video.save_video`、`logic.enable_global_video` 已彻底删除，不再生效

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

默认收口规则：

- 当这 3 项留空，或仍使用旧默认值时，运行时会自动改写到：
  - `/dev/shm/cleaningcar_runtime/<device_id>/heartbeat.json`
  - `/dev/shm/cleaningcar_runtime/<device_id>/started.flag`
  - `/dev/shm/cleaningcar_runtime/<device_id>/cmd/`
- 如果你显式填写了自定义路径，则仍按自定义路径运行

### `video.debug_frame_path`

- 调试画面输出文件
- 当留空或仍使用旧默认值 `/dev/shm/cleaningcar_debug.jpg` 时，运行时会自动改写到：
  - `/dev/shm/cleaningcar_runtime/<device_id>/debug.jpg`

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

启动 Web：

```bash
./start_web_server.sh start
```

说明：

- 首次执行会自动安装或修复 `venv-gst`
- 环境已就绪时会直接拉起 Web

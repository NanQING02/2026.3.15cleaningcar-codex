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
- Web 端不再浏览这些单车录像，但后台仍会继续保存
- 旧的全局视频保存字段 `video.save_video`、`logic.enable_global_video` 已彻底删除，不再生效

### `logic.draw_plate_boxes`

- 默认 `false`，车牌框和车牌文字不绘制到输出帧
- 只有同时未启用 `logic.no_draw` 且手动开启该项时，车牌绘制才会出现在调试帧与事件截图中

### `wheel.*`

- `wheel.enabled`
  - 是否启用左右车轮 RTSP 旁路
- `wheel.left_source` / `wheel.right_source`
  - 左右车轮视频源
- `wheel.target_fps`
  - 每路节流推理频率，默认低于主链路
- `wheel.center_min_margin_ratio`
  - 只有轮胎框中心落在画面中央安全区内，才缓存结果
  - 当前默认值：`0.25`
- `wheel.classes`
  - 当前默认：`0-25`、`25-50`、`50-75`、`75-100`

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
- Web 控制台里的“实时调试画面”读取的就是这里的文件
- 当前 Web 页面不会自动轮询，需要手动点击获取
- 调试帧会单独保存带绘制画面，不影响事件上报默认原图策略
- 当留空或仍使用旧默认值 `/dev/shm/cleaningcar_debug.jpg` 时，运行时会自动改写到：
  - `/dev/shm/cleaningcar_runtime/<device_id>/debug.jpg`

### 旧功能：运行产物清理

- 当前默认配置和 Web 配置页已不再暴露 `storage.*`
- `storage_cleanup.py` 旧实现仍保留在代码中，但运行期默认禁用
- 后续如果需要重新启用，建议单独评审清理策略、回收范围和误删风险后再恢复

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

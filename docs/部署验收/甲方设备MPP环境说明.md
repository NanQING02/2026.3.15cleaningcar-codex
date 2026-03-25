# 甲方设备 MPP 环境说明

本文档用于说明甲方设备当前为什么不能启用 `GStreamer+mpp` 硬件解码，以及后续可选处理方案。

## 1. 当前结论

本项目的硬解入口依赖：

- `video.hw_decode=true`
- OpenCV 支持 `GStreamer`
- GStreamer 存在 `mppvideodec`
- 系统存在 `libgstrockchipmpp.so` 和 `librockchip_mpp.so`

当前两套设备的排查结论如下。

### 实验室设备

- 系统：`Ubuntu 24.04.1`
- GStreamer：`1.24.2`
- 已安装包：
  - `gstreamer1.0-rockchip1`
  - `librockchip-mpp1`
  - `rockchip-multimedia-config`
- 已确认存在：
  - `/usr/lib/aarch64-linux-gnu/gstreamer-1.0/libgstrockchipmpp.so`
  - `/usr/lib/aarch64-linux-gnu/librockchip_mpp.so.0`
- 项目日志可见：
  - `[reader] Using GStreamer+mpp file pipeline for ...`

### 甲方设备

- 系统：`Ubuntu 20.04.6`
- GStreamer：`1.16.3`
- 已安装包：
  - `librockchip-mpp1`
  - `librockchip-vpu0`
- 未安装包：
  - `gstreamer1.0-rockchip1`
  - `rockchip-multimedia-config`
- 已确认存在：
  - `/usr/lib/aarch64-linux-gnu/librockchip_mpp.so.0`
- 已确认不存在：
  - `mppvideodec`
  - `libgstrockchipmpp.so`

现场命令结果已经证明：

```bash
gst-inspect-1.0 mppvideodec
```

返回：

```text
No such element or plugin 'mppvideodec'
```

这说明甲方设备只有 Rockchip MPP 运行库，没有 GStreamer 的 Rockchip MPP 插件层，因此当前无法启用项目内的 `GStreamer+mpp` 硬解链路。

## 2. 对项目的直接影响

- 当前甲方设备上，`video.hw_decode=true` 不能正常工作
- 本项目硬解失败时不会自动回退到软件解码主链路
- 因此在甲方设备环境修复前，正式部署必须保持：

```json
"video": {
  "hw_decode": false
}
```

本轮交付默认走软件解码部署。

## 3. 为什么不能直接拷实验室的插件

不建议直接把实验室设备的 `libgstrockchipmpp.so` 或对应 deb 包直接复制到甲方设备，原因如下：

- 实验室设备是 `Ubuntu 24.04`
- 甲方设备是 `Ubuntu 20.04`
- 两边 GStreamer 主版本和系统依赖不同
- 直接混装存在 ABI 不匹配、插件注册失败、运行时崩溃的风险

因此，MPP 环境问题应按系统级多媒体环境处理，而不是按 Python 项目文件处理。

## 4. 可选方案

### 方案 1：更换系统并对齐实验室环境

这是推荐方案。

目标：

- 将甲方设备更换为与实验室一致或兼容的系统镜像
- 至少对齐到支持 `gstreamer1.0-rockchip1` 的系统和软件源

建议口径：

- 优先由厂家/BSP 侧提供与实验室一致的板端镜像
- 或至少升级到可安装 `gstreamer1.0-rockchip1` 的系统版本

环境完成后需要满足：

- `gst-inspect-1.0 mppvideodec` 能输出插件详情
- `/usr/lib/aarch64-linux-gnu/gstreamer-1.0/libgstrockchipmpp.so` 存在
- 项目启用 `hw_decode` 后日志出现：
  - `[reader] Using GStreamer+mpp file pipeline for ...`
  - 或 `[reader] Using GStreamer+mpp RTSP TCP pipeline for ...`

### 方案 2：保留 Focal，由厂家编译适配插件

若甲方设备必须继续使用 `Ubuntu 20.04 (Focal)`，则需要由厂家或 BSP 供应方处理：

- 针对 `Ubuntu 20.04`
- 针对 `GStreamer 1.16.3`
- 针对当前板端 BSP / `librockchip-mpp1`
- 编译并交付一套可用的 `mppvideodec` 插件

交付形态建议为：

- 完整系统镜像
- 或可安装的 deb 包

不建议只给单个 `.so` 文件。

验收口径与方案 1 相同：

- `gst-inspect-1.0 mppvideodec` 成功
- `gst-launch-1.0` 本地 H.264 管线成功
- 项目日志确认实际走到 `GStreamer+mpp`

## 5. 本轮交付口径

本轮项目先按软件解码部署，不阻塞交付：

- `video.hw_decode=false`
- 先完成业务功能、稳定性和事件链路验收
- MPP 硬解作为甲方环境增强项单独推进

## 6. 环境修复后的最小验收命令

甲方设备完成环境补齐后，按以下顺序复测。

### 6.1 检查插件

```bash
gst-inspect-1.0 mppvideodec
gst-inspect-1.0 | grep -Ei "mpp|rockchip"
```

### 6.2 检查本地 H.264 管线

```bash
gst-launch-1.0 -v \
  filesrc location="/path/to/test.mp4" ! \
  qtdemux ! h264parse ! mppvideodec ! \
  videoconvert ! fpsdisplaysink video-sink=fakesink sync=false
```

### 6.3 检查项目实际日志

启用 `video.hw_decode=true` 后，项目日志必须出现：

- `[reader] Using GStreamer+mpp file pipeline for ...`
- 或 `[reader] Using GStreamer+mpp RTSP TCP pipeline for ...`

若未出现，则视为仍未真正启用 MPP。

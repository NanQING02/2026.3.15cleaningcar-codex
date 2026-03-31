# 甲方RK3588板端Rockchip MPP环境差异与驱动补齐说明

本文档依据以下材料整理：

- 《甲方设备MPP排查清单》
- `实验室设备输出.txt`
- `甲方设备输出.txt`

目的如下：

- 说明实验室设备与甲方设备在 Rockchip MPP 多媒体环境上的实际差异
- 明确当前问题归属在板端 BSP/多媒体环境，而不在项目代码层
- 形成一份可以正式发给板商/BSP 供应方的补齐需求与验收口径

文档时间基准：`2026-03-26`

> 阅读前说明（更新于 2026-03-31）
>
> - 当前代码解码顺序是：`FFmpeg 硬解 -> GStreamer+mpp 硬解 -> 软件解码`。
> - 当前 per-id 单车视频编码顺序是：`FFmpeg 硬编 -> GStreamer 硬编 -> FFmpeg libx264`。
> - 因此，本文中的 Rockchip GStreamer MPP 插件，应理解为“第二备选硬解链路所需能力”，不是当前唯一硬解入口。

## 1. 结论摘要

根据两台设备的实际输出，可以确认：

- 实验室设备已正常安装并加载 Rockchip GStreamer MPP 插件，至少可证明备用 `GStreamer+mpp` 链路可用
- 甲方设备虽然已安装 `librockchip-mpp1`、`librockchip-mpp-dev`、`librockchip-vpu0` 等运行库
- 甲方设备的 OpenCV 也已确认支持 `GStreamer`
- 但甲方设备 **未安装或未提供 Rockchip GStreamer MPP 插件库**
- 同批排查里 `ffmpeg -hide_banner -decoders | grep rkmpp` 与 `ffmpeg -hide_banner -encoders | grep rkmpp` 也未搜到可用项
- 因而 `gst-inspect-1.0 mppvideodec` 失败，`gst-launch-1.0 ... ! mppvideodec ! ...` 也无法执行

因此，当前阻塞点不是 Python 项目，也不是 OpenCV 是否开启了 GStreamer，而是：

**甲方 RK3588 板端同时缺少当前项目 `FFmpeg` 优先硬解/硬编所需的 `rkmpp` 能力，以及第二备选 `GStreamer+mpp` 所需的 Rockchip GStreamer MPP 插件层。**

## 2. 两台设备的关键差异对照

| 对比项 | 实验室设备 | 甲方设备 | 判断 |
| --- | --- | --- | --- |
| `gst-inspect-1.0 mppvideodec` | 成功，能输出插件详情 | 失败，返回 `No such element or plugin 'mppvideodec'` | 甲方缺插件 |
| GStreamer 插件名 | `rockchipmpp` | 无 | 甲方缺插件 |
| 插件文件 | `/usr/lib/aarch64-linux-gnu/gstreamer-1.0/libgstrockchipmpp.so` | 未找到 | 甲方缺插件文件 |
| 插件能力 | `Rockchip's MPP video decoder` | 无法识别 | 甲方未具备同等能力 |
| OpenCV 的 GStreamer 支持 | 本轮输出未重复采集 | `YES (1.16.2)` | 甲方该项已满足，不是当前问题 |
| `librockchip_mpp.so` 运行库 | 实验室输出未列出，但插件已实际可用 | 已存在 | 甲方并非完全没有 MPP，只是缺插件层 |
| `ffmpeg rkmpp` | 本轮实验室输出未体现 | 未搜到 | 甲方缺当前优先链路能力 |

## 3. 实验室设备的有效证据

实验室设备输出已经直接证明：正常设备上 `mppvideodec` 是可见、可识别、可加载的。

### 3.1 `mppvideodec` 插件存在

命令：

```bash
gst-inspect-1.0 mppvideodec
```

输出关键信息：

```text
Factory Details:
  Long-name                Rockchip's MPP video decoder

Plugin Details:
  Name                     rockchipmpp
  Description              Rockchip Mpp Video Plugin
  Filename                 /usr/lib/aarch64-linux-gnu/gstreamer-1.0/libgstrockchipmpp.so
  Version                  1.14.4
  Source module            gst-rockchip
```

这几条信息已经足以证明实验室设备具备以下条件：

- 存在 `mppvideodec`
- GStreamer 已注册 `rockchipmpp` 插件
- 插件文件位于 GStreamer 标准插件目录
- 实验室环境存在可被 `gst-inspect-1.0` 正常识别的 Rockchip MPP 插件实现

## 4. 甲方设备的有效证据

甲方设备输出能够完整证明：当前板端只具备部分运行库，不具备项目所需的 GStreamer MPP 插件层；结合 `ffmpeg rkmpp` 缺失，可判断当前板端硬件加速链路并不完整。

### 4.1 OpenCV 已开启 GStreamer

命令：

```bash
python - <<'PY'
import cv2
for line in cv2.getBuildInformation().splitlines():
    if "GStreamer" in line:
        print(line)
PY
```

输出：

```text
GStreamer:                   YES (1.16.2)
```

结论：

- OpenCV 与 GStreamer 的基础对接已经存在
- 当前故障不能归因于 OpenCV 未启用 GStreamer

### 4.2 `mppvideodec` 不存在

命令：

```bash
gst-inspect-1.0 mppvideodec
```

输出：

```text
No such element or plugin 'mppvideodec'
```

命令：

```bash
GST_DEBUG=2 gst-inspect-1.0 mppvideodec
```

输出仍然是：

```text
No such element or plugin 'mppvideodec'
```

结论：

- 不是普通日志级别太低导致“看不到”
- 而是系统里确实没有可被 GStreamer 注册的 `mppvideodec` 元素

### 4.3 GStreamer 插件目录下没有 Rockchip MPP 插件文件

命令：

```bash
find /usr/lib /usr/lib/aarch64-linux-gnu /usr/local/lib -path "*gstreamer-1.0*" -type f | grep -Ei "rockchip|mpp"
```

输出为空。

这条证据非常关键，说明当前板端的 GStreamer 插件目录中没有 `libgstrockchipmpp.so` 或同类 Rockchip MPP 插件文件。

### 4.4 已有 MPP 运行库，但没有插件层

命令：

```bash
find /usr/lib /usr/lib/aarch64-linux-gnu /usr/local/lib -type f | grep -Ei "mpp|rockchip" 2>/dev/null
```

关键输出：

```text
/usr/lib/aarch64-linux-gnu/pkgconfig/rockchip_vpu.pc
/usr/lib/aarch64-linux-gnu/pkgconfig/rockchip_mpp.pc
/usr/lib/aarch64-linux-gnu/librockchip_mpp.so.0
/usr/lib/aarch64-linux-gnu/librockchip_vpu.so.0
```

命令：

```bash
ldconfig -p | grep -Ei "rockchip|mpp|gstreamer"
```

关键输出：

```text
librockchip_vpu.so.1 => /lib/aarch64-linux-gnu/librockchip_vpu.so.1
librockchip_mpp.so.1 => /lib/aarch64-linux-gnu/librockchip_mpp.so.1
libgstreamer-1.0.so.0 => /lib/aarch64-linux-gnu/libgstreamer-1.0.so.0
```

结论：

- MPP 运行库本身已经装了
- GStreamer 主库也装了
- 但两者之间负责“把 MPP 能力注册为 GStreamer 元素”的插件层缺失

### 4.5 甲方设备系统与已安装包现状

命令：

```bash
cat /etc/os-release
uname -a
dpkg -l | grep -Ei "gstreamer|rockchip|mpp|ffmpeg"
```

关键输出：

```text
PRETTY_NAME="Ubuntu 20.04.6 LTS"
Linux RK3588 5.10.226 ... aarch64 GNU/Linux
```

以及以下已安装项：

- `gstreamer1.0-libav`
- `gstreamer1.0-plugins-bad`
- `gstreamer1.0-plugins-base`
- `gstreamer1.0-plugins-good`
- `gstreamer1.0-tools`
- `librockchip-mpp-dev`
- `librockchip-mpp1`
- `librockchip-vpu0`
- `rockchip-mpp-demos`
- `ffmpeg`

但从已提供输出中，没有看到类似以下插件包已安装：

- `gstreamer1.0-rockchip1`
- `gstreamer-rockchip`
- 其他提供 `libgstrockchipmpp.so` 的同类包

结论：

- 甲方设备当前是 `Ubuntu 20.04.6 LTS + Linux 5.10.226`
- 已经装了通用 GStreamer、MPP 运行库和 demo
- 但没有装到项目实际需要的 Rockchip GStreamer MPP 插件包

### 4.6 `ffmpeg` 也未提供 `rkmpp`

命令：

```bash
ffmpeg -hide_banner -decoders | grep rkmpp
ffmpeg -hide_banner -encoders | grep rkmpp
```

输出为空。

这说明当前项目的 `FFmpeg` 优先硬解/硬编链路也无法命中，不只是 `GStreamer+mpp` 备用链路缺失。

### 4.7 最小硬解管线直接失败

命令：

```bash
gst-launch-1.0 -v \
  filesrc location="/home/hinlink/视频/黑夜.mp4" ! \
  qtdemux ! h264parse ! mppvideodec ! \
  videoconvert ! fpsdisplaysink video-sink=fakesink sync=false
```

输出：

```text
WARNING: erroneous pipeline: no element "mppvideodec"
```

结论：

- 当前板端无法执行项目第二备选 `GStreamer+mpp` 硬解链路
- 故障在 GStreamer 元素解析阶段已经发生，早于项目代码逻辑

## 5. 归因结论

综合两台设备输出，可将问题归因为：

1. 实验室设备存在 `libgstrockchipmpp.so`，因此 `mppvideodec` 可被 GStreamer 正常识别，至少可证明备用 `GStreamer+mpp` 链路可用。
2. 甲方设备仅安装了 `librockchip-mpp1` 等底层运行库，但未提供 Rockchip GStreamer MPP 插件文件。
3. 甲方设备的 `ffmpeg` 也未提供 `rkmpp` 编解码能力，因此当前代码无法命中 `FFmpeg` 优先硬解/硬编链路。
4. 所以甲方设备虽然“有 MPP 库”，但没有“项目当前真正可调用的硬件加速编解码能力”。
5. 这属于板端 BSP/多媒体环境交付不完整，不属于 Python 项目改代码能解决的问题。

## 6. 对板商/BSP 供应方的正式补齐要求

请板商针对当前甲方板端环境：

- `Ubuntu 20.04.6 LTS`
- `Linux 5.10.226`
- `aarch64`
- 当前 RK3588 BSP
- 当前 GStreamer `1.16.3`
- 当前 `librockchip-mpp1 1.5.0-1`

补齐与之匹配的 **`FFmpeg rkmpp` 能力** 和 **Rockchip GStreamer MPP 插件层**。

至少应满足以下交付要求：

- 提供支持 `rkmpp` 的 `ffmpeg`，至少要能识别 `h264_rkmpp`，最好同时覆盖常见 `rkmpp` 解码器
- 提供可安装的 Rockchip GStreamer MPP 插件包，安装后可用 `mppvideodec`
- 安装后在 GStreamer 插件目录下可见 `libgstrockchipmpp.so` 或功能等价插件文件
- 提供两套链路所需的依赖库、配置和安装说明
- 提供明确的软件版本对应关系：
  - 系统版本
  - BSP 版本
  - FFmpeg 版本
  - GStreamer 版本
  - MPP 版本
  - 插件包版本

推荐交付形式：

- 完整系统镜像
- 完整 rootfs / BSP 多媒体增量包
- 或完整 deb 包集合

不建议仅提供单个 `.so` 文件让现场手工拷贝，原因如下：

- 容易出现 ABI 不匹配
- 容易出现依赖缺失
- 插件即使复制到位，也可能无法注册
- 后续维护与复现困难

## 7. 建议板商一并补齐的增强项

以下不是当前主阻塞项，但建议同步处理：

- 对应版本的安装文档
- 最小验收命令和预期结果说明
- 若无法提供某项能力，请明确书面说明“不支持”和原因

这样可以避免现场继续在“插件缺失”和“ffmpeg 能力缺失”之间反复排查。

## 8. 板商交付后的验收标准

### 8.1 FFmpeg 能力验收

执行：

```bash
ffmpeg -hide_banner -decoders | grep rkmpp
ffmpeg -hide_banner -encoders | grep -E "rkmpp|v4l2m2m|omx"
```

通过标准：

- `ffmpeg -hide_banner -decoders` 能看到 `rkmpp` 相关解码器
- `ffmpeg -hide_banner -encoders` 至少能看到一个项目可用的 H.264 编码器，优先 `h264_rkmpp`

### 8.2 插件层验收

执行：

```bash
gst-inspect-1.0 mppvideodec
gst-inspect-1.0 | grep -Ei "mpp|rockchip"
find /usr/lib /usr/lib/aarch64-linux-gnu /usr/local/lib -path "*gstreamer-1.0*" -type f | grep -Ei "rockchip|mpp"
```

通过标准：

- `gst-inspect-1.0 mppvideodec` 能输出插件详情
- 能看到 Rockchip MPP 插件信息
- 能找到 `libgstrockchipmpp.so` 或同等作用插件文件

### 8.3 最小本地文件硬解验收

执行：

```bash
gst-launch-1.0 -v \
  filesrc location="/home/hinlink/视频/黑夜.mp4" ! \
  qtdemux ! h264parse ! mppvideodec ! \
  videoconvert ! fpsdisplaysink video-sink=fakesink sync=false
```

通过标准：

- 管线能启动
- 不再报 `no element "mppvideodec"`
- 能正常跑完或持续输出 FPS

### 8.4 项目内实际启用验收

启用：

```json
"video": {
  "hw_decode": true
}
```

项目日志应出现以下任一内容：

- `[reader] Using FFmpeg hardware decoder ...`
- `[reader] Using GStreamer+mpp file pipeline for ...`
- `[reader] Using GStreamer+mpp RTSP TCP pipeline for ...`

若未出现，视为板端环境仍未真正满足项目所需硬解链路。

## 9. 当前交付阶段的临时处理口径

在板商补齐环境之前，项目部署建议继续保持：

```json
"video": {
  "hw_decode": false
}
```

原因不是项目代码不支持，而是当前甲方板端同时缺少 `FFmpeg rkmpp` 与 Rockchip GStreamer MPP 插件层。

## 10. 可直接发送给板商的简版说明

> 2026-03-26 现场对比结果显示：甲方 RK3588 设备虽然已安装 `librockchip-mpp1`、`librockchip-mpp-dev`、`librockchip-vpu0`，且 OpenCV 已支持 GStreamer，但 `gst-inspect-1.0 mppvideodec` 返回 `No such element or plugin 'mppvideodec'`，GStreamer 插件目录下也未找到 Rockchip MPP 插件文件，最小硬解命令 `gst-launch-1.0 ... ! mppvideodec ! ...` 直接失败；同时 `ffmpeg -hide_banner -decoders | grep rkmpp` 与 `ffmpeg -hide_banner -encoders | grep rkmpp` 也未搜到可用项。请提供与当前 `Ubuntu 20.04.6 + Linux 5.10.226 + GStreamer 1.16.3 + RK3588 BSP` 匹配的 `FFmpeg rkmpp` 能力和 Rockchip GStreamer MPP 插件包或完整多媒体环境，至少补齐 `h264_rkmpp` / `mppvideodec` 及其依赖，并确保现场命令能够通过验收。

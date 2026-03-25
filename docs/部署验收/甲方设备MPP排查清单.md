# 甲方设备 MPP 排查清单

本文档用于在甲方设备上排查“为什么项目无法走 `GStreamer + mpp` 硬解”。

## 1. 已知前提

当前已确认的信息：

- 实验室设备已经成功走过项目内置的 MPP 硬解链路
- 成功日志样例为：
  - `[reader] Using GStreamer+mpp file pipeline for /home/hinlink/视频/黑夜.mp4`
- 实验室设备的 OpenCV 已确认支持 `GStreamer`
- 实验室设备已确认存在 `mppvideodec` 插件
- 实验室设备已确认存在 `rockchip mpp` 相关系统库
- 实验室设备的 `ffmpeg` 已确认存在 `rkmpp` 编解码器
- **甲方提供的媒体已确认是 `H.264`**

因此，本轮在甲方设备排查时：

- **可以先排除“媒体本身不是 H.264”这个方向**
- 排查重点转为：
  - OpenCV 是否支持 `GStreamer`
  - GStreamer 是否存在 `mppvideodec`
  - 系统是否安装 Rockchip MPP 相关库
  - OpenCV + GStreamer + mpp 插件三者组合是否真的能跑通

## 2. 当前项目里的 MPP 硬解入口

项目里已经存在 MPP 硬解入口，不需要先改代码。

相关落点：

- `cleaningcar/cli.py:29`
  - `--hw_decode`
- `cleaningcar/runtime_config.py:85`
  - 从 `video.hw_decode` 读取配置
- `web/templates/zone_editor.html:98`
  - Web 面板里的“启用硬件解码 (GStreamer+mpp)”开关
- `cleaningcar/video_io.py:206`
  - 硬解视频读取入口
- `cleaningcar/video_io.py:212`
  - RTSP H.264 管线：`rtph264depay ! h264parse ! mppvideodec`
- `cleaningcar/video_io.py:218`
  - 本地文件管线：`qtdemux ! h264parse ! mppvideodec`

默认配置当前是关闭的：

- `configs/config.json:16`
  - `"hw_decode": false`

## 3. 现场排查顺序

建议严格按下面顺序执行，不要跳步。

### 第 1 步：激活环境

```bash
source venv-gst/bin/activate
```

若 `venv-gst` 不存在或失效，先执行：

```bash
./install_runtime_venv.sh
```

## 第 2 步：检查 OpenCV 是否支持 GStreamer

执行：

```bash
python - <<'PY'
import cv2
for line in cv2.getBuildInformation().splitlines():
    if "GStreamer" in line:
        print(line)
PY
```

期望输出：

- `GStreamer: YES`

### 结果判断

- 若为 `YES`
  - 继续下一步
- 若不是 `YES`
  - 当前项目内置 `cv2.CAP_GSTREAMER` 硬解链路无法工作

### 处理建议

- 优先对齐实验室设备与甲方设备的 OpenCV 来源和版本
- 不建议先改 Python 代码

## 第 3 步：检查 GStreamer 是否存在 mpp 插件

执行：

```bash
gst-inspect-1.0 mppvideodec
gst-inspect-1.0 | grep -Ei "mpp|rockchip"
```

期望输出：

- 能找到 `mppvideodec`
- 最好能看到 `libgstrockchipmpp.so`

### 结果判断

- 若 `mppvideodec` 存在
  - 继续下一步
- 若不存在
  - 说明甲方设备没有装 Rockchip GStreamer MPP 插件

### 处理建议

- 优先补齐 Rockchip GStreamer MPP 插件
- 最稳妥方案是直接对齐实验室设备的系统镜像或多媒体环境

## 第 4 步：检查系统是否存在 Rockchip MPP 相关库

执行：

```bash
find /usr/lib /usr/lib/aarch64-linux-gnu /usr/local/lib -type f | grep -Ei "mpp|rockchip" 2>/dev/null
```

实验室设备已知可参考的关键项：

- `libgstrockchipmpp.so`
- `librockchip_mpp.so.0`
- `rockchip_mpp.pc`

### 结果判断

- 若这些关键库基本存在
  - 继续下一步
- 若明显缺失
  - 说明问题在系统环境，不在项目 Python 层

### 处理建议

- 优先统一实验室设备与甲方设备的系统环境
- 不建议手工零散拷贝 so，除非两边镜像版本高度一致

## 第 5 步：检查 ffmpeg 是否存在 rkmpp

执行：

```bash
ffmpeg -hide_banner -decoders | grep rkmpp
ffmpeg -hide_banner -encoders | grep rkmpp
```

说明：

- 这一步不是当前项目“解码侧”必须条件
- 但对后续 `per_id` 硬编和更完整的硬件化非常重要

### 结果判断

- 若存在 `h264_rkmpp` / `hevc_rkmpp`
  - 说明系统的 ffmpeg 也具备 Rockchip 编解码能力
- 若不存在
  - 当前先不影响 `GStreamer+mppvideodec` 的最小解码验证

## 第 6 步：先用 GStreamer 独立验证本地 H.264 文件

建议优先使用实验室已经成功的同款文件做对比验证。

执行：

```bash
gst-launch-1.0 -v \
  filesrc location="/home/hinlink/视频/黑夜.mp4" ! \
  qtdemux ! h264parse ! mppvideodec ! \
  videoconvert ! fpsdisplaysink video-sink=fakesink sync=false
```

### 结果判断

- 若成功
  - 说明 GStreamer + mpp 插件 + 本地 H.264 文件路径是通的
- 若失败
  - 优先判断：
    - 文件路径问题
    - `mppvideodec` 问题
    - `qtdemux` / `h264parse` 问题

## 第 7 步：再用 GStreamer 独立验证 RTSP

执行：

```bash
gst-launch-1.0 -v \
  rtspsrc location="你的RTSP地址" latency=200 protocols=tcp ! \
  rtph264depay ! h264parse ! mppvideodec ! \
  videoconvert ! fpsdisplaysink video-sink=fakesink sync=false
```

说明：

- 当前已知甲方媒体是 `H.264`
- 因此如果这里失败，重点不再是 codec，而是：
  - RTSP 鉴权/地址问题
  - GStreamer RTSP 兼容问题
  - mpp 解码插件问题

### 结果判断

- 若本地文件成功、RTSP失败
  - 重点排查 RTSP 链路本身
- 若本地文件和 RTSP 都失败
  - 重点排查系统多媒体环境

## 第 8 步：用 OpenCV 单独验证项目同款硬解管线

### 本地文件验证

```bash
python - <<'PY'
import cv2
src="/home/hinlink/视频/黑夜.mp4"
pipe = (
    f'filesrc location="{src}" ! '
    'qtdemux ! h264parse ! mppvideodec ! '
    'videoconvert ! video/x-raw,format=BGR ! appsink sync=false drop=true'
)
cap = cv2.VideoCapture(pipe, cv2.CAP_GSTREAMER)
print("opened =", cap.isOpened())
ret, frame = cap.read()
print("ret =", ret)
print("shape =", None if frame is None else frame.shape)
cap.release()
PY
```

### RTSP 验证

```bash
python - <<'PY'
import cv2
src="你的RTSP地址"
pipe = (
    f'rtspsrc location="{src}" latency=200 protocols=tcp ! '
    'rtph264depay ! h264parse ! mppvideodec ! '
    'videoconvert ! video/x-raw,format=BGR ! appsink sync=false drop=true'
)
cap = cv2.VideoCapture(pipe, cv2.CAP_GSTREAMER)
print("opened =", cap.isOpened())
ret, frame = cap.read()
print("ret =", ret)
print("shape =", None if frame is None else frame.shape)
cap.release()
PY
```

### 结果判断

- 若 `gst-launch` 成功，但 OpenCV 失败
  - 问题大概率在 OpenCV 和 GStreamer 的对接层
- 若 OpenCV 也成功
  - 继续下一步项目内验证

## 第 9 步：在项目里启用 `hw_decode`

有 3 种方式：

- 改配置：`configs/config.json`
  - 把 `"hw_decode": true`
- Web 页面勾选“启用硬件解码 (GStreamer+mpp)”
- 直接命令行强制：

```bash
python run_zone_detect.py \
  --config configs/config.json \
  --video "/home/hinlink/视频/黑夜.mp4" \
  --hw_decode \
  --limit 200
```

## 第 10 步：看项目日志判断是否真的走了 MPP

### 成功走 MPP 的标志

日志里出现：

- `[reader] Using GStreamer+mpp file pipeline for ...`
- 或 `[reader] Using GStreamer+mpp RTSP TCP pipeline for ...`

### 未走 MPP 的标志

日志里出现：

- `[reader] Using OpenCV FFmpeg RTSP TCP capture for ...`

### 硬解尝试失败的标志

日志里出现：

- `硬解模式下 GStreamer+mpp 解码管道创建失败`

## 4. 问题归类与处理建议

### 情况 A：OpenCV 没有 GStreamer

现象：

- 第 2 步不是 `GStreamer: YES`

处理：

- 对齐实验室设备和甲方设备的 OpenCV 环境
- 不先改项目代码

### 情况 B：没有 `mppvideodec`

现象：

- 第 3 步失败

处理：

- 补齐 Rockchip GStreamer MPP 插件
- 最稳做法是统一系统镜像或多媒体环境

### 情况 C：本地文件能跑，RTSP 不行

现象：

- 第 6 步成功
- 第 7 步失败

已知：

- 甲方媒体已确认是 `H.264`

处理重点：

- RTSP 地址、鉴权、网络连通性
- GStreamer RTSP 链路兼容性
- `rtspsrc` / `rtph264depay` 的实际行为

### 情况 D：`gst-launch` 成功，但项目失败

现象：

- 第 6/7 步成功
- 第 8 或第 9/10 步失败

处理：

- 优先对齐 OpenCV、GStreamer、Rockchip 插件版本
- 再看 OpenCV 是否能正确吃下 `appsink`

## 5. 当前最推荐的现场执行顺序

1. 激活环境
2. 检查 OpenCV 的 GStreamer 支持
3. 检查 `mppvideodec`
4. 检查系统库
5. 跑本地 H.264 文件的 `gst-launch`
6. 跑 RTSP 的 `gst-launch`
7. 跑 OpenCV 同款管线
8. 最后再开项目里的 `hw_decode`

## 6. 现场建议

- 优先先用实验室已成功的本地 H.264 文件比对
- 不要一上来就只测 RTSP
- 若甲方设备失败，先判断“系统环境差异”，再考虑项目代码
- 第一次排查阶段，不建议先改项目硬解代码

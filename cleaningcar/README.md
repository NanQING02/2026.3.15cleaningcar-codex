# cleaningcar 模块说明

`cleaningcar/` 是项目的主运行包，负责检测、车牌识别、跟踪、事件判定、运行信号和主流程编排。

## 模块职责

- `constants.py`
  - 定义类别列表、类别阈值、颜色映射、车牌相关常量
  - 这是检测结果解释和业务判定的基础常量层

- `runtime_config.py`
  - 负责加载 `configs/*.json`
  - 负责把配置内容映射到运行时可直接使用的结构
  - 负责把 CLI 参数覆盖到配置上

- `video_io.py`
  - 负责打开视频源
  - 负责 RTSP / 本地视频输入识别
  - 负责 FFmpeg H.264 输出和运行时路径解析

- `vision.py`
  - 提供几何计算辅助
  - 包括 IoU、点位缩放、anchor 计算等

- `fp_detect.py`
  - 负责 FP 检测模型后处理
  - 现在支持两种模式：
    - `6` 输出模式：只用 `box + class`
    - `9` 输出模式：使用 `box + class + score`
  - 这是检测模型推理结果和业务主流程之间的解耦层

- `plate.py`
  - 负责车牌文本规范化和车牌跟踪辅助

- `plate_lpr.py`
  - 负责双模型车牌识别链路
  - 包括车牌检测、字符识别、颜色识别

- `tracking.py`
  - 负责车辆级目标跟踪

- `events.py`
  - 负责事件状态机、事件生成、截图保存、事件上报

- `worker.py`
  - 负责 RKNN worker 线程
  - 每个 worker 完成：
    - 检测模型推理
    - 检测结果后处理
    - 双模型车牌识别

- `monitoring.py`
  - 负责资源监控日志
  - 使用 `psutil` 输出 CPU、内存、温度等信息

- `runtime_signals.py`
  - 负责运行期信号输出
  - 包括：
    - 心跳文件
    - 启动信号
    - 启动截图
    - 手动截图命令

- `pipeline.py`
  - 负责主流程编排
  - 把读流、worker、跟踪、事件、截图、心跳、命令处理串起来
  - 这是 `cleaningcar/` 里的总调度层

- `cli.py`
  - 负责命令行入口参数
  - 负责把配置和运行参数组装后交给 `pipeline.py`

## 主调用关系

大致调用顺序如下：

1. `run_zone_detect.py`
2. `cleaningcar/cli.py`
3. `cleaningcar/pipeline.py`
4. `cleaningcar/worker.py`
5. `cleaningcar/fp_detect.py` + `cleaningcar/plate_lpr.py`
6. `cleaningcar/tracking.py` + `cleaningcar/events.py`
7. `cleaningcar/runtime_signals.py`

## 当前建议阅读顺序

如果要理解主链路，建议按这个顺序读：

1. `cli.py`
2. `pipeline.py`
3. `worker.py`
4. `fp_detect.py`
5. `plate_lpr.py`
6. `events.py`
7. `runtime_signals.py`

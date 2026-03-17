# 项目解耦报告

## 目标

在不改变现有功能、入口方式、配置格式、HTTP 路由和事件输出行为的前提下，拆解项目内两个主要单体文件：

- `run_zone_detect.py`
- `web/server.py`

## 解耦结果

### 1. 检测主链路已拆成独立包 `cleaningcar/`

原 `run_zone_detect.py` 为约 3088 行单体脚本，现已改为纯入口文件，只负责启动：

- `run_zone_detect.py`

核心逻辑拆分如下：

- `cleaningcar/constants.py`
  - 检测类别、阈值、车牌字符集、颜色映射、本地化标签、全局常量
- `cleaningcar/runtime_config.py`
  - 配置加载、配置合并、CLI 覆盖、类别阈值覆盖
- `cleaningcar/video_io.py`
  - FFmpeg H.264 写入器、视频源打开、源类型识别、运行时路径解析、存储目录收集
- `cleaningcar/vision.py`
  - ROI 缩放、anchor 计算、letterbox、NMS、框缩放、IoU 等通用视觉算法
- `cleaningcar/plate.py`
  - 车牌裁剪、增强、LPR 解码、车牌文本规范化、车牌跟踪
- `cleaningcar/tracking.py`
  - 车辆 ByteTrack 跟踪逻辑
- `cleaningcar/events.py`
  - 事件上报、事件状态机、抓拍构建、生命周期事件输出
- `cleaningcar/worker.py`
  - RKNN 检测 worker、车牌识别 worker
- `cleaningcar/monitoring.py`
  - 资源监控与性能日志
- `cleaningcar/pipeline.py`
  - 视频处理总编排、轨迹关联、分车视频输出、调试帧输出
- `cleaningcar/cli.py`
  - 命令行参数、批处理入口、磁盘清理装配

### 2. Web 控制层已拆成状态 / 服务 / 路由分层

原 `web/server.py` 为约 946 行单体文件，现拆为：

- `web/state.py`
  - Web 运行时共享状态、配置路径、模板路径、脚本路径、帧缓存
- `web/models.py`
  - Pydantic 请求模型
- `web/inference.py`
  - 推理守护进程管理、自动重启、日志缓存、默认实例管理
- `web/helpers.py`
  - 配置加载、路径解析、日志尾部读取、调试帧抓取、配置文件枚举
- `web/server.py`
  - 仅保留 FastAPI 应用装配和路由定义

## 当前模块分层

### 基础层

- `config_manager.py`
- `zone_manager.py`
- `utils/`
- `cleaningcar/constants.py`
- `cleaningcar/vision.py`
- `cleaningcar/video_io.py`

### 领域层

- `cleaningcar/plate.py`
- `cleaningcar/tracking.py`
- `cleaningcar/events.py`
- `cleaningcar/worker.py`

### 编排层

- `cleaningcar/pipeline.py`
- `cleaningcar/cli.py`
- `web/inference.py`
- `web/helpers.py`
- `web/server.py`

## 保持不变的外部行为

- `run_zone_detect.py` 仍然是主检测入口
- `web/server.py` 仍然是 FastAPI 入口
- CLI 参数名保持不变
- `config.json` 结构保持不变
- 事件类型、事件文件输出、上报逻辑保持不变
- Web 路由地址保持不变
- 前端模板和静态资源访问路径保持不变

## 校验结果

已对以下文件执行 `py_compile` 静态编译校验，全部通过：

- `run_zone_detect.py`
- `cleaningcar/*.py`
- `web/state.py`
- `web/models.py`
- `web/inference.py`
- `web/helpers.py`
- `web/server.py`

## 维护收益

- 主入口变成装配层，便于后续替换单个模块
- 检测、跟踪、事件、推流、Web 守护相互隔离，修改范围可控
- 后续如果需要继续细分，可直接在 `cleaningcar/events.py` 和 `cleaningcar/pipeline.py` 内继续按职责拆分，不会再影响入口层

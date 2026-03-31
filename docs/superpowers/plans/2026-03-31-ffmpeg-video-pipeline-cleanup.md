# FFmpeg Video Pipeline Cleanup Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 将视频编解码优先级收口为 FFmpeg 硬解/硬编优先，GStreamer 硬件链路次选，软件链路兜底，并彻底移除全局视频保存残留。

**Architecture:** 解码侧在 `cleaningcar/video_io.py` 中统一实现 reader 工厂和回退元数据，主流程只消费抽象 capture 接口。编码侧在 `cleaningcar/video_io.py` 中统一实现 per-id writer 工厂，`cleaningcar/pipeline.py` 只负责按时机创建与关闭 writer。全局视频保存相关 CLI、配置与 Web 面板字段一并删除。

**Tech Stack:** Python, OpenCV, FFmpeg CLI, GStreamer, unittest/pytest

---

### Task 1: 解码优先级

**Files:**
- Modify: `cleaningcar/video_io.py`
- Modify: `tests/test_video_io_decode_fallback.py`

- [ ] Step 1: 先写失败测试，覆盖 FFmpeg 硬解优先、GStreamer 硬解次选、软解兜底
- [ ] Step 2: 跑测试确认按当前实现失败
- [ ] Step 3: 实现 reader 工厂和回退元数据
- [ ] Step 4: 跑测试确认通过

### Task 2: per-id 编码 fallback

**Files:**
- Modify: `cleaningcar/video_io.py`
- Add: `tests/test_video_io_writer_fallback.py`

- [ ] Step 1: 先写失败测试，覆盖 FFmpeg 硬编、GStreamer 硬编、FFmpeg 软编三级 fallback
- [ ] Step 2: 跑测试确认失败
- [ ] Step 3: 实现 writer 工厂并保持 per-id 行为不变
- [ ] Step 4: 跑测试确认通过

### Task 3: 删除全局视频保存残留

**Files:**
- Modify: `cleaningcar/cli.py`
- Modify: `cleaningcar/runtime_config.py`
- Modify: `cleaningcar/pipeline.py`
- Modify: `config_manager.py`
- Modify: `web/config_tiers.py`
- Modify: `configs/config.json`
- Modify: `configs/config_绕行.json`
- Add: `tests/test_config_global_video_cleanup.py`

- [ ] Step 1: 先写失败测试，确认全局视频保存字段不再暴露
- [ ] Step 2: 跑测试确认失败
- [ ] Step 3: 删除 CLI、运行时配置、默认配置和 Web 面板残留
- [ ] Step 4: 跑测试确认通过

### Task 4: 回归验证

**Files:**
- Verify only

- [ ] Step 1: 运行本次改动相关测试集合
- [ ] Step 2: 核对两份配置文件差异并整理结论

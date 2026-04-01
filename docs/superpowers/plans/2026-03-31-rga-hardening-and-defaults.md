# RGA Hardening And Safer Defaults Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 加固 RGA resize 路径，避免小 ROI 和并发场景触发不稳定调用，并把默认运行入口切到更稳的非 RGA 路径。

**Architecture:** 在 `future_modules/acceleration/rga_resize_plugin.py` 内集中做 RGA 可用性判定、输入校验、并发串行化和诊断日志，保证 `resize_bgr()` 的调用点不需要理解底层约束。再在 `cleaningcar/cli.py` 按配置尽早设置环境变量，确保默认运行时在导入 `pipeline.py` 之前就选中稳定路径，同时在 `plate_lpr.py` 增加极小 ROI 保护，避免把无意义样本继续送入识别链路。

**Tech Stack:** Python, OpenCV, unittest/pytest

---

### Task 1: 锁住回归测试

**Files:**
- Modify: `tests/test_rga_resize_plugin.py`
- Modify: `tests/test_plate_lpr_color_conf.py`
- Add: `tests/test_cli_rga_runtime.py`
- Modify: `tests/test_config_global_video_cleanup.py`

- [ ] Step 1: 先写失败测试，覆盖小尺寸 resize fallback、RGA 锁、CLI 默认 RGA 开关和极小 ROI 跳过识别
- [ ] Step 2: 跑定向测试确认当前实现失败

### Task 2: 加固 RGA 插件

**Files:**
- Modify: `future_modules/acceleration/rga_resize_plugin.py`
- Modify: `cleaningcar/resize_accel.py`

- [ ] Step 1: 增加输入校验和小尺寸 fallback 判定
- [ ] Step 2: 增加进程内全局锁和受限日志
- [ ] Step 3: 跑定向测试确认插件行为通过

### Task 3: 切换默认运行策略并收紧 ROI

**Files:**
- Modify: `cleaningcar/cli.py`
- Modify: `cleaningcar/plate_lpr.py`
- Modify: `config_manager.py`
- Modify: `web/config_tiers.py`
- Modify: `configs/config.json`
- Modify: `configs/config_绕行.json`
- Modify: `README.md`

- [ ] Step 1: 在导入 `pipeline.py` 前按配置设置 RGA 环境变量
- [ ] Step 2: 给极小 plate ROI 增加保护
- [ ] Step 3: 更新默认配置和说明文档

### Task 4: 验证

**Files:**
- Verify only

- [ ] Step 1: 运行 RGA/CLI/plate 相关定向测试
- [ ] Step 2: 运行全量测试
- [ ] Step 3: 查看 diff，整理实际改动和预期效果

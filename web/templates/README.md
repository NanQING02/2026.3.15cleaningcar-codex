# web/templates 目录说明

这个目录存放 Web 页面模板。

## 当前文件

- `zone_editor.html`
  - 当前 Web 控制台主页面
  - 包含配置加载、ROI 编辑、推理控制、日志查看等页面逻辑

## 使用方式

- 由 `web/server.py` 加载
- 如果访问首页返回模板缺失，优先检查这个目录和文件是否存在

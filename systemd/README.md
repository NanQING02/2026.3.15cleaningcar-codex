# systemd 目录说明

这个目录存放项目的 `systemd` 服务模板。

## 当前文件

- `cleaningcar-web.service`
  - Web 服务单元模板
  - 由 `install_cleaningcar_systemd.sh` 注入项目路径、配置路径、用户和端口后安装到系统

## 使用方式

```bash
./install_cleaningcar_systemd.sh
systemctl status cleaningcar-web --no-pager
```

说明：

- 服务只负责拉起 Web
- 不会在开机时自动启动推理

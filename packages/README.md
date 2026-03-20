# packages 目录说明

这个目录存放板端运行所需的 RKNN / RGA 依赖包和动态库。

## 当前内容

- `rknn_toolkit_lite*.whl`
  - RKNN Lite Python 包
- `librknnrt.so`
  - RKNN 运行库
- `im2d.h`
  - RGA 头文件
- `packages.md5sum`
  - 包文件校验信息

## 使用方式

- `install_runtime_venv.sh` 会优先从这里安装或复制依赖
- 板端交付时建议整个目录一起带上

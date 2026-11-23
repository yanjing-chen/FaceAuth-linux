# 源码来源与边界

- stable：来自 `FaceAuth-Rebuild-v0.1.8-Full-Source.zip`。其 `src/daemon/src/main.rs` 与 `FaceAuth-Rebuild-v0.1.6-Full-Source.zip` 完全一致；v0.1.8 使用不同的 crate 版本号，重新构建的二进制**不可能以文件字节**等同于正在系统中运行的 v0.1.6 已编译二进制。
- experimental/ir-rc2：来自 `FaceAuth-Rebuild-v0.1.9-IR-RC2-Full-Source.zip`。不与 stable 共享构建目录，保持完整源码。未夹带以前所有阶段的重复压缩包。
- 仅整理目录和文档；两个完整源码树的原有源码、配置、程序脚本、测试用例未被修改。
- 已剔除编译产物、Python 缓存、照片、账户模板与模型权重。请通过原版模型导入脚本单独获取合法的模型文件。

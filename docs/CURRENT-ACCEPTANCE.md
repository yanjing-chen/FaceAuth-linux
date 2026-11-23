# 2026-11-23 当前验收记录

## 已验收的正式系统
- 生产 daemon 为 v0.1.6，已恢复此前的备份，两个文件 SHA256 相同：`6e56522ad0e74405565e2975ff2eb58b84eb7b80248078091a07e48646cde486`（用户当时机器上的二进制哈希，仅用于记录，不用于验证本 ZIP 的源码）。
- `faceauth-rebuild-cli LIST chen`：`OK 1 我`。
- `faceauth-rebuild-cli TEST chen`：连续三次 `accepted`，相似度 0.853、0.853、0.818。
- 以 root 运行 `pamtester -v gdm-password chen authenticate` 和 `pamtester -v polkit-1 chen authenticate`：两者均无需密码成功。GUI 实际 GDM、polkit 代理仍待单独验收。
- 之前 sudo 已验证人脸成功和密码回退；RC2 安装失败时也能回退密码。

## 实验 RC2 状态
- Python 63 项、Rust core 12 项和 daemon 6 项测试在用户 Ubuntu 上通过，Rust 构建通过。
- 隔离开发版 10/10，`ir_passes=2/2`、`ir_dark=0`。
- 生产 daemon 替换以后：`TEST chen` 为 `IGNORE unavailable-or-processing-error`，未能免密 sudo；回滚以后正常。
- 原始 IR 连续 30 帧：检出 15/30，诊断用对比度增强后 30/30；极暗帧全帧亮度均值约 5.6，不能据此宣称活体有效。

## 安全边界
- 不修改 `/etc/pam.d/common-auth` 或 `other`；不自动修改 sudo、GDM、polkit、su、login 的场景挂载。
- 真实登录/锁屏图形验收尚未由本记录覆盖。
- 不保存或附带人脸模板、拍摄图像、生产密钥及设备个人配置。
- v0.1.9 RC2 不可作为生产稳定版发布。

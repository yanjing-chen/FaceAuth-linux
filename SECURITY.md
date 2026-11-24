# FaceAuth Linux 0.1.6 安全说明

## 已实现的防护

- PAM 模块纯 Rust，没有 Python、GUI、exec 或网络，无法连接 daemon/超时/识别不成功一律 PAM_IGNORE。
- 管理员和用户分权：普通 GUI 只能为自己录入/删除，系统 PAM 场景必须额外通过系统 polkit 管理员交互授权。
- 管理授权绑定 Unix SO_PEERCRED 身份 `(pid,uid)` 和 `/proc` 开始时间，避免客户端自己伪造身份；`pkcheck` 授权失败不修改文件。GUI 绝不 pkexec/root。
- 真实挂载仅限 sudo/gdm/su/login/polkit，不访问 common-auth/other；`[success=done default=ignore]` 后保留现有 `@include common-auth` 回退。
- 修改前 root-only 备份，原子替换且严查已经变化的 PAM 管理块。支持 daemon 离线撤销，停用失败时不允许卸载 PAM 模块。
- 系统新服务与旧 FaceAuth 彻底隔离；系统安装 **不自动挂载** 真实 sudo/gdm/polkit 等场景。独立 PAM 测试栈无其它服务引用。
- FaceAuth 人脸模板文件为 0600，目录 0700，只保存 128 维 float32 特征及标签，不保存原始照片；帧通过 Unix socket/内存 JPEG 呈现。
- root daemon 仅执行受信任安装路径中的脚本与模型，忽略用户配置提供的 `infer_program`。

## 仍未完成的发布级安全事项

- **没有**完成 Ubuntu 26.10/GNOME 51 的真实 PAM、GDM、polkit 综合验收，不能宣称登录安全已验证；Linux 发行版可能使用自定义 PAM 结构，缺少 common-auth 会拒绝修改。
- IR liveness 仍是启发式；双摄像头 RGB 与 IR 的人脸并未进行跨模态实体绑定，可能遭受替换/重放攻击。**不能称为 Windows Hello 同等级**。
- 仅保留 RGB/IR 图像通道。旧版不可用的第三设备/深度选项已移除；旧用户配置由普通用户 GUI 迁移并私有备份。
- 生产代码仍使用 root Python + OpenCV 子进程，依赖其第三方库与 ONNX 模型的安全性；模型 sha256 与供应链审核仍须在发布前完成。
- 当前 Unix socket 限流、worker 阻塞及 PAM 超时的实机压力测试尚不足；错误应返回 IGNORE 回退密码，但不能保证所有桌面代理的 GUI 交互行为一致。
- PAM 管理器只处理它自己插入的标记块；若其他程序/管理员改动冲突文件，会拒绝覆盖，需人工恢复备份。

## 恢复命令

即使守护进程崩溃仍可：

```
sudo /usr/libexec/faceauth-linux/faceauth-scenes off sudo
sudo /usr/libexec/faceauth-linux/faceauth-scenes off gdm
sudo /usr/libexec/faceauth-linux/faceauth-scenes off polkit
```

详见 README。部署之前务必保持 root shell 与密码凭据可用。

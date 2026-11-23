# FaceAuth Rebuild v0.1.8 — GTK4 设置页双栏重设计

**本版为 GUI 更新，认证内核不变。** 用户已在 Ubuntu 26.10/GNOME 51 实机验证 v0.1.6 正式服务的双模 TEST、root AUTH、独立 pamtester，以及实际 sudo 成功刷脸与密码回退。**无需再次安装或卸载系统服务，也不能用本版源码执行系统重装。**

## 本轮界面设计

- 录入/测试：左右两栏，实时摄像头与跟踪框占主视图；右侧紧凑展示名称、录入、识别、进度和结果。
- 设置/管理：采用与首页一致的左右双栏。左侧「识别方式 + 摄像头」，右侧「已录人脸 + 系统认证」。
- 设备名称在选择按钮上简写、完整名称及稳定路径在宽弹出菜单和悬浮提示中保留。
- 系统认证开关不变，原来每行重复的权限说明合并到分组说明；右侧独立展示五个开关。
- 页面顶部直接刷新状态，底部仅保留一条 IR 活体安全提示；移除底部多余操作区。
- 继续使用原生 Wayland + Cairo、后台 IPC 预览及原有动态跟踪角框。
- 录入成功或失败、识别测试通过或失败都会弹出 GNOME 原生 AlertDialog。

## 使用现有 v0.1.6 已安装的服务

```bash
cd ~/下载
unzip -oq FaceAuth-Rebuild-v0.1.8-Full-Source.zip
cd FaceAuth-Rebuild-v0.1.8
# 先关闭旧 GUI；运行时不使用 sudo
bash scripts/launch-gui.sh
```

不需要 `cargo build`、`install-system.sh`、`uninstall-system.sh`、复制模型或重新录入。GUI 会读取已有用户配置，并连接 `/run/faceauth-rebuild/daemon.sock`。

## 安全边界

- 不改变已经在用户电脑上生效的 sudo PAM 配置或回滚工具。
- 保留 libadwaita 的系统标题栏、系统配色，窗口不能以 root 运行。
- 双模 IR 活体为实验性启发式，不等同 Windows Hello 的硬件级防伪。
- 系统认证场景具有安全风险，真实 GDM/polkit 需逐项验证。

源代码 Rust 部分沿用 v0.1.6，版本标识统一为 0.1.8，但无须重新编译。新界面当前无法在本容器运行真实 GNOME 51 渲染，需实机确认。

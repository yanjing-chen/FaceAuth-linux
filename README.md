# FaceAuth Rebuild

[![build](https://github.com/yanjing-chen/FaceAuth-linux/actions/workflows/build.yml/badge.svg)](https://github.com/yanjing-chen/FaceAuth-linux/actions/workflows/build.yml)

Linux 下的双摄人脸识别认证：RGB 摄像头做身份识别、IR 摄像头做活体辅助判断，通过 PAM 接入 `sudo` / GDM / `polkit` 等场景，失败时保留系统密码回退。提供 GTK4 / libadwaita 图形界面管理摄像头、人脸模板与认证场景。

> 试验性项目。IR 活体是启发式辅助判断，不等同 Windows Hello 的硬件级防伪，不能替代系统密码。

## 安装（一条命令）

Ubuntu / Debian 系发行版，从源码构建、下载模型并安装系统服务：

```bash
git clone https://github.com/yanjing-chen/FaceAuth-linux.git && cd FaceAuth-linux && sudo apt update && sudo apt install -y cargo rustc curl libpam0g-dev python3-opencv python3-numpy python3-gi gir1.2-gtk-4.0 gir1.2-adw-1 libadwaita-1-0 && bash scripts/fetch-models.sh && cargo build --release --workspace && sudo bash scripts/install-system.sh
```

命令依次完成：克隆 → 装依赖 → 下载 OpenCV Zoo 模型（YuNet + SFace，约 38 MB）→ 编译 Rust 组件 → 安装 systemd 服务、PAM 模块、polkit 策略与 CLI。

安装完成后服务 `faceauth-rebuild.service` 已启用，但**默认不挂载任何 PAM 场景**，需在 GUI 里逐项启用。

> 已有旧版服务时，`install-system.sh` 会检测到现存的 service 文件并拒绝覆盖（防止误重装）。升级请先 `sudo bash scripts/uninstall-system.sh`。

## 首次使用

以普通用户启动 GUI（**禁止 sudo 运行**，否则 Wayland 环境变量丢失、取景框白屏）：

```bash
bash scripts/launch-gui.sh
```

1. 在「摄像头」里选 RGB 与 IR 设备（优先 `/dev/v4l/by-path/` 稳定路径），点「检查两路」确认彩色/红外角色正确；
2. 录入人脸：先采集 5 帧，再弹 polkit 框授权写入；
3. 用「识别测试」确认通过率；
4. 在「系统认证」组按需启用 sudo / GDM / polkit 场景，每项都会弹管理员授权框。

删除人脸需先现场刷脸验证本人，再经 polkit 授权，防止他人在你的会话里偷偷删除模板。

## 命令行

```bash
faceauth-rebuild-cli PING
faceauth-rebuild-cli LIST <用户名>
faceauth-rebuild-cli TEST <用户名>
faceauth-rebuild-cli SCENE_LIST <用户名>
sudo /usr/libexec/faceauth-rebuild/faceauth-scenes on|off sudo|gdm|su|login|polkit
```

## 可调设置

GUI「设置 / 管理 → 识别策略」提供识别阈值、识别帧数、暗帧判定阈值、认证时间预算、活体严格度等数值项，每项都标注调高/调低的含义。修改后建议重新执行识别测试。

## 安全说明

- 模板只存特征向量、不存原始照片；仅本人/daemon 可读。
- 录入与删除均需 polkit 管理员授权；删除额外要求现场刷脸。
- SSH / 远程会话默认禁用刷脸、回退密码；可在「系统认证」组开启。
- 所有场景失败都回退系统密码，不会因刷脸失败把用户锁在外面。
- 改 PAM 配置需管理员授权；回滚用 `faceauth-scenes off <场景>`。

详见 `SECURITY.md`、`HARDWARE.md`、`docs/`。

## 验证源码

```bash
bash scripts/verify-source.sh
cargo test --workspace
python3 -m unittest discover -s tests -q
```

## 卸载

```bash
sudo bash scripts/uninstall-system.sh
```

卸载移除服务、PAM 模块、polkit 规则与场景挂载，但保留用户模板。

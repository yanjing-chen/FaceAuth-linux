#!/usr/bin/env bash
# Explicit system installation. Does NOT attach PAM to sudo/gdm/polkit/etc.
set -euo pipefail
if (( EUID != 0 )); then echo '请使用 sudo bash scripts/install-system.sh' >&2;exit 1;fi
ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd -P)"
cd "$ROOT"
for binary in target/release/faceauth-daemon target/release/faceauth-cli \
              target/release/faceauth-scenes target/release/libpam_faceauth.so; do
    [[ -f "$binary" ]] || { echo "缺少已编译文件：$binary，请先以普通用户 cargo build --release --workspace" >&2;exit 1;}
done
for name in face_detection_yunet_2023mar.onnx face_recognition_sface_2021dec.onnx; do
    f="assets/models/$name"
    [[ -f "$f" && ! -L "$f" ]] || { echo "缺少模型文件：$f（请先运行 import-models.sh）" >&2;exit 1;}
done
if [[ -e /etc/systemd/system/faceauth-linux.service ]]; then
    echo '已有 faceauth-linux.service；请先检查是否由本项目安装，禁止盲目覆盖。' >&2;exit 1
fi
install -d -o root -g root -m 0755 /usr/libexec/faceauth-linux/scripts /usr/lib/faceauth-linux /usr/share/polkit-1/actions /usr/share/polkit-1/rules.d
install -d -o root -g root -m 0700 /var/lib/faceauth-linux /var/lib/faceauth-linux/models /var/lib/faceauth-linux/backups
install -o root -g root -m 0755 target/release/faceauth-daemon /usr/libexec/faceauth-linux/faceauth-daemon
install -o root -g root -m 0755 target/release/faceauth-cli /usr/bin/faceauth-linux-cli
install -o root -g root -m 0755 target/release/faceauth-scenes /usr/libexec/faceauth-linux/faceauth-scenes
install -o root -g root -m 0644 target/release/libpam_faceauth.so /usr/lib/faceauth-linux/pam_faceauth_linux.so
for f in infer_engine.py capture_bridge.py; do
    install -o root -g root -m 0644 "scripts/$f" "/usr/libexec/faceauth-linux/scripts/$f"
done
# infer_engine.py expects models relative to project root, not cwd.
install -d -o root -g root -m 0755 /usr/libexec/faceauth-linux/assets/models
for f in assets/models/*.onnx; do
    install -o root -g root -m 0644 "$f" "/usr/libexec/faceauth-linux/assets/models/$(basename "$f")"
done
install -o root -g root -m 0644 packaging/org.faceauth.linux.manage-scenes.policy /usr/share/polkit-1/actions/
install -o root -g root -m 0644 packaging/org.faceauth.linux.manage-models.policy /usr/share/polkit-1/actions/
install -o root -g root -m 0644 packaging/50-faceauth-linux-admin-cli.rules /usr/share/polkit-1/rules.d/
install -o root -g root -m 0644 packaging/faceauth-linux.service /etc/systemd/system/
# This explicit test profile is not used by GDM/sudo/polkit.
if [[ ! -e /etc/pam.d/faceauth-linux-test ]]; then
    cat >/etc/pam.d/faceauth-linux-test <<'PAM'
# FaceAuth-Rebuild owned independent test stack (not used by login services)
auth [success=done default=ignore] /usr/lib/faceauth-linux/pam_faceauth_linux.so
@include common-auth
@include common-account
PAM
    chmod 0644 /etc/pam.d/faceauth-linux-test
fi
systemctl daemon-reload
systemctl enable --now faceauth-linux.service
systemctl --no-pager --full status faceauth-linux.service || true
echo '完成：独立服务已安装；没有自动启用 sudo/GDM/polkit/su/login。先在新服务下录入，并通过独立 pamtester 栈验证。'
echo '紧急回滚：sudo /usr/libexec/faceauth-linux/faceauth-scenes off sudo（或 gdm/polkit/su/login）'

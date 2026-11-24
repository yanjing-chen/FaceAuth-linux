#!/usr/bin/env bash
# 从旧的 faceauth-rebuild 安装迁移到 faceauth-linux。
# 保留用户配置与人脸模板，卸载旧服务后安装新组件。
# 用法（在已 clone 并编译好的新仓库目录）：sudo bash scripts/migrate-rebuild-to-linux.sh
set -euo pipefail
if (( EUID != 0 )); then echo '请用 sudo 运行' >&2; exit 1; fi
ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd -P)"
cd "$ROOT"
TS=$(date +%Y%m%d-%H%M%S)
BAK="/var/lib/faceauth-linux/migration-backup-$TS"

echo "=== 0. 前置检查 ==="
for binary in target/release/faceauth-daemon target/release/faceauth-cli \
              target/release/faceauth-scenes target/release/libpam_faceauth.so; do
    [[ -f "$binary" ]] || { echo "缺少 $binary，请先 cargo build --release --workspace" >&2; exit 1; }
done
[[ -e /etc/systemd/system/faceauth-rebuild.service ]] || {
    echo '未检测到旧的 faceauth-rebuild.service；若已是新安装，直接跑 install-system.sh 即可。' >&2
    exit 1
}
[[ ! -e /etc/systemd/system/faceauth-linux.service ]] || {
    echo '已存在 faceauth-linux.service，可能已迁移过，拒绝重复操作。' >&2; exit 1
}

echo "=== 1. 备份旧数据到 $BAK ==="
mkdir -p "$BAK"
[[ -d /var/lib/faceauth-rebuild ]] && cp -a /var/lib/faceauth-rebuild "$BAK/var-lib" || true
[[ -f /etc/pam.d/faceauth-rebuild-test ]] && cp -a /etc/pam.d/faceauth-rebuild-test "$BAK/" || true

echo "=== 2. 迁移用户配置与人脸模板（逐用户）==="
while IFS=: read -r user _ uid _ _ home _; do
    (( uid >= 1000 )) || continue
    old_cfg="$home/.config/faceauth-rebuild/config"
    new_cfg="$home/.config/faceauth-linux/config"
    if [[ -f "$old_cfg" ]]; then
        install -d -o "$uid" -g "$uid" -m 0700 "$(dirname "$new_cfg")"
        if [[ ! -e "$new_cfg" ]]; then
            cp -a "$old_cfg" "$new_cfg"
            chown "$uid:$uid" "$new_cfg"; chmod 600 "$new_cfg"
            echo "  配置已迁移：$user"
        fi
    fi
    old_models="$home/.local/share/faceauth-rebuild/models"
    new_models="$home/.local/share/faceauth-linux/models"
    if [[ -d "$old_models" ]]; then
        install -d -o "$uid" -g "$uid" -m 0700 "$(dirname "$new_models")"
        cp -a "$old_models" "$new_models"
        chown -R "$uid:$uid" "$new_models"
        echo "  用户模板已迁移：$user"
    fi
done < /etc/passwd

echo "=== 3. 迁移系统人脸模板 ==="
if [[ -d /var/lib/faceauth-rebuild/models ]]; then
    mkdir -p /var/lib/faceauth-linux/models
    cp -a /var/lib/faceauth-rebuild/models/. /var/lib/faceauth-linux/models/
    chmod 0700 /var/lib/faceauth-linux/models
    echo '  系统模板已迁移'
fi

echo "=== 4. 卸载旧组件（关闭服务、移除场景挂载）==="
systemctl disable --now faceauth-rebuild.service 2>/dev/null || true
if [[ -x /usr/libexec/faceauth-rebuild/faceauth-scenes ]]; then
    for scene in sudo gdm su login polkit; do
        /usr/libexec/faceauth-rebuild/faceauth-scenes off "$scene" >/dev/null 2>&1 || true
    done
fi
rm -f /etc/systemd/system/faceauth-rebuild.service \
      /etc/pam.d/faceauth-rebuild-test \
      /usr/bin/faceauth-rebuild-cli \
      /usr/share/polkit-1/actions/org.faceauth.rebuild.manage-scenes.policy \
      /usr/share/polkit-1/actions/org.faceauth.rebuild.manage-models.policy \
      /usr/share/polkit-1/rules.d/50-faceauth-rebuild-admin-cli.rules \
      /usr/lib/faceauth-rebuild/pam_faceauth_rebuild.so
rm -rf /usr/libexec/faceauth-rebuild /usr/lib/faceauth-rebuild
systemctl daemon-reload

echo "=== 5. 安装新组件 ==="
bash scripts/install-system.sh

echo "=== 迁移完成 ==="
echo "旧数据与配置备份在：$BAK"
echo "请重新打开 GUI（普通用户），确认摄像头与识别测试。"

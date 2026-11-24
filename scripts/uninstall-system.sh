#!/usr/bin/env bash
set -euo pipefail
if (( EUID != 0 )); then echo '需要 sudo 执行卸载' >&2;exit 1;fi
MANAGER=/usr/libexec/faceauth-linux/faceauth-scenes
if [[ ! -x "$MANAGER" ]]; then echo '找不到离线场景恢复工具；请先人工核查 /etc/pam.d/，禁止直接删除模块' >&2;exit 1;fi
# Never delete PAM module unless every managed hook has been safely removed.
for s in sudo gdm su login polkit; do
    "$MANAGER" off "$s" || { echo "场景 $s 不能安全恢复；停止卸载，请查阅备份" >&2;exit 1;}
done
systemctl disable --now faceauth-linux.service || true
# Never delete a test PAM profile if an administrator has edited it.
if [[ -f /etc/pam.d/faceauth-linux-test ]]; then
    EXPECTED="$(mktemp)"
    cat >"$EXPECTED" <<'PAM'
# FaceAuth-Rebuild owned independent test stack (not used by login services)
auth [success=done default=ignore] /usr/lib/faceauth-linux/pam_faceauth_linux.so
@include common-auth
@include common-account
PAM
    if cmp -s "$EXPECTED" /etc/pam.d/faceauth-linux-test; then
        rm -f /etc/pam.d/faceauth-linux-test
    else
        echo '独立测试栈已被修改，停止卸载以避免留下损坏的 PAM 引用：/etc/pam.d/faceauth-linux-test' >&2
        rm -f "$EXPECTED"
        exit 1
    fi
    rm -f "$EXPECTED"
fi
rm -f /etc/systemd/system/faceauth-linux.service /usr/share/polkit-1/actions/org.faceauth.linux.manage-scenes.policy /usr/share/polkit-1/actions/org.faceauth.linux.manage-models.policy /usr/share/polkit-1/rules.d/50-faceauth-linux-admin-cli.rules
rm -f /usr/bin/faceauth-linux-cli
rm -rf /usr/libexec/faceauth-linux /usr/lib/faceauth-linux
systemctl daemon-reload
echo '已移除系统服务和模块。用户人脸数据 /var/lib/faceauth-linux 保留，备份也保留；原 FaceAuth 未触碰。'

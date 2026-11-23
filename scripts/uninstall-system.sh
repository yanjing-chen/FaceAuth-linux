#!/usr/bin/env bash
set -euo pipefail
if (( EUID != 0 )); then echo '需要 sudo 执行卸载' >&2;exit 1;fi
MANAGER=/usr/libexec/faceauth-rebuild/faceauth-scenes
if [[ ! -x "$MANAGER" ]]; then echo '找不到离线场景恢复工具；请先人工核查 /etc/pam.d/，禁止直接删除模块' >&2;exit 1;fi
# Never delete PAM module unless every managed hook has been safely removed.
for s in sudo gdm su login polkit; do
    "$MANAGER" off "$s" || { echo "场景 $s 不能安全恢复；停止卸载，请查阅备份" >&2;exit 1;}
done
systemctl disable --now faceauth-rebuild.service || true
# Never delete a test PAM profile if an administrator has edited it.
if [[ -f /etc/pam.d/faceauth-rebuild-test ]]; then
    EXPECTED="$(mktemp)"
    cat >"$EXPECTED" <<'PAM'
# FaceAuth-Rebuild owned independent test stack (not used by login services)
auth [success=done default=ignore] /usr/lib/faceauth-rebuild/pam_faceauth_rebuild.so
@include common-auth
@include common-account
PAM
    if cmp -s "$EXPECTED" /etc/pam.d/faceauth-rebuild-test; then
        rm -f /etc/pam.d/faceauth-rebuild-test
    else
        echo '独立测试栈已被修改，停止卸载以避免留下损坏的 PAM 引用：/etc/pam.d/faceauth-rebuild-test' >&2
        rm -f "$EXPECTED"
        exit 1
    fi
    rm -f "$EXPECTED"
fi
rm -f /etc/systemd/system/faceauth-rebuild.service /usr/share/polkit-1/actions/org.faceauth.rebuild.manage-scenes.policy /usr/share/polkit-1/actions/org.faceauth.rebuild.manage-models.policy
rm -f /usr/bin/faceauth-rebuild-cli
rm -rf /usr/libexec/faceauth-rebuild /usr/lib/faceauth-rebuild
systemctl daemon-reload
echo '已移除系统服务和模块。用户人脸数据 /var/lib/faceauth-rebuild 保留，备份也保留；原 FaceAuth 未触碰。'

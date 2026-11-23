#!/usr/bin/env bash
# Strictly read-only audit of real system PAM profiles before enabling FaceAuth.
# Run with sudo for full read access. Never edits PAM, systemd, or templates.
set -euo pipefail
if (( EUID != 0 )); then
    echo '请使用：sudo bash scripts/auth-preflight.sh（脚本仅读取信息，不改系统）' >&2
    exit 2
fi
bad=0
printf '%s\n' '=== FaceAuth 系统认证启用前检查（只读） ==='
printf '当前系统：'
if [[ -r /etc/os-release ]]; then . /etc/os-release; printf '%s\n' "${PRETTY_NAME:-Linux}"; else printf 'Linux\n'; fi
printf '重构 daemon：'
systemctl is-active faceauth-rebuild.service 2>/dev/null || true
printf '重构 PAM 模块：'
if [[ -f /usr/lib/faceauth-rebuild/pam_faceauth_rebuild.so ]]; then echo '已安装'; else echo '未安装（先在独立模式下完成编译和服务安装）'; fi
printf '独立测试栈：'
if [[ -f /etc/pam.d/faceauth-rebuild-test ]]; then echo '已安装'; else echo '未安装'; fi
printf '\n=== 检查五个服务中是否存在旧版/新版 FaceAuth 挂载 ===\n'
for s in sudo gdm-password polkit-1 su login; do
    f="/etc/pam.d/$s"
    if [[ ! -e "$f" && "$s" == polkit-1 && -f /usr/lib/pam.d/polkit-1 ]]; then
        f=/usr/lib/pam.d/polkit-1
    fi
    if [[ ! -e "$f" ]]; then echo "[$s] 未找到，需进一步确认发行版配置"; continue; fi
    echo "[$s] $f"
    if [[ ! -f "$f" || -L "$f" ]]; then
        echo "  警告：文件不是普通文件（或为符号链接），不适合自动修改"; bad=1; continue
    fi
    if ! grep -q '^[[:space:]]*@include[[:space:]]\+common-auth' "$f"; then
        echo '  警告：未见 @include common-auth，自动挂载可能被拒绝'; bad=1
    fi
    if grep -nE 'pam_faceauth([^_[:alnum:]]|$)|pam_faceauth_rebuild\.so|FaceAuth-Rebuild managed' "$f"; then
        echo '  发现现有 FaceAuth PAM 条目；启用新模块前必须确认是否冲突！'
        # New-format blocks can be valid after intentional activation; still warn.
        bad=1
    else
        echo '  未发现 FaceAuth 插入条目'
    fi
done
printf '\n=== 检查系统公共认证栈 ===\n'
for s in common-auth other; do
    f="/etc/pam.d/$s"
    if [[ -e "$f" ]] && grep -nEi 'pam_faceauth|FaceAuth-Rebuild' "$f"; then
        echo "严重提醒：$f 含 FaceAuth 条目；本项目禁止写入公共栈，请先人工审查。"
        bad=1
    fi
done
printf '\n=== 检查紧急回滚工具 ===\n'
if [[ -x /usr/libexec/faceauth-rebuild/faceauth-scenes ]]; then
    echo '独立回滚工具存在：/usr/libexec/faceauth-rebuild/faceauth-scenes'
else
    echo '尚未安装回滚工具；禁止启用真实 sudo/GDM/polkit'
    bad=1
fi
if (( bad )); then
    echo '结果：存在需人工核对的条件。不要直接开启 sudo/GDM/polkit。'
    exit 2
fi
echo '结果：未发现明显冲突。仍需先验证生产 TEST 与独立 PAM 栈，并保留 root shell。'

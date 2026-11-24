#!/usr/bin/env bash
# Nonprivileged, side-by-side demonstration. Never modifies current FaceAuth installation or PAM.
set -euo pipefail
if (( EUID == 0 )); then echo '禁止使用 sudo 运行开发测试' >&2; exit 1; fi
ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd -P)"
cd "$ROOT"
echo "请先关闭旧 FaceAuth GUI；开发测试期间不要执行 sudo 刷脸或锁屏。旧服务及 PAM 不会被修改。"
command -v cargo >/dev/null || { echo '缺少 cargo；请先安装 sudo apt install cargo rustc'; exit 1; }
[[ -n "${XDG_RUNTIME_DIR:-}" ]] || { echo '必须在 GNOME 用户图形桌面会话中运行';exit 1; }
python3 -m unittest discover -s tests -v
cargo test --workspace
cargo build --workspace
SOCK_DIR="$XDG_RUNTIME_DIR/faceauth-dev"
if [[ -e "$SOCK_DIR/daemon.sock" ]]; then
   echo "测试 socket 已存在，请先关闭旧的测试 daemon: $SOCK_DIR/daemon.sock" >&2
   exit 1
fi
mkdir -p "$HOME/.config/faceauth-linux"
chmod 700 "$HOME/.config/faceauth-linux"
if [[ ! -e "$HOME/.config/faceauth-linux/config" ]]; then
    cat > "$HOME/.config/faceauth-linux/config" <<'CONF'
mode=dual
rgb_device=
ir_device=
liveness=heuristic
threshold=0.6
CONF
    chmod 600 "$HOME/.config/faceauth-linux/config"
fi
"$ROOT/target/debug/faceauth-daemon" --dev >"${XDG_RUNTIME_DIR}/faceauth-linux-dev.log" 2>&1 &
DAEMON_PID=$!
cleanup() { kill "$DAEMON_PID" 2>/dev/null || true;wait "$DAEMON_PID" 2>/dev/null || true;rm -f "$SOCK_DIR/daemon.sock"; }
trap cleanup EXIT
for i in $(seq 1 50); do
    [[ -S "$SOCK_DIR/daemon.sock" ]] && break
    sleep 0.1
done
if [[ ! -S "$SOCK_DIR/daemon.sock" ]]; then
    cat "${XDG_RUNTIME_DIR}/faceauth-linux-dev.log"
    exit 1
fi
"$ROOT/scripts/launch-gui.sh" --dev "$@"

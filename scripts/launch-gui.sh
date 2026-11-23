#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd -P)"
if (( EUID == 0 )); then
    echo '错误：FaceAuth GUI 禁止以 root/sudo 启动。' >&2
    exit 1
fi
if [[ ${XDG_SESSION_TYPE:-} == wayland ]]; then
    export GDK_BACKEND=wayland
else
    unset GDK_BACKEND
fi
export GSK_RENDERER="${GSK_RENDERER:-cairo}"
if [[ ${1:-} == --dev ]]; then
    shift
    export FACEAUTH_DEV_MODE=1
    export FACEAUTH_SOCKET="${XDG_RUNTIME_DIR:?Missing XDG_RUNTIME_DIR}/faceauth-dev/daemon.sock"
fi
exec /usr/bin/python3 -u -B "$ROOT/scripts/faceauth_gui.py" "$@"

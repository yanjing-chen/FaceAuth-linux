#!/usr/bin/env bash
# Reuse trustworthy, locally available OpenCV Zoo weights (never replace automatically).
set -euo pipefail
ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd -P)"
SOURCE="${1:-$HOME/WorkBuddy/2026-11-21-21-41-09/faceauth/assets/models}"
[[ -d "$SOURCE" ]] || { echo "模型目录不存在: $SOURCE" >&2; exit 1; }
for f in face_detection_yunet_2023mar.onnx face_recognition_sface_2021dec.onnx; do
  [[ -s "$SOURCE/$f" ]] || { echo "缺少 $SOURCE/$f" >&2; exit 1; }
  if [[ ! -f "$ROOT/assets/models/$f" ]]; then
      cp -- "$SOURCE/$f" "$ROOT/assets/models/$f"
      chmod 644 "$ROOT/assets/models/$f"
  fi
  if (( $(stat -c %s "$ROOT/assets/models/$f") < 100000 )); then
      echo "模型大小异常：$f" >&2; exit 1
  fi
done
echo '已准备 OpenCV 模型；原目录文件保持不变。'

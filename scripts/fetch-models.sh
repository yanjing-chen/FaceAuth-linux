#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd -P)"
DEST="$ROOT/assets/models"
mkdir -p "$DEST"
for name in face_detection_yunet_2023mar face_recognition_sface_2021dec; do
    if [[ "$name" == face_detection* ]]; then
      folder=face_detection_yunet
    else
      folder=face_recognition_sface
    fi
    target="$DEST/$name.onnx"
    if [[ -s "$target" ]] && (( $(stat -c %s "$target") > 100000 )); then
        echo "保留已有模型: $target"
        continue
    fi
    echo "下载 OpenCV Zoo 模型：$name（仅准备阶段联网）"
    min_bytes=100000
    [[ "$name" == face_recognition* ]] && min_bytes=5000000
    success=0
    for url in \
       "https://huggingface.co/opencv/opencv_zoo/resolve/main/models/$folder/$name.onnx" \
       "https://raw.githubusercontent.com/opencv/opencv_zoo/main/models/$folder/$name.onnx"; do
        rm -f "$target.part"
        if curl -fL --retry 2 --connect-timeout 15 --max-time 240 "$url" -o "$target.part"; then
            if [[ -f "$target.part" ]] && (( $(stat -c %s "$target.part") > min_bytes )) \
               && [[ $(head -c 7 "$target.part") != 'version' ]]; then
                mv -f "$target.part" "$target"
                success=1
                break
            fi
        fi
    done
    if (( ! success )); then
       rm -f "$target.part"
       echo "模型未下载成功：$name，请从 OpenCV Zoo 手动下载" >&2
       exit 1
    fi
    echo "模型已准备：$target"
done

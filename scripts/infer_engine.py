#!/usr/bin/env python3
"""
FaceAuth 外部推理器：对接 Rust ExternalBackend 的行协议。
使用 OpenCV 的 YuNet（人脸检测）+ SFace（特征提取）。

协议：
  输入:  EMBED <w> <h> <hex_gray>
         LIVENESS <w> <h> <hex_gray>
  输出:  OK <f32> ...     或  ERR <message>

安全：
  - 只读模型文件，不写文件、不访问网络
  - 输入为十六进制灰度像素，只在内存处理
  - 所有异常转为 ERR 行，不泄漏堆栈
"""

import sys
import os
import cv2
import numpy as np

MODEL_DIR = os.environ.get(
    "FACEAUTH_MODEL_DIR",
    os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "assets", "models"),
)
DETECT_MODEL = os.path.join(MODEL_DIR, "face_detection_yunet_2023mar.onnx")
RECOG_MODEL = os.path.join(MODEL_DIR, "face_recognition_sface_2021dec.onnx")

detector = None
recognizer = None


def init_models():
    global detector, recognizer
    if detector is None:
        if not os.path.exists(DETECT_MODEL):
            raise RuntimeError(f"检测模型不存在: {DETECT_MODEL}")
        detector = cv2.FaceDetectorYN_create(DETECT_MODEL, "", (320, 320),
                                             score_threshold=0.6,
                                             nms_threshold=0.3,
                                             top_k=5000)
    if recognizer is None:
        if not os.path.exists(RECOG_MODEL):
            raise RuntimeError(f"识别模型不存在: {RECOG_MODEL}")
        recognizer = cv2.FaceRecognizerSF_create(RECOG_MODEL, "")


def decode_hex(hexstr, w, h):
    """十六进制灰度串 -> 2D numpy 数组"""
    raw = bytes.fromhex(hexstr)
    expected = w * h
    if len(raw) < expected:
        return None
    arr = np.frombuffer(raw[:expected], dtype=np.uint8).reshape((h, w))
    return arr


def do_embed(gray, w, h):
    init_models()
    # YuNet 需要 3 通道 BGR；灰度转 BGR
    if gray.ndim == 2:
        bgr = cv2.cvtColor(gray, cv2.COLOR_GRAY2BGR)
    else:
        bgr = gray

    detector.setInputSize((w, h))
    _, faces = detector.detect(bgr)
    if faces is None or len(faces) == 0:
        return None  # 未检测到人脸

    # 取置信度最高的人脸
    best = max(faces, key=lambda f: f[-1])
    aligned = recognizer.alignCrop(bgr, best)
    feat = recognizer.feature(aligned)  # (1, 128) float32
    return feat.flatten().tolist()


def do_liveness(gray, w, h):
    """
    活体检测（启发式，IR 灰度帧）：
      - 检测人脸是否占据合理面积（太小的可能是照片）
      - 检测图像局部方差（打印照片通常纹理/反光特征不同）
    返回 0.0~1.0 置信度。生产环境应替换为专用活体模型。
    """
    init_models()
    if gray.ndim != 2:
        return 0.0
    # 人脸检测
    bgr = cv2.cvtColor(gray, cv2.COLOR_GRAY2BGR)
    detector.setInputSize((w, h))
    _, faces = detector.detect(bgr)
    if faces is None or len(faces) == 0:
        return 0.0
    best = max(faces, key=lambda f: f[-1])
    x, y, bw, bh = best[:4]
    area_ratio = (bw * bh) / (w * h)
    face_region = gray[int(y):int(y + bh), int(x):int(x + bw)]
    if face_region.size == 0:
        return 0.0
    # 纹理丰富度（拉普拉斯方差）：打印照片/屏幕通常低于真人
    sharpness = cv2.Laplacian(face_region, cv2.CV_64F).var()
    score = 0.0
    if 0.02 < area_ratio < 0.8:
        score += 0.4
    if sharpness > 50:
        score += 0.4
    elif sharpness > 20:
        score += 0.2
    # 亮度分布（真人 IR 有自然的中间调）
    mean = float(face_region.mean())
    if 30 < mean < 220:
        score += 0.2
    return min(1.0, score)


def main():
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            parts = line.split()
            cmd = parts[0].upper()

            if cmd == "PING":
                # 就绪握手：预热模型后回 PONG，无需其它参数
                init_models()
                print("PONG")
                sys.stdout.flush()
                continue

            w, h = int(parts[1]), int(parts[2])
            hexstr = parts[3]

            gray = decode_hex(hexstr, w, h)
            if gray is None:
                print("ERR 帧数据不足")
                sys.stdout.flush()
                continue

            if cmd == "DETECT":
                # 检测人脸框：返回 OK x y w h
                init_models()
                bgr = cv2.cvtColor(gray, cv2.COLOR_GRAY2BGR)
                detector.setInputSize((w, h))
                _, faces = detector.detect(bgr)
                if faces is None or len(faces) == 0:
                    print("OK")  # 无人脸返回空 OK
                else:
                    f = max(faces, key=lambda x: x[2] * x[3])
                    x, y, bw, bh = [int(v) for v in f[:4]]
                    print(f"OK {x} {y} {bw} {bh}")
            elif cmd == "EMBED":
                feat = do_embed(gray, w, h)
                if feat is None:
                    print("ERR 未检测到人脸")
                else:
                    print("OK " + " ".join(f"{x:.6f}" for x in feat))
            elif cmd == "LIVENESS":
                score = do_liveness(gray, w, h)
                print(f"OK {score:.6f}")
            else:
                print(f"ERR 未知命令: {cmd}")
            sys.stdout.flush()
        except Exception as e:
            # 不泄漏堆栈，只输出简短错误
            print(f"ERR {type(e).__name__}: {str(e)[:80]}")
            sys.stdout.flush()


if __name__ == "__main__":
    main()

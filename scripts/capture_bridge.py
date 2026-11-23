#!/usr/bin/env python3
"""Private daemon-owned V4L2 camera worker. One line in/out. Never writes frames to disk.

OPEN mode rgb_path ir_path    -> OK / ERR
PREVIEW                       -> OK <jpeg_hex>
SNAP                          -> OK w h gray_hex irw irh ir_gray_hex
CLOSE                         -> OK
PING                          -> PONG
"""
import os
import re
import sys
import stat
from pathlib import Path

import cv2
import numpy as np

# Keep single frame bounded even for 4K webcams.
MAX_WIDTH, MAX_HEIGHT = 800, 600


def index_for(path):
    target = os.path.realpath(path)
    if not re.fullmatch(r'/dev/video\d+', target):
        raise ValueError('only V4L2 /dev/videoN devices permitted')
    if not stat.S_ISCHR(os.stat(target).st_mode):
        raise ValueError('not a character device')
    return int(target[len('/dev/video'):])


def open_cap(path):
    cap = cv2.VideoCapture(index_for(path), cv2.CAP_V4L2)
    if not cap.isOpened():
        cap.release()
        raise RuntimeError('camera unavailable')
    cap.set(cv2.CAP_PROP_FRAME_WIDTH, 640)
    cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 480)
    cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)
    return cap


def visual_color_kind(image):
    """Image evidence: RGB vs monochrome, not proof of IR illumination."""
    if image.ndim == 2:
        return 'MONO'
    if image.ndim != 3 or image.shape[2] < 3:
        return 'MONO'
    # Avoid 8-bit unsigned subtraction wraparound.
    sample = image[::4, ::4, :3].astype(np.int16)
    spread = np.max(sample, axis=2) - np.min(sample, axis=2)
    return 'RGB' if float(spread.mean()) > 2.0 else 'MONO'


def read_gray(cap):
    ok, image = cap.read()
    if not ok or image is None:
        raise RuntimeError('camera did not return a frame')
    h, w = image.shape[:2]
    if w > MAX_WIDTH or h > MAX_HEIGHT:
        factor = min(MAX_WIDTH / w, MAX_HEIGHT / h)
        image = cv2.resize(image, (max(1, int(w * factor)), max(1, int(h * factor))))
    gray = (cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
            if image.ndim == 3 else image)
    return image, gray


# Visual-only detector: never used as proof of identity or liveness.
# It operates inside daemon-owned camera worker, not the GUI main thread.
_detector = None
_detector_unavailable = False


def get_preview_detector():
    global _detector, _detector_unavailable
    if _detector is not None or _detector_unavailable:
        return _detector
    model = Path(__file__).resolve().parents[1] / 'assets/models/face_detection_yunet_2023mar.onnx'
    try:
        if not model.is_file():
            _detector_unavailable = True
            return None
        _detector = cv2.FaceDetectorYN_create(str(model), '', (320, 320),
                                              score_threshold=0.60,
                                              nms_threshold=0.3, top_k=50)
    except (cv2.error, OSError, AttributeError):
        _detector_unavailable = True
        return None
    return _detector


def detect_preview_faces(image):
    """Return clamped (x,y,w,h) rectangles in original image coordinates."""
    detector = get_preview_detector()
    if detector is None:
        return []
    h, w = image.shape[:2]
    try:
        detector.setInputSize((w, h))
        bgr = cv2.cvtColor(image, cv2.COLOR_GRAY2BGR) if image.ndim == 2 else image
        _, faces = detector.detect(bgr)
    except (cv2.error, ValueError):
        return []
    result = []
    if faces is not None:
        for face in faces[:5]:
            x, y, bw, bh = (int(round(float(v))) for v in face[:4])
            x1, y1 = max(0, x), max(0, y)
            x2, y2 = min(w, x + bw), min(h, y + bh)
            if x2 > x1 and y2 > y1:
                result.append((x1, y1, x2-x1, y2-y1))
    return result


def draw_tracking_corners(image, rectangles):
    """Draw real-time corner brackets, not the old static guide circle."""
    result = (cv2.cvtColor(image, cv2.COLOR_GRAY2BGR)
              if image.ndim == 2 else image.copy())
    for x, y, bw, bh in rectangles:
        span = max(7, min(28, bw // 5, bh // 5))
        color = (90, 222, 112)  # OpenCV BGR
        t = 3
        for px, sign_x in ((x, 1), (x+bw, -1)):
            for py, sign_y in ((y, 1), (y+bh, -1)):
                cv2.line(result, (px, py), (px+sign_x*span, py), color, t, cv2.LINE_AA)
                cv2.line(result, (px, py), (px, py+sign_y*span), color, t, cv2.LINE_AA)
    return result


def preview_jpg(image):
    frame = draw_tracking_corners(image, detect_preview_faces(image))
    ok, coded = cv2.imencode('.jpg', frame, [cv2.IMWRITE_JPEG_QUALITY, 77])
    if not ok:
        raise RuntimeError('image encoding failed')
    return coded.tobytes().hex()


def main():
    rgb = ir = None
    mode = None
    for line in sys.stdin:
        try:
            p = line.strip().split()
            if not p:
                print('ERR empty request', flush=True)
                continue
            if p[0] == 'PING':
                print('PONG', flush=True)
                continue
            if p[0] == 'CLOSE':
                if rgb is not None: rgb.release()
                if ir is not None: ir.release()
                rgb = ir = None
                print('OK', flush=True)
                continue
            if p[0] == 'OPEN':
                if len(p) != 4 or p[1] not in ('rgb', 'ir', 'dual'):
                    raise ValueError('OPEN mode rgb_path ir_path required')
                if rgb is not None: rgb.release()
                if ir is not None: ir.release()
                rgb = ir = None
                mode = p[1]
                rgb_path, ir_path = ('' if x == '-' else x for x in p[2:4])
                if mode == 'ir':
                    if not ir_path: raise ValueError('IR device not configured')
                    rgb = open_cap(ir_path)
                else:
                    if not rgb_path: raise ValueError('RGB device not configured')
                    rgb = open_cap(rgb_path)
                    if mode == 'dual':
                        if not ir_path: raise ValueError('IR device not configured')
                        if os.path.realpath(rgb_path) == os.path.realpath(ir_path):
                            raise ValueError('dual mode needs two distinct video nodes')
                        ir = open_cap(ir_path)
                print('OK', flush=True)
                continue
            if p[0] == 'TYPES':
                if rgb is None:
                    raise RuntimeError('camera not opened')
                image, _ = read_gray(rgb)
                first = visual_color_kind(image)
                if mode == 'ir':
                    first, second = '-', first
                else:
                    second = '-'
                    if ir is not None:
                        image_ir, _ = read_gray(ir)
                        second = visual_color_kind(image_ir)
                print(f'OK {first} {second}', flush=True)
                continue
            if p[0] == 'PREVIEW':
                if rgb is None: raise RuntimeError('camera not opened')
                image, _ = read_gray(rgb)
                print('OK ' + preview_jpg(image), flush=True)
                continue
            if p[0] == 'SNAP':
                if rgb is None: raise RuntimeError('camera not opened')
                _, gray = read_gray(rgb)
                if ir is not None:
                    _, irgray = read_gray(ir)
                    ih, iw = irgray.shape
                    irhex = irgray.tobytes().hex()
                else:
                    iw = ih = 0
                    irhex = '-'
                h, w = gray.shape
                print(f'OK {w} {h} {gray.tobytes().hex()} {iw} {ih} {irhex}', flush=True)
                continue
            print('ERR invalid command', flush=True)
        except Exception as exc:
            if p and p[0] == 'OPEN':
                if rgb is not None: rgb.release()
                if ir is not None: ir.release()
                rgb = ir = None
                mode = None
            # No image bytes, embeddings, user names or paths in logs.
            print(f'ERR {type(exc).__name__}', flush=True)
    if rgb is not None: rgb.release()
    if ir is not None: ir.release()

if __name__ == '__main__':
    main()

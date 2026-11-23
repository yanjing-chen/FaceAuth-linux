OpenCV Zoo ONNX models required:

- face_detection_yunet_2023mar.onnx
- face_recognition_sface_2021dec.onnx

The source ZIP intentionally excludes the ~37 MB external pretrained SFace binary. 
Reuse local original files with `bash scripts/import-models.sh /path/to/original/assets/models`,
or explicitly download at setup with `bash scripts/fetch-models.sh`.

FaceAuth does not use the network during authentication.

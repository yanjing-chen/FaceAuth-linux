# FaceAuth v0.1.6 硬件与双摄工作原理

## 实际支持的通道

| 模式 | 预览与人脸特征 | 活体判定 | 采集设备 |
|---|---|---|---|
| `rgb` | RGB 彩色 V4L2 | `none` 或图像启发式 | 1 个 RGB 节点 |
| `ir` | IR V4L2 图像转灰度，再转换三通道交 YuNet / SFace | 启发式（或 none） | 1 个 IR 节点 |
| `dual` | **RGB** 图像用于 YuNet/SFace | **IR** 图像用于启发式活体 | **2 个不同的 V4L2 节点** |

`dual` 模式在 `capture_bridge.py` 中分别执行两次 `VideoCapture`，`SNAP` 读取 RGB 灰度帧与 IR 灰度帧。Rust `Pipeline::embedding` 使用 RGB 帧，`Pipeline::live` 选择 IR 帧。若第二路摄像头不能打开或无法取帧，录入会失败，测试会显示不可用，PAM 认证不会把它当成功。

**注意：只有在 `liveness=heuristic` 时 IR 帧用于活体判断。** 当用户将活体检测明确设为 `none` 时，尽管双模仍要求打开并读取两个节点，也不会根据 IR 帧拒绝照片；请不要将此状态理解为“双路活体”。

视频来源可以是同一 USB 复合模组的两个采集节点，也可以是两台独立 USB 摄像头。但不可在 RGB/IR 选项中选择同一个节点。`/dev/video0` 编号不代表类型，摄像头单色图像也不能证明其是红外传感器。根据 `/sys/class/video4linux` 名称和采样颜色作出的类型提示只是线索，需要用户确认。

## 防伪安全边界

目前实现的是单帧 IR 图像纹理启发式检查，RGB 与 IR 尚无硬件同步或跨模态同人绑定，也没有经过防伪标准测试。不能宣称可可靠抵御照片、屏幕或面具。RGB 单独认证容易受到照片欺骗。

本版仅实现 RGB 和 IR 两种图像通道及其双模组合；没有第三类深度数据采集与认证路径。

## 检查设备

```bash
v4l2-ctl --list-devices
v4l2-ctl --device=/dev/video0 --list-formats-ext
```

GUI 的选择面板完整显示设备名和 `/dev/v4l/by-id/` 或 `/dev/v4l/by-path/` 路径；选对两路视频节点并使双模预览正常后，再录入并执行识别测试。单 RGB 与单 IR 的模型特征分布不同，切换模式建议重新录入。

## v0.1.6 摄像头设备列表过滤说明

同一个摄像头模组常常在 `/dev/video*` 下公开**多个节点**：RGB/IR 等真实采集节点、metadata-only 节点，还可能有多组符号链接。系统也可能有虚拟摄像头。FaceAuth 现在使用 `VIDIOC_QUERYCAP` 检查有效 `VIDEO_CAPTURE`/`VIDEO_CAPTURE_MPLANE` 位，仅隐藏经能力查询确认**没有**视频捕获功能的节点，别名仍按实际 `/dev/videoN` 去重。

能力查询失败的设备会以“采集能力待确认”保留，不会武断隐藏。不能以列表项目数量推导物理摄像头数量，也不能仅靠灰度图断言硬件为 IR。可使用 `v4l2-ctl --list-devices` 及 `v4l2-ctl -d /dev/videoN --all` 分别核对。

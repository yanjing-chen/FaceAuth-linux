"""Unprivileged local IPC and user-owned settings, independent of GI/cv2."""
import glob
import fcntl
import struct
import os
from pathlib import Path
import re
import socket
import stat

DEFAULTS = {
    'mode': 'dual', 'rgb_device': '', 'ir_device': '',
    'liveness': 'heuristic', 'threshold': '0.6',
    'ssh_disabled': '1', 'dark_ir_skip': '1', 'auth_frames': '3',
}
CONFIG = Path.home() / '.config' / 'faceauth-rebuild' / 'config'


def current_socket():
    # --dev launcher exports FACEAUTH_SOCKET. Production never needs an override.
    return os.environ.get('FACEAUTH_SOCKET', '/run/faceauth-rebuild/daemon.sock')


def request(line, timeout=6.0, max_bytes=8192):
    if '\n' in line or '\r' in line or len(line) > 6000:
        raise ValueError('invalid protocol command')
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as s:
        s.settimeout(timeout)
        s.connect(current_socket())
        s.sendall((line + '\n').encode('utf-8'))
        result = bytearray()
        while len(result) <= max_bytes:
            data = s.recv(min(65536, max_bytes+1-len(result)))
            if not data: break
            result.extend(data)
            if b'\n' in data: break
    if len(result) > max_bytes:
        raise ValueError('daemon reply too large')
    if b'\n' not in result:
        raise ValueError('incomplete daemon reply')
    return result.split(b'\n', 1)[0].decode('utf-8')


def valid_label(label):
    return bool(label and len(label.encode('utf-8')) <= 64
                and not any(c.isspace() or c in ',/\\' or ord(c)<32 for c in label))


MODEL_AUTH_ACTION = 'org.faceauth.rebuild.manage-models'


ADMIN_CLI = '/usr/bin/faceauth-rebuild-cli'


def admin_request(line, timeout=90):
    """Run a privileged daemon command through pkexec.
    pkexec is launched from the GUI process, so it inherits the user's
    graphical session and the GNOME polkit agent can prompt for password /
    fingerprint. The GUI itself stays unprivileged: its Wayland environment
    and camera preview are untouched (no white-frame regression).
    Returns the daemon reply string, or an ERR line on denial/failure.
    """
    import subprocess
    if any(c in line for c in ('\n', '\r')) or len(line) > 6000:
        return 'ERR invalid admin command'
    try:
        proc = subprocess.run(
            ['/usr/bin/pkexec', ADMIN_CLI, line],
            capture_output=True, text=True, timeout=timeout)
    except FileNotFoundError:
        return 'ERR pkexec or CLI unavailable'
    except subprocess.TimeoutExpired:
        return 'ERR authorization or command timed out'
    except Exception as exc:
        return f'ERR {type(exc).__name__}'
    out = (proc.stdout or '').strip().splitlines()
    reply = out[0] if out else ''
    if proc.returncode != 0:
        err = (proc.stderr or '').strip().splitlines()
        detail = err[-1] if err else 'authorization denied'
        return f'ERR {detail[:120]}'
    return reply or 'ERR empty reply'


def load_config():
    cfg = dict(DEFAULTS)
    try:
        content = CONFIG.read_text(encoding='utf-8')
    except FileNotFoundError:
        return cfg
    for raw in content.splitlines():
        line = raw.split('#', 1)[0].strip()
        if '=' not in line: continue
        k,v = (part.strip() for part in line.split('=', 1))
        if k in cfg: cfg[k] = v
    if cfg['mode'] not in ('rgb','ir','dual'): cfg['mode']='dual'
    if cfg['liveness'] not in ('none','heuristic'): cfg['liveness']='heuristic'
    return cfg


def save_config(cfg):
    CONFIG.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    os.chmod(CONFIG.parent, 0o700)
    values = dict(DEFAULTS, **{k:v for k,v in cfg.items() if k in DEFAULTS})
    output = ''.join(f'{k}={values[k]}\n' for k in DEFAULTS)
    tmp = CONFIG.with_suffix(f'.{os.getpid()}.new')
    fd=os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as f:
            f.write(output)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, CONFIG)
        os.chmod(CONFIG, 0o600)
    finally:
        if tmp.exists(): tmp.unlink()


def video_node_name(real):
    """Best-effort kernel-advertised model/name, without opening the camera."""
    try:
        value = (Path('/sys/class/video4linux') / Path(real).name / 'name').read_text().strip()
        return value[:70]
    except OSError:
        return ''


def video_type_hint(real):
    """A hint, never a security assertion about an IR sensor.

    V4L2 may expose two nodes with the same USB product name. If metadata
    cannot distinguish them, say so instead of guessing by /dev/video number.
    """
    name = video_node_name(real)
    if re.search(r'(?i)(?:\binfrared\b|\bIR\b|\bir camera\b)', name):
        return '红外 IR'
    if re.search(r'(?i)(?:\bcolor\b|\bcolour\b|\bRGB\b)', name):
        return 'RGB 彩色'
    # Do not perform blocking V4L2 ioctl/subprocesses on the GTK main thread.
    # The daemon-owned camera worker does an asynchronous sample-based probe.
    return '类型待确认'


def short_device_label(path, kind=None):
    real = os.path.realpath(path)
    typ = kind or video_type_hint(real)
    name = video_node_name(real)
    # The selectable panel wraps long names; never truncate device metadata.
    suffix = ' · ' + name if name else ''
    return f'{typ} · {Path(real).name}{suffix}'


# VIDIOC_QUERYCAP from linux/videodev2.h: _IOR('V', 0, struct v4l2_capability)
# The struct is 104 bytes (16+32+32+4+4+4+12).
VIDIOC_QUERYCAP = 0x80685600
V4L2_CAP_VIDEO_CAPTURE = 0x00000001
V4L2_CAP_VIDEO_CAPTURE_MPLANE = 0x00001000
V4L2_CAP_DEVICE_CAPS = 0x80000000
V4L2_CAPTURE_MASK = V4L2_CAP_VIDEO_CAPTURE | V4L2_CAP_VIDEO_CAPTURE_MPLANE


def is_video_capture_node(device_path):
    """Return True/False if V4L2 capabilities are known; None if unreadable.

    /dev/video nodes may be metadata-only (even when belonging to the same
    physical USB webcam). The active VIDEO_CAPTURE flags matter, *not* the
    product name or numeric /dev/video index. Keep unknown nodes visible to
    avoid hiding a camera when its ioctl temporarily fails (busy/permissions).
    """
    fd = None
    try:
        fd = os.open(device_path, os.O_RDONLY | os.O_NONBLOCK | os.O_CLOEXEC)
        buf = bytearray(104)
        fcntl.ioctl(fd, VIDIOC_QUERYCAP, buf, True)
        # capabilities and device_caps at offsets 84, 88 respectively.
        capabilities, device_caps = struct.unpack_from('=II', buf, 84)
        active = device_caps if capabilities & V4L2_CAP_DEVICE_CAPS else capabilities
        return bool(active & V4L2_CAPTURE_MASK)
    except (OSError, ValueError, struct.error):
        return None
    finally:
        if fd is not None:
            os.close(fd)


def list_video_devices():
    paths = {}
    patterns = ('/dev/v4l/by-id/*', '/dev/v4l/by-path/*', '/dev/video[0-9]*')
    for pattern in patterns:
        for p in sorted(glob.glob(pattern)):
            real = os.path.realpath(p)
            if not re.fullmatch(r'/dev/video\d+', real):
                continue
            try:
                if not stat.S_ISCHR(os.stat(real).st_mode):
                    continue
            except OSError:
                continue
            # Prioritize stable symlinks and dedupe by resolved video node.
            paths.setdefault(real, p)
    visible = []
    for real, path in sorted(paths.items(), key=lambda kv: int(kv[0].removeprefix('/dev/video'))):
        capture = is_video_capture_node(real)
        if capture is False:
            continue  # discard metadata/output-only video nodes
        label = short_device_label(path)
        if capture is None:
            label += ' · 采集能力待确认'
        visible.append((path, label))
    return visible


def migrate_obsolete_settings():
    """Normalize user-owned 0.1.x config, never touch system PAM or templates.

    The former 3D option never provided authentication. If selected in an old
    GUI, migrate it to the explicitly documented experimental heuristic mode.
    Keep a private backup, and drop the unused third-device path.
    """
    try:
        text = CONFIG.read_text(encoding='utf-8')
    except FileNotFoundError:
        return False
    obsolete = any(line.strip().startswith('depth_device=') for line in text.splitlines())
    obsolete |= any(line.split('#', 1)[0].strip() == 'liveness=depth3d'
                    for line in text.splitlines())
    if not obsolete:
        return False
    import shutil
    backup = CONFIG.with_name('config.before-v0.1.4.bak')
    if not backup.exists():
        fd = os.open(backup, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        try:
            with os.fdopen(fd, 'wb') as out:
                out.write(text.encode('utf-8'))
        except BaseException:
            backup.unlink(missing_ok=True)
            raise
    save_config(load_config())
    return True

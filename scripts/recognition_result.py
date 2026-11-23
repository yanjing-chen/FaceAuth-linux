"""Interpret FaceAuth daemon TEST replies for an explicit, stable UI result.

No GTK imports: unit-testable without a desktop/camera; never turn an
incomplete or unknown daemon reply into an authentication success.
"""
from dataclasses import dataclass
import math


@dataclass(frozen=True)
class RecognitionResult:
    state: str  # passed, rejected, unavailable
    heading: str
    body: str


MODE_NAMES = {'rgb': 'RGB 彩色', 'ir': 'IR 红外', 'dual': 'RGB + IR 双模'}
REASON_NAMES = {
    'mismatch': '相似度未达到匹配阈值',
    'liveness': '活体检测未通过',
}
UNAVAILABLE = {
    'no-template': '尚未录入人脸；请先完成录入',
    'no-config': '缺少有效摄像头配置',
    'corrupt-template': '已保存的人脸模板损坏',
    'unavailable-or-processing-error': '摄像头、模型或识别处理暂不可用',
    'worker': '识别工作进程暂不可用',
}


def interpret_test_reply(reply: str, threshold: object = 0.6) -> RecognitionResult:
    """TEST is successful *only* for the explicit `OK ... result=accepted`."""
    tokens = str(reply).strip().split()
    if not tokens:
        return RecognitionResult('unavailable', '无法完成识别测试', '认证服务没有返回有效结果。')
    code = tokens[0]
    fields = {}
    for token in tokens[1:]:
        if '=' in token:
            k, v = token.split('=', 1)
            if k and v:
                fields[k] = v
    try:
        number = float(fields.get('sim', 'nan'))
        similarity = f'{number:.3f}' if math.isfinite(number) and -1 <= number <= 1 else '未提供'
    except ValueError:
        similarity = '未提供'
    try:
        t = float(threshold)
        threshold_text = f'{t:.3f}' if math.isfinite(t) and 0 <= t <= 1 else '未设置'
    except (TypeError, ValueError):
        threshold_text = '未设置'
    mode = MODE_NAMES.get(fields.get('mode', ''), fields.get('mode', '未提供'))
    face = fields.get('face', '未识别')
    base = f'模式：{mode}\n匹配人脸：{face}\n相似度：{similarity}（阈值：{threshold_text}）'
    outcome = fields.get('result', '')
    if code == 'OK' and outcome == 'accepted':
        # If the daemon claims success but omits a meaningful similarity/mode/
        # face, or claims a score below threshold, do not present a false pass.
        if (similarity == '未提供' or threshold_text == '未设置'
                or not fields.get('mode') or not fields.get('face')
                or number < t):
            return RecognitionResult('unavailable', '无法确认识别结果',
                                     '认证服务返回的通过结果缺少必要数据或与阈值矛盾，请检查后端日志。')
        return RecognitionResult('passed', '识别通过', base + '\n结果：人脸匹配且活体检查通过。')
    if code == 'DENIED':
        reason = REASON_NAMES.get(outcome, '识别未达到通过条件')
        return RecognitionResult('rejected', '识别未通过', base + f'\n原因：{reason}。\n可以重新面对摄像头测试；系统密码认证不受影响。')
    if code == 'IGNORE':
        reason = UNAVAILABLE.get(tokens[1] if len(tokens) > 1 else '', '认证服务未能完成本次识别')
        return RecognitionResult('unavailable', '无法完成识别测试', f'{reason}。\n状态：IGNORE；不能据此判断人脸是否匹配。')
    if code == 'ERR':
        error = str(reply)[4:].strip()[:160] or '未知服务错误'
        if 'timed out' in error.lower() or 'timeout' in error.lower():
            error = '认证服务响应超时，请检查摄像头和推理进程是否正常'
        return RecognitionResult('unavailable', '识别测试出错', error)
    return RecognitionResult('unavailable', '无法确认识别结果', '认证服务返回了不符合 TEST 协议的响应，未视为通过。')

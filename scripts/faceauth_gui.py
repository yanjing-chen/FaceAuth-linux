#!/usr/bin/env python3
"""FaceAuth 0.1.8 GNOME/Wayland GUI: no root, no direct V4L2 access, no photos saved.

All capture is daemon-owned. GTK only draws the latest in-memory JPEG from a
background IPC client. No network, disk frame buffers, or GTK calls off-thread.
"""
import argparse
import getpass
import glob
import os
from pathlib import Path
import queue
import re
import socket
import sys
import threading
import time
import traceback

import gi
gi.require_version('Gtk', '4.0')
gi.require_version('Gdk', '4.0')
gi.require_version('Adw', '1')
from gi.repository import Adw, Gdk, GLib, Gtk, Pango

from client import (request, current_socket, load_config, save_config,
                    migrate_obsolete_settings, list_video_devices,
                    short_device_label, valid_label, require_admin_auth)
from recognition_result import interpret_test_reply

MODES = [('rgb', '单 RGB（彩色）'), ('ir', '单 IR（红外）'), ('dual', '双模 RGB + IR')]
LIVENESS = [('none', '无活体（存在风险）'), ('heuristic', 'IR / 图像启发式（有限防伪）')]
# 认证帧数档位：帧数越多越稳，但认证耗时略增（PAM 5 秒超时上限）。
AUTH_FRAMES = [('1', '快速（1 帧）'), ('3', '均衡（3 帧）'), ('5', '稳健（5 帧）')]
SCENES = [('sudo', 'sudo 提权'), ('gdm', 'GDM 登录 / 锁屏'),
          ('su', 'su 用户切换'), ('login', 'TTY 登录'), ('polkit', 'polkit 图形授权')]



class ReadableChoiceRow(Adw.ActionRow):
    """Wide selector: selected value is visible on the button, never '选择…'.

    The short caption stays legible in the settings column; the selected full
    name/path is shown in the subtitle and the expanded, scrollable popover.
    Labels are plain text, not markup (device names are untrusted strings).
    """
    def __init__(self, title, choices, selected=0, on_change=None, compact=False):
        super().__init__(title=title)
        self.set_use_markup(False)
        self.set_title_lines(2)
        self.set_subtitle_lines(2)
        self._choices = []
        self._index = 0
        self._on_change = on_change
        self._compact = compact
        self._choose = Gtk.MenuButton()
        self._choose.set_size_request(176 if compact else 264, 42)
        self._choose.set_valign(Gtk.Align.CENTER)
        self._choose.set_halign(Gtk.Align.END)
        self.add_suffix(self._choose)
        self.set_activatable_widget(self._choose)
        self._popover = Gtk.Popover()
        self._popover.set_position(Gtk.PositionType.BOTTOM)
        self._scroll = Gtk.ScrolledWindow()
        self._scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        self._scroll.set_size_request(570, 240)
        self._popover.set_child(self._scroll)
        self._choose.set_popover(self._popover)
        self.set_options(choices, selected=selected)

    def set_options(self, choices, selected=None):
        """Choice = (full title, details [, concise button caption])."""
        self._choices = [(str(c[0]), str(c[1]), str(c[2]) if len(c) > 2 else str(c[0]))
                         for c in choices]
        listing = Gtk.ListBox()
        listing.set_selection_mode(Gtk.SelectionMode.NONE)
        listing.add_css_class('boxed-list')
        for title, detail, _caption in self._choices:
            item = Gtk.ListBoxRow()
            content = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
            for edge in ('top', 'bottom', 'start', 'end'):
                getattr(content, 'set_margin_' + edge)(11)
            name = Gtk.Label(label=title)
            name.set_xalign(0)
            name.set_wrap(True)
            name.set_wrap_mode(Pango.WrapMode.WORD_CHAR)
            name.set_max_width_chars(74)
            content.append(name)
            if detail:
                description = Gtk.Label(label=detail)
                description.add_css_class('dim-label')
                description.set_xalign(0)
                description.set_wrap(True)
                description.set_wrap_mode(Pango.WrapMode.CHAR)
                description.set_max_width_chars(76)
                content.append(description)
            item.set_child(content)
            listing.append(item)
        listing.connect('row-activated', self._activated)
        self._scroll.set_child(listing)
        self._scroll.set_size_request(570, min(445, max(120, len(self._choices)*84)))
        if selected is None:
            selected = self._index
        self.set_selected(selected, notify=False)

    def set_selected(self, index, notify=False):
        if not self._choices:
            return
        if not (0 <= index < len(self._choices)):
            index = 0
        changed = index != self._index
        self._index = index
        title, detail, caption = self._choices[index]
        self._choose.set_label(caption)  # immediately replaces '选择…'
        self._choose.set_tooltip_text(title + ('\n'+detail if detail else ''))
        # Avoid duplicating a very long V4L2 path in the narrow row. Full text
        # is always available in the popover and the mouseover tooltip.
        # In a two-column layout, avoid showing a long, unbreakable /dev/v4l
        # path as a subtitle; the full path remains in the popover and tooltip.
        if self._compact and detail.startswith('完整路径：'):
            self.set_subtitle(title)
        else:
            self.set_subtitle(detail if detail else title)
        self.set_tooltip_text(title + ('\n' + detail if detail else ''))
        if notify and changed and self._on_change:
            self._on_change(self, None)

    def get_selected(self):
        return self._index

    def _activated(self, _listing, row):
        self._popover.popdown()
        self.set_selected(row.get_index(), notify=True)

class PreviewClient:
    def __init__(self, user, test_pattern=False):
        self.user = user
        self.test_pattern = test_pattern
        self.last_seq = 0
        self._lock = threading.Lock()
        self._last_jpeg = None
        self.events = queue.Queue()
        self._stop = threading.Event()
        self._enabled = threading.Event()
        self._thread = threading.Thread(target=self._loop, daemon=True, name='faceauth-preview-ipc')

    def start(self):
        self._thread.start()

    def stop(self):
        self._enabled.clear()
        self._stop.set()

    def pause(self):
        self._enabled.clear()

    def resume(self):
        self._enabled.set()

    def is_active(self):
        return self._enabled.is_set()

    def newest(self, seq):
        with self._lock:
            return self._last_jpeg if self._last_jpeg and self._last_jpeg[0] != seq else None

    def _loop(self):
        last_err = None
        while not self._stop.is_set():
            if not self._enabled.is_set():
                self._stop.wait(0.08)
                continue
            if self.test_pattern:
                self._stop.wait(0.25)
                continue
            try:
                msg = request(f'PREVIEW {self.user}', timeout=6.0, max_bytes=550000)
                if not msg.startswith('OK '):
                    raise RuntimeError(msg[:100])
                payload = bytes.fromhex(msg[3:])
                if len(payload) < 100 or len(payload) > 250000:
                    raise ValueError('invalid JPEG length')
                with self._lock:
                    self.last_seq += 1
                    self._last_jpeg = (self.last_seq, payload)
                if last_err is not None:
                    self.events.put(('status', '摄像头预览已恢复'))
                last_err = None
                self._stop.wait(0.20)  # never flood the daemon
            except Exception as exc:
                summary = str(exc)[:100]
                if summary != last_err:
                    self.events.put(('status', '预览暂不可用：' + summary))
                last_err = summary
                self._stop.wait(1.25)


class FaceAuthApp(Adw.Application):
    def __init__(self, test_pattern=False):
        super().__init__(application_id='org.faceauth.Rebuild')
        self.user = getpass.getuser()
        self.migrated_old_settings = migrate_obsolete_settings() if not test_pattern else False
        self.cfg = load_config()
        self.test_pattern = test_pattern
        self.preview = PreviewClient(self.user, test_pattern)
        self.events = queue.Queue()
        self.busy = False
        self.alive = True
        self.seq = 0
        self.frame_count = 0
        self.win = None
        self.devices = list_video_devices()
        self.device_paths = [p for p, _ in self.devices]
        self._setting_devices = False
        self.detected_types = {}
        self.device_status_row = None
        self.progress = None
        self.face_box = None
        self.scene_rows = {}
        self._setting_scenes = False
        self._scene_pending = set()
        self._test_in_progress = False
        self.test_feedback = None
        self.test_feedback_title = None
        self.test_feedback_body = None

    def _mode_caption(self):
        return {'rgb': 'RGB 彩色', 'ir': 'IR 红外', 'dual': 'RGB + IR 双模'}.get(
            self.cfg.get('mode'), '摄像头模式未配置')

    def do_activate(self):
        if self.win:
            self.win.present()
            return
        win = Adw.ApplicationWindow(application=self, title='FaceAuth')
        self.win = win
        win.set_default_size(980, 650)
        win.set_size_request(790, 550)
        # 窗口/任务栏图标：与桌面入口使用同一个已安装的图标名
        win.set_icon_name('faceauth')
        css = Gtk.CssProvider()
        css.load_from_path(str(Path(__file__).with_name('gui_style.css')))
        Gtk.StyleContext.add_provider_for_display(
            Gdk.Display.get_default(), css, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)
        header = Adw.HeaderBar()
        switcher = Adw.ViewSwitcher()
        switcher.set_policy(Adw.ViewSwitcherPolicy.WIDE)
        header.set_title_widget(switcher)
        stack = Adw.ViewStack()
        switcher.set_stack(stack)
        toolbar = Adw.ToolbarView()
        toolbar.add_top_bar(header)
        toolbar.set_content(stack)
        win.set_content(toolbar)

        # Overview: one compact header, large video, dedicated action column.
        page = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=15)
        page.add_css_class('fa-overview')
        for edge in ('top', 'bottom', 'start', 'end'):
            getattr(page, 'set_margin_' + edge)(20)
        heading = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
        heading.set_valign(Gtk.Align.CENTER)
        brand = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        brand.set_hexpand(True)
        title = Gtk.Label(label='人脸认证')
        title.add_css_class('title-2')
        title.set_xalign(0)
        brand.append(title)
        hint = Gtk.Label(label='本地识别  ·  图像不上传')
        hint.set_xalign(0)
        hint.add_css_class('dim-label')
        brand.append(hint)
        heading.append(brand)
        self.mode_badge = Gtk.Label(label=self._mode_caption())
        self.mode_badge.add_css_class('fa-mode-badge')
        heading.append(self.mode_badge)
        page.append(heading)

        workspace = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=16)
        workspace.set_hexpand(True)
        page.append(workspace)

        left = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        left.set_hexpand(True)
        workspace.append(left)
        camera = Gtk.Overlay()
        camera.add_css_class('fa-camera')
        camera.set_hexpand(True)
        camera.set_vexpand(True)
        self.picture = Gtk.Picture()
        self.picture.set_size_request(460, 350)
        self.picture.set_hexpand(True)
        self.picture.set_vexpand(True)
        self.picture.set_content_fit(Gtk.ContentFit.CONTAIN)
        camera.set_child(self.picture)
        live = Gtk.Label(label='●  实时取景')
        live.add_css_class('fa-live-tag')
        live.set_halign(Gtk.Align.START)
        live.set_valign(Gtk.Align.START)
        live.set_margin_start(14)
        live.set_margin_top(14)
        camera.add_overlay(live)
        left.append(camera)
        camera_note = Gtk.Label(label='摄像头由认证服务管理 · 录入与识别后自动恢复预览')
        camera_note.add_css_class('dim-label')
        camera_note.add_css_class('fa-small-note')
        camera_note.set_xalign(0)
        camera_note.set_wrap(True)
        left.append(camera_note)

        sidebar = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        sidebar.add_css_class('fa-sidebar')
        sidebar.set_size_request(270, -1)
        sidebar.set_valign(Gtk.Align.START)
        workspace.append(sidebar)
        actions_heading = Gtk.Label(label='录入与测试')
        actions_heading.add_css_class('heading')
        actions_heading.set_xalign(0)
        sidebar.append(actions_heading)
        action_hint = Gtk.Label(label='请面向镜头，确保只有一张清晰的人脸')
        action_hint.add_css_class('dim-label')
        action_hint.set_wrap(True)
        action_hint.set_xalign(0)
        sidebar.append(action_hint)
        name_label = Gtk.Label(label='人脸名称')
        name_label.add_css_class('fa-field-label')
        name_label.set_xalign(0)
        sidebar.append(name_label)
        self.label_entry = Gtk.Entry()
        self.label_entry.set_placeholder_text('例如：我')
        self.label_entry.set_text('我')
        self.label_entry.set_hexpand(True)
        sidebar.append(self.label_entry)
        self.enroll_btn = Gtk.Button(label='录入人脸')
        self.enroll_btn.add_css_class('suggested-action')
        self.enroll_btn.add_css_class('fa-action-button')
        self.enroll_btn.connect('clicked', self.on_enroll)
        sidebar.append(self.enroll_btn)
        self.test_btn = Gtk.Button(label='测试人脸识别')
        self.test_btn.add_css_class('fa-action-button')
        self.test_btn.connect('clicked', self.on_test)
        sidebar.append(self.test_btn)
        self.progress = Gtk.ProgressBar()
        self.progress.add_css_class('fa-progress')
        sidebar.append(self.progress)
        self.status = Gtk.Label(label='正在连接认证服务…')
        self.status.add_css_class('fa-status')
        self.status.set_xalign(0)
        self.status.set_wrap(True)
        self.status.set_selectable(True)
        sidebar.append(self.status)

        # Concise inline verdict plus a native Adw.AlertDialog.
        self.test_feedback = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        self.test_feedback.add_css_class('fa-result')
        self.test_feedback_title = Gtk.Label(label='')
        self.test_feedback_title.add_css_class('heading')
        self.test_feedback_title.set_xalign(0)
        self.test_feedback_title.set_wrap(True)
        self.test_feedback_body = Gtk.Label(label='')
        self.test_feedback_body.add_css_class('dim-label')
        self.test_feedback_body.set_xalign(0)
        self.test_feedback_body.set_wrap(True)
        self.test_feedback_body.set_selectable(True)
        self.test_feedback.append(self.test_feedback_title)
        self.test_feedback.append(self.test_feedback_body)
        self.test_feedback.set_visible(False)
        sidebar.append(self.test_feedback)
        if self.test_pattern:
            self.enroll_btn.set_sensitive(False)
            self.test_btn.set_sensitive(False)
            self.status.set_label('动态图案测试（无摄像头、无认证）')
        preview_clamp = Adw.Clamp()
        preview_clamp.set_maximum_size(1010)
        preview_clamp.set_child(page)
        preview_scroll = Gtk.ScrolledWindow()
        preview_scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        preview_scroll.set_child(preview_clamp)
        stack.add_titled(preview_scroll, 'enroll', '录入 / 测试').set_icon_name('camera-photo-symbolic')

        # Settings dashboard: same two-column visual grammar as the first page.
        # This is presentation-only: all existing callbacks and daemon commands
        # remain unchanged. No PAM service is mounted by merely opening a page.
        scroll = Gtk.ScrolledWindow()
        scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        settings = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=18)
        settings.add_css_class('fa-settings')
        for edge in ('top', 'bottom', 'start', 'end'):
            getattr(settings, 'set_margin_' + edge)(20)

        settings_header = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
        settings_header.set_valign(Gtk.Align.CENTER)
        header_copy = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=3)
        header_copy.set_hexpand(True)
        settings_title = Gtk.Label(label='设置与管理')
        settings_title.add_css_class('title-2')
        settings_title.set_xalign(0)
        header_copy.append(settings_title)
        settings_desc = Gtk.Label(label='配置识别设备、管理人脸及使用场景')
        settings_desc.add_css_class('dim-label')
        settings_desc.set_xalign(0)
        header_copy.append(settings_desc)
        settings_header.append(header_copy)
        self.refresh_btn = Gtk.Button(label='刷新状态')
        self.refresh_btn.add_css_class('fa-refresh-button')
        self.refresh_btn.set_valign(Gtk.Align.CENTER)
        self.refresh_btn.connect('clicked', lambda *_: self.refresh_lists())
        settings_header.append(self.refresh_btn)
        settings.append(settings_header)

        settings_grid = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=16)
        settings_grid.add_css_class('fa-settings-grid')
        settings_grid.set_hexpand(True)
        settings.append(settings_grid)
        hardware_column = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=18)
        hardware_column.set_hexpand(True)
        hardware_column.add_css_class('fa-settings-column')
        settings_grid.append(hardware_column)
        account_column = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=18)
        account_column.set_hexpand(True)
        account_column.add_css_class('fa-settings-column')
        settings_grid.append(account_column)

        # Left: recognition options and RGB / IR devices.
        group = Adw.PreferencesGroup(title='识别方式',
            description='双模以 RGB 识别身份、IR 辅助活体检查')
        self.mode_row = ReadableChoiceRow('工作模式', [
            (MODES[0][1], '只使用彩色摄像头进行识别与预览', '单 RGB（彩色）'),
            (MODES[1][1], '只使用红外摄像头进行识别与预览', '单 IR（红外）'),
            (MODES[2][1], 'RGB 提取人脸特征；IR 采集另一帧进行活体启发式检查', '双模 RGB + IR')],
            selected=next((i for i, (key, _) in enumerate(MODES) if key == self.cfg['mode']), 2),
            on_change=self.change_mode, compact=True)
        group.add(self.mode_row)
        self.live_row = ReadableChoiceRow('活体检测', [
            (LIVENESS[0][1], '不检查活体，照片/显示屏可能骗过识别', '无活体'),
            (LIVENESS[1][1], '启发式 IR / 图像纹理评分，仅供实验；不等同硬件级活体', '启发式（实验）')],
            selected=next((i for i, (key, _) in enumerate(LIVENESS) if key == self.cfg['liveness']), 1),
            on_change=self.change_liveness, compact=True)
        group.add(self.live_row)
        # 识别灵敏度：数值框 + 上下小按钮，可在 0.30~0.95 间以 0.01 精细调节。
        # Adw.SpinRow 是 ActionRow 子类，放进 PreferencesGroup 与其余行风格一致。
        self._threshold_init = True
        try:
            initial_threshold = float(self.cfg.get('threshold', 0.6))
        except (TypeError, ValueError):
            initial_threshold = 0.6
        initial_threshold = min(0.95, max(0.30, initial_threshold))
        self.threshold_row = Adw.SpinRow(
            title='识别灵敏度（阈值）',
            subtitle='数值越小越易通过、越大越安全；可用上下按钮以 0.01 微调')
        self.threshold_row.set_adjustment(Gtk.Adjustment.new(
            initial_threshold, 0.30, 0.95, 0.01, 0.05, 0.0))
        self.threshold_row.set_digits(2)
        self.threshold_row.set_numeric(True)
        self.threshold_row.connect('notify::value', self.change_threshold)
        self._threshold_init = False
        group.add(self.threshold_row)
        hardware_column.append(group)

        # 高级识别策略：本地保存，与管理员授权的 PAM 场景开关分开。
        adv_group = Adw.PreferencesGroup(title='识别策略',
            description='本地生效，无需管理员授权')
        self.frames_row = ReadableChoiceRow('识别帧数', [
            (AUTH_FRAMES[0][1], '只取 1 帧：最快（约 1 秒），瞬态波动可能导致偶发不通过', '快速 1 帧'),
            (AUTH_FRAMES[1][1], '取 3 帧择优：默认，兼顾速度与稳定性（约 2-3 秒）', '均衡 3 帧'),
            (AUTH_FRAMES[2][1], '取 5 帧择优：最稳，但接近 PAM 5 秒超时上限', '稳健 5 帧')],
            selected=next((i for i, (key, _) in enumerate(AUTH_FRAMES)
                           if key == str(self.cfg.get('auth_frames', '3'))), 1),
            on_change=self.change_auth_frames, compact=True)
        adv_group.add(self.frames_row)
        self.dark_skip_row = Adw.SwitchRow(title='跳过过暗的 IR 帧',
            subtitle='IR 补光闪烁会产生全黑帧，自动丢弃可避免活体误判')
        self.dark_skip_row.set_active(self.cfg.get('dark_ir_skip', '1') == '1')
        self.dark_skip_row.connect('notify::active', self.on_dark_skip)
        adv_group.add(self.dark_skip_row)
        self.ssh_row = Adw.SwitchRow(title='SSH 远程会话禁用刷脸',
            subtitle='远程登录时自动回退密码（安全默认，需 PAM 模块支持）')
        self.ssh_row.set_active(self.cfg.get('ssh_disabled', '1') == '1')
        self.ssh_row.connect('notify::active', self.on_ssh_disabled)
        adv_group.add(self.ssh_row)
        hardware_column.append(adv_group)

        dev_group = Adw.PreferencesGroup(title='摄像头',
            description='支持复合模组或两台独立摄像头；完整路径可在菜单中查看')
        self.rgb_choice = self.device_picker('RGB 彩色', 'rgb_device', dev_group)
        self.ir_choice = self.device_picker('IR 红外', 'ir_device', dev_group)
        self.device_status_row = Adw.ActionRow(title='视频通道检测',
            subtitle='点击检查按钮以读取所选摄像头的实际画面')
        self.device_status_row.set_subtitle_lines(4)
        check_channels = Gtk.Button(label='检查两路')
        check_channels.set_valign(Gtk.Align.CENTER)
        check_channels.add_css_class('fa-small-button')
        check_channels.connect('clicked', self.check_camera_channels)
        self.device_status_row.add_suffix(check_channels)
        dev_group.add(self.device_status_row)
        rescan_actions = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        rescan_actions.set_halign(Gtk.Align.END)
        rescan_actions.add_css_class('fa-settings-actions')
        self.refresh_devices_button = Gtk.Button(label='重新扫描设备')
        self.refresh_devices_button.add_css_class('flat')
        self.refresh_devices_button.connect('clicked', lambda *_: self.rescan_devices(dev_group))
        rescan_actions.append(self.refresh_devices_button)
        dev_group.add(rescan_actions)
        hardware_column.append(dev_group)
        self.update_camera_row_visibility()

        # Right: enrolled templates and system authentication switches.
        face_group = Adw.PreferencesGroup(title='已录入人脸',
            description='仅保存特征向量，不保存原始照片')
        self.face_box = Gtk.ListBox()
        self.face_box.add_css_class('boxed-list')
        face_group.add(self.face_box)
        delete_actions = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL)
        delete_actions.set_halign(Gtk.Align.END)
        delete_actions.add_css_class('fa-settings-actions')
        self.delete_all = Gtk.Button(label='删除全部人脸')
        self.delete_all.add_css_class('destructive-action')
        self.delete_all.connect('clicked', self.on_delete_all)
        delete_actions.append(self.delete_all)
        face_group.add(delete_actions)
        account_column.append(face_group)

        scene_group = Adw.PreferencesGroup(title='系统认证',
            description='需要管理员授权；刷脸失败保留系统密码回退')
        for name, title in SCENES:
            r = Adw.SwitchRow(title=title)
            # Explanation belongs to the group, not repeated across five rows.
            r.set_sensitive(False)  # enabled only after reading daemon state
            r.connect('notify::active', self.scene_toggled, name)
            scene_group.add(r)
            self.scene_rows[name] = r
        account_column.append(scene_group)

        footer = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=7)
        footer.add_css_class('fa-settings-footer')
        info_icon = Gtk.Image.new_from_icon_name('dialog-information-symbolic')
        footer.append(info_icon)
        self.settings_feedback = Gtk.Label(label='IR 活体属于实验性辅助判断，不能代替系统密码。')
        self.settings_feedback.set_wrap(True)
        self.settings_feedback.set_xalign(0)
        self.settings_feedback.add_css_class('dim-label')
        footer.append(self.settings_feedback)
        settings.append(footer)

        settings_clamp = Adw.Clamp()
        settings_clamp.set_maximum_size(1120)
        settings_clamp.set_child(settings)
        scroll.set_child(settings_clamp)
        stack.add_titled(scroll, 'settings', '设置 / 管理').set_icon_name('preferences-system-symbolic')

        if self.test_pattern:
            for widget in (self.mode_row, self.live_row, self.rgb_choice,
                           self.ir_choice, self.delete_all, self.refresh_btn):
                widget.set_sensitive(False)
        win.connect('close-request', self.on_close)
        win.present()
        if self.migrated_old_settings:
            notice = Adw.AlertDialog()
            notice.set_heading('已移除旧版 3D 设置')
            notice.set_body('已保留旧配置备份，并删除不再使用的深度设备设置。'
                            '旧版不可用的 3D 活体选项改为实验级启发式检查；'
                            '请确认 RGB / IR 配置后重新执行识别测试。')
            notice.add_response('ok', '知道了')
            notice.present(win)
        self.preview.start()
        if not self.test_pattern:
            self.async_request(f'PREVIEW_START {self.user}', self.preview_started)
        else:
            self.preview.resume()
        self.refresh_lists()
        GLib.timeout_add(80, self.tick)
        print(f'FaceAuth rebuild GUI v0.1.8 start pid={os.getpid()} socket={current_socket()}', file=sys.stderr, flush=True)

    def device_choice_labels(self, key):
        labels = ['（不配置此摄像头）']
        for path, initial in self.devices:
            real = os.path.realpath(path)
            detected = self.detected_types.get(real)
            if detected == 'RGB':
                kind = '实测彩色 RGB'
            elif detected == 'MONO':
                kind = '实测单色（疑似 IR，需确认）'
            elif self.cfg.get(key, '') and os.path.realpath(self.cfg[key]) == real:
                kind = '已设置 RGB 角色' if key == 'rgb_device' else '已设置 IR 角色'
            else:
                kind = None
            labels.append(short_device_label(path, kind=kind) if kind else initial)
        return labels

    def device_choice_options(self, key):
        labels = self.device_choice_labels(key)
        choices = [(labels[0], '此通道不使用摄像头', '未配置')]
        for (path, _), name in zip(self.devices, labels[1:]):
            real = os.path.realpath(path)
            role = 'RGB' if key == 'rgb_device' else 'IR'
            # Keep button text short and specific; the full USB name and path
            # remain unabridged in the popover (and tooltip).
            caption = f'{role} · {os.path.basename(real)}'
            choices.append((name, '完整路径：' + path, caption))
        return choices

    def device_picker(self, name, key, group):
        selected = next((i+1 for i, p in enumerate(self.device_paths)
                         if os.path.realpath(p) == os.path.realpath(self.cfg.get(key, ''))), 0)
        row = ReadableChoiceRow(name, self.device_choice_options(key),
                                selected=selected,
                                on_change=lambda obj, _unused: self.device_changed(obj, None, key),
                                compact=True)
        group.add(row)
        return row

    def rescan_devices(self, _group=None):
        self.devices = list_video_devices()
        self.device_paths = [p for p, _ in self.devices]
        self._setting_devices = True
        try:
            for key, row in [('rgb_device', self.rgb_choice), ('ir_device', self.ir_choice)]:
                selected = next((i+1 for i, p in enumerate(self.device_paths)
                                 if os.path.realpath(p) == os.path.realpath(self.cfg.get(key, ''))), 0)
                row.set_options(self.device_choice_options(key), selected=selected)
        finally:
            self._setting_devices = False
        self.status.set_label(f'发现 {len(self.devices)} 个可采集/待确认的视频节点（已过滤纯元数据节点）')
        self.settings_feedback.set_label(f'已扫描 {len(self.devices)} 个视频节点（已过滤纯元数据节点）')

    def device_changed(self, row, _spec, key):
        if self._setting_devices:
            return
        idx = row.get_selected()
        self.cfg[key] = self.device_paths[idx-1] if 1 <= idx <= len(self.device_paths) else ''
        self.save_settings('设备已更改，请核对所选摄像头的实际图像')
        self.rescan_devices()
        self.async_request(f'DEVICE_TYPES {self.user}', self.apply_detected_types)

    def update_camera_row_visibility(self):
        mode = self.cfg.get('mode', 'dual')
        self.rgb_choice.set_visible(mode != 'ir')
        self.ir_choice.set_visible(mode != 'rgb')

    def change_mode(self, row, _spec):
        self.cfg['mode'] = MODES[row.get_selected()][0]
        self.update_camera_row_visibility()
        self.mode_badge.set_label(self._mode_caption())
        self.save_settings('工作模式已切换；RGB / IR 模板不可互用，建议重新录入')

    def change_liveness(self, row, _spec):
        self.cfg['liveness'] = LIVENESS[row.get_selected()][0]
        self.save_settings('活体设置已保存；启发式检查不等同安全认证')

    def change_threshold(self, row, _prop):
        if getattr(self, '_threshold_init', False):
            return
        value = min(0.95, max(0.30, row.get_value()))
        self.cfg['threshold'] = f'{value:.2f}'
        self.save_settings('识别灵敏度已保存；调低更易通过但安全性下降，请重新做识别测试')

    def change_auth_frames(self, row, _spec):
        self.cfg['auth_frames'] = AUTH_FRAMES[row.get_selected()][0]
        self.save_settings('识别帧数已保存；帧数越多越稳，但认证耗时略增')

    def on_dark_skip(self, row, _prop):
        self.cfg['dark_ir_skip'] = '1' if row.get_active() else '0'
        self.save_settings('暗 IR 帧跳过设置已保存')

    def on_ssh_disabled(self, row, _prop):
        self.cfg['ssh_disabled'] = '1' if row.get_active() else '0'
        self.save_settings('SSH 远程会话设置已保存；关闭后远程 SSH 中也会尝试刷脸')

    def save_settings(self, note):
        try:
            save_config(self.cfg)
            self.status.set_label(note)
            self.settings_feedback.set_label(note)
            if self.test_feedback is not None and self.test_feedback.get_visible():
                self.test_feedback_title.set_label('识别结果已过期 · 请重新测试')
                self.test_feedback_body.set_label('摄像头或识别配置已更改，上一次测试结果不适用于当前设置。')
        except Exception as exc:
            self.status.set_label('保存配置失败：' + str(exc))
            self.settings_feedback.set_label('保存配置失败：' + str(exc))

    def async_request(self, action, callback, timeout=8):
        def task():
            try:
                msg = request(action, timeout=timeout)
            except Exception as exc:
                msg = f'ERR {type(exc).__name__}: {exc}'
            self.events.put(('callback', callback, msg))
        threading.Thread(target=task, daemon=True, name='faceauth-rpc').start()

    def preview_started(self, response):
        if response.startswith('OK ') and self.alive and not self.busy:
            self.preview.resume()
            if not self.test_feedback.get_visible():
                self.status.set_label('摄像头持续预览中')
            # Determine sensor type from a sampled frame in the daemon-owned
            # camera worker. No direct V4L2 access in the GUI.
            self.async_request(f'DEVICE_TYPES {self.user}', self.apply_detected_types)
        elif not response.startswith('OK '):
            self.status.set_label('无法开启摄像头：' + response)

    def check_camera_channels(self, _button):
        if self.cfg['mode'] == 'dual' and self.cfg.get('rgb_device') and self.cfg.get('ir_device'):
            if os.path.realpath(self.cfg['rgb_device']) == os.path.realpath(self.cfg['ir_device']):
                self.device_status_row.set_subtitle('配置错误：RGB 与 IR 选择了同一个视频节点。双模必须分别选择不同节点。')
                return
        if self.device_status_row is not None:
            self.device_status_row.set_subtitle('正在读取 RGB / IR 实际图像，请稍候…')
        self.async_request(f'DEVICE_TYPES {self.user}', self.apply_detected_types, timeout=12)

    def apply_detected_types(self, response):
        parts = response.split()
        if len(parts) != 3 or parts[0] != 'OK':
            if self.device_status_row is not None:
                self.device_status_row.set_subtitle('采样失败：' + response[:160])
            return
        if self.device_status_row is not None:
            kind_label = {'RGB': '彩色画面', 'MONO': '单色画面（不保证为 IR）', '-': '未启用'}
            first = kind_label.get(parts[1], '未知格式')
            second = kind_label.get(parts[2], '未知格式')
            if self.cfg['mode'] == 'dual':
                status = f'已从两个不同的设备节点读取：RGB 通道 {first}；IR 通道 {second}。类型需人工核对。'
                if self.cfg['liveness'] == 'none':
                    status += ' 注意：目前关闭了 IR 活体判断！'
            elif self.cfg['mode'] == 'ir':
                status = f'单 IR 通道已读到 {second}；RGB 通道未启用。'
            else:
                status = f'单 RGB 通道已读到 {first}；IR 通道未启用。'
            self.device_status_row.set_subtitle(status)
        for field, kind in (('rgb_device',parts[1]), ('ir_device',parts[2])):
            path=self.cfg.get(field,'')
            if path and kind in ('RGB','MONO'):
                self.detected_types[os.path.realpath(path)] = kind
        self.rescan_devices()

    def resume_continuous_preview(self):
        if self.alive and not self.test_pattern:
            # daemon restores its preview lease after exclusive capture/auth;
            # reassert START if a preview session was lost due to an error.
            self.async_request(f'PREVIEW_START {self.user}', self.preview_started)

    def set_busy(self, value):
        self.busy = value
        self.enroll_btn.set_sensitive(not value and not self.test_pattern)
        self.test_btn.set_sensitive(not value and not self.test_pattern)
        self.test_btn.set_label('正在识别…' if value and self._test_in_progress else '识别测试')

    def on_enroll(self, _button):
        if self.busy or self.test_pattern:
            return
        label = self.label_entry.get_text().strip()
        if not valid_label(label):
            self.status.set_label('名称不得包含空格、逗号、斜杠，UTF-8 总长度不超过 64 字节')
            return
        self.set_busy(True)
        self.preview.pause()  # stop new preview polls before exclusive capture
        self.progress.set_fraction(0)
        # 先采集 5 帧（不弹授权框，避免 GNOME 模态框抢焦点导致采帧失败），
        # 采集成功后才请求管理员授权，授权通过再写入模板。
        self.status.set_label('正在采集 5 帧并进行活体检测，请正视摄像头…')
        self.async_request(f'ENROLL_CAPTURE {self.user} {label}', self.enroll_captured, timeout=28)

    def enroll_captured(self, response):
        # 采集阶段结束：恢复预览；此时特征只在内存，尚未写入模板。
        self.resume_continuous_preview()
        if not response.startswith('OK '):
            self.set_busy(False)
            self.progress.set_fraction(0)
            self.status.set_label('采集失败：' + response)
            return
        embedding = response[3:].strip()
        self.progress.set_fraction(0.6)
        # 授权由 daemon 弹框并当场判定（GUI 不自己弹，避免两次查询不缓存导致失败）。
        # 授权框由 GNOME 显示，GUI 仍是普通用户进程，取景框不受影响。
        self.status.set_label('采集完成，等待管理员授权写入…（请在弹框中输入密码/指纹）')
        label = self.label_entry.get_text().strip()
        self.async_request(
            f'ENROLL_AS {self.user} {label} {embedding}',
            self.enroll_done, timeout=60)

    def enroll_done(self, response):
        self.set_busy(False)
        ok = response.startswith('OK ')
        denied = 'authorization' in (response or '').lower()
        self.progress.set_fraction(1 if ok else 0)
        self.status.set_label(('录入成功：' if ok else '录入未完成：') + response + ' · 预览继续')
        self.resume_continuous_preview()
        dialog = Adw.AlertDialog()
        dialog.set_heading('录入成功' if ok else ('管理员授权未通过' if denied else '录入未完成'))
        dialog.set_body(
            '人脸已采集但未授权写入，特征已丢弃。' if denied
            else (response if response else ('已完成 5 帧采集并写入模板。' if ok else '请检查摄像头、IR 与人脸位置后重试。')))
        dialog.add_response('close', '关闭')
        dialog.set_default_response('close')
        dialog.set_close_response('close')
        dialog.present(self.win)
        if ok:
            self.refresh_lists()

    def on_test(self, _button):
        if self.busy or self.test_pattern:
            return
        self._test_in_progress = True
        self.set_busy(True)
        self.preview.pause()
        self.progress.set_fraction(0)
        self.test_feedback.set_visible(True)
        self.test_feedback_title.set_label('正在识别…')
        self.test_feedback_body.set_label('请面向摄像头。测试结束后将显示通过、未通过或无法完成的明确结果。')
        self.status.set_label('正在执行识别测试，摄像头将短暂让出给认证服务…')
        # Cold model initialization and queued preview requests can legitimately
        # exceed 15s. A timeout still returns an explicit unavailable verdict.
        self.async_request(f'TEST {self.user}', self.test_done, timeout=40)

    def test_done(self, response):
        if not self.alive:
            return
        result = interpret_test_reply(response, self.cfg.get('threshold', 0.6))
        self._test_in_progress = False
        self.set_busy(False)
        self.progress.set_fraction(0)
        self.test_feedback.set_visible(True)
        prefix = {'passed': '✓ ', 'rejected': '× ', 'unavailable': '! '}.get(result.state, '')
        self.test_feedback_title.set_label(prefix + result.heading)
        self.test_feedback_body.set_label(result.body)
        self.status.set_label('识别测试结束；摄像头预览恢复中…')
        # Resume preview without replacing the verdict, even if a delayed
        # PREVIEW_START/DEVICE_TYPES callback finishes after the TEST callback.
        self.resume_continuous_preview()
        dialog = Adw.AlertDialog()
        dialog.set_heading(result.heading)
        dialog.set_body(result.body)
        dialog.add_response('close', '关闭')
        dialog.set_default_response('close')
        dialog.set_close_response('close')
        dialog.present(self.win)

    def refresh_lists(self):
        if self.test_pattern:
            return
        self.async_request(f'LIST {self.user}', self.populate_faces)
        self.async_request(f'SCENE_LIST {self.user}', self.populate_scenes)

    def populate_faces(self, response):
        if self.face_box is None:
            return
        child = self.face_box.get_first_child()
        while child:
            nxt = child.get_next_sibling()
            self.face_box.remove(child)
            child = nxt
        names = response.split(' ', 2)
        labels = names[2].split(',') if len(names) == 3 and names[0] == 'OK' else []
        if not labels:
            self.face_box.append(Adw.ActionRow(title='暂无已录入的人脸'))
            return
        for label in labels:
            row = Adw.ActionRow(title=label)
            button = Gtk.Button(label='单独删除')
            button.add_css_class('flat')
            button.connect('clicked', lambda _b, l=label: self.confirm_delete(l))
            row.add_suffix(button)
            self.face_box.append(row)

    def populate_scenes(self, response):
        if not response.startswith('OK '):
            self.status.set_label('无法读取系统认证场景：' + response)
            return
        status = dict(item.split(':', 1) for item in response[3:].split(',') if ':' in item)
        self._setting_scenes = True
        try:
            for name, _ in SCENES:
                row = self.scene_rows[name]
                state = status.get(name, '?')
                if state in ('0', '1'):
                    row.set_active(state == '1')
                    row.set_sensitive(name not in self._scene_pending)
                else:
                    row.set_sensitive(False)
                    row.set_subtitle('PAM 配置异常：为防止破坏系统，已禁用此开关')
        finally:
            self._setting_scenes = False

    def scene_toggled(self, row, _pspec, name):
        if self._setting_scenes or name in self._scene_pending or self.test_pattern:
            return
        enable = row.get_active()
        # Never imply an operation succeeded before the root daemon confirms.
        self._scene_pending.add(name)
        row.set_sensitive(False)
        if enable:
            dialog = Adw.AlertDialog()
            dialog.set_heading('启用系统人脸认证？')
            dialog.set_body(
                '即将修改对应服务的 PAM 认证配置。人脸识别失败应回退到系统原有密码/指纹。'
                '请确保已经在独立测试栈完成验证，并保留一个管理员终端。'
                '图形登录及 polkit 场景建议最后启用。'
                if os.getenv('FACEAUTH_DEV_MODE') != '1'
                else '当前为开发隔离模式：仅修改测试 PAM 文件，不影响真实系统登录。')
            dialog.add_response('cancel', '取消')
            dialog.add_response('apply', '确认启用')
            dialog.set_default_response('cancel')
            dialog.set_close_response('cancel')
            dialog.choose(self.win, None,
                lambda d, res: self.scene_confirmed(name, enable, d.choose_finish(res)))
        else:
            self.apply_scene(name, False)

    def scene_confirmed(self, name, enable, choice):
        if choice != 'apply':
            self._scene_pending.discard(name)
            self.refresh_scene_state()
            return
        self.apply_scene(name, enable)

    def apply_scene(self, name, enable):
        self.status.set_label(('正在启用 ' if enable else '正在禁用 ') + name + '，可能弹出管理员认证窗口…')
        self.settings_feedback.set_label(('正在启用 ' if enable else '正在禁用 ') + name + '，等待管理员授权和服务确认…')
        verb = 'SCENE_ON' if enable else 'SCENE_OFF'
        self.async_request(f'{verb} {self.user} {name}',
            lambda result: self.scene_finished(name, result), timeout=70)

    def scene_finished(self, name, response):
        self._scene_pending.discard(name)
        self.status.set_label(('场景设置已确认：' if response.startswith('OK ') else '场景修改失败，未确认生效：') + response)
        self.settings_feedback.set_label(('场景修改完成：' if response.startswith('OK ') else '场景修改未确认成功：') + response)
        self.refresh_scene_state()

    def refresh_scene_state(self):
        if not self.test_pattern:
            self.async_request(f'SCENE_LIST {self.user}', self.populate_scenes)

    def on_delete_all(self, _):
        self.confirm_delete(None)

    def confirm_delete(self, label):
        dialog = Adw.AlertDialog()
        dialog.set_heading(f'确认删除「{label}」？' if label else '确认删除全部人脸？')
        dialog.set_body('删除不可撤销。其它系统密码与指纹设置不会受到影响。')
        dialog.add_response('cancel', '取消')
        dialog.add_response('delete', '删除')
        dialog.set_response_appearance('delete', Adw.ResponseAppearance.DESTRUCTIVE)
        dialog.set_default_response('cancel')
        dialog.set_close_response('cancel')
        dialog.choose(self.win, None, lambda d, r: self.delete_confirmed(d.choose_finish(r), label))

    def delete_confirmed(self, result, label):
        if result != 'delete':
            return
        # 授权由 daemon 弹框判定，GUI 保持普通用户、不提权。
        self.status.set_label('等待管理员授权删除人脸…（请在弹框中输入密码/指纹）')
        command = (f'DELETE_FACE {self.user} {label}' if label else f'DELETE {self.user}')
        self.async_request(command, self.delete_done, timeout=60)

    def delete_done(self, response):
        denied = 'authorization' in (response or '').lower()
        note = ('管理员授权未通过，人脸未被删除。' if denied else response)
        self.status.set_label(note)
        self.settings_feedback.set_label('人脸管理：' + note)
        self.refresh_lists()

    def tick(self):
        if not self.alive:
            return False
        try:
            if self.busy:
                self.progress.pulse()
            if self.test_pattern:
                # local in-memory RGBA test, never write a PNG or acquire a camera
                w, h = 320, 240
                pixels = bytearray(w*h*4)
                k = (self.frame_count*9) % (w-50)
                for y in range(h):
                    for x in range(w):
                        off = (y*w+x)*4
                        pixels[off:off+4] = (60, 180, 100, 255) if k <= x < k+50 and 70<=y<120 else (237, 239, 244, 255)
                self.picture.set_paintable(Gdk.MemoryTexture.new(w,h,Gdk.MemoryFormat.R8G8B8A8,GLib.Bytes.new(bytes(pixels)),w*4))
                self.frame_count += 1
            else:
                latest = self.preview.newest(self.seq) if self.preview.is_active() else None
                if latest:
                    self.seq, jpg = latest
                    texture = Gdk.Texture.new_from_bytes(GLib.Bytes.new(jpg))
                    self.picture.set_paintable(texture)
                    if self.frame_count == 0:
                        self.status.set_label('正在预览摄像头')
                    self.frame_count += 1
                for _ in range(5):
                    try: kind, text = self.preview.events.get_nowait()
                    except queue.Empty: break
                    if kind == 'status' and not self.busy:
                        self.status.set_label(text)
            for _ in range(8):
                try: kind, callback, response = self.events.get_nowait()
                except queue.Empty: break
                if kind == 'callback':
                    callback(response)
        except Exception:
            print('GUI event loop: ' + traceback.format_exc(), file=sys.stderr, flush=True)
        return True

    def on_close(self, _win):
        self.alive = False
        was_active = self.preview.is_active()
        self.preview.stop()
        if was_active and not self.test_pattern:
            try:
                request(f'PREVIEW_STOP {self.user}', timeout=0.45)
            except (OSError, ValueError):
                pass  # daemon expires an abandoned preview within 3 seconds
        return False


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--test-pattern', action='store_true')
    args = parser.parse_args()
    app = FaceAuthApp(test_pattern=args.test_pattern)
    app.register(None)
    if app.get_is_remote():
        print('FaceAuth already running in this GNOME session: close it first.', file=sys.stderr)
        return 2
    return app.run([])

if __name__ == '__main__':
    sys.exit(main())

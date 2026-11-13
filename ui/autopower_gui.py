#!/usr/bin/env python3
"""autopower 桌面控制台（GTK3 原生窗口，紧凑版）。

依赖系统自带的 python3-gi + GTK3，不额外安装任何软件包。
读操作以当前用户身份完成；写操作通过 `sudo -n autopower` 执行，
若免密规则不存在则回退 `pkexec`。
所有确认/提示都在主窗口内联显示（本机 WM 渲染独立对话框窗口会黑屏）。

交互约定：
- 关机/开机时间各用一组「− 时间 ＋」，步进半小时，改动自动保存（600ms 防抖），没有保存按钮；
- 「启用」开关切换同样自动保存；
- 保存后读回 /etc/auto-power-cycle.conf 校验；
- 各控件用鼠标悬停 tooltip 说明作用。
"""
from __future__ import annotations

import json
import os
import subprocess
import time
from pathlib import Path

import gi

gi.require_version("Gtk", "3.0")
gi.require_version("Gdk", "3.0")
from gi.repository import Gdk, GLib, Gtk

CLI = "/usr/local/bin/autopower"
CONF_FILE = Path("/etc/auto-power-cycle.conf")
STATE_DIR = Path("/var/lib/auto-power-cycle")
TEST_RESULT = STATE_DIR / "last-test-result"
TEST_PENDING = STATE_DIR / "test-pending"
TIMER = "auto-power-cycle.timer"
APP_ID = "org.zhaojian.autopowerui"



def run(cmd: list[str], timeout: int = 60) -> tuple[int, str]:
    env = dict(os.environ)
    env["LC_ALL"] = "C.UTF-8"
    env["LANG"] = "C.UTF-8"
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, env=env)
        return proc.returncode, ((proc.stdout or "") + (proc.stderr or "")).strip()
    except subprocess.TimeoutExpired:
        return 124, "命令执行超时（{} 秒）".format(timeout)
    except OSError as exc:
        return 127, "无法执行 {}：{}".format(cmd[0], exc)


def run_privileged(args: list[str], timeout: int = 60) -> tuple[int, str]:
    if os.geteuid() == 0:
        return run([CLI, *args], timeout)
    rc, out = run(["sudo", "-n", CLI, *args], timeout)
    if rc != 0 and ("password is required" in out or "a terminal is required" in out):
        rc, out = run(["pkexec", CLI, *args], timeout)
    return rc, out


def read_config() -> dict:
    data = {"shutdown": "", "wake": "", "enabled": None}
    try:
        for line in CONF_FILE.read_text(encoding="utf-8").splitlines():
            if line.startswith("SHUTDOWN_TIME="):
                data["shutdown"] = line.split("=", 1)[1].strip()
            elif line.startswith("WAKE_TIME="):
                data["wake"] = line.split("=", 1)[1].strip()
            elif line.startswith("DAILY_ENABLED="):
                data["enabled"] = line.split("=", 1)[1].strip() == "1"
    except OSError:
        pass
    return data


def read_timer() -> dict:
    info = {"state": "", "active": "", "next_epoch": None}
    rc, out = run(["/usr/bin/systemctl", "list-timers", TIMER, "--output=json", "--no-pager"], 10)
    if rc == 0 and out:
        try:
            rows = json.loads(out)
            if rows and isinstance(rows[0].get("next"), (int, float)):
                info["next_epoch"] = int(rows[0]["next"] / 1_000_000)
        except (ValueError, TypeError, KeyError):
            pass
    rc, out = run(
        ["/usr/bin/systemctl", "show", TIMER, "-p", "UnitFileState", "-p", "ActiveState"], 10
    )
    for line in out.splitlines():
        if line.startswith("UnitFileState="):
            info["state"] = line.split("=", 1)[1].strip()
        elif line.startswith("ActiveState="):
            info["active"] = line.split("=", 1)[1].strip()
    return info


def read_rtc() -> dict:
    info = {"alrm_time": "", "alrm_date": "", "alrm_pending": ""}
    try:
        text = Path("/proc/driver/rtc").read_text(encoding="ascii", errors="replace")
    except OSError:
        return info
    for line in text.splitlines():
        if ":" in line:
            key, value = (p.strip() for p in line.split(":", 1))
            if key in ("alrm_time", "alrm_date", "alrm_pending"):
                info[key.lower()] = value
    return info


def read_last_test() -> dict:
    data: dict[str, str] = {}
    try:
        for line in TEST_RESULT.read_text(encoding="utf-8").splitlines():
            if "=" in line:
                key, value = line.split("=", 1)
                data[key.strip()] = value.strip()
    except OSError:
        pass
    return data


def human_left(epoch: int | None) -> str:
    if not epoch:
        return "—"
    left = epoch - int(time.time())
    if left <= 0:
        return "已到点"
    h, rem = divmod(left, 3600)
    m, _ = divmod(rem, 60)
    return "{}h{}m 后".format(h, m) if h else "{} 分钟后".format(m)


def kv_row(label_text: str) -> tuple[Gtk.Box, Gtk.Label]:
    row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
    key = Gtk.Label(label=label_text, xalign=0)
    key.set_size_request(64, -1)
    key.get_style_context().add_class("dim-label")
    key.set_can_focus(False)
    row.pack_start(key, False, False, 0)
    value = Gtk.Label(label="—", xalign=0)
    value.set_ellipsize(3)  # Pango.EllipsizeMode.END
    value.set_can_focus(False)
    row.pack_start(value, True, True, 0)
    return row, value


STEP_MINUTES = 30          # 每次 −／＋ 的步进：半小时
DAY_MINUTES = 24 * 60


def fmt_minutes(total: int) -> str:
    total %= DAY_MINUTES
    return "{:02d}:{:02d}".format(total // 60, total % 60)


def parse_minutes(value: str | None, fallback: int = 0) -> int:
    if not value:
        return fallback
    parts = value.split(":")
    try:
        return (int(parts[0]) * 60 + int(parts[1])) % DAY_MINUTES
    except (IndexError, ValueError):
        return fallback


class AutopowerWindow(Gtk.ApplicationWindow):
    def __init__(self, app: Gtk.Application):
        super().__init__(application=app, title="自动开关机")
        self.set_default_size(560, 340)
        self._install_css()
        self.set_position(Gtk.WindowPosition.CENTER)
        self.set_icon_name("system-shutdown-symbolic")
        self.next_epoch: int | None = None
        self._pending = None
        self._loading = True
        self._save_source: int | None = None
        self._saved_config = {"shutdown": "", "wake": "", "enabled": None}

        root = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        root.set_border_width(12)
        self.add(root)

        # 顶部：标题 + 启停状态 + 下次触发
        header = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        title = Gtk.Label(xalign=0)
        title.set_markup("<b>自动开关机</b>")
        title.set_can_focus(False)
        header.pack_start(title, False, False, 0)
        self.state_label = Gtk.Label(xalign=0)
        self.state_label.set_can_focus(False)
        header.pack_start(self.state_label, False, False, 0)
        self.hint_label = Gtk.Label(label="ⓘ")
        self.hint_label.set_tooltip_text(
            "悬停在各按钮上有说明：\n"
            "· 预览：只推算唤醒时间，不写 RTC、不关机\n"
            "· 立即关机：写 RTC 并立刻关机，按「开机」时间自动启动\n"
            "· 唤醒测试：立刻关机，在延迟秒数后自动开机（测试用）\n"
            "· 清闹钟：清除未触发的 RTC 闹钟\n"
            "· 刷新：重新读取配置 / 定时器 / RTC\n"
            "· 「−／＋」每次调半小时，和「启用」开关一样改动后自动保存"
        )
        self.hint_label.set_can_focus(False)
        header.pack_start(self.hint_label, False, False, 0)
        self.next_label = Gtk.Label(xalign=1)
        self.next_label.get_style_context().add_class("dim-label")
        self.next_label.set_can_focus(False)
        header.pack_end(self.next_label, False, False, 0)
        root.pack_start(header, False, False, 0)

        # 计划行：关机 / 开机 各一组「− 时间 ＋」，步进半小时，改动自动保存
        plan = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        self.shutdown_min = 0
        self.wake_min = 6 * 60
        self.shutdown_label, self.shutdown_minus, self.shutdown_plus = self._time_controls(
            plan, "关机", "shutdown",
            "关机时间：点 −／＋ 每次调半小时，改动后自动保存。",
        )
        plan.pack_start(Gtk.Label(label=""), False, False, 6)
        self.wake_label, self.wake_minus, self.wake_plus = self._time_controls(
            plan, "开机", "wake",
            "自动开机时间：点 −／＋ 每次调半小时，改动后自动保存。",
        )
        self.switch_enabled = Gtk.Switch()
        self.switch_enabled.set_valign(Gtk.Align.CENTER)
        self.switch_enabled.set_tooltip_text("启用 / 停用每日计划。\n打开＝每天按设定的关机/开机时间执行；关闭＝不再计划关机，切换后自动保存。")
        plan.pack_start(self.switch_enabled, False, False, 6)

        auto_hint = Gtk.Label(label="改动即时保存")
        auto_hint.get_style_context().add_class("dim-label")
        auto_hint.set_can_focus(False)
        plan.pack_end(auto_hint, False, False, 0)
        root.pack_start(plan, False, False, 0)

        # 状态区
        info = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=3)
        timer_row, self.timer_value = kv_row("定时器")
        info.pack_start(timer_row, False, False, 0)
        rtc_row, self.rtc_value = kv_row("RTC")
        info.pack_start(rtc_row, False, False, 0)
        test_row, self.test_value = kv_row("上次自检")
        info.pack_start(test_row, False, False, 0)
        root.pack_start(info, False, False, 0)

        root.pack_start(Gtk.Separator(orientation=Gtk.Orientation.HORIZONTAL), False, False, 2)

        # 操作行：主操作正常大小，次要操作紧凑
        actions = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=4)
        self.btn_now = Gtk.Button(label="立即关机")
        self.btn_now.set_tooltip_text("立即关机：写入 RTC 闹钟后马上关机，\n并在「开机」时间自动开机。会弹出内联确认，VNC/SSH 将断开。")
        self.btn_now.connect("clicked", self.on_shutdown_now)
        actions.pack_start(self.btn_now, False, False, 0)

        self.btn_preview = self._mini_button(
            actions, "预览", "预览：只推算“立即关机”后会几点自动开机，\n不写 RTC、不关机，可放心点。", self.on_preview
        )
        self.btn_test = self._mini_button(
            actions, "唤醒测试", "唤醒测试：立刻关机，在「延迟秒数」后自动开机，\n用来验证这台机器能否被 RTC 唤醒（测试后配置会自动恢复）。", self.on_test
        )
        self.btn_cancel = self._mini_button(
            actions, "清闹钟", "清闹钟：清除尚未触发的 RTC 闹钟，\n之后关机就不会再被自动唤醒。", self.on_cancel_wake
        )
        self.btn_refresh = self._mini_button(
            actions, "刷新", "刷新：重新读取配置、systemd 定时器、RTC 和上次自检结果。", lambda *_: self.refresh()
        )
        root.pack_start(actions, False, False, 0)

        # 内联确认条（替代弹窗）
        self.confirm_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        self.confirm_label = Gtk.Label(xalign=0)
        self.confirm_label.set_line_wrap(True)
        self.confirm_label.set_max_width_chars(64)
        self.confirm_box.pack_start(self.confirm_label, False, False, 0)

        bar = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        self.delay_label = Gtk.Label(label="延迟秒数")
        self.delay_spin = Gtk.SpinButton.new_with_range(120, 3600, 30)
        self.delay_spin.set_value(300)
        self.delay_spin.set_numeric(True)
        self.delay_spin.set_width_chars(5)
        self.delay_spin.set_tooltip_text("120 - 3600 秒")
        bar.pack_start(self.delay_label, False, False, 0)
        bar.pack_start(self.delay_spin, False, False, 0)

        self.btn_abort = Gtk.Button(label="取消")
        self.btn_abort.connect("clicked", lambda *_: self.close_confirm())
        bar.pack_end(self.btn_abort, False, False, 0)
        self.btn_proceed = Gtk.Button(label="确认")
        self.btn_proceed.connect("clicked", self.on_proceed)
        bar.pack_end(self.btn_proceed, False, False, 0)
        self.confirm_box.pack_start(bar, False, False, 0)
        root.pack_start(self.confirm_box, False, False, 0)

        # 输出区
        scroller = Gtk.ScrolledWindow()
        scroller.set_policy(Gtk.PolicyType.AUTOMATIC, Gtk.PolicyType.AUTOMATIC)
        scroller.set_min_content_height(84)
        scroller.set_size_request(-1, 84)   # 固定日志区高度，避免窗口随日志行数长高
        scroller.set_shadow_type(Gtk.ShadowType.IN)
        self.text_out = Gtk.TextView()
        self.text_out.set_editable(False)
        self.text_out.set_cursor_visible(False)
        self.text_out.set_wrap_mode(Gtk.WrapMode.WORD_CHAR)
        self.text_out.set_left_margin(6)
        self.text_out.set_top_margin(3)
        scroller.add(self.text_out)
        root.pack_start(scroller, True, True, 0)

        self.confirm_box.hide()
        self._sync_time_labels()
        self.switch_enabled.connect("notify::active", self._on_plan_changed)
        self.connect("key-press-event", self._on_key)
        GLib.timeout_add_seconds(1, self._tick)
        GLib.timeout_add_seconds(20, self._auto_refresh)
        self.refresh()
        self._loading = False

    def _install_css(self) -> None:
        """只给次要按钮降级内边距，其余保持系统原生样式。"""
        provider = Gtk.CssProvider()
        provider.load_from_data(
            b"button.mini-action { padding: 1px 8px; min-height: 0; font-size: 85%; }"
        )
        Gtk.StyleContext.add_provider_for_screen(
            Gdk.Screen.get_default(),
            provider,
            Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION,
        )

    def _mini_button(self, container: Gtk.Box, label: str, tip: str, handler) -> Gtk.Button:
        button = Gtk.Button(label=label)
        button.set_tooltip_text(tip)
        button.set_relief(Gtk.ReliefStyle.NORMAL)
        button.get_style_context().add_class("mini-action")
        button.connect("clicked", handler)
        container.pack_start(button, False, False, 0)
        return button

    def _time_controls(self, container: Gtk.Box, title: str, key: str, tip: str):
        """一组「− HH:MM ＋」：只能按半小时步进，没有任何键入。"""
        label_title = Gtk.Label(label=title)
        label_title.set_can_focus(False)
        container.pack_start(label_title, False, False, 0)

        minus = Gtk.Button(label="−")
        minus.get_style_context().add_class("mini-action")
        minus.set_tooltip_text(tip + "\n每次 −30 分钟，00:00 再减会绕到 23:30。")
        minus.connect("clicked", lambda *_: self._shift(key, -STEP_MINUTES))
        container.pack_start(minus, False, False, 0)

        value = Gtk.Label()
        value.set_width_chars(6)
        value.set_can_focus(False)
        container.pack_start(value, False, False, 0)

        plus = Gtk.Button(label="+")
        plus.get_style_context().add_class("mini-action")
        plus.set_tooltip_text(tip + "\n每次 +30 分钟，23:30 再加会绕到 00:00。")
        plus.connect("clicked", lambda *_: self._shift(key, STEP_MINUTES))
        container.pack_start(plus, False, False, 0)
        return value, minus, plus

    def _shift(self, key: str, delta: int) -> None:
        attr = "shutdown_min" if key == "shutdown" else "wake_min"
        setattr(self, attr, (getattr(self, attr) + delta) % DAY_MINUTES)
        self._sync_time_labels()
        self._on_plan_changed()

    def _sync_time_labels(self) -> None:
        self.shutdown_label.set_markup("<b>{}</b>".format(fmt_minutes(self.shutdown_min)))
        self.wake_label.set_markup("<b>{}</b>".format(fmt_minutes(self.wake_min)))

    # ---------- 基础 ----------
    def _on_key(self, _widget, event) -> bool:
        if event.keyval == 65307 and self.confirm_box.get_visible():  # Escape
            self.close_confirm()
            return True
        return False

    def log(self, text: str) -> None:
        buf = self.text_out.get_buffer()
        buf.insert_at_cursor("[{}] {}\n".format(time.strftime("%H:%M:%S"), text))
        buf.move_mark(buf.get_insert(), buf.get_end_iter())
        self.text_out.scroll_to_mark(buf.get_insert(), 0.0, False, 0.0, 0.0)

    def _tick(self) -> bool:
        if self.next_epoch:
            self.next_label.set_text(
                "下次 {} · {}".format(
                    time.strftime("%m-%d %H:%M", time.localtime(self.next_epoch)),
                    human_left(self.next_epoch),
                )
            )
        return True

    def _auto_refresh(self) -> bool:
        self.refresh(silent=True)
        return True

    # ---------- 内联确认 ----------
    def ask(self, message: str, action, ok_label: str = "确认", with_delay: bool = False) -> None:
        self._pending = action
        self.confirm_label.set_text(message)
        self.btn_proceed.set_label(ok_label)
        self.confirm_box.show_all()
        self.delay_label.set_visible(with_delay)
        self.delay_spin.set_visible(with_delay)
        self.btn_abort.set_visible(True)
        self.btn_proceed.grab_focus()

    def notify(self, message: str) -> None:
        self.ask(message, None, ok_label="知道了")
        self.btn_abort.set_visible(False)
        self.delay_label.set_visible(False)
        self.delay_spin.set_visible(False)
        self.btn_proceed.grab_focus()

    def close_confirm(self) -> None:
        self._pending = None
        self.confirm_box.hide()
        self.btn_abort.show()
        self.btn_now.grab_focus()

    def on_proceed(self, _button: Gtk.Button) -> None:
        action = self._pending
        self.close_confirm()
        if action:
            action()

    # ---------- 自动保存 ----------
    def _on_plan_changed(self, *_args) -> None:
        if self._loading:
            return
        if self._save_source:
            GLib.source_remove(self._save_source)
        self._save_source = GLib.timeout_add(600, self._flush_save)

    def _flush_save(self) -> bool:
        self._save_source = None
        shutdown = fmt_minutes(self.shutdown_min)
        wake = fmt_minutes(self.wake_min)
        enabled = self.switch_enabled.get_active()
        saved = self._saved_config
        times_changed = (
            self._strip_seconds(shutdown) != self._strip_seconds(saved.get("shutdown") or "")
            or self._strip_seconds(wake) != self._strip_seconds(saved.get("wake") or "")
        )
        if enabled == saved.get("enabled") and not times_changed:
            return False

        if enabled:
            rc, out = run_privileged(["on", "-s", shutdown, "-w", wake])
        else:
            if times_changed:
                run_privileged(["set", "-s", shutdown, "-w", wake])
            rc, out = run_privileged(["off"])
        self.log(out or "（无输出）")

        config = read_config()
        ok = (
            config.get("enabled") == enabled
            and self._strip_seconds(config.get("shutdown")) == self._strip_seconds(shutdown)
            and self._strip_seconds(config.get("wake")) == self._strip_seconds(wake)
        )
        if rc != 0 or not ok:
            self.log("自动保存失败：{}".format(out or "配置未按预期更新"))
            self.notify("自动保存失败，请检查权限或时间值。")
        else:
            self.log(
                "已自动保存：关机 {} 开机 {} {} · 读回校验通过".format(
                    shutdown, wake, "已启用" if enabled else "已停用"
                )
            )
        self.refresh(silent=True)
        return False

    @staticmethod
    def _strip_seconds(value: str | None) -> str:
        if not value:
            return ""
        parts = value.split(":")
        return ":".join(parts[:2])

    # ---------- 状态 ----------
    def refresh(self, silent: bool = False) -> None:
        config = read_config()
        timer = read_timer()
        rtc = read_rtc()
        last_test = read_last_test()
        self.next_epoch = timer.get("next_epoch")

        enabled = config.get("enabled")
        if enabled is None:
            self.state_label.set_markup("<span foreground='#b45309'>状态未知</span>")
        elif enabled:
            self.state_label.set_markup("<span foreground='#15803d'>已启用</span>")
        else:
            self.state_label.set_markup("<span foreground='#b45309'>已停用</span>")

        self._loading = True
        if self._save_source is None and self._pending is None:
            self.shutdown_min = parse_minutes(config.get("shutdown"), 0)
            self.wake_min = parse_minutes(config.get("wake"), 6 * 60)
            self._sync_time_labels()
            self.switch_enabled.set_active(bool(enabled))
        self._loading = False
        self._saved_config = config

        active = "运行中" if timer.get("active") == "active" else (timer.get("active") or "未知")
        state = "已启用" if timer.get("state") == "enabled" else (timer.get("state") or "未知")
        next_text = (
            time.strftime("%Y-%m-%d %H:%M", time.localtime(self.next_epoch))
            if self.next_epoch
            else "无"
        )
        self.timer_value.set_text("{} · {} · 下次 {}".format(active, state, next_text))
        self.timer_value.set_tooltip_text("systemd 定时器 " + TIMER)

        self.rtc_value.set_text(
            "{} {}　pending={}".format(
                rtc.get("alrm_date") or "—",
                rtc.get("alrm_time") or "—",
                rtc.get("alrm_pending") or "—",
            )
        )

        if last_test:
            when = last_test.get("BOOT_OBSERVED", "")
            result = last_test.get("RESULT", "")
            summary = "{} {}".format(when, result).strip() or "无记录"
        else:
            summary = "无记录"
        if TEST_PENDING.exists():
            summary = "有待验证的测试 · " + summary
        self.test_value.set_text(summary)
        self.test_value.set_tooltip_text(
            "\n".join("{}={}".format(k, v) for k, v in last_test.items()) or "无测试记录"
        )
        self._tick()
        if not silent:
            self.log("状态已刷新")

    # ---------- 动作 ----------
    def on_preview(self, _button: Gtk.Button) -> None:
        _, out = run([CLI, "now", "--dry-run"])
        self.log(out or "（无输出）")

    def on_shutdown_now(self, _button: Gtk.Button) -> None:
        rc, out = run([CLI, "now", "--dry-run"])
        wake_line = ""
        for line in out.splitlines():
            if line.startswith("WAKE_AT="):
                wake_line = line.split("=", 1)[1].strip()
        self.ask(
            "确认立即关机？将在 {} 自动开机，VNC/SSH 会断开。".format(wake_line or "下一个开机时间"),
            self._do_shutdown,
            ok_label="关机",
        )

    def _do_shutdown(self) -> None:
        self.log("正在执行关机…")
        rc, out = run_privileged(["now"], timeout=90)
        self.log(out or "（无输出）")
        if rc != 0:
            self.notify("关机失败：{}".format(out or "返回码 {}".format(rc)))

    def on_test(self, _button: Gtk.Button) -> None:
        self.ask(
            "确认开始 RTC 唤醒测试？机器会立刻关机，并在设定秒数后自动开机。",
            self._do_test,
            ok_label="开始测试",
            with_delay=True,
        )

    def _do_test(self) -> None:
        try:
            delay = int(self.delay_spin.get_text().strip())
        except ValueError:
            delay = int(self.delay_spin.get_value())
        delay = max(120, min(3600, delay))
        self.log("启动 RTC 测试（{} 秒后开机）…".format(delay))
        rc, out = run_privileged(["test", str(delay)], timeout=120)
        self.log(out or "（无输出）")
        if rc != 0:
            self.notify("测试启动失败：{}".format(out or "返回码 {}".format(rc)))
        self.refresh(silent=True)

    def on_cancel_wake(self, _button: Gtk.Button) -> None:
        self.ask("确认清除 RTC 闹钟？清除后关机不会自动开机。", self._do_cancel_wake, ok_label="清除")

    def _do_cancel_wake(self) -> None:
        rc, out = run_privileged(["cancel-wake"])
        self.log(out or "（无输出）")
        if rc != 0:
            self.notify("清除失败：{}".format(out or "返回码 {}".format(rc)))
        self.refresh(silent=True)


class AutopowerApp(Gtk.Application):
    def __init__(self) -> None:
        super().__init__(application_id=APP_ID)
        self.win: AutopowerWindow | None = None

    def do_activate(self) -> None:
        if not self.win:
            self.win = AutopowerWindow(self)
            self.win.connect("destroy", lambda *_: setattr(self, "win", None))
        self.win.show_all()
        self.win.confirm_box.hide()
        self.win.present()


def main() -> int:
    return AutopowerApp().run(None)


if __name__ == "__main__":
    raise SystemExit(main())

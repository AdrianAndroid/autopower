#!/usr/bin/env bash
# 安装 autopower 桌面 GUI（GTK3 原生窗口）。
# 幂等：可反复执行。需要 sudo 执行一次（写 sudoers 免密规则与系统级入口）。
set -Eeuo pipefail

PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
GUI_SRC="$PROJECT_DIR/ui/autopower_gui.py"
GUI_TARGET="$PROJECT_DIR/ui/autopower_gui.py"
SUDOERS_FILE=/etc/sudoers.d/autopower-ui

if (( EUID != 0 )); then
    printf '请用 sudo 执行：sudo %s\n' "$0" >&2
    exit 1
fi
REAL_USER="${SUDO_USER:-zhaojian}"
REAL_HOME="$(getent passwd "$REAL_USER" | cut -d: -f6)"
[[ -n "$REAL_HOME" && -d "$REAL_HOME" ]] || { printf '无法确定用户 %s 的家目录\n' "$REAL_USER" >&2; exit 1; }
LAUNCHER="$REAL_HOME/.local/bin/autopower-gui"
APP_ENTRY_DIR="$REAL_HOME/.local/share/applications"
DESKTOP_DIR="$REAL_HOME/Desktop"

[[ -f "$GUI_SRC" ]] || { printf '找不到 %s\n' "$GUI_SRC" >&2; exit 1; }
/usr/bin/python3 -c "import gi; gi.require_version('Gtk','3.0'); from gi.repository import Gtk" \
    || { printf 'GTK3 不可用，无法安装。\n' >&2; exit 1; }

chmod 0755 "$GUI_TARGET"

# 1) 用户目录启动器
install -d -m 0755 -o "$REAL_USER" -g "$REAL_USER" "$REAL_HOME/.local/bin" "$REAL_HOME/.local/share/applications"
printf '#!/usr/bin/env bash\nexec /usr/bin/python3 %q "$@"\n' "$GUI_TARGET" > "$LAUNCHER"
chmod 0755 "$LAUNCHER"
chown "$REAL_USER:$REAL_USER" "$LAUNCHER"

# 2) 应用菜单 + 桌面图标
write_entry() {
    local path="$1"
    cat > "$path" <<EOF
[Desktop Entry]
Type=Application
Name=自动开关机
Name[en]=Autopower
Comment=查看与修改每日自动关机 / RTC 自动开机计划
Exec=$LAUNCHER
Icon=system-shutdown-symbolic
Terminal=false
Categories=System;Settings;
Keywords=power;shutdown;rtc;wake;autopower;
StartupWMClass=autopower-gui
EOF
    chmod 0755 "$path"
    chown "$REAL_USER:$REAL_USER" "$path"
}

write_entry "$APP_ENTRY_DIR/autopower-gui.desktop"
install -d -m 0755 -o "$REAL_USER" -g "$REAL_USER" "$REAL_HOME/Desktop"
write_entry "$REAL_HOME/Desktop/autopower-gui.desktop"

# 允许 GNOME 把桌面上的 .desktop 当成可信启动器
sudo -u "$REAL_USER" gio set "$REAL_HOME/Desktop/autopower-gui.desktop" \
    metadata::trusted true 2>/dev/null || true
update-desktop-database "$APP_ENTRY_DIR" 2>/dev/null || true

# 3) 仅对 autopower 一条命令免密，其它 sudo 仍要密码
cat > "$SUDOERS_FILE" <<EOF
# 由 autopower 桌面 GUI 安装脚本生成；删除本文件即可撤销（sudo rm $SUDOERS_FILE）
$REAL_USER ALL=(root) NOPASSWD: /usr/local/bin/autopower
EOF
chmod 0440 "$SUDOERS_FILE"
/usr/sbin/visudo -cf "$SUDOERS_FILE" >/dev/null

printf '安装完成：\n  启动器 %s\n  菜单项 %s\n  桌面图标 %s\n  免密规则 %s\n' \
    "$LAUNCHER" "$APP_ENTRY_DIR/autopower-gui.desktop" "$REAL_HOME/Desktop/autopower-gui.desktop" "$SUDOERS_FILE"

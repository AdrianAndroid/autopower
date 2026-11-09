#!/usr/bin/env bash
# Install the single autopower CLI/backend and its systemd units.
set -Eeuo pipefail

readonly MODE="${1:-install}"
readonly SOURCE_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
readonly CLI=/usr/local/bin/autopower
readonly LEGACY_CLI=/usr/local/sbin/auto-power-cycle
readonly CONFIG_FILE=/etc/auto-power-cycle.conf
readonly INSTALL_LOG=/var/log/auto-power-cycle-install.log

if (( EUID != 0 )); then
    printf '请使用 sudo 运行安装脚本。\n' >&2
    exit 1
fi

exec > >(/usr/bin/tee -a "$INSTALL_LOG") 2>&1
printf '\n=== %s; mode=%s; CLI-first upgrade ===\n' "$(/usr/bin/date '+%F %T %Z')" "$MODE"

for command_path in \
    /usr/sbin/rtcwake /usr/bin/systemctl /usr/bin/systemd-run \
    /usr/bin/systemd-analyze /usr/bin/flock; do
    [[ -x "$command_path" ]] || { printf '缺少命令：%s\n' "$command_path" >&2; exit 1; }
done
for source_file in \
    autopower auto-power-cycle.service auto-power-cycle.timer \
    auto-power-cycle-test-verify.service; do
    [[ -f "$SOURCE_DIR/$source_file" ]] || { printf '缺少源文件：%s\n' "$source_file" >&2; exit 1; }
done

[[ -e /sys/class/rtc/rtc0/wakealarm ]] || { printf '本机没有可用的 RTC 闹钟。\n' >&2; exit 1; }
[[ "$(/usr/bin/timedatectl show --property=LocalRTC --value)" == no ]] || {
    printf '硬件时钟必须使用 UTC。\n' >&2
    exit 1
}
[[ ! -e /var/lib/auto-power-cycle/test-pending ]] || {
    printf '存在等待开机验证的测试，暂停安装以免破坏验证状态。\n' >&2
    exit 1
}

/bin/bash -n "$SOURCE_DIR/autopower"
"$SOURCE_DIR/autopower" help >/dev/null
"$SOURCE_DIR/autopower" now --dry-run >/dev/null

/usr/bin/install -d -m 0755 /usr/local/bin /usr/local/sbin /var/lib/auto-power-cycle
/usr/bin/install -m 0755 "$SOURCE_DIR/autopower" "$CLI"
# Keep old internal/documentation invocations working; there is one implementation.
/usr/bin/ln -sfn "$CLI" "$LEGACY_CLI"
/usr/bin/install -m 0644 "$SOURCE_DIR/auto-power-cycle.service" /etc/systemd/system/auto-power-cycle.service
/usr/bin/install -m 0644 "$SOURCE_DIR/auto-power-cycle.timer" /etc/systemd/system/auto-power-cycle.timer
/usr/bin/install -m 0644 "$SOURCE_DIR/auto-power-cycle-test-verify.service" /etc/systemd/system/auto-power-cycle-test-verify.service

if [[ ! -e "$CONFIG_FILE" ]]; then
    enabled=0
    if /usr/bin/systemctl is-enabled --quiet auto-power-cycle.timer; then enabled=1; fi
    printf 'SHUTDOWN_TIME=00:00:00\nWAKE_TIME=06:00:00\nDAILY_ENABLED=%s\n' "$enabled" > "$CONFIG_FILE"
    /usr/bin/chown root:root "$CONFIG_FILE"
    /usr/bin/chmod 0644 "$CONFIG_FILE"
    printf '已从旧版迁移默认时间 00:00/06:00，原定时器启用状态=%s。\n' "$enabled"
else
    printf '保留已有配置：%s\n' "$CONFIG_FILE"
fi

/usr/bin/systemd-analyze verify \
    /etc/systemd/system/auto-power-cycle.service \
    /etc/systemd/system/auto-power-cycle.timer \
    /etc/systemd/system/auto-power-cycle-test-verify.service
/usr/bin/systemctl daemon-reload
/usr/bin/systemctl disable auto-power-cycle-test-verify.service 2>/dev/null || true

shutdown_time=
wake_time=
daily_enabled=
config_output=$("$CLI" get)
while IFS='=' read -r key value; do
    case "$key" in
        SHUTDOWN_TIME) shutdown_time="$value" ;;
        WAKE_TIME) wake_time="$value" ;;
        DAILY_ENABLED) daily_enabled="$value" ;;
    esac
done <<< "$config_output"
[[ -n "$shutdown_time" && -n "$wake_time" && -n "$daily_enabled" ]] || {
    printf '无法完整读取配置。\n' >&2
    exit 1
}

# This also creates the schedule drop-in and applies the preserved enabled state.
"$CLI" set --shutdown "$shutdown_time" --wake "$wake_time" --enabled "$daily_enabled"

case "$MODE" in
    install)
        printf 'autopower CLI 安装完成。使用 autopower help 查看帮助。\n'
        ;;
    --test|test)
        /usr/bin/systemd-run \
            --unit=auto-power-cycle-test-launcher \
            --on-active=20s \
            --timer-property=AccuracySec=1s \
            --description='Launch authorized RTC poweroff test' \
            "$CLI" test 300
        printf '测试已安排：约 20 秒后真正关机，约 5 分钟后自动开机。\n'
        /usr/bin/systemctl list-timers auto-power-cycle-test-launcher.timer --all --no-pager
        ;;
    *)
        printf '用法：%s [install|--test]\n' "$0" >&2
        exit 2
        ;;
esac

"$CLI" now --dry-run

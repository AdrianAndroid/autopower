# 自动开关机配置操作记录

## 2026-11-08：调研与方案确认

- 23:40 左右，读取 DMI、RTC、ACPI、网卡与系统时钟信息，未修改系统。
- 确认主板为 ASUS B85-PLUS R2.0，BIOS 0504，RTC 驱动为 `rtc_cmos`。
- 系统时区为 Asia/Shanghai，RTC 使用 UTC，NTP 同步正常。
- 官方华硕手册确认支持 `Power On By RTC` 和 `Power On By PCIE/PCI`。
- 网卡为 Realtek RTL8111/8168，WOL 当时未配置；本任务选择 RTC，不配置 WOL。
- 用户选择每天 `00:00` 关机、`06:00` 开机，并授权进行“立即关机、约 5 分钟后自动开机”的实测。

## 2026-11-08：脚本准备

- 创建目录 `/home/zhaojian/ubuntu/power-schedule/`。
- 创建 `setup-auto-power-cycle.sh`。
- 安装脚本设计为生成一个运行脚本、一个每日服务、一个每日 timer 和一个测试验证服务。
- 正式任务采用 `Persistent=false`，避免错过关机时间后在开机时补执行关机。
- 测试期间暂停正式 timer，避免与午夜任务冲突；测试后的首次开机由验证服务恢复正式 timer。
- 运行事件写入 journald，测试结果写入 `/var/lib/auto-power-cycle/last-test-result`。
- 安装输出追加到 `/var/log/auto-power-cycle-install.log`。
- 安装脚本及内嵌运行脚本均通过 `bash -n` 语法检查。
- `systemd-analyze calendar '*-*-* 00:00:00'` 验证每日午夜表达式有效。
- ShellCheck 未安装，因此未执行 ShellCheck；这不影响 Bash 语法检查结果。

## 2026-11-08 至 2026-11-09：管理员认证问题

- 检查 `sudo -n true`，结果为需要密码，当时无法非交互提权。
- 用户通过会话命令执行 `sudo -v`，但该执行方式没有终端，sudo 报告无法读取密码。
- 随后尝试通过 sudo 标准输入完成一次认证，认证未通过；安装脚本没有开始执行。
- 密码不写入脚本、项目文档或日志，本记录仅记录“认证失败”这一事实。
- 已确认失败时以下文件均不存在，说明没有进行部分安装：
  - `/usr/local/sbin/auto-power-cycle`
  - `/etc/systemd/system/auto-power-cycle.timer`
- 改为使用桌面图形授权方式，避免继续在会话中传递密码。
- 桌面为 XFCE，实际 Xorg 显示为 `:0`，Xauthority 路径为 `/run/user/1000/gdm/Xauthority`。
- 发现图形 askpass 帮助程序 `/usr/libexec/gcr-ssh-askpass`。
- 首次发起图形授权安装时，工具安全分类服务暂时超时，命令未获执行，因此该次没有弹出授权框，也没有修改系统。

## 2026-11-09：安装、实测与验收

- 00:12:10，管理员认证成功，安装脚本以 `--test` 模式完成安装。
- `/usr/local/sbin/auto-power-cycle` 通过 `bash -n`；三个新增 systemd 单元通过 `systemd-analyze verify`。
- 验证时 systemd 同时报告 snapd、1panel 自带单元存在当前 systemd 版本不认识的键；这些警告来自已有服务，不属于本次新增单元，不影响本次验证成功。
- 00:12:31，测试 launcher 按计划触发；正式每日 timer 在测试期间保持 disabled，未与午夜任务冲突。
- 00:12:32，运行脚本写入 RTC 闹钟，预计 00:17:32 左右唤醒。
- journald 记录 `rtcwake: wakeup from "off" using /dev/rtc0`，随后系统执行正常关机。
- 00:12:45，journald 记录 `Reached target System Power Off`，文件系统同步并正常停止。
- `last -x` 记录本次关机时间段为 00:12 至 00:17。
- 00:17:53，系统重新启动，证明 RTC 从真正关机状态自动开机成功。
- 00:17:57，测试验证服务运行成功，自动生成 `/var/lib/auto-power-cycle/last-test-result`：
  - `REQUESTED_AT=2026-11-09 00:12:32 CST`
  - `EXPECTED_WAKE=2026-11-09 00:17:32 CST`
  - `BOOT_OBSERVED=2026-11-09 00:17:57 CST`
  - `DELTA_SECONDS=25`
- 25 秒差值包含固件自检、操作系统启动及验证服务调度，属于合理的启动耗时。
- `test-pending` 已清除；一次性测试验证服务结果为 success，并已自动 disable。
- 正式 `auto-power-cycle.timer` 已自动恢复为 enabled、active (waiting)。
- 下一次正式触发时间已确认：2026-11-10 00:00:00 CST；服务将设置当天 06:00 的 RTC 开机闹钟并关机。
- 本次 RTC 实测和长期定时器恢复均验收通过。

## 最终状态

- 安装和 5 分钟 RTC 实测已完成。
- 每日 `00:00` 正常关机、`06:00` RTC 自动开机已启用。
- 详细结果见 `/var/lib/auto-power-cycle/last-test-result`。
- 安装记录见 `/var/log/auto-power-cycle-install.log`，运行事件可用 `journalctl -t auto-power-cycle` 查询。

## 验收清单

- [x] 管理员安装成功。
- [x] 三个 systemd 单元通过 `systemd-analyze verify`。
- [x] 测试 launcher 成功启动关机。
- [x] 无人干预下电脑在约 5 分钟后启动。
- [x] `/var/lib/auto-power-cycle/last-test-result` 自动生成。
- [x] 启动时间与预期 RTC 唤醒时间差合理。
- [x] 测试后 `auto-power-cycle.timer` 自动恢复为 enabled/active。
- [x] 下一次正式关机时间显示为北京时间 00:00。
- [x] 本次安装日志和 RTC 运行日志无影响功能的异常。

## 2026-11-09：CLI 优先升级，统一为 autopower

- 用户要求先完成 CLI，GUI/桌面双击版本暂缓；本阶段未创建或安装 GUI。
- 用户明确希望只有一个命令，后续参数完成不同动作，并最终将命令名确认为 `autopower`。
- 将旧单体安装器中的后台拆为独立源文件 `autopower`，systemd 单元也拆为独立源文件，避免重复维护 heredoc。
- 新命令安装到 `/usr/local/bin/autopower`；旧 `/usr/local/sbin/auto-power-cycle` 仅为兼容链接，指向同一实现。
- 两个 systemd 服务已改用 `/usr/local/bin/autopower`。
- 新增 root 所有的 `/etc/auto-power-cycle.conf`，统一保存 `SHUTDOWN_TIME`、`WAKE_TIME`、`DAILY_ENABLED`。
- 新增 `/etc/systemd/system/auto-power-cycle.timer.d/schedule.conf`，保存时间后重载并重启 timer，立即重算下次触发时间。
- 配置修改、关机与测试使用 `flock` 串行化；配置和 drop-in 使用临时文件原子替换。
- 用户接口统一为：
  - `autopower on [-s 时间] [-w 时间]`
  - `autopower off`
  - `autopower set [-s 时间] [-w 时间]`
  - `autopower now [--dry-run]`
  - `autopower status`
  - `autopower help`
- `on` 启用每日计划；`off` 只停用每日计划，不执行关机；`now` 才表示立即正常关机并设置 RTC 唤醒。
- `help`、`--help`、`-h` 均可显示中文帮助，包含时间设置示例、格式规则、真实关机警告和日志路径。
- 时间只接受 24 小时制 `HH:MM` 或 `HH:MM:SS`，小时 00～23，分钟和秒 00～59。
- `now --dry-run` 不需要 sudo，不写 RTC、不改变电源状态；真实 `now` 未在本阶段执行。

### 安装与静态验证

- 00:57:05，以 install 模式完成安装；从旧版迁移 00:00/06:00，保留原 timer enabled 状态。
- `autopower` 和安装脚本通过 `bash -n`。
- 安装后的三个 systemd 单元通过 `systemd-analyze verify`，新 CLI 路径可执行。
- 预安装时曾因 `/usr/local/bin/autopower` 尚未安装而提示单元 executable 不存在；安装后该问题消失。
- systemd 仍输出 snapd、1panel 已有服务的不兼容键警告，与本次新增文件无关。
- `help`、`--help`、`-h` 均验证成功；未知动作返回状态 2 并显示帮助。
- `autopower get` 返回 `00:00:00 / 06:00:00 / enabled=1`。
- `autopower now --dry-run` 正确显示仅预览、预计唤醒时间和未关机说明。
- `autopower status` 显示配置、timer、RTC 与历史测试结果。

### 时间校验与配置回归测试

- 自动关机时间输入 `6:00`、`24:00`、`12:60`、`06:00:99` 均被拒绝。
- 自动开机时间输入 `7:00`、`23:60`、`25:00`、`06:00:61` 均被拒绝。
- 所有无效输入测试前后 `/etc/auto-power-cycle.conf` 的 SHA-256 校验和一致，确认没有部分修改。
- 使用当前时间加 2 小时/3 小时的安全临时计划进行配置回归，实际值为 `02:57:43 / 03:57:43`。
- 配置、timer drop-in 和 `systemctl list-timers` 均显示临时值一致，dry-run 计算为当天 `03:57:43`。
- `autopower off` 后验证 timer 为 disabled/inactive。
- 随后执行 `autopower on -s 00:00 -w 06:00` 恢复原计划，timer 恢复 enabled/active。
- 下一次正式触发确认是 2026-11-10 00:00:00 CST。

### 本阶段边界

- 未执行 `autopower now` 的真实关机形式，也未执行新的 `autopower test`。
- 本阶段没有修改 RTC 闹钟，没有造成关机或 VNC 中断。
- 旧版 RTC 5 分钟实测结果继续保留；新版手动 `now` 的真实端到端测试仍需另行明确授权。
- README 已更新为单命令 CLI 用法，密码未写入脚本或文档。

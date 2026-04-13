# QMT 每日同步 — Windows Task Scheduler 安装指南

**目的**：每日盘后 17:00 自动跑 `scripts/qmt_daily_sync.py`，保证 QMT 本地数据永远最新，且支持缺失回补。

## 前置条件
- QMT 客户端（MiniQMT）已登录（脚本会在未登录时退出码 2，不损坏状态）
- LinDangAgent 位于 `C:\LinDangAgent`

## 设置步骤（Windows 任务计划程序 GUI）

1. Win+R → 输入 `taskschd.msc` 回车
2. 右侧操作面板 → "创建任务"（不是"创建基本任务"）
3. **常规** 标签：
   - 名称: `LinDangAgent QMT Daily Sync`
   - 勾选"不管用户是否登录都要运行"
   - 勾选"使用最高权限运行"
4. **触发器** 标签 → 新建：
   - 开始任务: "按预定计划"
   - 每日 17:00
   - 勾选"已启用"
5. **操作** 标签 → 新建：
   - 操作: "启动程序"
   - 程序/脚本: `C:\Users\lintian\AppData\Local\Programs\Python\Python312\python.exe`
   - 参数: `C:\LinDangAgent\scripts\qmt_daily_sync.py`
   - 起始于: `C:\LinDangAgent`
6. **条件** 标签：
   - 取消勾选"只有在计算机使用交流电源时才启动"
7. **设置** 标签：
   - 勾选"如果过了计划开始时间，立即启动任务"← **关键：这个保证重启/错过后的回补**
   - 勾选"如果任务运行时间超过 2 小时，停止任务"

## 手动测试

```powershell
# 干跑（不下载）
python C:\LinDangAgent\scripts\qmt_daily_sync.py --dry-run

# 正式运行
python C:\LinDangAgent\scripts\qmt_daily_sync.py

# 强制全量（忽略 state）
python C:\LinDangAgent\scripts\qmt_daily_sync.py --force-full
```

## 状态与日志
- 状态: `data/knowledge/qmt_sync_state.json`（含 last_success_date / last 30 runs history）
- 日志: `logs/qmt_sync/YYYYMMDD.log`

## 缺失回补行为
- 脚本每次跑时读 state 里的 `last_success_date`
- 若今天 > last_success_date + 1 天，自动把 window 扩展成 `[last_success_date, today]`，把中间缺的天都补回来
- gap ≥ 7 天则退化为全历史下载

## 退出码
- `0` 成功
- `1` 部分失败（抽查 >3 只没数据，但脚本跑完了）
- `2` 致命（QMT 未连接 / 股票池空）

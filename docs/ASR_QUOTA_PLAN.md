# ASR 额度池 + 可刷新方案（行动计划）

目标：用“本地额度池 + 可刷新”替代固定优先级，优先消耗免费额度，额度不足自动切换；余额更新后自动恢复。

## 设计原则
- 以“调用消耗时长（秒）”为唯一计量单位。
- 不依赖官方余额 API（如后续可用，再接入同步）。
- 支持日/月/总量三类窗口，按 provider+variant 维护。
- 可动态刷新（手动更新额度、重置窗口），支持前端查询。
- variant 约定：file / file_fast / stream_async / stream_realtime（可扩展）。

## 范围与阶段
### P0：数据与统计（只做本地额度池）
1) 数据模型 ✅
- 新表 `asr_quota`：
  - provider
  - variant（录音文件识别/流式等）
  - window_type: day | month | total
  - window_start / window_end
  - quota_seconds
  - used_seconds
  - status: active | exhausted
  - updated_at
- 新表 `asr_quota_events`（可选，首版可省略）
  - provider
  - task_id
  - duration_seconds
  - created_at

2) 统计入口 ✅
- 在 ASR 完成时写入消耗（按 task.duration_seconds 或转写总时长计算）。
- 入口位置：`worker/tasks/process_audio.py` 与 `worker/tasks/process_youtube.py` 的转写成功段。

3) 额度消耗规则 ✅
- 每次调用成功：
  - used_seconds += duration_seconds
  - 若 used_seconds >= quota_seconds -> 标记 exhausted

### P1：选择策略
1) 选择逻辑 ✅
- 若 task.options.asr_provider 指定：强制使用该 provider。
- 未指定时：
  - 只在未耗尽的 provider 中随机/加权选择。
  - 若全部耗尽：回退到现有策略或最小成本策略（默认回退现有策略）。

2) 临时禁用 ⏳
- provider 达到耗尽后设置状态为 exhausted。
- 支持 TTL（可选）：一段时间后再允许尝试（避免误判）。

### P2：可刷新与查询接口
1) 查询接口 ✅
- GET `/api/v1/asr/quotas` 返回各 provider+variant 当前额度、已用、窗口、状态。

2) 手动刷新接口 ✅
- POST `/api/v1/asr/quotas/refresh` 支持：
  - 更新 quota_seconds
  - 重置窗口（用于充值/额度恢复）
  - 重新激活 exhausted

## 数据来源与窗口计算
- day：以服务商时区 00:00 为边界（默认本地时区，后续可配置）。
- month：当月 1 日 00:00 起。
- total：可选传入 window_start/window_end 作为有效期。

## 风险与处理
- duration_seconds 可能为空：
  - 兜底从转写结果总时长或音频时长估算。
- 多 worker 并发写入：
  - 数据库层原子更新（UPDATE used_seconds = used_seconds + ?）。

## 验收标准
- 指定 provider 时必定使用该 provider（默认 variant=file）。
- 未指定时，额度不足的 provider 不再被选中。
- 刷新额度后 provider 可再次被选中。
- 前端可查询额度与使用情况。

## 待补充（后续）
- 计费/余额官方 API 调研结果与接入路径。
- 更细粒度统计（按用户/按任务）。

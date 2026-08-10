# 邮件自动化规则

## 1. 目标

稳定触达，不追求最大量。

---

## 2. Sequence 默认

```text
Day 0  初次邮件
Day 4  Follow-up 1
Day 10 Follow-up 2
```

Campaign 可配置，但必须受系统上限限制。

---

## 3. 发送前检查

每封邮件发送前必须执行：

1. Campaign 是否 running
2. Lead 是否允许发送
3. 联系邮箱是否有效
4. 是否 suppression
5. 是否已经回复
6. 是否已退订
7. 是否 Hard Bounce
8. 是否人工停止
9. 是否重复邮件
10. Mailbox 是否 active
11. 当日 quota
12. 时间窗口
13. 最小间隔
14. idempotency key

---

## 4. 停止 Follow-up

遇到：
- reply
- unsubscribe
- hard bounce
- complaint
- manual stop
- campaign stop
- wechat_added 且 stop_email=true

立即取消未执行任务。

---

## 5. 邮箱状态

- active
- paused
- unhealthy
- disabled

自动标记 unhealthy 条件建议：
- 短周期硬退信异常
- 连续 Provider 失败
- 人工设置

V1 不做复杂 deliverability AI。

---

## 6. 邮箱配置

每个 Mailbox：
- email
- provider
- credentials
- daily_limit
- send_window
- status

凭证必须加密存储。

---

## 7. 发送速率

V1 必须支持：
- 每邮箱每日上限
- Campaign 每日上限
- 最小/最大随机间隔
- 固定发送时间窗口

不允许绕过服务商限制。

---

## 8. 抑制名单

进入 suppression：
- unsubscribe
- hard bounce
- complaint
- manual block
- invalid

任何 Campaign 不能再次发送。

---

## 9. 退订

每封规模化触达邮件应有清晰退订机制或至少内部可识别退订请求。
对方表达“不再联系”等同 unsubscribe。

---

## 10. 回复同步

同步后：
1. 创建 Reply
2. 更新 Email
3. 更新 Campaign Lead
4. 停止后续 Follow-up
5. 触发 AI 分类
6. 创建 Inbox 待办

---

## 11. 幂等

每封发送有唯一 `idempotency_key`：

```text
campaign_lead_id + sequence_step + template_version
```

同 key 不允许重复发送。

---

## 12. 失败重试

SMTP/Provider 临时失败：
- retry 3 次
- 指数退避

明确 hard bounce：
- 不重试
- 加 suppression

---

## 13. 审核

默认：
前 50 封人工审核。

管理员可改：
- all
- first_n
- sample
- auto

---

## 14. 邮件事件

至少记录：
- queued
- sent
- delivered（如 provider 可得）
- bounced
- replied
- cancelled

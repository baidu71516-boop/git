# 达人智能触达系统 PRD V1.0

## 1. 产品定位

公司内部达人开发系统。

核心用途：
- 管理达人资源
- 批量导入灰豚达人
- 基于公司 SOP 自动生成个性化触达邮件
- 自动发送与 Follow-up
- 自动同步回复
- AI 分类回复
- CRM 管理长期达人资源
- 统计赛道、模板、员工、Campaign 表现

### Phase 2 冻结补充（2026-08-11）

当前 Phase 2 已由人工重新定义为“Huitun Bulk Acquisition & Freshness Management”，唯一权威实施契约为 `docs/PHASE_2_SCOPE.md`。旧的 Browser Automation 和“SOP/Campaign/AI 作为 Phase 2”排期已经废弃；后者属于未来待重新冻结范围。

当前 Phase 2 固定为：

- 一个 ImportJob 承载多个灰豚 CSV/XLSX。
- 多文件统一 Parse、Normalize、Hard Dedup、Preview 与人工 Confirm。
- 使用结构化、确定性的 platform/source_tags/followers Screening；不从行业描述、目的或备注做模糊/AI 推断。
- 建立 per-file `source_acquired_at`、Freshness、Refresh Priority 和 Department-owned Refresh Queue。
- Refresh 回流继续经过现有 Preview/Confirm/Merge。
- Email 永远不是 hard merge 依据，只标记疑似重复。

本补充覆盖本文中与当前 Phase 2 相冲突的旧排期、单文件直接写入或 Email 自动去重描述，但不授权实现 AI、Campaign、邮件、Inbox 或 CRM。

---

## 2. 登录方式

### 登录页面
字段：
- 部门
- 密码
- 30 天内保持登录

登录成功后：
- 如果部门存在多个操作人，要求选择当前操作人
- 右上角持续显示：`部门 · 操作人`

### 登录保护
- 连续 5 次错误：锁定 5 分钟
- Session 可配置为 7/30 天
- 管理员可强制部门全部退出
- 密码只存 hash

---

## 3. 用户角色

### Super Admin
全部权限。

### Manager
- 部门数据
- Campaign
- SOP
- 数据
- 员工任务

### Operator
- 导入达人
- 达人库
- Campaign
- Inbox
- CRM

### Viewer
只读。

V1 权限按“部门 + 角色 + 操作人”设计。

---

## 4. 页面结构

```text
登录
工作台
达人采集
达人库
Campaign
Inbox
CRM
SOP / 话术
数据中心
系统设置
```

---

## 5. 工作台

### 今日待办
- 待处理回复
- 待加微信
- 高意向达人
- 今日需跟进
- 待审核邮件
- 异常 Campaign

### 今日数据
- 新增达人
- 有效邮箱
- 已发送
- 成功送达
- 回复
- 回复率
- 新增微信
- 潜在合作
- 高意向

### 运行中的 Campaign
字段：
- 名称
- 负责人
- 今日发送
- 总发送
- 回复
- 加微信
- 状态

---

## 6. 达人采集

### 创建采集任务

字段：
- 任务名称
- 行业
- 细分方向
- 开发目的
- 核心动作
- 粉丝区间
- 目标数量
- 负责人
- 备注

系统输出：
- 结构化 Screening Rules
- MATCH / NOT_MATCH / UNKNOWN 与证据
- 导入后的 Refresh Priority Reasons

### 数据来源 V1
- 灰豚 Excel
- CSV
- 手工新增

### 数据源抽象
必须保留 `data_source` 层，未来可以扩展。

---

## 7. 导入流程

```text
创建多文件 ImportJob
↓
逐文件上传、识别列
↓
逐文件字段映射
↓
标准化
↓
文件内 / 跨文件 / 数据库去重
↓
联系方式提取
↓
统一 Preview
↓
人工 Confirm
↓
非破坏性 Merge
↓
生成导入结果
```

### 导入结果
- 总行数
- 成功
- 重复
- 新增
- 有邮箱
- 无邮箱
- 异常
- 跳过

---

## 8. 达人去重规则

优先级：
1. 小红书平台 ID
2. 灰豚 ID
3. 主页 URL

邮箱只用于疑似重复标记，不得自动合并达人。

匹配到已有达人：
- 默认不重复创建
- 更新允许更新的公开指标
- 保留原负责人和历史记录
- 严格遵守 Preview 中的非破坏性 Merge Plan

---

## 9. 达人库

### 列表字段
- 昵称
- 平台
- 赛道
- 粉丝
- 邮箱
- AI评分
- CRM阶段
- 负责人
- 最近联系
- 下次跟进

### 筛选
- 赛道
- 粉丝范围
- 联系方式
- AI评分
- 来源
- 负责人
- CRM状态
- Campaign
- 是否回复
- 是否加微信

---

## 10. 达人详情

Tab：
1. 基本资料
2. 数据表现
3. AI 分析
4. 触达历史
5. CRM
6. 备注

### 基本资料
- 昵称
- 小红书 ID
- 主页
- 简介
- 标签
- 赛道
- 地区
- MCN
- 邮箱
- 微信
- 电话
- 其他联系方式
- 来源

### 数据表现
仅显示真实导入/采集数据：
- 粉丝
- 获赞
- 笔记数
- 近 7 天笔记
- 平均点赞
- 平均收藏
- 平均评论
- 平均分享
- 爆文率
- 报价
- 灰豚指数

---

## 11. AI 达人分析

输出：
- 主赛道
- 细分赛道
- 达人类型
- 活跃程度
- 内容特征
- 合作匹配度
- 推荐开发方式
- 推荐邮件语气
- 个性化切入点
- 风险提示

### V1 评分建议
- 赛道匹配 30
- 粉丝区间 20
- 活跃度 20
- 内容匹配 20
- 联系方式完整度 10

评分只是排序工具。

---

## 12. SOP / Playbook

系统内置第一套：

`小红书创作者资源开发 V1`

内容：
- 筛选原则
- 标题库
- 首封模板
- Follow-up 1
- Follow-up 2
- 各赛道个性化开头
- 禁止表达
- 回复分类规则
- 微信沟通规则
- CRM 跟进节奏

每次修改必须创建新版本，不覆盖历史。

---

## 13. Campaign

### 创建步骤

#### Step 1 选择达人
来源：
- 采集任务
- 达人库筛选
- 手工选择

#### Step 2 Playbook
- 选择 SOP
- 选择模板
- 是否启用 A/B

#### Step 3 Sequence
默认：
- Day 0 首封
- Day 4 Follow-up 1
- Day 10 Follow-up 2

#### Step 4 发送
- 邮箱
- 每日上限
- 时间段
- 发送间隔
- 审核模式
- A/B 设置

---

## 14. 审核模式

- 全部人工审核
- 前 N 封审核
- 抽样审核
- 全自动

V1 默认：
`前 50 封人工审核`

---

## 15. Campaign 状态

- draft
- pending_review
- scheduled
- running
- paused
- completed
- cancelled
- failed

---

## 16. Campaign Lead 状态

- pending
- ready
- queued
- sent
- delivered
- replied
- bounced
- skipped
- suppressed
- stopped
- completed

---

## 17. 邮件停止条件

任一满足：
- 达人回复
- 退订
- 无效邮箱
- Hard Bounce
- 人工停止
- 加微信且用户设置停止邮件
- 已在 suppression list
- Campaign 取消

---

## 18. Inbox

分类：
- 全部
- 未处理
- 高意向
- 潜在意向
- 待加微信
- 暂不考虑
- 拒绝
- 自动回复
- 其他

每条回复展示：
- 达人
- Campaign
- 邮件线程
- AI 分类
- AI 置信度
- AI 摘要
- 建议下一步
- AI 建议回复
- 操作按钮

---

## 19. 回复标签

沿用 A-F：

- A：高意向
- B：潜在意向
- C：正常创作者
- D：已加微信但暂无需求
- E：邮件已回复未加微信
- F：无回复

额外系统类：
- unsubscribe
- auto_reply
- bounced
- unknown

---

## 20. CRM

阶段：
- 待开发
- 已发送邮件
- 第一次跟进
- 第二次跟进
- 已回复
- 已加微信
- 沟通中
- 潜在合作
- 高意向
- 暂不考虑
- 长期维护
- 已结束

支持：
- 看板
- 列表
- 拖拽
- 下次跟进日期
- 备注
- 负责人

---

## 21. CRM 业务字段

- 是否本人运营
- 个人 / 团队
- 是否全职
- 长期规划
- 主要痛点
- 商业化情况
- 合作开放度
- 心理预期
- 微信号
- 意向等级
- 下次跟进
- 备注

---

## 22. 默认跟进频率

- 高意向：1-3 天
- 潜在意向：7-15 天
- 普通创作者：30-60 天

---

## 23. Analytics

核心指标：
- 新增达人
- 有邮箱人数
- 有效邮箱人数
- 发送
- 送达
- 退信
- 回复
- 回复率
- 正向回复
- 正向回复率
- 加微信
- 微信转化率
- 潜在合作
- 高意向

维度：
- 赛道
- 粉丝区间
- Campaign
- 标题
- 模板
- 操作人
- 邮箱账号
- 时间

---

## 24. 北极星指标

每 1000 个有效触达达人产生的有效机会数：

```text
(潜在合作 + 高意向) / 有效触达达人 * 1000
```

---

## 25. 系统设置

- 部门
- 操作人
- 角色
- 权限
- 部门密码
- 邮箱账户
- AI Provider
- 模型
- Prompt Version
- 发送上限
- 操作日志
- 系统参数

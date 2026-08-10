# 给 Codex 的主指令

你现在负责实现一个公司内部使用的「达人智能触达系统 V1」。

你只负责代码实现，不负责重新定义产品。

开始前：
1. 阅读本项目全部 `/docs/*.md`
2. 严格遵守 `CODEX_RULES.md`
3. 不允许跳过数据库、测试、安全、审计
4. 不允许自行增加飞书登录
5. 不允许绕过灰豚限制
6. 不允许让 AI 虚构达人事实
7. 不允许邮件重复发送
8. 不允许回复后继续 Follow-up

开发顺序严格为：

Phase 0：
项目初始化、Docker、Next.js、FastAPI、PostgreSQL、Redis、Celery、Nginx。

Phase 1：
部门登录 → 操作人 → 导入 → 去重 → 达人库。

Phase 2：
Playbook → AI Adapter → 达人分析 → 个性化 → Campaign → 邮件预览。

Phase 3：
Mailbox → Send Queue → Follow-up → Reply Sync → Inbox → AI Reply Classifier。

Phase 4：
CRM → Tasks → Analytics → Audit。

Phase 5：
安全、备份、错误处理、测试、上线。

每次只开发当前 Phase，不提前重构下一 Phase。

每完成一个功能：
- 写 migration
- 写 API
- 写 service
- 写测试
- 写 UI
- 对照 Acceptance Criteria
- 更新 CHANGELOG

如果文档中出现冲突：
不要猜。
记录到 `docs/OPEN_QUESTIONS.md`。
采用最保守、不破坏数据的实现。

最终交付必须满足：
- Docker Compose 可启动
- `.env.example` 完整
- 数据库 migration 完整
- 测试可运行
- README 有启动说明
- Pilot 100 达人流程可跑通

# 当前项目目录与边界

本文描述已经采用的 monorepo 结构，不是候选架构。

```text
influencer_outreach_project/
├── apps/
│   ├── web/                         # Next.js UI
│   ├── api/                         # FastAPI HTTP 适配层
│   └── worker/                      # Celery 入口
├── packages/
│   ├── backend_core/                # 唯一 Python 业务核心
│   │   ├── src/backend_core/
│   │   │   ├── auth/
│   │   │   ├── audit/
│   │   │   ├── imports/
│   │   │   ├── influencers/
│   │   │   ├── db/
│   │   │   ├── config/
│   │   │   └── common/
│   │   └── tests/
│   ├── shared/
│   ├── types/
│   └── ui/
├── infrastructure/
│   ├── docker/
│   ├── nginx/
│   ├── migrations/
│   ├── scripts/
│   └── backup/
├── tests/
│   ├── integration/
│   └── smoke/
├── docs/
├── docker-compose.yml
├── pnpm-workspace.yaml
├── pyproject.toml
├── Makefile
└── README.md
```

## 强制边界

- Web 不直接访问数据库。
- `apps/api` 只处理 HTTP、认证依赖、参数和响应转换。
- `apps/worker` 只定义 Celery App、队列和 task entrypoint。
- Repository、Service、Matcher、Planner、Provider 和业务状态机只存在于 `packages/backend_core`。
- 不创建根目录 `services/`。
- 不在 API 与 Worker 各自复制业务规则。
- Provider、数据源 Adapter 和异步入口可替换，但都必须调用同一个 backend_core。

Phase 2 的详细边界和预计文件变更见 `docs/PHASE_2_SCOPE.md`。该文档冻结的是目标设计，不代表对应文件已经实现。

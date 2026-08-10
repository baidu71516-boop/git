# 推荐项目目录

```text
influencer-outreach/
├── README.md
├── docs/
│   ├── CODEX_RULES.md
│   ├── PRODUCT_PRD.md
│   ├── UI_SPEC.md
│   ├── ARCHITECTURE.md
│   ├── DATABASE_SCHEMA.md
│   ├── API_SPEC.md
│   ├── AI_RULES.md
│   ├── EMAIL_AUTOMATION.md
│   ├── SECURITY_RULES.md
│   ├── DEVELOPMENT_PLAN.md
│   └── ACCEPTANCE_CRITERIA.md
│
├── apps/
│   ├── web/
│   │   ├── app/
│   │   ├── components/
│   │   ├── features/
│   │   ├── lib/
│   │   └── types/
│   │
│   ├── api/
│   │   ├── app/
│   │   │   ├── main.py
│   │   │   ├── core/
│   │   │   ├── db/
│   │   │   ├── models/
│   │   │   ├── schemas/
│   │   │   ├── repositories/
│   │   │   ├── services/
│   │   │   └── routes/
│   │   └── tests/
│   │
│   └── worker/
│       ├── app/
│       └── tests/
│
├── packages/
│   ├── shared/
│   ├── types/
│   └── ui/
│
├── services/
│   ├── ai/
│   │   ├── providers/
│   │   ├── prompts/
│   │   ├── schemas/
│   │   └── tasks/
│   │
│   ├── email/
│   │   ├── providers/
│   │   ├── scheduler/
│   │   └── sync/
│   │
│   └── imports/
│       ├── adapters/
│       ├── mapping/
│       └── normalize/
│
├── infrastructure/
│   ├── docker/
│   ├── nginx/
│   ├── migrations/
│   └── scripts/
│
├── docker-compose.yml
├── .env.example
└── Makefile
```

## 原则

- Web 不直接访问 DB
- Route 不直接写复杂业务逻辑
- Provider 可替换
- Prompt 可版本化
- 数据源可替换
- 邮箱服务可替换

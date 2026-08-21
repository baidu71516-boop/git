# Backup Infrastructure

`influencer-outreach-backup` 是生产 PostgreSQL 的 custom-format 备份模板。
它在容器内使用 `pg_restore --list` 校验已完成的备份，生成 SHA-256 sidecar，
并保留 30 天。systemd service/timer 模板位于 `../systemd/`；安装、测试和恢复
列表检查步骤见 `../../docs/runbooks/production-deployment.md`。

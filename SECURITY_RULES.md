# 安全与稳定性规则

## 1. 登录

- 密码 Argon2id
- 5 次失败锁定 5 分钟
- Session Token 只存 hash
- Cookie HttpOnly
- Secure
- SameSite
- CSRF 防护

---

## 2. 权限

后端每个 API 必须鉴权。

禁止依赖前端隐藏按钮实现权限。

---

## 3. 敏感信息

以下禁止明文：
- 部门密码
- SMTP 密码
- AI Key
- Provider Key

Secrets：
- `.env`
- 生产环境 Secret Store

`.env` 不进 Git。

---

## 4. 数据导出

达人联系方式导出必须：
- 有权限
- 记录 audit
- 支持限制列

---

## 5. 操作日志

以下必须记录：
- 登录
- 登录失败
- 导入
- 批量导出
- 创建 Campaign
- 启动 Campaign
- 暂停/取消
- 修改 SOP
- 修改邮箱
- 修改系统设置
- 删除/屏蔽联系人

---

## 6. 数据删除

V1 默认软删除。

关键业务数据不允许直接物理删除。

---

## 7. 上传安全

- 文件类型白名单
- 文件大小限制
- 不执行宏
- Excel 只解析内容
- 文件名随机化
- 禁止路径穿越

---

## 8. 服务器

- Ubuntu LTS
- 非 root 运行服务
- SSH key
- 禁止密码 SSH（建议）
- 防火墙
- 只开放 80/443/SSH 管理来源
- 自动安全更新

---

## 9. 数据库

- 不公网开放 PostgreSQL
- 定时备份
- 每日备份
- 保留 7-30 天
- 定期恢复演练

---

## 10. AI

发送给 AI 的数据最小化。
无需提供完整邮箱、手机号时不要发送。

---

## 11. 日志

日志中不要打印：
- 完整密码
- SMTP password
- API key
- Session token
- 完整敏感凭证

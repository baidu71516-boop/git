# UI / UX 规范

> 本文件包含长期 V1 页面草案。当前 Phase 2 只实现明确标注的 Bulk Import、Freshness 和 Refresh Queue 功能性增量；AI、Campaign、邮件、CRM、通用 Batch Mutation 与整体视觉重构均不在当前范围。详细冻结契约见 `docs/PHASE_2_SCOPE.md`。

## 1. 总体风格

定位：内部企业后台。

原则：
- 快
- 清晰
- 少步骤
- 高密度
- 不做营销型花哨视觉
- 核心信息优先

推荐：
- Ant Design
- 桌面端优先
- 最小宽度 1280
- 响应式但不优先移动端

---

## 2. 主布局

左侧固定导航：
- 工作台
- 达人采集
- 达人库
- Campaign
- Inbox
- CRM
- SOP / 话术
- 数据中心
- 系统设置

顶部：
- 当前部门
- 当前操作人
- 通知
- 切换操作人
- 退出

---

## 3. 登录页

字段：
- 部门 Select
- 密码 Password
- 30天内保持登录 Checkbox
- 登录 Button

状态：
- 密码错误
- 部门停用
- 锁定
- 服务异常

登录后若操作人数 > 1：
显示操作人选择 Modal。

---

## 4. 工作台

布局：
- 第一行：5 个待办卡片
- 第二行：核心数据卡片
- 第三行：运行中 Campaign
- 第四行：回复率趋势 + 待处理列表

卡片均可点击跳转到筛选后的目标页面。

---

## 5. 达人采集页

顶部：
- 新建采集任务
- 导入记录
- 数据源说明

创建任务用 Step Form：
1. 基本目标
2. 结构化筛选规则
3. 导入
4. 结果

导入区支持：
- drag & drop
- Excel
- CSV
- 下载字段映射模板

### Phase 2 功能性补充

- 一次选择多个 CSV/XLSX，但每个文件独立上传和显示状态。
- 显示 uploaded/parsing/mapping_required/ready/failed/excluded。
- Blocking file 未 replace/exclude/retry 前禁止统一 Preview。
- Preview 首屏显示 Raw、Unique、Duplicate、Existing、New、Changed、No change、Warnings、Errors。
- 默认 Tab 为“需要处理”，行数据使用服务端分页，不渲染全部 2000 行。
- Preview 后禁止修改文件集合、Mapping 和 acquisition time。
- 保持人工 Confirm，不提供 Auto Confirm。

---

## 6. 达人库

使用 Table。

固定列：
- Checkbox
- 达人
- 赛道
- 粉丝
- 联系方式
- AI评分
- CRM阶段
- 负责人
- 最近联系
- 操作

支持：
- Column setting
- Sort
- Filter
- Pagination
- Batch actions

Phase 2 增加：
- 最近灰豚数据时间
- Freshness 状态
- 需要刷新筛选
- Refresh Queue 最小页面与 CSV 导出

`UI-BACKLOG-001`：粉丝数可显示为 `20.66万`，Tooltip/详情保留精确整数。
`UI-BACKLOG-002`：达人库整体视觉和信息层级重构继续延期。

Phase 2 不授权通用 Influencer Batch Mutation、AI Score 或整体视觉重构。

未来阶段批量操作（不属于当前 Phase 2，需重新冻结）：
- 加入 Campaign
- 分配负责人
- 设置标签
- 导出
- 停止触达

---

## 7. 达人详情 Drawer / Page

建议独立详情页。

Header：
- 头像
- 昵称
- 粉丝
- 赛道
- 邮箱
- AI分数
- 负责人
- CRM状态

Tabs：
- 基础
- 数据
- AI
- 触达
- CRM
- 备注

右侧快捷操作：
- 加入 Campaign
- 发邮件
- 标记加微信
- 创建跟进
- 停止触达

---

## 8. Campaign 列表

列：
- 名称
- 负责人
- 达人数
- 已发送
- 回复率
- 微信率
- 状态
- 更新时间
- 操作

操作：
- 查看
- 暂停
- 继续
- 复制
- 结束

---

## 9. Campaign 创建页

四步：
1. 达人
2. SOP / 模板
3. Sequence
4. 发送规则

提交前必须显示 Summary。

---

## 10. 邮件审核页

左：
达人列表。

中：
邮件预览。

右：
达人事实卡片 + AI个性化依据。

操作：
- 批准
- 编辑
- 重新生成
- 跳过
- 批量批准

必须清楚显示：
- 哪句话来自达人事实
- 使用哪个模板
- 使用哪个标题 variant

---

## 11. Inbox

三栏式布局：

左栏：
分类。

中栏：
回复列表。

右栏：
邮件线程 + AI建议 + 操作。

列表显示：
- 达人
- 最新回复摘要
- AI 分类
- 时间
- 是否待处理

---

## 12. CRM

默认 Kanban。

顶部切换：
- 看板
- 列表

每列显示数量。

卡片：
- 昵称
- 赛道
- 粉丝
- 负责人
- 最近联系
- 下次跟进
- A/B/C 标签

拖拽时写入 CRM Event。

---

## 13. SOP 页面

左：
Playbook 列表。

中：
版本列表。

右：
内容编辑。

任何修改必须：
- 保存新版本
- 填写变更说明
- 不覆盖正在运行 Campaign 所用版本

---

## 14. Analytics

顶部时间筛选：
- 今日
- 7天
- 30天
- 自定义

指标卡：
- 发送
- 回复
- 回复率
- 加微信
- 微信率
- 高意向

图表：
- 回复率趋势
- 赛道表现
- 粉丝区间
- 模板
- 标题
- 操作人

---

## 15. 系统设置

Tabs：
- 部门
- 操作人
- 角色权限
- 邮箱
- AI
- 发送规则
- 操作日志

危险操作必须二次确认。

# 器官分配与转运协调系统

Python 标准库独立项目。系统按器官类型、血型、地域、医疗匹配、紧急程度和等待时间排序候选患者，并管理提出、接受、转运、交接、植入或撤回流程。器官过期后所有继续流转操作都会被阻止，全部状态变化写入审计记录。

## 运行

```bash
python3 app.py --db organ_allocation.db
```

默认监听 `127.0.0.1:8203`，首页 `/`，健康检查 `/health`。

身份头：`X-User-Id`、`X-Role`。角色为 `viewer`、`hospital`、`coordinator`、`allocation_officer`、`auditor`；医院角色还需 `X-Hospital`。

## 主要接口

- `POST /api/donors`、`POST /api/candidates`：登记器官与候选患者。
- `GET /api/donors/{id}/ranking`：查看兼容候选排序。
- `POST /api/allocations`：提出唯一分配。
- `POST /api/allocations/{id}/accept`、`withdraw`：医院确认或撤回。
- `POST /api/allocations/{id}/transit`、`delay`：冷链转运和延误上报。
- `POST /api/allocations/{id}/handoff`、`handoff-accept`：来源医院发起、接收医院确认。
- `POST /api/allocations/{id}/implant`：确认植入。
- `POST /api/recalls`：协调员登记供体感染风险召回（`donor_id`、`risk_type`、`report_source`、`occurred_at`）。同一供体同一风险类型重复登记返回首次召回（200，`duplicate=true`）。未植入分配立即置为 `recalled` 并停住后续交接；已植入分配保留原状态，只生成受者随访。
- `POST /api/recalls/notifications/{id}/ack`：接收医院逐条确认召回通知。
- `POST /api/recalls/{id}/close`：协调员关闭召回；仍有未确认通知时返回 409。
- `GET /api/recalls`、`GET /api/recalls/{id}`：召回列表与详情（医院只能看到本机构相关通知）。
- `GET /api/allocations/{id}/audit`、`GET /api/state`：完整审计和权限视图；协调台 `recalls` 显示受影响医院、当前状态和未确认数量，医院视图包含 `recall_notifications` 与 `recipient_followups`。

## 测试

```bash
python3 -m unittest discover -s tests -v
```

## 主要局限

血型兼容与评分是演示规则，不包含 HLA 分型、器官大小、病程、儿科差异和真实移植网络规则。医院身份使用请求头模拟，SQLite 环境适合原型，不处理跨机构身份信任、远程患者隐私协议和真实冷链设备接入。

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
- `POST /api/recalls`：协调员登记供体安全召回（风险类型、报告来源、发生时刻）。系统找出该供体全部分配：未植入的立即停住后续交接，已植入的只生成受者随访并保留原状态；同一风险重复登记返回首次结果。
- `POST /api/recalls/{id}/acknowledge`、`close`：接收医院逐条确认召回通知，全部确认前召回不能关闭。
- `GET /api/recalls`、`GET /api/recalls/{id}`：召回视图；协调台 `/api/state` 展示受影响医院、当前状态和未确认数量，医院视图含本院待确认通知。
- `GET /api/allocations/{id}/audit`、`GET /api/state`：完整审计和权限视图。

召回判定、记录存取和请求入口分别在 `recall_policy.py`、`recall_store.py`、`recall_api.py` 三个业务文件维护。

## 测试

```bash
python3 -m unittest discover -s tests -v
```

## 主要局限

血型兼容与评分是演示规则，不包含 HLA 分型、器官大小、病程、儿科差异和真实移植网络规则。医院身份使用请求头模拟，SQLite 环境适合原型，不处理跨机构身份信任、远程患者隐私协议和真实冷链设备接入。

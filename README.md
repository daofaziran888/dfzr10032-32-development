# 多源数据采集与RPA自动化平台

面向网页抓取、API接入、增量采集、RPA流程编排、数据校验与采集监控的数据工程平台。

这是一个前后端分离的管理平台：前端 Vue 3 + Vite + TypeScript，后端 FastAPI（Python）。
两边各自独立启动，前端 dev server 已关掉自动打开页面，启动后按终端打印的地址手工打开。

## 目录结构

```text
.
├── frontend/                 Vue 3 + Vite + TypeScript 前端
│   ├── src/views/            每个业务模块一个页面
│   ├── src/api/              统一请求封装
│   ├── src/stores/           会话与筛选状态
│   └── vite.config.ts        dev server 配置（open: false）
├── backend/                  FastAPI（Python） 后端
│   ├── app/routers/          每个业务模块一组接口
│   ├── app/services/         业务规则与状态流转
│   └── app/store.py          内存数据仓库与示例数据
├── .gitignore
└── docker-compose.yml
```

## 启动

### 后端

```bash
cd backend
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
./run.sh
```

健康检查：`curl http://127.0.0.1:8000/api/health`

### 前端

```bash
cd frontend
npm install
npm run dev
```

前端默认监听 `http://127.0.0.1:5173/`，dev server 不会自动打开浏览器，
需要自己访问。`/api` 由 vite 代理到后端 `http://127.0.0.1:8000`。

## 业务模块

| 模块 | 目录 | 业务对象 | 主要字段 |
| --- | --- | --- | --- |
| 数据源配置 | `datasource` | 数据源 | 源端编号、连接类型、接入地址 |
| 网页采集规则 | `crawler` | 采集规则 | 规则编号、目标网址、页面选择器 |
| API接入管理 | `apiclient` | API客户端 | 客户端编号、接口名称、请求方法 |
| 增量采集 | `incremental` | 增量游标 | 游标编号、来源标识、增量字段 |
| RPA任务编排 | `robot_task` | RPA任务 | 任务编号、任务名称、页面定位器 |
| 数据接入通道 | `ingestion` | 接入通道 | 通道编号、通道名称、输入格式 |
| 格式解析器 | `parser` | 解析配置 | 解析编号、输入格式、输出格式 |
| 采集结果校验 | `validator` | 校验规则 | 规则编号、校验字段、校验类型 |
| 采集去重 | `dedup` | 去重策略 | 策略编号、输入来源、去重键 |
| 采集调度 | `schedule` | 调度计划 | 计划编号、关联任务、调度周期 |
| 采集监控 | `monitor` | 监控面板、采集告警 | 面板编号、采集源总数、今日采集量；告警编号、日志组、本次严重程度 |
| 采集日志 | `log_viewer` | 日志条目、采集故障 | 日志编号、关联任务、日志级别；故障编号、首次出现时间、重复次数 |
| 凭证管理 | `auth` | 认证凭证 | 凭证编号、凭证类型、关联数据源 |
| 采集转换管道 | `transform` | 转换节点 | 节点编号、输入来源、输出目标 |
| 数据流转编排 | `flow` | 流转管道 | 管道编号、管道名称、起始节点 |
| 数据血缘追踪 | `lineage` | 血缘关系 | 关系编号、上游节点、下游节点 |
| 采集报告 | `report` | 采集报告 | 报告编号、采集源、统计周期 |

## 约定

- 每个模块的前端页面在 `frontend/src/views/<模块>/index.vue`，后端接口在
  `backend/app/routers/<模块>.py`，业务规则在 `backend/app/services/<模块>.py`。
- 列表接口统一返回 `{ items, total, page, size }`，动作接口统一返回 `{ ok, message }`。
- 状态流转只允许在 `app/services` 里改，路由层不做业务判断。

## 采集故障归并与告警

- 归并键是「关联任务 + 错误堆栈」。同一任务、同一堆栈的日志归并到同一条采集故障；
  不同任务即使堆栈文本完全一样，也各自成为独立故障。
- 每条原始日志只计一次：归并后日志上会记录 `故障编号`/`故障id`，重复执行归并或重复
  登记同一个 `日志编号` 都不会让 `重复次数` 增加。故障明细保留 `首次出现时间`、
  `最近出现时间`、`重复次数` 和 `日志组`，原始日志可用
  `GET /api/log_viewer/faults/{id}/logs` 反查。
- 已处置（已处理 / 已关闭 / 已忽略）的故障再次出现时，不会被重新打开，而是新开一条故障，
  用 `上次故障编号` 指向前一次。
- 采集监控按采集故障明细登记告警：`POST /api/monitor/alerts/generate`。每条告警保留
  对应 `日志组`、`本次严重程度`、`重复次数`，并按严重程度提示同任务仍未处置的高等级故障。
- 告警与故障一一对应。关闭某条告警只关闭它自己的故障，同任务的其他故障及其告警不受影响，
  返回的 `关闭说明` 与 `同任务未处置` 会列出仍需接单的故障。

主要接口：

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| POST | `/api/log_viewer/faults/group` | 归并尚未归并的原始日志 |
| GET | `/api/log_viewer/faults` | 采集故障明细（`task`、`status` 可筛选） |
| GET | `/api/log_viewer/faults/{id}/logs` | 某条故障归并的原始日志 |
| POST | `/api/log_viewer/faults/{id}/actions` | 开始处理 / 标记已处理 / 关闭故障 / 忽略故障 |
| POST | `/api/monitor/alerts/generate` | 按采集故障生成告警记录 |
| GET | `/api/monitor/alerts` | 告警记录（`task`、`fault_id`、`status` 可筛选） |
| POST | `/api/monitor/alerts/{id}/actions` | 领取告警 / 关闭告警 / 忽略告警 |

# canvas-mcp

Canvas LMS 的 MCP server。纯标准库，无第三方依赖，**只读**。

面向 Fuqua 的独立实例开发，但 host 可配，任何 Canvas 都能用。

## 为什么需要它

Canvas 网页端要点四五层才能看到「这周到底要交什么」，而且跨课程没有统一视图。
这个 server 把作业、考试、课程日历、公告合并成一条按时间排序的流。

## 两个坑（也是写这个东西的主要动机）

**1. token 不跨实例。**
Fuqua 用的是 `fuqua.instructure.com`，**不是** `canvas.duke.edu`。同一个 token
打 Duke 主站返回 `401 Invalid access token.` —— 看着像 token 失效，其实是 host 错了。
本 server 的 401 报错文案会直接把这件事说出来。

**2. `due_at` 是 UTC，直接读会把每个 DDL 记晚一天。**

```
"due_at": "2026-09-07T03:59:59Z"   ← 实际是 09-06 周日 23:59 EDT
```

学校把截止时间设在本地 23:59，转成 UTC 就翻到了次日凌晨。所有对外输出的时间
都过 `model.local()`，同时给出本地时刻、星期和「几天后 / 逾期几天」。

## 安装

```bash
git clone git@github.com:andycjm17/canvas-mcp.git
cd canvas-mcp
```

不用装依赖，Python 3.9+ 即可（用到 `zoneinfo`）。

## 配置

token 在 Canvas → Account → Settings → Approved Integrations → **+ New Access Token** 生成。

写进 `~/.config/canvas-mcp/config.json`（会被强制设成 `0600`）：

```bash
python3 -c "
import sys; sys.path.insert(0, '.')
from canvas_mcp import config
config.save('<你的 token>', host_value='fuqua.instructure.com', timezone_value='America/New_York')
"
```

也可以走环境变量：`CANVAS_MCP_TOKEN` / `CANVAS_MCP_HOST` / `CANVAS_MCP_TZ`（优先级更高）。

**token 永远不进版本库。** `.gitignore` 里已经挡了 `config.json`、`*.token`、`.env`。

## 注册到 Claude Code

```bash
claude mcp add canvas -s user -- python3 /path/to/canvas-mcp/run.py
```

## 工具

| 工具 | 用途 |
|---|---|
| `canvas_status` | 自检：token 有效性、连的哪个实例、时区。排查问题先用它 |
| `list_courses` | 课程列表，含学期和当前总分 |
| `upcoming` | **主力工具**。未来 N 天的作业 / 考试 / 日历 / 公告，按时间排序 |
| `overdue` | 过期未交的作业 |
| `list_assignments` | 某门课的全部作业，含截止时间、分值、提交与评分状态 |
| `get_assignment` | 单条作业的完整要求正文（HTML 转纯文本） |
| `list_announcements` | 最近公告，可跨课程 |
| `list_grades` | 各门课当前总分 |

课程参数接受 id 或课程名，名字可以只写一部分（`"Data Analytics"` 能匹配到
`Data Analytics for Business`）。匹配到多门会报错并列出候选，不会瞎猜。

## 实现说明

- **分页**：Canvas 把下一页放在 `Link` 响应头的 `rel="next"` 里，默认 `per_page=10`。
  不跟 next 的话课程一多就静默截断。`api.paginate()` 会跟到底（上限 50 页防成环）。
- **数组参数**：Canvas 用 `state[]=active&state[]=completed` 这种同名重复 key，
  所以 `_encode()` 在值是 list 时展开成多个 pair，不能直接用 `urlencode(dict)`。
- **`/planner/items`**：`upcoming` 的数据源。它把作业、日历事件、公告、测验揉进
  一个流，统一时间字段是 `plannable_date`；各类型自己的时间字段藏在 `plannable`
  里且形状不一致，所以一律以 `plannable_date` 为准。
- **成绩**：往期课程不在 `enrollment_state=active` 的列表里，查名字时必须带上
  `state[]=completed`，否则会退化成 `"course 3639"`。

## 只读

所有工具都只发 GET。不会交作业、改成绩、发帖或退课。

## 布局

```
run.py                 入口（显式处理 sys.path，MCP client 的工作目录不确定）
canvas_mcp/config.py   凭证与实例配置，0600 写入
canvas_mcp/api.py      HTTP 客户端、分页、错误翻译
canvas_mcp/model.py    时区转换、HTML 转纯文本、结构整理
canvas_mcp/server.py   工具定义 + JSON-RPC over stdio
```

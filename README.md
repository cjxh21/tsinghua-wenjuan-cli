# 清华问卷系统 CLI / Python API 原型

2026-09-30，目标站点：https://wenjuan.tsinghua.edu.cn。

结论：可以把这个网站的原生请求封装成普通 Agent 能调用的 CLI。已有可运行的本地原型；公开前端接口核对、签名实现、离线测试均已完成。**2026-09-30 晚已在本机导入用户登录会话（通过 DevTools 复制的 cURL），六个只读接口有登录实测记录**，见下表。2026-10-01 修复了会话导入与登录检查的四个问题，41 项离线测试通过，更新后的 doctor 登录实测成功。写操作仍未在线执行，不能称为已完成增删改查验收。本次 CLI 研究与测试没有修改、删除、创建或发布任何线上问卷。

清华系统基于问卷网，之前工作区的问卷星 `wjx` CLI 和 API Key 属于另一个平台，不能用于这里。

## 快速开始

只需要 Python 3.9+ 和系统 `curl`，没有第三方依赖、不启动服务。

```bash
python3 thuwj.py capabilities      # 无需登录即可查看有哪些操作
python3 thuwj.py auth status       # 查看本机是否已导入会话
python3 thuwj.py doctor            # 会话有效时返回 ok:true
git config core.hooksPath .githooks   # 克隆后建议执行一次：启用提交前凭据自检
```

登录会话**只在本机保存**，获取与导入方式见下方 [登录只在本机接入](#登录只在本机接入)。凭据永不入库：`.state/` 已被忽略，仓库自带 `check_no_secrets.py` 与提交/推送钩子。

## 可直接查看的能力

运行环境：Python 3.9+、系统 curl。不需要安装 Python 依赖，不启动 HTTP 服务。

```bash
cd /path/to/tsinghua-wenjuan-cli   # 本仓库根目录
python3 thuwj.py --help
python3 thuwj.py capabilities
python3 thuwj.py auth status
```

成功/失败均输出一行 JSON。业务失败退出码为 2；不要只按 HTTP 200 判断成功。

| 能力 | 当前实现 | 验证边界 |
|---|---|---|
| 列问卷 | `call project.list` | **已登录实测 HTTP 200**：必须带完整筛选集（`p_type/time_order/search_txt/create_type/plugin/status`，已写入契约 `defaults`），只有 `page/page_size` 会 422；`page_size` 最小为 2。返回 `data.project_list` + `data.total_count/total_page`。注意列表里的 `rspd_count` 可能滞后（实测列表显示 0，回收数接口显示 9） |
| 读设置 | `call project.settings --project …` | **已登录实测 HTTP 200**，返回 120 个设置字段；这是设置页，不是完整题目/逻辑定义 |
| 读回收数量 | `call project.count --body-file …` | **已登录实测 HTTP 200**，需 `proj_id` + `scene_type`（表单编码），返回 `rspd_count/new_rspd_count/proj_status` |
| 发布前自检 | `call project.publish_check --project …` | **已登录实测 HTTP 200**，返回 `err_msg/invalid_qid_list` 等；只读，不改变发布状态 |
| 账号套餐 | `call account.package` | **已登录实测 HTTP 200**，返回题目/选项/音视频配额等 |
| 创建、复制、改名、删除、改变发布状态 | 相应 `call project.*` | 路径与编码有源码依据；默认只输出计划；**未在线执行或验收**；创建不是直接上传 JSONL |
| 查看下载中心 | `call downloads.list` | **已登录实测 HTTP 200**，返回 `data.task_list/data.total`；当前账号 0 个任务 |
| 已生成结果文件下载 | `download --page … --index … --out …` | 按下载中心任务获取原生 `download_url`；下载实现和边界已离线测试；没有实际下载你的答卷 |
| 导出准备请求 | `call export.prepare` | 接口已定位；**未在线执行**（会在账号上生成导出任务，属于写操作），具体导出参数及任务轮询尚未核对 |
| 题目及逻辑增删改查 | `request` 通用请求入口 | **未验收**：需要一份编辑器 Network 记录确认端点与完整原生 JSON；尚未提供“增加某题”这种已验收的高级命令 |

每份返回应检查 `ok`。`capabilities` 与 dry-run 的 `authenticated_verified:false` 是保守的全量在线验收标记；不会根据历史登录检查自动改成 true，也不能代替逐个能力的验收。`call` 成功只说明该次服务器没有返回业务错误，修改后仍应读回对应结构/状态。

## 登录只在本机接入

原型不从 Codex、浏览器数据库、钥匙串或其他账号的配置里提取 cookie。普通 Agent 可以通过显式指定的本地登录文件调用，无需 Computer Use。

当前采用用户在本机显式导入会话的方式，已通过只读 doctor 登录检查。CLI 不依赖浏览器自动化或浏览器内部凭据存储。

1. 在已登录的清华站点用浏览器开发者工具打开 Network，刷新“我的问卷”列表。
2. 将该站点请求导出为包含凭据的 HAR，保存到本机一个私有目录。浏览器默认的“脱敏 HAR”通常不含 Cookie，无法用于登录。HAR 可能包含你的登录 cookie 和页面内容，**不要发到群、仓库或云端**。
3. 在本机导入并只读验收：

```bash
python3 thuwj.py auth import-har --har /本机私有目录/清华站点.har
python3 thuwj.py bootstrap
python3 thuwj.py doctor
```

只导入 `wenjuan.tsinghua.edu.cn` 请求的 cookie，默认文件为本目录 `.state/session.json`，权限 0600；命令不打印凭据。导入成功不代表会话有效，必须用 `doctor` 读取列表确认。会话过期会输出 `AUTH_REQUIRED`，重新登录并导入即可，不保存清华账号密码。

### 更省事：从 DevTools 复制（不用导出 HAR）

导出含凭据的 HAR 对不熟悉 DevTools 的人偏麻烦。`import_session.py` 直接读剪贴板，接受三种内容，凭据不打印、不作为命令行参数、不进入聊天记录。

**方式 A（最省事，推荐）——右键复制成 cURL：**

1. 在**已登录**的浏览器里打开 `https://wenjuan.tsinghua.edu.cn`，按 F12（macOS 也可 ⌥⌘I）打开开发者工具，切到 **Network / 网络** 面板并刷新页面。
2. 在请求列表里**右键点**任意一条发往 `wenjuan.tsinghua.edu.cn` 的请求（筛选框输入 `list`，选类型为 `xhr` 的那条更稳）→ **复制 / Copy → 以 cURL 格式复制 / Copy as cURL**。
3. 回本机终端执行（无需把内容贴给任何人）：

```bash
cd /path/to/tsinghua-wenjuan-cli   # 本仓库根目录
python3 import_session.py          # 读剪贴板；也可 pbpaste | python3 import_session.py --stdin
python3 thuwj.py auth status       # 期望 configured:true
python3 thuwj.py doctor            # 期望 ok:true，才算会话真的有效
```

cURL 必须是浏览器复制的一条 POSIX curl 命令，且包含唯一的清华问卷 HTTPS 同源 URL。只取实际 `-H/--header` 的 Cookie 或 `-b/--cookie` 的内联串，支持短选项、长选项和等号形式；不执行命令，不展开变量，也不读取 cURL 的配置或 Cookie 文件。外站 URL、多个请求 URL、多个 Cookie 来源、未知选项或损坏引号会明确报错，原有会话不会被覆盖。Cookie 不从 URL 或请求体中兜底搜索。

shell 解码后保留值中的字面引号（如 `current_rid=""`），完整 Cookie 串内的同名值取最后一个。单独复制的 `user|timestamp|signature` 格式 sessionid 也可导入。Cookie/HAR 导入摘要不打印值；doctor 只有在认证与列表结构都正确时才报告会话有效，合法空账号也能通过。

**方式 B——只复制 Cookie 那一行：** 同上面第 1 步，点开请求 → **Headers → Request Headers** → 找到 `Cookie:` 行 → **右键 → Copy value**（要选 “Copy value”；普通 “Copy” 可能不含值），再执行第 3 步的 `python3 import_session.py`。

只复制到 `sessionid` 一个值也能用（此时没有 `_xsrf`，写操作可能被 CSRF 拦截，建议复制整行）。已有 HAR 时可用 `python3 import_session.py --har ~/私有目录/站点.har`，行为与 `auth import-har` 一致。输出只列出 cookie 名字与 `has_xsrf`，成功不代表会话有效，必须以 `doctor` 为准。

如果用户愿意手动整理 cookie，也可在私有文件写入：

```json
{"origin":"https://wenjuan.tsinghua.edu.cn","cookies":{"sessionid":"由用户在本机填写","_xsrf":"如请求中存在则填写"}}
```

随后用 `--session-file /私有目录/session.json` 指定文件。不要把真实值粘贴到聊天或作为命令行参数。

## 给普通 Agent 的调用例子

以下在线命令必须先完成登录导入与 `doctor`。参数从真实网页请求或已验收的调用获取，不猜字段，不把单页当作全部数据。示例中的 `0123456789abcdef01234567` 是**占位项目 ID**，请替换成你自己的 24 位问卷 ID。

```bash
# 原生问卷列表，保留服务端分页信息。
python3 thuwj.py call project.list

# 读取设置；这不是整份题目/逻辑定义的备份。
python3 thuwj.py call project.settings --project 0123456789abcdef01234567

# 下载中心列表：先查看任务，再选当前页上的序号（从 1 开始）。
python3 thuwj.py call downloads.list
python3 thuwj.py download --page 1 --index 1 --out ./exports/结果.xlsx
```

下载链接来自服务器，不根据项目 ID 猜 URL。任务 `status=1` 才能下载；`0` 准备中，`3` 已过期。跨域 CDN 和跨域重定向不会收到清华登录 cookie；拒绝降级到 HTTP，不把登录 HTML / JSON 错误保存成表格，不覆盖已有结果。

写操作先形成 JSON 文件，再预览。`title.json` 示例：

```json
{"title":"待确认的新标题"}
```

```bash
# 以下只展示方法、路径和字段名，不发送网络请求。
python3 thuwj.py call project.rename --project 0123456789abcdef01234567 --body-file title.json

# 只有用户明确要求修改后，Agent 才添加 --apply。
python3 thuwj.py call project.rename --project 0123456789abcdef01234567 --body-file title.json --apply
```

`project.create/copy/delete/status` 需要完整的原生请求体。请先在单独草稿验证，**不要拿已发布的正式问卷试接口**；本文不提供猜测的状态值或删题 payload。

新增编辑器能力时，可通过通用入口发送已经核对的真实接口：

```text
python3 thuwj.py request POST /从Network核对的清华原生接口/ --body-file native-payload.json
python3 thuwj.py request POST /从Network核对的清华原生接口/ --body-file native-payload.json --signer params --encoding json --apply
```

上面的路径是说明性占位符。不同接口可能用 `params`（参数签名）或 `header`（Authorization 签名），也可能用 `form` 而不是 `json`，均应按前端/真实请求核对。

## 直接通过 Python 调用

```python
from thuwj import Client

client = Client(session_file="/本机私有目录/session.json")
result = client.call("project.list")
assert result["ok"]
# SDK 的写操作也默认返回 dry-run 计划。
plan = client.call("project.rename", project="0123456789abcdef01234567", body={"title":"示例"})
```

其他普通 Agent 有 Python 或 shell 就能使用；无需读写 Codex 内部配置，也无需绑定某个模型。以后可以将 `Client.call` 包装为 stdio MCP；目前没有启动对外服务或给其他主机发放访问凭据。

## 当前接入方式与后续验收

本原型读取公开的 Nuxt 脚本，解析签名配置，按前端算法计算签名；**不执行下载的 JavaScript**。签名常量属于公开前端配置，不能代替登录会话。用户 cookie、公开签名缓存和导出文件均留在本机，TLS 校验保持开启。

接口属于网页内部接口，未来更新可能改变字段/签名。`bootstrap` 可刷新公开签名模块，但不自动猜测新契约。源码依据和哈希见 `research.json`，操作声明见 `contracts.json`。

后续完整闭环需要：登录后只读验证完整题目结构/答卷列表；在独立草稿验证创建与题目增删改及性别联动；发起一次原生导出、核对任务状态、下载文件并比对答卷数。执行这些验证后才能把相应能力标为已验收。

问卷网官方另有 [MCP](https://www.wenjuan.com/mcp/) 和 [OpenAPI](https://open.wenjuan.com/document/)。公开 MCP 指向 `mcp.wenjuan.com`，公开 API 使用应用密钥及自己的 V4 协议。**未找到清华部署开放相同凭据和服务的证据，不能假定改个域名就能使用**。如果学校管理员能提供正式 API，优先替换底层 adapter，CLI 的 JSON 调用方式可继续保留。

## 验证

离线：41 项测试通过（`test_thuwj.py` + `test_import_session.py`）。此前自动 bootstrap 成功读取公开模块；匿名线上探测返回原生认证错误 `10001`，CLI 将其识别为 `AUTH_REQUIRED`、退出码 2。

带登录会话（2026-09-30 23:2x，本机导入用户 cURL 会话后）：`doctor` 返回 `ok:true`、`project_total:1`；`project.list / project.settings / project.count / project.publish_check / account.package / downloads.list` 全部 HTTP 200 且 `ok:true`。**没有执行任何写操作**（改名/创建/删除/发布/导出任务）。

本轮复核（2026-10-01）：更新后的 `doctor` 校验 `data.project_list`、非负整数 `total_count/total_page` 后返回 `ok:true`、`session_valid:true`、`project_total:1`。这次只重跑了登录列表检查，未重复执行上表所有只读请求。坏结构返回 `UNEXPECTED_RESPONSE`，不会把无关 JSON 的 HTTP 200 当成登录成功。

```bash
python3 -m unittest -v test_thuwj.py test_import_session.py
python3 thuwj.py auth status
python3 thuwj.py doctor
python3 thuwj.py doctor --anonymous
```

匿名探测应返回 `ok:false` 和 `AUTH_REQUIRED`，这是认证边界验证，不能据此声称已接入用户账号。测试涵盖 HTTP 200 的业务失败、签名值转换、无网络的修改预览、契约默认参数的合并优先级、凭据的站点隔离、cURL 剪贴板解析（含同名 cookie 取最新、值内嵌引号）、下载重定向、假表格识别和不覆盖已有结果。

会话凭据只存在于本机 `.state/session.json`（0600，已 gitignore）；若曾把 cookie 复制到聊天、群或任何云端位置，应视为已泄露，用完请到站点退出登录使其失效。

## 公开仓库与凭据安全

本仓库只包含公开前端接口的调用代码、导出契约和离线测试，**不包含任何账号凭据**，可以公开。

- `.state/session.json`（登录会话）与 `.state/signer.json`（公开签名缓存）都在 `.gitignore` 中；`exports/`、`*.har`、`session*.json` 同样被排除。
- `check_no_secrets.py` 扫描全部跟踪文件，发现 `sessionid=`、Cookie 请求头、XSRF 令牌等疑似凭据即退出 1；它**只报告文件与行号，不打印命中的值**。
- `.githooks/pre-commit` 与 `pre-push` 调用同一脚本。克隆后启用一次：`git config core.hooksPath .githooks`（没有启用钩子时，`python3 check_no_secrets.py` 仍可手动或由 CI 执行）。
- GitHub 端如需再上一层：在 `.github/workflows/` 放一个跑同样两条命令的 workflow，并开启仓库的 push protection（公开仓库默认开启 secret scanning）。提交 workflow 前需要 CLI 具备 `workflow` 权限：`gh auth refresh -s workflow`。
- 测试只使用合成凭据和 `FakeTransport`，不依赖真实登录态；文档示例中的问卷 ID 是占位符。

需要在另一台自己的机器上继续时，**不要通过 git 传递会话**，在那台机器上重新走一次 [登录只在本机接入](#登录只在本机接入) 即可。若凭据曾被粘贴到聊天、群或任何云端位置，应视为已泄露，用完到站点退出登录使其失效。

## DevStd 开发入口

本目录已独立登记为 DevStd 项目“清华问卷 CLI”。功能图包含四个已有职责：本地登录凭据导入、登录状态只读检查、原生接口调用、已生成结果文件下载；每个功能有输入输出、真实文件绑定、流程和验收规则。题目与关联逻辑编辑、完整答卷导出闭环保留为两个待实现节点。

```bash
cd /path/to/tsinghua-wenjuan-cli   # 本仓库根目录
devstd context
devstd coverage
devstd                         # 打开当前项目工作台
devstd context --node <本轮功能ID>
devstd verify --node <本轮功能ID>
```

后续 Agent 从 [AGENTS.md](AGENTS.md) 进入开发流程；功能 ID 从当前 context 取得，保持已有节点身份。项目登记的验证命令是 `python3 -m unittest -v test_thuwj.py test_import_session.py`，超时 120 秒，只运行离线检查。线上写入、真实答卷文件下载及行数比对分别记录人工证据，不由离线 `verification.state=passed` 自动证明。当前未启用 DevStd 文件变更归档；开发意图、规则版本、检查结果和报告记录在 DevStd 事件中。

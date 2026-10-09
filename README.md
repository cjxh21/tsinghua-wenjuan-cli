# 清华问卷 CLI / Python SDK

把清华问卷站点（`wenjuan.tsinghua.edu.cn`，问卷网部署）的网页内部接口封装成命令行工具与 Python SDK：列问卷、读设置与回收数、发布前自检、下载已生成的结果文件。只用 Python 3.9+ 标准库和系统 `curl`，无第三方依赖、不启动服务、不执行页面里的 JavaScript。

> 非官方工具，与清华或问卷网无隶属关系。接口属网页内部接口，站点更新可能使其失效；请只用于你有权限访问的问卷。

## 快速开始

需要 Python 3.9+、Git 和系统 `curl`，无需安装 Python 第三方包。

```bash
git clone https://github.com/cjxh21/tsinghua-wenjuan-cli.git
cd tsinghua-wenjuan-cli
python3 thuwj.py capabilities        # 免登录查看全部操作
python3 thuwj.py auth status         # 本机是否已导入会话
git config core.hooksPath .githooks  # 建议执行一次：启用提交前凭据自检
```

第一次使用先按下一节导入自己的登录会话，再运行 `python3 thuwj.py doctor`；只有返回 `ok:true` 才继续在线调用。克隆仓库不会获得任何人的登录会话或答卷。

成功与失败都输出一行 JSON，业务失败退出码为 2；不要只凭 HTTP 200 判断成功。

## 登录会话只在本机

不从浏览器数据库、钥匙串或任何账号配置里提取 cookie，也不保存账号密码。最省事的方式：

1. 在**已登录**的浏览器打开站点，按 F12 → **Network / 网络** → 刷新页面；
2. 在请求列表里**右键**任意一条发往 `wenjuan.tsinghua.edu.cn` 的请求（筛 `list` 并选类型 `xhr` 更快）→ **复制 → 以 cURL 格式复制**；
3. 回终端执行（内容无需贴给任何人）：

```bash
python3 import_session.py            # 读剪贴板；也可 pbpaste | python3 import_session.py --stdin
python3 thuwj.py doctor              # 期望 ok:true，才算会话真的有效
```

只提取 `-H 'cookie: …'` 或 `-b '…'` 里的内联 Cookie，**不执行命令、不展开变量**；外站 URL、多个请求 URL、多个 Cookie 来源、未知选项或损坏引号都会明确报错，且不会覆盖已有会话。也支持只复制 `Cookie:` 行的值、单独的 `sessionid` 值，或 `python3 import_session.py --har 文件.har`。

会话写入 `.state/session.json`（权限 0600，已 gitignore）；过期后重新导入即可。导入成功不代表会话有效，以 `doctor` 为准。

## 普通用户与 Agent 调用

下列命令在克隆后的目录执行。先列出问卷，再把占位 ID 换成列表返回的实际 24 位问卷 ID：

```bash
python3 thuwj.py call project.list
python3 thuwj.py call project.settings --project 0123456789abcdef01234567
python3 thuwj.py --help
python3 thuwj.py call --help
```

读取回收数需要请求体：把 `{"proj_id":"0123456789abcdef01234567","scene_type":"survey"}` 保存为本机的 `exports/count.json`（先创建 `exports/` 目录，并替换实际 ID），再运行 `python3 thuwj.py call project.count --body-file exports/count.json`。

其他本机 Agent 只要有该目录和会话文件的访问权限，就能运行同样的命令。也可从任意工作目录用绝对路径调用：

```bash
python3 "/你的安装目录/tsinghua-wenjuan-cli/thuwj.py" capabilities
```

默认会话和签名缓存相对脚本目录定位；`--body-file`、`--out` 等相对路径相对当前工作目录定位。其他机器需要自行导入登录会话，不要把 `.state/` 上传或复制到公开位置。Agent 的线上写入须有用户授权，并显式加 `--apply`。

Python SDK 在该目录（或将其加入 Python 模块搜索路径后）使用：

```python
from thuwj import Client

client = Client()
result = client.call("project.list")
assert result["ok"]
```

## 命令速查

| 能力 | 命令 | 在线状态 |
|---|---|---|
| 列问卷 | `call project.list` | ✅ 200（契约已内置完整筛选集；`page_size` 最小 2） |
| 读设置 / 回收数 / 发布前自检 / 账号套餐 | `call project.settings`、`project.count`、`project.publish_check`、`account.package` | ✅ 200 |
| 下载中心任务列表 | `call downloads.list` | ✅ 200（任务生成期间可能短暂返回 `10003`，重试即可） |
| 下载已生成的结果文件 | `download --page 1 --index 1 --out ./exports/结果.xlsx` | ✅ 已用真实任务验收（行数 = 服务端 `rspd_count`） |
| Python SDK | `from thuwj import Client` | 同上；写操作默认返回 dry-run 计划 |
| 改名 / 创建 / 复制 / 删除 / 发布 | `call project.rename` 等 | ⚠️ 默认只输出计划，**未在线验收**；加 `--apply` 才真正执行 |
| 创建正式答卷导出任务 | `call export.create --body-file export.json --apply` | ✅ 2026-10-10 实测创建、查询、下载完整 10 份答卷 |
| 预准备缓存 | `call export.prepare` | 仅预准备；不能代替创建下载任务 |
| 题目与逻辑增删改查 | `request` 通用入口 | ⚠️ 未实现，需先抓编辑器请求核对契约 |

写操作先写 JSON 再预览，确认无误后才加 `--apply`；新增接口用 `request` 发送已经核对过的真实请求，不猜字段。

## 导出答卷

先运行 `python3 thuwj.py call report.base --project 问卷ID`，使用返回的 `response.data.version` 和 `rspd_count`，不要使用账号套餐版本。将下面内容保存为 `export.json`，替换实际 ID、版本和条数：

```json
{"project_id":"你的24位问卷ID","version":1,"filter_count":10}
```

```bash
python3 thuwj.py call export.create --body-file export.json --dry-run
python3 thuwj.py call export.create --body-file export.json --apply
python3 thuwj.py call downloads.list
python3 thuwj.py download --page 1 --index 1 --out ./exports/答卷.xlsx
```

创建返回成功只表示任务已接受。列表中核对项目、文件名和任务时间，等 `status=1` 后再下载；`--index` 是当前列表位置，不是固定任务身份，有其他任务时须重新核对。最后检查文件答卷行数与导出时条数一致。超时或失败时先查列表，避免重复创建。

## 凭据安全

- `.state/`、`exports/`、`*.har`、`session*.json` 均已忽略，**永不入库**；
- `check_no_secrets.py` 扫描全部跟踪文件，命中 `sessionid=`、Cookie 请求头、XSRF 令牌即退出 1；它只报告文件与行号，**不打印命中的值**；
- `.githooks/pre-commit` 与 `pre-push` 调用同一脚本（克隆后执行一次 `git config core.hooksPath .githooks` 启用）；
- 凭据若曾进入聊天、群或云端位置，应视为已泄露，用完到站点退出登录使其失效；
- 导出的答卷文件含填写者个人信息，请自行妥善保管。

## 已知边界

- 列表接口的 `rspd_count` 可能滞后，回收数请以 `project.count` 为准；
- 正式导出用 POST `/report/api/download/`，其中 XLSX 为 `file_type:[1]`，原始答卷为 `sub_type:3`；GET `pre_request` 仅是预准备；
- 匿名探测固定返回 `AUTH_REQUIRED`（业务码 `10001`），这验证的是认证边界，不能当作登录成功。

离线检查：`python3 -m unittest test_thuwj.py test_import_session.py`（42 项）。
接口依据与逐条实测记录见 [research.json](research.json)，操作清单与默认参数见 [contracts.json](contracts.json)。

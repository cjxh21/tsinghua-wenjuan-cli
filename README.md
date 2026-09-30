# 清华问卷 CLI / Python SDK

把清华问卷站点（`wenjuan.tsinghua.edu.cn`，问卷网部署）的网页内部接口封装成命令行工具与 Python SDK：列问卷、读设置与回收数、发布前自检、下载已生成的结果文件。只用 Python 3.9+ 标准库和系统 `curl`，无第三方依赖、不启动服务、不执行页面里的 JavaScript。

> 非官方工具，与清华或问卷网无隶属关系。接口属网页内部接口，站点更新可能使其失效；请只用于你有权限访问的问卷。

## 快速开始

```bash
python3 thuwj.py capabilities        # 免登录查看全部操作
python3 thuwj.py auth status         # 本机是否已导入会话
python3 thuwj.py doctor              # 会话有效时返回 ok:true
git config core.hooksPath .githooks  # 建议执行一次：启用提交前凭据自检
```

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

## 命令速查

| 能力 | 命令 | 在线状态 |
|---|---|---|
| 列问卷 | `call project.list` | ✅ 200（契约已内置完整筛选集；`page_size` 最小 2） |
| 读设置 / 回收数 / 发布前自检 / 账号套餐 | `call project.settings`、`project.count`、`project.publish_check`、`account.package` | ✅ 200 |
| 下载中心任务列表 | `call downloads.list` | ✅ 200（任务生成期间可能短暂返回 `10003`，重试即可） |
| 下载已生成的结果文件 | `download --page 1 --index 1 --out ./exports/结果.xlsx` | ✅ 已用真实任务验收（行数 = 服务端 `rspd_count`） |
| Python SDK | `from thuwj import Client` | 同上；写操作默认返回 dry-run 计划 |
| 改名 / 创建 / 复制 / 删除 / 发布 | `call project.rename` 等 | ⚠️ 默认只输出计划，**未在线验收**；加 `--apply` 才真正执行 |
| 触发导出任务 | `call export.prepare` | ⚠️ **未验收**：沿用前端固定参数集会被接受但不产出任务（详见 `research.json`） |
| 题目与逻辑增删改查 | `request` 通用入口 | ⚠️ 未实现，需先抓编辑器请求核对契约 |

写操作先写 JSON 再预览，确认无误后才加 `--apply`；新增接口用 `request` 发送已经核对过的真实请求，不猜字段。

## 凭据安全

- `.state/`、`exports/`、`*.har`、`session*.json` 均已忽略，**永不入库**；
- `check_no_secrets.py` 扫描全部跟踪文件，命中 `sessionid=`、Cookie 请求头、XSRF 令牌即退出 1；它只报告文件与行号，**不打印命中的值**；
- `.githooks/pre-commit` 与 `pre-push` 调用同一脚本（克隆后执行一次 `git config core.hooksPath .githooks` 启用）；
- 凭据若曾进入聊天、群或云端位置，应视为已泄露，用完到站点退出登录使其失效；
- 导出的答卷文件含填写者个人信息，请自行妥善保管。

## 已知边界

- 列表接口的 `rspd_count` 可能滞后，回收数请以 `project.count` 为准；
- 「前端能导出」不等于「CLI 能触发导出」——两条路径参数不同；
- 匿名探测固定返回 `AUTH_REQUIRED`（业务码 `10001`），这验证的是认证边界，不能当作登录成功。

离线检查：`python3 -m unittest test_thuwj.py test_import_session.py`（41 项）。
接口依据与逐条实测记录见 [research.json](research.json)，操作清单与默认参数见 [contracts.json](contracts.json)。

# API / CLI 访问控制方案（0.2.0）

## 通道与部署

桌面界面和 CLI 共用本机回环服务。CLI 不提供远程主机参数：软件运行时转交本机进程，未运行时取得同一单实例锁后操作指定数据目录。`serve` 也只允许回环地址。不要把本机服务转发到公网。

远程 API 使用第二个独立监听器，默认关闭。进入「设置 → API / CLI」配置 IP、端口和证书，保存后立即启动或停止，无需重启。默认端口 19527、协议 HTTPS；`0.0.0.0` 监听全部 IPv4 网卡，客户端使用电脑实际 IP。软件退出后 API 一同停止。启动失败显示状态并回退旧监听配置，不影响本机任务功能。

HTTPS 使用 PEM 证书和无密码 PEM 私钥，证书须覆盖客户端访问的域名/IP。路径可为绝对路径，相对路径按数据目录解析。私钥文件的读取权限由操作系统管理。HTTP 仅供可信局域网或已有加密隧道的环境显式选择；互联网连接使用 HTTPS。软件不自动开放防火墙端口，也不自动申请证书。

## Token 与权限

远程端口上的每个请求，包括来自本机的请求，都要求 `Authorization: Bearer TOKEN`。不接收 URL 查询参数或 Cookie 中的密钥。静态桌面界面和 CLI 不在远程端口开放，不开放跨域浏览器访问。

创建 Token 时设置名称、过期时间（默认 30 天，也可明确选择永不过期）、权限类型和接口范围。原文只在创建后展示一次，请立即复制保存；列表、日志及再次编辑都不返回原文。可随时修改授权或撤销，撤销不可恢复，需要重新创建密钥。

权限类型与接口白名单必须同时满足，权限相互独立：

| 类型 | 含义 | 例子 |
|---|---|---|
| `read` | 查询、预览 | `task.list`、`task.get` |
| `create` | 新增/写入 | `task.create` |
| `update` | 修改现有内容 | `task.update`、完成、移动 |
| `delete` | 删除 | `task.delete` |
| `execute` | 执行同步、提醒等动作 | `sync.run`、`alert.tick` |

`write` 是 CLI/API 创建授权时 `create` 的兼容别名，不隐含修改或删除。默认只授予 `read`。接口范围可选全部可远程业务接口，或勾选具体接口；搜索与分页用于快速定位。

复合操作要求全部相关权限：导入需要新增、修改、删除；同步需要新增、修改、删除、执行；备份恢复需要五类权限。`task.quick_add` 预览只要求读取，实际创建要求新增。确切要求以设置页接口清单或 `access.catalog` 为准；未知操作默认拒绝。

访问配置、Token 管理、审计查询、云端凭据管理、自启动与本机实例信息只允许本机管理，即使远程 Token 授予全部权限也不能调用。Token 不绕过业务自身的登录要求。

## 存储与防护

Token 使用 256 位随机密钥，仅保存 SHA-256 摘要。独立的 `<数据目录>/access.sqlite` 存放配置、授权和审计，不进入任务数据备份；恢复旧任务备份不会恢复已撤销密钥。迁移或备份访问配置时，应在软件退出后单独保管此文件；遗失后需重新配置并发放密钥。

每次业务执行前重新校验到期、撤销及最新授权，上传过程中撤销也不能继续写入。两个监听器的业务调用共用进程内操作锁，避免并发部分修改覆盖。已开始执行的操作不会强行中断。

默认按实际 TCP 来源 IP 限制每分钟 120 次请求，请求体最多 1024 KB，可在设置中调整。忽略代理来源头；反向代理部署时限流以代理的实际连接 IP 计算。日志最多保留最近 1000 条，只记录时间、调用方、IP、接口、方法和状态码，不记录请求体、Authorization 或 Token 原文。远程内部异常隐藏服务器细节。

这些选择遵循 [OWASP REST Security Cheat Sheet](https://cheatsheetseries.owasp.org/cheatsheets/REST_Security_Cheat_Sheet.html) 关于 HTTPS、请求头凭据、逐接口授权和请求限制的建议。

## 使用示例

本机 PowerShell（在软件目录执行，可加 `--data-dir D:\your-data`）：

```powershell
.\QingdanCLI.exe task list
.\QingdanCLI.exe access catalog
.\QingdanCLI.exe access token_create --name Reader --permissions '["read"]' --features '["task.list","task.get"]'
.\QingdanCLI.exe access token_revoke --token-id TOKEN_ID
```

远程客户端使用设置页创建的密钥：

```powershell
$headers = @{ Authorization = "Bearer $env:QINGDAN_TOKEN" }
Invoke-RestMethod -Uri 'https://your-host:19527/api/v1/tasks?limit=5&offset=0' -Headers $headers
Invoke-RestMethod -Uri 'https://your-host:19527/api/v1/meta/features' -Headers $headers
```

自签名证书需让客户端信任签发证书，例如 curl 使用 `--cacert server-cert.pem`，不要跳过证书校验。接口清单只返回当前密钥可调用的接口。响应保持现有 `{ok,data,trace_id}` / `{ok:false,error,trace_id}` 格式；未认证或密钥失效返回 401、无权限返回 403、超限返回 429、请求体过大返回 413。

## 验收

自动化测试覆盖 HTTPS 真实连接、启停与端口冲突、独立 CRUD/执行权限、接口白名单、复合操作、过期和撤销、上传中撤销、只存摘要、恢复不复活 Token、远程禁止管理、本机拒绝远程来源、限流与请求体上限、多线程修改、可选访问数据库损坏不阻塞本机任务。发布包另用隔离数据执行 CLI、HTTP 与远程权限冒烟验证。

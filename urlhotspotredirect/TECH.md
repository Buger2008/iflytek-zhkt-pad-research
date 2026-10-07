# 热点响应替换：把 `27.223.9.10:3000` 换成 NB 超链接

连到本机热点的**任何设备**打开 `http://27.223.9.10:3000/gx-lesson/#` 时，拿到的不是真实
服务器内容，而是这一行：

```html
<a href="http://www.nobook.com">打开NB虚拟实验室</a>
```

- **设备端零配置**：不装证书、不配代理、不改 DNS、不装 App。
- **不修改本机网卡配置**：不改 IP、不改路由、不动热点（上一版挂 IP 会把热点搞坏，已废弃）。
- **设备不需要能访问公网**：被拦下的请求根本不会离开这台 PC。
- **对全部热点设备生效**：默认不做来源 IP 限定，设备数量不限。

---

## 快速开始

### 1. 启动（需要管理员，会弹 UAC，点「是」）

在本目录下打开 PowerShell：

```powershell
Start-Process pwsh -Verb RunAs -ArgumentList '-NoExit','-NoProfile','-ExecutionPolicy','Bypass','-File',"$PWD\run-elevated.ps1"
```

看到这样的输出就算成功了：

```
生效范围  全部热点设备（不做来源限制）
INJECT    handle OK   false
OUT       handle OK   outbound and tcp and ip.SrcAddr == 192.168.137.1 and tcp.SrcPort == 3000
FWD       handle OK   tcp and ip.DstAddr == 27.223.9.10 and tcp.DstPort == 3000 and (...)
ready.  3 个句柄，Ctrl+C 停止。
```

### 2. 在设备上验证

用手机（或任何连到本机热点的设备）打开 `http://27.223.9.10:3000/gx-lesson/`，
应当看到一行「打开NB虚拟实验室」，点它跳转到 `www.nobook.com`。

同时启动窗口里会实时打印：

```
HTTP  <- 192.168.137.24:35914  GET /gx-lesson/  Host=27.223.9.10:3000  UA=...
```

### 3. 停止

在那个窗口按 `Ctrl+C`，或新开一个提权窗口执行：

```powershell
Start-Process pwsh -Verb RunAs -ArgumentList '-NoExit','-NoProfile','-ExecutionPolicy','Bypass','-File',"$PWD\stop.ps1"
```

> **重要**：服务跑在那个提权窗口里，**关掉窗口就等于停止服务**。重启电脑后也要重新启动。

---

## 常用场景（照抄即可）

| 我想…… | 命令 |
|---|---|
| 用默认设置启动 | `.\run-elevated.ps1` |
| 换目标地址 | `.\run-elevated.ps1 -TargetIp 1.2.3.4 -TargetPort 8080` |
| 换返回内容 | `.\run-elevated.ps1 -ExtraArgs '--body','helloworld'` |
| 只对某台设备生效 | `.\run-elevated.ps1 -ExtraArgs '--phone','192.168.137.24'` |
| 只对两台设备生效 | `.\run-elevated.ps1 -ExtraArgs '--phone','192.168.137.24,192.168.137.31'` |
| 恢复成对所有设备生效 | `.\run-elevated.ps1 -ExtraArgs '--phone','all'` |
| 排障：只看不改 | `.\run-elevated.ps1 -ExtraArgs '--observe','--verbose'` |
| 看每个包的细节 | `.\run-elevated.ps1 -ExtraArgs '--verbose'` |
| 假服务端口与目标不同 | `.\run-elevated.ps1 -TargetPort 3000 -ListenPort 8080` |
| 免管理员验证假服务本身 | `python hijack.py --serve-only --listen-ip 127.0.0.1` |
| 跑离线自测 | `python selftest.py` |

---

## 参数详解

### `hijack.py`（主程序，一般由 `run-elevated.ps1` 代跑）

```powershell
python hijack.py --help
```

| 参数 | 默认值 | 说明 |
|---|---|---|
| `--target-ip` | `27.223.9.10` | **设备要访问的目标 IP**。设备请求里的目的地址必须等于它才会被接管。这是**裸 IP**，和域名/DNS 无关。 |
| `--target-port` | `3000` | **目标端口**。回程改写时把源地址改回 `target-ip:target-port`，所以它决定设备"看到"的服务器身份。 |
| `--listen-ip` | `192.168.137.1` | **假服务的监听地址**，必须是本机已存在的地址。默认即热点网关，一般不用改。 |
| `--listen-port` | `3000` | **假服务端口**。可以与目标端口不同（例如目标 3000、本地监听 8080）。 |
| `--body` | `<a href="http://www.nobook.com">打开NB虚拟实验室</a>` | **替换后的响应体**。含 `<` 时按 `text/html; charset=utf-8` 返回，否则按 `text/plain; charset=utf-8`。 |
| `--phone` | 空 | **来源限定**，逗号分隔。**留空 / `all` / `any` / `*` 都表示对全部热点设备生效（默认）**。填具体 IP 则只接管这些设备，其它设备不受影响（返回 403）。 |
| `--observe` | 关 | **只嗅探观察**：不改写、不拦截，设备流量照常转发。用来先搞清楚"设备到底在发什么包"。 |
| `--serve-only` | 关 | **只起假 HTTP 服务**，不开 WinDivert 句柄——**不需要管理员**，用于自测/验证监听是否正常。 |
| `--verbose` | 关 | 打印**每一个包**（含 SYN/ACK/FIN 标志、序号、载荷长度），排障时开。 |

**要点**

- 目标必须写 **IP**，不能写域名。若设备访问的是域名，得在 DNS 层面拦（见 `backup/` 里的旧版）。
- `--phone` 里的 IP 是设备在**热点网段**里的地址（形如 `192.168.137.x`），设备重连后可能变化。
- 目标端口和监听端口相同时（默认），防火墙规则只需放行一个 3000。

**示例**

```powershell
# 1) 先摸底：只看设备发了什么，不做任何改动
python hijack.py --observe --verbose

# 2) 正式接管某台设备
python hijack.py --phone 192.168.137.24

# 3) 换目标为 1.2.3.4:8080，本地监听 9090
python hijack.py --target-ip 1.2.3.4 --target-port 8080 --listen-port 9090

# 4) 只用最简方式验证假服务（无需管理员）
python hijack.py --serve-only --listen-ip 127.0.0.1
#   另开窗口： curl.exe -s http://127.0.0.1:3000/anything
```

### `run-elevated.ps1`（提权启动器）

| 参数 | 默认值 | 说明 |
|---|---|---|
| `-TargetIp` | `27.223.9.10` | 传给 `--target-ip` |
| `-TargetPort` | `3000` | 传给 `--target-port` |
| `-ListenPort` | `0`（= 与 `-TargetPort` 相同） | 假服务监听端口；不同时会自动加 `--listen-port`，并按它建防火墙规则 |
| `-ExtraArgs` | 空 | 原样追加给 `hijack.py` 的额外参数，例如 `'--observe','--verbose'` |

它还负责：加入站防火墙规则 `hotspot-hijack-<监听端口>`（放行 `192.168.137.0/24`）、
杀掉上一个实例（按命令行匹配，不误伤其它 python 进程）。

### `stop.ps1`（提权停止）

| 参数 | 默认值 | 说明 |
|---|---|---|
| `-TargetPort` | `3000` | 要删除的防火墙规则对应端口 |

先按 `hijack.pid` 精确杀进程，找不到再按命令行兜底；最后删除防火墙规则
`hotspot-hijack-<端口>`（顺带清理上一版遗留的 `hotspot-hijack-8080`）。

---

## 日志怎么看

日志在 `hijack.log`（同时打印在启动窗口里）。

```
[19:59:52.653] 生效范围  全部热点设备（不做来源限制）        <- 本次的接管范围
[19:59:54.457]   FWD  192.168.137.24:35914 -> 27.223.9.10:3000 [S] seq=... capif=16
[19:59:54.459]   OUT  192.168.137.1:3000 -> 192.168.137.24:35914 [SA]
[19:59:54.529]   HTTP  <- 192.168.137.24:35914 GET /gx-lesson/ Host=27.223.9.10:3000 UA=...
[19:59:54.530]   OUT  192.168.137.1:3000 -> 192.168.137.24:35914 [AFP]
[20:00:02.699] heartbeat 1  alive=['out', 'fwd', 'hb']  fwd=10 injected=10 out=9
```

| 行 | 含义 |
|---|---|
| `FWD` | 拦到设备发往目标的包（这里看到的还是**改写前**的地址） |
| `INJ` | 改写目的地址后注入回协议栈（`--verbose` 才有） |
| `OUT` | 拦到本机假服务的回程包，并把源地址改回目标 |
| `HTTP` | 假 HTTP 服务真正收到请求（**这一行出现才说明设备拿到了内容**） |
| `heartbeat` | 每 10 秒一次的心跳与计数 |

标志位：`S`=SYN `A`=ACK `F`=FIN `R`=RST `P`=PSH（`[AFP]` = 响应数据包 + 结束）。

**计数含义**：`fwd` 拦到的包数、`injected` 注入的包数、`out` 回程改写数。

- `out=0` 且手机一直重传 `[S]` → 注入的包没被本地协议栈接收（历史上两个原因：包的
  `layer` 没改回 `NETWORK`、或包的 `interface` 没声明为热点网卡）。
- `HTTP` 行出现 → 链路闭合，设备已经拿到替换内容。

---

## 排障（按症状）

| 症状 | 排查 |
|---|---|
| 手机打开 URL 没反应，日志里连 `FWD` 都没有 | ① 设备是不是真连着本机热点？② 访问的是不是 `27.223.9.10:3000`？③ 先用 `--observe --verbose` 看有没有包 |
| 日志有 `FWD` 但一直重传 `[S]`，`out=0` | 注入未被本地协议栈接收。确认热点网卡地址仍是 `192.168.137.1`（重启热点后网卡索引会变，脚本自动探测，但地址一般不变） |
| 有 `HTTP` 行但手机上没显示 | 设备侧缓存/WebView 复用了旧响应，换路径或在设备上重启那个 App |
| 启动报「没有成功打开任何 WinDivert 句柄」 | 没提权。必须用 `run-elevated.ps1` 或管理员 PowerShell 运行 |
| 启动报「无法监听 192.168.137.1:3000」 | 热点没开，或该地址不属于本机。先开热点 |
| 服务莫名停了 | 那个提权窗口被关了；重启电脑后也不会自动恢复。重新执行第 1 步 |
| 想彻底恢复系统 | `stop.ps1`（删规则、杀进程）。本方案不改网卡配置，停止即完全恢复 |
| 怀疑上一版留下了附加 IP | 执行 `fix-hotspot.ps1`（摘 IP + 重启 ICS 服务） |
| 日志里冒出 `127.0.0.1:3000` 的实例 | 那是 `selftest.py` 起的自测子进程，正常，会自动退出 |
| 想知道服务是否真的在跑 | `python hijack.py` 的实例在 `hijack.pid` 里；也可看 `netstat -ano \| findstr :3000` 是否有 `192.168.137.1:3000` 监听 |

---

## 原理

两个 WinDivert 句柄配合，全程只改包、不动系统配置：

```
手机（192.168.137.x）          本机（热点网关 192.168.137.1）
 │  SYN -> 27.223.9.10:3000
 ├──────────────────────────►  ① NETWORK_FORWARD 层拦下
 │                                 目的改写为 192.168.137.1:3000
 │                                 layer 改回 NETWORK、标记 INBOUND 注入回协议栈
 │                                 原包不回送 => 不转发到公网
 │                             内核把它交付给本地假 HTTP 服务（监听 3000）
 │  ◄──────────────────────────  ② NETWORK 层拦本机回程包
 │     源地址改回 27.223.9.10:3000，四元组与设备发起时完全一致
```

`--observe` 只启用 ① 的**嗅探**版本（SNIFF 标志），不改写不拦截，用来先看清设备流量。

## 两个必须踩过才知道的坑

1. **注入前必须把 `packet.layer` 从 `NETWORK_FORWARD` 改回 `NETWORK`**。
   pydivert 的 `_populate_wd_addr()` 会把抓包时的 layer 写进注入地址
   （`address.Layer = self._layer`）。若仍是 `NETWORK_FORWARD`，`WinDivertSend` 会把它
   注回「转发」路径——表现为 `injected` 计数在涨、但 `out=0`、设备一直重传 SYN。

2. **注入前必须把 `packet.interface` 声明为热点网卡**。
   转发层抓到的包带的是出口网卡（实测 `capif=16` 是 WLAN）。注入成「从 WLAN 口收到一个
   源地址属热点网段的包」会被源地址校验丢弃。改成热点网卡索引后 `out` 立刻从 0 变正。

另有一条实测事实：**同一个转发包在 `NETWORK_FORWARD` 层会出现两次**——NAT 前
（`src=设备真实地址`）和 NAT 后（`src=本机上网网卡地址`）。只能改写 NAT 前那份，
所以过滤器里显式排除了所有本机地址（启动日志里那串 `ip.SrcAddr != ...` 就是这个用途）。

## 已验证（真机证据）

手机（`192.168.137.24`，App 内嵌 WebView：`AgentWeb/4.1.3 UCBrowser/11.6.4.950`）：

```
[19:59:54.457]   FWD  192.168.137.24:35914 -> 27.223.9.10:3000 [S]
[19:59:54.459]   OUT  192.168.137.1:3000 -> 192.168.137.24:35914 [SA]
[19:59:54.529]   HTTP  <- 192.168.137.24:35914  GET /gx-lesson/  Host=27.223.9.10:3000
                        UA=...AgentWeb/4.1.3 UCBrowser/11.6.4.950
[19:59:54.530]   OUT  192.168.137.1:3000 -> 192.168.137.24:35914 [AFP]
[19:59:54.694]   HTTP  <- 192.168.137.24:35922  GET /favicon.ico  ...
heartbeat 1  fwd=10 injected=10 out=9
```

离线自测 `python selftest.py` **18/18 通过**（含两个坑的过滤器回归校验、`--phone` 语义）。

## 文件

| 文件 | 作用 |
|---|---|
| `hijack.py` | 主程序：WinDivert 包级改写 + 本地假 HTTP 服务 |
| `run-elevated.ps1` | 提权启动：加防火墙规则 → 杀旧实例 → 前台运行 |
| `stop.ps1` | 提权停止：杀进程 + 删防火墙规则 |
| `selftest.py` | 离线自测（免管理员） |
| `fix-hotspot.ps1` | 应急恢复：清理历史遗留的附加 IP/规则并重启 ICS 服务 |
| `diag.py` | 早期 DNS 机制的 WinDivert 可见性探针（历史留存） |
| `hijack.log` | 运行日志（含心跳与计数） |
| `hijack.pid` | 当前实例 pid（**只有正式接管才写**；`--serve-only` 自测子进程不写，避免覆盖正牌实例） |
| `backup/` | 历史版本与归档脚本 |

## 历史与备份

git 仓库已初始化：`git log --oneline`

`backup/` 里的历史版本（都已被本版取代，保留仅供追溯）：

| 文件 | 说明 |
|---|---|
| `hijack_alias_ippmount.py` | **上一版**：把目标 IP 挂到本机网卡。**实测会把 Windows 移动热点搞坏**（开启几秒自动关闭、网卡销毁重建、ICS 状态错乱），已废弃并加了硬护栏 |
| `hijack_v1_full.py` | 更早的域名版（DNS 改写 + 80→8080 端口弹跳）。功能最全，由会话编辑记录逐段还原，并与 `hijack_OLD_bytecode.cpython-314.pyc` 逐函数比对：源码 20988 字节一致、573 行一致、字节码/常量/标识符差异 0 处、连函数起始行号都一致 |
| `hijack_simple_hardcoded.py` | 极简硬编码中间版 |
| `selftest_dns.py`、`dryrun_windivert.ps1`、`verify-alias_ippmount.ps1`、`run-elevated_alias.ps1` | 各历史版本的配套脚本 |

## 已知边界

- **只对 HTTP 有效**。HTTPS 需要设备信任伪造证书，做不到「零操作」。
- 目标必须是**裸 IP**；设备若访问域名，需要 DNS 层方案（见旧版）。
- 假服务对**任意路径**都返回同一内容，所以 URL 里的 `/gx-lesson/` 和 `#` 片段无关紧要。
- 真实服务器在本方案下完全不被访问（原包被丢弃）。
- 防火墙规则 `hotspot-hijack-<端口>` 会留在系统里，直到执行 `stop.ps1`。
- 服务不是常驻的：**关闭启动窗口或重启电脑即失效**，需重新执行启动命令。

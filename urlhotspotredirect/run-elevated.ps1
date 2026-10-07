#Requires -RunAsAdministrator
<#
  提权启动 hijack.py（WinDivert 包级改写：把设备对 目标IP:端口 的 HTTP 请求换成固定内容）。

  做三件事：
    1. 加入站防火墙规则，允许热点设备访问本机的监听端口；
    2. 杀掉上一个实例（按命令行匹配，不误伤其它 python 进程）；
    3. 在本窗口前台运行 hijack.py（它自己开 WinDivert 句柄，不修改任何网卡配置）。

  用法：
    .\run-elevated.ps1                                        # 默认 27.223.9.10:3000
    .\run-elevated.ps1 -TargetIp 1.2.3.4 -TargetPort 8080     # 换目标
    .\run-elevated.ps1 -ExtraArgs '--observe','--verbose'     # 只嗅探，排障用
    .\run-elevated.ps1 -ExtraArgs '--body','helloworld'       # 换响应内容
    .\run-elevated.ps1 -ExtraArgs '--phone','192.168.137.24'  # 只对某台设备生效
    .\run-elevated.ps1 -ListenPort 8080                       # 假服务监听端口与目标端口不同

  必须提权：WinDivert 需要管理员权限加载内核驱动。
  关掉这个窗口 = 服务停止。重启电脑后需重新执行。
#>
param(
    [string]$TargetIp = '27.223.9.10',
    [int]$TargetPort = 3000,
    [int]$ListenPort = 0,          # 0 = 与 TargetPort 相同
    [string[]]$ExtraArgs = @()
)
$ErrorActionPreference = 'Stop'

$here = Split-Path -Parent $MyInvocation.MyCommand.Path
$py   = 'D:\Program\Python3\python.exe'
if ($ListenPort -le 0) { $ListenPort = $TargetPort }
$rule = "hotspot-hijack-$ListenPort"

if (-not (Test-Path $py)) { throw "python not found: $py" }

# 清掉旧规则（包括上一版用过的 8080）
foreach ($r in @($rule, 'hotspot-hijack-8080')) {
    Get-NetFirewallRule -DisplayName $r -ErrorAction SilentlyContinue |
        Remove-NetFirewallRule -ErrorAction SilentlyContinue
}
New-NetFirewallRule -DisplayName $rule -Direction Inbound -Action Allow `
    -Protocol TCP -LocalPort $ListenPort -RemoteAddress 192.168.137.0/24 -Profile Any |
    Out-Null
Write-Host "firewall rule '$rule' added (inbound TCP $ListenPort from 192.168.137.0/24)" -ForegroundColor Green

# 按命令行匹配杀掉上一个实例，不会误伤其它 python 进程
Get-CimInstance Win32_Process -Filter "Name = 'python.exe'" -ErrorAction SilentlyContinue |
    Where-Object { $_.CommandLine -like '*hijack.py*' } |
    ForEach-Object {
        Write-Host "stopping previous hijack instance pid=$($_.ProcessId)" -ForegroundColor Yellow
        Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue
    }
Start-Sleep -Milliseconds 800

Write-Host ''
Write-Host "starting hijack.py  target=$TargetIp`:$TargetPort  listen=$ListenPort  (Ctrl+C to stop)" -ForegroundColor Cyan
Write-Host ''

$argv = @('--target-ip', $TargetIp, '--target-port', "$TargetPort")
if ($ListenPort -ne $TargetPort) { $argv += @('--listen-port', "$ListenPort") }
if ($ExtraArgs.Count) { $argv += $ExtraArgs }
Write-Host "args: $($argv -join ' ')" -ForegroundColor DarkGray

& $py (Join-Path $here 'hijack.py') @argv

Write-Host ''
Write-Host 'hijack.py exited.  Run stop.ps1 (elevated) to remove the firewall rule.' -ForegroundColor Yellow

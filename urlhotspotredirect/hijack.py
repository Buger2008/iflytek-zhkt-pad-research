#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
热点响应替换（WinDivert 版）—— **不修改任何网卡配置**。

设备打开 http://27.223.9.10:3000/gx-lesson/# 时，返回一行指向 NB 虚拟实验室的超链接。
设备不需要真的能访问公网：请求根本不会离开这台 PC。

为什么弃用上一版"本地挂载目标 IP"
--------------------------------
实测把附加 IP 挂到 Windows 移动热点的 Wi-Fi Direct 网卡上，会让热点**开启几秒后自动
关闭**、网卡被销毁重建（网卡索引从 16 漂到 17）、ICS 状态被搞乱，使用者一度无法上网。
本版改成纯包级改写，一个字节都不动网卡配置。

原理（两个句柄配合）
--------------------
  1) NETWORK_FORWARD 层拦"设备 -> 27.223.9.10:3000"的 TCP 包：
       把目的地址改写为 192.168.137.1:3000（本机监听），标记为 INBOUND 后注入协议栈，
       内核便把它当作"收到的包"交付给本地假 HTTP 服务；
       原包**不回送**，于是它不会被转发到公网（设备也不需要公网）。
  2) NETWORK 层拦本机 192.168.137.1:3000 发出的回程包：
       把源地址改回 27.223.9.10:3000，设备看到的四元组与它自己发起时完全一致。

必须以管理员运行（WinDivert 要装/起内核驱动）。

先跑 --observe 摸底（只嗅探、不改写、不拦截），确认：
  * 设备发往 27.223.9.10:3000 的包在 NETWORK_FORWARD 层看不看得见；
  * 看到的源地址是设备的真实地址，还是已被 ICS NAT 过的地址。
确认无误再去掉 --observe 正式接管。
"""

from __future__ import annotations

import argparse
import os
import subprocess
import threading
import time
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

try:
    import pydivert
    from pydivert import Direction, Flag, Layer
except ImportError:
    raise SystemExit("需要 pydivert:  python -m pip install pydivert")

# --------------------------------------------------------------------------
# 默认配置（全部可用命令行参数覆盖）
# --------------------------------------------------------------------------

TARGET_IP = "27.223.9.10"       # 设备要访问的目标 IP
TARGET_PORT = 3000              # 目标端口
LISTEN_IP = "192.168.137.1"     # 本机热点网关地址（假服务监听在这里）
LISTEN_PORT = 3000              # 假服务端口
BODY = '<a href="http://www.nobook.com">打开NB虚拟实验室</a>'
PHONE = ""                      # 来源限定，逗号分隔；空 = 不限制

HERE = os.path.dirname(os.path.abspath(__file__))
LOG_PATH = os.path.join(HERE, "hijack.log")
PID_PATH = os.path.join(HERE, "hijack.pid")


# --------------------------------------------------------------------------
# 日志
# --------------------------------------------------------------------------

_lock = threading.Lock()
_logfile = open(LOG_PATH, "a", encoding="utf-8", buffering=1)


def log(msg: str) -> None:
    line = f"[{datetime.now().strftime('%H:%M:%S.%f')[:-3]}] {msg}"
    with _lock:
        try:
            print(line, flush=True)
        except OSError:
            pass
        _logfile.write(line + "\n")


# --------------------------------------------------------------------------
# 本机地址探测
#
# 实测：ICS 的 NAT 让同一个转发包在 NETWORK_FORWARD 层**出现两次**——
#   一次是 NAT 前（src = 设备真实地址，例如 192.168.137.24）
#   一次是 NAT 后（src = 本机 WLAN 地址，例如 192.168.0.106）
# 只能改写 NAT 前那一份；改写 NAT 后那份会把伪造连接塞给本机自己。
# 所以过滤器里显式排除所有本机地址。
# --------------------------------------------------------------------------


def _ps(script: str) -> str:
    try:
        p = subprocess.run(
            ["powershell", "-NoProfile", "-NonInteractive", "-Command", script],
            capture_output=True, text=True, errors="replace", timeout=20,
        )
        return f"{p.stdout or ''}{p.stderr or ''}".strip()
    except Exception as exc:
        log(f"PowerShell 调用失败: {type(exc).__name__}: {exc}")
        return ""


def local_ipv4() -> list[str]:
    out = _ps("Get-NetIPAddress -AddressFamily IPv4 | ForEach-Object { $_.IPAddress }")
    return [x.strip() for x in out.splitlines() if x.strip()]


def iface_index_of(ip: str) -> int:
    out = _ps(
        f"(Get-NetIPAddress -AddressFamily IPv4 -IPAddress '{ip}' "
        f"-ErrorAction SilentlyContinue | Select-Object -First 1).InterfaceIndex"
    )
    try:
        return int(out.strip())
    except Exception:
        return 0


def parse_clients(value: str) -> list[str]:
    """解析 --phone。

    留空、或 all / any / * ——都表示**不限制来源**，即对连到热点的全部设备生效（默认）。
    只有显式列出具体 IP 时才做限定；并且先把 all/any/* 摘掉，避免被当成一个叫 "all"
    的 IP 拼进过滤器（那会生成非法过滤器，句柄直接打不开）。
    """
    out: list[str] = []
    for tok in (value or "").split(","):
        t = tok.strip()
        if not t:
            continue
        if t.lower() in ("all", "any", "*"):
            return []
        out.append(t)
    return out


# --------------------------------------------------------------------------
# 假 HTTP 服务
# --------------------------------------------------------------------------


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    server_version = "nb"
    sys_version = ""

    def log_message(self, *args):  # 屏蔽 http.server 自带噪音
        pass

    def _reply(self, with_body: bool = True) -> None:
        srv = self.server
        who = f"{self.client_address[0]}:{self.client_address[1]}"
        if srv.allowed and self.client_address[0] not in srv.allowed:
            log(f"  HTTP  <- {who}  {self.command} {self.path}  -> 403 (不在允许列表)")
            self.send_response(403)
            self.send_header("Content-Length", "0")
            self.send_header("Connection", "close")
            self.end_headers()
            return
        log(
            f"  HTTP  <- {who}  {self.command} {self.path}  "
            f"Host={self.headers.get('Host', '-')}  UA={self.headers.get('User-Agent', '-')}"
        )
        self.send_response(200)
        self.send_header("Content-Type", srv.ctype)
        self.send_header("Content-Length", str(len(srv.body)))
        self.send_header("Connection", "close")
        self.end_headers()
        if with_body:
            try:
                self.wfile.write(srv.body)
            except OSError:
                pass

    def do_GET(self):
        self._reply()

    def do_POST(self):
        self._reply()

    def do_PUT(self):
        self._reply()

    def do_DELETE(self):
        self._reply()

    def do_OPTIONS(self):
        self._reply()

    def do_HEAD(self):
        self._reply(with_body=False)


class Server(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True

    def __init__(self, addr, body: bytes, allowed: set[str]):
        self.body = body
        self.allowed = allowed
        self.ctype = "text/html; charset=utf-8" if body[:1] == b"<" else "text/plain; charset=utf-8"
        super().__init__(addr, Handler)


# --------------------------------------------------------------------------
# 主逻辑
# --------------------------------------------------------------------------


def tcp_flags(t) -> str:
    out = []
    for name, ch in (("syn", "S"), ("ack", "A"), ("fin", "F"), ("rst", "R"), ("psh", "P")):
        try:
            if getattr(t, name):
                out.append(ch)
        except Exception:
            pass
    return "".join(out) or "-"


class Hijack:
    def __init__(self, args, body: bytes):
        self.args = args
        self.body = body
        self.sources = parse_clients(args.phone)   # 空 = 对全部热点设备生效
        if args.serve_only:
            # 只起假服务时不需要探本机地址/网卡；跳过两次 PowerShell 调用，
            # 否则启动要好几秒（自测就曾因此等不到监听而连接被拒）
            self.locals, self.ifidx = [], 0
        else:
            self.locals = local_ipv4()                    # 本机地址，用于排除 NAT 后的副本
            self.ifidx = iface_index_of(args.listen_ip)   # 注入时要声明的接入接口
        self.threads: list[threading.Thread] = []
        self.injector = None
        self.n = {"fwd": 0, "inj": 0, "out": 0, "http": 0}

    # -- 过滤器 ------------------------------------------------------------

    def _src(self) -> str:
        return " or ".join(f"ip.SrcAddr == {ip}" for ip in self.sources)

    def _dst(self) -> str:
        return " or ".join(f"ip.DstAddr == {ip}" for ip in self.sources)

    def f_fwd(self) -> str:
        a = self.args
        f = f"tcp and ip.DstAddr == {a.target_ip} and tcp.DstPort == {a.target_port}"
        if self.sources:
            f += f" and ({self._src()})"
        # 排除 NAT 后的副本：那份额源地址是本机地址（实测会同时出现两份）
        if self.locals:
            f += " and (" + " and ".join(f"ip.SrcAddr != {ip}" for ip in self.locals) + ")"
        return f

    def f_out(self) -> str:
        f = (
            f"outbound and tcp and ip.SrcAddr == {self.args.listen_ip} "
            f"and tcp.SrcPort == {self.args.listen_port}"
        )
        return f + (f" and ({self._dst()})" if self.sources else "")

    # -- 包处理 ------------------------------------------------------------

    def on_fwd(self, p) -> None:
        t = p.tcp
        self.n["fwd"] += 1
        if self.args.verbose or t.syn or t.fin or t.rst:
            log(
                f"  FWD  {p.src_addr}:{p.src_port} -> {p.dst_addr}:{p.dst_port} "
                f"[{tcp_flags(t)}] seq={getattr(t, 'seq_num', '?')} "
                f"ack={getattr(t, 'ack_num', '?')} paylen={len(p.payload or b'')} "
                f"capif={p.interface[0] if p.interface else '?'}"
            )
        if self.args.observe:
            return  # 只观察：原包照常转发（不会被我们改写）

        # 防御：万一 NAT 后的副本仍被放进来（源地址是本机地址），绝不改写，直接放行
        if p.src_addr in self.locals:
            log(f"  FWD  ! 源地址是本机地址（{p.src_addr}），判为 NAT 后副本，放行不改写")
            p.layer = Layer.NETWORK
            p.direction = Direction.OUTBOUND
            self.injector.send(p)
            return

        # 改写目的为本地假服务，注入回协议栈；原包不回送 => 不转发到公网
        #
        # 关键：必须把 layer 从 NETWORK_FORWARD 改回 NETWORK。
        # pydivert 的 _populate_wd_addr 会把抓包时的 layer 写进注入地址
        # （address.Layer = self._layer），若仍是 NETWORK_FORWARD，WinDivertSend
        # 会把它注回"转发"路径 —— 结果是既不本地交付、也不发回程，
        # 表现为 injected 计数在涨但 out=0、设备一直重传 SYN。
        p.dst_addr = self.args.listen_ip
        p.dst_port = self.args.listen_port
        p.layer = Layer.NETWORK
        p.direction = Direction.INBOUND
        # 转发层抓到的包带的是出口网卡（WLAN），注入成"从 WLAN 口收到一个源地址
        # 属热点网段的包"会被源地址校验丢弃 —— 声明确实是从热点网卡接入的。
        cap_if = p.interface[0] if p.interface else 0
        if self.ifidx and cap_if != self.ifidx:
            p.interface = (self.ifidx, 0)
        self.injector.send(p)
        self.n["inj"] += 1
        if self.args.verbose:
            log(
                f"  INJ  {p.src_addr}:{p.src_port} -> {p.dst_addr}:{p.dst_port} "
                f"[{tcp_flags(t)}]  layer=NETWORK direction=INBOUND "
                f"ifidx={cap_if}->{self.ifidx}"
            )

    def on_out(self, p) -> None:
        t = p.tcp
        self.n["out"] += 1
        if self.args.verbose or t.syn or t.fin or t.rst:
            log(
                f"  OUT  {p.src_addr}:{p.src_port} -> {p.dst_addr}:{p.dst_port} "
                f"[{tcp_flags(t)}]"
            )
        if self.args.observe:
            return
        # 源改回设备以为自己在连的地址
        p.src_addr = self.args.target_ip
        p.src_port = self.args.target_port

    # -- 线程与句柄 --------------------------------------------------------

    def _open(self, label: str, filt: str, **kw):
        """串行打开句柄——并发首次打开会撞驱动加载竞态（WinError 1058）。"""
        try:
            w = pydivert.WinDivert(filt, **kw)
            w.open()
        except Exception as exc:
            log(f"!! {label} 句柄打开失败: {type(exc).__name__}: {exc}")
            return None
        log(f"{label:<9} handle OK   {filt}")
        return w

    def _pump(self, name: str, w, handler, send_back: bool) -> None:
        for p in w:
            try:
                handler(p)
            except Exception as exc:
                log(f"  {name} ! {type(exc).__name__}: {exc}")
            if send_back:
                w.send(p)

    def _spawn(self, name: str, w, handler, send_back: bool) -> None:
        def run():
            try:
                self._pump(name, w, handler, send_back)
            except Exception as exc:
                log(f"!! {name} 线程退出: {type(exc).__name__}: {exc}")

        t = threading.Thread(target=run, name=name, daemon=True)
        t.start()
        self.threads.append(t)

    def heartbeat(self) -> None:
        i = 0
        while True:
            time.sleep(10)
            i += 1
            alive = [t.name for t in self.threads if t.is_alive()]
            log(
                f"heartbeat {i}  alive={alive}  "
                f"fwd={self.n['fwd']} injected={self.n['inj']} out={self.n['out']}"
            )

    # -- 启动 --------------------------------------------------------------

    def start(self) -> bool:
        a = self.args

        # 1) 本地假 HTTP 服务
        try:
            srv = Server((a.listen_ip, a.listen_port), self.body, set(self.sources))
        except OSError as exc:
            log(f"!! 无法监听 {a.listen_ip}:{a.listen_port} -> {exc}")
            log("   热点没开？该地址必须存在于本机（热点网关）。")
            return False
        threading.Thread(target=srv.serve_forever, name="http", daemon=True).start()
        log(f"HTTP      http://{a.listen_ip}:{a.listen_port}/  ->  {self.body.decode('utf-8')}")
        if self.sources:
            log(f"生效范围  仅限: {', '.join(self.sources)}")
        else:
            log("生效范围  全部热点设备（不做来源限制）")

        opened = 0
        if a.serve_only:
            log("== --serve-only：只起假 HTTP 服务，不开 WinDivert 句柄（自测用）==")
            opened = 1
        else:
            if a.observe:
                log("== 观察模式：只嗅探，不改写、不拦截，设备流量照常转发 ==")
                hn = self._open("NET-SNIFF", self.f_fwd(), layer=Layer.NETWORK, flags=Flag.SNIFF)
                if hn:
                    opened += 1
                    self._spawn("net-sniff", hn, self.on_fwd, False)
            else:
                self.injector = self._open("INJECT", "false", layer=Layer.NETWORK)
                if self.injector:
                    opened += 1
                ho = self._open("OUT", self.f_out(), layer=Layer.NETWORK)
                if ho:
                    opened += 1
                    self._spawn("out", ho, self.on_out, True)

            hf = self._open(
                "FWD",
                self.f_fwd(),
                layer=Layer.NETWORK_FORWARD,
                flags=Flag.SNIFF if a.observe else Flag.DEFAULT,
            )
            if hf:
                opened += 1
                # 正式模式下 send_back=False => 原包被丢弃，不会转发到公网
                self._spawn("fwd", hf, self.on_fwd, False)

        if not opened:
            log("!! 没有成功打开任何 WinDivert 句柄——请确认以管理员身份运行。")
            srv.shutdown()
            return False

        t = threading.Thread(target=self.heartbeat, name="hb", daemon=True)
        t.start()
        self.threads.append(t)

        log(f"ready.  {opened} 个句柄，Ctrl+C 停止。")
        try:
            while True:
                time.sleep(1)
        except KeyboardInterrupt:
            log("stopping (Ctrl+C)")
        finally:
            log(
                f"shutting down  fwd={self.n['fwd']} injected={self.n['inj']} "
                f"out={self.n['out']}"
            )
            srv.shutdown()
        return True


# --------------------------------------------------------------------------
# 入口
# --------------------------------------------------------------------------


def main() -> int:
    ap = argparse.ArgumentParser(
        description="把设备对 目标IP:端口 的 HTTP 请求替换成固定内容（WinDivert 包级改写，不动网卡配置）",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "示例:\n"
            "  python hijack.py --observe     # 先摸底：只嗅探，看设备流量长什么样\n"
            "  python hijack.py               # 正式接管\n"
            "  python hijack.py --verbose     # 打印每个包\n"
        ),
    )
    ap.add_argument("--target-ip", default=TARGET_IP, help=f"设备要访问的目标 IP（默认 {TARGET_IP}）")
    ap.add_argument("--target-port", type=int, default=TARGET_PORT, help=f"目标端口（默认 {TARGET_PORT}）")
    ap.add_argument("--listen-ip", default=LISTEN_IP, help=f"假服务监听地址（默认 {LISTEN_IP}，热点网关）")
    ap.add_argument("--listen-port", type=int, default=LISTEN_PORT, help=f"假服务端口（默认 {LISTEN_PORT}）")
    ap.add_argument("--body", default=BODY, help="替换后的响应体")
    ap.add_argument("--phone", default=PHONE,
                    help="来源限定，逗号分隔；留空 / all / any = 对全部热点设备生效（默认）")
    ap.add_argument("--observe", action="store_true", help="只嗅探观察，不改写不拦截")
    ap.add_argument("--serve-only", action="store_true",
                    help="只起假 HTTP 服务，不开 WinDivert 句柄（自测用，不需要管理员）")
    ap.add_argument("--verbose", action="store_true", help="打印每一个包")
    args = ap.parse_args()

    body = args.body.encode("utf-8")
    # 只有正式接管才写 pid 文件。--serve-only 是自测用的临时子进程，
    # 若也写 pid 就会覆盖正在运行的正牌实例，导致 stop.ps1 精确杀进程时找错对象。
    owns_pidfile = not args.serve_only
    if owns_pidfile:
        with open(PID_PATH, "w", encoding="ascii") as fh:
            fh.write(str(os.getpid()))

    log("=" * 72)
    log(
        f"start  pid={os.getpid()}  mode={'OBSERVE' if args.observe else 'ACTIVE'}  "
        f"{args.target_ip}:{args.target_port} -> {args.listen_ip}:{args.listen_port}"
    )
    log(f"       body: {args.body}")

    ok = False
    try:
        ok = Hijack(args, body).start()
    finally:
        log("exited")
        if owns_pidfile:
            try:
                os.remove(PID_PATH)
            except OSError:
                pass
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())

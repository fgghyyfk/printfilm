# -*- coding: utf-8 -*-
"""媒体下载的 SSRF 防护：拒绝解析到内网、回环、链路本地的地址。

通过 httpx 的 request 事件钩子接入，每一跳重定向都会重新校验。
校验与实际连接之间各解析一次 DNS，DNS rebinding 不在本层防护范围内。
"""
from __future__ import annotations

import asyncio
import ipaddress
import socket

import httpx

from app.config import get_settings

BLOCKED_MESSAGE = "媒体地址指向内网或本机，已拒绝下载"
_RESOLVE_TIMEOUT_SEC = 10.0
# 代理软件 fake-ip 模式把公网域名解析到此网段，流量仍由代理发往公网，放行以免误伤上游结果下载。
_FAKE_IP_NETWORK = ipaddress.ip_network("198.18.0.0/15")


class UnsafeMediaUrlError(ValueError):
    """下载地址不允许访问；继承 ValueError，业务入口按 400 返回中文提示。"""


def _is_blocked_ip(raw: str) -> bool:
    """判断 IP 是否为非公网地址；IPv4 映射的 IPv6 按其 IPv4 判断，无法解析的一律拒绝。"""
    try:
        ip = ipaddress.ip_address(raw.split("%", 1)[0])
    except ValueError:
        return True
    if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped is not None:
        ip = ip.ipv4_mapped
    if isinstance(ip, ipaddress.IPv4Address) and ip in _FAKE_IP_NETWORK:
        return False
    return not ip.is_global or ip.is_multicast or ip.is_reserved


def _is_trusted_host(url: httpx.URL) -> bool:
    """本站、配置的 OSS 公网域名与 TokenFree 由配置决定，不做 IP 校验（本地开发的本站地址是回环）。"""
    from app.services import oss
    from app.services.tokenfree_gateway import TOKENFREE_BASE_URL

    settings = get_settings()
    site = httpx.URL(str(settings.public_base_url or "").strip() or "http://invalid.invalid")
    if (url.host, url.port) == (site.host, site.port):
        return True
    if url.host == httpx.URL(TOKENFREE_BASE_URL).host:
        return True
    if str(settings.oss_public_base or "").strip() or settings.oss_bucket:
        return url.host == httpx.URL(oss.public_base()).host
    return False


def _literal_ip(host: str) -> str | None:
    """host 本身是 IP 时返回它，域名返回 None。"""
    try:
        ipaddress.ip_address(host)
    except ValueError:
        return None
    return host


async def _resolve_ips(host: str, port: int) -> list[str]:
    """异步解析域名的全部地址。"""
    infos = await asyncio.wait_for(
        asyncio.get_running_loop().getaddrinfo(host, port, type=socket.SOCK_STREAM),
        timeout=_RESOLVE_TIMEOUT_SEC,
    )
    return [str(info[4][0]) for info in infos]


def _resolve_ips_sync(host: str, port: int) -> list[str]:
    """同步解析域名的全部地址。"""
    return [str(info[4][0]) for info in socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)]


def _reject_if_blocked(ips: list[str]) -> None:
    """任一解析结果落在非公网即拒绝；没有结果也拒绝。"""
    if not ips or any(_is_blocked_ip(ip) for ip in ips):
        raise UnsafeMediaUrlError(BLOCKED_MESSAGE)


def _port_of(url: httpx.URL) -> int:
    """取连接端口，缺省按协议补全。"""
    return url.port or (443 if url.scheme == "https" else 80)


async def guard_request(request: httpx.Request) -> None:
    """AsyncClient 的 request 钩子：地址不安全时抛 UnsafeMediaUrlError；解析失败按连接错误走原重试。"""
    url = request.url
    if _is_trusted_host(url):
        return
    literal = _literal_ip(url.host)
    if literal is not None:
        _reject_if_blocked([literal])
        return
    try:
        ips = await _resolve_ips(url.host, _port_of(url))
    except (OSError, asyncio.TimeoutError) as exc:
        raise httpx.ConnectError("无法解析媒体下载地址", request=request) from exc
    _reject_if_blocked(ips)


def guard_request_sync(request: httpx.Request) -> None:
    """同步 Client 的 request 钩子，语义同 guard_request。"""
    url = request.url
    if _is_trusted_host(url):
        return
    literal = _literal_ip(url.host)
    if literal is not None:
        _reject_if_blocked([literal])
        return
    try:
        ips = _resolve_ips_sync(url.host, _port_of(url))
    except OSError as exc:
        raise httpx.ConnectError("无法解析媒体下载地址", request=request) from exc
    _reject_if_blocked(ips)

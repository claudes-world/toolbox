import os
import socket

_original_getaddrinfo = socket.getaddrinfo


def _ipv4_only_getaddrinfo(host, port, family=0, type=0, proto=0, flags=0):
    return _original_getaddrinfo(host, port, socket.AF_INET, type, proto, flags)


def apply_ipv4_patch():
    """Force IPv4 for all socket connections. Gated on PULSE_FORCE_IPV4 env var (default on)."""
    if os.environ.get("PULSE_FORCE_IPV4", "1") != "0":
        socket.getaddrinfo = _ipv4_only_getaddrinfo

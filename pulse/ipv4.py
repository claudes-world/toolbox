import os
import socket

_original_getaddrinfo = socket.getaddrinfo


def _ipv4_only_getaddrinfo(host, port, family=0, type=0, proto=0, flags=0):
    return _original_getaddrinfo(host, port, socket.AF_INET, type, proto, flags)


def apply_ipv4_patch():
    """Force IPv4 for all socket connections. Gated on PULSE_FORCE_IPV4 env var (default on).

    Disable by setting PULSE_FORCE_IPV4 to any falsey value: 0, false, off, no, or empty string.
    Note: forces AF_INET regardless of caller's family arg — temporary VPS IPv6 workaround.
    """
    val = os.environ.get("PULSE_FORCE_IPV4", "1").strip().lower()
    if val not in ("0", "false", "off", "no", ""):
        socket.getaddrinfo = _ipv4_only_getaddrinfo

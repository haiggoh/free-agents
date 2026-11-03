#!/usr/bin/env python3
"""remote_http connects IPv4-first with a short per-address connect timeout (0.50.6).

Measured 2026-11-03 (waypoint free-agents-dispatch-a): generativelanguage.googleapis.com resolves to
8 IPv6 addresses ahead of 8 IPv4 ones, and a network that black-holes IPv6 left a dispatch in
SYN_SENT on IPv6 while curl answered in 4 s. Stock socket.create_connection tries each address in
turn with the FULL timeout, so 8 x 300 s passed before IPv4 was tried, and failover never got past
the first preference. These tests use fake sockets: nothing is sent anywhere.
"""
import socket
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "bin"))
import remote_http  # noqa: E402

V6 = [(socket.AF_INET6, socket.SOCK_STREAM, 6, "", (f"2001:db8::{i}", 443, 0, 0)) for i in range(3)]
V4 = [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (f"192.0.2.{i}", 443)) for i in range(2)]


class FakeSocket:
    log = []
    dead = set()            # addresses that "black-hole": connect times out

    def __init__(self, family, type_, proto):
        self.family, self.timeouts, self.closed = family, [], False

    def settimeout(self, t):
        self.timeouts.append(t)

    def setsockopt(self, *a):
        pass

    def connect(self, addr):
        FakeSocket.log.append((addr[0], self.timeouts[-1]))
        if addr[0] in FakeSocket.dead:
            raise socket.timeout("timed out")

    def close(self):
        self.closed = True


class ConnectOrder(unittest.TestCase):
    def setUp(self):
        FakeSocket.log, FakeSocket.dead = [], set()
        self.real = (remote_http.socket.getaddrinfo, remote_http.socket.socket)
        remote_http.socket.getaddrinfo = lambda *a, **k: V6 + V4     # DNS order: IPv6 first
        remote_http.socket.socket = FakeSocket

    def tearDown(self):
        remote_http.socket.getaddrinfo, remote_http.socket.socket = self.real

    def test_ipv4_is_tried_first_and_families_interleave(self):
        order = [a[4][0] for a in remote_http.ordered_addresses("h", 443)]
        self.assertEqual(order, ["192.0.2.0", "2001:db8::0", "192.0.2.1", "2001:db8::1", "2001:db8::2"])

    def test_black_holed_address_costs_only_the_connect_timeout(self):
        FakeSocket.dead = {"192.0.2.0", "2001:db8::0"}
        sock = remote_http.create_connection(("h", 443), timeout=300)
        self.assertEqual([a for a, _t in FakeSocket.log], ["192.0.2.0", "2001:db8::0", "192.0.2.1"])
        self.assertTrue(all(t == remote_http.CONNECT_TIMEOUT_S for _a, t in FakeSocket.log),
                        "each address gets the short connect timeout, never the 300 s read timeout")
        self.assertEqual(sock.timeouts[-1], 300, "after connecting, the socket uses the read timeout")

    def test_connect_timeout_never_exceeds_a_shorter_read_timeout(self):
        remote_http.create_connection(("h", 443), timeout=3)
        self.assertEqual(FakeSocket.log[0][1], 3)

    def test_every_address_dead_raises_the_last_error(self):
        FakeSocket.dead = {a[4][0] for a in V6 + V4}
        with self.assertRaises(OSError):
            remote_http.create_connection(("h", 443), timeout=300)
        self.assertEqual(len(FakeSocket.log), 5)

    def test_stream_chat_connections_use_it(self):
        self.assertIs(remote_http.new_connection("https", "h", 443, 60)._create_connection,
                      remote_http.create_connection)
        self.assertIs(remote_http.new_connection("http", "h", 80, 60)._create_connection,
                      remote_http.create_connection)


if __name__ == "__main__":
    unittest.main()

"""A fake Seyond sensor for the sensor-tool tests: the two channels of docker/sensor_tool.py on a
loopback address. Nothing here is a real sensor, and what a real one replies is not known for
`get_network` and `set_network` (docker/sensor_tool.py says so): the fake's replies there are
choices of this file, made so that every branch of the tool is run.

- TCP `command_port` (8002 on a sensor): one text command per connection, the reply ends with an
  empty line. `get_network`, `get_i_config time`, `get_status`, `set_i_config time <key> <value>`,
  `set_network` in both forms, `reboot 1`. Anything else: `Invalid command`.
- TCP `http_port` (8010 on a sensor): `GET /command/?get_sn`, `get_model`, `get_fw_version`,
  `get_udp_ports_ip`. A `set_` request is recorded and answered 200: the tests assert none came.

`received` is every command line, `requests` every HTTP path, `connections` and `max_open` count
the command connections. `apply` says what the fake does with an accepted `set_network`:
"live" (it answers at the new address at once), "restart" (it stores it, reports it in
get_network, and takes it at `reboot 1`), "never" (it acknowledges and keeps everything),
"vanish" (it answers nowhere afterwards).
"""

from __future__ import annotations

import socket
import threading
import time


def free_ports(ip: str = "127.0.0.1") -> tuple:
    """Two TCP port numbers that are free now."""
    found = []
    held = []
    for _ in range(2):
        sock = socket.socket()
        sock.bind((ip, 0))
        held.append(sock)
        found.append(sock.getsockname()[1])
    for sock in held:
        sock.close()
    return tuple(found)


class FakeSeyond:
    def __init__(self, ip: str = "127.0.61.1", *, ports: tuple | None = None, serial: str = "SEY-FAKE-0001", model: str = "w",
                 firmware: str = "fake-fw-1.2.3\nbuild 42", netmask: str = "255.0.0.0", gateway: str | None = None,
                 automotive: int | None = 1, stamping: int | None = 1, time_config: int = 0, network_reply: str = "labelled",
                 numbers: bool = True, strings: bool = True, apply: str = "live", takes_time: bool = True, reboots: bool = True,
                 udp: str = "8010,8010,8010,127.0.0.1", on_set_network=None) -> None:
        self.ip, self.serial, self.model, self.firmware = ip, serial, model, firmware
        self.command_port, self.http_port = ports or free_ports()
        self.live = {"ip": ip, "netmask": netmask, "gateway": gateway or "0.0.0.0"}
        self.stored = dict(self.live)
        self.time = {key: value for key, value in (("ptp_automotive", automotive), ("time_stamping", stamping)) if value is not None}
        self.time_config, self.network_reply, self.udp = time_config, network_reply, udp
        self.numbers, self.strings, self.apply, self.takes_time, self.reboots = numbers, strings, apply, takes_time, reboots
        self.on_set_network = on_set_network
        self.received: list = []
        self.requests: list = []
        self.connections = self.open_now = self.max_open = 0
        self._lock, self._moving = threading.Lock(), threading.Lock()
        self._stop = threading.Event()
        self._threads: list = []
        self._listeners: list = []

    # --- life ---------------------------------------------------------------------------------------

    def __enter__(self) -> "FakeSeyond":
        self._listen()
        return self

    def __exit__(self, *_exc) -> None:
        self.stop()

    def _listen(self) -> None:
        self._stop = threading.Event()
        for port, handler in ((self.command_port, self._command), (self.http_port, self._http)):
            listener = socket.socket()
            listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            listener.bind((self.ip, port))
            listener.listen(8)
            listener.settimeout(0.02)
            self._listeners.append(listener)
            thread = threading.Thread(target=self._accept, args=(listener, handler, self._stop), daemon=True)
            thread.start()
            self._threads.append(thread)

    def stop(self) -> None:
        self._stop.set()
        for thread in self._threads:
            if thread is not threading.current_thread():
                thread.join(2)
        for listener in self._listeners:
            listener.close()
        self._threads, self._listeners = [], []

    def _move(self) -> None:
        """Take the stored settings: answer at the stored address from now on."""
        with self._moving:
            if self.live == self.stored:
                return
            self.stop()
            self.live = dict(self.stored)
            self.ip = self.live["ip"]
            self._listen()

    def _later(self, action) -> None:
        threading.Thread(target=lambda: (time.sleep(0.03), action()), daemon=True).start()

    def _accept(self, listener: socket.socket, handler, stop: threading.Event) -> None:
        while not stop.is_set():
            try:
                connection, _peer = listener.accept()
            except socket.timeout:
                continue
            except OSError:
                return
            connection.settimeout(2)
            # A thread for each connection, so that two open at once are seen as two (max_open).
            threading.Thread(target=self._serve, args=(handler, connection), daemon=True).start()

    @staticmethod
    def _serve(handler, connection: socket.socket) -> None:
        try:
            handler(connection)
        except OSError:
            pass
        finally:
            connection.close()

    # --- TCP: the text commands ----------------------------------------------------------------------

    def _command(self, connection: socket.socket) -> None:
        with self._lock:
            self.connections += 1
            self.open_now += 1
            self.max_open = max(self.max_open, self.open_now)
        try:
            data = b""
            while not data.endswith(b"\n"):
                chunk = connection.recv(4096)
                if not chunk:
                    return
                data += chunk
            text = data.decode().strip()
            self.received.append(text)
            reply, after = self._reply(text)
            connection.sendall(reply.encode() + b"\n\n")
        finally:
            with self._lock:
                self.open_now -= 1
        if after is not None:
            self._later(after)

    def _reply(self, text: str) -> tuple:
        words = text.split()
        if text == "get_network":
            return self._network(), None
        if text == "get_i_config time":
            return "[time]\n" + "\n".join(f"{key} = {value}" for key, value in {**self.time, "ntp_en": 0}.items()), None
        if text == "get_status":
            return (f"time_config={self.time_config} up_time=1a2b err=0x0 stream_status=1 stream_count=0 "
                    "data_sent=0 count1=1 count2=0 count3=0"), None
        if words[:2] == ["set_i_config", "time"] and len(words) == 4 and words[2] in self.time:
            if self.takes_time:
                self.time[words[2]] = int(words[3])
            return "done", None
        if words[:1] == ["set_network"] and len(words) == 13 and self.numbers:
            numbers = words[1:]
            return self._set_network(*(".".join(numbers[i:i + 4]) for i in (0, 4, 8)))
        if words[:1] == ["set_network"] and len(words) == 4 and self.strings:
            return self._set_network(words[1], words[2], "0.0.0.0" if words[3] == "0" else words[3])
        if text == "reboot 1":
            return "reboot", (self._move if self.reboots and self.stored != self.live else None)
        return "Invalid command", None

    def _set_network(self, ip: str, netmask: str, gateway: str) -> tuple:
        if self.on_set_network is not None:
            self.on_set_network()
        if self.apply == "vanish":
            return "done", self.stop
        if self.apply == "never":
            return "done", None
        self.stored = {"ip": ip, "netmask": netmask, "gateway": gateway}
        return "done", (self._move if self.apply == "live" else None)

    def _network(self) -> str:
        net = self.stored
        return {
            "labelled": f"ip = {net['ip']}\nnetmask = {net['netmask']}\ngateway = {net['gateway']}",
            "labelled-other": f"IP Address: {net['ip']}, Subnet Mask: {net['netmask']}, Default GW: {net['gateway']}",
            "plain": f"{net['ip']} {net['netmask']} {net['gateway']}",
            "plain-two": f"network: {net['ip']} {net['netmask']}",
            "garbage": "network is up",
            "invalid": "Invalid command",
        }[self.network_reply]

    # --- HTTP ---------------------------------------------------------------------------------------

    def _http(self, connection: socket.socket) -> None:
        data = b""
        while b"\r\n\r\n" not in data:
            chunk = connection.recv(4096)
            if not chunk:
                return
            data += chunk
        path = data.split(b"\r\n", 1)[0].decode().split()[1]
        self.requests.append(path)
        body = {"/command/?get_sn": self.serial, "/command/?get_model": self.model, "/command/?get_fw_version": self.firmware,
                "/command/?get_udp_ports_ip": self.udp}.get(path)
        if body is None and path.startswith("/command/?set_"):
            body = ""
        status = "200 OK" if body is not None else "404 Not Found"
        payload = (body or "").encode()
        connection.sendall(f"HTTP/1.1 {status}\r\nContent-Type: text/html\r\nContent-Length: {len(payload)}\r\nConnection: close\r\n\r\n".encode() + payload)

    # --- for the tests --------------------------------------------------------------------------------

    def writes(self) -> list:
        """The commands received that change a sensor."""
        return [text for text in self.received if not text.startswith("get_")]

#!/usr/bin/python3 -I
"""sensor-tool for Seyond lidars: reads a sensor's own settings and sets them. Interface version 1.

    sensor-tool probe    --ip A [--model M] [--timeout S] [--driver-stopped]
    sensor-tool get-net  --ip A [--model M] [--timeout S] --driver-stopped
    sensor-tool set-net  --ip A --new-ip B --netmask M --gateway G [--no-reboot] [--dry-run] [--verify-timeout S] --driver-stopped
    sensor-tool set-dest --ip A --dest-ip H --data-port P [--dry-run] [--verify-timeout S] --driver-stopped
    sensor-tool set-ptp  --ip A --transport L2|UDPv4 --domain N --delay-mechanism E2E|P2P --timescale UTC|TAI
                         [--profile 1588v2] [--clock-source ptp] [--dry-run] [--verify-timeout S] --driver-stopped

It prints exactly one JSON object, also for a usage error, and exits 0 (reported, set, unchanged),
2 (changes: a dry run that would change the sensor), 1 (refused, failed, no-answer), 3 (unsupported)
or 5 (unverified: a change was sent and the sensor did not answer with the new values in time).
Standard library only. The interface and the job around it (metrolla-hostd's `sensor.program`) are
in metrolla-edge, docs/sensor-program.md.

**No command here has been run against a real sensor.** Every fact below has its source; what a
source does not say is marked "not known" and is read back, never assumed.

Two channels. Paths are from this repository's root; SDK = src/seyond_lidar_ros/src/seyond_sdk
(the vendor's SDK, a submodule, at 73a24e9).

- **HTTP, TCP 8010**: `GET /command/?get_<name>`, the answer is the body (SDK
  src/sdk_client/lidar_client_communication.cpp:59-98, `send_get_command_`; port 8010:
  SDK apps/example/demo.cpp:54). Read here: `get_sn` (:166-168), `get_model` (:170-172),
  `get_fw_version` (:162-164), `get_udp_ports_ip` (:218-243). This tool sends only `get_` names
  over HTTP. The driver itself talks to this port, so HTTP is not counted as a control session.
- **Text commands, TCP 8002** (SDK src/utils/net_manager.h:55, `kDefaultLidarPort`): one
  connection per command; the command and a newline are sent, the reply ends with an empty line
  (SDK src/utils/net_manager.cpp:695-786, `send_command_with_fd_v_`; :832-866 opens and closes a
  connection for each command). This is the control session: every command that uses it needs
  `--driver-stopped`, and the tool never has two open at once (`sessions` counts them).
  Read: `get_i_config time` (SDK src/sdk_common/lidar_communication.cpp:68-86, the reply is
  `[section]` and `key = value` lines), `get_status` (:18-34 and :437-450; `time_config=` is
  enum InnoTimeSyncConfig, SDK src/sdk_common/inno_lidar_packet.h:88-95), `get_network`.
  Written: `set_i_config time <key> <value>` (:88-99), `set_network`, `reboot 1` (:101-104).

What each command does:

- **probe**: HTTP only: serial number, model letter, firmware. No MAC address: no command for it
  is in any source.
- **get-net**: `get_network`, `get_i_config time`, `get_status` on TCP 8002 and `get_udp_ports_ip`
  over HTTP.
- **set-net**: `set_network`. **It is known only from the vendor's closed tool**, the binary SDK
  apps/tools/lidar_util/innovusion_lidar_util (sha256 8a3e489b...c1ff3d, not stripped): its help
  text `set_network <ip> <netmask> [gateway]`, its command table (`set_network`, `get_network`),
  and the disassembly of `handle_set_network(CommandData const&, int, char const**)` at 0xc3a0.
  That routine checks the addresses (first number not 0 or 255, the address not the network's
  first or last, a netmask of leading ones and not zero, a gateway inside the network and not the
  address itself), sends `set_network` followed by twelve numbers (address, netmask, gateway, a
  missing gateway as 0 0 0 0; format string at 0x1f4f8), and, only when the reply contains
  `Invalid command`, sends `set_network <ip> <netmask> <gateway or 0>` (format at 0x1fded). This
  tool does the same two forms in the same order. **Not known**: what the sensor replies, whether
  the address is taken at once or at the next restart, and the form of the `get_network` reply
  (the vendor's tool only prints it). So the tool reads `get_network` leniently (labelled
  addresses, or two or three addresses in the order of `set_network`), refuses to write when it
  cannot understand the reply and the address is not to change, and after a write watches both
  addresses: the new values at the new address are `set`; the new values stored while the sensor
  still answers at the old address are followed by `reboot 1` (unless `--no-reboot`: then
  `unverified`, `reboot: needed`); the old values still reported are `failed`.
- **set-dest**: **not possible**, always `unsupported` (exit 3), nothing is sent and no connection
  is opened. A Seyond has no destination that is kept: the driver asks for the stream at each
  start (`set_udp_ports_ip=<port>,<port>,<port>` over HTTP, SDK
  src/sdk_client/lidar_client_communication.cpp:287-292, called from
  src/sdk_client/lidar_client.cpp:927-966 `get_set_udp_ports_ip`) and the sensor sends to the
  host that asked: the call carries ports and no address. What `get_udp_ports_ip` says now is
  reported by get-net under `dest`, with `persistent: false`.
- **set-ptp**: the two `[time]` keys the fleet's PTP service sets (Metrolla-Inc/
  core_system_containers at 9067b4a, fs/usr/sbin/run_ptp4l.sh:190-255): `ptp_automotive 0`
  (1 is gPTP, 802.1AS: the sensor then sends no PTP to a 1588 end-to-end grandmaster) and
  `time_stamping` by the model letter of `get_model`: `h` 1, `w` and `l` 0 (:207); an unknown
  letter leaves it alone. Both are read back with `get_i_config time`. Not possible, and said:
  **L2 and peer-to-peer PTP** (the whole step is `unsupported`, nothing sent: the sensor's 1588
  client is UDP/IPv4, end-to-end, domain 0, run_ptp4l.sh:13 and :161); a **domain** other than 0;
  a **timescale** other than the one the model computes with (`w`, `l`: TAI, `h`: UTC,
  run_ptp4l.sh:171-173 and :180-183; it is the grandmaster's, no sensor setting). No command that
  chooses the clock source is in any source: what `get_status` says is reported as `time_source`,
  and `clock_source` is `ptp` only when the sensor says so, else null.

The rules of the interface, each with a test in tests/unit/test_sensor_tool.py: reading (`Reader`)
is separate from writing (`Writer`), and the reader refuses any text that is not one of its three
read commands; every write needs `--driver-stopped`; a set command reads first, changes only what
differs, reads back, and is idempotent; never two control connections at once; what cannot be set
is said (`unsupported`).
"""

from __future__ import annotations

import argparse
import http.client
import ipaddress
import json
import re
import socket
import sys
import time

VENDOR = "seyond"

# ---- the interface: this part is the same text in every driver repository's sensor-tool ----------

INTERFACE = 1
COMMANDS = ("probe", "get-net", "set-net", "set-dest", "set-ptp")
STEP = {"set-net": "net", "set-dest": "dest", "set-ptp": "ptp"}
EXIT = {"reported": 0, "set": 0, "unchanged": 0, "changes": 2, "refused": 1, "failed": 1, "no-answer": 1, "unsupported": 3, "unverified": 5}
PAUSE = 1.0  # seconds between two reads while a change is awaited; tests shorten it


class Stop(Exception):
    """The run ends with this outcome and message."""

    def __init__(self, outcome: str, message: str) -> None:
        super().__init__(message)
        self.outcome, self.message = outcome, message


class NoAnswer(Exception):
    """Nothing answered, or what answered is not this vendor's sensor."""


class Sessions:
    """Counts the connections of a run. A second control connection while one is open is a fault
    of this program, and is refused here before it is opened."""

    def __init__(self, control: tuple, others: tuple = ()) -> None:
        self.control, self.open_now, self.max_concurrent = control, 0, 0
        self.counts = {kind: 0 for kind in (*control, *others)}

    def opening(self, kind: str) -> None:
        if kind in self.control:
            if self.open_now:
                raise RuntimeError(f"a second control connection ({kind}) while one is open")
            self.open_now += 1
            self.max_concurrent = max(self.max_concurrent, self.open_now)
        self.counts[kind] += 1

    def not_opened(self, kind: str) -> None:
        """The connection just announced was never made."""
        self.counts[kind] -= 1
        self.closed(kind)

    def closed(self, kind: str) -> None:
        if kind in self.control:
            self.open_now -= 1

    def report(self) -> dict:
        return {**self.counts, "max_concurrent": self.max_concurrent}


class _Parser(argparse.ArgumentParser):
    def error(self, message: str) -> None:  # argparse would print usage and exit 2
        raise Stop("refused", f"usage: {message}")


def ipv4(text: str) -> str:
    try:
        return str(ipaddress.IPv4Address(text))
    except ValueError:
        raise argparse.ArgumentTypeError(f"{text!r} is not an IPv4 address") from None


def netmask(text: str) -> str:
    text = ipv4(text)
    bits = int(ipaddress.IPv4Address(text))
    if bits == 0 or (~bits & 0xFFFFFFFF) & ((~bits & 0xFFFFFFFF) + 1):
        raise argparse.ArgumentTypeError(f"{text} is not a netmask")
    return text


def number(low: int, high: int):
    def parse(text: str) -> int:
        if not text.isascii() or not text.isdigit() or not low <= int(text) <= high:
            raise argparse.ArgumentTypeError(f"{text!r} is not a number from {low} to {high}")
        return int(text)
    return parse


def seconds(text: str) -> float:
    try:
        value = float(text)
    except ValueError:
        value = -1.0
    if not 0 <= value <= 3600:
        raise argparse.ArgumentTypeError(f"{text!r} is not a number of seconds from 0 to 3600")
    return value


def parse_arguments(argv: list, extra=None) -> argparse.Namespace:
    """The arguments of interface version 1. `extra(parser, command)` adds a vendor's own options."""
    parser = _Parser(prog="sensor-tool", add_help=False, allow_abbrev=False)
    commands = parser.add_subparsers(dest="command", parser_class=_Parser, required=True)
    for name in COMMANDS:
        sub = commands.add_parser(name, add_help=False, allow_abbrev=False)
        sub.add_argument("--ip", type=ipv4, required=True)
        sub.add_argument("--model", default="")
        sub.add_argument("--dev-port", type=number(1, 65535), default=None)
        sub.add_argument("--timeout", type=seconds, default=5.0)
        sub.add_argument("--driver-stopped", action="store_true")
        if name == "set-net":
            sub.add_argument("--new-ip", type=ipv4, required=True)
            sub.add_argument("--netmask", type=netmask, required=True)
            sub.add_argument("--gateway", type=ipv4, required=True)
            sub.add_argument("--no-reboot", action="store_true")
        elif name == "set-dest":
            sub.add_argument("--dest-ip", type=ipv4, required=True)
            sub.add_argument("--data-port", type=number(1, 65535), required=True)
        elif name == "set-ptp":
            sub.add_argument("--transport", choices=("L2", "UDPv4"), required=True)
            sub.add_argument("--domain", type=number(0, 127), required=True)
            sub.add_argument("--delay-mechanism", choices=("E2E", "P2P"), required=True)
            sub.add_argument("--timescale", choices=("UTC", "TAI"), required=True)
            sub.add_argument("--profile", choices=("1588v2",), default="1588v2")
            sub.add_argument("--clock-source", choices=("ptp",), default="ptp")
        if name in STEP:
            sub.add_argument("--dry-run", action="store_true")
            sub.add_argument("--verify-timeout", type=seconds, default=60.0)
        if extra is not None:
            extra(sub, name)
    args = parser.parse_args(argv)
    args.dry_run = bool(getattr(args, "dry_run", False))
    args.writes = args.command in STEP and not args.dry_run
    return args


def blank(vendor: str) -> dict:
    return {"sensor_tool": INTERFACE, "vendor": vendor, "command": None, "ip": None, "outcome": None, "exit_code": None,
            "message": "", "identity": None, "before": None, "wanted": None, "after": None, "changed": [], "unsupported": [],
            "reboot": "none", "verified": None, "answers_at": None, "sessions": None, "warnings": []}


def blank_state() -> dict:
    return {"net": {"ip": None, "netmask": None, "gateway": None}, "dest": {"ip": None, "data_port": None},
            "ptp": {"clock_source": None, "profile": None, "transport": None, "domain": None, "status": None}, "channel": None}


def blank_identity(vendor: str) -> dict:
    return {"vendor": vendor, "model": None, "serial": None, "mac": None, "firmware": None}


def source_address(ip: str):
    """The address of this host that a packet to `ip` would leave from, or None. Sends nothing."""
    probe = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        probe.connect((ip, 9))
        return probe.getsockname()[0]
    except OSError:
        return None
    finally:
        probe.close()


def check_new_address(new_ip: str, mask: str, gateway: str) -> None:
    """Refuse an address that is no host address of its network, a gateway outside it, and an address
    this host could not reach afterwards: it has no address of its own in that network."""
    network = ipaddress.IPv4Network(f"{new_ip}/{mask}", strict=False)
    address = ipaddress.IPv4Address(new_ip)
    if address.is_multicast or address.is_unspecified or address.is_loopback and not ALLOW_LOOPBACK:
        raise Stop("refused", f"{new_ip} is not an address a sensor can have")
    if network.prefixlen < 31 and address in (network.network_address, network.broadcast_address):
        raise Stop("refused", f"{new_ip} is the network or broadcast address of {network}")
    if gateway != "0.0.0.0" and (ipaddress.IPv4Address(gateway) not in network or gateway == new_ip):
        raise Stop("refused", f"the gateway {gateway} is not another address of {network}")
    source = source_address(new_ip)
    if source is None or ipaddress.IPv4Address(source) not in network:
        raise Stop("refused", f"this host has no address in {network}, so the sensor could not be reached at {new_ip} afterwards; nothing was sent")
    if source == new_ip and not ALLOW_LOOPBACK:
        raise Stop("refused", f"{new_ip} is this host's own address")


ALLOW_LOOPBACK = False  # tests set it: their fake sensors are on loopback addresses


def differing(step: str, wanted: dict, have: dict) -> list:
    """The settings of `wanted` that `have` does not already have, as "step.key"."""
    return [f"{step}.{key}" for key, value in wanted.items() if (have or {}).get(key) != value]


def await_state(deadline: float, read, good):
    """Read until `good(state)` or the deadline: (the last state read or None, whether it was good).
    `read` returns None when nothing answered."""
    last = None
    while True:
        state = read()
        if state is not None:
            last = state
            if good(state):
                return state, True
        if time.monotonic() + PAUSE > deadline:
            return last, False
        time.sleep(PAUSE)


class Run:
    """One run of the tool: its arguments, its answer and its connections."""

    def __init__(self, args: argparse.Namespace, out: dict, sessions: Sessions) -> None:
        self.args, self.out, self.sessions = args, out, sessions

    def warn(self, text: str) -> None:
        if text not in self.out["warnings"]:
            self.out["warnings"].append(text)

    def cannot(self, setting: str, reason: str) -> None:
        self.out["unsupported"].append({"setting": setting, "reason": reason})

    def need_stopped(self, what: str) -> None:
        if not self.args.driver_stopped:
            raise Stop("refused", f"{what} needs --driver-stopped: the driver's own connection to the sensor must be closed first; nothing was sent")


def public(state):
    """`state` without the tool's own notes (keys that start with '_')."""
    if state is None:
        return None
    return {key: ({k: v for k, v in value.items() if not k.startswith("_")} if isinstance(value, dict) else value)
            for key, value in state.items()}


def settle(run: Run, step: str, wanted: dict, state: dict) -> list:
    """Note what is wanted and what differs. Ends the run when nothing differs, or on a dry run."""
    out = run.out
    out["before"], out["wanted"] = public(state), {step: wanted}
    out["changed"] = differing(step, wanted, state[step])
    if not out["changed"]:
        out["after"], out["verified"], out["answers_at"] = public(state), True, run.args.ip
        raise Stop("unchanged", f"{step} is already as asked; nothing was sent")
    if run.args.dry_run:
        raise Stop("changes", f"would change {', '.join(out['changed'])}")
    return out["changed"]


def finish(run: Run, step: str, wanted: dict, state, good: bool, at) -> None:
    out = run.out
    out["after"], out["verified"], out["answers_at"] = public(state), good, at
    if good:
        raise Stop("set", f"{', '.join(out['changed'])} set and read back")
    still = differing(step, wanted, state[step]) if state else out["changed"]
    raise Stop("unverified", f"the change was sent, and the sensor did not answer with the new values within "
               f"{run.args.verify_timeout:g} s (still different: {', '.join(still)})")


def run_tool(argv, vendor: str, sessions: Sessions, handlers: dict, extra=None, stdout=None) -> int:
    """Parse, run one command, print the one JSON object, return the exit status."""
    out = blank(vendor)
    words = list(sys.argv[1:] if argv is None else argv)
    if words and words[0] in COMMANDS:
        out["command"] = words[0]
    try:
        args = parse_arguments(words, extra)
        out["ip"] = args.ip
        if args.writes and not args.driver_stopped:
            raise Stop("refused", f"{args.command} writes to the sensor and needs --driver-stopped; nothing was sent")
        handlers[args.command](Run(args, out, sessions))
        raise RuntimeError("the command ended without an outcome")
    except Stop as stop:
        out["outcome"], out["message"] = stop.outcome, stop.message
    except NoAnswer as exc:  # after a change was sent, silence is "not read back", not "nothing there"
        out["outcome"], out["message"] = "unverified" if out.get("_wrote") else "no-answer", str(exc)
    except Exception as exc:  # noqa: BLE001 - the answer is always one JSON object
        out["outcome"] = "unverified" if out.get("_wrote") else "failed"
        out["message"] = f"{type(exc).__name__}: {exc}"
    out.pop("_wrote", None)
    out["exit_code"], out["sessions"] = EXIT[out["outcome"]], sessions.report()
    print(json.dumps(out), file=stdout or sys.stdout, flush=True)
    return out["exit_code"]


# ---- Seyond: the two channels ------------------------------------------------------------------------

PORTS = {"command": 8002, "http": 8010}  # net_manager.h:55 kDefaultLidarPort; apps/example/demo.cpp:54
HTTP_READS = ("sn", "model", "fw_version", "udp_ports_ip")  # lidar_client_communication.cpp:162-172, :218-243
READ_COMMANDS = ("get_network", "get_i_config time", "get_status")
# enum InnoTimeSyncConfig (inno_lidar_packet.h:88-95), the time_config= of get_status
TIME_SOURCES = {0: "host", 1: "ptp", 2: "gps", 3: "file", 4: "ntp"}
# run_ptp4l.sh:207 WANT_BY_MODEL: the [time] time_stamping each model's PTP client needs
TIME_STAMPING = {"h": 1, "w": 0, "l": 0}
# run_ptp4l.sh:171-173, :180-183: what each model takes the time on the wire to be
TIMESCALE = {"h": "UTC", "w": "TAI", "l": "TAI"}
INVALID = "Invalid command"  # the reply handle_set_network looks for (innovusion_lidar_util, 0x1fddd)
POLL_TIMEOUT = 2.0  # seconds one read may take while a change is awaited
_QUAD = r"(?<![\d.])(\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3})(?![\d.])"
_LABELS = (("gateway", r"gate\s*way|\bgw\b|router"), ("netmask", r"net\s*mask|subnet|\bmask\b"), ("ip", r"\bip\b|ip_?addr|address|\baddr\b"))


def _exchange(run: "Run", ip: str, text: str, wait: float) -> str:
    """One command on TCP 8002: connect, send the line, read to the empty line, close. Used only by
    Reader.command and Writer.command, which decide what `text` may be."""
    run.sessions.opening("command")
    try:
        sock = socket.create_connection((ip, PORTS["command"]), timeout=max(wait, 0.2))
    except OSError as exc:
        run.sessions.not_opened("command")
        raise NoAnswer(f"no connection to {ip}, TCP port {PORTS['command']} ({exc.strerror or exc})") from None
    data = b""
    try:
        sock.sendall(text.encode("ascii") + b"\n")
        while b"\n\n" not in data and len(data) < 65536:
            chunk = sock.recv(4096)
            if not chunk:
                break  # a reply without the empty line, ended by the sensor (net_manager.cpp:753-760)
            data += chunk
    except OSError as exc:
        if not data:
            raise NoAnswer(f"no reply from {ip}, TCP port {PORTS['command']}, to {text.split()[0]} ({exc.strerror or exc})") from None
    finally:
        sock.close()
        run.sessions.closed("command")
    if not data:
        raise NoAnswer(f"an empty reply from {ip}, TCP port {PORTS['command']}, to {text.split()[0]}")
    return data.decode("utf-8", "replace").strip()


class Reader:
    """Everything that reads a sensor. It can send three commands on TCP 8002, all reads, and HTTP
    GETs of four `get_` names; anything else is refused here, before a connection is opened."""

    def __init__(self, run: "Run") -> None:
        self.run = run

    def http(self, ip: str, name: str, wait: float = 0.0):
        """The body of `GET /command/?get_<name>`, or None when nothing answers or the sensor says no."""
        if name not in HTTP_READS:
            raise RuntimeError(f"the reader does not ask for get_{name}")
        self.run.sessions.opening("http")
        connection = http.client.HTTPConnection(ip, PORTS["http"], timeout=max(wait or self.run.args.timeout, 0.2))
        try:
            connection.request("GET", f"/command/?get_{name}", headers={"Connection": "close"})
            answer = connection.getresponse()
            body = answer.read(65536)
            return body.decode("utf-8", "replace").strip() if answer.status == 200 else None
        except (OSError, http.client.HTTPException):
            return None
        finally:
            connection.close()
            self.run.sessions.closed("http")

    def command(self, ip: str, text: str, wait: float = 0.0) -> str:
        if text not in READ_COMMANDS:
            raise RuntimeError(f"the reader does not send {text.split()[0] if text.split() else 'an empty command'!r}")
        self.run.need_stopped(f"{text.split()[0]} uses the sensor's command port (TCP {PORTS['command']}), a control session, which")
        return _exchange(self.run, ip, text, wait or self.run.args.timeout)


class Writer:
    """Everything that changes a sensor. Made only in a run that writes with the driver stopped."""

    def __init__(self, run: "Run") -> None:
        if not run.args.writes or not run.args.driver_stopped:
            raise Stop("refused", "a write needs --driver-stopped and no --dry-run; nothing was sent")
        self.run, self.reply = run, ""

    def command(self, ip: str, text: str) -> str:
        self.run.out["_wrote"] = True
        return _exchange(self.run, ip, text, max(self.run.args.timeout, 2.0))  # the SDK gives a set 2 s (lidar_communication.cpp:89)

    def set_time(self, ip: str, key: str, value: int) -> None:
        """`set_i_config time <key> <value>` (lidar_communication.cpp:88-99; run_ptp4l.sh:235, :250)."""
        if key not in ("ptp_automotive", "time_stamping") or value not in (0, 1):
            raise RuntimeError(f"[time] {key} {value} is not a setting this tool writes")
        self.command(ip, f"set_i_config time {key} {value}")

    def set_network(self, ip: str, new_ip: str, mask: str, gateway: str) -> bool:
        """What handle_set_network of the vendor's tool sends: twelve numbers first, and the three
        addresses only when the sensor answers `Invalid command`. False: it knows neither form."""
        numbers = " ".join(part for address in (new_ip, mask, gateway) for part in address.split("."))
        reply = self.command(ip, f"set_network {numbers}")
        if INVALID in reply:
            reply = self.command(ip, f"set_network {new_ip} {mask} {'0' if gateway == '0.0.0.0' else gateway}")
        self.reply = reply
        return INVALID not in reply

    def reboot(self, ip: str) -> None:
        """`reboot 1` (lidar_communication.cpp:101-104, set_reboot)."""
        try:
            self.command(ip, "reboot 1")
        except NoAnswer:
            pass  # a sensor that restarts may not answer


# ---- Seyond: reading ------------------------------------------------------------------------------------


def _address(text: str):
    try:
        return str(ipaddress.IPv4Address(text))
    except ValueError:
        return None


def _is_netmask(text: str) -> bool:
    bits = int(ipaddress.IPv4Address(text))
    return bits != 0 and not (~bits & 0xFFFFFFFF) & ((~bits & 0xFFFFFFFF) + 1)


def parse_network(reply: str):
    """{"ip", "netmask", "gateway"} from a `get_network` reply, or None when it is not understood.
    The form of the reply is in no source: labelled addresses are taken by their labels, and
    otherwise two or three addresses are taken in the order `set_network` has them, when the second
    is a netmask. A gateway of 0.0.0.0, or none, is no gateway."""
    found = {}
    for line in re.split(r"[\n;,]", reply):
        quads = [_address(q) for q in re.findall(_QUAD, line)]
        if len(quads) != 1 or quads[0] is None:
            continue
        label = line[:line.index(re.search(_QUAD, line).group(1))].lower()
        for key, pattern in _LABELS:
            if re.search(pattern, label):
                found.setdefault(key, quads[0])
                break
    if "ip" not in found or "netmask" not in found:
        quads = [_address(q) for q in re.findall(_QUAD, reply)]
        if None in quads or len(quads) not in (2, 3):
            return None
        found = dict(zip(("ip", "netmask", "gateway"), quads))
    if not _is_netmask(found["netmask"]) or found["ip"] == "0.0.0.0":
        return None
    gateway = found.get("gateway")
    return {"ip": found["ip"], "netmask": found["netmask"], "gateway": None if gateway in (None, "0.0.0.0") else gateway}


def parse_config(reply: str) -> dict:
    """The `key = value` lines of a get_i_config reply (lidar_communication.cpp:74; run_ptp4l.sh:221-225)."""
    found = {}
    for line in reply.splitlines():
        key, sign, value = line.partition("=")
        if sign and key.strip():
            value = value.strip()
            found[key.strip()] = int(value) if re.fullmatch(r"-?\d+", value) else value
    return found


def identify(reader: Reader, ip: str, wait: float = 0.0) -> dict:
    """Who answers at `ip`, over HTTP alone: NoAnswer unless it answers get_sn as a Seyond does."""
    serial = reader.http(ip, "sn", wait)
    if not serial or len(serial) > 64 or not serial.isprintable():
        raise NoAnswer(f"no Seyond answers at {ip}: no serial number from http://{ip}:{PORTS['http']}/command/?get_sn")
    identity = blank_identity(VENDOR)
    model = reader.http(ip, "model", wait) or ""
    model = model.split(":", 1)[1].strip() if model.lower().startswith("model:") else model  # "model: w" on TCP 8002 (lidar_communication.cpp:335)
    firmware = (reader.http(ip, "fw_version", wait) or "").splitlines()
    identity.update(serial=serial, model=model[:32] or None, firmware=firmware[0].strip()[:120] if firmware and firmware[0].strip() else None)
    return identity


def letter(identity: dict):
    """The model letter the fleet's script goes by: the first character of get_model (run_ptp4l.sh:217-220)."""
    return (identity.get("model") or "")[:1].lower() or None


def read_net(run: Run, reader: Reader, ip: str, state: dict, wait: float = 0.0) -> None:
    reply = reader.command(ip, "get_network", wait)
    found = None if INVALID in reply else parse_network(reply)
    if found is None:
        state["net"].update(ip=ip, _read=False)  # it answers there; what it has stored is not known
        run.warn("the sensor's reply to get_network was not understood" + (" (it does not know the command)" if INVALID in reply else "")
                 + f": its netmask and gateway are not known, and its address is given as where it answers ({reply[:80]!r})")
        return
    state["net"].update(found, _read=True)
    if found["ip"] != ip:
        run.warn(f"the sensor answers at {ip} and says its address is {found['ip']}: an address it takes at its next restart")


def read_ptp(run: Run, reader: Reader, ip: str, state: dict, wait: float = 0.0) -> None:
    config = parse_config(reader.command(ip, "get_i_config time", wait))
    ptp = state["ptp"]
    for key in ("ptp_automotive", "time_stamping"):
        ptp[key] = config.get(key)
    if config.get("ptp_automotive") == 0:  # the 1588 client: UDP/IPv4, end-to-end (run_ptp4l.sh:13, :161)
        ptp.update(profile="1588v2", transport="UDPv4", delay_mechanism="E2E")
    elif config.get("ptp_automotive") == 1:  # gPTP, 802.1AS (run_ptp4l.sh:192-193)
        ptp.update(profile="gPTP", transport="L2", delay_mechanism="P2P")
    found = re.search(r"time_config=(-?\d+)", reader.command(ip, "get_status", wait))  # lidar_communication.cpp:21
    ptp["time_source"] = TIME_SOURCES.get(int(found.group(1)), f"unknown({found.group(1)})") if found else None
    # No command chooses the clock source, so it is given only when the sensor says PTP: the host
    # compares this key with "ptp". What the sensor says is always in time_source.
    ptp["clock_source"] = "ptp" if ptp["time_source"] == "ptp" else None


def read_dest(reader: Reader, ip: str, state: dict) -> None:
    """Where the sensor sends now, as `get_udp_ports_ip` says: data, message and status port, then
    the address (lidar_client_communication.cpp:235-237). Not kept: the driver sets it at each start."""
    parts = [part.strip() for part in (reader.http(ip, "udp_ports_ip") or "").split(",")]
    dest = state["dest"]
    dest["persistent"] = False
    if len(parts) >= 4 and all(part.isdigit() for part in parts[:3]):
        address = _address(parts[3])
        dest.update(ip=None if address in (None, "0.0.0.0") else address, data_port=int(parts[0]) or None,
                    message_port=int(parts[1]) or None, status_port=int(parts[2]) or None)


def state_of(run: Run, reader: Reader, ip: str, parts: tuple, wait: float = 0.0) -> dict:
    state = blank_state()
    state["channel"] = "tcp8002+http"
    if "net" in parts:
        read_net(run, reader, ip, state, wait)
    if "ptp" in parts:
        read_ptp(run, reader, ip, state, wait)
    if "dest" in parts:
        read_dest(reader, ip, state)
    return state


# ---- Seyond: the commands ----------------------------------------------------------------------------


def whole(run: Run, step: str, cannot: list) -> None:
    """The whole step cannot be done on this sensor: exit 3, nothing sent."""
    run.out["unsupported"] = [{"setting": setting, "reason": reason} for setting, reason in cannot]
    raise Stop("unsupported", f"{step} cannot be set on this sensor: {'; '.join(reason for _, reason in cannot)}; nothing was sent")


def probe(run: Run) -> None:
    run.out["identity"] = identity = identify(Reader(run), run.args.ip)
    raise Stop("reported", f"a Seyond answers at {run.args.ip} (serial number {identity['serial']}, model {identity['model'] or 'not said'})")


def get_net(run: Run) -> None:
    run.need_stopped(f"reading a Seyond's settings uses its command port (TCP {PORTS['command']}), a control session, which")
    reader, ip = Reader(run), run.args.ip
    run.out["identity"] = identify(reader, ip)
    run.out["after"] = public(state_of(run, reader, ip, ("net", "ptp", "dest")))
    raise Stop("reported", f"the settings of the Seyond at {ip}")


def set_dest(run: Run) -> None:
    run.out["wanted"] = {"dest": {"ip": run.args.dest_ip, "data_port": run.args.data_port}}
    whole(run, "dest", [("dest", "a Seyond keeps no destination: the driver asks for the stream at each start and the sensor sends "
                                 "to the host that asked, on the port the driver named (its data_port)")])


def net_differs(wanted: dict, have: dict) -> list:
    """As differing(), with no gateway (null) and 0.0.0.0 being the same."""
    have = {**have, "gateway": have.get("gateway") or "0.0.0.0"}
    return differing("net", wanted, have)


def set_net(run: Run) -> None:
    args, out, old, new = run.args, run.out, run.args.ip, run.args.new_ip
    wanted = {"ip": new, "netmask": args.netmask, "gateway": args.gateway}
    run.need_stopped(f"reading a Seyond's address uses its command port (TCP {PORTS['command']}), a control session, which")
    reader = Reader(run)
    out["identity"] = identity = identify(reader, old)
    state = state_of(run, reader, old, ("net",))
    out["before"], out["wanted"] = public(state), {"net": wanted}
    if not state["net"]["_read"] and new == old:
        raise Stop("refused", "the sensor's reply to get_network was not understood, so its netmask and gateway cannot be compared "
                   "with the ones asked for, and its address is not to change; nothing was sent")
    out["changed"] = net_differs(wanted, state["net"]) if state["net"]["_read"] else ["net.ip", "net.netmask", "net.gateway"]
    if not out["changed"]:
        out["after"], out["verified"], out["answers_at"] = public(state), True, old
        raise Stop("unchanged", "net is already as asked; nothing was sent")
    check_new_address(new, args.netmask, args.gateway)
    if new.split(".")[0] in ("0", "255"):  # handle_set_network: "IP cannot start with 0", "... with 255"
        raise Stop("refused", f"{new} is not an address the sensor takes (its first number is 0 or 255); nothing was sent")
    if new != old:
        try:
            other = identify(reader, new, POLL_TIMEOUT)
        except NoAnswer:
            other = None
        if other is not None:
            raise Stop("refused", f"a Seyond already answers at {new} (serial number {other['serial']}): two sensors would share "
                       "the address; nothing was sent")
    if args.dry_run:
        raise Stop("changes", f"would change {', '.join(out['changed'])}")
    writer = Writer(run)
    if not writer.set_network(old, new, args.netmask, args.gateway):
        out.pop("_wrote")  # the sensor refused both forms as unknown commands: nothing was changed
        whole(run, "net", [("net", "the sensor answers 'Invalid command' to both forms of set_network that the vendor's tool sends")])

    def read(ip: str):
        """(identity, state) of what answers at `ip` now, or None."""
        try:
            who = identify(reader, ip, POLL_TIMEOUT)
            return who, state_of(run, reader, ip, ("net",), POLL_TIMEOUT)
        except NoAnswer:
            return None

    deadline, polls, last, at = time.monotonic() + args.verify_timeout, 0, None, None
    while True:
        polls += 1
        got = read(new)
        if got is not None and new != old and got[0]["serial"] != identity["serial"]:
            raise Stop("failed", f"another sensor answers at {new} (serial number {got[0]['serial']}, not {identity['serial']}); "
                       f"the address was sent to the sensor at {old}")
        if got is not None:
            last, at = got[1], new
            if got[1]["net"]["_read"] and not net_differs(wanted, got[1]["net"]):
                out["after"], out["verified"], out["answers_at"] = public(got[1]), True, new
                if "net.ip" not in out["changed"]:
                    run.warn("whether the sensor uses a new netmask or gateway before its next restart is not known")
                raise Stop("set", f"{', '.join(out['changed'])} set and read back at {new}")
            if not got[1]["net"]["_read"] and new != old:
                # The same sensor answers at the new address: that proves the address, and nothing more can be read.
                out["after"], out["verified"], out["answers_at"] = public(got[1]), True, new
                run.warn(f"the address is proven by the same sensor (serial number {identity['serial']}) answering at {new}; "
                         "its netmask and gateway could not be read back")
                raise Stop("set", f"net.ip set: the sensor answers at {new}; its netmask and gateway could not be read back")
        elif new != old:
            there = read(old)
            if there is not None and there[0]["serial"] == identity["serial"]:
                last, at = there[1], old
                stored = there[1]["net"]["_read"] and not net_differs(wanted, there[1]["net"])
                if stored and polls >= 3 and out["reboot"] == "none":  # stored and, after a moment, still not taken
                    if args.no_reboot:
                        out["reboot"], out["after"], out["verified"], out["answers_at"] = "needed", public(last), False, old
                        raise Stop("unverified", f"the sensor has stored the new address and still answers at {old}: it takes it at its next restart (--no-reboot)")
                    writer.reboot(old)
                    out["reboot"] = "sent"
                elif not stored and there[1]["net"]["_read"] and polls >= 3 and out["reboot"] == "none":
                    out["after"], out["verified"], out["answers_at"] = public(last), False, old
                    raise Stop("failed", f"the sensor took set_network (its reply: {writer.reply[:80]!r}) and still reports its old settings at {old}")
            else:
                at = None
        if polls >= 3 and got is not None and new == old and got[1]["net"]["_read"]:
            out["after"], out["verified"], out["answers_at"] = public(last), False, old
            raise Stop("failed", f"the sensor took set_network (its reply: {writer.reply[:80]!r}) and still reports its old settings")
        if time.monotonic() + PAUSE > deadline:
            break
        time.sleep(PAUSE)
    out["after"], out["verified"], out["answers_at"] = public(last) if at is not None else None, False, at
    raise Stop("unverified", f"set_network was sent, and the sensor did not answer with the new values at {new} within {args.verify_timeout:g} s"
               + (f"; it answers at {at}" if at else "; it answers at neither address")
               + ("; a restart was sent" if out["reboot"] == "sent" else ""))


def set_ptp(run: Run) -> None:
    args, ip = run.args, run.args.ip
    cannot = []
    if args.transport != "UDPv4":
        cannot.append(("ptp.transport", "a Seyond's 1588 client is UDP/IPv4; L2 exists only as gPTP (802.1AS), which no 1588v2 grandmaster speaks"))
    if args.delay_mechanism != "E2E":
        cannot.append(("ptp.delay_mechanism", "a Seyond's 1588 client is end-to-end; peer-to-peer exists only as gPTP (802.1AS)"))
    if cannot:
        whole(run, "ptp", cannot)
    run.need_stopped(f"reading a Seyond's [time] settings uses its command port (TCP {PORTS['command']}), a control session, which")
    reader = Reader(run)
    run.out["identity"] = identity = identify(reader, ip)
    state = state_of(run, reader, ip, ("ptp",))
    have, model, wanted = state["ptp"], letter(identity), {}
    if have["ptp_automotive"] is None and have["time_stamping"] is None:
        run.out["before"] = public(state)
        whole(run, "ptp", [("ptp", "the sensor's [time] settings have neither ptp_automotive nor time_stamping")])
    if have["ptp_automotive"] is not None:
        wanted["ptp_automotive"] = 0
    if model not in TIME_STAMPING:
        run.warn(f"the model {identity['model']!r} is not one whose time_stamping is known (h, w, l): it is left as it is ({have['time_stamping']})")
    elif have["time_stamping"] is None:
        run.warn("the sensor's [time] settings have no time_stamping: it is not set")
    else:
        wanted["time_stamping"] = TIME_STAMPING[model]
    # Said only when another value than the sensor's is asked for.
    if args.domain != 0:
        run.cannot("ptp.domain", f"a Seyond's 1588 client uses domain 0 and no setting for another is known; {args.domain} was asked for")
    if model in TIMESCALE and args.timescale != TIMESCALE[model]:
        run.cannot("ptp.timescale", f"model {model!r} takes the time on the wire to be {TIMESCALE[model]}; the timescale is the grandmaster's, "
                                    f"not a sensor setting, and {args.timescale} was asked for")
    if have["clock_source"] != "ptp":
        run.warn(f"the sensor says its time source is {have['time_source'] or 'not known'}; no command that chooses it is known, "
                 "and the two [time] settings are what its PTP client needs to follow a 1588 grandmaster")
    changed = settle(run, "ptp", wanted, state)
    writer = Writer(run)
    for name in changed:
        key = name.split(".", 1)[1]
        writer.set_time(ip, key, wanted[key])

    def read():
        try:
            return state_of(run, reader, ip, ("ptp",), POLL_TIMEOUT)
        except NoAnswer:
            return None
    after, good = await_state(time.monotonic() + args.verify_timeout, read, lambda s: not differing("ptp", wanted, s["ptp"]))
    finish(run, "ptp", wanted, after, good, ip if after is not None else None)


HANDLERS = {"probe": probe, "get-net": get_net, "set-net": set_net, "set-dest": set_dest, "set-ptp": set_ptp}


def main(argv=None, stdout=None) -> int:
    return run_tool(argv, VENDOR, Sessions(("command",), ("http",)), HANDLERS, None, stdout)


if __name__ == "__main__":
    sys.exit(main())

"""sensor-tool (docker/sensor_tool.py) against a fake Seyond on loopback (fake_seyond_sensor.py): the
rules of interface version 1, each with its test, every outcome of every command, and the answer's
shape as metrolla-hostd checks it. No real sensor is contacted: only 127.0.0.0/8, and one refusal
that sends nothing."""

from __future__ import annotations

import hashlib
import inspect
import io
import json
import subprocess
import sys
from pathlib import Path

import pytest

import sensor_tool
from fake_seyond_sensor import FakeSeyond, free_ports

REPO = Path(__file__).resolve().parents[2]
TOOL = REPO / "docker" / "sensor_tool.py"
SDK = REPO / "src" / "seyond_lidar_ros" / "src" / "seyond_sdk"
A, B = "127.0.61.1", "127.0.61.2"
MASK = "255.0.0.0"
KEYS = {"sensor_tool", "vendor", "command", "ip", "outcome", "exit_code", "message", "identity", "before", "wanted", "after",
        "changed", "unsupported", "reboot", "verified", "answers_at", "sessions", "warnings"}
NET = ["--new-ip", B, "--netmask", MASK, "--gateway", "0.0.0.0"]
DEST = ["--dest-ip", "127.0.0.1", "--data-port", "8010"]
PTP = ["--transport", "UDPv4", "--domain", "0", "--delay-mechanism", "E2E", "--timescale", "TAI", "--profile", "1588v2", "--clock-source", "ptp"]
SETS = {"set-net": NET, "set-dest": DEST, "set-ptp": PTP}
NUMBERS = "set_network 127 0 61 2 255 0 0 0 0 0 0 0"

# What metrolla-hostd accepts from a tool: metrolla-edge src/metrolla_edge/sensor_program.py,
# TOOL_OUTCOMES and check_answer (branch feat/sensor-program), written out here.
HOSTD_OUTCOMES = {"reported": 0, "set": 0, "unchanged": 0, "changes": 2, "refused": 1, "failed": 1, "no-answer": 1, "unsupported": 3, "unverified": 5}


def hostd_check(status: int, output: str, vendor: str, command: str) -> dict:
    assert len(output) <= 64 * 1024
    lines = [line for line in output.splitlines() if line.strip()]
    assert len(lines) == 1, "one JSON object, on one line"
    answer = json.loads(lines[0])
    assert isinstance(answer, dict) and answer.get("sensor_tool") == 1
    outcome = answer.get("outcome")
    assert isinstance(outcome, str) and outcome in HOSTD_OUTCOMES and HOSTD_OUTCOMES[outcome] == status and answer.get("exit_code") == status
    assert answer.get("vendor") == vendor and answer.get("command") == command
    assert isinstance(answer["message"], str) and answer["message"]
    for key in ("before", "wanted", "after", "identity"):
        assert answer[key] is None or isinstance(answer[key], dict)
    for key in ("before", "after"):  # hostd reads net, dest, ptp and channel of a state
        assert answer[key] is None or {"net", "dest", "ptp", "channel"} <= set(answer[key])
    if answer["identity"] is not None:
        assert set(answer["identity"]) == {"vendor", "model", "serial", "mac", "firmware"}
    assert all(isinstance(c, str) for c in answer["changed"]) and all(isinstance(w, str) for w in answer["warnings"])
    assert all(set(u) == {"setting", "reason"} for u in answer["unsupported"])
    assert answer["reboot"] in ("none", "sent", "needed") and answer["verified"] in (None, True, False)
    return answer


@pytest.fixture(autouse=True)
def quick(monkeypatch):
    monkeypatch.setattr(sensor_tool, "PAUSE", 0.05)
    monkeypatch.setattr(sensor_tool, "POLL_TIMEOUT", 0.5)
    monkeypatch.setattr(sensor_tool, "ALLOW_LOOPBACK", True)
    monkeypatch.setattr(sensor_tool, "PORTS", dict(sensor_tool.PORTS))


def run(fake, *argv, expect=None, stopped=True, verify="3"):
    """One tool run against `fake`: (answer, exit status). The answer is checked as hostd checks it."""
    sensor_tool.PORTS.update(command=fake.command_port, http=fake.http_port)
    words = [str(word) for word in argv] + ["--model", "robin-w", "--timeout", "1"]
    if argv[0] in SETS:
        words += ["--verify-timeout", verify]
    printed = io.StringIO()
    status = sensor_tool.main(words + (["--driver-stopped"] if stopped else []), stdout=printed)
    answer = hostd_check(status, printed.getvalue(), "seyond", argv[0])
    assert set(answer) == KEYS
    assert answer["sessions"]["max_concurrent"] <= 1 and set(answer["sessions"]) == {"command", "http", "max_concurrent"}
    assert fake.max_open <= 1
    if expect is not None:
        assert answer["outcome"] == expect, answer
    return answer, status


# --- reading ----------------------------------------------------------------------------------------


def test_probe_is_http_alone_and_needs_no_stopped_driver():
    with FakeSeyond(A) as fake:
        answer, status = run(fake, "probe", "--ip", A, expect="reported", stopped=False)
        assert status == 0
        assert answer["identity"] == {"vendor": "seyond", "model": "w", "serial": "SEY-FAKE-0001", "mac": None, "firmware": "fake-fw-1.2.3"}
        assert answer["sessions"] == {"command": 0, "http": 3, "max_concurrent": 0}
        assert fake.received == [] and fake.connections == 0
        assert fake.requests == ["/command/?get_sn", "/command/?get_model", "/command/?get_fw_version"]


def test_get_net_reports_what_the_sensor_says():
    with FakeSeyond(A, gateway="127.0.0.254", time_config=1, automotive=0, stamping=0) as fake:
        answer, status = run(fake, "get-net", "--ip", A, expect="reported")
        assert status == 0
        assert answer["after"] == {
            "net": {"ip": A, "netmask": MASK, "gateway": "127.0.0.254"},
            "dest": {"ip": "127.0.0.1", "data_port": 8010, "persistent": False, "message_port": 8010, "status_port": 8010},
            "ptp": {"clock_source": "ptp", "profile": "1588v2", "transport": "UDPv4", "domain": None, "status": None,
                    "ptp_automotive": 0, "time_stamping": 0, "delay_mechanism": "E2E", "time_source": "ptp"},
            "channel": "tcp8002+http"}
        assert answer["identity"]["serial"] == "SEY-FAKE-0001" and answer["warnings"] == []
        assert fake.received == ["get_network", "get_i_config time", "get_status"]
        assert answer["sessions"] == {"command": 3, "http": 4, "max_concurrent": 1}


def test_a_sensor_on_gptp_and_its_own_clock_is_reported_so():
    with FakeSeyond(A, udp="0,0,0,0") as fake:  # automotive 1, time_config 0 (host), no stream asked for
        answer, _ = run(fake, "get-net", "--ip", A, expect="reported")
        ptp = answer["after"]["ptp"]
        assert (ptp["profile"], ptp["transport"], ptp["delay_mechanism"], ptp["ptp_automotive"]) == ("gPTP", "L2", "P2P", 1)
        assert ptp["time_source"] == "host" and ptp["clock_source"] is None  # no setting chooses it: null unless it says PTP
        assert answer["after"]["net"]["gateway"] is None
        assert answer["after"]["dest"] == {"ip": None, "data_port": None, "persistent": False, "message_port": None, "status_port": None}


@pytest.mark.parametrize("form, expected", [
    ("labelled", (A, MASK, "127.0.0.254")),
    ("labelled-other", (A, MASK, "127.0.0.254")),
    ("plain", (A, MASK, "127.0.0.254")),
    ("plain-two", (A, MASK, None)),
    ("garbage", (A, None, None)),  # not understood: the address is where it answers, the rest is not known
    ("invalid", (A, None, None)),
])
def test_the_get_network_reply_is_read_leniently_and_never_guessed(form, expected):
    with FakeSeyond(A, gateway="127.0.0.254", network_reply=form) as fake:
        answer, _ = run(fake, "get-net", "--ip", A, expect="reported")
        net = answer["after"]["net"]
        assert (net["ip"], net["netmask"], net["gateway"]) == expected
        assert any("get_network was not understood" in w for w in answer["warnings"]) == (expected[1] is None)


@pytest.mark.parametrize("reply, expected", [
    ("ip=10.1.2.3\nnetmask=255.255.255.0\ngateway=0.0.0.0", {"ip": "10.1.2.3", "netmask": "255.255.255.0", "gateway": None}),
    ("gateway: 10.1.2.1; mask: 255.255.0.0; addr: 10.1.2.3", {"ip": "10.1.2.3", "netmask": "255.255.0.0", "gateway": "10.1.2.1"}),
    ("10.1.2.3 255.255.255.0 10.1.2.1", {"ip": "10.1.2.3", "netmask": "255.255.255.0", "gateway": "10.1.2.1"}),
    ("10.1.2.3 10.1.2.1 255.255.255.0", None),  # the second address is no netmask: the order is not the one known
    ("10.1.2.3", None),
    ("1.2.3.4 255.255.255.0 1.2.3.1 9.9.9.9", None),
    ("ip=999.1.2.3 netmask=255.255.255.0", None),
    ("", None),
])
def test_parse_network(reply, expected):
    assert sensor_tool.parse_network(reply) == expected


def test_nothing_there_is_no_answer():
    fake = FakeSeyond(A)  # never started: nothing listens
    for command in ("probe", "get-net"):
        answer, status = run(fake, command, "--ip", A, expect="no-answer")
        assert status == 1 and "no Seyond answers" in answer["message"]
    answer, status = run(fake, "set-net", "--ip", A, *NET, expect="no-answer")
    assert status == 1 and answer["sessions"]["command"] == 0


def test_something_that_is_not_a_seyond_is_no_answer():
    with FakeSeyond(A, serial="") as fake:
        run(fake, "probe", "--ip", A, expect="no-answer")
        assert fake.received == []


# --- the rules ---------------------------------------------------------------------------------------


def test_every_write_needs_driver_stopped_and_sends_nothing_without_it():
    with FakeSeyond(A) as fake:
        for command, words in SETS.items():
            answer, status = run(fake, command, "--ip", A, *words, expect="refused", stopped=False)
            assert status == 1 and "--driver-stopped" in answer["message"]
            assert answer["sessions"] == {"command": 0, "http": 0, "max_concurrent": 0}
        assert fake.received == [] and fake.requests == [] and fake.connections == 0


def test_a_read_over_the_command_port_needs_driver_stopped_too():
    """TCP 8002 is the control session: get-net and the dry runs that read it are refused beside a
    running driver, before any connection."""
    with FakeSeyond(A) as fake:
        for argv in (["get-net", "--ip", A], ["set-net", "--ip", A, *NET, "--dry-run"], ["set-ptp", "--ip", A, *PTP, "--dry-run"]):
            answer, _ = run(fake, *argv, expect="refused", stopped=False)
            assert "--driver-stopped" in answer["message"] and answer["sessions"]["command"] == 0
        assert fake.received == [] and fake.connections == 0 and fake.requests == []


def test_the_reader_itself_refuses_the_command_port_beside_a_running_driver():
    with FakeSeyond(A) as fake:
        sensor_tool.PORTS.update(command=fake.command_port, http=fake.http_port)
        args = sensor_tool.parse_arguments(["get-net", "--ip", A])  # no --driver-stopped
        reader = sensor_tool.Reader(sensor_tool.Run(args, sensor_tool.blank("seyond"), sensor_tool.Sessions(("command",), ("http",))))
        for text in sensor_tool.READ_COMMANDS:
            with pytest.raises(sensor_tool.Stop) as stop:
                reader.command(A, text)
            assert stop.value.outcome == "refused" and "--driver-stopped" in stop.value.message
        assert fake.received == [] and fake.connections == 0


def test_reads_and_dry_runs_send_no_write():
    with FakeSeyond(A) as fake:
        run(fake, "probe", "--ip", A, expect="reported")
        run(fake, "get-net", "--ip", A, expect="reported")
        answer, status = run(fake, "set-net", "--ip", A, *NET, "--dry-run", expect="changes")
        assert status == 2 and answer["changed"] == ["net.ip"]
        answer, status = run(fake, "set-ptp", "--ip", A, *PTP, "--dry-run", expect="changes")
        assert status == 2 and answer["changed"] == ["ptp.ptp_automotive", "ptp.time_stamping"]
        answer, status = run(fake, "set-dest", "--ip", A, *DEST, "--dry-run", expect="unsupported")
        assert status == 3
        assert fake.writes() == [] and set(fake.received) <= set(sensor_tool.READ_COMMANDS)
        assert fake.received and all(path.startswith("/command/?get_") for path in fake.requests)
        assert fake.stored == fake.live == {"ip": A, "netmask": MASK, "gateway": "0.0.0.0"} and fake.time == {"ptp_automotive": 1, "time_stamping": 1}


def test_the_reading_code_cannot_send_a_write(monkeypatch):
    with FakeSeyond(A) as fake:
        sensor_tool.PORTS.update(command=fake.command_port, http=fake.http_port)
        args = sensor_tool.parse_arguments(["get-net", "--ip", A, "--driver-stopped"])
        reader = sensor_tool.Reader(sensor_tool.Run(args, sensor_tool.blank("seyond"), sensor_tool.Sessions(("command",), ("http",))))
        for text in (NUMBERS, f"set_network {B} {MASK} 0", "set_i_config time ptp_automotive 0", "reboot 1", "get_network\nreboot 1", ""):
            with pytest.raises(RuntimeError, match="the reader does not send"):
                reader.command(A, text)
        for name in ("reboot", "udp_ports_ip=1,1,1", "mode"):
            with pytest.raises(RuntimeError, match="the reader does not ask"):
                reader.http(A, name)
        assert fake.received == [] and fake.requests == [] and fake.connections == 0
        # The reader's own text has no write in it, and a run that reads never makes a Writer.
        source = inspect.getsource(sensor_tool.Reader)
        assert "set_" not in source and "reboot" not in source and "Writer" not in source

        def no_writer(*_args):
            raise AssertionError("a read made a Writer")
        monkeypatch.setattr(sensor_tool, "Writer", no_writer)
        run(fake, "probe", "--ip", A, expect="reported")
        run(fake, "get-net", "--ip", A, expect="reported")
        for command, words in SETS.items():
            run(fake, command, "--ip", A, *words, "--dry-run")
        assert fake.writes() == []


def test_a_writer_is_made_only_in_a_run_that_writes_with_the_driver_stopped():
    for argv in (["get-net", "--ip", A, "--driver-stopped"], ["set-net", "--ip", A, *NET, "--dry-run", "--driver-stopped"], ["set-net", "--ip", A, *NET]):
        args = sensor_tool.parse_arguments(argv)
        with pytest.raises(sensor_tool.Stop) as stop:
            sensor_tool.Writer(sensor_tool.Run(args, sensor_tool.blank("seyond"), sensor_tool.Sessions(("command",), ("http",))))
        assert stop.value.outcome == "refused"
    args = sensor_tool.parse_arguments(["set-ptp", "--ip", A, *PTP, "--driver-stopped"])
    writer = sensor_tool.Writer(sensor_tool.Run(args, sensor_tool.blank("seyond"), sensor_tool.Sessions(("command",), ("http",))))
    with pytest.raises(RuntimeError):  # the only [time] writes are the two keys, to 0 or 1
        writer.set_time(A, "ntp_en", 1)
    with pytest.raises(RuntimeError):
        writer.set_time(A, "time_stamping", 2)


def test_never_two_control_connections_at_once():
    with FakeSeyond(A, apply="restart") as fake:  # the longest run: reads, a write, a restart, reads at both addresses
        answer, _ = run(fake, "set-net", "--ip", A, *NET, expect="set")
        assert fake.connections == answer["sessions"]["command"] >= 5
        assert fake.max_open == 1 and answer["sessions"]["max_concurrent"] == 1
    sessions = sensor_tool.Sessions(("command",), ("http",))
    sessions.opening("command")
    sessions.opening("http")  # HTTP is not a control session
    with pytest.raises(RuntimeError, match="a second control connection"):
        sessions.opening("command")
    sessions.closed("command")
    sessions.opening("command")
    assert sessions.report() == {"command": 2, "http": 1, "max_concurrent": 1}  # the refused one was never opened


# --- set-dest ---------------------------------------------------------------------------------------


@pytest.mark.parametrize("extra", [[], ["--dry-run"]])
@pytest.mark.parametrize("dest", [DEST, ["--dest-ip", "192.0.2.9", "--data-port", "9000"]])
def test_set_dest_is_always_unsupported_and_contacts_nothing(extra, dest):
    with FakeSeyond(A) as fake:  # also when what is asked for is where the sensor sends now
        answer, status = run(fake, "set-dest", "--ip", A, *dest, *extra, expect="unsupported")
        assert status == 3 and answer["exit_code"] == 3
        assert [u["setting"] for u in answer["unsupported"]] == ["dest"] and "keeps no destination" in answer["unsupported"][0]["reason"]
        assert answer["changed"] == [] and answer["sessions"] == {"command": 0, "http": 0, "max_concurrent": 0}
        assert fake.received == [] and fake.requests == [] and fake.connections == 0
    answer, status = run(FakeSeyond(A), "set-dest", "--ip", A, *DEST, expect="unsupported")  # no sensor at all: the same answer
    assert status == 3


# --- set-ptp ----------------------------------------------------------------------------------------


def test_set_ptp_reads_writes_the_two_keys_reads_back_and_is_idempotent():
    with FakeSeyond(A) as fake:
        answer, status = run(fake, "set-ptp", "--ip", A, *PTP, expect="set")
        assert status == 0 and answer["verified"] is True and answer["answers_at"] == A and answer["reboot"] == "none"
        assert answer["changed"] == ["ptp.ptp_automotive", "ptp.time_stamping"] and answer["unsupported"] == []
        assert answer["wanted"] == {"ptp": {"ptp_automotive": 0, "time_stamping": 0}}
        assert answer["before"]["ptp"]["ptp_automotive"] == 1 and answer["before"]["ptp"]["transport"] == "L2"
        assert answer["after"]["ptp"]["ptp_automotive"] == 0 and answer["after"]["ptp"]["transport"] == "UDPv4"
        assert fake.received == ["get_i_config time", "get_status", "set_i_config time ptp_automotive 0", "set_i_config time time_stamping 0",
                                 "get_i_config time", "get_status"]
        assert fake.time == {"ptp_automotive": 0, "time_stamping": 0}
        assert any("time source is host" in w for w in answer["warnings"])  # said: nothing chooses the clock source
        before = len(fake.received)
        answer, status = run(fake, "set-ptp", "--ip", A, *PTP, expect="unchanged")
        assert status == 0 and answer["changed"] == [] and answer["verified"] is True
        assert fake.received[before:] == ["get_i_config time", "get_status"] and len(fake.writes()) == 2


def test_set_ptp_changes_only_what_differs():
    with FakeSeyond(A, automotive=0, stamping=1) as fake:
        answer, _ = run(fake, "set-ptp", "--ip", A, *PTP, expect="set")
        assert answer["changed"] == ["ptp.time_stamping"] and fake.writes() == ["set_i_config time time_stamping 0"]


@pytest.mark.parametrize("model, stamping, timescale, writes", [
    ("h", 0, "UTC", ["set_i_config time ptp_automotive 0", "set_i_config time time_stamping 1"]),
    ("w", 1, "TAI", ["set_i_config time ptp_automotive 0", "set_i_config time time_stamping 0"]),
    ("l", 1, "TAI", ["set_i_config time ptp_automotive 0", "set_i_config time time_stamping 0"]),
    ("x", 1, "TAI", ["set_i_config time ptp_automotive 0"]),  # an unknown model's time_stamping is left alone
])
def test_time_stamping_goes_by_the_model_the_sensor_says(model, stamping, timescale, writes):
    with FakeSeyond(A, model=model, stamping=stamping) as fake:
        words = [timescale if word in ("TAI", "UTC") else word for word in PTP]
        answer, _ = run(fake, "set-ptp", "--ip", A, *words, expect="set")
        assert fake.writes() == writes and answer["unsupported"] == []
        assert any("is not one whose time_stamping is known" in w for w in answer["warnings"]) == (model == "x")


def test_l2_and_peer_to_peer_are_unsupported_and_nothing_is_contacted():
    with FakeSeyond(A) as fake:
        for change, setting in ((("UDPv4", "L2"), "ptp.transport"), (("E2E", "P2P"), "ptp.delay_mechanism")):
            words = [change[1] if word == change[0] else word for word in PTP]
            answer, status = run(fake, "set-ptp", "--ip", A, *words, expect="unsupported")
            assert status == 3 and [u["setting"] for u in answer["unsupported"]] == [setting]
            assert answer["sessions"] == {"command": 0, "http": 0, "max_concurrent": 0}
        assert fake.received == [] and fake.requests == []


def test_a_domain_or_timescale_is_unsupported_only_when_another_one_is_asked_for():
    with FakeSeyond(A) as fake:
        words = ["5" if word == "0" else "UTC" if word == "TAI" else word for word in PTP]
        answer, status = run(fake, "set-ptp", "--ip", A, *words, "--dry-run", expect="changes")
        assert sorted(u["setting"] for u in answer["unsupported"]) == ["ptp.domain", "ptp.timescale"] and fake.writes() == []
        answer, status = run(fake, "set-ptp", "--ip", A, *words, expect="set")  # what can be set is set, and the rest is said
        assert status == 0 and sorted(u["setting"] for u in answer["unsupported"]) == ["ptp.domain", "ptp.timescale"]
        answer, _ = run(fake, "set-ptp", "--ip", A, *words, expect="unchanged")
        assert sorted(u["setting"] for u in answer["unsupported"]) == ["ptp.domain", "ptp.timescale"]
        answer, _ = run(fake, "set-ptp", "--ip", A, *PTP, expect="unchanged")  # domain 0 and TAI on a Robin-W: the sensor's own
        assert answer["unsupported"] == []


def test_a_sensor_that_does_not_take_the_time_settings_is_unverified():
    with FakeSeyond(A, takes_time=False) as fake:
        answer, status = run(fake, "set-ptp", "--ip", A, *PTP, expect="unverified", verify="0.3")
        assert status == 5 and answer["verified"] is False and answer["answers_at"] == A
        assert "ptp.ptp_automotive" in answer["message"] and len(fake.writes()) == 2


def test_a_sensor_that_stops_answering_after_a_write_is_unverified_not_no_answer():
    fake = FakeSeyond(A)
    with fake:
        fake.takes_time = False
        original = fake._reply

        def reply(text):
            answer = original(text)
            return (answer[0], fake.stop) if text.startswith("set_i_config") else answer
        fake._reply = reply
        answer, status = run(fake, "set-ptp", "--ip", A, *PTP, expect="unverified", verify="0.3")
        assert status == 5 and answer["verified"] is False


def test_a_sensor_without_the_time_settings_is_unsupported():
    with FakeSeyond(A, automotive=None, stamping=None) as fake:
        answer, status = run(fake, "set-ptp", "--ip", A, *PTP, expect="unsupported")
        assert status == 3 and [u["setting"] for u in answer["unsupported"]] == ["ptp"] and fake.writes() == []


# --- set-net ----------------------------------------------------------------------------------------


def test_set_net_moves_the_sensor_reads_back_and_is_idempotent():
    with FakeSeyond(A, apply="live") as fake:
        answer, status = run(fake, "set-net", "--ip", A, *NET, expect="set")
        assert status == 0 and answer["verified"] is True and answer["answers_at"] == B and answer["reboot"] == "none"
        assert answer["changed"] == ["net.ip"]
        assert answer["before"]["net"] == {"ip": A, "netmask": MASK, "gateway": None}
        assert answer["wanted"] == {"net": {"ip": B, "netmask": MASK, "gateway": "0.0.0.0"}}
        assert answer["after"]["net"] == {"ip": B, "netmask": MASK, "gateway": None}
        assert fake.writes() == [NUMBERS]  # the vendor tool's first form, once, and no restart
        assert fake.received[0] == "get_network" and fake.received[1] == NUMBERS
        before = len(fake.received)
        answer, status = run(fake, "set-net", "--ip", B, *NET, expect="unchanged")
        assert status == 0 and answer["changed"] == [] and answer["answers_at"] == B
        assert fake.received[before:] == ["get_network"] and fake.writes() == [NUMBERS]


def test_an_address_that_is_only_stored_is_taken_with_a_restart():
    with FakeSeyond(A, apply="restart") as fake:
        answer, _ = run(fake, "set-net", "--ip", A, *NET, expect="set")
        assert answer["reboot"] == "sent" and answer["answers_at"] == B and answer["verified"] is True
        assert fake.writes() == [NUMBERS, "reboot 1"] and fake.ip == B


def test_no_reboot_leaves_a_stored_address_unverified_and_says_a_restart_is_needed():
    with FakeSeyond(A, apply="restart") as fake:
        answer, status = run(fake, "set-net", "--ip", A, *NET, "--no-reboot", expect="unverified")
        assert status == 5 and answer["reboot"] == "needed" and answer["answers_at"] == A and answer["verified"] is False
        assert fake.writes() == [NUMBERS] and fake.ip == A


def test_the_three_address_form_is_sent_only_after_invalid_command():
    with FakeSeyond(A, numbers=False) as fake:
        answer, _ = run(fake, "set-net", "--ip", A, "--new-ip", B, "--netmask", MASK, "--gateway", "127.0.0.254", expect="set")
        assert fake.writes() == ["set_network 127 0 61 2 255 0 0 0 127 0 0 254", f"set_network {B} {MASK} 127.0.0.254"]
        assert answer["after"]["net"]["gateway"] == "127.0.0.254" and answer["changed"] == ["net.ip", "net.gateway"]
    with FakeSeyond(A, numbers=False) as fake:  # no gateway is "0" in that form, as the vendor's tool sends it
        run(fake, "set-net", "--ip", A, *NET, expect="set")
        assert fake.writes() == [NUMBERS, f"set_network {B} {MASK} 0"]


def test_a_sensor_that_knows_neither_form_is_unsupported():
    with FakeSeyond(A, numbers=False, strings=False) as fake:
        answer, status = run(fake, "set-net", "--ip", A, *NET, expect="unsupported")
        assert status == 3 and [u["setting"] for u in answer["unsupported"]] == ["net"] and fake.ip == A
        assert fake.stored == fake.live


def test_a_sensor_that_keeps_its_old_settings_fails():
    with FakeSeyond(A, apply="never") as fake:
        answer, status = run(fake, "set-net", "--ip", A, *NET, expect="failed")
        assert status == 1 and answer["answers_at"] == A and answer["verified"] is False and "still reports its old settings" in answer["message"]
        assert "reboot 1" not in fake.received


def test_a_sensor_that_answers_nowhere_afterwards_is_unverified():
    with FakeSeyond(A, apply="vanish") as fake:
        answer, status = run(fake, "set-net", "--ip", A, *NET, expect="unverified", verify="0.5")
        assert status == 5 and answer["answers_at"] is None and answer["after"] is None and "neither address" in answer["message"]


def test_a_restart_that_never_brings_the_new_address_is_unverified():
    with FakeSeyond(A, apply="restart", reboots=False) as fake:
        answer, status = run(fake, "set-net", "--ip", A, *NET, expect="unverified", verify="0.5")
        assert status == 5 and answer["reboot"] == "sent" and answer["answers_at"] == A
        assert fake.received.count("reboot 1") == 1  # sent once, not at every look


def test_an_address_another_sensor_answers_at_is_refused_before_anything_is_sent():
    ports = free_ports()
    with FakeSeyond(A, ports=ports) as fake, FakeSeyond(B, ports=ports, serial="SEY-OTHER-0002") as other:
        answer, status = run(fake, "set-net", "--ip", A, *NET, expect="refused")
        assert status == 1 and "SEY-OTHER-0002" in answer["message"] and fake.writes() == [] and other.received == []


def test_another_sensor_at_the_new_address_afterwards_fails():
    ports = free_ports()
    other = FakeSeyond(B, ports=ports, serial="SEY-OTHER-0002")
    with FakeSeyond(A, ports=ports, apply="vanish", on_set_network=other._listen) as fake:
        try:
            answer, status = run(fake, "set-net", "--ip", A, *NET, expect="failed")
        finally:
            other.stop()
        assert status == 1 and "another sensor answers" in answer["message"] and other.writes() == []


def test_a_netmask_or_gateway_alone_is_set_at_the_same_address():
    with FakeSeyond(A, apply="restart") as fake:
        words = ["--new-ip", A, "--netmask", "255.255.0.0", "--gateway", "127.0.0.254"]
        answer, _ = run(fake, "set-net", "--ip", A, *words, expect="set")
        assert answer["changed"] == ["net.netmask", "net.gateway"] and answer["answers_at"] == A and answer["reboot"] == "none"
        assert any("before its next restart is not known" in w for w in answer["warnings"])
        assert fake.writes() == ["set_network 127 0 61 1 255 255 0 0 127 0 0 254"]
        run(fake, "set-net", "--ip", A, *words, expect="unchanged")
    with FakeSeyond(A, apply="never") as fake:
        answer, _ = run(fake, "set-net", "--ip", A, *words, expect="failed")
        assert answer["answers_at"] == A


def test_a_sensor_whose_get_network_is_not_understood():
    """Its address can still be moved, and is proven by the same serial number answering at the new
    one; with the address not to change there is nothing to compare, and nothing is sent."""
    with FakeSeyond(A, network_reply="garbage") as fake:
        answer, status = run(fake, "set-net", "--ip", A, "--new-ip", A, "--netmask", MASK, "--gateway", "0.0.0.0", expect="refused")
        assert status == 1 and "nothing was sent" in answer["message"] and fake.writes() == []
        answer, _ = run(fake, "set-net", "--ip", A, *NET, expect="set")
        assert answer["changed"] == ["net.ip", "net.netmask", "net.gateway"] and answer["answers_at"] == B
        assert answer["after"]["net"] == {"ip": B, "netmask": None, "gateway": None}
        assert any("could not be read back" in w for w in answer["warnings"])


def test_set_net_refusals_send_nothing():
    with FakeSeyond(A) as fake:
        for words, why in (
            (["--new-ip", "198.51.100.5", "--netmask", "255.255.255.0", "--gateway", "0.0.0.0"], "this host has no address in"),
            (["--new-ip", B, "--netmask", MASK, "--gateway", "10.0.0.1"], "is not another address of"),
            (["--new-ip", B, "--netmask", MASK, "--gateway", B], "is not another address of"),
            (["--new-ip", "127.255.255.255", "--netmask", MASK, "--gateway", "0.0.0.0"], "network or broadcast address"),
        ):
            for extra in ([], ["--dry-run"]):
                answer, status = run(fake, "set-net", "--ip", A, *words, *extra, expect="refused")
                assert status == 1 and why in answer["message"], answer["message"]
        answer, status = run(fake, "set-net", "--ip", A, "--new-ip", B, "--netmask", "255.0.255.0", "--gateway", "0.0.0.0", expect="refused")
        assert "usage" in answer["message"]
        assert fake.writes() == [] and fake.ip == A


# --- the answer ---------------------------------------------------------------------------------------


def test_the_arguments_metrolla_hostd_gives_are_taken():
    """sensor_program.tool_arguments (metrolla-edge): the model first, the stopped driver, a device
    port for a vendor that has one, then the values and the verify timeout."""
    with FakeSeyond(A) as fake:
        sensor_tool.PORTS.update(command=fake.command_port, http=fake.http_port)
        head = ["--ip", A, "--model", "robin-w", "--driver-stopped"]
        for argv, outcome in (
            (["probe", *head], "reported"),
            (["get-net", *head, "--dev-port", "8010"], "reported"),
            (["set-ptp", *head, *PTP, "--verify-timeout", "120", "--dry-run"], "changes"),
            (["set-dest", *head, *DEST, "--verify-timeout", "120"], "unsupported"),
            (["set-net", *head, *NET, "--verify-timeout", "120", "--dry-run"], "changes"),
        ):
            printed = io.StringIO()
            status = sensor_tool.main(argv, stdout=printed)
            assert hostd_check(status, printed.getvalue(), "seyond", argv[0])["outcome"] == outcome


def test_outcomes_and_exit_statuses_are_the_interfaces():
    assert sensor_tool.EXIT == HOSTD_OUTCOMES and sensor_tool.INTERFACE == 1 and sensor_tool.VENDOR == "seyond"
    assert sensor_tool.COMMANDS == ("probe", "get-net", "set-net", "set-dest", "set-ptp")
    assert set(sensor_tool.blank("seyond")) == KEYS


def test_it_prints_one_json_object_also_for_a_usage_error():
    for argv in ([], ["probe"], ["set-net", "--ip", "not-an-address"], ["explode", "--ip", A], ["probe", "--ip", A, "--frobnicate"],
                 ["set-ptp", "--ip", A, "--transport", "carrier-pigeon"]):
        done = subprocess.run([sys.executable, "-I", str(TOOL), *argv], capture_output=True, text=True, timeout=20, check=False)
        assert done.returncode == 1 and done.stderr == ""
        answer = json.loads(done.stdout)
        assert len(done.stdout.splitlines()) == 1 and set(answer) == KEYS
        assert (answer["sensor_tool"], answer["vendor"], answer["outcome"], answer["exit_code"]) == (1, "seyond", "refused", 1)
        assert answer["message"].startswith("usage:") and answer["sessions"] == {"command": 0, "http": 0, "max_concurrent": 0}


def test_a_fault_of_the_tool_is_one_json_object_too(monkeypatch):
    def broken(_run):
        raise ZeroDivisionError("a fault")
    monkeypatch.setitem(sensor_tool.HANDLERS, "probe", broken)
    answer, status = run(FakeSeyond(A), "probe", "--ip", A, expect="failed")
    assert status == 1 and "ZeroDivisionError" in answer["message"]


def test_it_is_standard_library_python_started_isolated():
    text = TOOL.read_text()
    assert text.startswith("#!/usr/bin/python3 -I\n") and TOOL.stat().st_mode & 0o111
    imports = {line.split()[1].split(".")[0] for line in text.splitlines() if line.startswith(("import ", "from "))}
    assert imports <= {"__future__", "argparse", "http", "ipaddress", "json", "re", "socket", "sys", "time"}


def test_the_interface_part_is_the_shared_text():
    """The part between the two marks is the same text in every driver repository's sensor-tool
    (HesaiLidar_ROS_2.0, lslidar_ros2_driver, ouster-ros, this one). A change here is a change there."""
    text = TOOL.read_text()
    start = text.index("# ---- the interface: this part is the same text in every driver repository's sensor-tool")
    shared = text[start:text.index("# ---- Seyond: the two channels")]
    assert hashlib.sha256(shared.encode()).hexdigest() == "a5a54aa59c939cd51e60bef9de6b49de4934e0f8750c52d15cf6d225c6c9ea58"


# --- the sources the tool cites are still what it says ---------------------------------------------------


def sdk(path: str) -> str:
    return (SDK / path).read_text(encoding="utf-8", errors="replace")


def test_the_sdk_still_says_what_the_tool_cites():
    assert "kDefaultLidarPort = 8002" in sdk("src/utils/net_manager.h")
    assert "uint16_t lidar_port = 8010" in sdk("apps/example/demo.cpp")
    common = sdk("src/sdk_common/lidar_communication.cpp")
    for fact in ('"%s %s %s", "get_i_config", section, key', '"[%s]\\n%s = %%lf"', '"%s %s %s %s", "set_i_config", section, key, value',
                 '"reboot %d"', '"time_config=%d up_time=', '"get_status"', '"model: %c"', "w:robinw, l: robine-lite"):
        assert fact in common, fact
    client = sdk("src/sdk_client/lidar_client_communication.cpp")
    for fact in ('"/command/?get_%s"', 'send_get_command_("sn"', 'send_get_command_("model"', 'send_get_command_("fw_version"',
                 'send_get_command_("udp_ports_ip"', '"%d,%d,%d,%32s"', '"udp_ports_ip=%hu,%hu,%hu", port, port, port'):
        assert fact in client, fact
    assert "comm_->set_server_udp_ports_ip(inputParam_.udp_param.udp_port)" in sdk("src/sdk_client/lidar_client.cpp")
    packet = sdk("src/sdk_common/inno_lidar_packet.h")
    for value, name in enumerate(("HOST", "PTP", "GPS", "FILE", "NTP")):
        assert f"INNO_TIME_SYNC_CONFIG_{name} = {value}," in packet
        assert sensor_tool.TIME_SOURCES[value] == name.lower()
    manager = sdk("src/utils/net_manager.cpp")
    assert "buffer[slen] = '\\n';" in manager and "if (reply[i] == '\\n' && reply[i+1] == '\\n')" in manager


def test_the_vendor_tool_still_has_what_set_network_was_taken_from():
    binary = (SDK / "apps/tools/lidar_util/innovusion_lidar_util").read_bytes()
    assert hashlib.sha256(binary).hexdigest() == "8a3e489bc0865ae497b00b66dfb4bb784c5ada7a14c4e3b5a4ea70f818c1ff3d"
    for text in (b"set_network %u %u %u %u %u %u %u %u %u %u %u %u\0", b"set_network %s %s %s\0", b"Invalid command\0", b"get_network\0",
                 b"set_network <ip> <netmask> [gateway]", b"_Z18handle_set_networkRK11CommandDataiPPKc", b"reboot 1\0"):
        assert text in binary, text
    assert sensor_tool.INVALID.encode() + b"\0" in binary

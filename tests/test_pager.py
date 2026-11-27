import struct
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "payload" / "quartermaster"))

from qmpager import buttons, hci, sound  # noqa: E402
from qmpager.listen import PagerListen  # noqa: E402


def test_short_press_is_ok_and_back():
    pad = buttons.PagerInput.__new__(buttons.PagerInput)
    pad.down, pad.fired, pad.quit, pad.on_quit = {}, set(), False, None
    keys = pad.feed([("A", 1), ("A", 0)], 0.0)
    assert [k.name for k in keys] == ["ENTER"]
    keys = pad.feed([("B", 1), ("B", 0)], 1.0)
    assert [k.name for k in keys] == ["ESC"]


def test_holding_green_opens_actions_once_and_red_quits():
    pad = buttons.PagerInput.__new__(buttons.PagerInput)
    pad.down, pad.fired, pad.quit = {}, set(), False
    quits = []
    pad.on_quit = lambda: quits.append(1)
    pad.feed([("A", 1)], 0.0)
    assert [k.name for k in pad.feed([], 0.7)] == ["TAB"]
    assert pad.feed([], 0.9) == []
    assert pad.feed([("A", 0)], 1.0) == []
    pad.feed([("B", 1)], 2.0)
    pad.feed([], 4.1)
    assert quits == [1]


def test_arrows_repeat_while_held():
    pad = buttons.PagerInput.__new__(buttons.PagerInput)
    pad.down, pad.fired, pad.quit, pad.on_quit = {}, set(), False, None
    assert [k.name for k in pad.feed([("DOWN", 1)], 0.0)] == ["DOWN"]
    assert [k.repeat for k in pad.feed([], 0.5)] == [True]
    assert pad.feed([], 0.52) == []


def test_every_tone_steps_through_the_buzzer():
    from quartermaster import tones
    for tone in tones.TONES:
        list(sound.steps(tone))
    hz = [round(h) for h, _, _ in sound.steps("siren")]
    assert min(hz) >= 600 and max(hz) <= 1480


class FakeBuzzer:
    def __init__(self):
        self.calls = []

    def present(self):
        return True

    def on(self, hz, level=255):
        self.calls.append(("on", int(hz)))

    def off(self):
        self.calls.append(("off",))


class FakeVibrator:
    def __init__(self):
        self.states = []

    def set(self, on):
        self.states.append(on)


def test_buzzer_player_plays_and_ends_silent():
    buzzer, motor = FakeBuzzer(), FakeVibrator()
    player = sound.BuzzerPlayer(buzzer, motor, sleep=lambda s: None)
    assert player.available()
    run = player.spawn(["buzzer", "-q", "chirp:medium"])
    run.thread.join(2)
    assert run.poll() == 0
    assert buzzer.calls[-1] == ("off",) and motor.states[-1] is False
    assert any(c[0] == "on" for c in buzzer.calls)


def advert(address, data, rssi=-60, address_type=1):
    report = bytes([0, address_type]) + bytes.fromhex(address.replace(":", ""))[::-1] + bytes([len(data)]) + data + \
        struct.pack("b", rssi)
    body = bytes([hci.LE_ADVERTISING_REPORT, 1]) + report
    return bytes([hci.HCI_EVENT_PKT, hci.EVT_LE_META, len(body)]) + body


def test_advertising_reports_become_bluez_style_records():
    data = bytes([2, 1, 6]) + bytes([7, 9]) + b"Flock1" + bytes([3, 3, 0x6F, 0xFD]) + \
        bytes([5, 0xFF, 0x4C, 0x00, 0x12, 0x19])
    packet = advert("B4:1E:52:3A:10:07", data, -71)
    [(address, kind, rssi, raw)] = hci.reports(packet)
    assert hci.address_text(address) == "B4:1E:52:3A:10:07" and rssi == -71 and kind == 1
    ad = hci.parse_ad(raw)
    assert ad["name"] == "Flock1"
    assert ad["uuids"] == ["0000fd6f-0000-1000-8000-00805f9b34fb"]
    assert ad["manufacturer"] == {0x004C: b"\x12\x19"}


class FakeHci:
    def __init__(self, packets):
        self.packets, self.sent = list(packets), []

    def setblocking(self, flag):
        pass

    def send(self, data):
        self.sent.append(data)

    def recv(self, size):
        if not self.packets:
            raise BlockingIOError
        return self.packets.pop(0)

    def fileno(self):
        return 0

    def close(self):
        pass


def test_hci_scanner_keeps_latest_of_each_device(monkeypatch):
    fake = FakeHci([advert("AA:BB:CC:DD:EE:01", bytes([4, 9]) + b"abc", -80),
                    advert("AA:BB:CC:DD:EE:01", b"", -50)])
    monkeypatch.setattr(hci.select, "select", lambda r, w, x, t: (r if fake.packets else [], [], []))
    scanner = hci.HciBleScanner(opener=lambda: fake)
    [record] = scanner.scan()
    assert record["address"] == "AA:BB:CC:DD:EE:01" and record["name"] == "abc" and record["rssi"] == -50
    assert fake.sent[-1] == hci.command(hci.SET_SCAN_ENABLE, b"\x01\x00")
    scanner.close()
    assert fake.sent[-1] == hci.command(hci.SET_SCAN_ENABLE, b"\x00\x00")


def test_listen_hands_networks_and_flock_frames_on():
    got = {"networks": [], "frame": []}
    deep = PagerListen({})
    deep.sinks = {"networks": lambda n, a: got["networks"].append((n, a)), "frame": got["frame"].append}
    deep.monitor = {"interface": "wlan1mon"}
    deep.handle("networks", [{"bssid": "AA"}])
    deep.handle("frame", {"rule": "probe"})
    assert got["networks"][0][1]["interface"] == "wlan1mon" and deep.counts == {"flock": 1, "deauth": 0, "networks": 1}
    assert deep.managed([{"interface": "wlan1mon"}, {"interface": "wlan2"}]) == [{"interface": "wlan2"}]


def beacon(bssid, ssid, channel=6, freq=2437, rssi=-55, privacy=True):
    tap = struct.pack("<BBHI", 0, 0, 13, (1 << 3) | (1 << 5)) + struct.pack("<HHb", freq, 0x00A0, rssi)
    mac = bytes.fromhex(bssid.replace(":", ""))
    head = b"\x80\x00\x00\x00" + b"\xff" * 6 + mac + mac + b"\x00\x00"
    fixed = b"\x00" * 8 + struct.pack("<HH", 100, 0x0011 if privacy else 0x0001)
    ies = bytes([0, len(ssid)]) + ssid.encode() + bytes([3, 1, channel])
    return tap + head + fixed + ies


class FakeMonitorSocket:
    def __init__(self, frames):
        self.frames = list(frames)

    def settimeout(self, value):
        pass

    def recv(self, size):
        if self.frames:
            return self.frames.pop(0)
        raise EOFError


class Outbox:
    def __init__(self):
        self.sent = []

    def send(self, item):
        self.sent.append(item)
        if item[0] == "networks":
            raise BrokenPipeError


def test_monitor_capture_turns_beacons_into_networks(monkeypatch):
    from qmpager import listen
    clock = iter(x * 0.6 for x in range(100))
    monkeypatch.setattr(listen.time, "monotonic", lambda: next(clock))
    sock = FakeMonitorSocket([beacon("AA:BB:CC:00:00:01", "Black Pearl")] * 3)
    out = Outbox()
    try:
        listen.capture(out, "wlan1mon", 4, opener=lambda: sock)
    except EOFError:
        pass
    networks = [item for kind, item in out.sent if kind == "networks"]
    assert networks, out.sent
    [net] = networks[0]
    assert net["bssid"] == "AA:BB:CC:00:00:01" and net["ssid"] == "Black Pearl" and net["channel"] == 6
    assert net["rssi"] == -55.0 and net["security"]


def test_install_swaps_in_the_pager_parts():
    import subprocess
    code = ("import qmpager.__main__ as m; m.install();"
            "from quartermaster import engine, recon_engine, tones;"
            "from qmpager import hci, listen, sound;"
            "assert engine.BleScanner is hci.HciBleScanner and recon_engine.BleScanner is hci.HciBleScanner;"
            "assert listen.PagerListen in engine.Wardrive.__init__.__defaults__ and listen.PagerListen in recon_engine.Recon.__init__.__defaults__;"
            "assert tones.Player is sound.BuzzerPlayer; print('ok')")
    result = subprocess.run([sys.executable, "-c", code], cwd=str(ROOT / "payload" / "quartermaster"),
                            capture_output=True, text=True, env={"QUARTERMASTER_HOME": "/tmp/qm-test", "PATH": "/usr/bin:/bin"})
    assert result.stdout.strip() == "ok", result.stderr

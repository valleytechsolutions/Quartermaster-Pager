import struct
import sys
import threading
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "payload" / "quartermaster"))

from qmpager import biscuit, listen  # noqa: E402

PAGER = bytes.fromhex("001337000001")
BISCUIT = bytes.fromhex("24ec4a0000b1")
OTHER = bytes.fromhex("24ec4a0000c2")
NET6 = bytes.fromhex("aa0000000006")
NET24 = bytes.fromhex("aa0000000024")


def radiotap(mhz, rssi):
    return struct.pack("<BBHI", 0, 0, 13, 1 << 3 | 1 << 5) + struct.pack("<HHb", mhz, 0, rssi)


def beacon(bssid, ssid, sae=True):
    head = struct.pack("<HH6s6s6sH", 0x0080, 0, b"\xff" * 6, bssid, bssid, 0) + b"\0" * 8
    head += struct.pack("<HH", 100, 0x0011)
    rsn = struct.pack("<H", 1) + b"\x00\x0f\xac\x04" + struct.pack("<H", 1) + b"\x00\x0f\xac\x04" + \
        struct.pack("<H", 1) + (b"\x00\x0f\xac\x08" if sae else b"\x00\x0f\xac\x02") + b"\0\0"
    return head + bytes([0, len(ssid)]) + ssid + bytes([48, len(rsn)]) + rsn


class FakeBiscuit:
    def __init__(self, reply=b"wdbatch=1 nodecfg=1 ap6=1", mac=BISCUIT):
        self.reply, self.mac = reply, mac
        self.firmware, self.texts, self.acks, self.records, self.heartbeats = [], [], [], [], 0
        self.assigned = False
        self.seq = 0

    def heard(self, dst, payload, out):
        kind, counter, text = biscuit.read_message(payload) if payload[4] != biscuit.WARDRIVE_BATCH else \
            (biscuit.WARDRIVE_BATCH, 0, payload)
        if kind == biscuit.CORE_REQUEST and dst == biscuit.BROADCAST:
            out.append(self.say(biscuit.message(biscuit.CORE_REPLY, 0, self.reply)))
        if dst != self.mac:
            return
        if kind == biscuit.FW_VERSION:
            self.firmware.append(text)
            if not self.assigned:
                self.assigned = True
                out.append(self.say(biscuit.message(biscuit.ROLE_ASSIGN, 0, b"\x01TOKN")))
        elif kind == biscuit.TEXT:
            self.texts.append(text)
        elif kind == biscuit.ROLE_ACK:
            self.acks.append((counter, text))
        elif kind == biscuit.HEARTBEAT:
            self.heartbeats += 1
        elif kind == biscuit.WARDRIVE_BATCH:
            body, count, at = payload[8:], payload[7], 0
            for _ in range(count):
                size = body[at + 10]
                self.records.append(body[at:at + 11 + size])
                at += 11 + size

    def say(self, payload, seq=None):
        self.seq += 1
        return radiotap(2437, -40) + biscuit.action_frame(PAGER, self.mac, self.seq if seq is None else seq, payload,
                                                          nonce=b"1234")


class FakeRadio:
    def __init__(self, biscuits, limit=30.0):
        self.mac = PAGER
        self.biscuits = biscuits
        self.now = 0.0
        self.limit = limit
        self.freq = None
        self.tuned = []
        self.sent = []
        self.queue = []
        self.node = None

    def clock(self):
        return self.now

    def tune(self, mhz):
        self.freq = mhz
        self.tuned.append(mhz)
        return True

    def send(self, dst, payload):
        self.sent.append((dst, payload, self.freq))
        if self.freq == 2437:
            for one in self.biscuits:
                one.heard(dst, payload, self.queue)
        return True

    def recv(self, timeout):
        self.now += max(timeout, 0.01)
        if self.node is not None and self.now > self.limit:
            self.node.stopped = True
        out = []
        if self.freq == 2437:
            out, self.queue = self.queue, []
            out.append(radiotap(2437, -50) + beacon(NET24, b"two-four"))
        elif self.freq == 5975:
            out.append(radiotap(5975, -60) + beacon(NET6, b"six-gig"))
        return out


def run_node(biscuits, limit=30.0):
    radio = FakeRadio(biscuits, limit)
    reports = []
    node = biscuit.Node(radio, reports.append, clock=radio.clock)
    radio.node = node
    node.run()
    return node, radio, reports


def test_message_and_frame_round_trip():
    payload = biscuit.message(biscuit.HEARTBEAT, 7, b"hi")
    assert len(payload) == 212
    assert biscuit.read_message(payload) == (biscuit.HEARTBEAT, 7, b"hi")
    frame = biscuit.action_frame(BISCUIT, PAGER, 5, payload, nonce=b"abcd")
    assert biscuit.read_action(frame) == (BISCUIT, PAGER, 5, payload)
    assert biscuit.read_action(frame[:30]) is None


def test_batches_stay_within_one_frame():
    records = [(bytes([i]) * 6, biscuit.record(bytes([i]) * 6, 37, -70, 4, b"x" * 32)) for i in range(20)]
    seq, packed = biscuit.batches(254, records)
    assert all(len(payload) <= 250 for payload, _ in packed)
    assert sum(len(ids) for _, ids in packed) == 20
    assert [payload[6] for payload, _ in packed] == [(254 + i) & 0xFF for i in range(len(packed))]
    assert seq == (254 + len(packed)) & 0xFF


class SilentBiscuit(FakeBiscuit):
    """A Biscuit whose owner has not searched for nodes in the Biscuit Manager app yet: it answers nobody."""

    def __init__(self, answer_after=None, **kwargs):
        super().__init__(**kwargs)
        self.answer_after, self.requests = answer_after, 0

    def heard(self, dst, payload, out):
        if biscuit.read_message(payload)[0] == biscuit.CORE_REQUEST:
            self.requests += 1
            if self.answer_after is None or self.requests < self.answer_after:
                return
        super().heard(dst, payload, out)


def test_like_codehedges_node_the_pager_asks_until_the_biscuit_adds_it_and_never_pairs_by_itself():
    nobody = SilentBiscuit()
    node, radio, _ = run_node([nobody], limit=3)
    assert node.core is None and node.state == biscuit.SEARCHING
    requests = [p for dst, p, _ in radio.sent if biscuit.read_message(p)[0] == biscuit.CORE_REQUEST]
    assert len(requests) >= 5 and all(biscuit.read_message(p)[2] == b"bsc=1" for p in requests)
    assert all(dst == biscuit.BROADCAST for dst, _, _ in radio.sent)
    later = SilentBiscuit(answer_after=5)
    node, _, _ = run_node([later], limit=10)
    assert node.core == BISCUIT and node.state == biscuit.SCANNING


def test_the_first_biscuit_that_takes_it_keeps_it():
    first, second = FakeBiscuit(), FakeBiscuit(mac=OTHER)
    node, radio, _ = run_node([first, second], limit=6)
    assert node.core == BISCUIT
    assert not second.firmware and all(dst != OTHER for dst, _, _ in radio.sent)


def test_node_reports_as_pager_and_sends_only_what_codehedges_node_sends():
    fake = FakeBiscuit()
    node, radio, reports = run_node([fake])
    kinds = {biscuit.read_message(p)[0] if p[4] != biscuit.WARDRIVE_BATCH else biscuit.WARDRIVE_BATCH
             for _, p, _ in radio.sent}
    assert kinds <= {biscuit.CORE_REQUEST, biscuit.FW_VERSION, biscuit.HEARTBEAT, biscuit.ROLE_ACK,
                     biscuit.WARDRIVE_BATCH, biscuit.CONFIG_ACK, biscuit.STOP_ACK}
    assert fake.texts == []
    assert len(fake.firmware) >= 3 and set(fake.firmware) == {b"Pager:v1.1.0"}


def test_node_scans_only_6ghz():
    fake = FakeBiscuit()
    node, radio, reports = run_node([fake, FakeBiscuit(mac=OTHER)])
    assert node.core == BISCUIT
    assert fake.firmware[0] == b"Pager:v1.1.0"
    assert fake.acks[0] == (1, b"TOKN")
    assert node.state == biscuit.SCANNING
    assert all(m == 2437 or 5925 <= m <= 7125 for m in radio.tuned)
    sent = [r[1:7] for r in fake.records]
    assert NET6 in sent and NET24 not in sent
    first = next(r for r in fake.records if r[1:7] == NET6)
    assert first[0] == biscuit.AP6_RECORD and first[7] == 5 and first[9] == 4 and first[11:] == b"six-gig"
    assert 1 < sent.count(NET6) <= 30 / biscuit.RESEND_SECONDS + 1
    status = [r[1] for r in reports if r[0] == "status"][-1]
    assert status["final"] and status["sent"] == 1 and status["heard"] == 1
    assert all(dst in (BISCUIT, biscuit.BROADCAST) for dst, _, _ in radio.sent)
    assert all(freq == 2437 for _, _, freq in radio.sent)


def test_secure_biscuit_is_not_joined():
    node, radio, _ = run_node([FakeBiscuit(b"wdbatch=1 encrypt=1")], limit=3)
    assert node.core is None and node.state == biscuit.SECURE
    requests = [p for _, p, _ in radio.sent if biscuit.read_message(p)[0] == biscuit.CORE_REQUEST]
    assert len(requests) >= 5


def test_old_biscuit_firmware_gets_no_scan():
    fake = FakeBiscuit(b"wdbatch=1")
    node, radio, _ = run_node([fake], limit=6)
    assert node.state == biscuit.OLD
    assert set(radio.tuned) == {2437}
    assert fake.records == []


def test_auth_codes():
    assert biscuit.auth_code({"rsn": {"auth": ["SAE"]}}) == 4
    assert biscuit.auth_code({"rsn": {"auth": ["PSK", "SAE"]}}) == 9
    assert biscuit.auth_code({"rsn": {"auth": ["OWE"]}}) == 0x0D
    assert biscuit.auth_code({"rsn": {"auth": ["IEEE 802.1X"]}}) == 6
    assert biscuit.auth_code({"rsn": {"auth": ["IEEE 802.1X/SUITE-B-192"]}}) == 7
    assert biscuit.auth_code({"rsn": {"auth": ["PSK"]}, "wpa": {"auth": ["PSK"]}}) == 5
    assert biscuit.auth_code({"privacy": True}) == 1
    assert biscuit.auth_code({}) == 0


def bpf(code, packet):
    a = x = pc = 0
    while True:
        op, jt, jf, k = code[pc]
        pc += 1
        if op == 0x30:
            a = packet[k] if k < len(packet) else None
        elif op == 0x50:
            a = packet[x + k] if x + k < len(packet) else None
        elif op == 0x40:
            a = struct.unpack(">I", packet[x + k:x + k + 4])[0]
        elif op == 0x64:
            a = (a << k) & 0xFFFFFFFF
        elif op == 0x0C:
            a = (a + x) & 0xFFFFFFFF
        elif op == 0x54:
            a &= k
        elif op == 0x07:
            x = a
        elif op == 0x15:
            pc += jt if a == k else jf
        elif op == 0x06:
            return k
        else:
            raise AssertionError("opcode %#x" % op)
        if a is None:
            return 0


def test_node_filter_keeps_what_node_mode_reads():
    code = biscuit.FILTER
    assert bpf(code, radiotap(5975, -60) + beacon(NET6, b"x"))
    assert bpf(code, radiotap(2437, -40) + biscuit.action_frame(PAGER, BISCUIT, 1, biscuit.message(1)))
    assert bpf(code, radiotap(2437, -40) + b"\xd4\x00\x00\x00" + PAGER)
    assert not bpf(code, radiotap(2437, -40) + b"\x08\x00" + b"\0" * 30)


def test_wardrive_filter_reads_the_frame_after_a_long_radiotap_header():
    long_header = struct.pack("<BBHI", 0, 0, 56, 0) + b"\0" * 48
    frame = long_header + beacon(NET6, b"x")
    assert bpf(listen.program(), frame)
    assert listen.run(listen.program(), frame)
    data = long_header + struct.pack("<HH6s6s6sH", 0x0008, 0, b"\x11" * 6, b"\x22" * 6, b"\x33" * 6, 0)
    assert not bpf(listen.program(), data)
    assert not listen.run(listen.program(), data)


class FakeProcess:
    def __init__(self, link_events):
        self.alive = True
        self.events = link_events

    def terminate(self):
        self.events.append("terminate")
        self.alive = False

    def join(self, timeout=None):
        pass

    def is_alive(self):
        return self.alive

    def kill(self):
        self.alive = False


class FakePipe:
    def __init__(self, items, proc):
        self.items, self.proc = list(items), proc

    def poll(self, timeout):
        if self.items:
            return True
        if not self.proc.alive:
            raise EOFError
        time.sleep(0.01)
        return False

    def recv(self):
        return self.items.pop(0)

    def close(self):
        pass


def wait_for(check, seconds=3):
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        if check():
            return True
        time.sleep(0.01)
    return False


def test_link_holds_and_hands_back_the_radio():
    events = []

    def factory(interface):
        events.append(("spawn", interface))
        proc = FakeProcess(events)
        return proc, FakePipe([("status", {"state": biscuit.SCANNING, "heard": 3})], proc)
    link = biscuit.BiscuitLink(process=factory, hold=lambda: events.append("hold"),
                               release=lambda: events.append("release"), present=lambda: True)
    assert link.start()
    assert wait_for(lambda: link.state == biscuit.SCANNING)
    assert not link.start()
    link.stop(wait=True)
    assert link.state == biscuit.OFF
    assert events[0] == "hold" and events[1] == ("spawn", "wlan1mon") and events[-1] == "release"


def test_link_starts_with_no_pairing_and_reports_a_node_that_died():
    events = []

    def factory(interface):
        proc = FakeProcess(events)
        proc.alive = False
        return proc, FakePipe([("error", "Cannot use wlan1mon: gone")], proc)
    link = biscuit.BiscuitLink(process=factory, hold=lambda: None, release=lambda: events.append("release"),
                               present=lambda: True)
    assert link.start()
    assert wait_for(lambda: link.state == biscuit.FAILED)
    assert "gone" in link.error and events[-1] == "release"


def test_link_needs_the_monitor_radio():
    link = biscuit.BiscuitLink(present=lambda: False)
    assert not link.start() and "wlan1mon" in link.error


def test_hold_and_release_commands(tmp_path):
    ran = []

    class Done:
        returncode = 0

    def runner(command, **kwargs):
        ran.append(command)
        return Done()
    marker = str(tmp_path / "radio")
    assert biscuit.hold_radio(runner, marker, settle=0)
    assert Path(marker).exists()
    biscuit.release_radio(runner, marker)
    assert not Path(marker).exists()
    assert ran == [list(c) for c in biscuit.UCI_HOLD] + [[biscuit.PINEAPD, "restart"]] + \
        [list(c) for c in biscuit.UCI_RELEASE] + [[biscuit.PINEAPD, "restart"]]


def test_stop_never_blocks_the_screen_thread():
    gate = threading.Event()
    link = biscuit.BiscuitLink(process=lambda *a: (_ for _ in ()).throw(AssertionError("spawned")),
                               hold=gate.wait, release=lambda: None, present=lambda: True)
    assert link.start()
    started = time.monotonic()
    link.stop()
    assert time.monotonic() - started < 0.5 and link.state == biscuit.STOPPING
    gate.set()
    assert wait_for(lambda: link.state == biscuit.OFF)


def test_a_network_is_sent_again_no_sooner_than_5_seconds():
    now = [0.0]
    seen = biscuit.Sightings(clock=lambda: now[0])
    seen.add(NET6, 37, -70, 4, b"a")
    assert len(seen.take()) == 1
    now[0] = 4.9
    seen.add(NET6, 37, -40, 4, b"a")
    assert seen.take() == []
    now[0] = 5.1
    seen.add(NET6, 37, -60, 4, b"a")
    seen.add(NET6, 37, -55, 4, b"a")
    taken = seen.take()
    assert len(taken) == 1 and taken[0][1][8] == (-55) & 0xff


def test_the_pager_name_is_sent_again_so_the_biscuit_keeps_it():
    fake = FakeBiscuit()
    node, _, _ = run_node([fake], limit=70)
    assert len(fake.firmware) >= 5 and set(fake.firmware) == {b"Pager:v1.1.0"}
    before = len(fake.firmware)
    node.handle(BISCUIT, biscuit.ROLE_ASSIGN, 0, b"\x01TOKN")
    node.check_in(node.clock())
    assert len(fake.firmware) == before + 1


def test_a_repeated_frame_is_handled_once():
    fake = FakeBiscuit()
    node, radio, _ = run_node([fake], limit=1)
    acks = len([p for _, p, _ in radio.sent if p[4] == biscuit.ROLE_ACK])
    again = fake.say(biscuit.message(biscuit.ROLE_ASSIGN, 0, b"\x01TOKN"), seq=900)
    node.frame(again)
    node.frame(again)
    assert len([p for _, p, _ in radio.sent if p[4] == biscuit.ROLE_ACK]) == acks + 1

import struct
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "payload" / "quartermaster"))

from quartermaster import hardware, lora, meshpackets  # noqa: E402
from qmpager import aes, ch341, ed25519, meshapi, usblora  # noqa: E402


class FakeStick:
    """A CH341 with an SX1262 behind it that answers as one in LoRa mode."""

    def __init__(self, reset_pin=2):
        self.reset_pin = reset_pin
        self.pending = b""
        self.levels = []
        self.spi = []
        self.awake = False

    def write(self, endpoint, data):
        assert endpoint == ch341.OUT and len(data) <= ch341.PACKET
        if data[0] == ch341.UIO_STREAM:
            level = data[1] & 0x3F
            if self.levels and self.levels[-1] >> self.reset_pin & 1 == 0 and level >> self.reset_pin & 1:
                self.awake = True
            self.levels.append(level)
        elif data[0] == ch341.STATUS:
            self.pending = bytes((0, 0, 0, 0, 0, 0))
        elif data[0] == ch341.SPI_STREAM:
            sent = bytes(data[1:]).translate(ch341.REVERSED)
            self.spi.append(sent)
            reply = bytearray(len(sent))
            if sent[0] == 0x11 and self.awake:
                reply[-1] = 0x01
            self.pending = bytes(reply).translate(ch341.REVERSED)

    def read(self, endpoint, size):
        out, self.pending = self.pending[:size], self.pending[size:]
        return out

    def close(self):
        self.closed = True


def test_spi_bytes_go_out_lsb_first_with_chip_select_around_them():
    stick = FakeStick()
    chip = ch341.Ch341(stick)
    assert chip.xfer2([0x11, 0, 0]) == [0, 0, 0]
    assert stick.spi == [bytes((0x11, 0, 0))]
    assert [level & 1 for level in stick.levels] == [0, 1]


def test_long_transfers_split_into_usb_packets():
    stick = FakeStick()
    chip = ch341.Ch341(stick)
    assert len(chip.xfer2(bytes(70))) == 70
    assert [len(p) for p in stick.spi] == [31, 31, 8]


def test_stick_radio_opens_on_either_reset_pin():
    for pin in (2, 1):
        stick = FakeStick(pin)
        radio = ch341.Ch341Radio("/dev/bus/usb/001/002", opener=lambda node: stick)
        radio.open()
        assert any(p[0] == 0x97 and p[1] == 0x02 for p in stick.spi)
        radio.close()
        assert stick.closed


def test_aes_and_ed25519_match_the_standard_vectors():
    key = bytes.fromhex("2b7e151628aed2a6abf7158809cf4f3c")
    assert aes.encrypt_block(aes.expand(key), bytes.fromhex("6bc1bee22e409f96e93d7e117393172a")).hex() == \
        "3ad77bb40d7a3660a89ecaf32466ef97"
    public = bytes.fromhex("d75a980182b10ab7d54bfed3c964073a0ee172f3daa62325af021a68f707511a")
    signature = bytes.fromhex("e5564300c360ac729086e2cc806e828a84877f1eb8e5d974d873e065224901555fb8821590a33bacc61e3970"
                              "1cf9b46bd25bf5f0595bbe24655141438e7a100b")
    assert ed25519.verify(public, b"", signature)
    assert not ed25519.verify(public, b"x", signature)


def _len(number, payload):
    return meshapi._key(number, 2) + meshapi._varint(len(payload)) + payload


def test_a_heard_nodeinfo_reads_like_one_off_the_air():
    user = _len(1, b"!a1b2c3d4") + _len(2, b"Hilltop Router") + _len(3, b"HT") + meshapi._key(7, 0) + b"\x02"
    data = meshapi._key(1, 0) + b"\x04" + _len(2, user)
    packet = (meshapi._key(1, 5) + struct.pack("<I", 0xA1B2C3D4) + meshapi._key(2, 5) + b"\xff\xff\xff\xff" +
              meshapi._key(3, 0) + b"\x00" + _len(4, data) + meshapi._key(6, 5) + struct.pack("<I", 77) +
              meshapi._key(7, 5) + b"\x01\x00\x00\x00" + meshapi._key(8, 5) + struct.pack("<f", 6.5) +
              meshapi._key(9, 0) + b"\x02" + meshapi._key(12, 0) + meshapi._varint(-97) + meshapi._key(15, 0) + b"\x03")
    message = meshapi._key(1, 0) + b"\x05" + _len(2, packet)
    frames = meshapi.Frames()
    assert frames.feed(b"debug text\r\n" + meshapi.frame(message)[:5]) == []
    (got,) = frames.feed(meshapi.frame(message)[5:])
    raw, rssi, snr = meshapi.heard(got)
    assert (rssi, snr) == (-97.0, 6.5)
    node = meshpackets.decode(raw, "meshtastic")
    assert node["node"] == "a1b2c3d4" and node["name"] == "Hilltop Router" and node["role"] == "ROUTER"
    assert node["hops"] == 1
    assert meshapi.heard(got, me=0xA1B2C3D4) is None


class Profile:
    def __init__(self, protocol):
        self.protocol = protocol


class FakeLink:
    def __init__(self, data):
        self.data, self.sent = data, []
        import os
        self.r, self.w = os.pipe()
        os.write(self.w, b"x")

    def fileno(self):
        return self.r

    def write(self, data):
        self.sent.append(data)

    def read(self):
        data, self.data = self.data, b""
        return data

    def close(self):
        import os
        os.close(self.r)
        os.close(self.w)


def test_stream_radio_only_asks_and_never_sends_packets():
    user = _len(2, b"Car")
    data = meshapi._key(1, 0) + b"\x04" + _len(2, user)
    packet = (meshapi._key(1, 5) + struct.pack("<I", 5) + _len(4, data) + meshapi._key(6, 5) + b"\x01\0\0\0" +
              meshapi._key(12, 0) + meshapi._varint(-80))
    link = FakeLink(meshapi.frame(_len(2, packet)))
    radio = meshapi.StreamRadio(lambda: link)
    radio.open(Profile("meshtastic"))
    raw, rssi, snr, ok = radio.receive(0.2)
    assert ok and rssi == -80.0 and meshpackets.decode(raw, "meshtastic")["name"] == "Car"
    sent = b"".join(link.sent)
    top = meshpackets.fields(meshapi.Frames().feed(sent)[0])
    assert set(top) == {3}
    radio.close()


def test_usb_lora_prefers_a_free_stick_then_meshtasticd_then_a_board(monkeypatch):
    class Radio:
        ok = True

        def __init__(self, node):
            pass

        def open(self, profile=None):
            if not Radio.ok:
                raise lora.RadioError(lora.BUSY_ELSEWHERE)
            return self

        def close(self):
            pass

    stick = [{"node": "/dev/bus/usb/001/003", "label": "MeshToad"}]
    up = [False]
    ports = [{"path": "/dev/ttyACM0", "label": "RAK4631"}]
    found = usblora.UsbLora(sticks=lambda: stick, radio=Radio, service=lambda: up[0], ports=lambda: ports,
                            answers=lambda path: True)
    found.look()
    assert found.found and found.kind == usblora.DIRECT and "MeshToad" in found.title()
    assert not found.meshtastic_only
    stick.clear()
    up[0] = True
    found.look()
    assert found.kind == usblora.SERVICE and found.meshtastic_only
    up[0] = False
    hardware.mark_port("/dev/ttyACM0", "checked")
    found.look()
    assert found.kind == usblora.BOARD and hardware.port_mark("/dev/ttyACM0") == "meshtastic"
    assert hardware.port_holder("/dev/ttyACM0") == "meshtastic"
    ports.clear()
    found.look()
    assert not found.found and found.state == lora.ABSENT
    assert hardware.port_holder("/dev/ttyACM0") is None


def test_install_plans_meshtastic_only_for_radios_with_their_own_firmware():
    import subprocess
    code = "\n".join([
        "import qmpager.__main__ as m",
        "m.install()",
        "from quartermaster import engine, meshpackets",
        "from qmpager import aes",
        "W = type('W', (), {'cfg': {'country': 'US'}})",
        "w = W()",
        "w.cap = type('C', (), {'meshtastic_only': True})()",
        "p = engine.Wardrive._lora_plan(w)",
        "assert len(p) == 1 and p[0].protocol == 'meshtastic', p",
        "w.cap = object()",
        "assert len(engine.Wardrive._lora_plan(w)) > 1",
        "assert meshpackets._aes_ctr(b'k' * 16, b'n' * 16, b'abc') == aes.ctr(b'k' * 16, b'n' * 16, b'abc')",
        "print('ok')"])
    result = subprocess.run([sys.executable, "-c", code], cwd=str(ROOT / "payload" / "quartermaster"),
                            capture_output=True, text=True,
                            env={"QUARTERMASTER_HOME": "/tmp/qm-test", "PATH": "/usr/bin:/bin"})
    assert result.stdout.strip() == "ok", result.stderr

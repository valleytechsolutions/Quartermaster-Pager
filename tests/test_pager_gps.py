import sys
import threading
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "payload" / "quartermaster"))

from quartermaster import hardware  # noqa: E402
from qmpager import gps, sky_tile  # noqa: E402


class Runs:
    def __init__(self, uci=("", "")):
        self.calls, self.uci = [], list(uci)

    def __call__(self, argv, **kwargs):
        self.calls.append(argv)
        out = ""
        if argv[:3] == ["uci", "-q", "get"]:
            out = self.uci[0] if argv[3].endswith("device") else self.uci[1]
        return type("Done", (), {"returncode": 0 if out else 1, "stdout": out})()


def pager_gps(ports, heard, runs):
    tried = []

    def listen(stopped, *args):
        tried.append(runs.calls[-2][1:])
        return heard(tried[-1])
    unit = gps.PagerGPS("auto", candidates=lambda: ports, power=False, which=lambda name: "/usr/bin/" + name,
                        run=runs, up=lambda: True, listen=listen)
    unit._gpsd = lambda stopped: False
    unit._serial = lambda stopped: None
    return unit, tried


def test_gpsd_is_pointed_at_each_port_and_speed_until_it_hears_a_gps(monkeypatch):
    monkeypatch.setattr(gps, "SETTLE_SECONDS", 0)
    monkeypatch.setattr(gps, "stable_name", lambda path: path)
    hardware.forget_ports(["/dev/ttyUSB0", "/dev/ttyACM0"])
    ports = [{"path": "/dev/ttyUSB0", "label": "CP2102"}, {"path": "/dev/ttyACM0", "label": "u-blox"}]
    runs = Runs()
    unit, tried = pager_gps(ports, lambda t: t == ["/dev/ttyACM0", "115200"], runs)
    assert unit._search(threading.Event()) is True
    assert tried == [["/dev/ttyUSB0", str(s)] for s in gps.SPEEDS] + [["/dev/ttyACM0", "9600"],
                                                                    ["/dev/ttyACM0", "115200"]]
    assert ["/etc/init.d/gpsd", "restart"] in runs.calls
    assert hardware.port_mark("/dev/ttyACM0") == "gps"
    assert hardware.port_mark("/dev/ttyUSB0") == "checked"
    assert unit.setting_up == ""
    hardware.forget_ports(["/dev/ttyUSB0", "/dev/ttyACM0"])


def test_nothing_heard_puts_back_what_was_picked_and_waits_before_trying_again(monkeypatch):
    monkeypatch.setattr(gps, "SETTLE_SECONDS", 0)
    monkeypatch.setattr(gps, "stable_name", lambda path: path)
    hardware.forget_ports(["/dev/ttyUSB1"])
    runs = Runs(("/dev/serial/by-path/old", "4800"))
    unit, tried = pager_gps([{"path": "/dev/ttyUSB1", "label": "x"}], lambda t: False, runs)
    assert unit._search(threading.Event()) is False
    assert runs.calls[-2] == ["GPS_CONFIGURE", "/dev/serial/by-path/old", "4800"]
    count = len(runs.calls)
    assert unit._search(threading.Event()) is False
    assert len(runs.calls) == count
    hardware.forget_ports(["/dev/ttyUSB1"])


def test_lora_boards_and_nodes_are_left_alone(monkeypatch):
    monkeypatch.setattr(gps, "SETTLE_SECONDS", 0)
    hardware.mark_port("/dev/ttyACM3", "meshtastic")
    hardware.mark_port("/dev/ttyUSB3", "node")
    runs = Runs()
    unit, tried = pager_gps([{"path": "/dev/ttyACM3"}, {"path": "/dev/ttyUSB3"}, {"path": "/dev/ttyS1"}],
                            lambda t: True, runs)
    assert unit._search(threading.Event()) is False
    assert tried == []
    hardware.forget_ports(["/dev/ttyACM3", "/dev/ttyUSB3"])


def test_a_working_gpsd_is_left_as_it_is():
    runs = Runs()
    unit, tried = pager_gps([{"path": "/dev/ttyUSB0"}], lambda t: True, runs)
    unit._gpsd = lambda stopped: True
    assert unit._search(threading.Event()) is True
    assert runs.calls == []


def test_adsb_has_its_own_tile_on_the_pager_menu():
    from qmpager.__main__ import install
    from quartermaster.ui import icons, screens
    install()
    install()
    assert screens.Home.TILES.count(sky_tile.TILE) == 1
    assert screens.Home.TILES[-1] == sky_tile.TILE and len(screens.Home.TILES) == 10
    assert "plane" in icons.ICONS
    app = type("App", (), {"wardriving": False, "sky": None, "hw": type("Hw", (), {"sdr": []})()})()
    assert sky_tile.subtitle(app)[0] == "No RTL-SDR"


def test_the_gps_thread_still_runs_the_gps_loop():
    unit = gps.PagerGPS("off", power=False)
    stopped = threading.Event()
    stopped.set()
    unit.run(stopped)

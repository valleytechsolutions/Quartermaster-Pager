"""Start Quartermaster on the Pager: python3 -m qmpager [--no-splash] [--probe]

The Pager's parts take the place of the CardputerZero's before Quartermaster is loaded: the monitor
radio for Wi-Fi, a raw HCI scan for Bluetooth, the buzzer for tones, and the wide screen and D-pad.
"""

import argparse
from contextlib import ExitStack
import os
from pathlib import Path
import signal
import sys
import threading

LOOT = Path(os.environ.get("QUARTERMASTER_HOME", "/root/loot/quartermaster"))
BUILT_IN = ("wlan0", "wlan1")


def built_in_phys(sys_net="/sys/class/net"):
    phys = set()
    try:
        names = os.listdir(sys_net)
    except OSError:
        return phys
    for name in names:
        if name.startswith(BUILT_IN):
            try:
                with open(os.path.join(sys_net, name, "phy80211", "name")) as f:
                    phys.add(f.read().strip())
            except OSError:
                pass
    return phys


def install():
    """Put the Pager's parts in place. Must run before any other part of Quartermaster is imported."""
    os.environ.setdefault("QUARTERMASTER_HOME", str(LOOT))
    from quartermaster import ble, deepscan, hardware, tones, wigle
    from . import hci, listen, sound
    wigle.DISPLAY_SIZES["pager"] = "480x222"
    ble.BleScanner = hci.HciBleScanner
    deepscan.DeepScan = listen.PagerListen
    deepscan.wanted = lambda cfg: True
    tones.Player = sound.BuzzerPlayer
    phys = built_in_phys()
    every = hardware.wifi_adapters

    def usb_adapters(*args, **kwargs):
        return [a for a in every(*args, **kwargs) if a.get("phy") not in phys and a.get("type") != "803"]
    hardware.wifi_adapters = usb_adapters
    return phys


def main(argv=None):
    parser = argparse.ArgumentParser(prog="qmpager", description="Quartermaster on the Hak5 WiFi Pineapple Pager")
    parser.add_argument("--no-splash", action="store_true")
    parser.add_argument("--probe", action="store_true")
    args = parser.parse_args(argv)
    os.umask(0o077)
    install()
    from quartermaster import config, faults, hardware, lora, updates_check, wigle
    from quartermaster.engine import DEFAULT_ROOT
    from quartermaster.gnss import GPS
    from quartermaster.power import Rails
    from quartermaster.ui.app import App, HardwareWatch
    from . import listen, wide
    from .buttons import PagerInput
    from .device import Lights
    from .screen import PagerDisplay
    from .sound import BuzzerPlayer

    gps_ports = lambda: hardware.gps_candidates(cardputer=False)  # noqa: E731
    if args.probe:
        print("monitor  %s  %s" % (listen.INTERFACE, "found" if listen.present() else "missing"))
        for adapter in hardware.wifi_adapters():
            print("wifi  %(interface)s  %(label)s  bands=%(bands)s  usb=%(usb_id)s" % adapter)
        for port in gps_ports():
            print("gps   %(path)s  %(label)s" % port)
        return 0

    DEFAULT_ROOT.mkdir(parents=True, exist_ok=True)
    faults.path = DEFAULT_ROOT / "errors.log"
    cfg = config.load()
    cfg.setdefault("country_set", True)
    lights = Lights()
    with ExitStack() as cleanup:
        try:
            display = PagerDisplay()
            cleanup.callback(display.close)
            keyboard = PagerInput()
            cleanup.callback(keyboard.close)
        except (OSError, ImportError) as exc:
            print("Cannot open the Pager screen or buttons: %s" % exc, file=sys.stderr)
            return 2
        cleanup.callback(lights.off)
        rails = Rails(())
        gps = GPS(cfg.get("gps_source", "auto"), candidates=gps_ports, rails=rails, power=False)
        stopped = threading.Event()
        worker = threading.Thread(target=gps.run, args=(stopped,), daemon=True, name="gps")
        worker.start()
        cleanup.callback(worker.join, 3)
        cleanup.callback(stopped.set)
        app = App(display, keyboard, gps, cfg, root=DEFAULT_ROOT, splash=not args.no_splash,
                  hw=HardwareWatch(gps_ports=gps_ports), cap=lora.Cap(rails, cardputer=False),
                  updates=updates_check.check_pi)
        app.square = wide.WideUI()
        if isinstance(app.player, BuzzerPlayer):
            app.player.vibrate = lambda: app.cfg.get("drive_alert_tones", True)
        wigle.prune_empty(app.logs_dir)

        def stop(*_):
            app.running = False
        keyboard.on_quit = stop
        for sig in (signal.SIGINT, signal.SIGTERM, signal.SIGHUP):
            signal.signal(sig, stop)
        app.run()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

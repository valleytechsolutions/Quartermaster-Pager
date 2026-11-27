#!/usr/bin/env python3
"""Draw every Pager screen with Quartermaster's sample data: python3 tools/render_screens.py QUARTERMASTER_CHECKOUT [--out docs/screens]

QUARTERMASTER_CHECKOUT is a checkout of the Quartermaster repo at the commit in core.lock: its sample data and
fakes are used, and the Pager screens come from this repo's payload.
"""

import argparse
from pathlib import Path
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("checkout")
    parser.add_argument("--out", default=str(ROOT / "docs" / "screens"))
    parser.add_argument("--scale", type=int, default=1)
    args = parser.parse_args()
    checkout = Path(args.checkout).resolve()
    sys.path[:0] = [str(ROOT / "payload" / "quartermaster"), str(checkout), str(checkout / "tools")]
    import render_screens as qm
    from quartermaster import wigle
    from quartermaster import lora
    from quartermaster.ui import screens
    from qmpager import __main__ as pager, listen, usblora, wide
    wigle.DISPLAY_SIZES["pager"] = "480x222"
    screens.Hardware.lora_card = staticmethod(pager.lora_card)
    listen.present = lambda *a, **k: True

    class Pager(qm.Hat):
        def __init__(self):
            qm.Display.__init__(self)
            self.profile, self.width, self.height = "pager", wide.WIDTH, wide.HEIGHT

    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory() as tmp:
        home = Path(tmp)
        qm.old_logs(home)
        app = qm.build(home, Pager())
        app.square = wide.WideUI()
        app.hw.battery = {"percent": 76, "charging": False}

        def shot(name):
            image = qm.steady(app)
            if args.scale > 1:
                image = image.resize((image.width * args.scale, image.height * args.scale), 0)
            image.save(out / ("%s.png" % name))
            print(name)
        shot("menu")
        qm.press(app, "ENTER")
        qm.heard(app)
        shot("wardrive")
        qm.press(app, "TAB")
        shot("actions")
        qm.press(app, "ESC", "ENTER")
        shot("stop")
        qm.press(app, "ESC")
        qm.flock(app)
        shot("alert")
        app.stop_wardrive()
        app.open("Hardware")
        shot("hardware")
        stick = usblora.UsbLora(sticks=lambda: [], service=lambda: False, ports=lambda: [])
        stick.state, stick.kind, stick.label = lora.FOUND, usblora.DIRECT, "MeshToad"
        app.cap = stick
        app.open("Hardware")
        cards = wide.pager_cards(app.screen.cards(app))
        app.screen.top = next(i for i, card in enumerate(cards) if card["tag"] == "LORA")
        shot("hardware-lora")
        app.open("Settings")
        shot("settings")
        app.open("Logs")
        shot("logs")
        app.open("Keys")
        shot("keys")
        qm.press(app, "ENTER")
        shot("phone-setup")
        qm.press(app, "ESC", "DOWN", "ENTER")
        app.keyboard.type("AID0f1e2d3c")
        qm.press(app, "RIGHT", "RIGHT", "DOWN")
        shot("keyboard")
        app.open("Recon")
        qm.press(app, "SPACE")
        qm.flagged(app)
        qm.press(app)
        shot("recon")
        app.stop_recon()
        app.open("About")
        shot("about")
        app.shutdown()


if __name__ == "__main__":
    main()

"""The Biscuit tile: pair the Pager with a Biscuit or Biscuit Pro and run it as the Biscuit's 6 GHz node."""

from quartermaster.ui import icons, screens
from quartermaster.ui.gfx import AMBER, GOLD, GREEN, MUTED, RED

from . import biscuit

TILE = ("B", "Biscuit", "biscuit")
WORDS = {
    biscuit.STARTING: ("Starting", "Taking the Pager radio from PineAP.", GOLD),
    biscuit.PAIRING: ("Pairing", "Looking for Biscuits on channel 6.", GOLD),
    biscuit.SEARCHING: ("Searching", "Looking for your Biscuit on channel 6.", AMBER),
    biscuit.JOINED: ("Joined", "Joined. Start a wardrive on the Biscuit and this Pager scans 6 GHz for it.", GOLD),
    biscuit.SCANNING: ("Scanning", "Scanning 6 GHz for the Biscuit.", GREEN),
    biscuit.SECURE: ("Secure mode", "The Biscuit is in Secure node mode. Set it to Extended node mode.", RED),
    biscuit.OLD: ("Old firmware", "This Biscuit's firmware takes no 6 GHz nodes. Update the Biscuit.", AMBER),
    biscuit.STOPPING: ("Stopping", "Giving the Pager radio back to PineAP.", GOLD),
    biscuit.FAILED: ("Stopped", "Node mode stopped.", RED),
    biscuit.OFF: ("Off", "", MUTED),
}
BUSY = "Stop the wardrive and Recon first: node mode scans 6 GHz and nothing else."
HELP = ("Set your Biscuit or Biscuit Pro to Extended node mode, then press green. The Pager reports to it as a "
        "Pager 6 GHz node and scans 6 GHz only.")


def cookie(d, s):
    d.ellipse((s * 0.06, s * 0.06, s * 0.94, s * 0.94), fill=255)
    for x, y in ((0.34, 0.32), (0.62, 0.30), (0.48, 0.52), (0.30, 0.66), (0.66, 0.66)):
        d.ellipse((s * (x - 0.07), s * (y - 0.07), s * (x + 0.07), s * (y + 0.07)), fill=0)


class BiscuitScreen(screens.Screen):
    title = "Biscuit node"
    refresh = 0.5

    def __init__(self):
        self.index = 0
        self.start_after = False

    def link(self, app):
        return app.biscuit

    def hints(self, app):
        link = app.biscuit
        if link.running and link.pairing_mode:
            return [("OK", "Pair")]
        if link.running:
            return [("OK", "Stop node")]
        if not link.pairing.mac:
            return [("OK", "Search")]
        return [("OK", "Start node"), ("P", "Pair again"), ("F", "Forget Biscuit")]

    def busy(self, app):
        if app.wardriving or app.recon_running:
            app.toast(BUSY, AMBER, 4)
            return True
        return False

    def pair(self, app):
        if not self.busy(app):
            self.index = 0
            app.biscuit.start(pair=True)

    def tick(self, app):
        link = app.biscuit
        if self.start_after and link.state in (biscuit.OFF, biscuit.FAILED):
            self.start_after = False
            if not self.busy(app):
                link.start()

    def pick(self, app):
        found = app.biscuit.status()["found"]
        if not found:
            return
        chosen = found[min(self.index, len(found) - 1)]
        if chosen["secure"]:
            app.toast("That Biscuit is in Secure node mode. Set it to Extended node mode first.", AMBER, 5)
            return
        app.biscuit.pairing.save(chosen["mac"], chosen["name"])
        app.biscuit.stop()
        self.start_after = True
        app.toast("Paired with %s." % chosen["name"], GREEN)

    def forget(self, app):
        def done():
            app.biscuit.pairing.forget()
        app.confirm("Forget %s?" % app.biscuit.pairing.name, "Pair again to feed a Biscuit from this Pager.",
                    "Forget", done, danger=True)

    def handle(self, key, app):
        link = app.biscuit
        name = key.name
        if name in ("UP", "DOWN") and link.running and link.pairing_mode:
            count = len(link.found)
            if count:
                self.index = (self.index + (1 if name == "DOWN" else -1)) % count
            return True
        if name == "ENTER" and not key.repeat:
            if link.running and link.pairing_mode:
                self.pick(app)
            elif link.running:
                link.stop()
            elif not link.pairing.mac:
                self.pair(app)
            elif not self.busy(app):
                link.start()
            return True
        if name == "P" and not link.running:
            self.pair(app)
            return True
        if name == "F" and not link.running and link.pairing.mac:
            self.forget(app)
            return True
        if name in ("ESC", "BACKSPACE"):
            if link.running and link.pairing_mode:
                link.stop()
                return True
            app.pop()
            return True
        return False

    def render(self, c, app):
        pass


def subtitle(app):
    link = app.biscuit
    if link.running:
        word, _, color = WORDS.get(link.state, WORDS[biscuit.FAILED])
        return word, color
    if link.state == biscuit.FAILED:
        return "Stopped", AMBER
    return ("Paired", MUTED) if link.pairing.mac else ("Not paired", MUTED)


def install():
    """Add the Biscuit tile to the Pager's menu and keep node mode, wardrives and Recon from running together."""
    from quartermaster.ui.app import App
    icons.ICONS["biscuit"] = cookie
    if TILE not in screens.Home.TILES:
        screens.Home.TILES = screens.Home.TILES[:2] + [TILE] + screens.Home.TILES[2:]
    words = screens.Home.subtitle

    def home_subtitle(self, label, app):
        if label == "Biscuit" and getattr(app, "biscuit", None) is not None:
            return subtitle(app)
        return words(self, label, app)
    screens.Home.subtitle = home_subtitle

    opened = App.open

    def open_screen(self, name):
        if name == "Biscuit":
            self.home()
            self.push(BiscuitScreen())
            return
        return opened(self, name)
    App.open = open_screen

    def guarded(method):
        def run(self, *args, **kwargs):
            link = getattr(self, "biscuit", None)
            if link is not None and link.running:
                self.toast("Biscuit node mode is on. Stop it on the Biscuit tile first.", AMBER, 4)
                return False
            return method(self, *args, **kwargs)
        return run
    App.start_wardrive = guarded(App.start_wardrive)
    App.start_recon = guarded(App.start_recon)

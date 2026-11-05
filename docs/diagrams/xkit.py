"""Tiny Excalidraw scene builder: fixed grid, cards with an icon tile, zones, arrows.

Excalidraw has no layout engine, so positions come from a grid (columns x rows). Seeds are
derived from element ids, so the same script gives the same scene (and the same SVG).
"""

import base64
import hashlib
import json
import pathlib

ICONS = pathlib.Path(__file__).resolve().parent / "icons"

# Two themes, matching the blog the figures appear on: paper and navy, one blue accent. Each hue
# keeps one meaning across every figure. Icons (icons/<theme>/) are recoloured to the accent.
PALETTE = {
    # The blog's two themes (dropbars.be): paper and navy, one blue accent.
    "light": {
        "bg": "#FCFBF9", "ink": "#1A202C", "muted": "#525866", "card": "#FFFFFF", "note": "#8A8F9A",
        "paris": ("#3366CC", "#E3EBF8"), "home": ("#2E7D32", "#E3F0E1"), "net": ("#1A202C", "#EEEDEA"),
        "store": ("#9A5B00", "#F6EBD5"), "obs": ("#C2185B", "#F8E1E8"), "out": ("#D84315", "#F9E4DA"),
        "neutral": ("#525866", "#F0EFEC"),
    },
    "dark": {
        "bg": "#101218", "ink": "#E6E4E1", "muted": "#96948E", "card": "#171A22", "note": "#5E6270",
        "paris": ("#468CDC", "#0F1A2B"), "home": ("#7CD86A", "#0F1D10"), "net": ("#E6E4E1", "#1A1D25"),
        "store": ("#F0A040", "#22180A"), "obs": ("#FF5C8A", "#241018"), "out": ("#FF8A3D", "#24150B"),
        "neutral": ("#96948E", "#191B21"),
    },
}

HAND, NORMAL = 5, 6  # Excalifont, Nunito


def _seed(key):
    return int(hashlib.sha1(key.encode()).hexdigest()[:8], 16) % 2_000_000_000


class Scene:
    def __init__(self, theme, font=HAND, body_font=None, roughness=1):
        self.t = theme
        self.p = PALETTE[theme]
        self.font = font
        self.body_font = body_font or font
        self.rough = roughness
        self.el = []
        self.files = {}
        self.boxes = {}

    def _base(self, eid, kind, x, y, w, h, **kw):
        e = {
            "id": eid, "type": kind, "x": x, "y": y, "width": w, "height": h, "angle": 0,
            "strokeColor": self.p["ink"], "backgroundColor": "transparent", "fillStyle": "solid",
            "strokeWidth": 2, "strokeStyle": "solid", "roughness": self.rough, "opacity": 100,
            "groupIds": [], "frameId": None, "roundness": None, "seed": _seed(eid), "version": 1,
            "versionNonce": _seed(eid + "n"), "isDeleted": False, "boundElements": None,
            "updated": 1, "link": None, "locked": False,
        }
        e.update(kw)
        self.el.append(e)
        return e

    def text(self, eid, x, y, w, s, size=16, color=None, align="center", font=None, h=None):
        lines = s.split("\n")
        lh = 1.25
        return self._base(
            eid, "text", x, y, w, h or size * lh * len(lines), strokeColor=color or self.p["ink"],
            text=s, originalText=s, fontSize=size, fontFamily=font or self.body_font,
            textAlign=align, verticalAlign="top", containerId=None, lineHeight=lh, autoResize=False,
        )

    def zone(self, eid, x, y, w, h, title, hue, dashed=False, fill="hachure"):
        stroke, soft = self.p[hue]
        self._base(eid, "rectangle", x, y, w, h, strokeColor=stroke, backgroundColor=soft,
                   fillStyle=fill, strokeStyle="dashed" if dashed else "solid",
                   roundness=None, strokeWidth=2)
        self.text(eid + ":t", x + 20, y + 14, w - 40, title, size=24, align="left", font=self.font)
        self.boxes[eid] = (x, y, w, h)

    def icon(self, eid, name, x, y, size=44):
        data = (ICONS / self.t / f"{name}.svg").read_bytes()
        fid = hashlib.sha1(data).hexdigest()
        self.files[fid] = {
            "mimeType": "image/svg+xml", "id": fid, "created": 1,
            "dataURL": "data:image/svg+xml;base64," + base64.b64encode(data).decode(),
        }
        self._base(eid, "image", x, y, size, size, fileId=fid, status="saved", scale=[1, 1],
                   strokeColor="transparent", roughness=0)

    def card(self, eid, x, y, w, h, title, sub, icon, hue):
        stroke, _ = self.p[hue]
        self._base(eid, "rectangle", x, y, w, h, strokeColor=stroke, backgroundColor=self.p["card"],
                   fillStyle="solid", roundness=None)
        self.icon(eid + ":i", icon, x + 16, y + (h - 44) / 2)
        tx, tw = x + 72, w - 84
        if sub:
            self.text(eid + ":t", tx, y + h / 2 - 24, tw, title, size=20, align="left", font=self.font)
            self.text(eid + ":s", tx, y + h / 2 + 2, tw, sub, size=15, align="left",
                      color=self.p["muted"])
        else:
            self.text(eid + ":t", tx, y + h / 2 - 12, tw, title, size=20, align="left", font=self.font)
        self.boxes[eid] = (x, y, w, h)

    def note(self, eid, x, y, w, h, s):
        self._base(eid, "rectangle", x, y, w, h, strokeColor=self.p["note"], strokeStyle="dashed",
                   roundness=None, strokeWidth=1)
        self.text(eid + ":t", x + 10, y + h / 2 - 10, w - 20, s, size=16, color=self.p["muted"],
                  font=self.font)
        self.boxes[eid] = (x, y, w, h)

    def _anchor(self, eid, side, gap=6):
        x, y, w, h = self.boxes[eid]
        return {
            "r": (x + w + gap, y + h / 2), "l": (x - gap, y + h / 2),
            "b": (x + w / 2, y + h + gap), "t": (x + w / 2, y - gap),
        }[side]

    def arrow(self, eid, a, b, sides="rl", kind="main", label=None, both=False, hue=None, via=None, label_at=None, sharp=False):
        x1, y1 = self._anchor(a, sides[0])
        x2, y2 = self._anchor(b, sides[1])
        pts = [[0, 0]]
        for vx, vy in via or []:
            pts.append([vx - x1, vy - y1])
        pts.append([x2 - x1, y2 - y1])
        color = self.p[hue][0] if hue else (self.p["ink"] if kind == "main" else self.p["note"])
        self._base(eid, "arrow", x1, y1, abs(x2 - x1), abs(y2 - y1), points=pts,
                   strokeColor=color, strokeWidth=3 if kind == "main" else 2,
                   strokeStyle="solid" if kind == "main" else "dashed",
                   startArrowhead="arrow" if both else None, endArrowhead="arrow",
                   startBinding=None, endBinding=None, lastCommittedPoint=None,
                   roundness={"type": 2} if via and not sharp else None, elbowed=False)
        if label:
            mx, my = label_at or ((x1 + x2) / 2, (y1 + y2) / 2 - 30)
            self.text(eid + ":l", mx - 80, my, 160, label, size=16, color=color, font=self.font)

    def scene(self):
        return {
            "type": "excalidraw", "version": 2, "source": "diagram-proto",
            "elements": self.el,
            "appState": {"viewBackgroundColor": self.p["bg"], "exportBackground": True,
                         "exportWithDarkMode": False, "exportPadding": 32},
            "files": self.files,
        }

    def dump(self, path):
        pathlib.Path(path).write_text(json.dumps(self.scene()))


# ---------------------------------------------------------------- shared grid (stage figures)
# Every stage figure uses these columns: the internet left, the rented/cloud side in the middle,
# the house right. Same x, same rows, so two years side by side read as before/after.
# Per-column widths: the internet column only holds the visitors, so it is narrower, and the
# figure stays small enough to read in the docs' text column (about 1270 px wide in all).
COL = {"net": 0, "mid": 330, "home": 870}
ZWS = {"net": 260, "mid": 400, "home": 400}
CWS = {"net": 220, "mid": 340, "home": 340}
ZW, CW, CH, STEP, TOP = 400, 340, 76, 112, 80


def row(i, top=TOP):
    return top + i * STEP


def cx(col):
    return COL[col] + (ZWS[col] - CWS[col]) / 2


def _badge(self, eid, x, y, s, hue="star"):
    """A small 'new' tag on a card's top-right corner: what changed since the previous stage."""
    # A yellow sticker, as on the brand's dark section bars, in both themes.
    color = "#08050F"
    fill = "#F3E600"
    w = 14 + 9 * len(s)
    self._base(eid, "rectangle", x - w, y - 12, w, 24, strokeColor=color, backgroundColor=fill,
               fillStyle="solid", roundness=None, strokeWidth=1)
    self.text(eid + ":t", x - w, y - 9, w, s, size=14, color=color, font=self.font)


def _title(self, eid, x, y, s, sub=None):
    self.text(eid, x, y, 1200, s, size=34, align="left", font=self.font)
    if sub:
        self.text(eid + ":s", x, y + 46, 1200, sub, size=17, align="left", color=self.p["muted"])


def _lifeline(self, eid, x, top, bottom):
    self._base(eid, "line", x, top, 0, bottom - top, points=[[0, 0], [0, bottom - top]],
               strokeColor=self.p["note"], strokeStyle="dashed", strokeWidth=1,
               startBinding=None, endBinding=None, lastCommittedPoint=None,
               startArrowhead=None, endArrowhead=None)


def _msg(self, eid, x1, x2, y, label, n=None, kind="main", hue=None):
    color = self.p[hue][0] if hue else (self.p["ink"] if kind == "main" else self.p["muted"])
    gap = 8 if x2 > x1 else -8
    if x1 == x2:  # self-call: a small loop on the right of the lifeline
        pts = [[0, 0], [46, 0], [46, 30], [6, 30]]
        self._base(eid, "arrow", x1 + 6, y, 46, 30, points=pts, strokeColor=color,
                   strokeWidth=2, strokeStyle="solid", startArrowhead=None, endArrowhead="arrow",
                   startBinding=None, endBinding=None, lastCommittedPoint=None, roundness=None,
                   elbowed=False)
        self.text(eid + ":l", x1 + 62, y + 4, 300, label, size=15, align="left", color=color)
    else:
        self._base(eid, "arrow", x1 + gap, y, abs(x2 - x1) - 16, 0,
                   points=[[0, 0], [x2 - x1 - 2 * gap, 0]], strokeColor=color,
                   strokeWidth=2 if kind == "main" else 2,
                   strokeStyle="solid" if kind == "main" else "dashed",
                   startArrowhead=None, endArrowhead="arrow", startBinding=None, endBinding=None,
                   lastCommittedPoint=None, roundness=None, elbowed=False)
        lx = min(x1, x2)
        self.text(eid + ":l", lx, y - 26, abs(x2 - x1), label, size=15, color=color)
    if n is not None:
        cxp = x1 - 30 if x2 >= x1 else x1 + 14
        self._base(eid + ":n", "ellipse", cxp, y - 11, 22, 22, strokeColor=color,
                   backgroundColor=self.p["card"], fillStyle="solid", strokeWidth=1)
        self.text(eid + ":nt", cxp, y - 9, 22, str(n), size=13, color=color, font=self.font)


Scene.badge = _badge
Scene.title = _title
Scene.lifeline = _lifeline
Scene.msg = _msg

"""
Hue — Colour Picker Application
Stage 9e: Palette code search — RAL, BS4800, NCS, CSS name/code lookup,
         previews in Live Capture swatch, left-click or Alt+X to add to Picked Colours.

Author: Bad Kitty Software — Made in the UK
Environment: Ubuntu 24.04 / PyQt6 6.10.2
Run: python3 main.py  (inside ~/Hue/venv)
"""

import sys
import os
import json
import math
import colorsys

from PyQt6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QLabel, QFrame, QStatusBar, QMenu, QSystemTrayIcon,
    QMessageBox, QSizePolicy, QInputDialog, QLineEdit,
    QListWidget, QListWidgetItem, QAbstractItemView, QStyledItemDelegate,
    QComboBox, QToolButton, QRadioButton, QCheckBox, QButtonGroup,
    QSlider, QPushButton, QSpacerItem, QDialog, QDialogButtonBox,
    QFileDialog, QFormLayout, QGroupBox
)
from PyQt6.QtGui import (
    QIcon, QPixmap, QColor, QPainter, QAction,
    QPen, QBrush, QGuiApplication, QCursor, QFont, QFontMetrics, QLinearGradient
)
from PyQt6.QtCore import Qt, QPoint, QTimer, QRect, QSize, QDateTime, pyqtSignal

from colour_data import CSS_NAMED, RAL_CLASSIC, RAL_DESIGN, BS4800, NCS, BS381C, BS5252


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

APP_NAME         = "Hue"
APP_VERSION      = "1.0.0"
CONFIG_DIR       = os.path.expanduser("~/.local/share/Hue")
CONFIG_FILE      = os.path.join(CONFIG_DIR, "config.json")

WINDOW_WIDTH     = 420
MAG_CAPTURE_SIZE = 48
MAX_COLOURS      = 50
STRIP_HEIGHT     = 18

COLOUR_FORMATS   = [
    "HEX", "RGB", "HSL", "HSV", "CMYK",
    "HTML/CSS Named", "RAL Classic", "RAL Design", "BS4800", "NCS",
    "BS381C", "BS5252"
]

FIXED_PALETTE_FORMATS = {"RAL Classic", "RAL Design", "BS4800", "NCS", "BS381C", "BS5252"}

DEFAULT_CONFIG = {
    "window_x":         100,
    "window_y":         100,
    "window_w":         420,
    "window_h":         740,
    "always_on_top":    False,
    "window_opacity":   1.0,
    "zoom_level":       4,
    "cursor_style":     "Crosshair",
    "colour_format":    "HEX",
    "default_colour_format": "HEX",
    "persistence_mode": False,
    "hotkey_capture":   "Alt+X",
    "hotkey_undo":      "Ctrl+Z",

    "last_export_dir":  os.path.expanduser("~"),
    "theme":            "System"
}


# ---------------------------------------------------------------------------
# Display protocol detection
# ---------------------------------------------------------------------------

def detect_display_protocol() -> str:
    if os.environ.get("XDG_SESSION_TYPE", "").lower() == "wayland":
        return "wayland"
    if os.environ.get("WAYLAND_DISPLAY", ""):
        return "wayland"
    return "x11"


# ---------------------------------------------------------------------------
# Taskbar detection
# ---------------------------------------------------------------------------

def get_taskbar_rect() -> QRect:
    screen = QGuiApplication.primaryScreen()
    if screen is None:
        return QRect()
    full      = screen.geometry()
    available = screen.availableGeometry()
    if available.bottom() < full.bottom():
        top = available.bottom() + 1
        return QRect(full.left(), top, full.width(), full.bottom() - top + 1)
    if available.top() > full.top():
        return QRect(full.left(), full.top(), full.width(), available.top() - full.top())
    if available.left() > full.left():
        return QRect(full.left(), full.top(), available.left() - full.left(), full.height())
    if available.right() < full.right():
        return QRect(available.right() + 1, full.top(), full.right() - available.right(), full.height())
    return QRect()


def cursor_over_taskbar(cx: int, cy: int) -> bool:
    rect = get_taskbar_rect()
    if rect.isNull() or not rect.isValid():
        return False
    return rect.contains(cx, cy)


# ---------------------------------------------------------------------------
# Screen capture
# ---------------------------------------------------------------------------

def capture_pixel_colour(x: int, y: int) -> QColor:
    screen = QGuiApplication.primaryScreen()
    if screen is None:
        return QColor(128, 128, 128)
    # grabWindow(0) captures the root window which bypasses the compositor
    # and shows the raw desktop behind all windows.
    # screenshot() captures the fully composited screen as the user sees it.
    full = screen.grabWindow(0)
    if full.isNull():
        # fallback to single-pixel grab
        full = screen.grabWindow(0, x, y, 1, 1)
        if full.isNull():
            return QColor(128, 128, 128)
        return QColor(full.toImage().pixel(0, 0))
    # Clamp to screen bounds
    sg = screen.geometry()
    px = max(0, min(x - sg.left(), sg.width() - 1))
    py = max(0, min(y - sg.top(), sg.height() - 1))
    return QColor(full.toImage().pixel(px, py))


def capture_magnifier_region(x: int, y: int, size: int) -> QPixmap:
    screen = QGuiApplication.primaryScreen()
    if screen is None:
        return QPixmap()
    full = screen.grabWindow(0)
    if full.isNull():
        half = size // 2
        return screen.grabWindow(0, x - half, y - half, size, size)
    sg = screen.geometry()
    half = size // 2
    rx = max(0, min(x - sg.left() - half, sg.width() - size))
    ry = max(0, min(y - sg.top() - half, sg.height() - size))
    return full.copy(rx, ry, size, size)


# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

def load_config() -> dict:
    if not os.path.isfile(CONFIG_FILE):
        return dict(DEFAULT_CONFIG)
    try:
        with open(CONFIG_FILE, "r", encoding="utf-8") as f:
            saved = json.load(f)
        merged = dict(DEFAULT_CONFIG)
        merged.update(saved)
        return merged
    except Exception:
        return dict(DEFAULT_CONFIG)


def save_config(config: dict) -> None:
    os.makedirs(CONFIG_DIR, exist_ok=True)
    with open(CONFIG_FILE, "w", encoding="utf-8") as f:
        json.dump(config, f, indent=2)


# ---------------------------------------------------------------------------
# Colour Translation Engine
# ---------------------------------------------------------------------------

def _rgb_to_lab(r: int, g: int, b: int) -> tuple[float, float, float]:
    """Convert sRGB (0-255) to CIELAB. Used for Delta E calculations."""
    def linearise(c: float) -> float:
        c = c / 255.0
        return c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4

    lr = linearise(r)
    lg = linearise(g)
    lb = linearise(b)

    x = (lr * 0.4124564 + lg * 0.3575761 + lb * 0.1804375) / 0.95047
    y = (lr * 0.2126729 + lg * 0.7151522 + lb * 0.0721750) / 1.00000
    z = (lr * 0.0193339 + lg * 0.1191920 + lb * 0.9503041) / 1.08883

    def f(t: float) -> float:
        return t ** (1/3) if t > 0.008856 else 7.787 * t + 16/116

    fx, fy, fz = f(x), f(y), f(z)
    L = 116 * fy - 16
    a = 500 * (fx - fy)
    b_val = 200 * (fy - fz)
    return L, a, b_val


def delta_e_2000(r1: int, g1: int, b1: int,
                 r2: int, g2: int, b2: int) -> float:
    L1, a1, b1v = _rgb_to_lab(r1, g1, b1)
    L2, a2, b2v = _rgb_to_lab(r2, g2, b2)

    Lbar = (L1 + L2) / 2.0
    C1 = math.sqrt(a1**2 + b1v**2)
    C2 = math.sqrt(a2**2 + b2v**2)
    Cbar = (C1 + C2) / 2.0

    C7 = Cbar**7
    G = 0.5 * (1 - math.sqrt(C7 / (C7 + 25**7)))
    a1p = a1 * (1 + G)
    a2p = a2 * (1 + G)

    C1p = math.sqrt(a1p**2 + b1v**2)
    C2p = math.sqrt(a2p**2 + b2v**2)

    def h_prime(ap: float, bp: float) -> float:
        if ap == 0 and bp == 0:
            return 0.0
        return math.degrees(math.atan2(bp, ap)) % 360

    h1p = h_prime(a1p, b1v)
    h2p = h_prime(a2p, b2v)

    dLp = L2 - L1
    dCp = C2p - C1p

    if C1p * C2p == 0:
        dhp = 0.0
    elif abs(h2p - h1p) <= 180:
        dhp = h2p - h1p
    elif h2p - h1p > 180:
        dhp = h2p - h1p - 360
    else:
        dhp = h2p - h1p + 360

    dHp = 2 * math.sqrt(C1p * C2p) * math.sin(math.radians(dhp / 2))

    Lbarp = (L1 + L2) / 2
    Cbarp = (C1p + C2p) / 2

    if C1p * C2p == 0:
        hbarp = h1p + h2p
    elif abs(h1p - h2p) <= 180:
        hbarp = (h1p + h2p) / 2
    elif h1p + h2p < 360:
        hbarp = (h1p + h2p + 360) / 2
    else:
        hbarp = (h1p + h2p - 360) / 2

    T = (1
         - 0.17 * math.cos(math.radians(hbarp - 30))
         + 0.24 * math.cos(math.radians(2 * hbarp))
         + 0.32 * math.cos(math.radians(3 * hbarp + 6))
         - 0.20 * math.cos(math.radians(4 * hbarp - 63)))

    SL = 1 + 0.015 * (Lbarp - 50)**2 / math.sqrt(20 + (Lbarp - 50)**2)
    SC = 1 + 0.045 * Cbarp
    SH = 1 + 0.015 * Cbarp * T

    Cbarp7 = Cbarp**7
    RC = 2 * math.sqrt(Cbarp7 / (Cbarp7 + 25**7))
    d_theta = 30 * math.exp(-((hbarp - 275) / 25)**2)
    RT = -math.sin(math.radians(2 * d_theta)) * RC

    dE = math.sqrt(
        (dLp / SL)**2 +
        (dCp / SC)**2 +
        (dHp / SH)**2 +
        RT * (dCp / SC) * (dHp / SH)
    )
    return dE


def nearest_palette_match(
    colour: QColor,
    palette: dict[str, tuple]
) -> tuple[str, tuple, float]:
    r, g, b = colour.red(), colour.green(), colour.blue()
    best_code  = ""
    best_entry = None
    best_de    = float("inf")

    for code, entry in palette.items():
        pr, pg, pb = entry[0], entry[1], entry[2]
        de = delta_e_2000(r, g, b, pr, pg, pb)
        if de < best_de:
            best_de    = de
            best_code  = code
            best_entry = entry

    return best_code, best_entry, best_de


def format_colour(colour: QColor, fmt: str) -> tuple[str, bool, str, QColor | None]:
    r, g, b = colour.red(), colour.green(), colour.blue()
    a = colour.alpha()
    has_alpha = a < 255

    if fmt == "HEX":
        if has_alpha:
            return f"#{r:02X}{g:02X}{b:02X}{a:02X}", True, "", None
        return f"#{r:02X}{g:02X}{b:02X}", True, "", None

    if fmt == "RGB":
        if has_alpha:
            return f"rgba({r}, {g}, {b}, {a})", True, "", None
        return f"RGB ({r}, {g}, {b})", True, "", None

    if fmt == "HSL":
        h, l, s = colorsys.rgb_to_hls(r/255, g/255, b/255)
        if has_alpha:
            return f"hsla({round(h*360)}, {round(s*100)}%, {round(l*100)}%, {a})", True, "", None
        return f"HSL ({round(h*360)}, {round(s*100)}%, {round(l*100)}%)", True, "", None

    if fmt == "HSV":
        h, s, v = colorsys.rgb_to_hsv(r/255, g/255, b/255)
        if has_alpha:
            return f"HSV ({round(h*360)}, {round(s*100)}%, {round(v*100)}%, A:{a})", True, "", None
        return f"HSV ({round(h*360)}, {round(s*100)}%, {round(v*100)}%)", True, "", None

    if fmt == "CMYK":
        if r == 0 and g == 0 and b == 0:
            if has_alpha:
                return f"CMYK (0%, 0%, 0%, 100%, A:{a})", True, "", None
            return "CMYK (0%, 0%, 0%, 100%)", True, "", None
        rf, gf, bf = r/255, g/255, b/255
        k = 1 - max(rf, gf, bf)
        if k == 1:
            if has_alpha:
                return f"CMYK (0%, 0%, 0%, 100%, A:{a})", True, "", None
            return "CMYK (0%, 0%, 0%, 100%)", True, "", None
        c = (1 - rf - k) / (1 - k)
        m = (1 - gf - k) / (1 - k)
        y = (1 - bf - k) / (1 - k)
        if has_alpha:
            return (
                f"CMYK ({round(c*100)}%, {round(m*100)}%, "
                f"{round(y*100)}%, {round(k*100)}%, A:{a})"
            ), True, "", None
        return (
            f"CMYK ({round(c*100)}%, {round(m*100)}%, "
            f"{round(y*100)}%, {round(k*100)}%)"
        ), True, "", None

    if fmt == "HTML/CSS Named":
        best_name  = ""
        best_de    = float("inf")
        best_rgb   = (0, 0, 0)
        for name, (nr, ng, nb) in CSS_NAMED.items():
            de = delta_e_2000(r, g, b, nr, ng, nb)
            if de < best_de:
                best_de   = de
                best_name = name
                best_rgb  = (nr, ng, nb)
        exact = best_de < 1.0
        nearest_c = QColor(*best_rgb)
        if exact:
            return best_name, True, "", None
        return best_name, False, f"Nearest CSS name match: {best_name} — ΔE={best_de:.1f}", nearest_c

    if fmt == "RAL Classic":
        code, entry, de = nearest_palette_match(colour, RAL_CLASSIC)
        exact = de < 1.0
        ral_name = entry[3] if entry else ""
        nearest_c = QColor(entry[0], entry[1], entry[2]) if entry else None
        label = f"{code} ({ral_name})"
        if exact:
            return label, True, "", None
        return label, False, f"Nearest RAL match: {code} — not an exact match, ΔE={de:.1f}", nearest_c

    if fmt == "RAL Design":
        code, entry, de = nearest_palette_match(colour, RAL_DESIGN)
        exact = de < 1.0
        design_name = entry[3] if entry else ""
        nearest_c = QColor(entry[0], entry[1], entry[2]) if entry else None
        label = f"{code} ({design_name})"
        if exact:
            return label, True, "", None
        return label, False, f"Nearest RAL Design match: {code} — not an exact match, ΔE={de:.1f}", nearest_c

    if fmt == "BS4800":
        code, entry, de = nearest_palette_match(colour, BS4800)
        exact = de < 1.0
        bs_name = entry[3] if entry else ""
        nearest_c = QColor(entry[0], entry[1], entry[2]) if entry else None
        label = f"{code} ({bs_name})"
        if exact:
            return label, True, "", None
        return label, False, f"Nearest BS4800 match: {code} — not an exact match, ΔE={de:.1f}", nearest_c

    if fmt == "NCS":
        code, entry, de = nearest_palette_match(colour, NCS)
        exact = de < 1.0
        ncs_desc = entry[3] if entry else ""
        nearest_c = QColor(entry[0], entry[1], entry[2]) if entry else None
        label = f"{code} ({ncs_desc})"
        if exact:
            return label, True, "", None
        return label, False, f"Nearest NCS match: {code} — close match", nearest_c

    if fmt == "BS381C":
        code, entry, de = nearest_palette_match(colour, BS381C)
        exact = de < 1.0
        bs381c_name = entry[3] if entry else ""
        nearest_c = QColor(entry[0], entry[1], entry[2]) if entry else None
        label = f"{code} ({bs381c_name})"
        if exact:
            return label, True, "", None
        return label, False, f"Nearest BS381C match: {code} — close match", nearest_c

    if fmt == "BS5252":
        code, entry, de = nearest_palette_match(colour, BS5252)
        exact = de < 1.0
        bs5252_name = entry[3] if entry else ""
        nearest_c = QColor(entry[0], entry[1], entry[2]) if entry else None
        label = f"{code} ({bs5252_name})"
        if exact:
            return label, True, "", None
        return label, False, f"Nearest BS5252 match: {code} — close match", nearest_c

    return f"#{r:02X}{g:02X}{b:02X}", True, "", None


def format_colour_simple(colour: QColor, fmt: str) -> str:
    code, _, _, _ = format_colour(colour, fmt)
    return code


def strip_format_prefix(code: str) -> str:
    """Remove format name prefix for compact display in colour bars."""
    for prefix in ("RGB ", "HSL ", "HSV ", "CMYK "):
        if code.startswith(prefix):
            return code[len(prefix):]
    return code


def display_swatch_colour(colour: QColor, fmt: str) -> QColor:
    if fmt in FIXED_PALETTE_FORMATS:
        _, _, _, nearest = format_colour(colour, fmt)
        if nearest is not None:
            return nearest
    return colour


# ---------------------------------------------------------------------------
# Tray icon
# ---------------------------------------------------------------------------

def _icon_dir() -> str:
    """Return the directory containing the app icon PNGs.
    Checks, in order:
      1. icons/ subfolder next to main.py  (preferred)
      2. same folder as main.py
      3. ~/Hue/icons/
    """
    here = os.path.dirname(os.path.abspath(__file__))
    for candidate in (
        os.path.join(here, "icons"),
        here,
        os.path.expanduser("~/Hue/icons"),
    ):
        if os.path.isfile(os.path.join(candidate, "hue_64.png")):
            return candidate
    return os.path.join(here, "icons")


def load_app_icon() -> QIcon:
    """Return a multi-resolution QIcon built from the hue_*.png files."""
    d = _icon_dir()
    icon = QIcon()
    for size in (16, 32, 48, 64, 128, 256):
        path = os.path.join(d, f"hue_{size}.png")
        if os.path.isfile(path):
            icon.addFile(path, QSize(size, size))
    if icon.isNull():
        # Fallback: coloured square
        pm = QPixmap(64, 64)
        pm.fill(Qt.GlobalColor.transparent)
        p = QPainter(pm)
        p.setBrush(QColor("#A0393B"))
        p.setPen(Qt.PenStyle.NoPen)
        p.drawRoundedRect(4, 4, 56, 56, 10, 10)
        p.end()
        icon.addPixmap(pm)
    return icon


def make_tray_icon() -> QIcon:
    """Return a tray icon using hue_tray_22.png / hue_tray_16.png, with fallback."""
    d = _icon_dir()
    icon = QIcon()
    for name, size in (("hue_tray_22.png", 22), ("hue_tray_16.png", 16)):
        path = os.path.join(d, name)
        if os.path.isfile(path):
            icon.addFile(path, QSize(size, size))
    if icon.isNull():
        pm = QPixmap(22, 22)
        pm.fill(Qt.GlobalColor.transparent)
        p = QPainter(pm)
        p.setBrush(QColor("#A0393B"))
        p.setPen(Qt.PenStyle.NoPen)
        p.drawRoundedRect(1, 1, 20, 20, 4, 4)
        p.end()
        icon.addPixmap(pm)
    return icon


# ---------------------------------------------------------------------------
# Colour entry
# ---------------------------------------------------------------------------

class ColourEntry:
    def __init__(self, colour: QColor, name: str = ""):
        self.colour = colour
        self.name   = name


# ---------------------------------------------------------------------------
# Format Rollback Manager
# ---------------------------------------------------------------------------

class FormatRollback:
    def __init__(self):
        self._active          = False
        self._snapshot_format = "HEX"
        self._snapshot_colours: list[tuple[QColor, str]] = []

    @property
    def active(self) -> bool:
        return self._active

    @property
    def snapshot_format(self) -> str:
        return self._snapshot_format

    def activate(self, current_format: str, entries: list[ColourEntry]) -> None:
        self._active          = True
        self._snapshot_format = current_format
        self._snapshot_colours = [(e.colour, e.name) for e in entries]

    def append(self, colour: QColor, name: str = "") -> None:
        if self._active:
            self._snapshot_colours.insert(0, (colour, name))

    def rollback(self, entries: list[ColourEntry]) -> tuple[list[ColourEntry], str]:
        restored = [ColourEntry(colour, name) for colour, name in self._snapshot_colours]
        fmt = self._snapshot_format
        self._active = False
        self._snapshot_colours = []
        self._snapshot_format  = "HEX"
        return restored, fmt

    def deactivate(self) -> None:
        self._active = False
        self._snapshot_colours = []
        self._snapshot_format  = "HEX"


# ---------------------------------------------------------------------------
# Delegate
# ---------------------------------------------------------------------------

class ColourStripDelegate(QStyledItemDelegate):

    def __init__(self, entries_ref: list, config: dict, parent=None):
        super().__init__(parent)
        self.entries = entries_ref
        self.config  = config
        self.theme   = THEMES["Dark"]  # updated by apply_theme

    def sizeHint(self, option, index) -> QSize:
        return QSize(option.rect.width(), STRIP_HEIGHT)

    def paint(self, painter, option, index) -> None:
        row = index.row()
        if row >= len(self.entries):
            return

        entry    = self.entries[row]
        fmt      = self.config.get("colour_format", "HEX")
        rect     = option.rect
        w, h     = rect.width(), rect.height()
        x, y     = rect.x(), rect.y()
        selected = bool(option.state & option.state.State_Selected)

        # Entire row background is the colour itself
        swatch_colour = display_swatch_colour(entry.colour, fmt)
        painter.fillRect(rect, swatch_colour)

        # Selection indicator — white left-edge bar, more readable than overlay
        if selected:
            painter.fillRect(QRect(x, y, 3, h), QColor(255, 255, 255, 220))
            painter.fillRect(QRect(x + 3, y, w - 3, h), QColor(255, 255, 255, 30))

        # Decide text colour based on luminance for readability
        r, g, b = swatch_colour.red(), swatch_colour.green(), swatch_colour.blue()
        luminance = 0.299 * r + 0.587 * g + 0.114 * b
        text_colour = QColor(0, 0, 0, 210) if luminance > 140 else QColor(255, 255, 255, 230)

        # Code + name as text on top of the colour bar
        code = strip_format_prefix(format_colour_simple(entry.colour, fmt))
        text = f"{code}   {entry.name}" if entry.name else code

        font = painter.font()
        font.setPointSize(8)
        font.setBold(True)
        painter.setFont(font)
        painter.setPen(text_colour)
        painter.drawText(
            QRect(x + 6, y, w - 8, h),
            Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft,
            text
        )

        # Subtle bottom divider
        painter.setPen(QPen(QColor(0, 0, 0, 60), 1))
        painter.drawLine(x, y + h - 1, x + w, y + h - 1)


# ---------------------------------------------------------------------------
# Picked Colours Frame
# ---------------------------------------------------------------------------

class PickedColoursFrame(QFrame):

    def __init__(self, config: dict, set_status_fn, rollback: FormatRollback, parent=None):
        super().__init__(parent)
        self.config     = config
        self.set_status = set_status_fn
        self.rollback   = rollback
        self.entries: list[ColourEntry] = []
        self._spectrum_callback = None   # called on any list-item click
        self._spectrum_frame    = None   # direct ref for first-colour and empty notifications

        self.setFrameShape(QFrame.Shape.StyledPanel)
        self.setFrameShadow(QFrame.Shadow.Sunken)
        self.setMinimumHeight(10)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        self.empty_label = QLabel("No colours yet\npress Alt+X")
        self.empty_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.empty_label.setMinimumWidth(0)
        self.empty_label.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        layout.addWidget(self.empty_label)

        self.list_widget = QListWidget()
        self.list_widget.setVisible(False)
        self.list_widget.setSpacing(0)
        self.list_widget.setUniformItemSizes(True)
        self.list_widget.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.list_widget.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.list_widget.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self.list_widget.setFocusPolicy(Qt.FocusPolicy.NoFocus)

        self._delegate = ColourStripDelegate(self.entries, self.config)
        self.list_widget.setItemDelegate(self._delegate)
        self.list_widget.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.list_widget.customContextMenuRequested.connect(self._on_right_click)
        self.list_widget.itemClicked.connect(self._on_item_clicked)
        layout.addWidget(self.list_widget)

    def set_spectrum_callback(self, fn, spectrum_frame) -> None:
        self._spectrum_callback = fn
        self._spectrum_frame    = spectrum_frame

    def _on_item_clicked(self, item) -> None:
        if self._spectrum_callback is None:
            return
        idx = self.list_widget.row(item)
        if 0 <= idx < len(self.entries):
            self._spectrum_callback(self.entries[idx].colour)

    def _fmt(self) -> str:
        return self.config.get("colour_format", "HEX")

    def add_colour(self, colour: QColor) -> None:
        if len(self.entries) >= MAX_COLOURS:
            self.set_status(
                f"{MAX_COLOURS} colour limit reached — no further colours can be added to this session",
                colour="red", timeout_ms=6000
            )
            return
        is_first = len(self.entries) == 0
        entry = ColourEntry(colour)
        self.entries.insert(0, entry)
        self.rollback.append(colour)
        self._sync()
        self.list_widget.scrollToTop()
        code = format_colour_simple(colour, self._fmt())
        self.set_status(f"Colour added: {code}", colour="green", timeout_ms=3000)
        if is_first and self._spectrum_frame is not None:
            self._spectrum_frame.notify_first_colour(colour)

    def undo_last(self) -> None:
        if not self.entries:
            self.set_status("Nothing to undo", timeout_ms=2000)
            return
        self.entries.pop(0)
        if self.rollback.active and self.rollback._snapshot_colours:
            self.rollback._snapshot_colours.pop(0)
        self._sync()
        self.set_status("Undo — last colour removed", timeout_ms=3000)

    def _sync(self) -> None:
        if not self.entries:
            self.empty_label.setVisible(True)
            self.list_widget.setVisible(False)
            self.list_widget.clear()
            if self._spectrum_frame is not None:
                self._spectrum_frame.notify_list_empty()
            return

        self.empty_label.setVisible(False)
        self.list_widget.setVisible(True)

        while self.list_widget.count() < len(self.entries):
            item = QListWidgetItem()
            item.setSizeHint(QSize(self.list_widget.width(), STRIP_HEIGHT))
            self.list_widget.addItem(item)

        while self.list_widget.count() > len(self.entries):
            self.list_widget.takeItem(self.list_widget.count() - 1)

        self.list_widget.viewport().update()

    def _on_right_click(self, pos) -> None:
        item = self.list_widget.itemAt(pos)
        if item is None:
            return

        # If the right-clicked item is not already in the selection, select it
        # exclusively (single item). If it IS already selected, preserve the
        # full multi-selection so Ctrl+click groups survive right-click.
        if not item.isSelected():
            self.list_widget.clearSelection()
            item.setSelected(True)

        # Collect all selected indices — right-clicked item always included
        selected_indices = sorted(
            {self.list_widget.row(i) for i in self.list_widget.selectedItems()}
            | {self.list_widget.row(item)}
        )
        multi = len(selected_indices) > 1

        idx   = self.list_widget.row(item)
        entry = self.entries[idx]
        code  = format_colour_simple(entry.colour, self._fmt())

        menu = QMenu(self)
        t = self._delegate.theme
        menu.setStyleSheet(f"""
            QMenu {{ background-color: {t['menu_bg']}; color: {t['text_primary']}; border: 1px solid {t['frame_border']}; }}
            QMenu::item:selected {{ background-color: {t['highlight']}; color: #ffffff; }}
            QMenu::separator {{ height: 1px; background: {t['panel_border']}; margin: 3px 0; }}
        """)

        if multi:
            act_delete_sel = menu.addAction(f"Delete {len(selected_indices)} selected colours…")
            menu.addSeparator()
            act_delete_all = menu.addAction("Delete all swatch colours…")
            menu.addSeparator()
            act_export = menu.addAction("Export list as text…")
            act_up = act_down = act_copy = act_name = act_delete = None
        else:
            act_copy   = menu.addAction(f"Copy colour code  ({code})")
            menu.addSeparator()
            act_up   = menu.addAction("Move up")
            act_down = menu.addAction("Move down")
            menu.addSeparator()
            act_name   = menu.addAction("Assign / edit name…")

            menu.addSeparator()
            act_delete     = menu.addAction("Delete this colour")
            act_delete_all = menu.addAction("Delete all swatch colours…")
            menu.addSeparator()
            act_export = menu.addAction("Export list as text…")
            act_delete_sel = None
            act_up.setEnabled(idx > 0)
            act_down.setEnabled(idx < len(self.entries) - 1)

        chosen = menu.exec(self.list_widget.mapToGlobal(pos))
        if chosen is None:
            return

        if multi and chosen == act_delete_sel:
            n = len(selected_indices)
            reply = QMessageBox.question(
                self, "Delete Selected Colours",
                f"Are you sure you want to delete {n} selected colour{'s' if n != 1 else ''}?\n"
                "This cannot be undone.",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No
            )
            if reply == QMessageBox.StandardButton.Yes:
                for i in sorted(selected_indices, reverse=True):
                    self.entries.pop(i)
                self._sync()
                self.set_status(f"{n} colour{'s' if n != 1 else ''} deleted", timeout_ms=3000)
            return

        if chosen == act_delete_all:
            count = len(self.entries)
            reply = QMessageBox.question(
                self, "Delete All Swatch Colours",
                f"Are you sure you want to delete all {count} swatch colour{'s' if count != 1 else ''}?\n"
                "This cannot be undone.",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No
            )
            if reply == QMessageBox.StandardButton.Yes:
                self.entries.clear()
                self._sync()
                self.set_status("All swatch colours deleted", timeout_ms=3000)
            return

        if chosen == act_copy:
            QApplication.clipboard().setText(code)
            self.set_status("Colour code copied to clipboard", colour="green", timeout_ms=3000)

        elif chosen == act_up:
            self.entries[idx], self.entries[idx - 1] = self.entries[idx - 1], self.entries[idx]
            self.list_widget.setCurrentRow(idx - 1)
            self._sync()

        elif chosen == act_down:
            self.entries[idx], self.entries[idx + 1] = self.entries[idx + 1], self.entries[idx]
            self.list_widget.setCurrentRow(idx + 1)
            self._sync()

        elif chosen == act_name:
            new_name, ok = QInputDialog.getText(
                self, "Assign Name",
                f"Enter a name for {code}:",
                QLineEdit.EchoMode.Normal,
                entry.name or ""
            )
            if ok:
                entry.name = new_name.strip()
                self.list_widget.viewport().update()
                self.set_status(
                    f"Name assigned: {entry.name}" if entry.name else "Name cleared",
                    colour="green" if entry.name else None,
                    timeout_ms=3000
                )

        elif chosen == act_delete:
            reply = QMessageBox.question(
                self, "Delete Colour",
                f"Are you sure you want to delete this colour ({code})?\n"
                "This cannot be undone.",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No
            )
            if reply == QMessageBox.StandardButton.Yes:
                self.entries.pop(idx)
                self._sync()
                self.set_status("Colour deleted", timeout_ms=2000)

        if chosen == act_export:
            fmt = self._fmt()
            lines = [format_colour_simple(e.colour, fmt) for e in self.entries]
            text = "\n".join(lines)
            path, _ = QFileDialog.getSaveFileName(
                self, "Export Colour List",
                os.path.expanduser("~/colour-list.txt"),
                "Text files (*.txt);;All files (*)"
            )
            if path:
                try:
                    with open(path, "w", encoding="utf-8") as f:
                        f.write(text)
                    self.set_status(f"Colour list exported: {os.path.basename(path)}", colour="green", timeout_ms=5000)
                except OSError as ex:
                    self.set_status(f"Export failed: {ex}", colour="red", timeout_ms=6000)


    def refresh_format(self) -> None:
        self.list_widget.viewport().update()

    def replace_entries(self, new_entries: list[ColourEntry]) -> None:
        self.entries.clear()
        self.entries.extend(new_entries)
        self._sync()

    def retheme(self, t: dict) -> None:
        self.setStyleSheet(
            f"QFrame {{ background-color: {t['frame_bg']}; border: 1px solid {t['frame_border']}; border-radius: 2px; }}"
        )
        self.empty_label.setStyleSheet(
            f"color: {t['text_dim']}; font-size: 11px; background: {t['frame_bg']}; border: none; padding: 20px;"
        )
        self.list_widget.setStyleSheet(f"""
            QListWidget {{
                background-color: {t['frame_bg']};
                border: none;
                outline: none;
            }}
            QListWidget::item {{ padding: 0px; border: none; }}
            QListWidget::item:selected {{ background-color: {t['highlight']}; }}
            QScrollBar:vertical {{
                background: {t['scrollbar_bg']}; width: 8px; border: none;
            }}
            QScrollBar::handle:vertical {{
                background: {t['scrollbar_handle']}; border-radius: 4px; min-height: 20px;
            }}
            QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{ height: 0px; }}
        """)
        self._delegate.theme = t
        self.list_widget.viewport().update()


# ---------------------------------------------------------------------------
# Swatch comparison widget — draws colour fill + optional diagonal slash
# ---------------------------------------------------------------------------

class SwatchWidget(QWidget):
    """A fixed-size colour swatch that paints its label inside at the bottom,
    and optionally shows a diagonal slash when the comparison is inactive."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedSize(52, 28)
        self._colour  = QColor("#2b2b2b")
        self._slashed = False
        self._label   = ""
        self._active  = False

    def set_active(self, active: bool) -> None:
        self._active = active
        self.update()

    def set_colour(self, colour: QColor, slashed: bool = False, label: str = "") -> None:
        self._colour  = colour
        self._slashed = slashed
        self._label   = label
        self.update()

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, False)
        w, h = self.width(), self.height()

        if not self._active:
            # Greyed out — flat muted fill, no colour, no label
            painter.fillRect(0, 0, w, h, QColor(60, 60, 60, 80))
            painter.setPen(QPen(QColor("#444444"), 1))
            painter.drawRect(0, 0, w - 1, h - 1)
            painter.end()
            return

        # Fill
        painter.fillRect(0, 0, w, h, self._colour)

        # Label strip at bottom — dark semi-transparent band
        if self._label:
            band_h = 10
            painter.fillRect(0, h - band_h, w, band_h, QColor(0, 0, 0, 160))
            painter.setPen(QColor(200, 200, 200, 220))
            font = painter.font()
            font.setPointSize(6)
            font.setBold(True)
            painter.setFont(font)
            painter.drawText(0, h - band_h, w, band_h,
                             Qt.AlignmentFlag.AlignCenter, self._label)

        # Border
        painter.setPen(QPen(QColor("#555555"), 1))
        painter.drawRect(0, 0, w - 1, h - 1)

        # Diagonal slash when showing same colour (exact match)
        if self._slashed:
            painter.setPen(QPen(QColor(80, 80, 80, 200), 1))
            painter.drawLine(0, h, w, 0)

        painter.end()


# ---------------------------------------------------------------------------
# Colour Colour Format Frame (Frame 5)
# ---------------------------------------------------------------------------

class ColourOutputFrame(QFrame):

    def __init__(self, config: dict, set_status_fn, rollback: FormatRollback,
                 on_format_change, parent=None):
        super().__init__(parent)
        self.config           = config
        self.set_status       = set_status_fn
        self.rollback         = rollback
        self.on_format_change = on_format_change
        self._current_colour  = QColor(128, 128, 128)
        self._nearest_colour  = None

        self.setFrameShape(QFrame.Shape.StyledPanel)
        self.setFrameShadow(QFrame.Shadow.Sunken)
        self.setMinimumHeight(80)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(8, 6, 8, 6)
        outer.setSpacing(4)

        # Row 1: dropdown + rollback button
        row1 = QHBoxLayout()
        row1.setSpacing(6)

        fmt_label = QLabel("Format:")
        fmt_label.setStyleSheet("background: transparent; border: none; font-size: 10px;")
        row1.addWidget(fmt_label)

        self.format_combo = QComboBox()
        self.format_combo.addItems(COLOUR_FORMATS)
        self.format_combo.setCurrentText(self.config.get("colour_format", "HEX"))
        self.format_combo.setFixedHeight(22)
        self.format_combo.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.format_combo.currentTextChanged.connect(self._on_format_changed)
        row1.addWidget(self.format_combo)

        row1.addStretch()

        self.rollback_btn = QToolButton()
        self.rollback_btn.setText("↩ Roll Back")
        self.rollback_btn.setVisible(True)
        self.rollback_btn.setEnabled(False)
        self.rollback_btn.setFixedHeight(22)
        self.rollback_btn.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self._apply_rollback_btn_style(active=False)
        self.rollback_btn.clicked.connect(self._do_rollback)
        row1.addWidget(self.rollback_btn)

        outer.addLayout(row1)

        # Row 2: code output + swatch comparison
        row2 = QHBoxLayout()
        row2.setSpacing(6)

        self.code_label = QLabel("—")
        self.code_label.setFixedHeight(28)
        self.code_label.setMaximumWidth(200)
        self.code_label.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.code_label.setAlignment(Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft)
        self.code_label.setStyleSheet(
            "font-size: 12px; font-weight: bold; font-family: 'Courier New', monospace;"
            " border-radius: 2px; padding: 0 8px;"
        )
        self.code_label.setCursor(Qt.CursorShape.PointingHandCursor)
        self.code_label.mousePressEvent = self._copy_code
        row2.addWidget(self.code_label)

        self.swatch_container = QFrame()
        self.swatch_container.setFixedSize(122, 32)
        self.swatch_container.setStyleSheet("background: transparent; border: none;")
        swatch_layout = QHBoxLayout(self.swatch_container)
        swatch_layout.setContentsMargins(0, 2, 0, 2)
        swatch_layout.setSpacing(4)

        self.swatch_picked  = SwatchWidget()
        self.swatch_nearest = SwatchWidget()

        self._arrow_label = QLabel("→")
        self._arrow_label.setFixedWidth(12)
        self._arrow_label.setAlignment(Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignCenter)
        self._arrow_label.setStyleSheet("background: transparent; border: none; font-size: 10px;")

        swatch_layout.addWidget(self.swatch_picked)
        swatch_layout.addWidget(self._arrow_label)
        swatch_layout.addWidget(self.swatch_nearest)
        row2.addWidget(self.swatch_container)

        outer.addLayout(row2)

        self._hint_label = QLabel("Click code to copy to clipboard")
        self._hint_label.setStyleSheet("font-size: 9px; background: transparent; border: none;")
        outer.addWidget(self._hint_label)

    def retheme(self, t: dict) -> None:
        self.setStyleSheet(
            f"QFrame {{ background-color: {t['frame_bg']}; border: 1px solid {t['frame_border']}; border-radius: 2px; }}"
        )
        self.code_label.setStyleSheet(f"""
            QLabel {{
                background-color: {t['input_bg']};
                color: {t['text_primary']};
                font-size: 12px; font-weight: bold;
                font-family: 'Courier New', monospace;
                border: 1px solid {t['input_border']};
                border-radius: 2px; padding: 0 8px;
            }}
            QLabel:hover {{ background-color: {t['panel_bg']}; border-color: {t['highlight']}; }}
        """)
        self._hint_label.setStyleSheet(
            f"color: {t['text_dim']}; font-size: 9px; background: transparent; border: none;"
        )
        self._arrow_label.setStyleSheet(
            f"color: {t['text_secondary']}; background: transparent; border: none; font-size: 10px;"
        )
        # Re-apply rollback button style to pick up correct greyed border colour
        self._apply_rollback_btn_style(active=self.rollback_btn.isEnabled(), t=t)

    def _nearest_label_for_fmt(self, fmt: str) -> str:
        labels = {
            "RAL Classic":    "RAL",
            "RAL Design":     "RAL D",
            "BS4800":         "BS4800",
            "NCS":            "NCS",
            "HTML/CSS Named": "CSS",
            "BS381C":         "BS381C",
            "BS5252":         "BS5252",
        }
        return labels.get(fmt, "—")

    def set_colour(self, colour: QColor) -> None:
        self._current_colour = colour
        fmt = self.config.get("colour_format", "HEX")
        code, is_exact, warning, nearest = format_colour(colour, fmt)

        self.code_label.setText(code)
        self._nearest_colour = nearest

        is_palette_fmt = fmt in FIXED_PALETTE_FORMATS
        palette_label = self._nearest_label_for_fmt(fmt)

        if is_palette_fmt:
            if not is_exact and nearest is not None:
                # Active comparison — picked on left, nearest match on right
                self.swatch_picked.set_active(True)
                self.swatch_nearest.set_active(True)
                self.swatch_picked.set_colour(colour, slashed=False, label="Picked")
                self.swatch_nearest.set_colour(nearest, slashed=False, label=palette_label)
                if warning:
                    self.set_status(warning, colour="red", timeout_ms=6000)
            else:
                # Exact match — both show same colour with slash
                self.swatch_picked.set_active(True)
                self.swatch_nearest.set_active(True)
                self.swatch_picked.set_colour(colour, slashed=True, label="Picked")
                self.swatch_nearest.set_colour(colour, slashed=True, label=palette_label)
        else:
            # Non-palette format — grey out both swatches
            self.swatch_picked.set_active(False)
            self.swatch_nearest.set_active(False)

    def _on_format_changed(self, fmt: str) -> None:
        self.config["colour_format"] = fmt
        self.on_format_change(fmt)
        self.set_colour(self._current_colour)

    def _copy_code(self, event) -> None:
        code = self.code_label.text()
        if code and code != "—":
            QApplication.clipboard().setText(code)
            self.set_status("Colour code copied to clipboard", colour="green", timeout_ms=3000)

    def _apply_rollback_btn_style(self, active: bool, t: dict | None = None) -> None:
        if t is None:
            # Fallback colours when called before a theme is available
            text_active   = "#cccccc"
            text_inactive = "#888888"
            border_active = "#cc2222"
            border_inactive = "#555555"
        else:
            text_active     = t['text_primary']
            text_inactive   = t['text_secondary']
            border_active   = "#cc2222"
            border_inactive = t['input_border']

        if active:
            self.rollback_btn.setStyleSheet(f"""
                QToolButton {{
                    background-color: transparent;
                    color: {text_active};
                    border: 1px solid {border_active};
                    border-radius: 3px;
                    padding: 1px 8px;
                    font-size: 10px;
                }}
            """)
        else:
            self.rollback_btn.setStyleSheet(f"""
                QToolButton {{
                    background-color: transparent;
                    color: {text_inactive};
                    border: 1px solid {border_inactive};
                    border-radius: 3px;
                    padding: 1px 8px;
                    font-size: 10px;
                }}
            """)

    def show_rollback_button(self, visible: bool) -> None:
        self.rollback_btn.setEnabled(visible)
        self._apply_rollback_btn_style(active=visible)

    def _do_rollback(self) -> None:
        if hasattr(self, "_rollback_callback"):
            self._rollback_callback()

    def set_rollback_callback(self, fn) -> None:
        self._rollback_callback = fn

    def update_format_display(self, fmt: str) -> None:
        self.format_combo.blockSignals(True)
        self.format_combo.setCurrentText(fmt)
        self.format_combo.blockSignals(False)


# ---------------------------------------------------------------------------
# Spectrum / Gradient Frame (Stage 5)
# ---------------------------------------------------------------------------

class SpectrumFrame(QFrame):
    """
    Gradient spectrum bar between two picked colours, with live harmony panes.

    Layout (top to bottom):
      - Gradient bar  — mouse hover previews colour, click locks it
      - Harmony panes — source + generated partners, click any to add to picked list
      - Radio buttons — Complementary / Triadic / Tetradic / Analogous
    """

    HARMONY_MODES = ["Complementary", "Split Comp", "Triadic", "Tetradic", "Analogous"]
    HARMONY_OFFSETS = {
        "Complementary": [180.0],
        "Split Comp":    [150.0, 210.0],
        "Triadic":        [120.0, 240.0],
        "Tetradic":       [90.0, 180.0, 270.0],
        "Analogous":      [-30.0, 30.0],
    }

    def __init__(self, config: dict, set_status_fn, add_colour_fn, parent=None):
        super().__init__(parent)
        self.config     = config
        self.set_status = set_status_fn
        self.add_colour = add_colour_fn

        self._left:   QColor | None = None
        self._right:  QColor | None = None
        self._next_slot  = "left"
        self._locked_colour: QColor | None = None   # colour locked by click
        self._hover_colour:  QColor | None = None   # colour under mouse (live)
        self._blank_colour   = QColor("#1e1e1e")
        self._theme: dict    = {}

        self.setFrameShape(QFrame.Shape.StyledPanel)
        self.setFrameShadow(QFrame.Shadow.Sunken)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(4, 4, 4, 4)
        outer.setSpacing(4)

        # ── Gradient bar ────────────────────────────────────────────
        self._bar = _SpectrumBar(self)
        self._bar.setFixedHeight(32)
        self._bar.hovered.connect(self._on_bar_hover)
        self._bar.clicked.connect(self._on_bar_click)
        outer.addWidget(self._bar)

        # ── Placeholder label (shown when no colours set) ──────────
        self._placeholder = QLabel(
            "Click two colours in the Picked Colours list to generate a spectrum"
        )
        self._placeholder.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._placeholder.setWordWrap(True)
        self._placeholder.setStyleSheet(
            "color: #555555; font-size: 10px; background: transparent; border: none; padding: 4px;"
        )
        outer.addWidget(self._placeholder)

        # ── Harmony panes row ───────────────────────────────────────
        self._panes_widget = QWidget()
        self._panes_layout = QHBoxLayout(self._panes_widget)
        self._panes_layout.setContentsMargins(0, 0, 0, 0)
        self._panes_layout.setSpacing(4)
        self._panes: list[_HarmonyPane] = []
        outer.addWidget(self._panes_widget)
        self._panes_widget.setVisible(False)

        # ── Radio buttons ───────────────────────────────────────────
        radio_row = QHBoxLayout()
        radio_row.setContentsMargins(0, 0, 0, 0)
        radio_row.setSpacing(8)
        self._radio_group = QButtonGroup(self)
        for i, mode in enumerate(self.HARMONY_MODES):
            rb = QRadioButton(mode)
            rb.setChecked(i == 0)
            rb.setStyleSheet("font-size: 10px; background: transparent; color: palette(text);")
            self._radio_group.addButton(rb, i)
            radio_row.addWidget(rb)
        radio_row.addStretch()
        outer.addLayout(radio_row)

        self._radio_group.idToggled.connect(self._on_mode_changed)
        self._rebuild_panes()

    # ------------------------------------------------------------------
    # Theme
    # ------------------------------------------------------------------

    def retheme(self, t: dict) -> None:
        self._theme = t
        self._blank_colour = QColor(t["frame_bg"])
        self.setStyleSheet(
            f"QFrame {{ background-color: {t['frame_bg']}; "
            f"border: 1px solid {t['frame_border']}; border-radius: 2px; }}"
        )
        self._placeholder.setStyleSheet(
            f"color: {t['text_dim']}; font-size: 10px; "
            f"background: transparent; border: none; padding: 4px;"
        )
        rb_style = f"""
            QRadioButton {{
                color: {t['text_primary']};
                background: transparent;
                font-size: 10px;
                spacing: 4px;
            }}
            QRadioButton::indicator {{
                width: 11px; height: 11px;
                border-radius: 6px;
                border: 1px solid {t['text_secondary']};
                background: {t['input_bg']};
            }}
            QRadioButton::indicator:checked {{
                background: {t['highlight']};
                border: 1px solid {t['highlight']};
            }}
        """
        for rb in self._radio_group.buttons():
            rb.setStyleSheet(rb_style)
        for p in self._panes:
            p.retheme(t)
        self._bar.retheme(t)
        self.update()

    # ------------------------------------------------------------------
    # Public API — called by PickedColoursFrame
    # ------------------------------------------------------------------

    def notify_list_empty(self) -> None:
        self._left = self._right = None
        self._locked_colour = self._hover_colour = None
        self._next_slot = "left"
        self._bar.set_colours(None, None)
        self._show_placeholder(True)
        self.update()

    def notify_first_colour(self, colour: QColor) -> None:
        self._left  = colour
        self._right = None
        self._next_slot = "right"
        self._bar.set_colours(colour, None)
        self._show_placeholder(False)
        self._update_panes(colour)

    def receive_colour_click(self, colour: QColor) -> None:
        if self._next_slot == "left":
            self._left = colour
            self._next_slot = "right"
        else:
            self._right = colour
            self._next_slot = "left"
        self._bar.set_colours(self._left, self._right)
        if self._locked_colour:
            self._update_panes(self._locked_colour)
        self._show_placeholder(False)

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _show_placeholder(self, show: bool) -> None:
        self._placeholder.setVisible(show)
        self._panes_widget.setVisible(not show)

    def _current_mode(self) -> str:
        bid = self._radio_group.checkedId()
        return self.HARMONY_MODES[bid] if bid >= 0 else "Complementary"

    def _on_mode_changed(self, bid: int, checked: bool) -> None:
        if checked and self._locked_colour:
            self._update_panes(self._locked_colour)

    def _on_bar_hover(self, colour: QColor) -> None:
        """Mouse moving over bar — update panes live ONLY if not locked."""
        self._hover_colour = colour
        if self._locked_colour is None:
            self._update_panes(colour)

    def _on_bar_click(self, colour: QColor) -> None:
        """Click adds the colour to the picked list and updates harmony panes."""
        self.add_colour(colour)
        self._update_panes(colour)
        code = format_colour_simple(colour, self.config.get("colour_format", "HEX"))
        self.set_status(f"Spectrum colour added: {code}", timeout_ms=5000)

    def _rebuild_panes(self) -> None:
        """Rebuild the pane widgets for the current harmony mode."""
        mode    = self._current_mode()
        offsets = self.HARMONY_OFFSETS[mode]
        n_panes = 1 + len(offsets)   # source + partners

        # Clear old panes
        for p in self._panes:
            p.setParent(None)
        self._panes.clear()

        labels = {
            "Complementary": ["Source", "Complement"],
            "Split Comp":    ["Source", "+150°", "+210°"],
            "Triadic":        ["Source", "+120°", "+240°"],
            "Tetradic":       ["Source", "+90°", "+180°", "+270°"],
            "Analogous":      ["−30°",   "Source", "+30°"],
        }[mode]

        for i in range(n_panes):
            pane = _HarmonyPane(labels[i], self.config, self)
            pane.clicked.connect(self._on_pane_clicked)
            if self._theme:
                pane.retheme(self._theme)
            self._panes_layout.addWidget(pane)
            self._panes.append(pane)

    def _update_panes(self, source: QColor) -> None:
        """Recompute harmony colours and push to panes."""
        mode    = self._current_mode()
        offsets = self.HARMONY_OFFSETS[mode]

        # For Analogous the source goes in the middle (index 1)
        all_colours: list[QColor] = []
        if mode == "Analogous":
            all_colours = [
                _rotate_hue(source, -30.0),
                source,
                _rotate_hue(source, 30.0),
            ]
        else:
            all_colours = [source] + [_rotate_hue(source, d) for d in offsets]

        # Rebuild panes if count changed (mode switch)
        if len(self._panes) != len(all_colours):
            self._rebuild_panes()

        for pane, colour in zip(self._panes, all_colours):
            pane.set_colour(colour)

        self._panes_widget.setVisible(True)
        self._placeholder.setVisible(False)

    def _on_pane_clicked(self, colour: QColor) -> None:
        self.add_colour(colour)
        code = format_colour_simple(colour, self.config.get("colour_format", "HEX"))
        self.set_status(f"Harmony colour added: {code}", colour="green", timeout_ms=3000)


# ---------------------------------------------------------------------------
# Internal widgets for SpectrumFrame
# ---------------------------------------------------------------------------

class _SpectrumBar(QWidget):
    """The gradient bar — emits hovered(colour) on mouse move, clicked(colour) on click."""

    hovered = pyqtSignal(QColor)
    clicked = pyqtSignal(QColor)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._left:  QColor | None = None
        self._right: QColor | None = None
        self._blank = QColor("#1e1e1e")
        self.setCursor(Qt.CursorShape.CrossCursor)
        self.setMouseTracking(True)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)

    def retheme(self, t: dict) -> None:
        self._blank = QColor(t["frame_bg"])
        self.update()

    def set_colours(self, left: QColor | None, right: QColor | None) -> None:
        self._left  = left
        self._right = right
        self.update()

    def _colour_at(self, x: int) -> QColor | None:
        if self._left is None:
            return None
        if self._right is None:
            return QColor(self._left)
        t = max(0.0, min(1.0, x / max(self.width(), 1)))
        r = round(self._left.red()   + t * (self._right.red()   - self._left.red()))
        g = round(self._left.green() + t * (self._right.green() - self._left.green()))
        b = round(self._left.blue()  + t * (self._right.blue()  - self._left.blue()))
        return QColor(r, g, b)

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        w, h = self.width(), self.height()
        if self._left is None:
            painter.fillRect(0, 0, w, h, self._blank)
        elif self._right is None:
            painter.fillRect(0, 0, w, h, self._left)
        else:
            grad = QLinearGradient(0, 0, w, 0)
            grad.setColorAt(0.0, self._left)
            grad.setColorAt(1.0, self._right)
            painter.fillRect(0, 0, w, h, QBrush(grad))
        painter.setPen(QPen(QColor("#555555"), 1))
        painter.drawRect(0, 0, w - 1, h - 1)
        painter.end()

    def mouseMoveEvent(self, event) -> None:
        c = self._colour_at(int(event.position().x()))
        if c:
            self.update()
            self.hovered.emit(c)

    def mousePressEvent(self, event) -> None:
        if event.button() != Qt.MouseButton.LeftButton:
            return
        c = self._colour_at(int(event.position().x()))
        if c:
            self.update()
            self.clicked.emit(c)

    def leaveEvent(self, event) -> None:
        self.update()


class _HarmonyPane(QWidget):
    """A single harmony colour pane — swatch block + code label, clickable to add."""

    clicked = pyqtSignal(QColor)

    def __init__(self, label: str, config: dict, parent=None):
        super().__init__(parent)
        self.config        = config
        self._colour: QColor | None = None
        self._header_label = label
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setToolTip(f"{label} — click to add to picked colours")
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        self.setMinimumWidth(60)

        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(2)

        self._hdr = QLabel(label)
        self._hdr.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._hdr.setStyleSheet("font-size: 9px; color: #888888; background: transparent;")
        lay.addWidget(self._hdr)

        self._swatch = QLabel()
        self._swatch.setFixedHeight(40)
        self._swatch.setStyleSheet("background: #333333; border: 1px solid #555555; border-radius: 2px;")
        lay.addWidget(self._swatch)

        self._code = QLabel("—")
        self._code.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._code.setWordWrap(True)
        self._code.setStyleSheet("font-size: 9px; color: #aaaaaa; background: transparent;")
        lay.addWidget(self._code)

    def retheme(self, t: dict) -> None:
        self._hdr.setStyleSheet(
            f"font-size: 9px; color: {t['text_dim']}; background: transparent;"
        )
        self._code.setStyleSheet(
            f"font-size: 9px; color: {t['text_secondary']}; background: transparent;"
        )
        if self._colour is None:
            self._swatch.setStyleSheet(
                f"background: {t['panel_bg']}; border: 1px solid {t['frame_border']}; border-radius: 2px;"
            )

    def set_colour(self, colour: QColor) -> None:
        self._colour = colour
        hx  = f"#{colour.red():02X}{colour.green():02X}{colour.blue():02X}"
        self._swatch.setStyleSheet(
            f"background: {hx}; border: 1px solid #55555566; border-radius: 2px;"
        )
        fmt  = self.config.get("colour_format", "HEX")
        code = format_colour_simple(colour, fmt)
        self._code.setText(code)

    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton and self._colour:
            self.clicked.emit(self._colour)


# ---------------------------------------------------------------------------
# Magnifier widget
# ---------------------------------------------------------------------------

class MagnifierWidget(QLabel):

    CROSSHAIR = "Crosshair"
    DOT       = "Micro Dot"
    POINTER   = "Pointer"

    def __init__(self, parent=None):
        super().__init__(parent)
        self._raw_pixmap    = None
        self._frozen_pixmap = None
        self._cursor_style  = self.CROSSHAIR
        self._frozen        = False
        self.setMinimumSize(50, 50)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.setStyleSheet("background-color: #111111; border: none;")

    def set_cursor_style(self, style: str) -> None:
        self._cursor_style = style

    def update_capture(self, pixmap: QPixmap, frozen: bool = False) -> None:
        if not frozen:
            self._raw_pixmap    = pixmap
            self._frozen_pixmap = pixmap
            self._frozen        = False
        else:
            self._frozen = True
        self.update()

    def paintEvent(self, event) -> None:
        super().paintEvent(event)
        painter = QPainter(self)
        w, h = self.width(), self.height()

        display = self._frozen_pixmap if self._frozen else self._raw_pixmap
        if display and not display.isNull():
            scaled = display.scaled(
                w, h,
                Qt.AspectRatioMode.IgnoreAspectRatio,
                Qt.TransformationMode.FastTransformation
            )
            painter.drawPixmap(0, 0, scaled)
        else:
            painter.fillRect(0, 0, w, h, QColor("#111111"))

        if self._frozen:
            painter.setPen(QPen(QColor(255, 200, 0, 180), 1))
            painter.drawText(4, 14, "⏸")

        cx, cy = w // 2, h // 2
        if self._cursor_style == self.CROSSHAIR:
            gap = 6
            painter.setPen(QPen(QColor(255, 255, 255, 160), 1))
            painter.drawLine(0, cy, cx - gap, cy)
            painter.drawLine(cx + gap, cy, w, cy)
            painter.drawLine(cx, 0, cx, cy - gap)
            painter.drawLine(cx, cy + gap, cx, h)
            painter.setPen(QPen(QColor(255, 60, 60, 255), 1))
            painter.drawRect(cx - 1, cy - 1, 2, 2)
        elif self._cursor_style == self.DOT:
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QBrush(QColor(255, 60, 60, 220)))
            painter.drawEllipse(cx - 3, cy - 3, 6, 6)
        elif self._cursor_style == self.POINTER:
            painter.setPen(QPen(QColor(255, 255, 255, 200), 1))
            painter.drawLine(cx - 10, cy - 10, cx, cy)
            painter.drawLine(cx - 10, cy - 10, cx - 10, cy - 4)
            painter.drawLine(cx - 10, cy - 10, cx - 4, cy - 10)

        painter.end()


# ---------------------------------------------------------------------------
# Theme engine
# ---------------------------------------------------------------------------

THEMES = {
    "Dark": {
        "window_bg":        "#1a1a1a",
        "panel_bg":         "#2b2b2b",
        "panel_border":     "#444444",
        "frame_bg":         "#1e1e1e",
        "frame_border":     "#555555",
        "text_primary":     "#cccccc",
        "text_secondary":   "#888888",
        "text_dim":         "#666666",
        "text_label":       "#777777",
        "menubar_bg":       "#2b2b2b",
        "menubar_border":   "#444444",
        "menu_bg":          "#2b2b2b",
        "input_bg":         "#2b2b2b",
        "input_border":     "#444444",
        "statusbar_bg":     "#1a1a1a",
        "statusbar_border": "#444444",
        "highlight":        "#0078d4",
        "scrollbar_bg":     "#2a2a2a",
        "scrollbar_handle": "#555555",
        "list_row_a":       "#2a2a2a",
        "list_row_b":       "#252525",
    },
    "Light": {
        "window_bg":        "#f0f0f0",
        "panel_bg":         "#ffffff",
        "panel_border":     "#cccccc",
        "frame_bg":         "#f5f5f5",
        "frame_border":     "#cccccc",
        "text_primary":     "#1a1a1a",
        "text_secondary":   "#555555",
        "text_dim":         "#888888",
        "text_label":       "#666666",
        "menubar_bg":       "#f0f0f0",
        "menubar_border":   "#cccccc",
        "menu_bg":          "#ffffff",
        "input_bg":         "#ffffff",
        "input_border":     "#aaaaaa",
        "statusbar_bg":     "#e8e8e8",
        "statusbar_border": "#cccccc",
        "highlight":        "#0078d4",
        "scrollbar_bg":     "#e0e0e0",
        "scrollbar_handle": "#aaaaaa",
        "list_row_a":       "#f5f5f5",
        "list_row_b":       "#ebebeb",
    },
}


def detect_system_theme() -> str:
    """Detect whether the system is using a dark or light theme."""
    try:
        app = QApplication.instance()
        if app:
            palette = app.palette()
            window_colour = palette.color(palette.ColorRole.Window)
            # If the window background is dark, system is in dark mode
            luminance = (0.299 * window_colour.red() +
                         0.587 * window_colour.green() +
                         0.114 * window_colour.blue())
            return "Dark" if luminance < 128 else "Light"
    except Exception:
        pass
    return "Light"


def build_stylesheet(t: dict) -> str:
    return f"""
        QMainWindow, QDialog {{
            background-color: {t['window_bg']};
            border: 1px solid {t['frame_border']};
        }}
        QWidget {{
            background-color: {t['window_bg']};
            color: {t['text_primary']};
            font-family: 'Segoe UI', 'DejaVu Sans', sans-serif;
            font-size: 12px;
        }}
        QMenuBar {{
            background-color: {t['menubar_bg']};
            color: {t['text_primary']};
            border-bottom: 1px solid {t['menubar_border']};
        }}
        QMenuBar::item:selected {{ background-color: {t['highlight']}; color: #ffffff; }}
        QMenu {{
            background-color: {t['menu_bg']};
            color: {t['text_primary']};
            border: 1px solid {t['frame_border']};
        }}
        QMenu::item:selected {{ background-color: {t['highlight']}; color: #ffffff; }}
        QMenu::separator {{ height: 1px; background: {t['panel_border']}; margin: 3px 0; }}
        QComboBox {{
            background-color: {t['input_bg']};
            color: {t['text_primary']};
            border: 1px solid {t['input_border']};
            border-radius: 2px;
            padding: 1px 6px;
            font-size: 11px;
        }}
        QComboBox::drop-down {{ border: none; width: 18px; }}
        QComboBox QAbstractItemView {{
            background-color: {t['input_bg']};
            color: {t['text_primary']};
            selection-background-color: {t['highlight']};
            border: 1px solid {t['frame_border']};
        }}
        QScrollBar:vertical {{
            background: {t['scrollbar_bg']}; width: 8px; border: none;
        }}
        QScrollBar::handle:vertical {{
            background: {t['scrollbar_handle']}; border-radius: 4px; min-height: 20px;
        }}
        QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{ height: 0px; }}
        QStatusBar {{
            background-color: {t['statusbar_bg']};
            color: {t['text_secondary']};
            font-size: 11px;
            border-top: 1px solid {t['statusbar_border']};
        }}
        QMessageBox {{ background-color: {t['window_bg']}; color: {t['text_primary']}; }}
        QInputDialog {{ background-color: {t['window_bg']}; color: {t['text_primary']}; }}
        QLineEdit {{
            background-color: {t['input_bg']};
            color: {t['text_primary']};
            border: 1px solid {t['input_border']};
            border-radius: 2px;
            padding: 2px 4px;
        }}
    """


# ---------------------------------------------------------------------------
# Colour Adjustment Frame (Stage 6)
# ---------------------------------------------------------------------------

class ColourAdjustFrame(QFrame):
    """
    Colour adjustment editor — swatch, mode radio buttons (HEX/RGB/HSV/HSL/CMYK),
    three channel sliders with arrow fine-step buttons, alpha checkbox + slider.
    Editing updates the picked colour in place and propagates to the output frame.
    """

    MODES = ["HEX", "RGB", "HSV", "HSL", "CMYK"]

    def __init__(self, config: dict, set_status_fn, parent=None):
        super().__init__(parent)
        self.config     = config
        self.set_status = set_status_fn
        self._colour    = QColor(128, 128, 128)
        self._alpha     = 255
        self._alpha_on  = False
        _cfg_fmt = config.get("default_colour_format", "RGB")
        self._mode = _cfg_fmt if _cfg_fmt in self.MODES else "RGB"
        self._suppress  = False     # block re-entrant slider updates

        self.setFrameShape(QFrame.Shape.StyledPanel)
        self.setFrameShadow(QFrame.Shadow.Sunken)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(6, 6, 6, 6)
        outer.setSpacing(4)

        # ── Row 1: swatch + mode radio buttons ───────────────────────────
        row1 = QHBoxLayout()
        row1.setSpacing(8)

        self.swatch = QLabel()
        self.swatch.setFixedSize(44, 44)
        self.swatch.setStyleSheet("border-radius: 2px;")
        row1.addWidget(self.swatch)

        radio_col = QVBoxLayout()
        radio_col.setSpacing(2)

        row_top = QHBoxLayout()
        row_top.setSpacing(6)
        row_bot = QHBoxLayout()
        row_bot.setSpacing(6)

        self._mode_group = QButtonGroup(self)
        self._mode_radios = {}
        for i, mode in enumerate(self.MODES):
            rb = QRadioButton(mode)
            rb.setChecked(mode == self._mode)
            self._mode_group.addButton(rb, i)
            self._mode_radios[mode] = rb
            if i < 3:
                row_top.addWidget(rb)
            else:
                row_bot.addWidget(rb)

        row_top.addStretch()
        row_bot.addStretch()
        radio_col.addLayout(row_top)
        radio_col.addLayout(row_bot)

        # Alpha checkbox on same panel
        self._alpha_check = QCheckBox("Alpha channel")
        self._alpha_check.setChecked(False)
        self._alpha_check.toggled.connect(self._on_alpha_toggled)
        radio_col.addWidget(self._alpha_check)

        row1.addLayout(radio_col)
        row1.addStretch()

        # ── Action buttons (right of radio col) ──────────────────────────
        btn_col = QVBoxLayout()
        btn_col.setSpacing(4)

        self._btn_copy = QToolButton()
        self._btn_copy.setText("Copy")
        self._btn_copy.setFixedWidth(76)
        self._btn_copy.setFixedHeight(20)
        self._btn_copy.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self._btn_copy.clicked.connect(self._copy_adjusted)
        btn_col.addWidget(self._btn_copy)

        self._btn_add = QToolButton()
        self._btn_add.setText("Add to List")
        self._btn_add.setFixedWidth(76)
        self._btn_add.setFixedHeight(20)
        self._btn_add.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self._btn_add.clicked.connect(self._add_to_picked)
        btn_col.addWidget(self._btn_add)

        btn_col.addStretch()
        row1.addLayout(btn_col)

        outer.addLayout(row1)

        self._mode_group.idClicked.connect(self._on_mode_changed)

        # ── Row 2+: channel sliders ───────────────────────────────────────
        self._slider_area = QWidget()
        slider_layout = QVBoxLayout(self._slider_area)
        slider_layout.setContentsMargins(0, 0, 0, 0)
        slider_layout.setSpacing(3)
        outer.addWidget(self._slider_area)

        # Build 4 slider rows (3 channels + alpha) — show/hide as needed
        self._slider_rows = []
        for i in range(5):
            row = self._make_slider_row()
            slider_layout.addLayout(row["layout"])
            self._slider_rows.append(row)

        self._refresh_sliders()
        self._update_swatch()

    # ------------------------------------------------------------------
    # Slider row factory
    # ------------------------------------------------------------------

    def _make_slider_row(self) -> dict:
        layout = QHBoxLayout()
        layout.setSpacing(3)
        layout.setContentsMargins(0, 0, 0, 0)

        lbl = QLabel("—")
        lbl.setFixedWidth(22)
        lbl.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        lbl.setStyleSheet("font-size: 9px; background: transparent; border: none;")

        btn_down = QToolButton()
        btn_down.setText("◀")
        btn_down.setFixedSize(18, 18)
        btn_down.setAutoRepeat(True)
        btn_down.setAutoRepeatDelay(400)
        btn_down.setAutoRepeatInterval(60)
        btn_down.setStyleSheet("font-size: 8px; padding: 0;")

        slider = QSlider(Qt.Orientation.Horizontal)
        slider.setMinimum(0)
        slider.setMaximum(255)
        slider.setValue(128)

        btn_up = QToolButton()
        btn_up.setText("▶")
        btn_up.setFixedSize(18, 18)
        btn_up.setAutoRepeat(True)
        btn_up.setAutoRepeatDelay(400)
        btn_up.setAutoRepeatInterval(60)
        btn_up.setStyleSheet("font-size: 8px; padding: 0;")

        val_lbl = QLabel("128")
        val_lbl.setFixedWidth(30)
        val_lbl.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        val_lbl.setStyleSheet("font-size: 9px; background: transparent; border: none;")

        layout.addWidget(lbl)
        layout.addWidget(btn_down)
        layout.addWidget(slider)
        layout.addWidget(btn_up)
        layout.addWidget(val_lbl)

        row = {
            "layout":  layout,
            "label":   lbl,
            "slider":  slider,
            "val_lbl": val_lbl,
            "btn_down": btn_down,
            "btn_up":   btn_up,
            "visible":  True,
        }

        slider.valueChanged.connect(lambda v, r=row: self._on_slider_changed(r, v))
        btn_down.clicked.connect(lambda _, r=row: self._step_slider(r, -1))
        btn_up.clicked.connect(lambda _, r=row: self._step_slider(r, +1))

        return row

    # ------------------------------------------------------------------
    # Mode / alpha
    # ------------------------------------------------------------------

    def _on_mode_changed(self, idx: int) -> None:
        self._mode = self.MODES[idx]
        self._refresh_sliders()

    def _on_alpha_toggled(self, checked: bool) -> None:
        self._alpha_on = checked
        self._refresh_sliders()
        self._emit_change()

    # ------------------------------------------------------------------
    # Slider configuration per mode
    # ------------------------------------------------------------------

    def _channel_defs(self) -> list[dict]:
        """Return list of {name, min, max, value} for current mode."""
        r, g, b = self._colour.red(), self._colour.green(), self._colour.blue()

        if self._mode == "HEX":
            # HEX = R, G, B 0-255
            return [
                {"name": "R", "min": 0, "max": 255, "value": r},
                {"name": "G", "min": 0, "max": 255, "value": g},
                {"name": "B", "min": 0, "max": 255, "value": b},
            ]
        if self._mode == "RGB":
            return [
                {"name": "R", "min": 0, "max": 255, "value": r},
                {"name": "G", "min": 0, "max": 255, "value": g},
                {"name": "B", "min": 0, "max": 255, "value": b},
            ]
        if self._mode == "HSV":
            h, s, v = colorsys.rgb_to_hsv(r/255, g/255, b/255)
            return [
                {"name": "H", "min": 0, "max": 360, "value": round(h * 360)},
                {"name": "S", "min": 0, "max": 100, "value": round(s * 100)},
                {"name": "V", "min": 0, "max": 100, "value": round(v * 100)},
            ]
        if self._mode == "HSL":
            h, l, s = colorsys.rgb_to_hls(r/255, g/255, b/255)
            return [
                {"name": "H", "min": 0, "max": 360, "value": round(h * 360)},
                {"name": "S", "min": 0, "max": 100, "value": round(s * 100)},
                {"name": "L", "min": 0, "max": 100, "value": round(l * 100)},
            ]
        if self._mode == "CMYK":
            r2, g2, b2 = r/255, g/255, b/255
            k = 1 - max(r2, g2, b2)
            if k == 1:
                c = m = y = 0
            else:
                c = (1 - r2 - k) / (1 - k)
                m = (1 - g2 - k) / (1 - k)
                y = (1 - b2 - k) / (1 - k)
            return [
                {"name": "C", "min": 0, "max": 100, "value": round(c * 100)},
                {"name": "M", "min": 0, "max": 100, "value": round(m * 100)},
                {"name": "Y", "min": 0, "max": 100, "value": round(y * 100)},
                {"name": "K", "min": 0, "max": 100, "value": round(k * 100)},
            ]
        return []

    def _refresh_sliders(self) -> None:
        """Reconfigure slider rows for current mode and alpha state."""
        self._suppress = True
        defs = self._channel_defs()

        # CMYK has 4 channels, others have 3
        n_channels = len(defs)
        alpha_row_idx = n_channels   # alpha always goes after channels

        for i, row in enumerate(self._slider_rows):
            if i < n_channels:
                d = defs[i]
                row["label"].setText(d["name"])
                row["slider"].setMinimum(d["min"])
                row["slider"].setMaximum(d["max"])
                row["slider"].setValue(d["value"])
                row["val_lbl"].setText(str(d["value"]))
                self._set_row_visible(row, True)
            elif i == alpha_row_idx and self._alpha_on:
                row["label"].setText("A")
                row["slider"].setMinimum(0)
                row["slider"].setMaximum(255)
                row["slider"].setValue(self._alpha)
                row["val_lbl"].setText(str(self._alpha))
                self._set_row_visible(row, True)
            else:
                self._set_row_visible(row, False)

        self._suppress = False

    def _set_row_visible(self, row: dict, visible: bool) -> None:
        row["visible"] = visible
        for w in (row["label"], row["slider"], row["val_lbl"],
                  row["btn_down"], row["btn_up"]):
            w.setVisible(visible)

    # ------------------------------------------------------------------
    # Slider interaction
    # ------------------------------------------------------------------

    def _on_slider_changed(self, row: dict, value: int) -> None:
        if self._suppress:
            return
        row["val_lbl"].setText(str(value))

        defs = self._channel_defs()
        n_channels = len(defs)
        alpha_row_idx = n_channels

        # Identify which row this is
        idx = self._slider_rows.index(row)

        if idx == alpha_row_idx and self._alpha_on:
            self._alpha = value
        else:
            self._colour = self._build_colour_from_sliders()

        self._update_swatch()
        self._emit_change()

    def _step_slider(self, row: dict, delta: int) -> None:
        s = row["slider"]
        s.setValue(max(s.minimum(), min(s.maximum(), s.value() + delta)))

    def _build_colour_from_sliders(self) -> QColor:
        """Read current slider values and reconstruct the QColor."""
        defs = self._channel_defs()
        n = len(defs)
        vals = []
        for i in range(n):
            if self._slider_rows[i]["visible"]:
                vals.append(self._slider_rows[i]["slider"].value())
            else:
                vals.append(defs[i]["value"])

        if self._mode in ("HEX", "RGB"):
            return QColor(vals[0], vals[1], vals[2])
        if self._mode == "HSV":
            h, s, v = vals[0]/360, vals[1]/100, vals[2]/100
            r, g, b = colorsys.hsv_to_rgb(h, s, v)
            return QColor(round(r*255), round(g*255), round(b*255))
        if self._mode == "HSL":
            h, s, l = vals[0]/360, vals[1]/100, vals[2]/100
            r, g, b = colorsys.hls_to_rgb(h, l, s)
            return QColor(round(r*255), round(g*255), round(b*255))
        if self._mode == "CMYK":
            c, m, y, k = vals[0]/100, vals[1]/100, vals[2]/100, vals[3]/100
            r = round((1 - c) * (1 - k) * 255)
            g = round((1 - m) * (1 - k) * 255)
            b = round((1 - y) * (1 - k) * 255)
            return QColor(r, g, b)
        return self._colour

    # ------------------------------------------------------------------
    # Swatch + output
    # ------------------------------------------------------------------

    def _get_adjusted_colour(self) -> QColor:
        """Return the current colour with alpha baked in if active."""
        c = QColor(self._colour)
        c.setAlpha(self._alpha if self._alpha_on else 255)
        return c

    def _update_swatch(self) -> None:
        r = self._colour.red()
        g = self._colour.green()
        b = self._colour.blue()
        a = self._alpha if self._alpha_on else 255
        self.swatch.setStyleSheet(
            f"background-color: rgba({r},{g},{b},{a}); "
            f"border: 1px solid #888888; border-radius: 2px;"
        )

    def _emit_change(self) -> None:
        # kept for any future external wiring but frame is now self-contained
        pass  # placeholder for future external wiring

    def _copy_adjusted(self) -> None:
        colour = self._get_adjusted_colour()
        fmt = self.config.get("colour_format", "HEX")
        code, _, _, _ = format_colour(colour, fmt)
        QApplication.clipboard().setText(code)
        self.set_status(f"Adjusted colour copied: {code}", colour="green", timeout_ms=3000)

    def _add_to_picked(self) -> None:
        if hasattr(self, "_add_to_picked_cb") and self._add_to_picked_cb:
            self._add_to_picked_cb(self._get_adjusted_colour())

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def set_colour(self, colour: QColor) -> None:
        """Load a colour into the editor."""
        self._colour = QColor(colour.red(), colour.green(), colour.blue())
        if colour.alpha() < 255:
            self._alpha = colour.alpha()
            if not self._alpha_on:
                self._alpha_check.setChecked(True)
        self._refresh_sliders()
        self._update_swatch()



    def set_add_to_picked_callback(self, cb) -> None:
        self._add_to_picked_cb = cb

    def retheme(self, t: dict) -> None:
        self.setStyleSheet(
            f"QFrame {{ background-color: {t['frame_bg']}; border: 1px solid {t['frame_border']}; border-radius: 2px; }}"
        )
        rb_style = f"""
            QRadioButton {{
                color: {t['text_primary']};
                background: transparent;
                font-size: 10px;
                spacing: 4px;
            }}
            QRadioButton::indicator {{
                width: 11px; height: 11px;
                border-radius: 6px;
                border: 1px solid {t['text_secondary']};
                background: {t['input_bg']};
            }}
            QRadioButton::indicator:checked {{
                background: {t['highlight']};
                border: 1px solid {t['highlight']};
            }}
        """
        cb_style = f"""
            QCheckBox {{
                color: {t['text_primary']};
                background: transparent;
                font-size: 10px;
                spacing: 4px;
            }}
            QCheckBox::indicator {{
                width: 11px; height: 11px;
                border: 1px solid {t['text_secondary']};
                background: {t['input_bg']};
                border-radius: 2px;
            }}
            QCheckBox::indicator:checked {{
                background: {t['highlight']};
                border: 1px solid {t['highlight']};
            }}
        """
        for rb in self._mode_radios.values():
            rb.setStyleSheet(rb_style)
        self._alpha_check.setStyleSheet(cb_style)
        for row in self._slider_rows:
            row["label"].setStyleSheet(
                f"color: {t['text_secondary']}; font-size: 9px; background: transparent; border: none;"
            )
            row["val_lbl"].setStyleSheet(
                f"color: {t['text_primary']}; font-size: 9px; background: transparent; border: none;"
            )
        btn_style = f"""
            QToolButton {{
                background-color: {t['input_bg']};
                color: {t['text_primary']};
                border: 1px solid {t['input_border']};
                border-radius: 2px;
                font-size: 9px;
                padding: 1px 4px;
            }}
            QToolButton:hover {{
                background-color: {t['highlight']};
                color: #ffffff;
                border-color: {t['highlight']};
            }}
            QToolButton:pressed {{
                background-color: {t['panel_bg']};
            }}
        """
        self._btn_copy.setStyleSheet(btn_style)
        self._btn_add.setStyleSheet(btn_style)


# ---------------------------------------------------------------------------
# Palette Code Search Frame (Stage 9)
# ---------------------------------------------------------------------------

def _build_palette_index() -> list:
    """Flat searchable index across all named/coded palette systems.
    Each entry: (system_label, code, R, G, B, name)
    """
    index = []
    for code, (r, g, b, name) in RAL_CLASSIC.items():
        index.append(("RAL Classic", code, r, g, b, name))
    for code, (r, g, b, name) in RAL_DESIGN.items():
        index.append(("RAL Design", code, r, g, b, name))
    for code, (r, g, b, name) in BS4800.items():
        index.append(("BS4800", code, r, g, b, name))
    for code, (r, g, b, name) in NCS.items():
        index.append(("NCS", code, r, g, b, name))
    for code, (r, g, b, name) in BS381C.items():
        index.append(("BS381C", code, r, g, b, name))
    for code, (r, g, b, name) in BS5252.items():
        index.append(("BS5252", code, r, g, b, name))
    for name, (r, g, b) in CSS_NAMED.items():
        index.append(("CSS Named", name, r, g, b, name))
    return index


PALETTE_INDEX = _build_palette_index()


class PaletteSearchFrame(QFrame):
    """
    Palette Code Search — type any RAL, BS4800, NCS or CSS name/code.
    Single match  → colour previews immediately in the Live Capture swatch.
    Multiple      → dropdown to the right lists all matches; pick one to preview.
    No match      → status bar: NO MATCH FOUND (red).
    Left-click the Live Capture swatch, or press Alt+X, to add to Picked Colours.
    """


    def __init__(self, config: dict, set_status_fn, preview_colour_fn, parent=None):
        super().__init__(parent)
        self.config         = config
        self.set_status     = set_status_fn
        self.preview_colour = preview_colour_fn  # fn(QColor, label: str) -> None
        self._matches: list = []

        self.setFrameShape(QFrame.Shape.StyledPanel)
        self.setFrameShadow(QFrame.Shadow.Sunken)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(8, 6, 8, 6)
        outer.setSpacing(0)

        row = QHBoxLayout()
        row.setSpacing(6)

        self._lbl = QLabel("Search:")
        self._lbl.setStyleSheet("background: transparent; border: none; font-size: 10px;")
        row.addWidget(self._lbl)

        # Search box — ONLY a left mouse click inside it gives it focus.
        # Every other route (tab, window activation, keyboard, right-click) is blocked.
        # Clicking anywhere outside it immediately returns focus to the main window.
        class _SearchBox(QLineEdit):
            def focusOutEvent(self_, event):
                super().focusOutEvent(event)
                w = self_.window()
                if w:
                    w.setFocus()

            def keyPressEvent(self_, event):
                # Absorb Tab/Backtab so they cannot cycle focus out to another widget
                if event.key() in (Qt.Key.Key_Tab, Qt.Key.Key_Backtab):
                    event.ignore()
                    return
                super().keyPressEvent(event)

            def mousePressEvent(self_, event):
                # Only a left-click grants focus
                if event.button() == Qt.MouseButton.LeftButton:
                    super().mousePressEvent(event)
                else:
                    event.ignore()

        self.search_box = _SearchBox()
        self.search_box.setFixedHeight(22)
        self.search_box.setPlaceholderText("RAL 3002, 04 E 53, NCS S 2060-R, tomato…")
        self.search_box.setFocusPolicy(Qt.FocusPolicy.ClickFocus)
        self.search_box.textChanged.connect(self._on_text_changed)
        self.search_box.returnPressed.connect(self._on_return)
        row.addWidget(self.search_box, stretch=1)

        # Dropdown — only visible when multiple matches exist
        self.match_combo = QComboBox()
        self.match_combo.setFixedHeight(22)
        self.match_combo.setVisible(False)
        self.match_combo.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.match_combo.currentIndexChanged.connect(self._on_match_selected)
        row.addWidget(self.match_combo)

        outer.addLayout(row)

    def retheme(self, t: dict) -> None:
        self.setStyleSheet(
            f"QFrame {{ background-color: {t['frame_bg']}; border: 1px solid {t['frame_border']}; border-radius: 2px; }}"
        )
        self._lbl.setStyleSheet(
            f"color: {t['text_secondary']}; background: transparent; border: none; font-size: 10px;"
        )
        self.search_box.setStyleSheet(f"""
            QLineEdit {{
                background-color: {t['input_bg']};
                color: {t['text_primary']};
                border: 1px solid {t['input_border']};
                border-radius: 2px;
                padding: 1px 6px;
                font-size: 11px;
            }}
            QLineEdit:focus {{ border-color: {t['highlight']}; }}
        """)
        self.match_combo.setStyleSheet(f"""
            QComboBox {{
                background-color: {t['input_bg']};
                color: {t['text_primary']};
                border: 1px solid {t['input_border']};
                border-radius: 2px;
                padding: 1px 6px;
                font-size: 11px;
            }}
            QComboBox::drop-down {{ border: none; width: 18px; }}
            QComboBox QAbstractItemView {{
                background-color: {t['input_bg']};
                color: {t['text_primary']};
                selection-background-color: {t['highlight']};
                border: 1px solid {t['input_border']};
            }}
        """)

    # ------------------------------------------------------------------
    # Search logic
    # ------------------------------------------------------------------

    def _search(self, query: str) -> list:
        q = query.strip().lower()
        if not q:
            return []
        exact, prefix, name_hits = [], [], []
        for entry in PALETTE_INDEX:
            system, code, r, g, b, name = entry
            cl = code.lower()
            nl = name.lower()
            if q == cl:
                exact.append(entry)
            elif cl.startswith(q) or q in cl:
                prefix.append(entry)
            elif q in nl:
                name_hits.append(entry)
        seen, results = set(), []
        for e in exact + prefix + name_hits:
            key = (e[0], e[1])
            if key not in seen:
                seen.add(key)
                results.append(e)
        return results

    def _on_text_changed(self, text: str) -> None:
        self._matches = self._search(text)

        if not text.strip():
            self.match_combo.setVisible(False)
            self.match_combo.clear()
            return

        if not self._matches:
            self.match_combo.setVisible(False)
            self.match_combo.clear()
            self.set_status("NO MATCH FOUND", colour="red", timeout_ms=4000)
            return

        if len(self._matches) == 1:
            self.match_combo.setVisible(False)
            self.match_combo.clear()
            self._apply_match(self._matches[0])
        else:
            self.match_combo.blockSignals(True)
            self.match_combo.clear()
            for system, code, r, g, b, name in self._matches:
                lbl = f"{system}: {code}"
                if name and name.lower() != code.lower():
                    lbl += f"  ({name})"
                self.match_combo.addItem(lbl)
            self.match_combo.setCurrentIndex(0)
            self.match_combo.blockSignals(False)
            self.match_combo.setVisible(True)
            self._apply_match(self._matches[0])

    def _on_return(self) -> None:
        if self._matches:
            idx = max(self.match_combo.currentIndex(), 0)
            self._apply_match(self._matches[idx])

    def _on_match_selected(self, idx: int) -> None:
        if 0 <= idx < len(self._matches):
            self._apply_match(self._matches[idx])

    def _apply_match(self, entry: tuple) -> None:
        system, code, r, g, b, name = entry
        colour = QColor(r, g, b)
        label  = f"{system}: {code}"
        if name and name.lower() != code.lower():
            label += f" — {name}"
        self.preview_colour(colour, label)

    def clear(self) -> None:
        self.search_box.clear()
        self.match_combo.clear()
        self.match_combo.setVisible(False)
        self._matches = []


# ---------------------------------------------------------------------------
# Export Engine
# ---------------------------------------------------------------------------

import re as _re

def _slug(name: str) -> str:
    """Convert a colour name to a CSS/SCSS variable slug."""
    s = name.strip().lower()
    s = _re.sub(r"[^a-z0-9]+", "-", s)
    s = s.strip("-")
    return s or "colour"


def _timestamp() -> str:
    return QDateTime.currentDateTime().toString("yyyy-MM-dd hh:mm:ss")

def _timestamp_human() -> str:
    """Unambiguous date for human-readable output — e.g. 10 Mar 2026 14:32"""
    return QDateTime.currentDateTime().toString("d MMM yyyy  hh:mm")


def _build_hex(colour: QColor) -> str:
    return f"#{colour.red():02X}{colour.green():02X}{colour.blue():02X}"


# --------------- Session Save (.txt) ----------------------------------------

def export_session_txt(entries: list, include_names: bool, fmt: str = "HEX") -> str:
    lines = [f"// Hue Session Save", f"// Saved: {_timestamp()}", f"// Format: {fmt}", ""]
    for e in reversed(entries):          # oldest first (list is newest-first internally)
        hex_code = _build_hex(e.colour)
        if include_names and e.name:
            lines.append(f"{hex_code} {e.name}")
        else:
            lines.append(hex_code)
    return "\n".join(lines)


def load_session_txt(text: str) -> tuple[list, str]:
    """Parse a saved session file. Returns (entries, format_name)."""
    entries = []
    saved_fmt = "HEX"
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        if line.startswith("// Format:"):
            saved_fmt = line.replace("// Format:", "").strip()
            continue
        if line.startswith("//"):
            continue
        parts = line.split(None, 1)
        hex_part = parts[0]
        name_part = parts[1].strip() if len(parts) > 1 else ""
        if not hex_part.startswith("#") or len(hex_part) not in (7, 9):
            continue
        try:
            r = int(hex_part[1:3], 16)
            g = int(hex_part[3:5], 16)
            b = int(hex_part[5:7], 16)
            a = int(hex_part[7:9], 16) if len(hex_part) == 9 else 255
        except ValueError:
            continue
        c = QColor(r, g, b, a)
        entries.append(ColourEntry(c, name_part))
    return entries, saved_fmt


# --------------- GPL Palette (.gpl) -----------------------------------------

def export_gpl(entries: list, palette_name: str, include_names: bool) -> str:
    lines = [
        "GIMP Palette",
        f"Name: {palette_name}",
        "Columns: 16",
        "#",
    ]
    for e in reversed(entries):
        r, g, b = e.colour.red(), e.colour.green(), e.colour.blue()
        if include_names and e.name:
            label = e.name
        else:
            label = _build_hex(e.colour)
        lines.append(f"{r:3d} {g:3d} {b:3d}  {label}")
    return "\n".join(lines)


# --------------- CSS Variables (.css) ----------------------------------------

def export_css(entries: list, include_names: bool) -> str:
    lines = [f"/* Hue Palette Export — {_timestamp()} */", ":root {"]
    seen_slugs: dict[str, int] = {}
    for e in reversed(entries):
        hex_code = _build_hex(e.colour)
        if include_names and e.name:
            base = _slug(e.name)
        else:
            base = f"colour-{hex_code[1:].lower()}"
        if base in seen_slugs:
            seen_slugs[base] += 1
            var_name = f"{base}-{seen_slugs[base]}"
        else:
            seen_slugs[base] = 0
            var_name = base
        lines.append(f"  --{var_name}: {hex_code};")
    lines.append("}")
    return "\n".join(lines)


# --------------- SCSS Variables (.scss) --------------------------------------

def export_scss(entries: list, include_names: bool) -> str:
    lines = [f"// Hue Palette Export — {_timestamp()}", ""]
    seen_slugs: dict[str, int] = {}
    for e in reversed(entries):
        hex_code = _build_hex(e.colour)
        if include_names and e.name:
            base = _slug(e.name)
        else:
            base = f"colour-{hex_code[1:].lower()}"
        if base in seen_slugs:
            seen_slugs[base] += 1
            var_name = f"{base}-{seen_slugs[base]}"
        else:
            seen_slugs[base] = 0
            var_name = base
        lines.append(f"${var_name}: {hex_code};")
    return "\n".join(lines)


# --------------- HTML Swatch ------------------------------------------------

def export_html(entries: list, include_names: bool, arrangement: str,
                fmt: str = "HEX") -> str:
    """Export palette as a clean HTML5 page — colour blocks as table cells."""
    if not entries:
        return "<html><body><p>No colours to export.</p></body></html>"

    def _contrast(colour: QColor) -> str:
        """Return black or white for readable text on this background."""
        lum = (0.299 * colour.red() + 0.587 * colour.green() + 0.114 * colour.blue()) / 255
        return "#000000" if lum > 0.5 else "#ffffff"

    # Build rows — same logic as SVG/PNG
    if arrangement == "As picked":
        rows = [[e] for e in reversed(entries)]
        col_headers: list[str] = []
    else:
        harmony_rows = _build_harmony_rows(entries, arrangement)
        rows = harmony_rows
        col_headers = {
            "Complementary": ["Picked", "Complement"],
            "Triadic":        ["Picked", "+120°", "+240°"],
            "Tetradic":       ["Picked", "+90°", "+180°", "+270°"],
            "Analogous":      ["−30°", "Picked", "+30°"],
        }.get(arrangement, [])

    # Build table rows HTML
    table_rows = []

    if col_headers:
        ths = "".join(f'<th style="padding:4px 8px;font-size:11px;'
                      f'font-weight:normal;color:#666;border:none;">{h}</th>'
                      for h in col_headers)
        table_rows.append(f"<tr>{ths}</tr>")

    for row in rows:
        cells = []
        for ci, item in enumerate(row):
            colour  = item.colour if hasattr(item, "colour") else item
            hx      = _build_hex(colour)
            fg      = _contrast(colour)
            if ci == 0:
                code = (item.name if (include_names and hasattr(item, "name") and item.name)
                        else format_colour_simple(colour, fmt))
                border = "2px solid rgba(0,0,0,0.4)"
            else:
                code   = format_colour_simple(colour, fmt)
                border = "1px solid rgba(0,0,0,0.15)"
            cells.append(
                f'<td style="padding:0;border:none;">'
                f'<div style="background:{hx};width:90px;height:60px;'
                f'border:{border};border-radius:3px;"></div>'
                f'<div style="padding:2px 4px;font-size:10px;color:#333;'
                f'white-space:normal;word-break:break-word;'
                f'max-width:90px;">{code}</div>'
                f'</td>'
            )
        table_rows.append(f'<tr style="vertical-align:top;">{"".join(cells)}</tr>')

    table_html = "\n".join(table_rows)

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Hue Palette Export</title>
<style>
  body {{ font-family: 'Segoe UI', Arial, sans-serif; background: #f8f8f8;
         margin: 24px; color: #333; }}
  h1   {{ font-size: 15px; font-weight: normal; color: #666;
          margin: 0 0 12px 0; }}
  table {{ border-collapse: separate; border-spacing: 6px; }}
  td, th {{ vertical-align: top; }}
</style>
</head>
<body>
<h1>Hue Palette — {arrangement} — {_timestamp_human()}</h1>
<table>
{table_html}
</table>
</body>
</html>"""


# --------------- SVG Swatch --------------------------------------------------

def _hue_of(colour: QColor) -> float:
    h, s, v, _ = colour.getHsvF()
    return h if h >= 0 else 0.0


def _rotate_hue(colour: QColor, degrees: float) -> QColor:
    """Return a new colour with hue rotated by degrees, preserving S and V."""
    h, s, v, _ = colour.getHsvF()
    if h < 0:
        h = 0.0
    h = (h + degrees / 360.0) % 1.0
    result = QColor.fromHsvF(h, s, v)
    return result


def _harmony_offsets(arrangement: str) -> list[float]:
    """Return hue rotation offsets (in degrees) for generated partner colours.
    The original picked colour is always position 0 (offset 0 is implicit).
    """
    return {
        "Complementary": [180.0],
        "Triadic":        [120.0, 240.0],
        "Tetradic":       [90.0, 180.0, 270.0],
        "Analogous":      [-30.0, 30.0],
    }.get(arrangement, [])


def _build_harmony_rows(entries: list, arrangement: str) -> list[list]:
    """
    For each picked colour entry, build a row:
      [original_entry, generated_colour_1, generated_colour_2, ...]
    Generated colours are plain QColor objects (no name/entry wrapper).
    Returns a list of rows, one per picked colour, oldest first.
    """
    offsets = _harmony_offsets(arrangement)
    rows = []
    for e in reversed(entries):   # oldest first
        row = [e]                  # col 0 — the original picked colour entry
        for deg in offsets:
            row.append(_rotate_hue(e.colour, deg))
        rows.append(row)
    return rows




def _svg_label(text: str, cx: int, y: int, max_chars: int, font_size: int) -> str:
    """Split text into tspan lines at word boundaries and render as SVG text."""
    words   = text.split()
    lines_out: list[str] = []
    current = ""
    for w in words:
        candidate = (current + " " + w).strip()
        if len(candidate) <= max_chars:
            current = candidate
        else:
            if current:
                lines_out.append(current)
            current = w
    if current:
        lines_out.append(current)
    if not lines_out:
        return ""
    line_h = font_size + 2
    spans  = "".join(
        f'<tspan x="{cx}" dy="{0 if i == 0 else line_h}">{ln}</tspan>'
        for i, ln in enumerate(lines_out)
    )
    return (
        f'<text x="{cx}" y="{y}" text-anchor="middle" '
        f'font-size="{font_size}" font-family="sans-serif" fill="#333333">'
        f'{spans}</text>'
    )


def export_svg(entries: list, include_names: bool, arrangement: str,
               fmt: str = "HEX", font_size: int = 10) -> str:
    if not entries:
        return '<svg xmlns="http://www.w3.org/2000/svg" width="100" height="100"></svg>'

    SWATCH_W  = 100
    SWATCH_H  = 65
    MAX_CHARS = 14
    MAX_LINES = 3
    LINE_H    = font_size + 2
    LABEL_H   = (MAX_LINES * LINE_H + 4) if include_names else 0
    PAD       = 8

    def _label_for(item, ci):
        colour = item.colour if hasattr(item, "colour") else item
        if ci == 0:
            return (item.name if (include_names and hasattr(item, "name") and item.name)
                    else format_colour_simple(colour, fmt))
        return format_colour_simple(colour, fmt)

    # ── As picked — flat grid ─────────────────────────────────────────
    if arrangement == "As picked":
        ordered = list(reversed(entries))
        n       = len(ordered)
        COLS    = 6
        cols    = min(COLS, n)
        rows    = (n + cols - 1) // cols
        cell_w  = SWATCH_W + PAD
        cell_h  = SWATCH_H + LABEL_H + PAD
        SVG_W   = cols * cell_w + PAD
        SVG_H   = rows * cell_h + PAD
        shapes  = []
        for idx, e in enumerate(ordered):
            col = idx % cols
            row = idx // cols
            x   = PAD + col * cell_w
            y   = PAD + row * cell_h
            hx  = _build_hex(e.colour)
            shapes.append(
                f'<rect x="{x}" y="{y}" width="{SWATCH_W}" height="{SWATCH_H}" '
                f'fill="{hx}" rx="3" stroke="#00000022" stroke-width="1"/>'
            )
            if include_names:
                shapes.append(_svg_label(_label_for(e, 0),
                                         x + SWATCH_W // 2,
                                         y + SWATCH_H + LINE_H, MAX_CHARS, font_size))
        return (
            f'<svg xmlns="http://www.w3.org/2000/svg" width="{SVG_W}" height="{SVG_H}">'
            f'<rect width="{SVG_W}" height="{SVG_H}" fill="#f8f8f8"/>'
            + "".join(shapes) + "</svg>"
        )

    # ── Harmony arrangements — one row per picked colour ─────────────
    harmony_rows = _build_harmony_rows(entries, arrangement)
    num_cols     = 1 + len(_harmony_offsets(arrangement))
    num_rows     = len(harmony_rows)
    cell_w       = SWATCH_W + PAD
    cell_h       = SWATCH_H + LABEL_H + PAD
    ROW_GAP      = 8
    HDR_H        = 16
    SVG_W        = num_cols * cell_w + PAD
    SVG_H        = HDR_H + num_rows * (cell_h + ROW_GAP) + PAD

    col_headers = {
        "Complementary": ["Picked", "Complement"],
        "Triadic":        ["Picked", "+120\u00b0", "+240\u00b0"],
        "Tetradic":       ["Picked", "+90\u00b0", "+180\u00b0", "+270\u00b0"],
        "Analogous":      ["\u221230\u00b0", "Picked", "+30\u00b0"],
    }
    shapes  = []
    headers = col_headers.get(arrangement, [])
    for ci, hdr in enumerate(headers):
        x = PAD + ci * cell_w + SWATCH_W // 2
        shapes.append(
            f'<text x="{x}" y="{HDR_H - 3}" text-anchor="middle" '
            f'font-size="9" font-family="sans-serif" fill="#666666">{hdr}</text>'
        )

    for ri, row in enumerate(harmony_rows):
        y_base     = HDR_H + PAD + ri * (cell_h + ROW_GAP)
        stripe_w   = num_cols * cell_w + PAD
        stripe_col = "#f0f0f0" if ri % 2 == 0 else "#e8e8e8"
        shapes.append(
            f'<rect x="0" y="{y_base - 2}" width="{stripe_w}" '
            f'height="{cell_h + 2}" fill="{stripe_col}" rx="2"/>'
        )
        for ci, item in enumerate(row):
            colour = item.colour if hasattr(item, "colour") else item
            x      = PAD + ci * cell_w
            y      = y_base
            hx     = _build_hex(colour)
            stroke = "#000000AA" if ci == 0 else "#00000033"
            sw     = "2"         if ci == 0 else "1"
            shapes.append(
                f'<rect x="{x}" y="{y}" width="{SWATCH_W}" height="{SWATCH_H}" '
                f'fill="{hx}" rx="3" stroke="{stroke}" stroke-width="{sw}"/>'
            )
            if include_names:
                shapes.append(_svg_label(_label_for(item, ci),
                                         x + SWATCH_W // 2,
                                         y + SWATCH_H + LINE_H, MAX_CHARS, font_size))

    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{SVG_W}" height="{SVG_H}">'
        f'<rect width="{SVG_W}" height="{SVG_H}" fill="#f8f8f8"/>'
        + "".join(shapes)
        + "</svg>"
    )


# --------------- PNG Swatch --------------------------------------------------

def export_png_pixmap(entries: list, include_names: bool,
                      arrangement: str, fmt: str = "HEX") -> QPixmap:
    """Render the swatch grid to a QPixmap for PNG export."""
    if not entries:
        px = QPixmap(100, 100)
        px.fill(QColor("#f8f8f8"))
        return px

    SWATCH_W  = 100
    SWATCH_H  = 65
    MAX_LINES = 3
    FONT_SIZE = 8
    LINE_H    = FONT_SIZE + 3
    LABEL_H   = (MAX_LINES * LINE_H + 4) if include_names else 0
    PAD       = 8
    COLS      = 6
    WW        = Qt.TextFlag.TextWordWrap

    def _label_for(item, ci):
        colour = item.colour if hasattr(item, "colour") else item
        if ci == 0:
            return (item.name if (include_names and hasattr(item, "name") and item.name)
                    else format_colour_simple(colour, fmt))
        return format_colour_simple(colour, fmt)

    def _draw_label(painter, label, x, y):
        painter.setPen(QColor("#333333"))
        f = painter.font(); f.setPointSize(FONT_SIZE); painter.setFont(f)
        painter.drawText(x, y, SWATCH_W, LABEL_H,
                         Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignTop | WW,
                         label)

    # ── As picked — flat grid ─────────────────────────────────────────
    if arrangement == "As picked":
        ordered = list(reversed(entries))
        n       = len(ordered)
        cols    = min(COLS, n)
        rows    = (n + cols - 1) // cols
        cell_w  = SWATCH_W + PAD
        cell_h  = SWATCH_H + LABEL_H + PAD
        W       = cols * cell_w + PAD
        H       = rows * cell_h + PAD
        px      = QPixmap(W, H)
        px.fill(QColor("#f8f8f8"))
        painter = QPainter(px)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        for idx, e in enumerate(ordered):
            col = idx % cols
            row = idx // cols
            x   = PAD + col * cell_w
            y   = PAD + row * cell_h
            painter.setBrush(QBrush(e.colour))
            painter.setPen(QPen(QColor(0, 0, 0, 30), 1))
            painter.drawRoundedRect(x, y, SWATCH_W, SWATCH_H, 3, 3)
            if include_names:
                _draw_label(painter, _label_for(e, 0), x, y + SWATCH_H + 2)
        painter.end()
        return px

    # ── Harmony arrangements — one row per picked colour ─────────────
    harmony_rows = _build_harmony_rows(entries, arrangement)
    num_cols     = 1 + len(_harmony_offsets(arrangement))
    num_rows     = len(harmony_rows)
    HDR_H        = 18
    ROW_GAP      = 6
    cell_w       = SWATCH_W + PAD
    cell_h       = SWATCH_H + LABEL_H + PAD
    W            = num_cols * cell_w + PAD
    H            = HDR_H + num_rows * (cell_h + ROW_GAP) + PAD

    px      = QPixmap(W, H)
    px.fill(QColor("#f8f8f8"))
    painter = QPainter(px)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)

    col_headers = {
        "Complementary": ["Picked", "Complement"],
        "Triadic":        ["Picked", "+120°", "+240°"],
        "Tetradic":       ["Picked", "+90°", "+180°", "+270°"],
        "Analogous":      ["−30°", "Picked", "+30°"],
    }
    headers = col_headers.get(arrangement, [])
    painter.setPen(QColor("#666666"))
    fh = painter.font(); fh.setPointSize(7); painter.setFont(fh)
    for ci, hdr in enumerate(headers):
        x = PAD + ci * cell_w
        painter.drawText(x, 0, SWATCH_W, HDR_H,
                         Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignVCenter,
                         hdr)

    for ri, row in enumerate(harmony_rows):
        y_base = HDR_H + PAD + ri * (cell_h + ROW_GAP)
        stripe = QColor("#f0f0f0") if ri % 2 == 0 else QColor("#e8e8e8")
        painter.fillRect(0, y_base - 2, W, cell_h + 2, stripe)

        for ci, item in enumerate(row):
            colour = item.colour if hasattr(item, "colour") else item
            x = PAD + ci * cell_w
            y = y_base
            painter.setBrush(QBrush(colour))
            if ci == 0:
                painter.setPen(QPen(QColor(0, 0, 0, 160), 2))
            else:
                painter.setPen(QPen(QColor(0, 0, 0, 40), 1))
            painter.drawRoundedRect(x, y, SWATCH_W, SWATCH_H, 3, 3)
            if include_names:
                _draw_label(painter, _label_for(item, ci), x, y + SWATCH_H + 2)

    painter.end()
    return px

# ---------------------------------------------------------------------------
# Export Dialogs
# ---------------------------------------------------------------------------

ARRANGEMENTS = ["As picked", "Complementary", "Triadic", "Tetradic", "Analogous"]


class _BaseExportDialog(QDialog):
    """Shared base for all export dialogs."""

    def __init__(self, title: str, theme: dict, parent=None):
        super().__init__(parent)
        self.setWindowTitle(title)
        self.setModal(True)
        self.setMinimumWidth(380)
        t = theme
        self.setStyleSheet(f"""
            QDialog {{
                background-color: {t["window_bg"]};
                color: {t["text_primary"]};
            }}
            QLabel {{
                color: {t["text_primary"]};
                background: transparent;
            }}
            QCheckBox {{
                color: {t["text_primary"]};
                background: transparent;
            }}
            QCheckBox::indicator {{
                width: 14px; height: 14px;
                border: 1px solid {t["frame_border"]};
                border-radius: 2px;
                background: {t["panel_bg"]};
            }}
            QCheckBox::indicator:checked {{
                background: {t["highlight"]};
                border-color: {t["highlight"]};
            }}
            QRadioButton {{
                color: {t["text_primary"]};
                background: transparent;
            }}
            QRadioButton::indicator {{
                width: 13px; height: 13px;
                border: 1px solid {t["frame_border"]};
                border-radius: 7px;
                background: {t["panel_bg"]};
            }}
            QRadioButton::indicator:checked {{
                background: {t["highlight"]};
                border-color: {t["highlight"]};
            }}
            QLineEdit {{
                background: {t["panel_bg"]};
                color: {t["text_primary"]};
                border: 1px solid {t["input_border"]};
                border-radius: 2px;
                padding: 2px 6px;
            }}
            QPushButton {{
                background: {t["panel_bg"]};
                color: {t["text_primary"]};
                border: 1px solid {t["frame_border"]};
                border-radius: 3px;
                padding: 4px 14px;
                min-width: 72px;
            }}
            QPushButton:hover {{ background: {t["frame_bg"]}; }}
            QPushButton:default {{
                background: {t["highlight"]};
                color: #ffffff;
                border-color: {t["highlight"]};
            }}
            QGroupBox {{
                color: {t["text_secondary"]};
                border: 1px solid {t["frame_border"]};
                border-radius: 3px;
                margin-top: 8px;
                padding-top: 6px;
                font-size: 10px;
            }}
            QGroupBox::title {{
                subcontrol-origin: margin;
                subcontrol-position: top left;
                padding: 0 4px;
            }}
        """)

    def _make_buttons(self, ok_label: str = "Export…") -> QDialogButtonBox:
        bb = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        bb.button(QDialogButtonBox.StandardButton.Ok).setText(ok_label)
        bb.accepted.connect(self.accept)
        bb.rejected.connect(self.reject)
        return bb

    def _names_checkbox(self, has_names: bool) -> QCheckBox:
        cb = QCheckBox("Include colour names in export")
        cb.setChecked(has_names)
        return cb


class SaveSessionDialog(_BaseExportDialog):
    def __init__(self, has_names: bool, theme: dict, parent=None, initial_name: str = "Hue Palette"):
        super().__init__("Save Session", theme, parent)
        lay = QVBoxLayout(self)
        lay.setSpacing(10)
        lay.addWidget(QLabel("Save the current picked colours to a .txt file.\n"
                             "The file can be reloaded to restore this session."))
        name_row = QWidget()
        name_lay = QHBoxLayout(name_row)
        name_lay.setContentsMargins(0, 0, 0, 0)
        name_lay.addWidget(QLabel("Session name:"))
        self.name_edit = QLineEdit(initial_name)
        name_lay.addWidget(self.name_edit)
        lay.addWidget(name_row)
        self.cb_names = self._names_checkbox(has_names)
        lay.addWidget(self.cb_names)
        lay.addWidget(self._make_buttons("Save…"))

    @property
    def session_name(self) -> str:
        return self.name_edit.text().strip() or "Hue Palette"

    @property
    def include_names(self) -> bool:
        return self.cb_names.isChecked()


class LoadSessionDialog(_BaseExportDialog):
    def __init__(self, theme: dict, parent=None):
        super().__init__("Load Session", theme, parent)
        lay = QVBoxLayout(self)
        lay.setSpacing(10)
        lay.addWidget(QLabel(
            "Load a previously saved session file (.txt).\n\n"
            "This will ADD the loaded colours to your current list.\n"
            "Use Options → Session → Clear Current Session first if you want a clean start."
        ))
        lay.addWidget(self._make_buttons("Load…"))


class ExportGraphicDialog(_BaseExportDialog):
    """Single dialog for all graphic exports — SVG, PNG, HTML."""

    FORMATS = ["SVG", "PNG", "HTML"]

    def __init__(self, has_names: bool, theme: dict, parent=None, initial_name: str = "Hue Palette"):
        super().__init__("Export Graphic", theme, parent)
        lay = QVBoxLayout(self)
        lay.setSpacing(10)
        lay.addWidget(QLabel("Export the picked colours as a swatch graphic."))

        # Format
        fmt_grp = QGroupBox("File format")
        fmt_lay = QVBoxLayout(fmt_grp)
        fmt_lay.setSpacing(4)
        self._fmt_group = QButtonGroup(self)
        for i, f in enumerate(self.FORMATS):
            rb = QRadioButton(f)
            rb.setChecked(i == 0)
            self._fmt_group.addButton(rb, i)
            fmt_lay.addWidget(rb)
        lay.addWidget(fmt_grp)

        # Arrangement
        arr_grp = QGroupBox("Arrangement")
        arr_lay = QVBoxLayout(arr_grp)
        arr_lay.setSpacing(4)
        self._arr_group = QButtonGroup(self)
        for i, arr in enumerate(ARRANGEMENTS):
            rb = QRadioButton(arr)
            rb.setChecked(i == 0)
            self._arr_group.addButton(rb, i)
            arr_lay.addWidget(rb)
        lay.addWidget(arr_grp)

        # Palette name — drives the saved filename
        name_row = QWidget()
        name_lay = QHBoxLayout(name_row)
        name_lay.setContentsMargins(0, 0, 0, 0)
        name_lay.addWidget(QLabel("Palette name:"))
        self.name_edit = QLineEdit(initial_name)
        name_lay.addWidget(self.name_edit)
        lay.addWidget(name_row)

        self.cb_names = self._names_checkbox(has_names)
        lay.addWidget(self.cb_names)
        lay.addWidget(self._make_buttons("Export…"))

    @property
    def palette_name(self) -> str:
        return self.name_edit.text().strip() or "Hue Palette"

    @property
    def file_format(self) -> str:
        bid = self._fmt_group.checkedId()
        return self.FORMATS[bid] if bid >= 0 else "SVG"

    @property
    def arrangement(self) -> str:
        bid = self._arr_group.checkedId()
        return ARRANGEMENTS[bid] if bid >= 0 else "As picked"

    @property
    def include_names(self) -> bool:
        return self.cb_names.isChecked()


class ExportDataDialog(_BaseExportDialog):
    """Single dialog for all data exports — GPL, CSS, SCSS."""

    FORMATS = ["GPL — GIMP / Inkscape / Krita palette (.gpl)",
               "CSS variables (.css)",
               "SCSS variables (.scss)"]
    KEYS    = ["GPL", "CSS", "SCSS"]

    def __init__(self, has_names: bool, theme: dict, parent=None, initial_name: str = "Hue Palette"):
        super().__init__("Export Data", theme, parent)
        lay = QVBoxLayout(self)
        lay.setSpacing(10)
        lay.addWidget(QLabel("Export the picked colours as a data file\nfor use in other applications."))

        fmt_grp = QGroupBox("File format")
        fmt_lay = QVBoxLayout(fmt_grp)
        fmt_lay.setSpacing(4)
        self._fmt_group = QButtonGroup(self)
        for i, f in enumerate(self.FORMATS):
            rb = QRadioButton(f)
            rb.setChecked(i == 0)
            self._fmt_group.addButton(rb, i)
            fmt_lay.addWidget(rb)
        lay.addWidget(fmt_grp)

        # GPL palette name — only relevant for GPL
        self._name_row = QWidget()
        name_lay = QHBoxLayout(self._name_row)
        name_lay.setContentsMargins(0, 0, 0, 0)
        name_lay.addWidget(QLabel("Palette name:"))
        self.name_edit = QLineEdit(initial_name)
        name_lay.addWidget(self.name_edit)
        lay.addWidget(self._name_row)

        self._fmt_group.idToggled.connect(self._on_fmt_toggled)

        self.cb_names = self._names_checkbox(has_names)
        lay.addWidget(self.cb_names)
        lay.addWidget(self._make_buttons("Export…"))

    def _on_fmt_toggled(self, bid: int, checked: bool) -> None:
        if checked:
            self._name_row.setVisible(bid == 0)

    @property
    def file_format(self) -> str:
        bid = self._fmt_group.checkedId()
        return self.KEYS[bid] if bid >= 0 else "GPL"

    @property
    def palette_name(self) -> str:
        return self.name_edit.text().strip() or "Hue Palette"

    @property
    def include_names(self) -> bool:
        return self.cb_names.isChecked()


# ---------------------------------------------------------------------------
# Colour Chart Browser Dialog
# ---------------------------------------------------------------------------

class ColourChartBrowser(QDialog):
    """
    Scrollable swatch grid for all fixed palette systems.
    Tabs across the top select the system (RAL Classic, RAL Design, BS4800, NCS).
    Click any swatch to add that colour to the Picked Colours Frame.
    """

    SWATCH_W = 52
    SWATCH_H = 36
    COLS     = 7

    def __init__(self, theme: dict, add_colour_fn, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Colour Chart Browser")
        self.setModal(False)   # non-modal — user can keep picking while browsing
        self.setMinimumSize(430, 520)
        self._add_colour = add_colour_fn
        self._t = theme

        t = theme
        self.setStyleSheet(f"""
            QDialog {{
                background-color: {t['window_bg']};
                color: {t['text_primary']};
            }}
            QTabWidget::pane {{
                border: 1px solid {t['frame_border']};
                background-color: {t['frame_bg']};
            }}
            QTabBar::tab {{
                background-color: {t['panel_bg']};
                color: {t['text_secondary']};
                border: 1px solid {t['frame_border']};
                padding: 4px 10px;
                margin-right: 2px;
            }}
            QTabBar::tab:selected {{
                background-color: {t['frame_bg']};
                color: {t['text_primary']};
                border-bottom: 1px solid {t['frame_bg']};
            }}
            QScrollArea {{
                border: none;
                background-color: {t['frame_bg']};
            }}
            QLabel {{
                background: transparent;
                color: {t['text_primary']};
            }}
            QStatusBar, QLabel#status {{
                color: {t['text_secondary']};
                font-size: 10px;
                background: transparent;
            }}
        """)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(8, 8, 8, 8)
        outer.setSpacing(6)

        # Instruction label
        hint = QLabel("Click any swatch to add it to your Picked Colours list.")
        hint.setStyleSheet(f"color: {t['text_secondary']}; font-size: 10px; background: transparent;")
        outer.addWidget(hint)

        # Tab widget — one tab per system
        from PyQt6.QtWidgets import QTabWidget, QScrollArea
        self._tabs = QTabWidget()
        self._tabs.setFocusPolicy(Qt.FocusPolicy.NoFocus)

        # Normalise CSS_NAMED to same format as other palettes: {code: (r,g,b,name)}
        css_normalised = {name: (r, g, b, name) for name, (r, g, b) in CSS_NAMED.items()}

        palettes = [
            ("HTML/CSS",     css_normalised),
            ("RAL Classic",  RAL_CLASSIC),
            ("RAL Design",   RAL_DESIGN),
            ("BS4800",       BS4800),
            ("BS5252",       BS5252),
            ("NCS",          NCS),
            ("BS381C",       BS381C),
        ]
        for system_name, palette in palettes:
            scroll = QScrollArea()
            scroll.setWidgetResizable(True)
            scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)

            container = QWidget()
            container.setStyleSheet(f"background-color: {t['frame_bg']};")
            grid = QVBoxLayout(container)
            grid.setContentsMargins(6, 6, 6, 6)
            grid.setSpacing(4)

            # Build rows of swatches
            items = list(palette.items())
            row_widget = None
            row_layout = None
            for i, (code, entry) in enumerate(items):
                r, g, b, name = entry
                colour = QColor(r, g, b)
                hex_col = f"#{r:02X}{g:02X}{b:02X}"

                if i % self.COLS == 0:
                    row_widget = QWidget()
                    row_widget.setStyleSheet("background: transparent;")
                    row_layout = QHBoxLayout(row_widget)
                    row_layout.setContentsMargins(0, 0, 0, 0)
                    row_layout.setSpacing(4)
                    grid.addWidget(row_widget)

                swatch = QLabel()
                swatch.setFixedSize(self.SWATCH_W, self.SWATCH_H)
                swatch.setAlignment(Qt.AlignmentFlag.AlignCenter)
                swatch.setToolTip(f"{code}\n{name}\n{hex_col}")
                swatch.setCursor(Qt.CursorShape.PointingHandCursor)
                swatch.setStyleSheet(
                    f"background-color: {hex_col};"
                    f"border: 1px solid {t['frame_border']};"
                    f"border-radius: 2px;"
                )
                # Short label inside swatch — code only, clipped to fit
                font = swatch.font()
                font.setPointSize(6)
                swatch.setFont(font)
                # Use light or dark text depending on swatch luminance
                lum = 0.299 * r + 0.587 * g + 0.114 * b
                txt_col = "#000000" if lum > 140 else "#ffffff"
                short = code.replace("RAL ", "").replace("NCS S ", "").replace("NCS ", "").replace("BS381C ", "").replace("BS5252 ", "")
                swatch.setText(f'<span style="color:{txt_col};font-size:6pt;">{short}</span>')
                swatch.setTextFormat(Qt.TextFormat.RichText)

                # Capture colour and code for the click handler
                def _make_handler(c, lbl):
                    def handler(event):
                        if event.button() == Qt.MouseButton.LeftButton:
                            self._add_colour(c)
                            self._status.setText(f"Added: {lbl}")
                    return handler

                swatch.mousePressEvent = _make_handler(colour, f"{code} — {name}")
                row_layout.addWidget(swatch)

            # Pad last row
            remainder = len(items) % self.COLS
            if remainder:
                row_layout.addStretch()

            grid.addStretch()
            scroll.setWidget(container)
            self._tabs.addTab(scroll, system_name)

        outer.addWidget(self._tabs)

        # Status strip at bottom
        self._status = QLabel("Hover a swatch to see its code and name.")
        self._status.setObjectName("status")
        self._status.setStyleSheet(f"color: {t['text_secondary']}; font-size: 10px; background: transparent;")
        outer.addWidget(self._status)

        # Close button
        btn_row = QHBoxLayout()
        btn_row.addStretch()
        close_btn = QPushButton("Close")
        close_btn.setFixedHeight(24)
        close_btn.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        close_btn.clicked.connect(self.close)
        close_btn.setStyleSheet(f"""
            QPushButton {{
                background-color: {t['panel_bg']};
                color: {t['text_primary']};
                border: 1px solid {t['frame_border']};
                border-radius: 3px;
                padding: 2px 16px;
                font-size: 11px;
            }}
            QPushButton:hover {{ border-color: {t['highlight']}; }}
        """)
        btn_row.addWidget(close_btn)
        outer.addLayout(btn_row)


# ---------------------------------------------------------------------------
# Hotkey Configuration Dialog
# ---------------------------------------------------------------------------

class HotkeyDialog(QDialog):
    """
    Lets the user reassign the two global hotkeys.
    Click Edit then press the desired key combination.
    """

    DEFAULT_CAPTURE = "Alt+X"
    DEFAULT_UNDO    = "Ctrl+Z"

    CONFLICTS = {
        "Ctrl+C", "Ctrl+V", "Ctrl+X", "Ctrl+Z", "Ctrl+Y",
        "Ctrl+A", "Ctrl+S", "Ctrl+W", "Ctrl+Q", "Ctrl+T",
        "Alt+F4", "Alt+Tab",
    }

    def __init__(self, config: dict, theme: dict, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Configure Hotkeys")
        self.setModal(True)
        self.setMinimumWidth(420)
        self._capture_key = config.get("hotkey_capture", self.DEFAULT_CAPTURE)
        self._undo_key    = config.get("hotkey_undo",    self.DEFAULT_UNDO)
        self._listening   = None   # "capture" or "undo" when in key-listen mode
        t = theme
        self.setStyleSheet(f"""
            QDialog   {{ background: {t['window_bg']}; color: {t['text_primary']}; }}
            QLabel    {{ background: transparent; color: {t['text_primary']}; }}
            QLineEdit {{ background: {t['panel_bg']}; color: {t['text_primary']};
                         border: 1px solid {t['input_border']}; border-radius: 2px; padding: 2px 6px; }}
            QPushButton {{ background: {t['panel_bg']}; color: {t['text_primary']};
                           border: 1px solid {t['frame_border']}; border-radius: 3px;
                           padding: 3px 10px; min-width: 60px; }}
            QPushButton:hover   {{ background: {t['frame_bg']}; }}
            QPushButton:default {{ background: {t['highlight']}; color: #ffffff; border-color: {t['highlight']}; }}
            QPushButton#listening {{ background: {t['highlight']}; color: #ffffff; border-color: {t['highlight']}; }}
        """)

        lay = QVBoxLayout(self)
        lay.setSpacing(12)
        lay.addWidget(QLabel(
            "Click Edit next to a hotkey, then press the desired key combination."
        ))

        # Capture row
        lay.addWidget(self._make_row_label("Capture colour (default: Alt+X)"))
        self._capture_edit, self._capture_edit_btn, self._capture_reset_btn = self._make_row("capture")
        row1 = self._make_row_layout(self._capture_edit, self._capture_edit_btn, self._capture_reset_btn)
        lay.addLayout(row1)

        # Undo row
        lay.addWidget(self._make_row_label("Undo last colour (default: Ctrl+Z)"))
        self._undo_edit, self._undo_edit_btn, self._undo_reset_btn = self._make_row("undo")
        row2 = self._make_row_layout(self._undo_edit, self._undo_edit_btn, self._undo_reset_btn)
        lay.addLayout(row2)

        self._status_label = QLabel("")
        self._status_label.setStyleSheet("color: #ff6b6b; background: transparent;")
        lay.addWidget(self._status_label)

        # Reset all
        reset_all = QPushButton("Reset All to Defaults")
        reset_all.clicked.connect(self._reset_all)
        lay.addWidget(reset_all)

        bb = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        bb.accepted.connect(self._on_accept)
        bb.rejected.connect(self.reject)
        lay.addWidget(bb)

        self._refresh_display()

    def _make_row_label(self, text):
        lbl = QLabel(text)
        lbl.setStyleSheet("font-weight: bold; background: transparent;")
        return lbl

    def _make_row(self, which):
        edit = QLineEdit()
        edit.setReadOnly(True)
        edit.setMinimumWidth(160)
        edit_btn  = QPushButton("Edit")
        reset_btn = QPushButton("Reset")
        edit_btn.clicked.connect(lambda: self._start_listen(which))
        reset_btn.clicked.connect(lambda: self._reset_one(which))
        return edit, edit_btn, reset_btn

    def _make_row_layout(self, edit, edit_btn, reset_btn):
        row = QHBoxLayout()
        row.addWidget(edit)
        row.addWidget(edit_btn)
        row.addWidget(reset_btn)
        return row

    def _refresh_display(self):
        self._capture_edit.setText(self._capture_key)
        self._undo_edit.setText(self._undo_key)

    def _start_listen(self, which: str):
        self._listening = which
        self._status_label.setText("Press the desired key combination now…")
        if which == "capture":
            self._capture_edit.setText("… press keys …")
            self._capture_edit_btn.setObjectName("listening")
            self._capture_edit_btn.setStyleSheet(
                "background: #0078d4; color: #ffffff; border-color: #0078d4;"
            )
        else:
            self._undo_edit.setText("… press keys …")
            self._undo_edit_btn.setObjectName("listening")
            self._undo_edit_btn.setStyleSheet(
                "background: #0078d4; color: #ffffff; border-color: #0078d4;"
            )
        self.grabKeyboard()

    def keyPressEvent(self, event):
        if self._listening is None:
            super().keyPressEvent(event)
            return

        key  = event.key()
        mods = event.modifiers()

        # Ignore bare modifiers
        if key in (Qt.Key.Key_Control, Qt.Key.Key_Shift, Qt.Key.Key_Alt,
                   Qt.Key.Key_Meta, Qt.Key.Key_AltGr):
            return

        # Build combo string
        parts = []
        if mods & Qt.KeyboardModifier.ControlModifier: parts.append("Ctrl")
        if mods & Qt.KeyboardModifier.AltModifier:     parts.append("Alt")
        if mods & Qt.KeyboardModifier.ShiftModifier:   parts.append("Shift")
        key_name = Qt.Key(key).name.replace("Key_", "")
        parts.append(key_name)
        combo = "+".join(parts)

        which_was = self._listening
        self.releaseKeyboard()
        self._listening = None
        self._capture_edit_btn.setStyleSheet("")
        self._undo_edit_btn.setStyleSheet("")

        # Conflict check
        other = self._undo_key if which_was == "capture" else self._capture_key
        if combo == other:
            self._status_label.setText(f"⚠  {combo} is already assigned to the other action.")
            self._refresh_display()
            return
        if combo in self.CONFLICTS:
            self._status_label.setText(f"⚠  {combo} conflicts with a common system shortcut. Choose another.")
            self._refresh_display()
            return

        self._status_label.setText("")
        if which_was == "capture":
            self._capture_key = combo
        else:
            self._undo_key = combo
        self._refresh_display()

    def _reset_one(self, which: str):
        if which == "capture":
            self._capture_key = self.DEFAULT_CAPTURE
        else:
            self._undo_key = self.DEFAULT_UNDO
        self._status_label.setText("")
        self._refresh_display()

    def _reset_all(self):
        self._capture_key = self.DEFAULT_CAPTURE
        self._undo_key    = self.DEFAULT_UNDO
        self._status_label.setText("")
        self._refresh_display()

    def _on_accept(self):
        if self._capture_key == self._undo_key:
            self._status_label.setText("⚠  Both hotkeys cannot be the same.")
            return
        self.accept()

    @property
    def capture_key(self) -> str:
        return self._capture_key

    @property
    def undo_key(self) -> str:
        return self._undo_key


# ---------------------------------------------------------------------------
# Main window
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# Custom Title Bar
# ---------------------------------------------------------------------------

class TitleBar(QWidget):
    """
    Frameless custom title bar — themed, draggable, with minimise and close.
    Replaces the native OS title bar entirely.
    """

    def __init__(self, parent_window, parent=None):
        super().__init__(parent)
        self._win        = parent_window
        self._drag_pos   = None
        self.setFixedHeight(28)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(8, 0, 4, 0)
        layout.setSpacing(0)

        # App icon in title bar
        self._icon_lbl = QLabel()
        self._icon_lbl.setFixedSize(16, 16)
        self._icon_lbl.setStyleSheet("background: transparent; border: none;")
        _app_icon = load_app_icon()
        if not _app_icon.isNull():
            self._icon_lbl.setPixmap(_app_icon.pixmap(QSize(16, 16)))
        layout.addWidget(self._icon_lbl)

        layout.addSpacing(6)

        self._title_lbl = QLabel(f"{APP_NAME}  {APP_VERSION}")
        self._title_lbl.setStyleSheet(
            "font-size: 10px; font-weight: bold; background: transparent; border: none;"
        )
        layout.addWidget(self._title_lbl)
        layout.addStretch()

        # Minimise button
        self._btn_min = QToolButton()
        self._btn_min.setText("—")
        self._btn_min.setFixedSize(28, 22)
        self._btn_min.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self._btn_min.clicked.connect(self._win.showMinimized)
        layout.addWidget(self._btn_min)

        # Close button
        self._btn_close = QToolButton()
        self._btn_close.setText("✕")
        self._btn_close.setFixedSize(28, 22)
        self._btn_close.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self._btn_close.clicked.connect(self._win._exit_app)
        layout.addWidget(self._btn_close)

    def retheme(self, t: dict) -> None:
        self.setStyleSheet(
            f"QWidget {{ background-color: {t['menubar_bg']}; border-bottom: 1px solid {t['menubar_border']}; }}"
        )
        self._icon_lbl.setStyleSheet("background: transparent; border: none;")
        self._title_lbl.setStyleSheet(
            f"color: {t['text_primary']}; font-size: 10px; font-weight: bold; background: transparent; border: none;"
        )
        btn_base = f"""
            QToolButton {{
                background: transparent;
                color: {t['text_secondary']};
                border: none;
                font-size: 11px;
                padding: 0;
            }}
            QToolButton:hover {{
                background-color: {t['frame_border']};
                color: {t['text_primary']};
            }}
        """
        close_style = f"""
            QToolButton {{
                background: transparent;
                color: {t['text_secondary']};
                border: none;
                font-size: 11px;
                padding: 0;
            }}
            QToolButton:hover {{
                background-color: #c42b1c;
                color: #ffffff;
            }}
        """
        self._btn_min.setStyleSheet(btn_base)
        self._btn_close.setStyleSheet(close_style)

    # Drag to move the frameless window
    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self._drag_pos = event.globalPosition().toPoint() - self._win.frameGeometry().topLeft()
            event.accept()

    def mouseMoveEvent(self, event) -> None:
        if self._drag_pos is not None and event.buttons() == Qt.MouseButton.LeftButton:
            self._win.move(event.globalPosition().toPoint() - self._drag_pos)
            event.accept()

    def mouseReleaseEvent(self, event) -> None:
        self._drag_pos = None



class HueWindow(QMainWindow):

    def __init__(self, config: dict, protocol: str):
        super().__init__()
        self.config          = config
        self.protocol        = protocol
        self._tray_icon      = None
        self._current_colour = QColor(128, 128, 128)
        self._last_pos       = QPoint(-9999, -9999)
        self._search_previewing = False

        self._rollback = FormatRollback()
        self._palette_name   = "Hue Palette"   # resets each session
        self._last_system_theme = detect_system_theme()

        self._status_clear_timer = QTimer(self)
        self._status_clear_timer.setSingleShot(True)
        self._status_clear_timer.timeout.connect(self._clear_status)

        self._track_timer = QTimer(self)
        self._track_timer.setInterval(100)
        self._track_timer.timeout.connect(self._update_live_capture)

        self._theme_poll_timer = QTimer(self)
        self._theme_poll_timer.setInterval(3000)
        self._theme_poll_timer.timeout.connect(self._poll_system_theme)
        self._theme_poll_timer.start()

        self._build_ui()
        self._build_menu()
        self._build_tray()
        self._apply_config()

        self._track_timer.start()
        self.setFocus()

        if self.protocol == "wayland":
            self._set_status(
                "Running on Wayland — screen capture limited by compositor",
                colour="red", timeout_ms=6000
            )

    # ------------------------------------------------------------------
    # UI construction
    # ------------------------------------------------------------------

    def _build_ui(self) -> None:
        self.setWindowTitle(APP_NAME)
        self.setWindowIcon(load_app_icon())
        self.setMinimumWidth(WINDOW_WIDTH)
        # Remove native title bar — we draw our own themed one
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint |
            Qt.WindowType.Window
        )

        central = QWidget()
        self.setCentralWidget(central)
        master = QVBoxLayout(central)
        master.setContentsMargins(0, 0, 0, 0)
        master.setSpacing(0)

        # ── Custom title bar (top) ────────────────────────────────────────
        self.title_bar = TitleBar(self)
        master.addWidget(self.title_bar)

        # Inner content widget with padding
        content_widget = QWidget()
        content_layout = QVBoxLayout(content_widget)
        content_layout.setContentsMargins(6, 4, 6, 4)
        content_layout.setSpacing(0)
        master.addWidget(content_widget)
        master = content_layout  # remainder of _build_ui uses this

        # ── Top row: three panels side by side ───────────────────────────
        # [Current Colour | Magnifier | Picked Colours List]
        top_row = QHBoxLayout()
        top_row.setSpacing(4)
        top_row.setContentsMargins(0, 0, 0, 0)

        # Panel 1 — Current colour under cursor (top-left)
        colour_panel = QFrame()
        colour_panel.setFrameShape(QFrame.Shape.StyledPanel)
        colour_panel.setFrameShadow(QFrame.Shadow.Sunken)
        colour_panel.setFixedSize(100, 130)
        self._colour_panel = colour_panel
        cp_layout = QVBoxLayout(colour_panel)
        cp_layout.setContentsMargins(4, 4, 4, 4)
        cp_layout.setSpacing(2)

        self.lbl_swatch = QLabel()
        self.lbl_swatch.setMinimumHeight(88)
        self.lbl_swatch.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.lbl_swatch.setStyleSheet(
            "background-color: #808080; border-radius: 1px;"
        )
        self.lbl_swatch.setCursor(Qt.CursorShape.PointingHandCursor)
        self.lbl_swatch.mousePressEvent = self._swatch_clicked
        self.lbl_colour_code = QLabel("#808080")
        self.lbl_colour_code.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.lbl_colour_code.setStyleSheet(
            "font-size: 9px; font-weight: bold; background: transparent; border: none;"
        )
        self.lbl_coords = QLabel("X: —  Y: —")
        self.lbl_coords.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.lbl_coords.setStyleSheet(
            "font-size: 8px; background: transparent; border: none;"
        )
        cp_layout.addWidget(self.lbl_swatch)
        cp_layout.addWidget(self.lbl_colour_code)
        cp_layout.addWidget(self.lbl_coords)

        # Panel 2 — Magnifier (top-centre)
        mag_frame = QFrame()
        mag_frame.setFrameShape(QFrame.Shape.StyledPanel)
        mag_frame.setFrameShadow(QFrame.Shadow.Sunken)
        mag_frame.setFixedSize(130, 130)
        self._mag_frame = mag_frame
        mag_layout = QVBoxLayout(mag_frame)
        mag_layout.setContentsMargins(2, 2, 2, 2)
        self.magnifier = MagnifierWidget()
        self.magnifier.set_cursor_style(self.config.get("cursor_style", "Crosshair"))
        mag_layout.addWidget(self.magnifier)

        top_row.addWidget(colour_panel)
        top_row.addWidget(mag_frame)

        # Panel 3 — Picked Colours list (top-right, same height as other panels)
        self.picked_frame = PickedColoursFrame(
            self.config, self._set_status, self._rollback
        )
        self.picked_frame.setFixedHeight(130)
        self.picked_frame.setMinimumWidth(0)
        self.picked_frame.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        top_row.addWidget(self.picked_frame)

        # ── Bottom section: full-width frames stacked ─────────────────────
        self.spectrum_frame = SpectrumFrame(
            self.config,
            self._set_status,
            lambda colour: self.picked_frame.add_colour(colour)
        )
        frame4 = ColourAdjustFrame(self.config, self._set_status)
        self.adjust_frame = frame4

        self.output_frame = ColourOutputFrame(
            self.config,
            self._set_status,
            self._rollback,
            self._on_format_changed
        )
        self.output_frame.set_rollback_callback(self._perform_rollback)

        # Wire picked colours list → spectrum frame
        self.picked_frame.set_spectrum_callback(
            self.spectrum_frame.receive_colour_click,
            self.spectrum_frame
        )

        # Wire picked colours list → adjust frame (single click loads colour into editor)
        # adjust frame is self-contained — no on_change push to output frame
        self.adjust_frame.set_add_to_picked_callback(self.picked_frame.add_colour)
        self.picked_frame.list_widget.itemClicked.connect(self._on_picked_item_clicked_for_adjust)

        # Palette Code Search frame
        self.search_frame = PaletteSearchFrame(
            self.config,
            self._set_status,
            self._search_preview_colour
        )

        # ── Section headers ───────────────────────────────────────────────────
        def make_static_header(label_text: str) -> QLabel:
            """Non-collapsible header label (for the Preview section)."""
            lbl = QLabel(label_text)
            lbl.setFixedHeight(20)
            lbl.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
            lbl.setStyleSheet(
                "color: #777777; font-size: 9pt; font-weight: bold; "
                "padding: 2px 4px; background: transparent; border: none;"
            )
            return lbl

        def make_section_btn(label_text: str, target_widget: QWidget) -> QLabel:
            """Clickable header label that collapses/expands target_widget."""
            lbl = QLabel(f"▾  {label_text}")
            lbl.setFixedHeight(20)
            lbl.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
            lbl.setToolTip(f"Click to collapse / expand {label_text}")
            lbl.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
            lbl.setCursor(Qt.CursorShape.PointingHandCursor)
            lbl.setStyleSheet(
                "color: #777777; font-size: 9pt; font-weight: bold; "
                "padding: 2px 4px; background: transparent; border: none;"
            )
            lbl._collapsed = False

            def _click(event, w=target_widget, b=lbl, txt=label_text):
                b._collapsed = not b._collapsed
                w.setVisible(not b._collapsed)
                arrow = "▸" if b._collapsed else "▾"
                b.setText(f"{arrow}  {txt}")

            lbl.mousePressEvent = _click
            return lbl

        self._sec_hdr_preview    = make_static_header("Preview")
        self._sec_btn_spectrum   = make_section_btn("Spectrum",             self.spectrum_frame)
        self._sec_btn_format     = make_section_btn("Colour Format", self.output_frame)
        self._sec_btn_search     = make_section_btn("Palette Code Search",  self.search_frame)
        self._sec_btn_adjustment = make_section_btn("Colour Adjustment",    frame4)
        self._sec_btns = [
            self._sec_btn_spectrum,
            self._sec_btn_format,
            self._sec_btn_search,
            self._sec_btn_adjustment,
        ]

        master.addWidget(self._sec_hdr_preview)
        master.addLayout(top_row)
        master.addSpacing(2)
        master.addWidget(self._sec_btn_spectrum)
        master.addWidget(self.spectrum_frame)
        master.addSpacing(2)
        master.addWidget(self._sec_btn_format)
        master.addWidget(self.output_frame)
        master.addSpacing(2)
        master.addWidget(self._sec_btn_search)
        master.addWidget(self.search_frame)
        master.addSpacing(2)
        master.addWidget(self._sec_btn_adjustment)
        master.addWidget(frame4)
        master.addStretch()

        self.status_bar = QStatusBar()
        self.setStatusBar(self.status_bar)
        self.status_bar.showMessage("Ready")

        self.setStyleSheet(build_stylesheet(self._current_theme()))

    def _placeholder(self, title, min_h):
        t = self._current_theme()
        f = QFrame()
        f.setFrameShape(QFrame.Shape.StyledPanel)
        f.setFrameShadow(QFrame.Shadow.Sunken)
        f.setMinimumHeight(min_h)
        f.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        f.setStyleSheet(
            f"QFrame {{ background-color: {t['frame_bg']}; border: 1px solid {t['frame_border']}; border-radius: 2px; }}"
        )
        lay = QVBoxLayout(f)
        lay.setContentsMargins(8, 6, 8, 6)
        lbl = QLabel(title)
        lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        lbl.setStyleSheet(f"color: {t['text_dim']}; font-size: 11px; background: transparent; border: none;")
        lay.addWidget(lbl)
        if not hasattr(self, '_placeholder_frames'):
            self._placeholder_frames = []
        self._placeholder_frames.append((f, lbl))
        return f

    # ------------------------------------------------------------------
    # Menu
    # ------------------------------------------------------------------

    def _build_menu(self) -> None:
        mb = self.menuBar()

        fm = mb.addMenu("&File")
        act_file_save = QAction("&Save Session…", self)
        act_file_save.triggered.connect(self._do_save_session)
        fm.addAction(act_file_save)
        act_file_load = QAction("&Load Session…", self)
        act_file_load.triggered.connect(self._do_load_session)
        fm.addAction(act_file_load)
        fm.addSeparator()
        ax = QAction("E&xit", self)
        ax.setShortcut("Ctrl+Q")
        ax.triggered.connect(self._exit_app)
        fm.addAction(ax)

        em = mb.addMenu("&Export")

        act_graphic = QAction("Export &Graphic…", self)
        act_graphic.triggered.connect(self._do_export_graphic)
        em.addAction(act_graphic)

        act_data = QAction("Export &Data…", self)
        act_data.triggered.connect(self._do_export_data)
        em.addAction(act_data)

        om = mb.addMenu("&Options")
        dm = om.addMenu("Display")
        self.act_always_on_top = QAction("Always on &Top", self)
        self.act_always_on_top.setCheckable(True)
        self.act_always_on_top.setChecked(self.config.get("always_on_top", False))
        self.act_always_on_top.triggered.connect(self._toggle_always_on_top)
        dm.addAction(self.act_always_on_top)

        # Theme submenu
        theme_menu = dm.addMenu("&Theme")
        self._theme_actions = {}
        for theme_name in ("Light", "Dark", "System"):
            a = QAction(theme_name, self)
            a.setCheckable(True)
            a.setChecked(self.config.get("theme", "System") == theme_name)
            a.triggered.connect(lambda checked, tn=theme_name: self._set_theme(tn))
            theme_menu.addAction(a)
            self._theme_actions[theme_name] = a

        act_opacity = QAction("Window &Opacity…", self)
        act_opacity.triggered.connect(self._show_opacity_dialog)
        dm.addAction(act_opacity)

        # Magnifier zoom submenu
        zoom_menu = dm.addMenu("Magnifier &Zoom Level")
        self._zoom_actions = {}
        current_zoom = self.config.get("zoom_level", 4)
        for z in (2, 4, 8, 12, 16):
            a = QAction(f"{z}×", self)
            a.setCheckable(True)
            a.setChecked(z == current_zoom)
            a.triggered.connect(lambda checked, zv=z: self._set_zoom(zv))
            zoom_menu.addAction(a)
            self._zoom_actions[z] = a

        # Magnifier cursor style submenu
        cursor_menu = dm.addMenu("Magnifier &Cursor Style")
        self._cursor_actions = {}
        current_cursor = self.config.get("cursor_style", "Crosshair")
        for cs in ("Crosshair", "Micro Dot", "Pointer"):
            a = QAction(cs, self)
            a.setCheckable(True)
            a.setChecked(cs == current_cursor)
            a.triggered.connect(lambda checked, cv=cs: self._set_cursor_style(cv))
            cursor_menu.addAction(a)
            self._cursor_actions[cs] = a

        cm = om.addMenu("Colour Systems")
        dfm = cm.addMenu("Default Colour &Format")
        self._default_fmt_actions = {}
        current_default = self.config.get("default_colour_format", "HEX")
        for fmt_name in COLOUR_FORMATS:
            a = QAction(fmt_name, self)
            a.setCheckable(True)
            a.setChecked(fmt_name == current_default)
            a.triggered.connect(lambda checked, fn=fmt_name: self._set_default_format(fn))
            dfm.addAction(a)
            self._default_fmt_actions[fmt_name] = a

        act_chart = QAction("Open Colour &Chart Browser…", self)
        act_chart.triggered.connect(self._show_chart_browser)
        cm.addAction(act_chart)

        sm = om.addMenu("Session")
        self.act_persistence = QAction("&Persistence Mode", self)
        self.act_persistence.setCheckable(True)
        self.act_persistence.setChecked(self.config.get("persistence_mode", False))
        self.act_persistence.triggered.connect(self._toggle_persistence)
        sm.addAction(self.act_persistence)
        act_clear = QAction("&Clear Current Session", self)
        act_clear.triggered.connect(self._clear_session)
        sm.addAction(act_clear)
        sm.addSeparator()
        act_reset_all = QAction("&Reset All Settings to Defaults…", self)
        act_reset_all.triggered.connect(self._reset_all_settings)
        sm.addAction(act_reset_all)

        hm = om.addMenu("Hotkeys")
        ah = QAction("&Configure Hotkeys…", self)
        ah.triggered.connect(self._show_hotkey_dialog)
        hm.addAction(ah)

        hlp = mb.addMenu("&Help")
        aa = QAction("&About Hue", self)
        aa.triggered.connect(self._show_about)
        hlp.addAction(aa)

    # ------------------------------------------------------------------
    # Tray
    # ------------------------------------------------------------------

    def _build_tray(self) -> None:
        if not QSystemTrayIcon.isSystemTrayAvailable():
            return
        self._tray_icon = QSystemTrayIcon(make_tray_icon(), self)
        self._tray_icon.setToolTip(APP_NAME)
        tm = QMenu()
        a1 = QAction("&Show Hue", self)
        a1.triggered.connect(self._restore_from_tray)
        tm.addAction(a1)
        tm.addSeparator()
        a2 = QAction("E&xit", self)
        a2.triggered.connect(self._exit_app)
        tm.addAction(a2)
        self._tray_icon.setContextMenu(tm)
        self._tray_icon.activated.connect(self._on_tray_activated)
        # Tray icon hidden by default — app minimises to taskbar, not tray

    # ------------------------------------------------------------------
    # Config
    # ------------------------------------------------------------------

    def _apply_config(self) -> None:
        self.move(self.config.get("window_x", 100), self.config.get("window_y", 100))
        self.resize(self.config.get("window_w", WINDOW_WIDTH),
                    self.config.get("window_h", 740))
        self._apply_always_on_top(self.config.get("always_on_top", False))
        self.setWindowOpacity(max(0.2, min(1.0, float(self.config.get("window_opacity", 1.0)))))
        # Always start in the user's chosen default format
        default_fmt = self.config.get("default_colour_format", "HEX")
        self.config["colour_format"] = default_fmt
        self.output_frame.update_format_display(default_fmt)
        self.apply_theme()
        # Restore persisted session if persistence mode is on
        if self.config.get("persistence_mode", False):
            self._restore_session_from_config()

    def _current_theme(self) -> dict:
        setting = self.config.get("theme", "System")
        if setting == "System":
            return THEMES[detect_system_theme()]
        return THEMES.get(setting, THEMES["Dark"])

    def apply_theme(self) -> None:
        t = self._current_theme()
        self.setStyleSheet(build_stylesheet(t))
        self._apply_theme_to_frames(t)

    def _apply_theme_to_frames(self, t: dict) -> None:
        # Colour preview panel
        if hasattr(self, '_colour_panel'):
            self._colour_panel.setStyleSheet(
                f"QFrame {{ background-color: {t['panel_bg']}; border: 1px solid {t['panel_border']}; border-radius: 2px; }}"
            )
        if hasattr(self, 'lbl_colour_code'):
            self.lbl_colour_code.setStyleSheet(
                f"color: {t['text_primary']}; font-size: 9px; font-weight: bold; background: transparent; border: none;"
            )
        if hasattr(self, 'lbl_coords'):
            self.lbl_coords.setStyleSheet(
                f"color: {t['text_secondary']}; font-size: 8px; background: transparent; border: none;"
            )
        # Magnifier frame
        if hasattr(self, '_mag_frame'):
            self._mag_frame.setStyleSheet(
                f"QFrame {{ background-color: #111111; border: 1px solid {t['panel_border']}; border-radius: 2px; }}"
            )
        # Picked colours frame
        if hasattr(self, 'picked_frame'):
            self.picked_frame.retheme(t)
        # Spectrum frame
        if hasattr(self, 'spectrum_frame'):
            self.spectrum_frame.retheme(t)
        # Output frame
        if hasattr(self, 'output_frame'):
            self.output_frame.retheme(t)
        # Adjust frame
        if hasattr(self, 'adjust_frame'):
            self.adjust_frame.retheme(t)
        # Search frame
        if hasattr(self, 'search_frame'):
            self.search_frame.retheme(t)
        # Custom title bar
        if hasattr(self, 'title_bar'):
            self.title_bar.retheme(t)
        # Placeholder frames
        if hasattr(self, '_placeholder_frames'):
            for frame, lbl in self._placeholder_frames:
                frame.setStyleSheet(
                    f"QFrame {{ background-color: {t['frame_bg']}; border: 1px solid {t['frame_border']}; border-radius: 2px; }}"
                )
                lbl.setStyleSheet(
                    f"color: {t['text_dim']}; font-size: 11px; background: transparent; border: none;"
                )
        # Section headers — static and collapsible
        if hasattr(self, '_sec_hdr_preview'):
            self._sec_hdr_preview.setStyleSheet(
                f"color: {t['text_label']}; font-size: 9pt; font-weight: bold; "
                f"padding: 2px 4px; background: transparent; border: none;"
            )
        if hasattr(self, '_sec_btns'):
            for lbl in self._sec_btns:
                lbl.setStyleSheet(
                    f"color: {t['text_label']}; font-size: 9pt; font-weight: bold; "
                    f"padding: 2px 4px; background: transparent; border: none;"
                )
        # Status bar
        self.status_bar.setStyleSheet(f"""
            QStatusBar {{
                background-color: {t['statusbar_bg']};
                color: {t['text_secondary']};
                font-size: 11px;
                border-top: 1px solid {t['statusbar_border']};
            }}
        """)

    # ------------------------------------------------------------------
    # Format change + rollback
    # ------------------------------------------------------------------

    def _on_format_changed(self, new_fmt: str) -> None:
        self.config["colour_format"] = new_fmt          # keep config in sync
        default_fmt = self.config.get("default_colour_format", "HEX")

        if new_fmt != default_fmt:
            if not self._rollback.active:
                self._rollback.activate(
                    current_format=default_fmt,
                    entries=self.picked_frame.entries
                )
            self.output_frame.show_rollback_button(True)
        else:
            # Back to default — deactivate rollback
            self._rollback.deactivate()
            self.output_frame.show_rollback_button(False)

        self.picked_frame.refresh_format()
        live_code = format_colour_simple(self._current_colour, new_fmt)
        self.lbl_colour_code.setText(live_code)
        self.setFocus()

    def _perform_rollback(self) -> None:
        if not self._rollback.active:
            return

        default_fmt = self.config.get("default_colour_format", "HEX")
        restored_entries, _ = self._rollback.rollback(self.picked_frame.entries)
        self.picked_frame.replace_entries(restored_entries)

        # Restore config to default format
        self.config["colour_format"] = default_fmt

        # Update combo without re-triggering _on_format_changed
        self.output_frame.update_format_display(default_fmt)

        # Update all displays manually
        self.output_frame.set_colour(self._current_colour)
        self.picked_frame.refresh_format()
        self.lbl_colour_code.setText(format_colour_simple(self._current_colour, default_fmt))

        self.output_frame.show_rollback_button(False)

        count = len(restored_entries)
        self._set_status(
            f"Rolled back to {default_fmt} — {count} colour{'s' if count != 1 else ''} restored",
            colour="green", timeout_ms=5000
        )
        self.setFocus()

    def _on_picked_item_clicked_for_adjust(self, item) -> None:
        """Load the clicked picked colour into the adjustment editor."""
        idx = self.picked_frame.list_widget.row(item)
        if 0 <= idx < len(self.picked_frame.entries):
            self.adjust_frame.set_colour(self.picked_frame.entries[idx].colour)

    def _on_adjust_colour_changed(self, colour: QColor) -> None:
        """Called when the user moves a slider — update output frame live preview."""
        self.output_frame.set_colour(colour)

    def _cursor_over_own_window(self, cx: int, cy: int) -> bool:
        """Return True if the cursor is within this application window's bounds.
        Uses mapToGlobal from the central widget to get reliable screen coordinates
        regardless of window manager frame offsets."""
        cw = self.centralWidget()
        if cw is None:
            return self.frameGeometry().contains(cx, cy)
        # Top-left and bottom-right of the central widget in screen coords
        tl = cw.mapToGlobal(cw.rect().topLeft())
        br = cw.mapToGlobal(cw.rect().bottomRight())
        # Expand outward to include the title bar and window frame
        frame = self.frameGeometry()
        left   = min(tl.x(), frame.left())
        top    = min(tl.y(), frame.top())
        right  = max(br.x(), frame.right())
        bottom = max(br.y(), frame.bottom())
        return left <= cx <= right and top <= cy <= bottom

    # ------------------------------------------------------------------
    # Live capture
    # ------------------------------------------------------------------

    def _update_live_capture(self) -> None:
        # Do not overwrite a search result being previewed in the swatch
        if self._search_previewing:
            return

        gp = QCursor.pos()
        cx, cy = gp.x(), gp.y()

        over_taskbar = cursor_over_taskbar(cx, cy)
        frozen = over_taskbar

        if not frozen:
            colour = capture_pixel_colour(cx, cy)
            self._current_colour = colour

            hex_col = f"#{colour.red():02X}{colour.green():02X}{colour.blue():02X}"
            border = self._current_theme()['panel_border']
            self.lbl_swatch.setStyleSheet(
                f"background-color: {hex_col}; border: 1px solid {border}; border-radius: 1px;"
            )
            fmt = self.config.get("colour_format", "HEX")
            self.lbl_colour_code.setText(format_colour_simple(colour, fmt))
            self.output_frame.set_colour(colour)

        self.lbl_coords.setText(f"X: {cx}   Y: {cy}")

        current_pos = QPoint(cx, cy)
        if not frozen and current_pos != self._last_pos:
            self._last_pos = current_pos
            zoom = self.config.get("zoom_level", 4)
            # Capture enough pixels so that after scaling to widget size the zoom is correct.
            # Widget is roughly 200px wide; capture width = widget_width / zoom
            mag_w = self.magnifier.width() if self.magnifier.width() > 0 else 200
            cap_size = max(8, mag_w // zoom)
            raw = capture_magnifier_region(cx, cy, cap_size)
            self.magnifier.update_capture(raw, frozen=False)
        elif frozen:
            self.magnifier.update_capture(QPixmap(), frozen=True)

    # ------------------------------------------------------------------
    # Capture / undo
    # ------------------------------------------------------------------

    def capture_current_colour(self) -> None:
        # If a search result is loaded into the preview, add that colour
        if self._search_previewing:
            self.picked_frame.add_colour(self._current_colour)
            self._search_previewing = False
            self.search_frame.clear()
            self._restore_swatch_border()
        else:
            gp = QCursor.pos()
            colour = capture_pixel_colour(gp.x(), gp.y())
            self.picked_frame.add_colour(colour)
        if self.config.get("persistence_mode", False):
            self._save_session_to_config()
            save_config(self.config)

    def undo_last_colour(self) -> None:
        self.picked_frame.undo_last()
        if self.config.get("persistence_mode", False):
            self._save_session_to_config()
            save_config(self.config)

    # ------------------------------------------------------------------
    # Search preview
    # ------------------------------------------------------------------

    def _search_preview_colour(self, colour: QColor, label: str) -> None:
        """Called by PaletteSearchFrame when a match is found.
        Freezes the Live Capture swatch to show the searched colour.
        An amber border signals it is a search result, not the live cursor.
        """
        self._current_colour    = colour
        self._search_previewing = True
        hex_col = f"#{colour.red():02X}{colour.green():02X}{colour.blue():02X}"
        t = self._current_theme()
        self.lbl_swatch.setStyleSheet(
            f"background-color: {hex_col}; border: 2px solid #e8a020; border-radius: 1px;"
        )
        self.lbl_colour_code.setStyleSheet(
            f"color: {t['text_primary']}; font-size: 9px; font-weight: bold; background: transparent; border: none;"
        )
        self.lbl_colour_code.setText(label)
        self.output_frame.set_colour(colour)
        self._set_status(
            f"{label} — click swatch or Alt+X to add to Picked Colours",
            colour="green", timeout_ms=8000
        )

    def _swatch_clicked(self, event) -> None:
        """Left-click on the Live Capture swatch — adds colour to Picked Colours.
        In search preview mode: adds the searched colour.
        In normal mode: captures the current live colour under the cursor.
        """
        if event.button() != Qt.MouseButton.LeftButton:
            return
        if self._search_previewing:
            self.picked_frame.add_colour(self._current_colour)
            if self.config.get("persistence_mode", False):
                self._save_session_to_config()
                save_config(self.config)
            self._search_previewing = False
            self.search_frame.clear()
            self._restore_swatch_border()
        else:
            self.capture_current_colour()

    def _restore_swatch_border(self) -> None:
        """Restore the normal swatch border after a search preview is consumed."""
        hex_col = f"#{self._current_colour.red():02X}{self._current_colour.green():02X}{self._current_colour.blue():02X}"
        t = self._current_theme()
        self.lbl_swatch.setStyleSheet(
            f"background-color: {hex_col}; border: 1px solid {t['panel_border']}; border-radius: 1px;"
        )

    # ------------------------------------------------------------------
    # Key events
    # ------------------------------------------------------------------

    def keyPressEvent(self, event) -> None:
        mods = event.modifiers()
        key  = event.key()

        # Build combo string to match against config
        parts = []
        if mods & Qt.KeyboardModifier.ControlModifier: parts.append("Ctrl")
        if mods & Qt.KeyboardModifier.AltModifier:     parts.append("Alt")
        if mods & Qt.KeyboardModifier.ShiftModifier:   parts.append("Shift")
        key_name = Qt.Key(key).name.replace("Key_", "")
        parts.append(key_name)
        combo = "+".join(parts)

        if combo == self.config.get("hotkey_capture", "Alt+X"):
            self.capture_current_colour()
            return
        if combo == self.config.get("hotkey_undo", "Ctrl+Z"):
            self.undo_last_colour()
            return
        super().keyPressEvent(event)

    # ------------------------------------------------------------------
    # Actions
    # ------------------------------------------------------------------

    def _set_default_format(self, fmt_name: str) -> None:
        self.config["default_colour_format"] = fmt_name
        save_config(self.config)
        # Update checkmarks
        for name, action in self._default_fmt_actions.items():
            action.setChecked(name == fmt_name)
        self._set_status(f"Default colour format set to {fmt_name}", colour="green", timeout_ms=3000)

    def _set_theme(self, theme_name: str) -> None:
        self.config["theme"] = theme_name
        save_config(self.config)
        # Update checkmarks
        for name, action in self._theme_actions.items():
            action.setChecked(name == theme_name)
        self.apply_theme()
        self._set_status(f"Theme set to {theme_name}", colour="green", timeout_ms=3000)

    def _poll_system_theme(self) -> None:
        """Check if system theme has changed and reapply if in System mode."""
        if self.config.get("theme", "System") != "System":
            return
        current = detect_system_theme()
        if current != self._last_system_theme:
            self._last_system_theme = current
            self.apply_theme()

    def _toggle_always_on_top(self, checked: bool) -> None:
        self.config["always_on_top"] = checked
        self._apply_always_on_top(checked)
        save_config(self.config)
        self._set_status(
            "Always on Top enabled" if checked else "Always on Top disabled",
            colour="green" if checked else None
        )

    def _apply_always_on_top(self, on_top: bool) -> None:
        # Always preserve FramelessWindowHint — never let setWindowFlags drop it
        flags = (
            Qt.WindowType.FramelessWindowHint |
            Qt.WindowType.Window
        )
        if on_top:
            flags |= Qt.WindowType.WindowStaysOnTopHint
        self.setWindowFlags(flags)
        self.show()

    # ------------------------------------------------------------------
    # Export / Session handlers
    # ------------------------------------------------------------------

    def _has_names(self) -> bool:
        return any(e.name for e in self.picked_frame.entries)

    def _current_theme(self) -> dict:
        from_key = self.config.get("theme", "System")
        sys_dark = detect_system_theme()
        key = sys_dark if from_key == "System" else from_key
        return THEMES.get(key, THEMES["Dark"])

    def _last_export_dir(self) -> str:
        return self.config.get("last_export_dir", os.path.expanduser("~"))

    def _save_export_dir(self, path: str) -> None:
        self.config["last_export_dir"] = os.path.dirname(path)
        save_config(self.config)

    def _do_save_session(self) -> None:
        if not self.picked_frame.entries:
            self._set_status("Nothing to save — pick some colours first", colour="red", timeout_ms=4000)
            return
        dlg = SaveSessionDialog(self._has_names(), self._current_theme(), self, self._palette_name)
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return
        path, _ = QFileDialog.getSaveFileName(
            self, "Save Session",
            os.path.join(self._last_export_dir(),
                ("".join(c for c in dlg.session_name if c not in r'\/:*?"<>|').strip() or "Hue Palette") + ".txt"),
            "Text files (*.txt);;All files (*)"
        )
        if not path:
            return
        text = export_session_txt(self.picked_frame.entries, dlg.include_names, self.config.get('colour_format', 'HEX'))
        try:
            with open(path, "w", encoding="utf-8") as f:
                f.write(text)
            self._save_export_dir(path)
            self._palette_name = dlg.session_name
            self._set_status(f"Session saved: {os.path.basename(path)}", colour="green", timeout_ms=4000)
        except OSError as ex:
            self._set_status(f"Save failed: {ex}", colour="red", timeout_ms=6000)

    def _do_load_session(self) -> None:
        # If colours are present, ask whether to clear them first
        clear_first = False
        if self.picked_frame.entries:
            msg = QMessageBox(self)
            msg.setWindowTitle("Load Session")
            msg.setText("You have colours in the current session.")
            msg.setInformativeText(
                "Replace — clear the list and load the file\n"
                "Merge — keep existing colours and add the loaded ones"
            )
            msg.setMinimumWidth(420)
            # Force wider layout via stylesheet
            msg.setStyleSheet("QLabel { min-width: 380px; }")
            btn_replace = msg.addButton("Replace", QMessageBox.ButtonRole.AcceptRole)
            btn_merge   = msg.addButton("Merge",   QMessageBox.ButtonRole.NoRole)
            msg.addButton("Cancel", QMessageBox.ButtonRole.RejectRole)
            msg.setDefaultButton(btn_replace)
            msg.exec()
            clicked = msg.clickedButton()
            if clicked is None or clicked.text() == "Cancel":
                return
            clear_first = (clicked is btn_replace)

        path, _ = QFileDialog.getOpenFileName(
            self, "Load Session", self._last_export_dir(),
            "Text files (*.txt);;All files (*)"
        )
        if not path:
            return
        try:
            with open(path, "r", encoding="utf-8") as f:
                text = f.read()
        except OSError as ex:
            self._set_status(f"Load failed: {ex}", colour="red", timeout_ms=6000)
            return
        loaded, saved_fmt = load_session_txt(text)
        if not loaded:
            self._set_status("No valid colours found in that file", colour="red", timeout_ms=4000)
            return

        if clear_first:
            self.picked_frame.entries.clear()
            self._rollback.deactivate()
            self.output_frame.show_rollback_button(False)

        added = 0
        for entry in loaded:
            if len(self.picked_frame.entries) >= MAX_COLOURS:
                break
            self.picked_frame.entries.insert(0, entry)
            added += 1
        self.picked_frame._sync()
        self._save_export_dir(path)

        # Restore the format the session was saved in
        if saved_fmt in COLOUR_FORMATS:
            self.config["colour_format"] = saved_fmt
            self.output_frame.update_format_display(saved_fmt)
            self.picked_frame.refresh_format()

        self._set_status(
            f"Session loaded — {added} colour{'s' if added != 1 else ''} added  |  Format restored to {saved_fmt}",
            colour="green", timeout_ms=5000
        )

    def _do_export_graphic(self) -> None:
        if not self.picked_frame.entries:
            self._set_status("Nothing to export — pick some colours first", colour="red", timeout_ms=4000)
            return
        dlg = ExportGraphicDialog(self._has_names(), self._current_theme(), self, self._palette_name)
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return
        fmt      = self.config.get("colour_format", "HEX")
        file_fmt = dlg.file_format   # "SVG", "PNG", or "HTML"

        filters = {"SVG":  "SVG files (*.svg);;All files (*)",
                   "PNG":  "PNG files (*.png);;All files (*)",
                   "HTML": "HTML files (*.html);;All files (*)"}
        _illegal = r'\/:*?"<>|'
        _gfx_name = "".join(c for c in dlg.palette_name if c not in _illegal).strip() or "Hue Palette"
        default = {"SVG": f"{_gfx_name}.svg", "PNG": f"{_gfx_name}.png", "HTML": f"{_gfx_name}.html"}

        path, _ = QFileDialog.getSaveFileName(
            self, f"Export {file_fmt} Swatch",
            os.path.join(self._last_export_dir(), default[file_fmt]),
            filters[file_fmt]
        )
        if not path:
            return

        try:
            if file_fmt == "SVG":
                data = export_svg(self.picked_frame.entries, dlg.include_names,
                                  dlg.arrangement, fmt=fmt)
                with open(path, "w", encoding="utf-8") as f:
                    f.write(data)

            elif file_fmt == "PNG":
                pixmap = export_png_pixmap(self.picked_frame.entries, dlg.include_names,
                                           dlg.arrangement, fmt=fmt)
                if not pixmap.save(path, "PNG"):
                    self._set_status("PNG export failed — could not write file",
                                     colour="red", timeout_ms=6000)
                    return

            elif file_fmt == "HTML":
                data = export_html(self.picked_frame.entries, dlg.include_names,
                                   dlg.arrangement, fmt=fmt)
                with open(path, "w", encoding="utf-8") as f:
                    f.write(data)

            self._save_export_dir(path)
            self._palette_name = dlg.palette_name
            self._set_status(f"{file_fmt} swatch saved: {os.path.basename(path)}",
                             colour="green", timeout_ms=4000)
        except OSError as ex:
            self._set_status(f"Export failed: {ex}", colour="red", timeout_ms=6000)

    def _do_export_data(self) -> None:
        if not self.picked_frame.entries:
            self._set_status("Nothing to export — pick some colours first", colour="red", timeout_ms=4000)
            return
        dlg = ExportDataDialog(self._has_names(), self._current_theme(), self, self._palette_name)
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return
        file_fmt = dlg.file_format   # "GPL", "CSS", "SCSS"

        filters = {"GPL":  "GIMP Palette (*.gpl);;All files (*)",
                   "CSS":  "CSS files (*.css);;All files (*)",
                   "SCSS": "SCSS files (*.scss);;All files (*)"}
        _illegal = r'\/:*?"<>|'
        pal_name = "".join(c for c in dlg.palette_name if c not in _illegal).strip() or "Hue Palette"
        default = {"GPL": f"{pal_name}.gpl", "CSS": f"{pal_name}.css", "SCSS": f"{pal_name}.scss"}
        titles  = {"GPL": "Export GPL Palette", "CSS": "Export CSS Variables",
                   "SCSS": "Export SCSS Variables"}

        path, _ = QFileDialog.getSaveFileName(
            self, titles[file_fmt],
            os.path.join(self._last_export_dir(), default[file_fmt]),
            filters[file_fmt]
        )
        if not path:
            return

        try:
            if file_fmt == "GPL":
                data = export_gpl(self.picked_frame.entries, dlg.palette_name, dlg.include_names)
            elif file_fmt == "CSS":
                data = export_css(self.picked_frame.entries, dlg.include_names)
            elif file_fmt == "SCSS":
                data = export_scss(self.picked_frame.entries, dlg.include_names)

            with open(path, "w", encoding="utf-8") as f:
                f.write(data)
            self._save_export_dir(path)
            self._palette_name = dlg.palette_name
            self._set_status(f"{file_fmt} file saved: {os.path.basename(path)}",
                             colour="green", timeout_ms=4000)
        except OSError as ex:
            self._set_status(f"Export failed: {ex}", colour="red", timeout_ms=6000)

    # ------------------------------------------------------------------
    # Options — Display
    # ------------------------------------------------------------------

    def _show_opacity_dialog(self) -> None:
        from PyQt6.QtWidgets import QSlider, QDialogButtonBox
        dlg = QDialog(self)
        dlg.setWindowTitle("Window Opacity")
        dlg.setMinimumWidth(300)
        t = self._current_theme()
        dlg.setStyleSheet(f"""
            QDialog {{ background: {t['window_bg']}; color: {t['text_primary']}; }}
            QLabel  {{ background: transparent; color: {t['text_primary']}; }}
            QSlider::groove:horizontal {{ height: 6px; background: {t['frame_border']}; border-radius: 3px; }}
            QSlider::handle:horizontal {{ background: {t['highlight']}; width: 16px; height: 16px;
                margin: -5px 0; border-radius: 8px; }}
            QSlider::sub-page:horizontal {{ background: {t['highlight']}; border-radius: 3px; }}
            QPushButton {{ background: {t['panel_bg']}; color: {t['text_primary']};
                border: 1px solid {t['frame_border']}; border-radius: 3px; padding: 4px 14px; }}
            QPushButton:default {{ background: {t['highlight']}; color: #ffffff; border-color: {t['highlight']}; }}
        """)
        lay = QVBoxLayout(dlg)
        lay.setSpacing(12)
        current = int(self.config.get("window_opacity", 1.0) * 100)
        self._opacity_label = QLabel(f"Opacity: {current}%")
        self._opacity_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        lay.addWidget(self._opacity_label)
        slider = QSlider(Qt.Orientation.Horizontal)
        slider.setRange(20, 100)
        slider.setValue(current)
        slider.setTickInterval(10)
        slider.setTickPosition(QSlider.TickPosition.TicksBelow)
        slider.valueChanged.connect(self._preview_opacity)
        lay.addWidget(slider)
        bb = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        bb.accepted.connect(dlg.accept)
        bb.rejected.connect(dlg.reject)
        lay.addWidget(bb)
        if dlg.exec() == QDialog.DialogCode.Accepted:
            val = slider.value() / 100.0
            self.config["window_opacity"] = val
            self.setWindowOpacity(val)
            save_config(self.config)
            self._set_status(f"Opacity set to {slider.value()}%", timeout_ms=3000)
        else:
            # Restore original on cancel
            self.setWindowOpacity(self.config.get("window_opacity", 1.0))
            if hasattr(self, '_opacity_label'):
                del self._opacity_label

    def _preview_opacity(self, value: int) -> None:
        self.setWindowOpacity(value / 100.0)
        if hasattr(self, '_opacity_label'):
            self._opacity_label.setText(f"Opacity: {value}%")

    def _set_zoom(self, zoom: int) -> None:
        self.config["zoom_level"] = zoom
        # Update zoom action checkmarks
        for z, a in self._zoom_actions.items():
            a.setChecked(z == zoom)
        save_config(self.config)
        self._set_status(f"Magnifier zoom set to {zoom}x", colour="green", timeout_ms=3000)

    def _set_cursor_style(self, style: str) -> None:
        self.config["cursor_style"] = style
        self.magnifier.set_cursor_style(style)
        for s, a in self._cursor_actions.items():
            a.setChecked(s == style)
        save_config(self.config)
        self._set_status(f"Magnifier cursor: {style}", colour="green", timeout_ms=3000)

    # ------------------------------------------------------------------
    # Options — Persistence
    # ------------------------------------------------------------------

    def _toggle_persistence(self, checked: bool) -> None:
        self.config["persistence_mode"] = checked
        if checked:
            self._save_session_to_config()
            self._set_status("Persistence mode enabled — session will be restored on next launch",
                             colour="green", timeout_ms=5000)
        else:
            # Clear persisted session data
            self.config.pop("persisted_colours", None)
            self._set_status("Persistence mode disabled — next launch will start clean",
                             timeout_ms=5000)
        save_config(self.config)

    def _save_session_to_config(self) -> None:
        """Serialise current picked colours into config for persistence."""
        data = []
        for e in self.picked_frame.entries:
            data.append({
                "r": e.colour.red(), "g": e.colour.green(),
                "b": e.colour.blue(), "a": e.colour.alpha(),
                "name": e.name
            })
        self.config["persisted_colours"] = data

    def _restore_session_from_config(self) -> None:
        """Restore picked colours from config on launch (persistence mode)."""
        data = self.config.get("persisted_colours", [])
        if not data:
            return
        for item in data:
            c = QColor(item["r"], item["g"], item["b"], item.get("a", 255))
            self.picked_frame.entries.append(ColourEntry(c, item.get("name", "")))
        self.picked_frame._sync()
        count = len(self.picked_frame.entries)
        self._set_status(f"Previous session restored — {count} colour{'s' if count != 1 else ''} loaded",
                         colour="green", timeout_ms=5000)

    # ------------------------------------------------------------------
    # Options — Hotkeys
    # ------------------------------------------------------------------

    def _show_chart_browser(self) -> None:
        dlg = ColourChartBrowser(
            self._current_theme(),
            self.picked_frame.add_colour,
            self
        )
        dlg.show()

    def _show_hotkey_dialog(self) -> None:
        dlg = HotkeyDialog(self.config, self._current_theme(), self)
        if dlg.exec() == QDialog.DialogCode.Accepted:
            self.config["hotkey_capture"] = dlg.capture_key
            self.config["hotkey_undo"]    = dlg.undo_key
            save_config(self.config)
            self._set_status(
                f"Hotkeys saved — Capture: {dlg.capture_key}  Undo: {dlg.undo_key}",
                colour="green", timeout_ms=4000
            )

    def _reset_all_settings(self) -> None:
        reply = QMessageBox.question(
            self, "Reset All Settings to Defaults",
            "This will reset every application setting to its default value "
            "and clear the current session.\n\n"
            "This cannot be undone. Continue?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No
        )
        if reply != QMessageBox.StandardButton.Yes:
            return

        # Wipe config file and replace with defaults
        new_config = dict(DEFAULT_CONFIG)
        # Preserve window position
        new_config["window_x"] = self.config.get("window_x", 100)
        new_config["window_y"] = self.config.get("window_y", 100)
        self.config.clear()
        self.config.update(new_config)
        save_config(self.config)

        # Clear the picked colours list
        self.picked_frame.entries.clear()
        self.picked_frame._sync()
        self._rollback.deactivate()
        self.output_frame.show_rollback_button(False)

        # Apply all reset settings to the UI
        self.setWindowOpacity(1.0)
        self._apply_always_on_top(False)
        self.act_always_on_top.setChecked(False)
        self.act_persistence.setChecked(False)
        self.magnifier.set_cursor_style("Crosshair")
        for s, a in self._cursor_actions.items():
            a.setChecked(s == "Crosshair")
        for z, a in self._zoom_actions.items():
            a.setChecked(z == 4)
        for tn, a in self._theme_actions.items():
            a.setChecked(tn == "System")

        # Reset format to HEX
        self.config["colour_format"] = "HEX"
        self.output_frame.update_format_display("HEX")
        self.picked_frame.refresh_format()

        # Reset default format menu
        for fn, a in self._default_fmt_actions.items():
            a.setChecked(fn == "HEX")

        self.apply_theme()
        self._set_status("All settings reset to defaults", colour="green", timeout_ms=5000)

    def _clear_session(self) -> None:
        reply = QMessageBox.question(
            self, "Clear Session",
            "Are you sure you want to clear all picked colours?\nThis cannot be undone.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No
        )
        if reply == QMessageBox.StandardButton.Yes:
            self.picked_frame.entries.clear()
            self.picked_frame._sync()
            self._rollback.deactivate()
            self.output_frame.show_rollback_button(False)
            self._set_status("Session cleared", timeout_ms=3000)

    def _show_about(self) -> None:
        dlg = QDialog(self)
        dlg.setWindowTitle(f"About {APP_NAME}")
        dlg.setWindowIcon(load_app_icon())
        dlg.setMinimumWidth(400)
        dlg.setMaximumWidth(460)

        layout = QVBoxLayout(dlg)
        layout.setSpacing(12)
        layout.setContentsMargins(24, 20, 24, 20)

        # ── Icon + name row ──────────────────────────────────────────
        top_row = QHBoxLayout()
        top_row.setSpacing(16)

        icon_label = QLabel()
        app_icon = load_app_icon()
        if not app_icon.isNull():
            icon_label.setPixmap(app_icon.pixmap(QSize(64, 64)))
        else:
            icon_label.setFixedSize(64, 64)
        icon_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        top_row.addWidget(icon_label)

        title_col = QVBoxLayout()
        title_col.setSpacing(2)
        name_lbl = QLabel(APP_NAME)
        name_font = name_lbl.font()
        name_font.setPointSize(20)
        name_font.setBold(True)
        name_lbl.setFont(name_font)
        ver_lbl = QLabel(f"Version {APP_VERSION}")
        ver_lbl.setStyleSheet("color: grey;")
        import platform as _platform
        _os = _platform.system()
        if _os == "Windows":
            _platform_str = "Windows"
        elif _os == "Darwin":
            _platform_str = "macOS"
        else:
            _platform_str = "Linux — X11 &amp; Wayland"

        subtitle_lbl = QLabel(f"Universal screen colour picker for {_platform_str.replace(' &amp; Wayland', '').replace(' — X11', '')}"
                              if _os != "Linux" else "Universal screen colour picker for Linux")
        title_col.addWidget(name_lbl)
        title_col.addWidget(ver_lbl)
        title_col.addWidget(subtitle_lbl)
        title_col.addStretch()
        top_row.addLayout(title_col)
        top_row.addStretch()
        layout.addLayout(top_row)

        # ── Divider ──────────────────────────────────────────────────
        line = QFrame()
        line.setFrameShape(QFrame.Shape.HLine)
        line.setFrameShadow(QFrame.Shadow.Sunken)
        layout.addWidget(line)

        # ── Details ──────────────────────────────────────────────────
        details = QLabel(
            "<b>Colour Systems:</b> HEX, RGB, HSL, HSV, CMYK, "
            "RAL Classic, RAL Design, BS4800, NCS<br>"
            f"<b>Platform:</b> {_platform_str}<br>"
            "<b>Built with:</b> Python 3 and PyQt6<br><br>"
            "<b>Bad Kitty Software</b><br>"
            "Made in the UK 🇬🇧"
        )
        details.setWordWrap(True)
        details.setOpenExternalLinks(False)
        layout.addWidget(details)

        # ── Licence ──────────────────────────────────────────────────
        line2 = QFrame()
        line2.setFrameShape(QFrame.Shape.HLine)
        line2.setFrameShadow(QFrame.Shadow.Sunken)
        layout.addWidget(line2)

        lic = QLabel(
            "<small>Free to use and share for non-commercial purposes. "
            "Credit must be given to Bad Kitty Software. "
            "Modification, reverse engineering, decompilation, selling, or "
            "including this software in any commercial product requires the "
            "express written permission of Bad Kitty Software.</small>"
        )
        lic.setWordWrap(True)
        lic.setStyleSheet("color: grey;")
        layout.addWidget(lic)

        # ── Close button ─────────────────────────────────────────────
        btn_box = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        btn_box.rejected.connect(dlg.accept)
        layout.addWidget(btn_box)

        dlg.exec()

    # ------------------------------------------------------------------
    # Status bar
    # ------------------------------------------------------------------

    def _set_status(self, message: str, colour: str | None = None, timeout_ms: int = 4000) -> None:
        t = self._current_theme()
        if colour == "red":
            text_colour = "#dd4444" if t is THEMES["Light"] else "#ff6b6b"
        elif colour == "green":
            text_colour = "#2a7a2a" if t is THEMES["Light"] else "#6bcb77"
        else:
            text_colour = t['text_secondary']
        self.status_bar.setStyleSheet(f"""
            QStatusBar {{
                background-color: {t['statusbar_bg']};
                color: {text_colour};
                font-size: 11px;
                border-top: 1px solid {t['statusbar_border']};
            }}
        """)
        self.status_bar.showMessage(message)
        if timeout_ms > 0:
            self._status_clear_timer.start(timeout_ms)

    def _clear_status(self) -> None:
        t = self._current_theme()
        self.status_bar.setStyleSheet(f"""
            QStatusBar {{
                background-color: {t['statusbar_bg']};
                color: {t['text_secondary']};
                font-size: 11px;
                border-top: 1px solid {t['statusbar_border']};
            }}
        """)
        self.status_bar.showMessage("Ready")

    # ------------------------------------------------------------------
    # Window events
    # ------------------------------------------------------------------

    def changeEvent(self, event) -> None:
        super().changeEvent(event)

    def closeEvent(self, event) -> None:
        self._track_timer.stop()
        self._theme_poll_timer.stop()
        pos  = self.pos()
        size = self.size()
        self.config["window_x"] = pos.x()
        self.config["window_y"] = pos.y()
        self.config["window_w"] = size.width()
        self.config["window_h"] = size.height()
        save_config(self.config)
        if self._tray_icon:
            self._tray_icon.hide()
        event.accept()

    def _minimise_to_tray(self) -> None:
        """Minimise to taskbar."""
        self.showMinimized()

    def _restore_from_tray(self) -> None:
        self.showNormal()
        self.activateWindow()
        self.raise_()

    def _on_tray_activated(self, reason) -> None:
        # S1-03 fix: single click OR double click restores window
        if reason in (
            QSystemTrayIcon.ActivationReason.DoubleClick,
            QSystemTrayIcon.ActivationReason.Trigger
        ):
            self._restore_from_tray()

    def _exit_app(self) -> None:
        self._track_timer.stop()
        pos  = self.pos()
        size = self.size()
        self.config["window_x"] = pos.x()
        self.config["window_y"] = pos.y()
        self.config["window_w"] = size.width()
        self.config["window_h"] = size.height()
        save_config(self.config)
        if self._tray_icon:
            self._tray_icon.hide()
        QApplication.quit()


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main() -> None:
    os.makedirs(CONFIG_DIR, exist_ok=True)
    protocol = detect_display_protocol()
    app = QApplication(sys.argv)
    app.setApplicationName(APP_NAME)
    app.setWindowIcon(load_app_icon())
    app.setQuitOnLastWindowClosed(True)
    config = load_config()
    window = HueWindow(config, protocol)
    window.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
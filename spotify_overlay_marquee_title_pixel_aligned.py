import sys
import os
import re
import ctypes
import unicodedata
import json
import time
import threading
import hashlib
from pathlib import Path
from ctypes import wintypes

import psutil
import requests
from PyQt5 import QtWidgets, QtCore, QtGui

user32 = ctypes.windll.user32

GWL_EXSTYLE = -20
WS_EX_TRANSPARENT = 0x00000020
WS_EX_LAYERED = 0x00080000

GetWindowLongPtrW = user32.GetWindowLongPtrW
SetWindowLongPtrW = user32.SetWindowLongPtrW

GetWindowLongPtrW.argtypes = [wintypes.HWND, ctypes.c_int]
GetWindowLongPtrW.restype = ctypes.c_longlong if ctypes.sizeof(ctypes.c_void_p) == 8 else ctypes.c_long

SetWindowLongPtrW.argtypes = [wintypes.HWND, ctypes.c_int, ctypes.c_longlong]
SetWindowLongPtrW.restype = ctypes.c_longlong

REQUEST_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.5",
}

IGNORE_TITLES = {
    "spotify",
    "spotify free",
    "spotify premium",
    "advertisement",
}

SPOTIFY_TOKEN = None
SPOTIFY_TOKEN_EXPIRY = 0
SPOTIFY_TOKEN_LOCK = threading.Lock()

CACHE_LOCK = threading.RLock()
CACHE_INDEX = None
MEMORY_CACHE_LIMIT = 75


def get_cache_root():
    base = os.environ.get("LOCALAPPDATA")

    if not base:
        base = os.path.join(os.path.expanduser("~"), "AppData", "Local")

    root = Path(base) / "SpotifyOverlay" / "AlbumArtCache"
    root.mkdir(parents=True, exist_ok=True)
    return root


ALBUM_ART_CACHE_DIR = get_cache_root()
ALBUM_ART_INDEX_FILE = ALBUM_ART_CACHE_DIR / "index.json"


def load_cache_index():
    global CACHE_INDEX

    with CACHE_LOCK:
        if CACHE_INDEX is not None:
            return CACHE_INDEX

        try:
            if ALBUM_ART_INDEX_FILE.exists():
                with ALBUM_ART_INDEX_FILE.open("r", encoding="utf-8") as fh:
                    data = json.load(fh)

                if isinstance(data, dict):
                    CACHE_INDEX = data
                else:
                    CACHE_INDEX = {}
            else:
                CACHE_INDEX = {}
        except Exception:
            CACHE_INDEX = {}

        return CACHE_INDEX


def save_cache_index():
    with CACHE_LOCK:
        try:
            tmp_path = ALBUM_ART_INDEX_FILE.with_suffix(".json.tmp")

            with tmp_path.open("w", encoding="utf-8") as fh:
                json.dump(CACHE_INDEX or {}, fh, ensure_ascii=False, indent=2)

            tmp_path.replace(ALBUM_ART_INDEX_FILE)
        except Exception:
            pass


def image_extension_from_bytes(image_bytes):
    if not image_bytes:
        return ".img"

    if image_bytes.startswith(b"\xff\xd8"):
        return ".jpg"

    if image_bytes.startswith(b"\x89PNG"):
        return ".png"

    if image_bytes.startswith(b"RIFF") and image_bytes[8:12] == b"WEBP":
        return ".webp"

    return ".img"


def art_key_hash(art_key):
    return hashlib.sha256((art_key or "").encode("utf-8", errors="ignore")).hexdigest()


def cache_candidates_for_key(art_key):
    digest = art_key_hash(art_key)
    return [
        ALBUM_ART_CACHE_DIR / f"{digest}.jpg",
        ALBUM_ART_CACHE_DIR / f"{digest}.png",
        ALBUM_ART_CACHE_DIR / f"{digest}.webp",
        ALBUM_ART_CACHE_DIR / f"{digest}.img",
    ]


def safe_cached_file_bytes(filename):
    if not filename:
        return None

    try:
        path = ALBUM_ART_CACHE_DIR / filename

        if path.exists() and path.is_file() and path.stat().st_size >= 128:
            return path.read_bytes()
    except Exception:
        pass

    return None


def load_art_from_disk(art_key):
    with CACHE_LOCK:
        index = load_cache_index()
        record = index.get(art_key, {})

        if isinstance(record, dict):
            custom_bytes = safe_cached_file_bytes(record.get("custom_filename"))

            if custom_bytes:
                return custom_bytes

            fetched_bytes = safe_cached_file_bytes(record.get("filename"))

            if fetched_bytes:
                return fetched_bytes

        for path in cache_candidates_for_key(art_key):
            try:
                if path.exists() and path.is_file() and path.stat().st_size >= 1024:
                    return path.read_bytes()
            except Exception:
                pass

    return None


def save_art_to_disk(art_key, artist, track, image_bytes, art_url=None):
    if not art_key or not image_bytes or len(image_bytes) < 1024:
        return

    ext = image_extension_from_bytes(image_bytes)
    filename = f"{art_key_hash(art_key)}{ext}"
    path = ALBUM_ART_CACHE_DIR / filename

    with CACHE_LOCK:
        try:
            path.write_bytes(image_bytes)

            index = load_cache_index()
            existing = index.get(art_key, {})

            if not isinstance(existing, dict):
                existing = {}

            existing.update({
                "filename": filename,
                "artist": artist or "",
                "track": track or "",
                "source_url": art_url or "",
                "cached_at": int(time.time()),
                "size": len(image_bytes),
            })

            index[art_key] = existing
            save_cache_index()
        except Exception:
            pass


def save_custom_art_to_disk(art_key, artist, track, image_bytes):
    if not art_key or not image_bytes or len(image_bytes) < 128:
        return False

    filename = f"{art_key_hash(art_key)}.custom.png"
    path = ALBUM_ART_CACHE_DIR / filename

    with CACHE_LOCK:
        try:
            index = load_cache_index()
            existing = index.get(art_key, {})

            if not isinstance(existing, dict):
                existing = {}

            old_custom = existing.get("custom_filename")

            if old_custom and old_custom != filename:
                try:
                    old_path = ALBUM_ART_CACHE_DIR / old_custom

                    if old_path.exists() and old_path.is_file():
                        old_path.unlink()
                except Exception:
                    pass

            path.write_bytes(image_bytes)

            existing.update({
                "custom_filename": filename,
                "custom_artist": artist or "",
                "custom_track": track or "",
                "custom_cached_at": int(time.time()),
                "custom_size": len(image_bytes),
            })

            index[art_key] = existing
            save_cache_index()
            return True
        except Exception:
            return False


def get_spotify_token(session):
    global SPOTIFY_TOKEN, SPOTIFY_TOKEN_EXPIRY

    with SPOTIFY_TOKEN_LOCK:
        if SPOTIFY_TOKEN and time.time() < SPOTIFY_TOKEN_EXPIRY:
            return SPOTIFY_TOKEN

        try:
            resp = session.get("https://open.spotify.com/", headers=REQUEST_HEADERS, timeout=8)

            match = re.search(r'"accessToken"\s*:\s*"([^"]+)"', resp.text)
            if match:
                SPOTIFY_TOKEN = match.group(1)
                SPOTIFY_TOKEN_EXPIRY = time.time() + 3500
                return SPOTIFY_TOKEN

            token_resp = session.get(
                "https://open.spotify.com/get_access_token?reason=transport&productType=web_player",
                headers=REQUEST_HEADERS,
                timeout=8
            )

            if token_resp.status_code == 200:
                data = token_resp.json()

                if "accessToken" in data:
                    SPOTIFY_TOKEN = data["accessToken"]
                    expiry_ms = data.get("accessTokenExpirationTimestampMs")
                    SPOTIFY_TOKEN_EXPIRY = (expiry_ms / 1000) - 60 if expiry_ms else time.time() + 3500
                    return SPOTIFY_TOKEN

        except Exception:
            pass

    return None


def normalize_text(value):
    value = value or ""
    value = unicodedata.normalize("NFKD", value)
    value = "".join(ch for ch in value if not unicodedata.combining(ch))
    value = value.lower()
    value = value.replace("&", " and ")
    value = re.sub(r"[^a-z0-9]+", " ", value)
    return re.sub(r"\s+", " ", value).strip()


def strip_search_noise(value):
    value = value or ""
    value = re.sub(r"\s*\([^)]*(remaster|remastered|live|mono|stereo|explicit|clean|version|edit|mix|sped up|slowed|deluxe)[^)]*\)", "", value, flags=re.I)
    value = re.sub(r"\s*\[[^]]*(remaster|remastered|live|mono|stereo|explicit|clean|version|edit|mix|sped up|slowed|deluxe)[^]]*\]", "", value, flags=re.I)
    value = re.sub(r"\s+-\s+(remaster(ed)?|live|mono|stereo|radio edit|single edit|explicit|clean).*$", "", value, flags=re.I)
    value = re.sub(r"\s+feat\.?\s+.*$", "", value, flags=re.I)
    value = re.sub(r"\s+ft\.?\s+.*$", "", value, flags=re.I)
    value = re.sub(r"\s+with\s+.*$", "", value, flags=re.I)
    return re.sub(r"\s+", " ", value).strip()


def fetch_spotify_art_url(session, artist, track):
    token = get_spotify_token(session)

    if not token:
        return None

    c_artist = strip_search_noise(artist)
    c_track = strip_search_noise(track)

    headers = {
        "Authorization": f"Bearer {token}",
        "User-Agent": REQUEST_HEADERS["User-Agent"]
    }

    query = f"{c_artist} {c_track}"

    try:
        resp = session.get(
            "https://api.spotify.com/v1/search",
            headers=headers,
            params={"q": query, "type": "track", "limit": 3},
            timeout=7
        )
        resp.raise_for_status()
        items = resp.json().get("tracks", {}).get("items", [])

        if items:
            for item in items:
                result_artists = [a.get("name", "").lower() for a in item.get("artists", [])]

                if any(c_artist.lower() in ra or ra in c_artist.lower() for ra in result_artists):
                    images = item.get("album", {}).get("images", [])

                    if images:
                        return images[0].get("url")

            images = items[0].get("album", {}).get("images", [])

            if images:
                return images[0].get("url")

    except Exception:
        pass

    return None


def fetch_deezer_strict_fallback(session, artist, track):
    c_artist = strip_search_noise(artist)
    c_track = strip_search_noise(track)

    query = f'artist:"{c_artist}" track:"{c_track}"'

    try:
        resp = session.get("https://api.deezer.com/search", params={"q": query, "limit": 1}, timeout=5)
        data = resp.json().get("data", [])

        if data:
            return data[0].get("album", {}).get("cover_xl")

    except Exception:
        pass

    return None


def download_image_bytes(session, url):
    if not url:
        return None

    try:
        resp = session.get(url, headers=REQUEST_HEADERS, timeout=8)
        resp.raise_for_status()

        content_type = resp.headers.get("Content-Type", "").lower()
        looks_like_image = resp.content.startswith((b"\xff\xd8", b"\x89PNG", b"RIFF"))

        if "image" not in content_type and not looks_like_image:
            return None

        if len(resp.content) < 1024:
            return None

        return resp.content

    except Exception:
        return None


class ArtWorker(QtCore.QThread):
    result_signal = QtCore.pyqtSignal(str, object)

    def __init__(self, request_key, artist, track):
        super().__init__()
        self.request_key = request_key
        self.artist = artist
        self.track = track

    def run(self):
        cached_bytes = load_art_from_disk(self.request_key)

        if cached_bytes:
            self.result_signal.emit(self.request_key, cached_bytes)
            return

        session = requests.Session()
        session.headers.update(REQUEST_HEADERS)

        art_url = fetch_spotify_art_url(session, self.artist, self.track)

        if not art_url:
            art_url = fetch_deezer_strict_fallback(session, self.artist, self.track)

        image_bytes = download_image_bytes(session, art_url) if art_url else None

        if image_bytes:
            save_art_to_disk(self.request_key, self.artist, self.track, image_bytes, art_url)

        self.result_signal.emit(self.request_key, image_bytes)


def split_spotify_title(title):
    title = title or ""
    title = title.replace(" - Spotify", "")
    title = title.replace("Spotify - ", "")
    title = re.sub(r"\s+", " ", title).strip()

    if not title or title.lower() in IGNORE_TITLES:
        return None, None, None

    for sep in (" - ", " – ", " — "):
        if sep in title:
            artist, track = title.split(sep, 1)
            artist = artist.strip()
            track = track.strip()

            if artist and track:
                return title, artist, track

    return title, "", title


def get_spotify_window_info():
    found_titles = []

    def _enum_callback(hwnd, _lparam):
        try:
            if not user32.IsWindowVisible(hwnd):
                return True

            length = user32.GetWindowTextLengthW(hwnd)

            if length <= 0:
                return True

            buff = ctypes.create_unicode_buffer(length + 1)
            user32.GetWindowTextW(hwnd, buff, length + 1)
            title = buff.value.strip()

            if not title:
                return True

            pid = wintypes.DWORD()
            user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))

            proc = psutil.Process(pid.value)

            if proc.name().lower() == "spotify.exe":
                clean = title.replace(" - Spotify", "").replace("Spotify - ", "").strip()

                if clean and clean.lower() not in IGNORE_TITLES:
                    found_titles.append(title)

        except Exception:
            pass

        return True

    enum_windows_proc = ctypes.WINFUNCTYPE(ctypes.c_bool, wintypes.HWND, wintypes.LPARAM)
    user32.EnumWindows(enum_windows_proc(_enum_callback), 0)

    if not found_titles:
        return None, None, None

    parsed = []

    for title in found_titles:
        full, artist, track = split_spotify_title(title)

        if full:
            parsed.append((full, artist, track))

    if not parsed:
        return None, None, None

    parsed.sort(key=lambda item: (bool(item[1] and item[2]), len(item[0])), reverse=True)
    return parsed[0]


class MarqueeLabel(QtWidgets.QLabel):
    """Custom-painted pill label used for BOTH title and artist.

    Using one painter for both rows prevents stylesheet/QLabel padding from drifting
    between the sliding title and static artist label.
    """

    def __init__(
        self,
        text="",
        parent=None,
        scroll_enabled=True,
        font_size=16,
        fixed_height=28,
        bg_color=None,
        text_color=None,
    ):
        super().__init__("", parent)
        self._marquee_text = text or ""
        self._scroll_enabled = scroll_enabled
        self._fixed_height = fixed_height
        self._offset = 0.0
        self._direction = 1
        self._pause_ticks = 0
        self._edge_pause_ticks = 24
        self._scroll_speed = 0.55
        self._side_padding = 7
        self._gap_padding = 10
        self._radius = 6
        self._bg_color = bg_color or QtGui.QColor(255, 255, 255, 205)
        self._text_color = text_color or QtGui.QColor(0, 0, 0)

        font = QtGui.QFont("Arial Black", font_size)
        font.setStyleStrategy(QtGui.QFont.PreferAntialias)
        font.setBold(True)
        try:
            font.setWeight(QtGui.QFont.Black)
        except Exception:
            font.setWeight(87)
        self.setFont(font)

        self._timer = QtCore.QTimer(self)
        self._timer.setInterval(30)
        self._timer.timeout.connect(self._advance_marquee)

        self.setAttribute(QtCore.Qt.WA_TranslucentBackground, True)
        self.setAutoFillBackground(False)
        self.setSizePolicy(QtWidgets.QSizePolicy.Fixed, QtWidgets.QSizePolicy.Fixed)
        self.setMinimumHeight(fixed_height)
        self.setToolTip(self._marquee_text)
        self._update_timer_state()

    def setText(self, text):
        self._marquee_text = text or ""
        self._offset = 0.0
        self._direction = 1
        self._pause_ticks = self._edge_pause_ticks
        self.setToolTip(self._marquee_text)
        super().setText("")
        self._update_timer_state()
        self.update()

    def text(self):
        return self._marquee_text

    def sizeHint(self):
        width = self.width() if self.width() > 0 else 235
        return QtCore.QSize(width, self._fixed_height)

    def minimumSizeHint(self):
        width = self.width() if self.width() > 0 else 235
        return QtCore.QSize(width, self._fixed_height)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._offset = min(self._offset, self._max_offset())
        self._update_timer_state()

    def _available_text_width(self):
        return max(1, self.width() - (self._side_padding * 2))

    def _text_width(self):
        if not self._marquee_text:
            return 0
        return QtGui.QFontMetrics(self.font()).horizontalAdvance(self._marquee_text)

    def _max_offset(self):
        return max(0.0, float(self._text_width() - self._available_text_width() + self._gap_padding))

    def _needs_scroll(self):
        return self._scroll_enabled and bool(self._marquee_text) and self._text_width() > self._available_text_width()

    def _update_timer_state(self):
        if self._needs_scroll():
            if not self._timer.isActive():
                self._timer.start()
        else:
            self._timer.stop()
            self._offset = 0.0

    def _advance_marquee(self):
        max_offset = self._max_offset()

        if max_offset <= 0:
            self._timer.stop()
            self._offset = 0.0
            self.update()
            return

        if self._pause_ticks > 0:
            self._pause_ticks -= 1
            self.update()
            return

        self._offset += self._direction * self._scroll_speed

        if self._offset >= max_offset:
            self._offset = max_offset
            self._direction = -1
            self._pause_ticks = self._edge_pause_ticks
        elif self._offset <= 0:
            self._offset = 0.0
            self._direction = 1
            self._pause_ticks = self._edge_pause_ticks

        self.update()

    def paintEvent(self, event):
        painter = QtGui.QPainter(self)
        painter.setRenderHint(QtGui.QPainter.Antialiasing, True)
        painter.setRenderHint(QtGui.QPainter.TextAntialiasing, True)

        painter.setPen(QtCore.Qt.NoPen)
        painter.setBrush(QtGui.QBrush(self._bg_color))
        bg_rect = QtCore.QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        painter.drawRoundedRect(bg_rect, self._radius, self._radius)

        if not self._marquee_text:
            painter.end()
            return

        painter.setFont(self.font())
        painter.setPen(QtGui.QPen(self._text_color))

        # This exact content rect is shared by the title and artist labels.
        # Same widget x + same fixed width + same padding = pixel-aligned text.
        content_rect = self.rect().adjusted(self._side_padding, 0, -self._side_padding, 0)
        painter.setClipRect(content_rect)

        if self._needs_scroll():
            x = content_rect.left() - int(round(self._offset))
            draw_rect = QtCore.QRectF(x, 0, self._text_width() + self._gap_padding, self.height())
        else:
            draw_rect = QtCore.QRectF(content_rect)

        painter.drawText(draw_rect, QtCore.Qt.AlignVCenter | QtCore.Qt.AlignLeft, self._marquee_text)
        painter.end()

class UltimateOverlay(QtWidgets.QWidget):
    def __init__(self):
        super().__init__()

        self.setWindowFlags(
            QtCore.Qt.FramelessWindowHint |
            QtCore.Qt.WindowStaysOnTopHint |
            QtCore.Qt.Tool
        )

        self.setAttribute(QtCore.Qt.WA_TranslucentBackground)
        self.setWindowTitle("Spotify Overlay")

        self.hwnd = wintypes.HWND(int(self.winId()))
        self.click_through = False
        self.last_full_title = None
        self.current_art_key = None
        self.current_artist = ""
        self.current_track = ""
        self.default_opacity = 0.95

        self.setWindowOpacity(self.default_opacity)

        self.art_cache = {}
        self.pending_art_keys = set()
        self.active_workers = set()
        self.drag_pos = None

        self.init_ui()

        self.timer = QtCore.QTimer(self)
        self.timer.setInterval(1000)
        self.timer.timeout.connect(self.update_track_info)
        self.timer.start()

        self.setup_tray()

    def init_ui(self):
        self.main_layout = QtWidgets.QHBoxLayout()
        self.main_layout.setContentsMargins(10, 10, 10, 10)
        self.main_layout.setSpacing(12)

        self.art_label = QtWidgets.QLabel()
        self.art_label.setFixedSize(60, 60)
        self.art_label.setScaledContents(True)
        self.art_label.setStyleSheet("""
            QLabel {
                background-color: #222;
                border-radius: 8px;
                border: 1px solid #444;
            }
        """)

        self.placeholder_pix = self.make_placeholder_pixmap()
        self.art_label.setPixmap(self.placeholder_pix)
        self.art_label.setContextMenuPolicy(QtCore.Qt.CustomContextMenu)
        self.art_label.customContextMenuRequested.connect(self.show_album_art_context_menu)

        text_layout = QtWidgets.QVBoxLayout()
        text_layout.setContentsMargins(0, 0, 0, 0)
        text_layout.setSpacing(4)
        text_layout.setAlignment(QtCore.Qt.AlignVCenter | QtCore.Qt.AlignLeft)

        self.text_box_width = 235

        self.lbl_track = MarqueeLabel(
            "Waiting for Spotify...",
            scroll_enabled=True,
            font_size=16,
            fixed_height=28,
            bg_color=QtGui.QColor(255, 255, 255, 205),
        )
        self.lbl_track.setWordWrap(False)
        self.lbl_track.setFixedSize(self.text_box_width, 28)

        self.lbl_artist = MarqueeLabel(
            "",
            scroll_enabled=False,
            font_size=13,
            fixed_height=24,
            bg_color=QtGui.QColor(255, 255, 255, 185),
        )
        self.lbl_artist.setWordWrap(False)
        self.lbl_artist.setFixedSize(self.text_box_width, 24)

        text_layout.addWidget(self.lbl_track, 0, QtCore.Qt.AlignLeft)
        text_layout.addWidget(self.lbl_artist, 0, QtCore.Qt.AlignLeft)

        self.ctrl_layout = QtWidgets.QVBoxLayout()
        self.ctrl_layout.setSpacing(4)
        self.ctrl_layout.setContentsMargins(5, 0, 0, 0)

        self.btn_pin = QtWidgets.QPushButton("📌")
        self.btn_pin.setFixedSize(24, 24)
        self.btn_pin.setCheckable(True)
        self.btn_pin.setChecked(True)
        self.btn_pin.setToolTip("Toggle Always on Top")
        self.btn_pin.clicked.connect(self.toggle_pin)
        self.style_button(self.btn_pin)

        self.btn_click = QtWidgets.QPushButton("🖱️")
        self.btn_click.setFixedSize(24, 24)
        self.btn_click.setCheckable(True)
        self.btn_click.setToolTip("Enable Click-Through. Use tray to disable.")
        self.btn_click.clicked.connect(self.toggle_click_through)
        self.style_button(self.btn_click)

        self.ctrl_layout.addWidget(self.btn_pin)
        self.ctrl_layout.addWidget(self.btn_click)

        self.main_layout.addWidget(self.art_label)
        self.main_layout.addLayout(text_layout)
        self.main_layout.addStretch()
        self.main_layout.addLayout(self.ctrl_layout)

        self.setLayout(self.main_layout)

        self.setStyleSheet("""
            QWidget {
                background-color: rgba(20, 20, 20, 200);
                border-radius: 12px;
            }

            QLabel {
                background-color: transparent;
            }
        """)

    def make_placeholder_pixmap(self):
        pix = QtGui.QPixmap(60, 60)
        pix.fill(QtGui.QColor("#222"))

        painter = QtGui.QPainter(pix)
        painter.setRenderHint(QtGui.QPainter.Antialiasing)
        painter.setPen(QtGui.QPen(QtGui.QColor("#555"), 2))
        painter.drawEllipse(17, 17, 26, 26)
        painter.drawEllipse(27, 27, 6, 6)
        painter.end()

        return pix

    def style_button(self, btn):
        btn.setStyleSheet("""
            QPushButton {
                background-color: rgba(255, 255, 255, 25);
                color: white;
                border: none;
                border-radius: 4px;
                font-size: 13px;
            }

            QPushButton:hover {
                background-color: rgba(255, 255, 255, 55);
            }

            QPushButton:checked {
                background-color: rgba(29, 185, 84, 150);
            }
        """)

    def setup_tray(self):
        self.tray_icon = QtWidgets.QSystemTrayIcon(self)

        pix = QtGui.QPixmap(16, 16)
        pix.fill(QtGui.QColor("green"))
        self.tray_icon.setIcon(QtGui.QIcon(pix))

        menu = QtWidgets.QMenu()

        action_controls = menu.addAction("Show Controls")
        action_controls.setCheckable(True)
        action_controls.setChecked(True)
        action_controls.triggered.connect(self.toggle_controls_visibility)

        action_cache_folder = menu.addAction("Open Art Cache Folder")
        action_cache_folder.triggered.connect(self.open_cache_folder)

        menu.addSeparator()

        action_show = menu.addAction("Reset Position")
        action_show.triggered.connect(self.reset_position)

        action_click = menu.addAction("Disable Click-Through")
        action_click.triggered.connect(self.disable_click_through_from_tray)

        menu.addSeparator()

        action_quit = menu.addAction("Quit")
        action_quit.triggered.connect(QtWidgets.QApplication.instance().quit)

        self.tray_icon.setContextMenu(menu)
        self.tray_icon.show()

    def open_cache_folder(self):
        try:
            os.startfile(str(ALBUM_ART_CACHE_DIR))
        except Exception:
            pass

    def toggle_controls_visibility(self, visible):
        self.btn_pin.setVisible(visible)
        self.btn_click.setVisible(visible)
        self.adjustSize()

    def update_track_info(self):
        full, artist, track = get_spotify_window_info()

        if full == self.last_full_title:
            return

        self.last_full_title = full

        if not full:
            self.current_art_key = None
            self.current_artist = ""
            self.current_track = ""
            self.lbl_track.setText("No song playing")
            self.lbl_artist.setText("")
            self.art_label.setPixmap(self.placeholder_pix)
            return

        self.lbl_track.setText(track if track else full)
        self.lbl_artist.setText(artist if artist else "")

        if not artist or not track:
            self.current_art_key = None
            self.current_artist = ""
            self.current_track = ""
            self.art_label.setPixmap(self.placeholder_pix)
            return

        art_key = f"{normalize_text(artist)}::{normalize_text(track)}"
        self.current_art_key = art_key
        self.current_artist = artist
        self.current_track = track

        cached = self.art_cache.get(art_key, "__missing__")

        if cached != "__missing__":
            self.apply_art_bytes(art_key, cached)
            return

        disk_cached = load_art_from_disk(art_key)

        if disk_cached:
            self.art_cache[art_key] = disk_cached
            self.apply_art_bytes(art_key, disk_cached)
            self.trim_memory_cache()
            return

        self.art_label.setPixmap(self.placeholder_pix)
        self.start_art_worker(art_key, artist, track)

    def start_art_worker(self, art_key, artist, track):
        if art_key in self.pending_art_keys:
            return

        self.pending_art_keys.add(art_key)

        worker = ArtWorker(art_key, artist, track)
        self.active_workers.add(worker)
        worker.result_signal.connect(self.update_art)

        def cleanup(w=worker, key=art_key):
            self.active_workers.discard(w)
            self.pending_art_keys.discard(key)

        worker.finished.connect(cleanup)
        worker.start()

    def update_art(self, art_key, image_bytes):
        self.art_cache[art_key] = image_bytes
        self.trim_memory_cache()
        self.apply_art_bytes(art_key, image_bytes)

    def trim_memory_cache(self):
        while len(self.art_cache) > MEMORY_CACHE_LIMIT:
            oldest_key = next(iter(self.art_cache))
            self.art_cache.pop(oldest_key, None)

    def apply_art_bytes(self, art_key, image_bytes):
        if art_key != self.current_art_key:
            return

        if not image_bytes:
            self.art_label.setPixmap(self.placeholder_pix)
            return

        pixmap = QtGui.QPixmap()

        if not pixmap.loadFromData(image_bytes):
            self.art_label.setPixmap(self.placeholder_pix)
            return

        self.art_label.setPixmap(pixmap)

    def show_album_art_context_menu(self, pos):
        menu = QtWidgets.QMenu(self)
        replace_action = menu.addAction("Replace Album Art")
        replace_action.setEnabled(bool(self.current_art_key and self.current_artist and self.current_track))

        chosen = menu.exec_(self.art_label.mapToGlobal(pos))

        if chosen == replace_action:
            self.replace_album_art_for_current_song()

    def replace_album_art_for_current_song(self):
        if not self.current_art_key:
            return

        file_path, _ = QtWidgets.QFileDialog.getOpenFileName(
            self,
            "Replace Album Art",
            str(Path.home()),
            "Images (*.png *.jpg *.jpeg *.webp *.bmp *.gif);;All Files (*.*)"
        )

        if not file_path:
            return

        pixmap = QtGui.QPixmap(file_path)

        if pixmap.isNull():
            QtWidgets.QMessageBox.warning(self, "Invalid Image", "That file could not be loaded as an image.")
            return

        image_bytes = self.pixmap_to_png_bytes(pixmap)

        if not image_bytes:
            QtWidgets.QMessageBox.warning(self, "Invalid Image", "That image could not be converted for caching.")
            return

        saved = save_custom_art_to_disk(
            self.current_art_key,
            self.current_artist,
            self.current_track,
            image_bytes
        )

        if not saved:
            QtWidgets.QMessageBox.warning(self, "Cache Error", "The replacement art could not be saved to the local cache.")
            return

        self.art_cache[self.current_art_key] = image_bytes
        self.trim_memory_cache()
        self.apply_art_bytes(self.current_art_key, image_bytes)

    def pixmap_to_png_bytes(self, pixmap):
        byte_array = QtCore.QByteArray()
        buffer = QtCore.QBuffer(byte_array)

        if not buffer.open(QtCore.QIODevice.WriteOnly):
            return None

        try:
            if not pixmap.save(buffer, "PNG"):
                return None

            return bytes(byte_array)
        finally:
            buffer.close()

    def toggle_pin(self):
        is_pinned = self.btn_pin.isChecked()
        self.setWindowFlag(QtCore.Qt.WindowStaysOnTopHint, is_pinned)
        self.show()

    def toggle_click_through(self):
        self.click_through = self.btn_click.isChecked()
        self.apply_click_through()

    def disable_click_through_from_tray(self):
        self.click_through = False
        self.btn_click.setChecked(False)
        self.apply_click_through()

    def apply_click_through(self):
        ex_style = GetWindowLongPtrW(self.hwnd, GWL_EXSTYLE)

        if self.click_through:
            new_style = ex_style | WS_EX_TRANSPARENT | WS_EX_LAYERED
        else:
            new_style = ex_style & ~WS_EX_TRANSPARENT

        SetWindowLongPtrW(self.hwnd, GWL_EXSTYLE, new_style)

    def reset_position(self):
        self.move(100, 100)
        self.show()
        self.activateWindow()

    def mousePressEvent(self, event):
        if event.button() == QtCore.Qt.LeftButton and not self.click_through:
            self.drag_pos = event.globalPos() - self.frameGeometry().topLeft()
            event.accept()

    def mouseMoveEvent(self, event):
        if event.buttons() == QtCore.Qt.LeftButton and self.drag_pos and not self.click_through:
            self.move(event.globalPos() - self.drag_pos)
            event.accept()

    def closeEvent(self, event):
        self.timer.stop()

        for worker in list(self.active_workers):
            worker.quit()
            worker.wait(500)

        self.tray_icon.hide()
        event.accept()


def main():
    QtWidgets.QApplication.setAttribute(QtCore.Qt.AA_EnableHighDpiScaling, True)
    QtWidgets.QApplication.setAttribute(QtCore.Qt.AA_UseHighDpiPixmaps, True)

    app = QtWidgets.QApplication(sys.argv)
    app.setQuitOnLastWindowClosed(False)

    overlay = UltimateOverlay()
    overlay.resize(375, 85)

    screen_geom = app.primaryScreen().availableGeometry()

    x = screen_geom.right() - 395
    y = screen_geom.top() + 20

    overlay.move(x, y)
    overlay.show()

    sys.exit(app.exec_())


if __name__ == "__main__":
    main()

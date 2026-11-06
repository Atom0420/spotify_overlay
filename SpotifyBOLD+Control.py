import sys
import ctypes
from ctypes import wintypes
import psutil
import requests
from PyQt5 import QtWidgets, QtCore, QtGui

user32 = ctypes.windll.user32

GWL_EXSTYLE = -20
WS_EX_TRANSPARENT = 0x00000020
WS_EX_LAYERED = 0x00080000

VK_MEDIA_NEXT_TRACK = 0xB0
VK_MEDIA_PREV_TRACK = 0xB1
VK_MEDIA_PLAY_PAUSE = 0xB3
KEYEVENTF_KEYUP = 0x0002

GetWindowLongPtrW = user32.GetWindowLongPtrW
SetWindowLongPtrW = user32.SetWindowLongPtrW

GetWindowLongPtrW.argtypes = [wintypes.HWND, ctypes.c_int]
GetWindowLongPtrW.restype = (
    ctypes.c_longlong if ctypes.sizeof(ctypes.c_void_p) == 8 else ctypes.c_long
)

SetWindowLongPtrW.argtypes = [wintypes.HWND, ctypes.c_int, ctypes.c_longlong]
SetWindowLongPtrW.restype = ctypes.c_longlong


def send_media_key(vk_code):
    user32.keybd_event(vk_code, 0, 0, 0)
    user32.keybd_event(vk_code, 0, KEYEVENTF_KEYUP, 0)


class ArtWorker(QtCore.QThread):
    result_signal = QtCore.pyqtSignal(object)

    def __init__(self, artist, track):
        super().__init__()
        self.artist = artist
        self.track = track

    def run(self):
        try:
            term = f"{self.artist} {self.track}"

            resp = requests.get(
                "https://itunes.apple.com/search",
                params={
                    "term": term,
                    "limit": 1,
                    "media": "music"
                },
                timeout=5
            )

            data = resp.json()

            if data.get("resultCount", 0) > 0:
                art_url = data["results"][0].get("artworkUrl100")

                if art_url:
                    art_url = art_url.replace("100x100bb", "600x600bb")

                    img_resp = requests.get(art_url, timeout=5)

                    pixmap = QtGui.QPixmap()
                    pixmap.loadFromData(img_resp.content)

                    self.result_signal.emit(pixmap)
                    return

        except Exception:
            pass

        self.result_signal.emit(None)


def get_spotify_window_info():
    found_title = []

    def _enum_callback(hwnd, lParam):
        if user32.IsWindowVisible(hwnd):
            length = user32.GetWindowTextLengthW(hwnd)

            if length > 0:
                buff = ctypes.create_unicode_buffer(length + 1)
                user32.GetWindowTextW(hwnd, buff, length + 1)

                title = buff.value

                pid = wintypes.DWORD()
                user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))

                try:
                    proc = psutil.Process(pid.value)

                    if proc.name().lower() == "spotify.exe":
                        found_title.append(title)

                except Exception:
                    pass

        return True

    EnumWindowsProc = ctypes.WINFUNCTYPE(
        ctypes.c_bool,
        wintypes.HWND,
        wintypes.LPARAM
    )

    user32.EnumWindows(EnumWindowsProc(_enum_callback), 0)

    if not found_title:
        return None, None, None

    raw_title = max(found_title, key=len)

    clean_title = (
        raw_title
        .replace(" - Spotify", "")
        .replace("Spotify - ", "")
        .strip()
    )

    if not clean_title or clean_title.lower() == "spotify":
        return None, None, None

    parts = [p.strip() for p in clean_title.split(" - ")]

    if len(parts) >= 2:
        artist = parts[0]
        track = " - ".join(parts[1:])
    else:
        artist = ""
        track = clean_title

    return clean_title, artist, track


class UltimateOverlay(QtWidgets.QWidget):
    def __init__(self):
        super().__init__()

        self.setWindowFlags(
            QtCore.Qt.FramelessWindowHint |
            QtCore.Qt.WindowStaysOnTopHint |
            QtCore.Qt.Tool
        )

        self.setAttribute(QtCore.Qt.WA_TranslucentBackground)

        self.click_through = False
        self.controls_visible = True
        self.last_full_title = None
        self.drag_pos = None

        self.hwnd = wintypes.HWND(int(self.winId()))

        self.init_ui()

        self.timer = QtCore.QTimer()
        self.timer.setInterval(1000)
        self.timer.timeout.connect(self.update_track_info)
        self.timer.start()

        self.setup_tray()

    def init_ui(self):
        self.main_layout = QtWidgets.QHBoxLayout()
        self.main_layout.setContentsMargins(12, 12, 12, 12)
        self.main_layout.setSpacing(12)

        # =========================
        # Album Art
        # =========================

        self.art_label = QtWidgets.QLabel()
        self.art_label.setFixedSize(72, 72)
        self.art_label.setScaledContents(True)

        self.art_label.setStyleSheet("""
            QLabel {
                background-color: rgb(28, 28, 28);
                border-radius: 10px;
                border: 1px solid rgba(255,255,255,25);
            }
        """)

        self.placeholder_pix = QtGui.QPixmap(72, 72)
        self.placeholder_pix.fill(QtGui.QColor("#222"))

        self.art_label.setPixmap(self.placeholder_pix)

        # =========================
        # Text Layout
        # =========================

        text_layout = QtWidgets.QVBoxLayout()
        text_layout.setSpacing(5)

        self.lbl_track = QtWidgets.QLabel("Waiting for Spotify...")
        self.lbl_track.setStyleSheet("""
            QLabel {
                color: black;

                font-family: 'Arial Black', 'Segoe UI Black';
                font-size: 16px;
                font-weight: 900;

                background-color: rgba(255,255,255,210);

                border-radius: 7px;
                padding: 3px 8px;
            }
        """)

        self.lbl_artist = QtWidgets.QLabel("")
        self.lbl_artist.setStyleSheet("""
            QLabel {
                color: black;

                font-family: 'Arial Black', 'Segoe UI Black';
                font-size: 13px;
                font-weight: 900;

                background-color: rgba(255,255,255,185);

                border-radius: 7px;
                padding: 3px 8px;
            }
        """)

        # =========================
        # Multimedia Controls
        # =========================

        self.media_controls_layout = QtWidgets.QHBoxLayout()
        self.media_controls_layout.setSpacing(8)

        self.btn_prev = QtWidgets.QPushButton("⏮")
        self.btn_prev.setFixedSize(42, 30)
        self.btn_prev.clicked.connect(
            lambda: send_media_key(VK_MEDIA_PREV_TRACK)
        )

        self.btn_play = QtWidgets.QPushButton("⏯")
        self.btn_play.setFixedSize(42, 30)
        self.btn_play.clicked.connect(
            lambda: send_media_key(VK_MEDIA_PLAY_PAUSE)
        )

        self.btn_next = QtWidgets.QPushButton("⏭")
        self.btn_next.setFixedSize(42, 30)
        self.btn_next.clicked.connect(
            lambda: send_media_key(VK_MEDIA_NEXT_TRACK)
        )

        self.style_button(self.btn_prev)
        self.style_button(self.btn_play)
        self.style_button(self.btn_next)

        self.media_controls_layout.addWidget(self.btn_prev)
        self.media_controls_layout.addWidget(self.btn_play)
        self.media_controls_layout.addWidget(self.btn_next)
        self.media_controls_layout.addStretch()

        text_layout.addWidget(self.lbl_track)
        text_layout.addWidget(self.lbl_artist)
        text_layout.addLayout(self.media_controls_layout)

        # =========================
        # Side Controls
        # =========================

        self.side_controls_layout = QtWidgets.QVBoxLayout()
        self.side_controls_layout.setSpacing(6)

        self.btn_pin = QtWidgets.QPushButton("📌")
        self.btn_pin.setFixedSize(32, 28)
        self.btn_pin.setCheckable(True)
        self.btn_pin.setChecked(True)
        self.btn_pin.clicked.connect(self.toggle_pin)

        self.btn_click = QtWidgets.QPushButton("🖱️")
        self.btn_click.setFixedSize(32, 28)
        self.btn_click.setCheckable(True)
        self.btn_click.clicked.connect(self.toggle_click_through)

        self.style_button(self.btn_pin)
        self.style_button(self.btn_click)

        self.side_controls_layout.addWidget(self.btn_pin)
        self.side_controls_layout.addWidget(self.btn_click)
        self.side_controls_layout.addStretch()

        # =========================
        # Final Layout
        # =========================

        self.main_layout.addWidget(self.art_label)
        self.main_layout.addLayout(text_layout)
        self.main_layout.addStretch()
        self.main_layout.addLayout(self.side_controls_layout)

        self.setLayout(self.main_layout)

        self.setStyleSheet("""
            QWidget {
                background-color: rgba(18, 18, 18, 220);
                border-radius: 14px;
            }
        """)

    def style_button(self, btn):
        btn.setCursor(QtGui.QCursor(QtCore.Qt.PointingHandCursor))

        btn.setStyleSheet("""
            QPushButton {
                background-color: rgba(35, 35, 35, 210);
                color: rgb(240, 240, 240);

                border: 1px solid rgba(255, 255, 255, 25);
                border-radius: 8px;

                font-size: 14px;
                font-weight: bold;

                padding-top: 1px;
                padding-bottom: 1px;
            }

            QPushButton:hover {
                background-color: rgba(29, 185, 84, 230);
                color: white;

                border: 1px solid rgba(120, 255, 170, 180);
            }

            QPushButton:pressed {
                background-color: rgba(20, 120, 55, 255);

                border: 1px solid rgba(120, 255, 170, 255);

                padding-top: 2px;
                padding-bottom: 0px;
            }

            QPushButton:checked {
                background-color: rgba(29, 185, 84, 180);

                border: 1px solid rgba(120, 255, 170, 220);
            }
        """)

        shadow = QtWidgets.QGraphicsDropShadowEffect()
        shadow.setBlurRadius(14)
        shadow.setOffset(0, 0)
        shadow.setColor(QtGui.QColor(29, 185, 84, 90))

        btn.setGraphicsEffect(shadow)

    def setup_tray(self):
        self.tray_icon = QtWidgets.QSystemTrayIcon(self)

        pix = QtGui.QPixmap(16, 16)
        pix.fill(QtGui.QColor("#1DB954"))

        self.tray_icon.setIcon(QtGui.QIcon(pix))

        menu = QtWidgets.QMenu()

        action_controls = menu.addAction("Show Controls")
        action_controls.setCheckable(True)
        action_controls.setChecked(True)
        action_controls.triggered.connect(self.toggle_controls_visibility)

        menu.addSeparator()

        action_reset = menu.addAction("Reset Position")
        action_reset.triggered.connect(self.reset_position)

        action_disable_click = menu.addAction("Disable Click-Through")
        action_disable_click.triggered.connect(
            self.disable_click_through_from_tray
        )

        menu.addSeparator()

        action_quit = menu.addAction("Quit")
        action_quit.triggered.connect(
            QtWidgets.QApplication.instance().quit
        )

        self.tray_icon.setContextMenu(menu)
        self.tray_icon.show()

    def set_controls_visible(self, visible):
        should_show = visible and not self.click_through

        self.btn_prev.setVisible(should_show)
        self.btn_play.setVisible(should_show)
        self.btn_next.setVisible(should_show)

        self.btn_pin.setVisible(should_show)
        self.btn_click.setVisible(should_show)

        self.adjustSize()

    def toggle_controls_visibility(self, visible):
        self.controls_visible = visible
        self.set_controls_visible(visible)

    def update_track_info(self):
        full, artist, track = get_spotify_window_info()

        if full == self.last_full_title:
            return

        self.last_full_title = full

        if full:
            self.lbl_track.setText(track if track else full)
            self.lbl_artist.setText(artist if artist else "")

            if artist and track:
                self.worker = ArtWorker(artist, track)
                self.worker.result_signal.connect(self.update_art)
                self.worker.start()

            else:
                self.art_label.setPixmap(self.placeholder_pix)

        else:
            self.lbl_track.setText("No song playing")
            self.lbl_artist.setText("")
            self.art_label.setPixmap(self.placeholder_pix)

    def update_art(self, pixmap):
        if pixmap:
            self.art_label.setPixmap(pixmap)
        else:
            self.art_label.setPixmap(self.placeholder_pix)

    def toggle_pin(self):
        self.setWindowFlag(
            QtCore.Qt.WindowStaysOnTopHint,
            self.btn_pin.isChecked()
        )

        self.show()

    def toggle_click_through(self):
        self.click_through = self.btn_click.isChecked()

        self.apply_click_through()
        self.set_controls_visible(self.controls_visible)

    def disable_click_through_from_tray(self):
        self.click_through = False

        self.btn_click.setChecked(False)

        self.apply_click_through()
        self.set_controls_visible(self.controls_visible)

    def apply_click_through(self):
        ex_style = GetWindowLongPtrW(self.hwnd, GWL_EXSTYLE)

        if self.click_through:
            new_style = ex_style | WS_EX_TRANSPARENT | WS_EX_LAYERED
        else:
            new_style = ex_style & ~WS_EX_TRANSPARENT

        SetWindowLongPtrW(
            self.hwnd,
            GWL_EXSTYLE,
            new_style
        )

    def reset_position(self):
        self.move(100, 100)

    def mousePressEvent(self, event):
        if (
            event.button() == QtCore.Qt.LeftButton
            and not self.click_through
        ):
            self.drag_pos = (
                event.globalPos() -
                self.frameGeometry().topLeft()
            )

            event.accept()

    def mouseMoveEvent(self, event):
        if (
            event.buttons() == QtCore.Qt.LeftButton
            and self.drag_pos
            and not self.click_through
        ):
            self.move(event.globalPos() - self.drag_pos)
            event.accept()


def main():
    QtWidgets.QApplication.setAttribute(
        QtCore.Qt.AA_EnableHighDpiScaling,
        True
    )

    QtWidgets.QApplication.setAttribute(
        QtCore.Qt.AA_UseHighDpiPixmaps,
        True
    )

    app = QtWidgets.QApplication(sys.argv)

    overlay = UltimateOverlay()

    overlay.resize(455, 120)

    screen_geom = app.primaryScreen().availableGeometry()

    x = screen_geom.right() - 480
    y = screen_geom.top() + 20

    overlay.move(x, y)

    overlay.show()

    sys.exit(app.exec_())


if __name__ == "__main__":
    try:
        main()

    except Exception:
        import traceback

        traceback.print_exc()

        input("\nPress Enter to close...")
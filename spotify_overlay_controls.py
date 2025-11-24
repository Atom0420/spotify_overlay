import sys
import ctypes
from ctypes import wintypes
import psutil
import requests
import time
from PyQt5 import QtWidgets, QtCore, QtGui

user32 = ctypes.windll.user32
kernel32 = ctypes.windll.kernel32

GWL_EXSTYLE = -20
WS_EX_TRANSPARENT = 0x00000020
WS_EX_LAYERED = 0x00080000

GetWindowLongPtrW = user32.GetWindowLongPtrW
SetWindowLongPtrW = user32.SetWindowLongPtrW
GetWindowLongPtrW.argtypes = [wintypes.HWND, ctypes.c_int]
GetWindowLongPtrW.restype = ctypes.c_longlong if ctypes.sizeof(ctypes.c_void_p) == 8 else ctypes.c_long
SetWindowLongPtrW.argtypes = [wintypes.HWND, ctypes.c_int, ctypes.c_longlong]
SetWindowLongPtrW.restype = ctypes.c_longlong

class ArtWorker(QtCore.QThread):
    result_signal = QtCore.pyqtSignal(object)

    def __init__(self, artist, track):
        super().__init__()
        self.artist = artist
        self.track = track

    def run(self):
        try:
            term = f"{self.artist} {self.track}"
            url = 'https://itunes.apple.com/search'
            params = {'term': term, 'limit': 1, 'media': 'music'}
            resp = requests.get(url, params=params, timeout=5)
            data = resp.json()
            
            if data.get('resultCount', 0) > 0:
                art_url = data['results'][0].get('artworkUrl100')
                if art_url:
                    art_url = art_url.replace('100x100bb', '600x600bb')
                    img_resp = requests.get(art_url, timeout=5)
                    pixmap = QtGui.QPixmap()
                    pixmap.loadFromData(img_resp.content)
                    self.result_signal.emit(pixmap)
                    return
        except Exception:
            pass
        
        self.result_signal.emit(None)

def get_spotify_window_info():
    """Returns (full_title, artist, track) or (None, None, None)"""
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
                    if proc.name().lower() == 'spotify.exe':
                        found_title.append(title)
                except:
                    pass
        return True

    EnumWindowsProc = ctypes.WINFUNCTYPE(ctypes.c_bool, wintypes.HWND, wintypes.LPARAM)
    user32.EnumWindows(EnumWindowsProc(_enum_callback), 0)

    if not found_title:
        return None, None, None

    raw_title = max(found_title, key=len)
    
    clean_title = raw_title.replace(' - Spotify', '').replace('Spotify - ', '').strip()
    
    if not clean_title or clean_title.lower() == 'spotify':
        return None, None, None

    parts = [p.strip() for p in clean_title.split(' - ')]
    artist, track = None, None
    
    if len(parts) >= 2:
        artist = parts[0]
        track = " - ".join(parts[1:])
    else:
        track = clean_title
        artist = ""

    return clean_title, artist, track

class UltimateOverlay(QtWidgets.QWidget):
    def __init__(self):
        super().__init__()
        
        self.setWindowFlags(QtCore.Qt.FramelessWindowHint | QtCore.Qt.WindowStaysOnTopHint | QtCore.Qt.Tool)
        self.setAttribute(QtCore.Qt.WA_TranslucentBackground)
        self.setWindowTitle("Spotify Overlay")

        self.hwnd = wintypes.HWND(int(self.winId()))
        self.click_through = False
        self.last_full_title = None
        self.default_opacity = 0.95
        self.setWindowOpacity(self.default_opacity)

        self.init_ui()
        
        self.timer = QtCore.QTimer()
        self.timer.setInterval(1000) # 1 second poll
        self.timer.timeout.connect(self.update_track_info)
        self.timer.start()

        self.setup_tray()

        self.drag_pos = None

    def init_ui(self):
        self.main_layout = QtWidgets.QHBoxLayout()
        self.main_layout.setContentsMargins(10, 10, 10, 10)
        self.main_layout.setSpacing(12)

        self.art_label = QtWidgets.QLabel()
        self.art_label.setFixedSize(60, 60)
        self.art_label.setStyleSheet("background-color: #222; border-radius: 8px; border: 1px solid #444;")
        self.art_label.setScaledContents(True)
        
        self.placeholder_pix = QtGui.QPixmap(60, 60)
        self.placeholder_pix.fill(QtGui.QColor("#222"))
        self.art_label.setPixmap(self.placeholder_pix)

        text_layout = QtWidgets.QVBoxLayout()
        text_layout.setSpacing(2)
        text_layout.setAlignment(QtCore.Qt.AlignVCenter)

        self.lbl_track = QtWidgets.QLabel("Waiting for Spotify...")
        self.lbl_track.setStyleSheet("color: white; font-family: 'Segoe UI'; font-size: 14px; font-weight: bold;")
        
        self.lbl_artist = QtWidgets.QLabel("")
        self.lbl_artist.setStyleSheet("color: #bbb; font-family: 'Segoe UI'; font-size: 12px;")

        text_layout.addWidget(self.lbl_track)
        text_layout.addWidget(self.lbl_artist)

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
        self.btn_click.setToolTip("Enable Click-Through (Use Tray to disable)")
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
            QLabel { background-color: transparent; }
        """)

    def style_button(self, btn):
        btn.setStyleSheet("""
            QPushButton {
                background-color: rgba(255,255,255,20);
                color: white;
                border: none;
                border-radius: 4px;
            }
            QPushButton:hover { background-color: rgba(255,255,255,50); }
            QPushButton:checked { background-color: rgba(29, 185, 84, 150); } 
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

    def toggle_controls_visibility(self, visible):
        self.btn_pin.setVisible(visible)
        self.btn_click.setVisible(visible)
        self.adjustSize()

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
        is_pinned = self.btn_pin.isChecked()
        if is_pinned:
            self.setWindowFlag(QtCore.Qt.WindowStaysOnTopHint, True)
        else:
            self.setWindowFlag(QtCore.Qt.WindowStaysOnTopHint, False)
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

def main():
    QtWidgets.QApplication.setAttribute(QtCore.Qt.AA_EnableHighDpiScaling, True)
    QtWidgets.QApplication.setAttribute(QtCore.Qt.AA_UseHighDpiPixmaps, True)
    
    app = QtWidgets.QApplication(sys.argv)
    
    overlay = UltimateOverlay()
    overlay.resize(350, 80)
    
    screen_geom = app.primaryScreen().availableGeometry()
    x = screen_geom.right() - 370
    y = screen_geom.top() + 20
    overlay.move(x, y)
    
    overlay.show()
    sys.exit(app.exec_())

if __name__ == "__main__":
    main()
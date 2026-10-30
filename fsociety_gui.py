#!/usr/bin/env python3
"""
fsociety_gui.py - Ventana estilo eDEX-UI con terminal real integrada (Windows).

  - Paneles en vivo: CPU, RAM, disco, red, procesos.
  - Terminal real (ConPTY): funciona yazi, nmap, hashcat, hydra, etc.
  - Pestañas de terminal y botones que escriben los comandos por ti.

Instalar:   python -m pip install PyQt6 psutil pyte
Opcional:   python -m pip install pywinpty     (si falla, no pasa nada)
Ejecutar:   python fsociety_gui.py

Atajos:  F11 pantalla completa | Ctrl+Shift+C copiar | Ctrl+Shift+V pegar
         rueda del mouse = historial | clic derecho = pegar (o copiar si hay selección)

Úsalo SOLO contra equipos y redes tuyos o donde tengas permiso por escrito.
"""

import codecs
import collections
import ctypes
import datetime
import json
import math
import os
import queue
import random
import re
import shutil
import socket
import subprocess
import sys
import threading
import time
from pathlib import Path

try:
    import psutil
    import pyte
    from PyQt6.QtCore import (
        QEasingCurve, QEvent, QPointF, QPropertyAnimation, QRectF, QSize, Qt,
        QTimer,
    )
    from PyQt6.QtGui import (
        QBrush, QColor, QFont, QFontMetrics, QIcon, QKeySequence, QLinearGradient,
        QPainter, QPen, QPixmap, QPolygonF, QShortcut,
    )
    from PyQt6.QtWidgets import (
        QApplication, QButtonGroup, QFrame, QGraphicsOpacityEffect, QGridLayout,
        QHBoxLayout, QLabel, QListView, QListWidget, QListWidgetItem,
        QMainWindow, QPushButton, QSizePolicy, QStackedWidget, QVBoxLayout,
        QWidget,
    )
except ImportError as e:
    print(f"Falta una librería: {e}")
    print("Instala con:  python -m pip install PyQt6 psutil pyte")
    sys.exit(1)


# --------------------------------------------------------------------------
# Ajustes (edita aquí)
# --------------------------------------------------------------------------

SHELL = ["cmd.exe"]                      # o ["powershell.exe", "-NoLogo"]
FUENTE_TERMINAL = ["Cascadia Mono", "Consolas", "Courier New"]
TAM_FUENTE = 11
MAX_PESTANAS = 5
PEDIR_ADMIN = True                       # pide permisos de administrador al abrir (Windows)

# Tema gris
BG = "#0b0c0d"
FG = "#cfd2d6"
PANEL_BG = "#0e0f11"
BORDE = "#26282b"
ACENTO = "#9a9da2"
TEXTO = "#b9bcc0"
BRILLO = "#eceef0"
TENUE = "#6b6f75"
ALERTA = "#c0605f"

ANSI = {
    "black": "#1a1b1d", "red": "#c0605f", "green": "#8fb08c", "brown": "#c9b37e",
    "blue": "#7f9bbf", "magenta": "#a98bb5", "cyan": "#7fb3b5", "white": "#c8cacd",
    "brightblack": "#5c6066", "brightred": "#e08583", "brightgreen": "#b0d2ac",
    "brightbrown": "#e6d39c", "brightblue": "#a2bde0", "brightmagenta": "#c9aed4",
    "brightcyan": "#a2d4d6", "brightwhite": "#f2f3f4",
}
BASICOS = {"black", "red", "green", "brown", "blue", "magenta", "cyan", "white"}

CONFIG = Path(__file__).with_name("fsociety_config.json")
CREATE_NO_WINDOW = 0x08000000

_cache_colores = {}


def qcolor(hexstr):
    c = _cache_colores.get(hexstr)
    if c is None:
        c = QColor(hexstr if hexstr.startswith("#") else "#" + hexstr)
        _cache_colores[hexstr] = c
    return c


def resolver_color(nombre, por_defecto, negrita=False):
    if nombre == "default":
        return por_defecto
    if negrita and nombre in BASICOS:
        nombre = "bright" + nombre
    if nombre in ANSI:
        return ANSI[nombre]
    if len(nombre) == 6:
        try:
            int(nombre, 16)
            return "#" + nombre
        except ValueError:
            pass
    return por_defecto


def cargar_config():
    try:
        return json.loads(CONFIG.read_text(encoding="utf-8"))
    except Exception:
        return {}


def guardar_config(cfg):
    try:
        CONFIG.write_text(json.dumps(cfg, indent=2), encoding="utf-8")
    except OSError:
        pass


# --------------------------------------------------------------------------
# Pseudo-terminal en Windows (ConPTY)
# --------------------------------------------------------------------------

class WinptyBackend:
    """Usa la librería pywinpty si está instalada."""

    def __init__(self, cmd, cwd, cols, rows):
        from winpty import PtyProcess
        self.p = PtyProcess.spawn(cmd, cwd=cwd, dimensions=(rows, cols))

    def read(self):
        while True:
            try:
                s = self.p.read(65536)
            except Exception:
                return b""
            if s:
                return s.encode("utf-8", "replace")
            if not self.p.isalive():
                return b""

    def write(self, data):
        try:
            self.p.write(data.decode("utf-8", "replace"))
        except Exception:
            pass

    def resize(self, cols, rows):
        try:
            self.p.setwinsize(rows, cols)
        except Exception:
            pass

    def close(self):
        try:
            self.p.terminate(force=True)
        except Exception:
            pass


class ConPtyBackend:
    """ConPTY directo con ctypes (sin dependencias)."""

    def __init__(self, cmd, cwd, cols, rows):
        import ctypes
        from ctypes import wintypes

        self.ct = ctypes
        self.wt = wintypes
        k = ctypes.WinDLL("kernel32", use_last_error=True)
        self.k = k
        HANDLE = wintypes.HANDLE
        DWORD = wintypes.DWORD
        LPVOID = ctypes.c_void_p

        class COORD(ctypes.Structure):
            _fields_ = [("X", ctypes.c_short), ("Y", ctypes.c_short)]

        class STARTUPINFOW(ctypes.Structure):
            _fields_ = [
                ("cb", DWORD), ("lpReserved", wintypes.LPWSTR),
                ("lpDesktop", wintypes.LPWSTR), ("lpTitle", wintypes.LPWSTR),
                ("dwX", DWORD), ("dwY", DWORD), ("dwXSize", DWORD), ("dwYSize", DWORD),
                ("dwXCountChars", DWORD), ("dwYCountChars", DWORD),
                ("dwFillAttribute", DWORD), ("dwFlags", DWORD),
                ("wShowWindow", wintypes.WORD), ("cbReserved2", wintypes.WORD),
                ("lpReserved2", ctypes.POINTER(ctypes.c_byte)),
                ("hStdInput", HANDLE), ("hStdOutput", HANDLE), ("hStdError", HANDLE),
            ]

        class STARTUPINFOEXW(ctypes.Structure):
            _fields_ = [("StartupInfo", STARTUPINFOW), ("lpAttributeList", LPVOID)]

        class PROCESS_INFORMATION(ctypes.Structure):
            _fields_ = [
                ("hProcess", HANDLE), ("hThread", HANDLE),
                ("dwProcessId", DWORD), ("dwThreadId", DWORD),
            ]

        self.COORD = COORD

        k.CreatePipe.argtypes = [ctypes.POINTER(HANDLE), ctypes.POINTER(HANDLE), LPVOID, DWORD]
        k.CreatePipe.restype = wintypes.BOOL
        k.CreatePseudoConsole.argtypes = [COORD, HANDLE, HANDLE, DWORD, ctypes.POINTER(HANDLE)]
        k.CreatePseudoConsole.restype = ctypes.c_long
        k.ResizePseudoConsole.argtypes = [HANDLE, COORD]
        k.ResizePseudoConsole.restype = ctypes.c_long
        k.ClosePseudoConsole.argtypes = [HANDLE]
        k.ClosePseudoConsole.restype = None
        k.InitializeProcThreadAttributeList.argtypes = [
            LPVOID, DWORD, DWORD, ctypes.POINTER(ctypes.c_size_t)]
        k.InitializeProcThreadAttributeList.restype = wintypes.BOOL
        k.UpdateProcThreadAttribute.argtypes = [
            LPVOID, DWORD, ctypes.c_size_t, LPVOID, ctypes.c_size_t, LPVOID, LPVOID]
        k.UpdateProcThreadAttribute.restype = wintypes.BOOL
        k.CreateProcessW.argtypes = [
            wintypes.LPCWSTR, wintypes.LPWSTR, LPVOID, LPVOID, wintypes.BOOL, DWORD,
            LPVOID, wintypes.LPCWSTR, ctypes.POINTER(STARTUPINFOEXW),
            ctypes.POINTER(PROCESS_INFORMATION)]
        k.CreateProcessW.restype = wintypes.BOOL
        k.ReadFile.argtypes = [HANDLE, LPVOID, DWORD, ctypes.POINTER(DWORD), LPVOID]
        k.ReadFile.restype = wintypes.BOOL
        k.WriteFile.argtypes = [HANDLE, LPVOID, DWORD, ctypes.POINTER(DWORD), LPVOID]
        k.WriteFile.restype = wintypes.BOOL
        k.CloseHandle.argtypes = [HANDLE]
        k.CloseHandle.restype = wintypes.BOOL
        k.TerminateProcess.argtypes = [HANDLE, wintypes.UINT]
        k.TerminateProcess.restype = wintypes.BOOL

        def falla(msg):
            return OSError(f"{msg} (error {ctypes.get_last_error()})")

        in_r, in_w = HANDLE(), HANDLE()
        out_r, out_w = HANDLE(), HANDLE()
        if not k.CreatePipe(ctypes.byref(in_r), ctypes.byref(in_w), None, 0):
            raise falla("CreatePipe")
        if not k.CreatePipe(ctypes.byref(out_r), ctypes.byref(out_w), None, 0):
            raise falla("CreatePipe")

        hpc = HANDLE()
        hr = k.CreatePseudoConsole(COORD(cols, rows), in_r, out_w, 0, ctypes.byref(hpc))
        if hr != 0:
            raise OSError(f"CreatePseudoConsole falló: 0x{hr & 0xFFFFFFFF:08x}")
        k.CloseHandle(in_r)
        k.CloseHandle(out_w)

        size = ctypes.c_size_t(0)
        k.InitializeProcThreadAttributeList(None, 1, 0, ctypes.byref(size))
        self._attr = ctypes.create_string_buffer(size.value)
        if not k.InitializeProcThreadAttributeList(self._attr, 1, 0, ctypes.byref(size)):
            raise falla("InitializeProcThreadAttributeList")
        PROC_THREAD_ATTRIBUTE_PSEUDOCONSOLE = 0x00020016
        if not k.UpdateProcThreadAttribute(
            self._attr, 0, PROC_THREAD_ATTRIBUTE_PSEUDOCONSOLE,
            hpc, ctypes.sizeof(ctypes.c_void_p), None, None,
        ):
            raise falla("UpdateProcThreadAttribute")

        si = STARTUPINFOEXW()
        si.StartupInfo.cb = ctypes.sizeof(si)
        si.lpAttributeList = ctypes.addressof(self._attr)
        pi = PROCESS_INFORMATION()
        linea = ctypes.create_unicode_buffer(subprocess.list2cmdline(cmd))
        EXTENDED_STARTUPINFO_PRESENT = 0x00080000
        if not k.CreateProcessW(
            None, linea, None, None, False, EXTENDED_STARTUPINFO_PRESENT,
            None, cwd, ctypes.byref(si), ctypes.byref(pi),
        ):
            raise falla("CreateProcessW")
        k.CloseHandle(pi.hThread)

        self.hpc, self.in_w, self.out_r, self.proc = hpc, in_w, out_r, pi.hProcess
        self._buf = ctypes.create_string_buffer(65536)
        self._cerrado = False

    def read(self):
        n = self.wt.DWORD(0)
        ok = self.k.ReadFile(self.out_r, self._buf, 65536, self.ct.byref(n), None)
        if not ok or n.value == 0:
            return b""
        return self._buf.raw[: n.value]

    def write(self, data):
        if self._cerrado:
            return
        n = self.wt.DWORD(0)
        self.k.WriteFile(self.in_w, data, len(data), self.ct.byref(n), None)

    def resize(self, cols, rows):
        if not self._cerrado:
            self.k.ResizePseudoConsole(self.hpc, self.COORD(cols, rows))

    def close(self):
        if self._cerrado:
            return
        self._cerrado = True
        self.k.TerminateProcess(self.proc, 0)

        def cerrar():
            self.k.ClosePseudoConsole(self.hpc)
            for h in (self.in_w, self.out_r, self.proc):
                self.k.CloseHandle(h)

        threading.Thread(target=cerrar, daemon=True).start()


def crear_pty(cmd, cwd, cols, rows):
    try:
        return WinptyBackend(cmd, cwd, cols, rows)
    except Exception:
        pass
    return ConPtyBackend(cmd, cwd, cols, rows)


# --------------------------------------------------------------------------
# Emulador de pantalla (pyte) con pantalla alterna, historial y respuestas
# --------------------------------------------------------------------------

class Pantalla(pyte.HistoryScreen):
    def __init__(self, cols, rows):
        self.alt_activa = False
        self._snap = None
        self.responder = None
        super().__init__(cols, rows, history=5000, ratio=0.5)

    def write_process_input(self, data):
        if self.responder:
            self.responder(data)

    def set_mode(self, *modes, **kwargs):
        if kwargs.get("private") and any(m in (47, 1047, 1049) for m in modes):
            self._entrar_alt()
            modes = tuple(m for m in modes if m not in (47, 1047, 1049))
        if modes:
            super().set_mode(*modes, **kwargs)

    def reset_mode(self, *modes, **kwargs):
        if kwargs.get("private") and any(m in (47, 1047, 1049) for m in modes):
            self._salir_alt()
            modes = tuple(m for m in modes if m not in (47, 1047, 1049))
        if modes:
            super().reset_mode(*modes, **kwargs)

    def _entrar_alt(self):
        if self.alt_activa:
            return
        self.alt_activa = True
        self._snap = (
            {y: dict(fila) for y, fila in self.buffer.items()},
            self.cursor.x, self.cursor.y,
        )
        self.erase_in_display(2)
        self.cursor_position()

    def _salir_alt(self):
        if not self.alt_activa:
            return
        self.alt_activa = False
        filas, cx, cy = self._snap
        self._snap = None
        self.buffer.clear()
        for y, fila in filas.items():
            for x, ch in fila.items():
                self.buffer[y][x] = ch
        self.cursor.x, self.cursor.y = cx, cy
        self.dirty.update(range(self.lines))


# --------------------------------------------------------------------------
# Teclado -> secuencias de terminal
# --------------------------------------------------------------------------

K = Qt.Key
TECLAS_CSI = {K.Key_Up: "A", K.Key_Down: "B", K.Key_Right: "C", K.Key_Left: "D",
              K.Key_Home: "H", K.Key_End: "F"}
TECLAS_TILDE = {K.Key_Insert: 2, K.Key_Delete: 3, K.Key_PageUp: 5, K.Key_PageDown: 6,
                K.Key_F5: 15, K.Key_F6: 17, K.Key_F7: 18, K.Key_F8: 19,
                K.Key_F9: 20, K.Key_F10: 21, K.Key_F12: 24}
TECLAS_SS3 = {K.Key_F1: "P", K.Key_F2: "Q", K.Key_F3: "R", K.Key_F4: "S"}


def tecla_a_texto(key, mods, text):
    try:
        k = Qt.Key(key)
    except ValueError:
        k = None
    shift = bool(mods & Qt.KeyboardModifier.ShiftModifier)
    ctrl = bool(mods & Qt.KeyboardModifier.ControlModifier)
    alt = bool(mods & Qt.KeyboardModifier.AltModifier)
    mod = 1 + (1 if shift else 0) + (2 if alt else 0) + (4 if ctrl else 0)

    if k in TECLAS_CSI:
        f = TECLAS_CSI[k]
        return f"\x1b[1;{mod}{f}" if mod > 1 else f"\x1b[{f}"
    if k in TECLAS_TILDE:
        n = TECLAS_TILDE[k]
        return f"\x1b[{n};{mod}~" if mod > 1 else f"\x1b[{n}~"
    if k in TECLAS_SS3:
        f = TECLAS_SS3[k]
        return f"\x1b[1;{mod}{f}" if mod > 1 else f"\x1bO{f}"
    if k in (K.Key_Return, K.Key_Enter):
        return "\r"
    if k == K.Key_Backspace:
        return "\x1b\x7f" if alt else "\x7f"
    if k == K.Key_Tab:
        return "\t"
    if k == K.Key_Backtab:
        return "\x1b[Z"
    if k == K.Key_Escape:
        return "\x1b"

    # AltGr en Windows llega como Ctrl+Alt con el carácter ya resuelto (@, €, etc.)
    if ctrl and alt and text and ord(text[0]) >= 32:
        return text
    if ctrl and not alt and K.Key_A.value <= key <= K.Key_Z.value:
        return chr(key - 0x40)
    if text and ord(text[0]) >= 32:
        return "\x1b" + text if alt else text
    return None


# --------------------------------------------------------------------------
# Widget de terminal
# --------------------------------------------------------------------------

class Terminal(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setCursor(Qt.CursorShape.IBeamCursor)
        self.setMinimumSize(300, 150)

        self.font_ = QFont()
        self.font_.setFamilies(FUENTE_TERMINAL)
        self.font_.setPointSize(TAM_FUENTE)
        self.font_.setStyleHint(QFont.StyleHint.Monospace)
        self.font_.setFixedPitch(True)
        self.font_bold = QFont(self.font_)
        self.font_bold.setBold(True)
        fm = QFontMetrics(self.font_)
        self.cw = max(6, fm.horizontalAdvance("M"))
        self.chh = fm.height()
        self.asc = fm.ascent()

        self.pant = Pantalla(100, 30)
        self.stream = pyte.Stream(self.pant, strict=False)
        self.pant.responder = self._responder
        self.decoder = codecs.getincrementaldecoder("utf-8")(errors="replace")
        self._vacia = collections.defaultdict(lambda: self.pant.default_char)

        self.pty = None
        self.q = queue.Queue()
        self.started = False
        self.dead = False
        self.offset = 0
        self.sel = None          # ((fila, col), (fila, col)) en coordenadas de vista
        self.blink = True

        self.t_drain = QTimer(self)
        self.t_drain.timeout.connect(self._drain)
        self.t_drain.start(16)
        self.t_blink = QTimer(self)
        self.t_blink.timeout.connect(self._parpadeo)
        self.t_blink.start(530)

    # ---- ciclo de vida -------------------------------------------------

    def start(self):
        self.started = True
        self.dead = False
        self._ajustar()
        cols, rows = self.pant.columns, self.pant.lines
        try:
            if os.name != "nt":
                raise OSError("La terminal integrada solo funciona en Windows.")
            self.pty = crear_pty(SHELL, str(Path.home()), cols, rows)
        except Exception as e:
            self.pty = None
            self.dead = True
            self._pintar_texto(f"\r\n[no se pudo iniciar la terminal: {e}]\r\n")
            return
        self.q = queue.Queue()
        threading.Thread(target=self._lector, args=(self.pty, self.q), daemon=True).start()

    @staticmethod
    def _lector(pty, q):
        while True:
            try:
                data = pty.read()
            except Exception:
                data = b""
            if not data:
                break
            q.put(data)
        q.put(None)

    def reiniciar(self):
        self.pant.alt_activa = False
        self.pant._snap = None
        self.pant.reset()
        self.offset = 0
        self.sel = None
        self.start()

    def cerrar(self):
        if self.pty:
            self.pty.close()
            self.pty = None

    def send(self, texto):
        if self.pty and not self.dead:
            self.offset = 0
            self.pty.write(texto.encode("utf-8"))

    def _responder(self, data):
        if self.pty and not self.dead:
            self.pty.write(data.encode("utf-8"))

    def _pintar_texto(self, texto):
        self.stream.feed(texto)
        self.pant.dirty.clear()
        self.update()

    # ---- salida del proceso -------------------------------------------

    def _drain(self):
        trozos = []
        fin = False
        try:
            while True:
                item = self.q.get_nowait()
                if item is None:
                    fin = True
                    break
                trozos.append(item)
        except queue.Empty:
            pass
        if trozos:
            self.stream.feed(self.decoder.decode(b"".join(trozos)))
            self.pant.dirty.clear()
            self.update()
        if fin:
            self.dead = True
            if self.pty:
                self.pty.close()
                self.pty = None
            self._pintar_texto("\r\n[sesión terminada - presiona Enter para reiniciar]\r\n")

    def _parpadeo(self):
        self.blink = not self.blink
        if self.hasFocus():
            self.update()

    # ---- tamaño --------------------------------------------------------

    def _ajustar(self):
        cols = max(20, self.width() // self.cw)
        rows = max(5, self.height() // self.chh)
        if (cols, rows) != (self.pant.columns, self.pant.lines):
            self.pant.resize(lines=rows, columns=cols)
            if self.pty:
                self.pty.resize(cols, rows)

    def resizeEvent(self, ev):
        super().resizeEvent(ev)
        self._ajustar()

    # ---- dibujo --------------------------------------------------------

    def _fila(self, vy, hist):
        if hist is None:
            return self.pant.buffer[vy]
        idx = len(hist) - self.offset + vy
        if idx < 0:
            return self._vacia
        if idx < len(hist):
            return hist[idx]
        return self.pant.buffer[idx - len(hist)]

    def _flush(self, p, x, y, texto, st, ncel):
        fg, bg, negrita, subr, rev = st
        if rev:
            fgc = resolver_color(bg, BG)
            bgc = resolver_color(fg, FG, negrita)
        else:
            fgc = resolver_color(fg, FG, negrita)
            bgc = None if bg == "default" else resolver_color(bg, BG)
        px = x * self.cw
        if bgc:
            p.fillRect(px, y, ncel * self.cw, self.chh, qcolor(bgc))
        if texto.strip():
            p.setFont(self.font_bold if negrita else self.font_)
            p.setPen(qcolor(fgc))
            p.drawText(px, y + self.asc, texto)
        if subr:
            p.setPen(qcolor(fgc))
            p.drawLine(px, y + self.asc + 1, px + ncel * self.cw, y + self.asc + 1)

    def paintEvent(self, ev):
        p = QPainter(self)
        p.fillRect(self.rect(), qcolor(BG))
        scr = self.pant
        if scr.alt_activa:
            self.offset = 0
        hist = list(scr.history.top) if self.offset > 0 else None
        if hist is not None:
            self.offset = min(self.offset, len(hist))

        for vy in range(scr.lines):
            fila = self._fila(vy, hist)
            y = vy * self.chh
            run, estilo, rx, ncel = [], None, 0, 0
            for vx in range(scr.columns):
                ch = fila[vx]
                st = (ch.fg, ch.bg, ch.bold, ch.underscore, ch.reverse)
                if st != estilo:
                    if run:
                        self._flush(p, rx, y, "".join(run), estilo, ncel)
                    run, estilo, rx, ncel = [], st, vx, 0
                run.append(ch.data)
                ncel += 1
            if run:
                self._flush(p, rx, y, "".join(run), estilo, ncel)

        # selección
        if self.sel:
            (r1, c1), (r2, c2) = self._sel_ordenada()
            marca = QColor(207, 210, 214, 70)
            for r in range(r1, r2 + 1):
                a = c1 if r == r1 else 0
                b = c2 if r == r2 else scr.columns - 1
                if b >= a:
                    p.fillRect(a * self.cw, r * self.chh, (b - a + 1) * self.cw, self.chh, marca)

        # cursor
        if self.offset == 0 and not scr.cursor.hidden:
            cx, cy = scr.cursor.x * self.cw, scr.cursor.y * self.chh
            if self.hasFocus():
                if self.blink:
                    c = QColor(FG)
                    c.setAlpha(170)
                    p.fillRect(cx, cy, self.cw, self.chh, c)
            else:
                p.setPen(QPen(qcolor(ACENTO), 1))
                p.drawRect(cx, cy, self.cw - 1, self.chh - 1)
        p.end()

    # ---- selección / portapapeles -------------------------------------

    def _celda(self, pos):
        c = int(pos.x() // self.cw)
        r = int(pos.y() // self.chh)
        c = max(0, min(self.pant.columns - 1, c))
        r = max(0, min(self.pant.lines - 1, r))
        return (r, c)

    def _sel_ordenada(self):
        a, b = self.sel
        return (a, b) if a <= b else (b, a)

    def _texto_seleccion(self):
        if not self.sel:
            return ""
        hist = list(self.pant.history.top) if self.offset > 0 else None
        (r1, c1), (r2, c2) = self._sel_ordenada()
        lineas = []
        for r in range(r1, r2 + 1):
            fila = self._fila(r, hist)
            a = c1 if r == r1 else 0
            b = c2 if r == r2 else self.pant.columns - 1
            lineas.append("".join(fila[x].data for x in range(a, b + 1)).rstrip())
        return "\n".join(lineas)

    def copiar(self):
        t = self._texto_seleccion()
        if t:
            QApplication.clipboard().setText(t)

    def pegar(self):
        t = QApplication.clipboard().text()
        if t:
            self.send(t.replace("\r\n", "\r").replace("\n", "\r"))

    def mousePressEvent(self, ev):
        self.setFocus()
        if ev.button() == Qt.MouseButton.LeftButton:
            c = self._celda(ev.position())
            self.sel = (c, c)
            self.update()
        elif ev.button() == Qt.MouseButton.RightButton:
            if self.sel and self.sel[0] != self.sel[1]:
                self.copiar()
                self.sel = None
                self.update()
            else:
                self.pegar()

    def mouseMoveEvent(self, ev):
        if ev.buttons() & Qt.MouseButton.LeftButton and self.sel:
            self.sel = (self.sel[0], self._celda(ev.position()))
            self.update()

    def mouseReleaseEvent(self, ev):
        if self.sel and self.sel[0] == self.sel[1]:
            self.sel = None
            self.update()

    def wheelEvent(self, ev):
        if self.pant.alt_activa:
            return
        paso = 3 if ev.angleDelta().y() > 0 else -3
        maximo = len(self.pant.history.top)
        self.offset = max(0, min(maximo, self.offset + paso))
        self.update()
        ev.accept()

    # ---- teclado -------------------------------------------------------

    def focusNextPrevChild(self, siguiente):
        return False

    def event(self, ev):
        if ev.type() == QEvent.Type.ShortcutOverride:
            ev.accept()
            return True
        return super().event(ev)

    def keyPressEvent(self, ev):
        key = ev.key()
        mods = ev.modifiers()
        ctrl = bool(mods & Qt.KeyboardModifier.ControlModifier)
        shift = bool(mods & Qt.KeyboardModifier.ShiftModifier)

        if key == Qt.Key.Key_F11:
            self.window().alternar_pantalla_completa()
            return
        if ctrl and shift and key == Qt.Key.Key_C:
            self.copiar()
            return
        if ctrl and shift and key == Qt.Key.Key_V:
            self.pegar()
            return
        if shift and key == Qt.Key.Key_Insert:
            self.pegar()
            return
        if self.dead:
            if key in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
                self.reiniciar()
            return

        texto = tecla_a_texto(key, mods, ev.text())
        if texto:
            self.sel = None
            self.send(texto)

    def focusInEvent(self, ev):
        super().focusInEvent(ev)
        self.update()

    def focusOutEvent(self, ev):
        super().focusOutEvent(ev)
        self.update()


# --------------------------------------------------------------------------
# Widgets decorativos / de datos
# --------------------------------------------------------------------------

def fuente_ui(tam=9, negrita=False, espaciado=0.0):
    f = QFont("Segoe UI", tam)
    f.setBold(negrita)
    if espaciado:
        f.setLetterSpacing(QFont.SpacingType.AbsoluteSpacing, espaciado)
    return f


def fuente_mono(tam=10, negrita=False):
    f = QFont()
    f.setFamilies(FUENTE_TERMINAL)
    f.setPointSize(tam)
    f.setBold(negrita)
    return f


class Panel(QFrame):
    """Marco con esquinas marcadas, al estilo eDEX-UI."""

    def __init__(self, titulo, parent=None):
        super().__init__(parent)
        self.titulo = titulo
        self.cuerpo = QVBoxLayout(self)
        self.cuerpo.setContentsMargins(14, 34, 14, 12)
        self.cuerpo.setSpacing(8)

    def agregar(self, w, stretch=0):
        self.cuerpo.addWidget(w, stretch)

    def paintEvent(self, ev):
        p = QPainter(self)
        w, h = self.width(), self.height()
        p.fillRect(self.rect(), qcolor(PANEL_BG))
        p.setPen(QPen(qcolor(BORDE), 1))
        p.drawRect(0, 0, w - 1, h - 1)
        p.drawLine(14, 26, w - 14, 26)
        p.setPen(QPen(qcolor(ACENTO), 2))
        L = 10
        for (x, y, dx, dy) in ((0, 0, 1, 1), (w - 1, 0, -1, 1),
                               (0, h - 1, 1, -1), (w - 1, h - 1, -1, -1)):
            p.drawLine(x, y, x + dx * L, y)
            p.drawLine(x, y, x, y + dy * L)
        p.setFont(fuente_ui(8, True, 2.5))
        p.setPen(qcolor(TENUE))
        p.drawText(14, 18, self.titulo)
        p.end()


class Grafica(QWidget):
    """Gráfica de líneas con área degradada. series = [(deque, color_hex), ...]"""

    def __init__(self, series, maximo=None, alto=80, parent=None):
        super().__init__(parent)
        self.series = series
        self.maximo = maximo
        self.setMinimumHeight(alto)
        self.setMaximumHeight(alto)

    def paintEvent(self, ev):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        w, h = self.width(), self.height()
        p.setPen(QPen(qcolor(BORDE), 1))
        for i in range(1, 4):
            y = h * i / 4
            p.drawLine(0, int(y), w, int(y))
        maximo = self.maximo
        if maximo is None:
            maximo = max(1.0, max((max(d) if d else 0) for d, _ in self.series)) * 1.15
        for datos, color in self.series:
            n = len(datos)
            if n < 2:
                continue
            pts = [QPointF(i * (w - 1) / (n - 1), h - 2 - min(v, maximo) / maximo * (h - 4))
                   for i, v in enumerate(datos)]
            area = QPolygonF(pts + [QPointF(w - 1, h), QPointF(0, h)])
            g = QLinearGradient(0, 0, 0, h)
            c1 = QColor(color)
            c1.setAlpha(80)
            c2 = QColor(color)
            c2.setAlpha(0)
            g.setColorAt(0, c1)
            g.setColorAt(1, c2)
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QBrush(g))
            p.drawPolygon(area)
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.setPen(QPen(qcolor(color), 1.5))
            p.drawPolyline(QPolygonF(pts))
        p.end()


class Medidor(QWidget):
    def __init__(self, etiqueta, parent=None):
        super().__init__(parent)
        self.etiqueta = etiqueta
        self.fraccion = 0.0
        self.valor = ""
        self.setFixedHeight(30)

    def poner(self, porcentaje, valor):
        self.fraccion = max(0.0, min(1.0, porcentaje / 100))
        self.valor = valor
        self.update()

    def paintEvent(self, ev):
        p = QPainter(self)
        w = self.width()
        p.setFont(fuente_ui(8, True, 1.5))
        p.setPen(qcolor(TENUE))
        p.drawText(0, 11, self.etiqueta)
        p.setFont(fuente_mono(9))
        p.setPen(qcolor(TEXTO))
        p.drawText(QRectF(0, 0, w, 14), Qt.AlignmentFlag.AlignRight, self.valor)
        p.fillRect(0, 20, w, 4, qcolor(BORDE))
        color = ALERTA if self.fraccion >= 0.9 else BRILLO
        p.fillRect(0, 20, int(w * self.fraccion), 4, qcolor(color))
        p.end()


class Nucleos(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.valores = []
        self.setFixedHeight(44)

    def poner(self, valores):
        self.valores = valores
        self.update()

    def paintEvent(self, ev):
        p = QPainter(self)
        n = len(self.valores)
        if not n:
            return
        w, h = self.width(), self.height()
        gap = 3
        bw = max(2, (w - gap * (n - 1)) // n)
        for i, v in enumerate(self.valores):
            alto = max(2, int((h - 2) * v / 100))
            x = i * (bw + gap)
            p.fillRect(x, 0, bw, h, qcolor(BORDE))
            p.fillRect(x, h - alto, bw, alto, qcolor(ALERTA if v >= 90 else ACENTO))
        p.end()


# --------------------------------------------------------------------------
# Búsqueda de herramientas
# --------------------------------------------------------------------------

def buscar_acceso(nombre):
    casa = Path.home()
    for carpeta in (casa / "Desktop", casa / "OneDrive" / "Desktop",
                    casa / "Escritorio", casa / "OneDrive" / "Escritorio"):
        ruta = carpeta / nombre
        if ruta.exists():
            return ruta
    return None


def leer_lnk(nombre):
    """Lee un acceso directo del escritorio: dict(target, args, cwd, lnk) o None."""
    ruta = buscar_acceso(nombre)
    if not ruta:
        return None
    ps = (
        "$s=(New-Object -ComObject WScript.Shell).CreateShortcut('%s');"
        "Write-Output ($s.TargetPath + '|||' + $s.Arguments + '|||' + $s.WorkingDirectory)"
    ) % str(ruta).replace("'", "''")
    try:
        salida = subprocess.run(
            ["powershell", "-NoProfile", "-Command", ps],
            capture_output=True, text=True, creationflags=CREATE_NO_WINDOW,
        ).stdout.strip()
    except Exception:
        return None
    partes = salida.split("|||")
    if len(partes) != 3:
        return None
    target, args, cwd = (x.strip() for x in partes)
    if not cwd and target:
        cwd = os.path.dirname(target)
    return {"target": target, "args": args, "cwd": cwd, "lnk": str(ruta)}


def leer_acceso(nombre):
    """(programa, carpeta_de_trabajo) a la que apunta un acceso directo .lnk."""
    d = leer_lnk(nombre)
    if not d:
        return None, None
    return d["target"], d["cwd"]


def comandos_lnk(info):
    """Comandos que reproducen un acceso directo dentro de la terminal integrada."""
    cmds = []
    if info["cwd"]:
        cmds.append(cmd_cd(info["cwd"]))
    pref = "& " if es_powershell() else ""
    linea = f'{pref}"{info["target"]}"'
    if info["args"]:
        linea += " " + info["args"]
    cmds.append(linea)
    return cmds


def encontrar_hashcat():
    guardada = cargar_config().get("hashcat_dir")
    if guardada and (Path(guardada) / "hashcat.exe").exists():
        return Path(guardada)

    # Mirar todo lo que dice tu acceso directo: destino, "Iniciar en" y argumentos
    info = leer_lnk("Hashcat.lnk")
    if info:
        candidatos = [info["target"], info["cwd"]]
        candidatos += re.findall(r'[A-Za-z]:\\[^"<>|*?&\r\n]+', info["args"])
        for c in candidatos:
            if not c:
                continue
            ruta = Path(c.strip().rstrip("\\"))
            for carpeta in (ruta, ruta.parent):
                if (carpeta / "hashcat.exe").exists():
                    return carpeta

    exe = shutil.which("hashcat")
    if exe:
        return Path(exe).parent

    casa = Path.home()
    bases = [casa / "Desktop", casa / "OneDrive" / "Desktop", casa / "Downloads",
             casa / "Descargas", casa / "Documents", Path("C:/Tools"), Path("C:/")]
    patrones = ("hashcat*/hashcat.exe", "*/hashcat*/hashcat.exe", "*/*/hashcat*/hashcat.exe")
    for base in bases:
        if not base.exists():
            continue
        for patron in patrones:
            try:
                for hit in base.glob(patron):
                    return hit.parent
            except OSError:
                pass
    return None


def es_powershell():
    return "powershell" in SHELL[0].lower() or "pwsh" in SHELL[0].lower()


def cmd_cd(ruta):
    if es_powershell():
        return f'Set-Location -LiteralPath "{ruta}"'
    return f'cd /d "{ruta}"'


# nombre, tipo, argumento
MODULOS = [
    ("PING", "escribir", "ping "),
    ("TRACERT", "escribir", "tracert "),
    ("NSLOOKUP", "escribir", "nslookup "),
    ("NMAP", "escribir", "nmap -F "),
    ("IPCONFIG", "ejecutar", "ipconfig /all"),
    ("WIRESHARK", "lnk", "Wireshark.lnk"),
    ("ZENMAP", "lnk", "Nmap - Zenmap GUI.lnk"),
    ("HASHCAT", "hashcat", ""),
    ("HYDRA", "hydra", ""),
    ("TOR", "lnk", "Tor Browser.lnk"),
]


def tasa(bytes_s):
    for unidad in ("B/s", "KB/s", "MB/s", "GB/s"):
        if bytes_s < 1024 or unidad == "GB/s":
            return f"{bytes_s:,.1f} {unidad}"
        bytes_s /= 1024


# --------------------------------------------------------------------------
# Pantalla de arranque (estilo eDEX-UI)
# --------------------------------------------------------------------------

class Arranque(QWidget):
    """Pantalla de carga que cubre la ventana y se desvanece al terminar."""

    def __init__(self, parent, al_terminar):
        super().__init__(parent)
        self.al_terminar = al_terminar
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.lineas = []                 # (texto, estado) estado: True / False / None
        self.pendientes = self._preparar()
        self.total = len(self.pendientes)
        self.terminado = False
        self.t0 = time.time()

        self.efecto = QGraphicsOpacityEffect(self)
        self.efecto.setOpacity(1.0)
        self.setGraphicsEffect(self.efecto)

        self.timer = QTimer(self)
        self.timer.timeout.connect(self._avanzar)
        self.timer.start(120)

    @staticmethod
    def _preparar():
        def hay(nombre, lnk=None):
            return bool(shutil.which(nombre) or (lnk and buscar_acceso(lnk)))

        hashcat = bool(
            cargar_config().get("hashcat_dir")
            or buscar_acceso("Hashcat.lnk")
            or shutil.which("hashcat")
        )
        lineas = [
            ("fsociety kernel // iniciando entorno", None),
            ("montando sistema de archivos", True),
            ("cargando controladores de red", True),
            ("inicializando interfaz gráfica", True),
            ("abriendo canal de terminal (ConPTY)", os.name == "nt"),
            ("módulo nmap", hay("nmap")),
            ("módulo wireshark", hay("wireshark", "Wireshark.lnk")),
            ("módulo zenmap", bool(buscar_acceso("Nmap - Zenmap GUI.lnk"))),
            ("módulo hashcat", hashcat),
            ("módulo hydra", bool(buscar_acceso("hydra.exe.lnk") or shutil.which("hydra"))),
            ("módulo tor browser", bool(buscar_acceso("Tor Browser.lnk"))),
            ("módulo yazi", hay("yazi")),
            ("conexión segura establecida", True),
            ("Hello, friend.", None),
        ]
        return lineas

    def _avanzar(self):
        if self.terminado:
            return
        if self.pendientes:
            # a veces salen dos líneas seguidas para que no se sienta uniforme
            for _ in range(2 if random.random() < 0.3 else 1):
                if self.pendientes:
                    self.lineas.append(self.pendientes.pop(0))
            self.timer.setInterval(random.randint(60, 190))
        else:
            self.timer.stop()
            QTimer.singleShot(900, self.cerrar)
        self.update()

    def cerrar(self):
        if self.terminado:
            return
        self.terminado = True
        self.timer.stop()
        self.anim = QPropertyAnimation(self.efecto, b"opacity", self)
        self.anim.setDuration(600)
        self.anim.setStartValue(1.0)
        self.anim.setEndValue(0.0)
        self.anim.setEasingCurve(QEasingCurve.Type.InOutQuad)
        self.anim.finished.connect(self._fin)
        self.anim.start()

    def _fin(self):
        self.hide()
        self.al_terminar()

    def keyPressEvent(self, ev):
        self.cerrar()

    def mousePressEvent(self, ev):
        self.cerrar()

    def paintEvent(self, ev):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        w, h = self.width(), self.height()
        p.fillRect(self.rect(), qcolor(BG))

        # todo el dibujo crece con el tamaño de la ventana (letras, barra, título)
        s = max(1.0, min(w / 1100, h / 620))
        p.scale(s, s)
        w, h = w / s, h / s

        # rejilla muy tenue de fondo
        p.setPen(QPen(QColor(255, 255, 255, 7), 1))
        wi, hi = int(w) + 1, int(h) + 1
        for x in range(0, wi, 48):
            p.drawLine(x, 0, x, hi)
        for y in range(0, hi, 48):
            p.drawLine(0, y, wi, y)

        # título con un leve parpadeo
        t = time.time() - self.t0
        c = QColor(BRILLO)
        c.setAlpha(int(215 + 40 * math.sin(t * 2.4)))
        p.setFont(fuente_ui(44, True, 16))
        p.setPen(c)
        ytit = h * 0.14
        p.drawText(QRectF(0, ytit, w, 90), Qt.AlignmentFlag.AlignCenter, "FSOCIETY")
        p.setFont(fuente_ui(10, True, 7))
        p.setPen(qcolor(TENUE))
        p.drawText(QRectF(0, ytit + 86, w, 24), Qt.AlignmentFlag.AlignCenter, "PREPARANDO ENTORNO")

        # registro de arranque
        ancho = min(620, w - 80)
        x0 = (w - ancho) / 2
        y0 = ytit + 150
        alto_linea = 22
        espacio = max(1, int((h * 0.86 - 70 - y0) // alto_linea))
        p.setFont(fuente_mono(10))
        for i, (texto, estado) in enumerate(self.lineas[-espacio:]):
            y = y0 + i * alto_linea + 15
            if estado is True:
                tag, col, col_t = "[  OK  ]", BRILLO, TEXTO
            elif estado is False:
                tag, col, col_t = "[  --  ]", TENUE, TENUE
                texto += "  ·  no encontrado"
            else:
                tag, col, col_t = "[  >>  ]", ACENTO, TEXTO
            p.setPen(qcolor(col))
            p.drawText(QPointF(x0, y), tag)
            p.setPen(qcolor(col_t))
            p.drawText(QPointF(x0 + 88, y), texto)

        # barra de progreso
        frac = min(1.0, len(self.lineas) / max(1, self.total))
        bw = min(560, w - 80)
        bx = (w - bw) / 2
        by = h * 0.86
        p.setFont(fuente_mono(9))
        p.setPen(qcolor(TENUE))
        p.drawText(QRectF(bx, by - 24, bw, 18), Qt.AlignmentFlag.AlignLeft, "PREPARANDO FSOCIETY")
        p.drawText(QRectF(bx, by - 24, bw, 18), Qt.AlignmentFlag.AlignRight, f"{int(frac * 100)}%")
        p.fillRect(QRectF(bx, by, bw, 4), qcolor(BORDE))
        p.fillRect(QRectF(bx, by, bw * frac, 4), qcolor(BRILLO))
        p.drawText(QRectF(0, by + 18, w, 18), Qt.AlignmentFlag.AlignCenter,
                   "presiona cualquier tecla para saltar")
        p.end()


# --------------------------------------------------------------------------
# Explorador de archivos (panel inferior)
# --------------------------------------------------------------------------

_iconos = {}


def icono(tipo):
    """Iconos grises dibujados a mano (carpeta, archivo, unidad, subir)."""
    if tipo in _iconos:
        return _iconos[tipo]
    pm = QPixmap(64, 64)
    pm.fill(Qt.GlobalColor.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    p.scale(64 / 44, 64 / 44)      # los dibujos están pensados en 44x44
    p.setPen(QPen(qcolor(ACENTO), 1.6))
    p.setBrush(QColor(154, 157, 162, 40))
    if tipo == "dir":
        p.drawPolygon(QPolygonF([
            QPointF(5, 11), QPointF(18, 11), QPointF(22, 16),
            QPointF(39, 16), QPointF(39, 36), QPointF(5, 36)]))
    elif tipo == "disco":
        p.drawRoundedRect(QRectF(5, 14, 34, 18), 3, 3)
        p.setBrush(qcolor(ACENTO))
        p.drawEllipse(QPointF(33, 23), 2, 2)
    elif tipo == "arriba":
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawLine(QPointF(22, 34), QPointF(22, 12))
        p.drawPolyline(QPolygonF([QPointF(13, 21), QPointF(22, 12), QPointF(31, 21)]))
    else:
        p.drawPolygon(QPolygonF([
            QPointF(11, 5), QPointF(26, 5), QPointF(34, 13),
            QPointF(34, 39), QPointF(11, 39)]))
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawPolyline(QPolygonF([QPointF(26, 5), QPointF(26, 13), QPointF(34, 13)]))
    p.end()
    _iconos[tipo] = pm
    return pm


class ListaArchivos(QListWidget):
    """Lista de iconos cuyas celdas se reparten todo el ancho disponible."""

    ANCHO_MIN = 124
    ALTO_CELDA = 112

    def resizeEvent(self, ev):
        super().resizeEvent(ev)
        ancho = self.viewport().width()
        if ancho > 0:
            columnas = max(1, ancho // self.ANCHO_MIN)
            nuevo = QSize(ancho // columnas - 1, self.ALTO_CELDA)
            if nuevo != self.gridSize():
                self.setGridSize(nuevo)


class Explorador(Panel):
    """Navegador de archivos. Clic en carpeta = entrar; clic en archivo = escribe su ruta en la terminal."""

    def __init__(self, ventana):
        super().__init__("FILESYSTEM")
        self.v = ventana
        self.ruta = None
        self.cuerpo.setContentsMargins(14, 32, 14, 10)
        self.cuerpo.setSpacing(6)

        barra = QHBoxLayout()
        barra.setSpacing(6)
        self.lbl = QLabel("")
        self.lbl.setFont(fuente_mono(9))
        self.lbl.setStyleSheet(f"color: {BRILLO};")
        barra.addWidget(self.lbl, 1)
        for texto, fn in (
            ("INICIO", lambda: self.ir(Path.home())),
            ("UNIDADES", lambda: self.ir(None)),
            ("SUBIR", self.subir),
            ("CD EN TERMINAL", self.cd_terminal),
        ):
            b = QPushButton(texto)
            b.setStyleSheet("padding: 3px 10px; font-size: 8pt;")
            b.clicked.connect(lambda _=False, f=fn: f())
            barra.addWidget(b)
        self.cuerpo.addLayout(barra)

        self.lista = ListaArchivos()
        self.lista.setViewMode(QListView.ViewMode.IconMode)
        self.lista.setFlow(QListView.Flow.LeftToRight)
        self.lista.setWrapping(True)
        self.lista.setResizeMode(QListView.ResizeMode.Adjust)
        self.lista.setMovement(QListView.Movement.Static)
        self.lista.setIconSize(QSize(64, 64))
        self.lista.setGridSize(QSize(136, ListaArchivos.ALTO_CELDA))
        self.lista.setSpacing(2)
        self.lista.setUniformItemSizes(True)
        self.lista.setWordWrap(True)
        # sin "..." : los nombres largos bajan a otra línea en vez de cortarse
        self.lista.setTextElideMode(Qt.TextElideMode.ElideNone)
        self.lista.setFont(fuente_mono(8))
        self.lista.itemClicked.connect(self._click)
        self.cuerpo.addWidget(self.lista, 1)

        self.ir(Path.home())

    def _agregar(self, nombre, tipo, ruta):
        item = QListWidgetItem(QIcon(icono(tipo)), nombre)
        item.setData(Qt.ItemDataRole.UserRole, (tipo, ruta))
        item.setToolTip(str(ruta) if ruta else nombre)
        self.lista.addItem(item)

    def ir(self, ruta):
        self.lista.clear()
        if ruta is None:
            self.ruta = None
            self.lbl.setText("MI PC  ·  unidades")
            try:
                for part in psutil.disk_partitions(all=False):
                    self._agregar(part.mountpoint, "disco", part.mountpoint)
            except Exception:
                pass
            return
        ruta = Path(ruta)
        try:
            with os.scandir(ruta) as it:
                entradas = list(it)
        except OSError as e:
            self.v.poner_estado(f"no pude abrir {ruta}: {e.strerror or e}")
            if self.ruta is not None and self.ruta != ruta:
                self.ir(self.ruta)      # volver a mostrar la carpeta anterior
            return
        self.ruta = ruta
        self.lbl.setText(str(ruta))
        padre = ruta.parent
        self._agregar("..", "arriba", None if padre == ruta else str(padre))

        dirs, archivos = [], []
        for e in entradas:
            try:
                es_dir = e.is_dir()
            except OSError:
                es_dir = False
            (dirs if es_dir else archivos).append(e.name)
        for n in sorted(dirs, key=str.lower)[:300]:
            self._agregar(n, "dir", str(ruta / n))
        for n in sorted(archivos, key=str.lower)[:300]:
            self._agregar(n, "archivo", str(ruta / n))

    def _click(self, item):
        tipo, ruta = item.data(Qt.ItemDataRole.UserRole)
        if tipo in ("dir", "disco", "arriba"):
            self.ir(ruta)
        else:
            self.v.terminal_actual().send(f'"{ruta}" ')
            self.v.poner_estado("ruta escrita en la terminal")
        self.v.terminal_actual().setFocus()

    def subir(self):
        if self.ruta is None:
            return
        padre = self.ruta.parent
        self.ir(None if padre == self.ruta else padre)

    def cd_terminal(self):
        if self.ruta is None:
            self.v.poner_estado("entra primero a una unidad o carpeta")
            return
        term = self.v.terminal_actual()
        term.send(cmd_cd(self.ruta) + "\r")
        term.setFocus()
        self.v.poner_estado(f"terminal movida a {self.ruta}")


# --------------------------------------------------------------------------
# Ventana principal
# --------------------------------------------------------------------------

QSS = f"""
QMainWindow, QWidget#raiz {{ background: {BG}; }}
QLabel {{ color: {TEXTO}; background: transparent; }}
QPushButton {{
    background: {PANEL_BG}; color: {TEXTO}; border: 1px solid {BORDE};
    padding: 6px 10px; font-family: 'Segoe UI'; font-size: 9pt;
}}
QPushButton:hover {{ background: #1a1c1f; color: {BRILLO}; border-color: #5a5d62; }}
QPushButton:pressed {{ background: #25272a; }}
QPushButton:checked {{ background: #25272a; color: {BRILLO}; border-color: {ACENTO}; }}
QFrame#cabecera {{ background: {PANEL_BG}; border: 1px solid {BORDE}; }}
QListWidget {{ background: transparent; border: none; outline: none; color: {TEXTO}; }}
QListWidget::item {{ color: {TEXTO}; padding: 2px; }}
QListWidget::item:hover {{ background: #1a1c1f; color: {BRILLO}; }}
QListWidget::item:selected {{ background: #25272a; color: {BRILLO}; }}
QScrollBar:vertical {{ background: transparent; width: 8px; margin: 0; }}
QScrollBar::handle:vertical {{ background: {BORDE}; min-height: 24px; }}
QScrollBar::handle:vertical:hover {{ background: #5a5d62; }}
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{ height: 0; }}
"""


class VentanaPrincipal(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("fsociety")
        self.resize(1480, 880)
        self.setStyleSheet(QSS)

        self.hist_cpu = collections.deque([0.0] * 60, maxlen=60)
        self.hist_rx = collections.deque([0.0] * 60, maxlen=60)
        self.hist_tx = collections.deque([0.0] * 60, maxlen=60)
        self.net_anterior = psutil.net_io_counters()
        self.t_anterior = time.time()
        self.contador = 0
        self.terminales = []
        self.botones_tab = []

        raiz = QWidget()
        raiz.setObjectName("raiz")
        self.setCentralWidget(raiz)
        externo = QVBoxLayout(raiz)
        externo.setContentsMargins(10, 10, 10, 8)
        externo.setSpacing(8)
        externo.addWidget(self._cabecera())

        cuerpo = QHBoxLayout()
        cuerpo.setSpacing(8)
        cuerpo.addWidget(self._columna_izquierda())
        cuerpo.addWidget(self._centro(), 1)
        cuerpo.addWidget(self._columna_derecha())
        externo.addLayout(cuerpo, 1)
        self.explorador = Explorador(self)
        self.explorador.setFixedHeight(310)
        externo.addWidget(self.explorador)
        externo.addWidget(self._pie())

        # la terminal no arranca hasta que termine la pantalla de carga
        self.agregar_terminal(iniciar=False)

        self.sc_f11 = QShortcut(QKeySequence("F11"), self)
        self.sc_f11.activated.connect(self.alternar_pantalla_completa)

        # la pantalla de carga es hija de la ventana entera para cubrirla completa
        self.arranque = Arranque(self, self._arranque_listo)
        self.arranque.setGeometry(self.rect())
        self.arranque.raise_()
        QTimer.singleShot(200, self.arranque.setFocus)

        psutil.cpu_percent(interval=None)
        psutil.cpu_percent(interval=None, percpu=True)
        self.timer = QTimer(self)
        self.timer.timeout.connect(self.tick)
        self.timer.start(1000)
        self.tick()

    # ---- construcción --------------------------------------------------

    def _cabecera(self):
        marco = QFrame()
        marco.setObjectName("cabecera")
        marco.setFixedHeight(56)
        lay = QHBoxLayout(marco)
        lay.setContentsMargins(16, 6, 16, 6)

        titulo = QLabel("FSOCIETY")
        titulo.setFont(fuente_ui(16, True, 6))
        titulo.setStyleSheet(f"color: {BRILLO};")
        sub = QLabel(f"root@{socket.gethostname().lower()}" + ("  ·  ADMIN" if es_admin() else ""))
        sub.setFont(fuente_mono(9))
        sub.setStyleSheet(f"color: {TENUE};")
        bloque = QVBoxLayout()
        bloque.setSpacing(0)
        bloque.addWidget(titulo)
        bloque.addWidget(sub)
        lay.addLayout(bloque)
        lay.addStretch(1)

        self.tabs_layout = QHBoxLayout()
        self.tabs_layout.setSpacing(4)
        self.btn_mas = QPushButton("+")
        self.btn_mas.setFixedWidth(34)
        self.btn_mas.clicked.connect(self.agregar_terminal)
        self.tabs_layout.addWidget(self.btn_mas)
        lay.addLayout(self.tabs_layout)
        lay.addStretch(1)

        self.reloj = QLabel("00:00:00")
        self.reloj.setFont(fuente_mono(18, True))
        self.reloj.setStyleSheet(f"color: {BRILLO};")
        self.fecha = QLabel("")
        self.fecha.setFont(fuente_mono(9))
        self.fecha.setStyleSheet(f"color: {TENUE};")
        self.fecha.setAlignment(Qt.AlignmentFlag.AlignRight)
        b2 = QVBoxLayout()
        b2.setSpacing(0)
        b2.addWidget(self.reloj, alignment=Qt.AlignmentFlag.AlignRight)
        b2.addWidget(self.fecha)
        lay.addLayout(b2)
        self.tab_group = QButtonGroup(self)
        self.tab_group.setExclusive(True)
        return marco

    def _columna_izquierda(self):
        col = QWidget()
        col.setFixedWidth(300)
        lay = QVBoxLayout(col)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(8)

        sistema = Panel("SYSTEM")
        self.lbl_cpu = QLabel("0%")
        self.lbl_cpu.setFont(fuente_mono(28, True))
        self.lbl_cpu.setStyleSheet(f"color: {BRILLO};")
        sistema.agregar(self.lbl_cpu)
        self.graf_cpu = Grafica([(self.hist_cpu, ACENTO)], maximo=100, alto=70)
        sistema.agregar(self.graf_cpu)
        self.nucleos = Nucleos()
        sistema.agregar(self.nucleos)
        self.med_ram = Medidor("MEMORY")
        self.med_disco = Medidor("DISK C:")
        sistema.agregar(self.med_ram)
        sistema.agregar(self.med_disco)
        self.lbl_uptime = QLabel("")
        self.lbl_uptime.setFont(fuente_mono(9))
        self.lbl_uptime.setStyleSheet(f"color: {TENUE};")
        self.lbl_uptime.setWordWrap(True)
        sistema.agregar(self.lbl_uptime)
        lay.addWidget(sistema)

        procs = Panel("PROCESSES")
        self.lbl_procs = QLabel("")
        self.lbl_procs.setFont(fuente_mono(9))
        self.lbl_procs.setAlignment(Qt.AlignmentFlag.AlignTop)
        procs.agregar(self.lbl_procs, 1)
        lay.addWidget(procs, 1)
        return col

    def _centro(self):
        panel = Panel("TERMINAL")
        panel.cuerpo.setContentsMargins(10, 34, 10, 10)
        self.pila = QStackedWidget()
        self.pila.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        panel.agregar(self.pila, 1)
        return panel

    def _columna_derecha(self):
        col = QWidget()
        col.setFixedWidth(300)
        lay = QVBoxLayout(col)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(8)

        red = Panel("NETWORK")
        self.lbl_ip = QLabel("")
        self.lbl_ip.setFont(fuente_mono(10))
        self.lbl_ip.setStyleSheet(f"color: {BRILLO};")
        self.lbl_ip.setWordWrap(True)          # si no cabe en una línea, baja a la siguiente
        red.agregar(self.lbl_ip)
        self.graf_red = Grafica([(self.hist_rx, BRILLO), (self.hist_tx, TENUE)], alto=90)
        red.agregar(self.graf_red)
        self.lbl_rx = QLabel("▼ 0 B/s")
        self.lbl_tx = QLabel("▲ 0 B/s")
        self.lbl_tot = QLabel("")
        for lbl in (self.lbl_rx, self.lbl_tx, self.lbl_tot):
            lbl.setFont(fuente_mono(9))
            red.agregar(lbl)
        self.lbl_tot.setStyleSheet(f"color: {TENUE};")
        self.lbl_tot.setWordWrap(True)
        lay.addWidget(red)

        mods = Panel("MODULES")
        rejilla = QGridLayout()
        rejilla.setSpacing(6)
        total = len(MODULOS)
        for i, (nombre, tipo, arg) in enumerate(MODULOS):
            b = QPushButton(nombre)
            b.setStyleSheet("padding: 7px 4px;")
            b.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
            b.clicked.connect(lambda _=False, t=tipo, a=arg, n=nombre: self.ejecutar_modulo(n, t, a))
            if i == total - 1 and total % 2 == 1:
                rejilla.addWidget(b, i // 2, 0, 1, 2)     # el último ocupa todo el ancho
            else:
                rejilla.addWidget(b, i // 2, i % 2)
        cont = QWidget()
        cont.setLayout(rejilla)
        mods.agregar(cont)
        mods.agregar(QWidget(), 1)
        lay.addWidget(mods, 1)
        return col

    def _pie(self):
        w = QWidget()
        lay = QHBoxLayout(w)
        lay.setContentsMargins(4, 0, 4, 0)
        self.estado = QLabel("listo · usa estas herramientas solo con permiso")
        self.estado.setFont(fuente_mono(9))
        self.estado.setStyleSheet(f"color: {TENUE};")
        ayuda = QLabel("F11 salir/entrar de pantalla completa   Ctrl+Shift+C/V copiar/pegar")
        ayuda.setFont(fuente_mono(9))
        ayuda.setStyleSheet(f"color: {TENUE};")
        lay.addWidget(self.estado, 1)
        lay.addWidget(ayuda)
        return w

    # ---- pestañas ------------------------------------------------------

    def _arranque_listo(self):
        self.arranque.deleteLater()
        self.arranque = None
        self.seleccionar_tab(0)

    def agregar_terminal(self, iniciar=True):
        if len(self.terminales) >= MAX_PESTANAS:
            self.poner_estado(f"máximo {MAX_PESTANAS} terminales")
            return
        idx = len(self.terminales)
        term = Terminal()
        self.terminales.append(term)
        self.pila.addWidget(term)

        btn = QPushButton(f"TERM {idx + 1}")
        btn.setCheckable(True)
        self.tab_group.addButton(btn, idx)
        self.tabs_layout.insertWidget(idx, btn)
        btn.clicked.connect(lambda _=False, i=idx: self.seleccionar_tab(i))
        self.botones_tab.append(btn)
        if iniciar:
            # arrancar después de que la ventana tenga su tamaño final
            QTimer.singleShot(150, lambda i=idx: self.seleccionar_tab(i))

    def seleccionar_tab(self, i):
        self.pila.setCurrentIndex(i)
        self.botones_tab[i].setChecked(True)
        term = self.terminales[i]
        if not term.started:
            term.start()
        term.setFocus()

    def terminal_actual(self):
        return self.terminales[self.pila.currentIndex()]

    # ---- módulos -------------------------------------------------------

    def poner_estado(self, texto):
        if hasattr(self, "estado"):
            self.estado.setText(texto)

    def avisar(self, texto):
        """Muestra un aviso en la barra de abajo y también dentro de la terminal."""
        self.poner_estado(texto)
        term = self.terminal_actual()
        if es_powershell():
            term.send(f'Write-Host "[fsociety] {texto}"\r')
        else:
            term.send(f"echo [fsociety] {texto}\r")

    def ejecutar_modulo(self, nombre, tipo, arg):
        term = self.terminal_actual()
        if tipo == "escribir":
            term.send(arg)
            self.poner_estado(f"{nombre}: escribe el objetivo y presiona Enter")
        elif tipo == "ejecutar":
            term.send(arg + "\r")
            self.poner_estado(f"{nombre} ejecutándose en la terminal")
        elif tipo == "lnk":
            ruta = buscar_acceso(arg)
            if not ruta:
                self.poner_estado(f"no encontré {arg} en tu escritorio")
                return
            if hasattr(os, "startfile"):
                os.startfile(str(ruta))
                self.poner_estado(f"abriendo {nombre}")
            else:
                self.poner_estado("abrir accesos directos solo funciona en Windows")
        elif tipo == "lnk_term":
            # Reproduce el acceso directo dentro de la terminal integrada
            info = leer_lnk(arg)
            if not info:
                self.avisar(f"no encontre {arg} en tu escritorio")
                return
            if not info["target"]:
                os.startfile(info["lnk"])
                self.poner_estado(f"abriendo {nombre} con su acceso directo")
            else:
                for c in comandos_lnk(info):
                    term.send(c + "\r")
                self.poner_estado(f"{nombre}: ejecutandose en la terminal (puede tardar en cargar)")
        elif tipo == "hashcat":
            carpeta = encontrar_hashcat()
            if carpeta:
                cfg = cargar_config()
                cfg["hashcat_dir"] = str(carpeta)
                guardar_config(cfg)
                term.send(cmd_cd(carpeta) + "\r")
                # -I muestra los dispositivos detectados: sirve para comprobar que funciona
                term.send(("hashcat.exe" if not es_powershell() else ".\\hashcat.exe")
                          + " -I --backend-ignore-hip\r")
                self.poner_estado(f"HASHCAT: terminal en {carpeta}")
            else:
                info = leer_lnk("Hashcat.lnk")
                if info and info["target"]:
                    for c in comandos_lnk(info):
                        term.send(c + "\r")
                    self.poner_estado("HASHCAT: no encontre hashcat.exe, uso tu acceso directo")
                else:
                    self.avisar("no encontre hashcat.exe - ponlo en hashcat_dir de fsociety_config.json")
        elif tipo == "hydra":
            destino, carpeta = leer_acceso("hydra.exe.lnk")
            if destino and destino.lower().endswith(".exe") and os.path.exists(destino):
                term.send(cmd_cd(os.path.dirname(destino)) + "\r")
                pref = "& " if es_powershell() else ""
                term.send(f'{pref}"{destino}" -h\r')
                self.poner_estado("HYDRA: mostrando ayuda")
            elif carpeta:
                term.send(cmd_cd(carpeta) + "\r")
                term.send("hydra.exe -h\r")
                self.poner_estado(f"HYDRA: terminal en {carpeta}")
            else:
                self.poner_estado("no encontré hydra.exe.lnk en tu escritorio")
        term.setFocus()

    # ---- estadísticas en vivo -----------------------------------------

    def tick(self):
        ahora = datetime.datetime.now()
        self.reloj.setText(ahora.strftime("%H:%M:%S"))
        self.fecha.setText(ahora.strftime("%d/%m/%Y"))

        cpu = psutil.cpu_percent(interval=None)
        self.hist_cpu.append(cpu)
        self.lbl_cpu.setText(f"{cpu:.0f}%")
        self.graf_cpu.update()
        self.nucleos.poner(psutil.cpu_percent(interval=None, percpu=True)[:32])

        mem = psutil.virtual_memory()
        self.med_ram.poner(mem.percent, f"{mem.used / 1e9:.1f} / {mem.total / 1e9:.1f} GB")
        try:
            disco = psutil.disk_usage("C:\\" if os.name == "nt" else "/")
            self.med_disco.poner(disco.percent, f"{disco.used / 1e9:.0f} / {disco.total / 1e9:.0f} GB")
        except Exception:
            pass
        arranque = datetime.datetime.fromtimestamp(psutil.boot_time())
        self.lbl_uptime.setText("UPTIME  " + str(ahora - arranque).split(".")[0])

        io = psutil.net_io_counters()
        dt = max(0.1, time.time() - self.t_anterior)
        rx = (io.bytes_recv - self.net_anterior.bytes_recv) / dt
        tx = (io.bytes_sent - self.net_anterior.bytes_sent) / dt
        self.net_anterior, self.t_anterior = io, time.time()
        self.hist_rx.append(rx)
        self.hist_tx.append(tx)
        self.graf_red.update()
        self.lbl_rx.setText(f"▼ {tasa(rx)}")
        self.lbl_tx.setText(f"▲ {tasa(tx)}")
        self.lbl_tot.setText(f"TOTAL  ▼{io.bytes_recv / 1e9:.2f} GB  ▲{io.bytes_sent / 1e9:.2f} GB")
        self.lbl_ip.setText(f"{socket.gethostname()}  ·  {self._ip_local()}")

        self.contador += 1
        if self.contador % 2 == 0:
            self._actualizar_procesos()

    _ip = {"v": "sin red", "t": 0.0}

    def _ip_local(self):
        ahora = time.time()
        if ahora - self._ip["t"] > 15:
            try:
                s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
                s.connect(("8.8.8.8", 80))   # no envía datos, solo averigua tu interfaz
                self._ip["v"] = s.getsockname()[0]
                s.close()
            except Exception:
                self._ip["v"] = "sin red"
            self._ip["t"] = ahora
        return self._ip["v"]

    def _actualizar_procesos(self):
        nuc = psutil.cpu_count(logical=True) or 1
        lista = []
        for p in psutil.process_iter(["name", "cpu_percent"]):
            try:
                nombre = p.info["name"] or "?"
                if nombre == "System Idle Process":
                    continue
                lista.append(((p.info["cpu_percent"] or 0.0) / nuc, nombre))
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                pass
        lista.sort(reverse=True)
        lineas = [f"{nombre[:22]:<22}{cpu:5.1f}%" for cpu, nombre in lista[:12]]
        self.lbl_procs.setText("\n".join(lineas))

    # ---- ventana -------------------------------------------------------

    def resizeEvent(self, ev):
        super().resizeEvent(ev)
        arr = getattr(self, "arranque", None)
        if arr is not None:
            arr.setGeometry(self.rect())

    def alternar_pantalla_completa(self):
        if self.isFullScreen():
            self.showNormal()
        else:
            self.showFullScreen()

    def closeEvent(self, ev):
        for t in self.terminales:
            t.cerrar()
        super().closeEvent(ev)


def es_admin():
    try:
        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except Exception:
        return False


def reiniciar_como_admin():
    """Vuelve a abrir el programa pidiendo permisos (aparece el aviso de Windows)."""
    try:
        params = subprocess.list2cmdline([os.path.abspath(sys.argv[0])] + sys.argv[1:])
        r = ctypes.windll.shell32.ShellExecuteW(None, "runas", sys.executable, params, None, 1)
        return r > 32           # 5 o menos = el usuario canceló el aviso
    except Exception:
        return False


def main():
    # Si el usuario cancela el aviso de administrador, el programa sigue como usuario normal.
    if os.name == "nt" and PEDIR_ADMIN and not es_admin():
        if reiniciar_como_admin():
            return
    app = QApplication(sys.argv)
    app.setStyle("Fusion")
    ventana = VentanaPrincipal()
    ventana.showFullScreen()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()

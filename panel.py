# -*- coding: utf-8 -*-
"""
Ses Klonlama - Masaüstü Kontrol Paneli
Tkinter GUI: Başlat / Durdur / Yeniden Başlat / Logları Temizle / Kurulum
"""
import sys
import os
import subprocess
import threading
import time
import queue
import signal
from pathlib import Path
import tkinter as tk
from tkinter import font as tkfont

BASE_DIR = Path(__file__).parent
LOG_FILE = BASE_DIR / "logs" / "app.log"
PID_FILE = BASE_DIR / "logs" / "server.pid"
LOG_FILE.parent.mkdir(exist_ok=True)

# ─── Python yolu bul ────────────────────────────────────────────────────────
def find_python():
    candidates = [
        Path(os.environ.get("USERPROFILE", "")) / "miniconda3" / "python.exe",
        Path(os.environ.get("USERPROFILE", "")) / "anaconda3"  / "python.exe",
        Path("C:/ProgramData/miniconda3/python.exe"),
        Path("C:/ProgramData/anaconda3/python.exe"),
    ]
    for c in candidates:
        if c.exists():
            return str(c)
    for name in ("python", "py"):
        try:
            r = subprocess.run([name, "-m", "pip", "--version"],
                               capture_output=True, timeout=5)
            if r.returncode == 0:
                return name
        except Exception:
            pass
    return sys.executable  # fallback: kendi Python'umuz

PYTHON_EXE = find_python()

# ─── Renk paleti ────────────────────────────────────────────────────────────
BG        = "#1a1a1a"
BG2       = "#0d0d0d"
FG        = "#e8e8e0"
GREEN     = "#2ecc40"
GREEN_DK  = "#1a7a26"
RED       = "#e05555"
RED_DK    = "#8c2020"
ORANGE    = "#e08030"
ORANGE_DK = "#7a4010"
BLUE      = "#5588cc"
BLUE_DK   = "#2a4a88"
GRAY      = "#444444"
GRAY_DK   = "#2a2a2a"
LOG_FG    = "#33ff33"
LOG_BG    = "#0a0a0a"

# ─── Global süreç referansı ──────────────────────────────────────────────────
_server_proc = None
_log_queue   = queue.Queue()

# ─── Panel sınıfı ───────────────────────────────────────────────────────────
class Panel(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("Ses Klonlama - Kontrol Paneli")
        self.configure(bg=BG)
        self.resizable(True, True)
        self.minsize(820, 520)
        self.geometry("920x600")
        try:
            self.iconbitmap(default="")
        except Exception:
            pass

        self._build_ui()
        self._restore_pid()
        self._poll_queue()
        self._status_tick()

        self.protocol("WM_DELETE_WINDOW", self._on_close)

    # ── UI inşası ─────────────────────────────────────────────────────────
    def _build_ui(self):
        mono = tkfont.Font(family="Courier New", size=10)
        bold = tkfont.Font(family="Courier New", size=11, weight="bold")
        title_font = tkfont.Font(family="Courier New", size=14, weight="bold")

        # Başlık
        hdr = tk.Frame(self, bg=BG, pady=12)
        hdr.pack(fill="x", padx=16)
        tk.Label(hdr, text="SES KLONLAMA  —  Kontrol Paneli",
                 font=title_font, bg=BG, fg=FG).pack(side="left")

        # Durum etiketi
        self.lbl_status = tk.Label(hdr, text="● DURDU", font=mono,
                                   bg=BG, fg=RED)
        self.lbl_status.pack(side="right", padx=8)

        # Buton çubuğu
        bar = tk.Frame(self, bg=GRAY_DK, pady=10, padx=12)
        bar.pack(fill="x")

        def btn(parent, text, color, dark, cmd, width=14):
            b = tk.Button(parent, text=text, font=bold,
                          bg=color, fg="white", activebackground=dark,
                          activeforeground="white", relief="flat",
                          cursor="hand2", bd=0, padx=14, pady=8,
                          width=width, command=cmd)
            b.pack(side="left", padx=5)
            return b

        self.btn_start   = btn(bar, "▶  BAŞLAT",         GREEN,  GREEN_DK,  self.do_start)
        self.btn_stop    = btn(bar, "■  DURDUR",          RED,    RED_DK,    self.do_stop)
        self.btn_restart = btn(bar, "↺  YENİDEN BAŞLAT", ORANGE, ORANGE_DK, self.do_restart)
        self.btn_install = btn(bar, "⬇  KURULUM",         BLUE,   BLUE_DK,   self.do_install, width=12)

        # Sağ butonlar
        right = tk.Frame(bar, bg=GRAY_DK)
        right.pack(side="right")
        btn(right, "🗑  Logları Temizle", GRAY, "#111", self.do_clearlog, width=16)

        # Separator
        tk.Frame(self, bg="#222", height=1).pack(fill="x")

        # Log alanı etiketi
        lf = tk.Frame(self, bg=BG, pady=6, padx=12)
        lf.pack(fill="x")
        tk.Label(lf, text="Terminal Çıktısı (Loglar):",
                 font=bold, bg=BG, fg="#888").pack(side="left")
        self.lbl_pid = tk.Label(lf, text="", font=mono, bg=BG, fg="#555")
        self.lbl_pid.pack(side="right")

        # Log text widget + scrollbar
        log_frame = tk.Frame(self, bg=BG, padx=12, pady=0)
        log_frame.pack(fill="both", expand=True, padx=0, pady=(0, 12))
        scrollbar = tk.Scrollbar(log_frame, bg=GRAY_DK, troughcolor=BG2,
                                 activebackground=GRAY)
        scrollbar.pack(side="right", fill="y")
        self.log_text = tk.Text(
            log_frame, bg=LOG_BG, fg=LOG_FG, font=mono,
            insertbackground=LOG_FG, relief="flat",
            wrap="word", state="disabled", bd=0,
            yscrollcommand=scrollbar.set,
            selectbackground="#223322", selectforeground=LOG_FG,
        )
        self.log_text.pack(fill="both", expand=True)
        scrollbar.config(command=self.log_text.yview)

        self._log_line("=" * 60)
        self._log_line("HOŞ GELDİNİZ!  Ses Klonlama Kontrol Paneli Hazır.")
        self._log_line(f"Python: {PYTHON_EXE}")
        self._log_line("Sunucuyu başlatmak için [BAŞLAT] butonuna tıklayın.")
        self._log_line("=" * 60)

    # ── Log yazdır ────────────────────────────────────────────────────────
    def _log_line(self, text):
        self.log_text.configure(state="normal")
        self.log_text.insert("end", str(text) + "\n")
        self.log_text.see("end")
        self.log_text.configure(state="disabled")
        # Dosyaya da yaz
        try:
            with open(LOG_FILE, "a", encoding="utf-8") as f:
                f.write(str(text) + "\n")
        except Exception:
            pass

    # ── Kuyruktan log oku (thread-safe) ──────────────────────────────────
    def _poll_queue(self):
        try:
            while True:
                line = _log_queue.get_nowait()
                self._log_line(line)
        except queue.Empty:
            pass
        self.after(150, self._poll_queue)

    # ── Durum göstergesi ──────────────────────────────────────────────────
    def _status_tick(self):
        self._update_status()
        self.after(3000, self._status_tick)

    def _update_status(self):
        global _server_proc
        running = False
        pid_info = ""
        if _server_proc and _server_proc.poll() is None:
            running = True
            pid_info = f"PID {_server_proc.pid}"
        elif PID_FILE.exists():
            try:
                pid = int(PID_FILE.read_text().strip())
                import ctypes
                handle = ctypes.windll.kernel32.OpenProcess(0x1000, False, pid)
                if handle:
                    ctypes.windll.kernel32.CloseHandle(handle)
                    running = True
                    pid_info = f"PID {pid}"
            except Exception:
                pass

        if running:
            self.lbl_status.config(text=f"● ÇALIŞIYOR  {pid_info}", fg=GREEN)
            self.btn_start.config(state="disabled", bg="#2a6a2a")
            self.btn_stop.config(state="normal",   bg=RED)
            self.btn_restart.config(state="normal", bg=ORANGE)
            self.lbl_pid.config(text="http://localhost:9000", fg=BLUE)
        else:
            self.lbl_status.config(text="● DURDU", fg=RED)
            self.btn_start.config(state="normal",   bg=GREEN)
            self.btn_stop.config(state="disabled",  bg="#5a2a2a")
            self.btn_restart.config(state="normal", bg=ORANGE)
            self.lbl_pid.config(text="", fg="#555")
            if PID_FILE.exists():
                try:
                    PID_FILE.unlink()
                except Exception:
                    pass
            _server_proc = None

    # ── Çıktı okuma thread'i ─────────────────────────────────────────────
    def _reader(self, stream):
        try:
            for line in iter(stream.readline, b""):
                decoded = line.decode("utf-8", errors="replace").rstrip()
                if decoded:
                    _log_queue.put(decoded)
        except Exception:
            pass

    # ── BAŞLAT ────────────────────────────────────────────────────────────
    def do_start(self):
        global _server_proc
        self._log_line("\n[*] Sunucu başlatılıyor...")
        env = os.environ.copy()
        env["SES_KLON_SERVE"] = "panel"
        try:
            _server_proc = subprocess.Popen(
                [PYTHON_EXE, "app.py"],
                cwd=str(BASE_DIR),
                env=env,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0,
            )
            PID_FILE.write_text(str(_server_proc.pid))
            threading.Thread(target=self._reader,
                             args=(_server_proc.stdout,), daemon=True).start()
            self._log_line(f"[OK] Sunucu başlatıldı — PID {_server_proc.pid}")
            self._log_line("[i]  Adres: http://localhost:9000")
            self._log_line("[i]  Model yükleniyor, tarayıcı hazır olunca açılacak...")
            self._update_status()
            threading.Thread(target=self._open_browser, daemon=True).start()
        except Exception as e:
            self._log_line(f"[HATA] Başlatılamadı: {e}")

    def _open_browser(self):
        import urllib.request
        import webbrowser
        deadline = time.time() + 120
        while time.time() < deadline:
            try:
                with urllib.request.urlopen(
                    "http://localhost:9000/status", timeout=2
                ) as r:
                    import json
                    data = json.loads(r.read())
                    if data.get("model_ready"):
                        break
            except Exception:
                pass
            time.sleep(2)
        webbrowser.open("http://localhost:9000")

    # ── DURDUR ────────────────────────────────────────────────────────────
    def do_stop(self):
        global _server_proc
        self._log_line("\n[*] Sunucu durduruluyor...")
        stopped = False
        if _server_proc and _server_proc.poll() is None:
            try:
                _server_proc.terminate()
                _server_proc.wait(timeout=5)
            except Exception:
                try:
                    _server_proc.kill()
                except Exception:
                    pass
            stopped = True

        # Portu da temizle
        self._kill_port(9000)

        if PID_FILE.exists():
            try:
                PID_FILE.unlink()
            except Exception:
                pass
        _server_proc = None
        self._log_line("[OK] Sunucu durduruldu.")
        self._update_status()

    def _kill_port(self, port):
        my_pid = os.getpid()
        try:
            r = subprocess.run(
                f'netstat -aon | findstr ":{port} "',
                shell=True, capture_output=True, text=True
            )
            for line in r.stdout.splitlines():
                # Sadece LISTENING state — bagli clientlari oldurme
                if 'LISTENING' not in line:
                    continue
                parts = line.split()
                if not parts:
                    continue
                try:
                    pid = int(parts[-1])
                except ValueError:
                    continue
                if pid == my_pid:
                    continue
                try:
                    subprocess.run(f"taskkill /PID {pid} /F",
                                   shell=True, capture_output=True)
                except Exception:
                    pass
        except Exception:
            pass

    # ── YENİDEN BAŞLAT ────────────────────────────────────────────────────
    def do_restart(self):
        self._log_line("\n[*] Yeniden başlatılıyor...")
        def _restart():
            self.do_stop()
            time.sleep(2)
            self.after(0, self.do_start)
        threading.Thread(target=_restart, daemon=True).start()

    # ── KURULUM ────────────────────────────────────────────────────────────
    def do_install(self):
        self._log_line("\n[*] Kurulum başlatılıyor — paketler kontrol ediliyor...")
        self._log_line("[i]  Eksik paketler yüklenecek. Bu işlem birkaç dakika sürebilir.\n")
        self.btn_install.config(state="disabled", text="⏳ Kuruluyor...")
        env = os.environ.copy()
        env.pop("SES_KLON_SERVE", None)

        def _run():
            try:
                proc = subprocess.Popen(
                    [PYTHON_EXE, "app.py", "--install-only"],
                    cwd=str(BASE_DIR),
                    env=env,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0,
                )
                for line in iter(proc.stdout.readline, b""):
                    decoded = line.decode("utf-8", errors="replace").rstrip()
                    if decoded:
                        _log_queue.put(decoded)
                proc.wait()
                _log_queue.put("\n[OK] Kurulum tamamlandı. Artık Başlat butonuna basabilirsiniz.")
            except Exception as e:
                _log_queue.put(f"[HATA] Kurulum başarısız: {e}")
            finally:
                # Butonu geri aç (GUI thread'inde çalıştır)
                self.after(0, lambda: self.btn_install.config(
                    state="normal", text="⬇  KURULUM"
                ))

        threading.Thread(target=_run, daemon=True).start()

    # ── LOGLARI TEMİZLE ────────────────────────────────────────────────────
    def do_clearlog(self):
        self.log_text.configure(state="normal")
        self.log_text.delete("1.0", "end")
        self.log_text.configure(state="disabled")
        try:
            LOG_FILE.write_text("", encoding="utf-8")
        except Exception:
            pass
        self._log_line("[OK] Loglar temizlendi.")

    # ── PID'i yükle (önceki oturum) ───────────────────────────────────────
    def _restore_pid(self):
        if PID_FILE.exists():
            try:
                pid = int(PID_FILE.read_text().strip())
                import ctypes
                handle = ctypes.windll.kernel32.OpenProcess(0x1000, False, pid)
                if handle:
                    ctypes.windll.kernel32.CloseHandle(handle)
                    self._log_line(f"[i]  Mevcut sunucu tespit edildi — PID {pid}")
                    self._update_status()
            except Exception:
                PID_FILE.unlink(missing_ok=True)

    # ── Pencere kapatma ───────────────────────────────────────────────────
    def _on_close(self):
        global _server_proc
        if _server_proc and _server_proc.poll() is None:
            import tkinter.messagebox as mb
            ans = mb.askyesnocancel(
                "Çıkış",
                "Sunucu çalışıyor.\n\nSunucuyu durdurup çıkılsın mı?\n"
                "(Hayır = sunucu çalışmaya devam eder, panel kapanır)"
            )
            if ans is None:   # İptal
                return
            if ans:           # Evet — durdur
                self.do_stop()
        self.destroy()


if __name__ == "__main__":
    app = Panel()
    app.mainloop()

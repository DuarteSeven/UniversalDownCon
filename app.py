import os
import sys
import re
import json
import threading
import subprocess
from tkinter import filedialog, messagebox
import customtkinter as ctk
import yt_dlp
import requests
from PIL import Image, ImageDraw
import pystray

# Suporte a registo do Windows para arranque automático
if os.name == 'nt':
    import winreg
    import ctypes

# TkinterDnD integration para drag-and-drop
try:
    from tkinterdnd2 import TkinterDnD, DND_FILES
    HAS_DND = True
except ImportError:
    HAS_DND = False

try:
    import static_ffmpeg
    static_ffmpeg.add_paths()
except ImportError:
    pass

from converter import convert_media, detect_file_category, compress_video_to_size

ctk.set_appearance_mode("Dark")
ctk.set_default_color_theme("blue")

CONFIG_FILE = os.path.join(os.path.expanduser("~"), ".mediahub_config.json")
STARTUP_REG_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
APP_NAME = "MediaHub"


def load_config():
    default_dir = os.path.expanduser("~/Downloads")
    default_config = {
        "dl_folder": default_dir,
        "conv_folder": default_dir,
        "minimize_to_tray": True,
        "start_minimized": False
    }
    if os.path.isfile(CONFIG_FILE):
        try:
            with open(CONFIG_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
                default_config.update(data)
        except Exception:
            pass
    return default_config


def save_config(cfg):
    try:
        with open(CONFIG_FILE, "w", encoding="utf-8") as f:
            json.dump(cfg, f, indent=4)
    except Exception:
        pass


def set_windows_startup(enable: bool):
    """Regista ou remove o programa no arranque do Windows."""
    if os.name != 'nt':
        return
    try:
        key = winreg.OpenKey(winreg.HKEY_CURRENT_USER, STARTUP_REG_KEY, 0, winreg.KEY_SET_VALUE)
        if enable:
            if getattr(sys, 'frozen', False):
                exe_path = f'"{sys.executable}" --minimized'
            else:
                exe_path = f'"{sys.executable}" "{os.path.abspath(__file__)}" --minimized'
            winreg.SetValueEx(key, APP_NAME, 0, winreg.REG_SZ, exe_path)
        else:
            try:
                winreg.DeleteValue(key, APP_NAME)
            except FileNotFoundError:
                pass
        winreg.CloseKey(key)
    except Exception as e:
        print("Erro ao definir arranque no registo:", e)


def register_start_menu_shortcut():
    """Cria o atalho no Menu Iniciar para indexação do Windows."""
    if os.name != 'nt':
        return
    start_menu = os.path.join(os.environ.get('APPDATA', ''), r'Microsoft\Windows\Start Menu\Programs')
    shortcut_path = os.path.join(start_menu, "MediaHub.lnk")
    if os.path.exists(shortcut_path):
        return

    target = sys.executable if getattr(sys, 'frozen', False) else os.path.abspath(__file__)
    work_dir = os.path.dirname(target)
    ps_cmd = (
        f'$WshShell = New-Object -ComObject WScript.Shell; '
        f'$Shortcut = $WshShell.CreateShortcut("{shortcut_path}"); '
        f'$Shortcut.TargetPath = "{target}"; '
        f'$Shortcut.WorkingDirectory = "{work_dir}"; '
        f'$Shortcut.Save()'
    )
    try:
        startupinfo = subprocess.STARTUPINFO()
        startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
        subprocess.run(['powershell', '-WindowStyle', 'Hidden', '-Command', ps_cmd], startupinfo=startupinfo)
    except Exception:
        pass


if HAS_DND:
    class BaseWindow(ctk.CTk, TkinterDnD.DnDWrapper):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            self.TkdndVersion = TkinterDnD._require(self)
else:
    class BaseWindow(ctk.CTk):
        pass


class MediaApp(BaseWindow):
    def __init__(self, start_minimized=False):
        super().__init__()

        self.config = load_config()

        self.title("Media Hub: Downloader & Converter")
        self.geometry("740x660")
        self.resizable(False, False)

        # Registo do atalho do Windows
        register_start_menu_shortcut()

        # Pastas configuradas de forma independente
        self.dl_folder = self.config.get("dl_folder", os.path.expanduser("~/Downloads"))
        self.conv_folder = self.config.get("conv_folder", os.path.expanduser("~/Downloads"))
        self.selected_convert_file = ""

        # Variáveis de Tray e Arranque
        self.minimize_to_tray_var = ctk.BooleanVar(value=self.config.get("minimize_to_tray", True))
        self.start_minimized_var = ctk.BooleanVar(value=self.config.get("start_minimized", False))

        # Tabview layout
        self.tabs = ctk.CTkTabview(self, width=710, height=630)
        self.tabs.pack(padx=15, pady=15, fill="both", expand=True)

        self.tab_dl = self.tabs.add("Downloader")
        self.tab_conv = self.tabs.add("Universal Converter")
        self.tab_settings = self.tabs.add("Definições")

        self._setup_downloader_tab()
        self._setup_converter_tab()
        self._setup_settings_tab()

        if HAS_DND:
            self.drop_target_register(DND_FILES)
            self.dnd_bind('<<Drop>>', self._on_file_drop)

        self.protocol("WM_DELETE_WINDOW", self._on_close_window)

        self.tray_icon = None
        self._init_system_tray()

        if start_minimized or (self.start_minimized_var.get() and "--minimized" in sys.argv):
            self.withdraw()
            self._trim_memory()

    # ==========================================
    # MEMÓRIA & SYSTEM TRAY
    # ==========================================
    def _trim_memory(self):
        """Liberta páginas de memória inativas no Windows de forma segura."""
        is_busy = (
            self.dl_btn.cget("text") == "A processar..." or
            self.conv_btn.cget("text") == "A converter..."
        )
        if is_busy:
            return

        if os.name == 'nt':
            try:
                ctypes.windll.psapi.EmptyWorkingSet(ctypes.windll.kernel32.GetCurrentProcess())
            except Exception:
                pass

    def _create_tray_image(self):
        img = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
        draw = ImageDraw.Draw(img)
        draw.rounded_rectangle((4, 4, 60, 60), radius=16, fill="#1f538d")
        draw.polygon([(24, 18), (48, 32), (24, 46)], fill="white")
        return img

    def _init_system_tray(self):
        def on_show_window(icon, item):
            self.after(0, self._show_window)

        def toggle_tray_minimize(icon, item):
            new_val = not self.minimize_to_tray_var.get()
            self.minimize_to_tray_var.set(new_val)
            self._save_settings()

        def toggle_startup(icon, item):
            new_val = not self.start_minimized_var.get()
            self.start_minimized_var.set(new_val)
            self._save_settings()

        def on_exit_app(icon, item):
            self.after(0, self._quit_app)

        menu = pystray.Menu(
            pystray.MenuItem("Abrir MediaHub", on_show_window, default=True),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem(
                "Minimizar para o Tray ao fechar",
                toggle_tray_minimize,
                checked=lambda item: self.minimize_to_tray_var.get()
            ),
            pystray.MenuItem(
                "Iniciar com o Windows (Minimizado)",
                toggle_startup,
                checked=lambda item: self.start_minimized_var.get()
            ),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("Sair", on_exit_app)
        )

        self.tray_icon = pystray.Icon("MediaHub", self._create_tray_image(), "MediaHub", menu)
        threading.Thread(target=self.tray_icon.run, daemon=True).start()

    def _show_window(self):
        self.deiconify()
        self.lift()
        self.focus_force()

    def _on_close_window(self):
        if self.minimize_to_tray_var.get():
            self.withdraw()
            self._trim_memory()
        else:
            self._quit_app()

    def _quit_app(self):
        if self.tray_icon:
            self.tray_icon.stop()
        self.destroy()
        sys.exit(0)

    def _save_settings(self):
        self.config["dl_folder"] = self.dl_folder
        self.config["conv_folder"] = self.conv_folder
        self.config["minimize_to_tray"] = self.minimize_to_tray_var.get()
        self.config["start_minimized"] = self.start_minimized_var.get()
        save_config(self.config)
        set_windows_startup(self.start_minimized_var.get())

    def _open_folder(self, path):
        if not os.path.exists(path):
            os.makedirs(path, exist_ok=True)
        if os.name == 'nt':
            os.startfile(path)
        else:
            subprocess.Popen(['open' if sys.platform == 'darwin' else 'xdg-open', path])

    def _shorten_path(self, path: str, max_chars=34):
        return ("..." + path[-max_chars:]) if len(path) > max_chars else path

    # ==========================================
    # DRAG AND DROP
    # ==========================================
    def _on_file_drop(self, event):
        raw_data = event.data.strip()
        if raw_data.startswith('{') and raw_data.endswith('}'):
            file_path = raw_data[1:-1]
        else:
            file_path = self.tk.splitlist(raw_data)[0]

        if os.path.isfile(file_path):
            self.tabs.set("Universal Converter")
            self._load_file_into_converter(file_path)

    # ==========================================
    # TAB 1: DOWNLOADER
    # ==========================================
    def _setup_downloader_tab(self):
        parent = self.tab_dl

        ctk.CTkLabel(parent, text="Web Media Downloader", font=ctk.CTkFont(size=20, weight="bold")).pack(pady=(6, 2))
        ctk.CTkLabel(parent, text="YouTube, SoundCloud, TikTok, Instagram, X, Spotify", text_color="gray70").pack(pady=(0, 10))

        url_frame = ctk.CTkFrame(parent)
        url_frame.pack(fill="x", padx=15, pady=4)
        ctk.CTkLabel(url_frame, text="URL:", font=ctk.CTkFont(weight="bold")).pack(side="left", padx=10)
        self.dl_url_entry = ctk.CTkEntry(url_frame, placeholder_text="Cole o link do vídeo/áudio aqui...")
        self.dl_url_entry.pack(side="left", fill="x", expand=True, padx=(0, 10), pady=8)

        opt_frame = ctk.CTkFrame(parent)
        opt_frame.pack(fill="x", padx=15, pady=5)

        ctk.CTkLabel(opt_frame, text="Tipo:").grid(row=0, column=0, padx=10, pady=6, sticky="w")
        self.dl_type_menu = ctk.CTkOptionMenu(opt_frame, values=["Video", "Audio Only"], command=self._on_dl_type_change)
        self.dl_type_menu.grid(row=0, column=1, padx=10, pady=6)

        ctk.CTkLabel(opt_frame, text="Formato:").grid(row=0, column=2, padx=10, pady=6, sticky="w")
        self.dl_format_menu = ctk.CTkOptionMenu(opt_frame, values=["mp4", "mkv", "webm"], command=self._on_dl_format_changed)
        self.dl_format_menu.grid(row=0, column=3, padx=10, pady=6)

        # Checkbox WebM Legacy para Downloader
        self.dl_legacy_webm_var = ctk.BooleanVar(value=False)
        self.dl_legacy_webm_cb = ctk.CTkCheckBox(
            opt_frame,
            text="Legacy/MV (VP8)",
            variable=self.dl_legacy_webm_var
        )

        # Compressão
        dl_comp_frame = ctk.CTkFrame(parent)
        dl_comp_frame.pack(fill="x", padx=15, pady=5)

        self.dl_compress_var = ctk.BooleanVar(value=False)
        self.dl_compress_checkbox = ctk.CTkCheckBox(
            dl_comp_frame,
            text="Compress",
            variable=self.dl_compress_var,
            command=self._toggle_dl_compress
        )
        self.dl_compress_checkbox.grid(row=0, column=0, padx=10, pady=8, sticky="w")

        self.dl_slider = ctk.CTkSlider(
            dl_comp_frame,
            from_=8,
            to=100,
            number_of_steps=92,
            command=self._on_dl_slider_change,
            state="disabled"
        )
        self.dl_slider.set(19)
        self.dl_slider.grid(row=0, column=1, padx=10, pady=8, sticky="ew")
        dl_comp_frame.grid_columnconfigure(1, weight=1)

        self.dl_size_label = ctk.CTkLabel(dl_comp_frame, text="19 MB", width=55, font=ctk.CTkFont(weight="bold"), text_color="gray50")
        self.dl_size_label.grid(row=0, column=2, padx=5, pady=8)

        self.dl_preset_btn = ctk.CTkButton(
            dl_comp_frame,
            text="Discord (<20MB)",
            width=120,
            fg_color="#5865F2",
            hover_color="#4752C4",
            command=lambda: self._set_discord_preset("dl")
        )
        self.dl_preset_btn.grid(row=0, column=3, padx=10, pady=8)

        # Pasta de Destino (Downloader)
        folder_frame = ctk.CTkFrame(parent)
        folder_frame.pack(fill="x", padx=15, pady=5)
        ctk.CTkLabel(folder_frame, text="Guardar em:").pack(side="left", padx=10)
        self.dl_folder_label = ctk.CTkLabel(folder_frame, text=self._shorten_path(self.dl_folder), text_color="gray80")
        self.dl_folder_label.pack(side="left", fill="x", expand=True, padx=5)

        ctk.CTkButton(folder_frame, text="Mudar Pasta", width=90, command=self._browse_dl_folder).pack(side="left", padx=5, pady=6)
        ctk.CTkButton(folder_frame, text="Abrir", width=60, fg_color="gray40", command=lambda: self._open_folder(self.dl_folder)).pack(side="left", padx=5, pady=6)

        self.dl_status_label = ctk.CTkLabel(parent, text="Pronto", text_color="gray60")
        self.dl_status_label.pack(pady=(8, 2))
        self.dl_progress = ctk.CTkProgressBar(parent)
        self.dl_progress.set(0)
        self.dl_progress.pack(fill="x", padx=15, pady=4)

        self.dl_btn = ctk.CTkButton(parent, text="Descarregar Média", font=ctk.CTkFont(weight="bold"), height=38, command=self._start_download)
        self.dl_btn.pack(fill="x", padx=15, pady=10)

    def _browse_dl_folder(self):
        folder = filedialog.askdirectory(initialdir=self.dl_folder)
        if folder:
            self.dl_folder = folder
            self.dl_folder_label.configure(text=self._shorten_path(folder))
            self.settings_dl_label.configure(text=self._shorten_path(folder))
            self._save_settings()

    def _toggle_dl_compress(self):
        active = self.dl_compress_var.get()
        self.dl_slider.configure(state="normal" if active else "disabled")
        self.dl_size_label.configure(text_color="white" if active else "gray50")

    def _on_dl_slider_change(self, val):
        self.dl_size_label.configure(text=f"{int(val)} MB")

    def _update_dl_legacy_webm_visibility(self):
        if self.dl_type_menu.get() == "Video" and self.dl_format_menu.get() == "webm":
            self.dl_legacy_webm_cb.grid(row=0, column=4, padx=10, pady=6)
        else:
            self.dl_legacy_webm_cb.grid_forget()

    def _on_dl_format_changed(self, choice):
        self._update_dl_legacy_webm_visibility()

    def _on_dl_type_change(self, choice):
        if choice == "Audio Only":
            self.dl_format_menu.configure(values=["mp3", "wav", "ogg", "flac", "m4a"])
            self.dl_format_menu.set("mp3")
            self.dl_compress_var.set(False)
            self._toggle_dl_compress()
            self.dl_compress_checkbox.configure(state="disabled")
            self.dl_preset_btn.configure(state="disabled")
        else:
            self.dl_format_menu.configure(values=["mp4", "mkv", "webm"])
            self.dl_format_menu.set("mp4")
            self.dl_compress_checkbox.configure(state="normal")
            self.dl_preset_btn.configure(state="normal")
        self._update_dl_legacy_webm_visibility()

    def _set_discord_preset(self, target):
        if target == "dl":
            self.dl_compress_var.set(True)
            self._toggle_dl_compress()
            self.dl_slider.set(19)
            self.dl_size_label.configure(text="19 MB")
        else:
            self.conv_compress_var.set(True)
            self._toggle_conv_compress()
            self.conv_slider.set(19)
            self.conv_size_label.configure(text="19 MB")

    def _resolve_spotify_track(self, url: str) -> str:
        match = re.search(r'track/([a-zA-Z0-9]+)', url)
        if not match:
            raise RuntimeError("URL do Spotify inválido.")
        track_id = match.group(1)

        track_title = ""
        artist_name = ""
        headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'}

        try:
            r = requests.get(f"https://open.spotify.com/embed/track/{track_id}", headers=headers, timeout=10)
            if r.status_code == 200:
                data_match = re.search(r'<script id="__NEXT_DATA__" type="application/json">(.*?)</script>', r.text)
                if data_match:
                    js = json.loads(data_match.group(1))
                    entity = js.get('props', {}).get('pageProps', {}).get('state', {}).get('data', {}).get('entity', {})
                    track_title = entity.get('name', '')
                    artists = entity.get('artists', [])
                    if artists:
                        artist_name = artists[0].get('name', '')
                if not track_title:
                    title_tag = re.search(r'<title>(.*?)</title>', r.text)
                    if title_tag:
                        full_t = title_tag.group(1).replace(" | Spotify", "").replace("Spotify – ", "").strip()
                        if " - " in full_t:
                            parts = full_t.split(" - ")
                            track_title, artist_name = parts[0].strip(), parts[1].strip()
        except Exception:
            pass

        if not track_title:
            try:
                res = requests.get(f"https://open.spotify.com/oembed?url=https://open.spotify.com/track/{track_id}", headers=headers, timeout=10)
                if res.status_code == 200:
                    track_title = res.json().get("title", "")
            except Exception:
                pass

        if not track_title:
            raise RuntimeError("Não foi possível obter dados da música do Spotify.")

        query = f"ytsearch1:{artist_name} {track_title}" if artist_name else f"ytsearch1:{track_title}"
        self.after(0, lambda: self.dl_status_label.configure(text=f"A pesquisar: {artist_name} - {track_title}", text_color="gray70"))
        return query

    def _start_download(self):
        url = self.dl_url_entry.get().strip()
        if not url:
            messagebox.showwarning("Aviso", "Por favor insira um URL válido.")
            return

        if "open.spotify.com/track" in url or "spotify.com/intl-" in url:
            self.dl_status_label.configure(text="A identificar faixa do Spotify...", text_color="gray70")
            try:
                url = self._resolve_spotify_track(url)
            except Exception as err:
                messagebox.showerror("Erro Spotify", str(err))
                return

        if "&list=" in url:
            url = url.split("&list=")[0]
        if "music.youtube.com" in url:
            url = url.replace("music.youtube.com", "www.youtube.com")

        self.dl_btn.configure(state="disabled", text="A processar...")
        self.dl_status_label.configure(text="A conectar...", text_color="gray70")
        self.dl_progress.set(0)

        threading.Thread(target=self._run_downloader, args=(url,), daemon=True).start()

    def _run_downloader(self, url):
        mode = self.dl_type_menu.get()
        fmt = self.dl_format_menu.get()
        outtmpl = os.path.join(self.dl_folder, "%(title)s.%(ext)s")

        def hook(d):
            if d['status'] == 'downloading':
                tot = d.get('total_bytes') or d.get('total_bytes_estimate') or 1
                cur = d.get('downloaded_bytes', 0)
                ratio = cur / tot
                pct = int(ratio * 100)
                self.after(0, lambda: self.dl_progress.set(ratio))
                self.after(0, lambda: self.dl_status_label.configure(text=f"A transferir: {pct}%"))
            elif d['status'] == 'finished':
                self.after(0, lambda: self.dl_status_label.configure(text="A processar / Juntar com FFmpeg..."))

        ydl_opts = {
            'outtmpl': outtmpl,
            'progress_hooks': [hook],
            'quiet': True,
            'noplaylist': True,
            'extract_flat': False,
            'default_search': 'ytsearch',
        }

        if mode == "Audio Only":
            ydl_opts.update({
                'format': 'bestaudio/best',
                'writethumbnail': True,
                'postprocessors': [
                    {'key': 'FFmpegThumbnailsConvertor', 'format': 'jpg'},
                    {'key': 'FFmpegExtractAudio', 'preferredcodec': fmt, 'preferredquality': '320' if fmt == 'mp3' else '0'},
                    {'key': 'FFmpegMetadata', 'add_metadata': True},
                    {'key': 'EmbedThumbnail', 'already_have_thumbnail': False},
                ],
            })
        else:
            if fmt == 'webm' and self.dl_legacy_webm_var.get():
                merger_args = ['-c:v', 'libvpx', '-crf', '10', '-b:v', '2M', '-c:a', 'libvorbis', '-q:a', '4']
            elif fmt in ['mkv', 'webm']:
                merger_args = ['-c:v', 'copy', '-c:a', 'copy']
            else:
                merger_args = ['-c:v', 'copy', '-c:a', 'aac', '-b:a', '256k']

            ydl_opts.update({
                'format': 'bestvideo*+bestaudio/best',
                'merge_output_format': fmt,
                'postprocessor_args': {'merger': merger_args}
            })

        try:
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                info = ydl.extract_info(url, download=True)
                downloaded_file = ydl.prepare_filename(info)

                if mode != "Audio Only":
                    base = os.path.splitext(downloaded_file)[0]
                    downloaded_file = f"{base}.{fmt}"

            if mode != "Audio Only" and self.dl_compress_var.get() and os.path.isfile(downloaded_file):
                target_mb = float(self.dl_slider.get())
                self.after(0, lambda: self.dl_status_label.configure(text=f"A comprimir para < {int(target_mb)}MB..."))
                compress_video_to_size(downloaded_file, target_mb=target_mb)

            self.after(0, self._on_dl_success)
        except Exception as e:
            self.after(0, lambda: self._on_dl_error(str(e)))

    def _on_dl_success(self):
        self.dl_btn.configure(state="normal", text="Descarregar Média")
        self.dl_status_label.configure(text="Download Concluído!", text_color="#2ecc71")
        self.dl_progress.set(1.0)
        self._trim_memory()
        messagebox.showinfo("Sucesso", "Download terminado com sucesso!")

    def _on_dl_error(self, err):
        self.dl_btn.configure(state="normal", text="Descarregar Média")
        self.dl_status_label.configure(text="Erro no Download", text_color="#e74c3c")
        self._trim_memory()
        messagebox.showerror("Erro", err)

    # ==========================================
    # TAB 2: UNIVERSAL CONVERTER
    # ==========================================
    def _setup_converter_tab(self):
        parent = self.tab_conv

        ctk.CTkLabel(parent, text="Universal File Converter", font=ctk.CTkFont(size=20, weight="bold")).pack(pady=(6, 2))
        hint_text = "Arrasta ficheiros diretamente para aqui ou clica em Escolher Ficheiro" if HAS_DND else "Converte ficheiros locais com compressão opcional"
        ctk.CTkLabel(parent, text=hint_text, text_color="gray70").pack(pady=(0, 10))

        file_frame = ctk.CTkFrame(parent)
        file_frame.pack(fill="x", padx=15, pady=4)
        self.conv_file_label = ctk.CTkLabel(file_frame, text="Nenhum ficheiro selecionado", text_color="gray70")
        self.conv_file_label.pack(side="left", fill="x", expand=True, padx=10, pady=8)
        ctk.CTkButton(file_frame, text="Escolher Ficheiro", width=120, command=self._select_convert_file).pack(side="right", padx=10, pady=8)

        # Formato e Opções contextuais (GIF / WebM Legacy)
        self.conv_opt_frame = ctk.CTkFrame(parent)
        self.conv_opt_frame.pack(fill="x", padx=15, pady=5)

        ctk.CTkLabel(self.conv_opt_frame, text="Formato Alvo:").pack(side="left", padx=(10, 5), pady=8)
        
        # Menu de formatos
        self.conv_target_format = ctk.CTkOptionMenu(
            self.conv_opt_frame,
            values=["Selecione um ficheiro primeiro"],
            command=self._on_conv_format_changed
        )
        self.conv_target_format.pack(side="left", padx=5, pady=8)

        # Seletor contextual de compressão para GIFs
        self.conv_gif_mode_menu = ctk.CTkOptionMenu(
            self.conv_opt_frame,
            values=["Balanced", "Compress Quality", "Compress Frames"],
            width=160
        )
        self.conv_gif_mode_menu.set("Balanced")

        # Checkbox contextual para WebM Legacy/MV
        self.conv_legacy_webm_var = ctk.BooleanVar(value=False)
        self.conv_legacy_webm_cb = ctk.CTkCheckBox(
            self.conv_opt_frame,
            text="Legacy/MV (VP8)",
            variable=self.conv_legacy_webm_var
        )

        # Compressão (Converter)
        conv_comp_frame = ctk.CTkFrame(parent)
        conv_comp_frame.pack(fill="x", padx=15, pady=5)

        self.conv_compress_var = ctk.BooleanVar(value=False)
        self.conv_compress_checkbox = ctk.CTkCheckBox(
            conv_comp_frame,
            text="Compress",
            variable=self.conv_compress_var,
            command=self._toggle_conv_compress,
            state="disabled"
        )
        self.conv_compress_checkbox.grid(row=0, column=0, padx=10, pady=8, sticky="w")

        self.conv_slider = ctk.CTkSlider(
            conv_comp_frame,
            from_=8,
            to=100,
            number_of_steps=92,
            command=self._on_conv_slider_change,
            state="disabled"
        )
        self.conv_slider.set(19)
        self.conv_slider.grid(row=0, column=1, padx=10, pady=8, sticky="ew")
        conv_comp_frame.grid_columnconfigure(1, weight=1)

        self.conv_size_label = ctk.CTkLabel(conv_comp_frame, text="19 MB", width=55, font=ctk.CTkFont(weight="bold"), text_color="gray50")
        self.conv_size_label.grid(row=0, column=2, padx=5, pady=8)

        self.conv_preset_btn = ctk.CTkButton(
            conv_comp_frame,
            text="Discord (<20MB)",
            width=120,
            fg_color="#5865F2",
            hover_color="#4752C4",
            command=lambda: self._set_discord_preset("conv"),
            state="disabled"
        )
        self.conv_preset_btn.grid(row=0, column=3, padx=10, pady=8)

        # Pasta de Destino (Converter)
        conv_folder_frame = ctk.CTkFrame(parent)
        conv_folder_frame.pack(fill="x", padx=15, pady=5)
        ctk.CTkLabel(conv_folder_frame, text="Guardar em:").pack(side="left", padx=10)
        self.conv_folder_label = ctk.CTkLabel(conv_folder_frame, text=self._shorten_path(self.conv_folder), text_color="gray80")
        self.conv_folder_label.pack(side="left", fill="x", expand=True, padx=5)

        ctk.CTkButton(conv_folder_frame, text="Mudar Pasta", width=90, command=self._browse_conv_folder).pack(side="left", padx=5, pady=6)
        ctk.CTkButton(conv_folder_frame, text="Abrir", width=60, fg_color="gray40", command=lambda: self._open_folder(self.conv_folder)).pack(side="left", padx=5, pady=6)

        self.conv_status_label = ctk.CTkLabel(parent, text="Pronto", text_color="gray60")
        self.conv_status_label.pack(pady=(8, 2))

        self.conv_btn = ctk.CTkButton(parent, text="Converter Ficheiro", font=ctk.CTkFont(weight="bold"), height=38, command=self._start_conversion)
        self.conv_btn.pack(fill="x", padx=15, pady=10)

    def _update_converter_contextual_options(self):
        """Atualiza a visibilidade das opções de GIF e WebM Legacy."""
        fmt = self.conv_target_format.get().lower()
        is_gif = (fmt == "gif")
        is_webm = (fmt == "webm")
        is_comp = self.conv_compress_var.get()

        # Mostra/esconde opções de GIF
        if is_gif and is_comp:
            self.conv_gif_mode_menu.pack(side="left", padx=10, pady=8)
        else:
            self.conv_gif_mode_menu.pack_forget()

        # Mostra/esconde opção de WebM Legacy (VP8)
        if is_webm:
            self.conv_legacy_webm_cb.pack(side="left", padx=10, pady=8)
        else:
            self.conv_legacy_webm_cb.pack_forget()

    def _on_conv_format_changed(self, choice):
        self._update_converter_contextual_options()

    def _browse_conv_folder(self):
        folder = filedialog.askdirectory(initialdir=self.conv_folder)
        if folder:
            self.conv_folder = folder
            self.conv_folder_label.configure(text=self._shorten_path(folder))
            self.settings_conv_label.configure(text=self._shorten_path(folder))
            self._save_settings()

    def _toggle_conv_compress(self):
        active = self.conv_compress_var.get()
        self.conv_slider.configure(state="normal" if active else "disabled")
        self.conv_size_label.configure(text_color="white" if active else "gray50")
        self._update_converter_contextual_options()

    def _on_conv_slider_change(self, val):
        self.conv_size_label.configure(text=f"{int(val)} MB")

    def _load_file_into_converter(self, file_path: str):
        self.selected_convert_file = file_path
        self.conv_file_label.configure(text=os.path.basename(file_path), text_color="white")

        category = detect_file_category(file_path)
        if category == 'video':
            options = ["mp4", "webm", "mkv", "avi", "mov", "gif"]
            self.conv_compress_checkbox.configure(state="normal")
            self.conv_preset_btn.configure(state="normal")
        else:
            if category == 'image':
                options = ["png", "jpg", "webp", "bmp", "ico", "tiff"]
            elif category == 'audio':
                options = ["mp3", "ogg", "wav", "flac", "m4a", "aac"]
            else:
                options = ["Unsupported"]
            self._disable_conv_compress()

        self.conv_target_format.configure(values=options)
        self.conv_target_format.set(options[0])
        self._update_converter_contextual_options()

    def _select_convert_file(self):
        file_path = filedialog.askopenfilename()
        if not file_path:
            return
        self._load_file_into_converter(file_path)

    def _disable_conv_compress(self):
        self.conv_compress_var.set(False)
        self._toggle_conv_compress()
        self.conv_compress_checkbox.configure(state="disabled")
        self.conv_preset_btn.configure(state="disabled")

    def _start_conversion(self):
        if not self.selected_convert_file or not os.path.isfile(self.selected_convert_file):
            messagebox.showwarning("Aviso", "Por favor selecione um ficheiro para converter.")
            return

        fmt = self.conv_target_format.get()
        if fmt in ["Selecione um ficheiro primeiro", "Unsupported"]:
            messagebox.showwarning("Aviso", "Por favor selecione um formato válido.")
            return

        self.conv_btn.configure(state="disabled", text="A converter...")
        self.conv_status_label.configure(text="A processar ficheiro...", text_color="gray70")

        threading.Thread(target=self._run_conversion, args=(self.selected_convert_file, self.conv_folder, fmt), daemon=True).start()

    def _run_conversion(self, input_path, out_dir, fmt):
        try:
            legacy_mode = self.conv_legacy_webm_var.get() if fmt.lower() == 'webm' else False
            out_file = convert_media(input_path, out_dir, fmt, legacy_webm=legacy_mode)

            if detect_file_category(out_file) == 'video' and self.conv_compress_var.get():
                target_mb = float(self.conv_slider.get())
                self.after(0, lambda: self.conv_status_label.configure(text=f"A comprimir para < {int(target_mb)}MB..."))
                
                selected_label = self.conv_gif_mode_menu.get()
                if selected_label == "Compress Frames":
                    gif_mode = "frames"
                elif selected_label == "Compress Quality":
                    gif_mode = "quality"
                else:
                    gif_mode = "balanced"

                compress_video_to_size(out_file, target_mb=target_mb, gif_mode=gif_mode)

            self.after(0, lambda: self._on_conv_success(out_file))
        except Exception as e:
            self.after(0, lambda: self._on_conv_error(str(e)))

    def _on_conv_success(self, out_file):
        self.conv_btn.configure(state="normal", text="Converter Ficheiro")
        self.conv_status_label.configure(text="Convertido com sucesso!", text_color="#2ecc71")
        self._trim_memory()
        messagebox.showinfo("Sucesso", f"Ficheiro guardado em:\n{out_file}")

    def _on_conv_error(self, err):
        self.conv_btn.configure(state="normal", text="Converter Ficheiro")
        self.conv_status_label.configure(text="Erro na conversão.", text_color="#e74c3c")
        self._trim_memory()
        messagebox.showerror("Erro na Conversão", err)

    # ==========================================
    # TAB 3: DEFINIÇÕES
    # ==========================================
    def _setup_settings_tab(self):
        parent = self.tab_settings

        ctk.CTkLabel(parent, text="Definições da Aplicação", font=ctk.CTkFont(size=20, weight="bold")).pack(pady=(10, 15))

        paths_group = ctk.CTkFrame(parent)
        paths_group.pack(fill="x", padx=15, pady=8)

        ctk.CTkLabel(paths_group, text="Pastas Padrão (Independentes)", font=ctk.CTkFont(weight="bold")).grid(row=0, column=0, columnspan=3, padx=10, pady=8, sticky="w")

        ctk.CTkLabel(paths_group, text="Downloader:").grid(row=1, column=0, padx=10, pady=6, sticky="w")
        self.settings_dl_label = ctk.CTkLabel(paths_group, text=self._shorten_path(self.dl_folder), text_color="gray80")
        self.settings_dl_label.grid(row=1, column=1, padx=5, pady=6, sticky="ew")
        ctk.CTkButton(paths_group, text="Mudar", width=70, command=self._browse_dl_folder).grid(row=1, column=2, padx=10, pady=6)

        ctk.CTkLabel(paths_group, text="Converter:").grid(row=2, column=0, padx=10, pady=6, sticky="w")
        self.settings_conv_label = ctk.CTkLabel(paths_group, text=self._shorten_path(self.conv_folder), text_color="gray80")
        self.settings_conv_label.grid(row=2, column=1, padx=5, pady=6, sticky="ew")
        ctk.CTkButton(paths_group, text="Mudar", width=70, command=self._browse_conv_folder).grid(row=2, column=2, padx=10, pady=6)

        paths_group.grid_columnconfigure(1, weight=1)

        behavior_group = ctk.CTkFrame(parent)
        behavior_group.pack(fill="x", padx=15, pady=12)

        ctk.CTkLabel(behavior_group, text="Comportamento e Bandeja do Sistema", font=ctk.CTkFont(weight="bold")).pack(anchor="w", padx=10, pady=(8, 4))

        self.tray_switch = ctk.CTkSwitch(
            behavior_group,
            text="Minimizar para a bandeja do sistema ao fechar (ícone junto ao relógio)",
            variable=self.minimize_to_tray_var,
            command=self._save_settings
        )
        self.tray_switch.pack(anchor="w", padx=10, pady=8)

        self.startup_switch = ctk.CTkSwitch(
            behavior_group,
            text="Iniciar minimizado com o arranque do Windows",
            variable=self.start_minimized_var,
            command=self._save_settings
        )
        self.startup_switch.pack(anchor="w", padx=10, pady=8)

        bottom_frame = ctk.CTkFrame(parent)
        bottom_frame.pack(fill="x", padx=15, pady=15)

        ctk.CTkLabel(
            bottom_frame,
            text="Dica: Podes clicar com o botão direito no ícone junto à bateria/relógio\npara alternar estas opções a qualquer momento ou fechar a app.",
            text_color="gray70",
            justify="left"
        ).pack(side="left", padx=10, pady=10)

        ctk.CTkButton(
            bottom_frame,
            text="Encerrar Totalmente",
            fg_color="#e74c3c",
            hover_color="#c0392b",
            command=self._quit_app
        ).pack(side="right", padx=10, pady=10)


if __name__ == "__main__":
    start_min = "--minimized" in sys.argv
    app = MediaApp(start_minimized=start_min)
    app.mainloop()
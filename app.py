import os
import re
import requests
import threading
import subprocess
from tkinter import filedialog, messagebox
import customtkinter as ctk
import yt_dlp

# TkinterDnD integration for drag-and-drop
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


# Hybrid class allowing CustomTkinter to use TkinterDnD under the hood
if HAS_DND:
    class BaseWindow(ctk.CTk, TkinterDnD.DnDWrapper):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            self.TkdndVersion = TkinterDnD._require(self)
else:
    class BaseWindow(ctk.CTk):
        pass


class MediaApp(BaseWindow):
    def __init__(self):
        super().__init__()

        self.title("Media Hub: Downloader & Converter")
        self.geometry("740x630")
        self.resizable(False, False)

        # Paths state
        self.dl_folder = os.path.expanduser("~/Downloads")
        self.conv_folder = os.path.expanduser("~/Downloads")
        self.selected_convert_file = ""

        # Tabview layout
        self.tabs = ctk.CTkTabview(self, width=710, height=600)
        self.tabs.pack(padx=15, pady=15, fill="both", expand=True)

        self.tab_dl = self.tabs.add("Downloader")
        self.tab_conv = self.tabs.add("Universal Converter")

        self._setup_downloader_tab()
        self._setup_converter_tab()

        # Enable Drag-and-Drop if library is installed
        if HAS_DND:
            self.drop_target_register(DND_FILES)
            self.dnd_bind('<<Drop>>', self._on_file_drop)

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
    # DRAG AND DROP HANDLER
    # ==========================================
    def _on_file_drop(self, event):
        raw_data = event.data.strip()
        # Handles Windows Explorer file paths with spaces or curly braces
        if raw_data.startswith('{') and raw_data.endswith('}'):
            file_path = raw_data[1:-1]
        else:
            # If multiple files are dropped, grab the first one
            file_path = self.tk.splitlist(raw_data)[0]

        if os.path.isfile(file_path):
            # Automatically switch to the Converter tab when a local file is dropped
            self.tabs.set("Universal Converter")
            self._load_file_into_converter(file_path)

    # ==========================================
    # TAB 1: DOWNLOADER
    # ==========================================
    def _setup_downloader_tab(self):
        parent = self.tab_dl

        ctk.CTkLabel(parent, text="Web Media Downloader", font=ctk.CTkFont(size=20, weight="bold")).pack(pady=(6, 2))
        ctk.CTkLabel(parent, text="YouTube, SoundCloud, TikTok, Instagram, X, Spotify", text_color="gray70").pack(pady=(0, 10))

        # URL Entry
        url_frame = ctk.CTkFrame(parent)
        url_frame.pack(fill="x", padx=15, pady=4)
        ctk.CTkLabel(url_frame, text="URL:", font=ctk.CTkFont(weight="bold")).pack(side="left", padx=10)
        self.dl_url_entry = ctk.CTkEntry(url_frame, placeholder_text="Paste video/audio URL here...")
        self.dl_url_entry.pack(side="left", fill="x", expand=True, padx=(0, 10), pady=8)

        # Type & Format
        opt_frame = ctk.CTkFrame(parent)
        opt_frame.pack(fill="x", padx=15, pady=5)

        ctk.CTkLabel(opt_frame, text="Type:").grid(row=0, column=0, padx=10, pady=6, sticky="w")
        self.dl_type_menu = ctk.CTkOptionMenu(opt_frame, values=["Video", "Audio Only"], command=self._on_dl_type_change)
        self.dl_type_menu.grid(row=0, column=1, padx=10, pady=6)

        ctk.CTkLabel(opt_frame, text="Format:").grid(row=0, column=2, padx=10, pady=6, sticky="w")
        self.dl_format_menu = ctk.CTkOptionMenu(opt_frame, values=["mp4", "mkv", "webm"])
        self.dl_format_menu.grid(row=0, column=3, padx=10, pady=6)

        # Compress Checkbox & Slider Row
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

        # Folder Select
        folder_frame = ctk.CTkFrame(parent)
        folder_frame.pack(fill="x", padx=15, pady=5)
        ctk.CTkLabel(folder_frame, text="Save To:").pack(side="left", padx=10)
        self.dl_folder_label = ctk.CTkLabel(folder_frame, text=self._shorten_path(self.dl_folder), text_color="gray80")
        self.dl_folder_label.pack(side="left", fill="x", expand=True, padx=5)

        ctk.CTkButton(folder_frame, text="Browse", width=70, command=self._browse_dl_folder).pack(side="left", padx=5, pady=6)
        ctk.CTkButton(folder_frame, text="Open", width=60, fg_color="gray40", command=lambda: self._open_folder(self.dl_folder)).pack(side="left", padx=5, pady=6)

        # Status & Progress
        self.dl_status_label = ctk.CTkLabel(parent, text="Ready", text_color="gray60")
        self.dl_status_label.pack(pady=(8, 2))
        self.dl_progress = ctk.CTkProgressBar(parent)
        self.dl_progress.set(0)
        self.dl_progress.pack(fill="x", padx=15, pady=4)

        # Download Button
        self.dl_btn = ctk.CTkButton(parent, text="Download Media", font=ctk.CTkFont(weight="bold"), height=38, command=self._start_download)
        self.dl_btn.pack(fill="x", padx=15, pady=10)

    def _toggle_dl_compress(self):
        active = self.dl_compress_var.get()
        self.dl_slider.configure(state="normal" if active else "disabled")
        self.dl_size_label.configure(text_color="white" if active else "gray50")

    def _on_dl_slider_change(self, val):
        self.dl_size_label.configure(text=f"{int(val)} MB")

    def _browse_dl_folder(self):
        folder = filedialog.askdirectory(initialdir=self.dl_folder)
        if folder:
            self.dl_folder = folder
            self.dl_folder_label.configure(text=self._shorten_path(folder))

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
        """
        Extrai o artista e título reais através da página pública embed do Spotify
        (que nunca bloqueia scraping) e seleciona o vídeo exato no YouTube.
        """
        import json
        
        # 1. Extrair o ID da faixa a partir do URL (ex: 4heaLu6Y8zvYzQQaEQ8iEB)
        match = re.search(r'track/([a-zA-Z0-9]+)', url)
        if not match:
            raise RuntimeError("URL do Spotify inválido.")
        track_id = match.group(1)

        track_title = ""
        artist_name = ""

        # 2. Consultar a página embed pública (aberta e sem proteções anti-bot)
        embed_url = f"https://open.spotify.com/embed/track/{track_id}"
        headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
        }

        try:
            r = requests.get(embed_url, headers=headers, timeout=10)
            if r.status_code == 200:
                # O Spotify guarda os dados no script __NEXT_DATA__ ou num JSON escapado dentro do HTML
                data_match = re.search(r'<script id="__NEXT_DATA__" type="application/json">(.*?)</script>', r.text)
                if data_match:
                    js = json.loads(data_match.group(1))
                    entity = js.get('props', {}).get('pageProps', {}).get('state', {}).get('data', {}).get('entity', {})
                    track_title = entity.get('name', '')
                    artists = entity.get('artists', [])
                    if artists:
                        artist_name = artists[0].get('name', '')

                # Fallback: Extrair diretamente da tag <title> do embed (formato: "Título - Artista | Spotify")
                if not track_title:
                    title_tag = re.search(r'<title>(.*?)</title>', r.text)
                    if title_tag:
                        full_t = title_tag.group(1).replace(" | Spotify", "").replace("Spotify – ", "").strip()
                        if " - " in full_t:
                            parts = full_t.split(" - ")
                            track_title = parts[0].strip()
                            artist_name = parts[1].strip()
                        elif " by " in full_t:
                            parts = full_t.split(" by ")
                            track_title = parts[0].strip()
                            artist_name = parts[1].strip()
        except Exception:
            pass

        # 3. Se ainda não tiver os dados, tentar o endpoint oficial oEmbed
        if not track_title:
            try:
                res = requests.get(f"https://open.spotify.com/oembed?url=https://open.spotify.com/track/{track_id}", headers=headers, timeout=10)
                if res.status_code == 200:
                    track_title = res.json().get("title", "")
            except Exception:
                pass

        if not track_title:
            raise RuntimeError("Não foi possível obter os dados da música do Spotify.")

        # Montar a pesquisa explícita no YouTube
        if artist_name:
            query = f"ytsearch1:{artist_name} {track_title}"
        else:
            query = f"ytsearch1:{track_title}"

        # Atualiza a mensagem na interface para saberes exatamente o que está a ser pesquisado
        self.after(0, lambda: self.dl_status_label.configure(
            text=f"A pesquisar: {artist_name} - {track_title}",
            text_color="gray70"
        ))

        return query

    def _start_download(self):
        url = self.dl_url_entry.get().strip()
        if not url:
            messagebox.showwarning("Warning", "Please enter a valid URL.")
            return

        if "open.spotify.com/track" in url or "spotify.com/intl-" in url:
            self.dl_status_label.configure(text="Resolving Spotify track...", text_color="gray70")
            try:
                url = self._resolve_spotify_track(url)
            except Exception as err:
                messagebox.showerror("Spotify Error", str(err))
                return

        if "&list=" in url:
            url = url.split("&list=")[0]
        if "music.youtube.com" in url:
            url = url.replace("music.youtube.com", "www.youtube.com")

        self.dl_btn.configure(state="disabled", text="Processing...")
        self.dl_status_label.configure(text="Connecting...", text_color="gray70")
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
                self.after(0, lambda: self.dl_status_label.configure(text=f"Downloading: {pct}%"))
            elif d['status'] == 'finished':
                self.after(0, lambda: self.dl_status_label.configure(text="Processing / Merging with FFmpeg..."))

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
            merger_args = ['-c:v', 'copy', '-c:a', 'copy'] if fmt in ['mkv', 'webm'] else ['-c:v', 'copy', '-c:a', 'aac', '-b:a', '256k']
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
                self.after(0, lambda: self.dl_status_label.configure(text=f"Compressing under {int(target_mb)}MB..."))
                compress_video_to_size(downloaded_file, target_mb=target_mb)

            self.after(0, self._on_dl_success)
        except Exception as e:
            self.after(0, lambda: self._on_dl_error(str(e)))

    def _on_dl_success(self):
        self.dl_btn.configure(state="normal", text="Download Media")
        self.dl_status_label.configure(text="Download Complete!", text_color="#2ecc71")
        self.dl_progress.set(1.0)
        messagebox.showinfo("Success", "Download finished successfully!")

    def _on_dl_error(self, err):
        self.dl_btn.configure(state="normal", text="Download Media")
        self.dl_status_label.configure(text="Download Failed", text_color="#e74c3c")
        messagebox.showerror("Error", err)

    # ==========================================
    # TAB 2: UNIVERSAL CONVERTER
    # ==========================================
    def _setup_converter_tab(self):
        parent = self.tab_conv

        ctk.CTkLabel(parent, text="Universal File Converter", font=ctk.CTkFont(size=20, weight="bold")).pack(pady=(6, 2))
        hint_text = "Drag & drop files directly here, or click Choose File" if HAS_DND else "Convert local Images, Audio, or Videos with optional compression"
        ctk.CTkLabel(parent, text=hint_text, text_color="gray70").pack(pady=(0, 10))

        # File Select Box
        file_frame = ctk.CTkFrame(parent)
        file_frame.pack(fill="x", padx=15, pady=4)
        self.conv_file_label = ctk.CTkLabel(file_frame, text="Drag a file here or Choose File...", text_color="gray70")
        self.conv_file_label.pack(side="left", fill="x", expand=True, padx=10, pady=8)
        ctk.CTkButton(file_frame, text="Choose File", width=100, command=self._select_convert_file).pack(side="right", padx=10, pady=8)

        # Output Format Choice
        conv_opt_frame = ctk.CTkFrame(parent)
        conv_opt_frame.pack(fill="x", padx=15, pady=5)
        ctk.CTkLabel(conv_opt_frame, text="Target Format:").pack(side="left", padx=10, pady=8)
        self.conv_target_format = ctk.CTkOptionMenu(conv_opt_frame, values=["Select a file first"])
        self.conv_target_format.pack(side="left", padx=10, pady=8)

        # Compress Checkbox & Slider Row (Converter)
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

        # Save Folder Selection
        conv_folder_frame = ctk.CTkFrame(parent)
        conv_folder_frame.pack(fill="x", padx=15, pady=5)
        ctk.CTkLabel(conv_folder_frame, text="Save To:").pack(side="left", padx=10)
        self.conv_folder_label = ctk.CTkLabel(conv_folder_frame, text=self._shorten_path(self.conv_folder), text_color="gray80")
        self.conv_folder_label.pack(side="left", fill="x", expand=True, padx=5)

        ctk.CTkButton(conv_folder_frame, text="Browse", width=70, command=self._browse_conv_folder).pack(side="left", padx=5, pady=6)
        ctk.CTkButton(conv_folder_frame, text="Open", width=60, fg_color="gray40", command=lambda: self._open_folder(self.conv_folder)).pack(side="left", padx=5, pady=6)

        # Status Label
        self.conv_status_label = ctk.CTkLabel(parent, text="Ready", text_color="gray60")
        self.conv_status_label.pack(pady=(8, 2))

        # Convert Action Button
        self.conv_btn = ctk.CTkButton(parent, text="Convert File", font=ctk.CTkFont(weight="bold"), height=38, command=self._start_conversion)
        self.conv_btn.pack(fill="x", padx=15, pady=10)

    def _toggle_conv_compress(self):
        active = self.conv_compress_var.get()
        self.conv_slider.configure(state="normal" if active else "disabled")
        self.conv_size_label.configure(text_color="white" if active else "gray50")

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

    def _browse_conv_folder(self):
        folder = filedialog.askdirectory(initialdir=self.conv_folder)
        if folder:
            self.conv_folder = folder
            self.conv_folder_label.configure(text=self._shorten_path(folder))

    def _start_conversion(self):
        if not self.selected_convert_file or not os.path.isfile(self.selected_convert_file):
            messagebox.showwarning("Warning", "Please select a file to convert.")
            return

        fmt = self.conv_target_format.get()
        if fmt in ["Select a file first", "Unsupported"]:
            messagebox.showwarning("Warning", "Please select a valid target format.")
            return

        self.conv_btn.configure(state="disabled", text="Converting...")
        self.conv_status_label.configure(text="Processing file...", text_color="gray70")

        threading.Thread(target=self._run_conversion, args=(self.selected_convert_file, self.conv_folder, fmt), daemon=True).start()

    def _run_conversion(self, input_path, out_dir, fmt):
        try:
            out_file = convert_media(input_path, out_dir, fmt)

            if detect_file_category(out_file) == 'video' and self.conv_compress_var.get():
                target_mb = float(self.conv_slider.get())
                self.after(0, lambda: self.conv_status_label.configure(text=f"Compressing under {int(target_mb)}MB..."))
                compress_video_to_size(out_file, target_mb=target_mb)

            self.after(0, lambda: self._on_conv_success(out_file))
        except Exception as e:
            self.after(0, lambda: self._on_conv_error(str(e)))

    def _on_conv_success(self, out_file):
        self.conv_btn.configure(state="normal", text="Convert File")
        self.conv_status_label.configure(text="Converted successfully!", text_color="#2ecc71")
        messagebox.showinfo("Success", f"File saved to:\n{out_file}")

    def _on_conv_error(self, err):
        self.conv_btn.configure(state="normal", text="Convert File")
        self.conv_status_label.configure(text="Conversion failed.", text_color="#e74c3c")
        messagebox.showerror("Conversion Error", err)


if __name__ == "__main__":
    app = MediaApp()
    app.mainloop()
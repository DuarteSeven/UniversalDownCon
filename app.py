import os
from statistics import mode
import threading
import subprocess
from tkinter import filedialog, messagebox
import customtkinter as ctk
import yt_dlp
import re
import requests

# Auto-configure FFmpeg binaries if installed via pip
try:
    import static_ffmpeg
    static_ffmpeg.add_paths()
except ImportError:
    pass

from converter import convert_media, detect_file_category

ctk.set_appearance_mode("Dark")
ctk.set_default_color_theme("blue")


class MediaApp(ctk.CTk):
    def __init__(self):
        super().__init__()

        self.title("Media Hub: Downloader & Converter")
        self.geometry("700x560")
        self.resizable(False, False)

        # Paths state
        self.dl_folder = os.path.expanduser("~/Downloads")
        self.conv_folder = os.path.expanduser("~/Downloads")
        self.selected_convert_file = ""

        # Tabview layout
        self.tabs = ctk.CTkTabview(self, width=670, height=530)
        self.tabs.pack(padx=15, pady=15, fill="both", expand=True)

        self.tab_dl = self.tabs.add("Downloader")
        self.tab_conv = self.tabs.add("Universal Converter")

        self._setup_downloader_tab()
        self._setup_converter_tab()

    def _open_folder(self, path):
        """Cross-platform helper to open the download folder directly."""
        if not os.path.exists(path):
            os.makedirs(path, exist_ok=True)
        if os.name == 'nt':
            os.startfile(path)
        else:
            subprocess.Popen(['open' if sys.platform == 'darwin' else 'xdg-open', path])

    def _shorten_path(self, path: str, max_chars=32):
        return ("..." + path[-max_chars:]) if len(path) > max_chars else path

    # ==========================================
    # TAB 1: DOWNLOADER
    # ==========================================
    def _setup_downloader_tab(self):
        parent = self.tab_dl

        ctk.CTkLabel(parent, text="Web Media Downloader", font=ctk.CTkFont(size=20, weight="bold")).pack(pady=(10, 2))
        ctk.CTkLabel(parent, text="YouTube, TikTok, Instagram, X (Twitter), etc.", text_color="gray70").pack(pady=(0, 15))

        # URL Box
        url_frame = ctk.CTkFrame(parent)
        url_frame.pack(fill="x", padx=15, pady=5)
        ctk.CTkLabel(url_frame, text="URL:", font=ctk.CTkFont(weight="bold")).pack(side="left", padx=10)
        self.dl_url_entry = ctk.CTkEntry(url_frame, placeholder_text="Paste video/audio URL here...")
        self.dl_url_entry.pack(side="left", fill="x", expand=True, padx=(0, 10), pady=10)

        # Format Options
        opt_frame = ctk.CTkFrame(parent)
        opt_frame.pack(fill="x", padx=15, pady=8)

        ctk.CTkLabel(opt_frame, text="Type:").grid(row=0, column=0, padx=10, pady=8, sticky="w")
        self.dl_type_menu = ctk.CTkOptionMenu(opt_frame, values=["Video", "Audio Only"], command=self._on_dl_type_change)
        self.dl_type_menu.grid(row=0, column=1, padx=10, pady=8)

        ctk.CTkLabel(opt_frame, text="Format:").grid(row=0, column=2, padx=10, pady=8, sticky="w")
        self.dl_format_menu = ctk.CTkOptionMenu(opt_frame, values=["mp4", "mkv", "webm"])
        self.dl_format_menu.grid(row=0, column=3, padx=10, pady=8)

        # Folder Selection
        folder_frame = ctk.CTkFrame(parent)
        folder_frame.pack(fill="x", padx=15, pady=5)
        ctk.CTkLabel(folder_frame, text="Save To:").pack(side="left", padx=10)
        self.dl_folder_label = ctk.CTkLabel(folder_frame, text=self._shorten_path(self.dl_folder), text_color="gray80")
        self.dl_folder_label.pack(side="left", fill="x", expand=True, padx=5)

        ctk.CTkButton(folder_frame, text="Browse", width=70, command=self._browse_dl_folder).pack(side="left", padx=5, pady=6)
        ctk.CTkButton(folder_frame, text="Open", width=60, fg_color="gray40", command=lambda: self._open_folder(self.dl_folder)).pack(side="left", padx=5, pady=6)

        # Status & Progress
        self.dl_status_label = ctk.CTkLabel(parent, text="Ready", text_color="gray60")
        self.dl_status_label.pack(pady=(15, 2))
        self.dl_progress = ctk.CTkProgressBar(parent)
        self.dl_progress.set(0)
        self.dl_progress.pack(fill="x", padx=15, pady=5)

        # Action Button
        self.dl_btn = ctk.CTkButton(parent, text="Download Media", font=ctk.CTkFont(weight="bold"), height=38, command=self._start_download)
        self.dl_btn.pack(fill="x", padx=15, pady=15)

    def _browse_dl_folder(self):
        folder = filedialog.askdirectory(initialdir=self.dl_folder)
        if folder:
            self.dl_folder = folder
            self.dl_folder_label.configure(text=self._shorten_path(folder))

    def _on_dl_type_change(self, choice):
        if choice == "Audio Only":
            self.dl_format_menu.configure(values=["mp3", "wav", "ogg", "flac", "m4a"])
            self.dl_format_menu.set("mp3")
        else:
            self.dl_format_menu.configure(values=["mp4", "mkv", "webm"])
            self.dl_format_menu.set("mp4")
            
    def _resolve_spotify_track(self, url: str) -> str:
        """
        Fetches the song title and artist name from a public Spotify track URL
        using Spotify's public embed/oEmbed API (requires NO credentials/API keys).
        Returns a yt-dlp search query like 'ytsearch1:Song Title Artist Name'.
        """
        # Clean tracking parameters like ?si=...
        clean_url = url.split("?")[0]
        
        try:
            # Use Spotify's official open oEmbed endpoint
            oembed_url = f"https://open.spotify.com/oembed?url={clean_url}"
            headers = {'User-Agent': 'Mozilla/5.0'}
            response = requests.get(oembed_url, headers=headers, timeout=10)
            
            if response.status_code == 200:
                data = response.json()
                title = data.get("title", "")
                if title:
                    # Spotify oEmbed title is typically formatted as "Song Title by Artist" or "Song Title"
                    return f"ytsearch1:{title} audio"
        except Exception:
            pass

        # Fallback: Scrape title tag directly from Spotify embed iframe
        try:
            match = re.search(r'track/([a-zA-Z0-9]+)', clean_url)
            if match:
                track_id = match.group(1)
                embed_url = f"https://open.spotify.com/embed/track/{track_id}"
                r = requests.get(embed_url, headers={'User-Agent': 'Mozilla/5.0'}, timeout=10)
                title_match = re.search(r'<title>(.*?)</title>', r.text)
                if title_match:
                    track_name = title_match.group(1).replace(" | Spotify", "").replace("Spotify - ", "").strip()
                    return f"ytsearch1:{track_name} audio"
        except Exception:
            pass

        raise RuntimeError("Could not retrieve track information from this Spotify link.")

    def _start_download(self):
        url = self.dl_url_entry.get().strip()
        if not url:
            messagebox.showwarning("Warning", "Please enter a valid URL.")
            return

        # Handle Spotify Links
        if "open.spotify.com/track" in url or "spotify.com/intl-" in url:
            self.dl_status_label.configure(text="Resolving Spotify track info...", text_color="gray70")
            try:
                url = self._resolve_spotify_track(url)
            except Exception as err:
                messagebox.showerror("Spotify Error", str(err))
                return

        # Handle YouTube Music / Playlist URL cleanup
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

        outtmpl = os.path.join(self.dl_folder, "%(title)s.%(ext)s")

        ydl_opts = {
            'outtmpl': outtmpl,
            'progress_hooks': [hook],
            'quiet': True,
            'noplaylist': True,
            'extract_flat': False,
        }

        if mode == "Audio Only":
            ydl_opts.update({
                'format': 'bestaudio/best',
                'writethumbnail': True,
                'postprocessors': [
                    # Converts YouTube's WebP thumbnail to JPEG
                    {
                        'key': 'FFmpegThumbnailsConvertor',
                        'format': 'jpg',
                    },
                    # Extracts audio to target format (e.g. mp3)
                    {
                        'key': 'FFmpegExtractAudio',
                        'preferredcodec': fmt,
                        'preferredquality': '192',
                    },
                    # Adds title, artist tags
                    {
                        'key': 'FFmpegMetadata',
                        'add_metadata': True,
                    },
                    # Embeds the JPG into the MP3/M4A/FLAC file
                    {
                        'key': 'EmbedThumbnail',
                        'already_have_thumbnail': False,
                    },
                ],
            })
        else:
            ydl_opts.update({
                'format': 'bestvideo*+bestaudio/best',
                'merge_output_format': fmt,
                'postprocessor_args': {'merger': ['-c:v', 'copy', '-c:a', 'aac', '-b:a', '192k']}
            })

        try:
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                ydl.download([url])
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

        ctk.CTkLabel(parent, text="Universal File Converter", font=ctk.CTkFont(size=20, weight="bold")).pack(pady=(10, 2))
        ctk.CTkLabel(parent, text="Convert local Images, Audio, or Videos into other formats", text_color="gray70").pack(pady=(0, 15))

        # File Select Box
        file_frame = ctk.CTkFrame(parent)
        file_frame.pack(fill="x", padx=15, pady=5)
        self.conv_file_label = ctk.CTkLabel(file_frame, text="No file selected", text_color="gray70")
        self.conv_file_label.pack(side="left", fill="x", expand=True, padx=10, pady=8)
        ctk.CTkButton(file_frame, text="Choose File", width=100, command=self._select_convert_file).pack(side="right", padx=10, pady=8)

        # Output Format Choice
        conv_opt_frame = ctk.CTkFrame(parent)
        conv_opt_frame.pack(fill="x", padx=15, pady=8)
        ctk.CTkLabel(conv_opt_frame, text="Target Format:").pack(side="left", padx=10, pady=10)
        self.conv_target_format = ctk.CTkOptionMenu(conv_opt_frame, values=["Select a file first"])
        self.conv_target_format.pack(side="left", padx=10, pady=10)

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
        self.conv_status_label.pack(pady=(20, 5))

        # Action Button
        self.conv_btn = ctk.CTkButton(parent, text="Convert File", font=ctk.CTkFont(weight="bold"), height=38, command=self._start_conversion)
        self.conv_btn.pack(fill="x", padx=15, pady=15)

    def _select_convert_file(self):
        file_path = filedialog.askopenfilename()
        if not file_path:
            return

        self.selected_convert_file = file_path
        self.conv_file_label.configure(text=os.path.basename(file_path), text_color="white")

        category = detect_file_category(file_path)
        if category == 'image':
            options = ["png", "jpg", "webp", "bmp", "ico", "tiff"]
        elif category == 'audio':
            options = ["mp3", "ogg", "wav", "flac", "m4a", "aac"]
        elif category == 'video':
            options = ["mp4", "webm", "mkv", "avi", "mov", "gif"]
        else:
            options = ["Unsupported"]

        self.conv_target_format.configure(values=options)
        self.conv_target_format.set(options[0])

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
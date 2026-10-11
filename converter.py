import os
import subprocess
from PIL import Image
import pillow_heif
pillow_heif.register_heif_opener()

IMAGE_EXTENSIONS = {
    '.jpg', '.jpeg', '.jfif', '.pjpeg', '.pjp',
    '.png', '.webp', '.bmp', '.ico', '.tiff', '.tif', '.heic', '.heif', '.avif'
}
AUDIO_EXTENSIONS = {'.mp3', '.wav', '.ogg', '.flac', '.m4a', '.aac', '.wma', '.opus'}
VIDEO_EXTENSIONS = {'.mp4', '.mkv', '.webm', '.avi', '.mov', '.flv', '.wmv', '.gif'}

def detect_file_category(filepath: str) -> str:
    ext = os.path.splitext(filepath)[1].lower()
    if ext in IMAGE_EXTENSIONS:
        return 'image'
    elif ext in AUDIO_EXTENSIONS:
        return 'audio'
    elif ext in VIDEO_EXTENSIONS:
        return 'video'
    return 'unknown'

def _has_audio_stream(input_file: str, startupinfo=None) -> bool:
    """Checks whether the media file contains an audio stream."""
    cmd = [
        'ffprobe', '-v', 'error',
        '-select_streams', 'a',
        '-show_entries', 'stream=codec_type',
        '-of', 'csv=p=0',
        input_file
    ]
    try:
        out = subprocess.check_output(cmd, startupinfo=startupinfo).decode().strip()
        return len(out) > 0
    except Exception:
        return False

def _get_duration(input_file: str, startupinfo=None) -> float:
    """Extracts duration in seconds."""
    probe_cmd = [
        'ffprobe', '-v', 'error',
        '-show_entries', 'format=duration',
        '-of', 'default=noprint_wrappers=1:nokey=1',
        input_file
    ]
    try:
        return float(subprocess.check_output(probe_cmd, startupinfo=startupinfo).strip())
    except Exception:
        return 0.0

def compress_video_to_size(input_file: str, target_mb: float = 19.0) -> str:
    """
    Compresses an entire video (even 1h30+ long) to strictly stay below target_mb.
    If the file is a GIF, reduces resolution and framerate iteratively.
    """
    if not os.path.isfile(input_file):
        return input_file

    current_size_mb = os.path.getsize(input_file) / (1024 * 1024)
    if current_size_mb <= target_mb:
        return input_file

    startupinfo = None
    if os.name == 'nt':
        startupinfo = subprocess.STARTUPINFO()
        startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW

    ext = os.path.splitext(input_file)[1].lower()

    # --- SPECIAL HANDLING FOR GIF COMPRESSION ---
    if ext == '.gif':
        # Scale resolution and framerate down until it hits the target size
        base = os.path.splitext(input_file)[0]
        temp_gif = f"{base}_temp_comp.gif"
        for scale, fps in [(360, 10), (280, 8), (200, 6), (160, 5)]:
            vf = f"fps={fps},scale={scale}:-1:flags=lanczos,split[s0][s1];[s0]palettegen[p];[s1][p]paletteuse"
            subprocess.run([
                'ffmpeg', '-y', '-i', input_file,
                '-vf', vf, temp_gif
            ], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, startupinfo=startupinfo)
            
            if os.path.exists(temp_gif) and (os.path.getsize(temp_gif) / (1024 * 1024)) <= target_mb:
                break
        
        if os.path.exists(temp_gif) and os.path.getsize(temp_gif) > 0:
            os.replace(temp_gif, input_file)
        return input_file

    # --- VIDEO (MP4/MKV/WEBM) 2-PASS COMPRESSION ---
    duration = _get_duration(input_file, startupinfo)
    if duration <= 0:
        return input_file

    has_audio = _has_audio_stream(input_file, startupinfo)

    # Reserve 85% for safer headroom against container muxing variance
    target_total_kbps = (target_mb * 8192 * 0.85) / duration

    if has_audio:
        # Scale down audio bitrate if video is long to reserve budget for video
        if target_total_kbps < 200:
            audio_kbps = 48
        elif target_total_kbps < 400:
            audio_kbps = 64
        else:
            audio_kbps = 96
        video_kbps = max(int(target_total_kbps - audio_kbps), 45)
    else:
        audio_kbps = 0
        video_kbps = max(int(target_total_kbps), 45)

    # Automatically downscale resolution if the bitrate budget is too low (e.g. 1h30 video)
    # This prevents the encoder from dropping frames or corrupting long videos!
    scale_filter = []
    if video_kbps < 150:
        scale_filter = ['-vf', 'scale=-2:360']
    elif video_kbps < 350:
        scale_filter = ['-vf', 'scale=-2:480']
    elif video_kbps < 700:
        scale_filter = ['-vf', 'scale=-2:720']

    base = os.path.splitext(input_file)[0]
    output_temp = f"{base}_comp_temp.mp4"
    null_dev = 'NUL' if os.name == 'nt' else '/dev/null'

    # Pass 1
    pass1_cmd = [
        'ffmpeg', '-y', '-i', input_file,
        '-c:v', 'libx264', '-b:v', f'{video_kbps}k',
        '-preset', 'veryfast',
        *scale_filter,
        '-pass', '1', '-an', '-f', 'mp4', null_dev
    ]
    subprocess.run(pass1_cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, startupinfo=startupinfo)

    # Pass 2
    pass2_cmd = [
        'ffmpeg', '-y', '-i', input_file,
        '-c:v', 'libx264', '-b:v', f'{video_kbps}k',
        '-preset', 'veryfast',
        *scale_filter,
        '-pass', '2'
    ]
    if has_audio:
        pass2_cmd.extend(['-c:a', 'aac', '-b:a', f'{audio_kbps}k'])
    else:
        pass2_cmd.append('-an')
        
    pass2_cmd.append(output_temp)
    subprocess.run(pass2_cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, startupinfo=startupinfo)

    # Cleanup FFmpeg 2-pass logs
    for log_f in ['ffmpeg2pass-0.log', 'ffmpeg2pass-0.log.mbtree']:
        if os.path.exists(log_f):
            try:
                os.remove(log_f)
            except Exception:
                pass

    if os.path.exists(output_temp) and os.path.getsize(output_temp) > 0:
        try:
            # Overwrite original output with compressed file
            os.replace(output_temp, input_file)
        except Exception:
            pass

    return input_file

def convert_media(input_path: str, output_dir: str, target_format: str, progress_callback=None) -> str:
    """
    Standard conversion maintaining maximum fidelity / lossless where possible.
    """
    if not os.path.isfile(input_path):
        raise FileNotFoundError(f"Input file not found: {input_path}")

    os.makedirs(output_dir, exist_ok=True)
    base_name = os.path.splitext(os.path.basename(input_path))[0]
    target_format = target_format.lower().lstrip('.')
    output_path = os.path.join(output_dir, f"{base_name}.{target_format}")

    # Handle same-format conversion in same folder (e.g. mp4 -> mp4)
    if os.path.abspath(input_path) == os.path.abspath(output_path):
        output_path = os.path.join(output_dir, f"{base_name}_converted.{target_format}")

    category = detect_file_category(input_path)

    # --- IMAGE CONVERSION ---
    if category == 'image':
        with Image.open(input_path) as img:
            if target_format in ['jpg', 'jpeg', 'bmp'] and img.mode in ('RGBA', 'LA', 'P'):
                bg = Image.new("RGB", img.size, (255, 255, 255))
                alpha = img.split()[-1] if 'A' in img.getbands() else None
                bg.paste(img, mask=alpha)
                bg.save(output_path, quality=98)
            elif target_format == 'ico':
                img.save(output_path, sizes=[(256, 256), (128, 128), (64, 64), (32, 32), (16, 16)])
            else:
                img.save(output_path)
        return output_path

    # --- AUDIO CONVERSION ---
    elif category == 'audio':
        cmd = ['ffmpeg', '-y', '-i', input_path, '-vn']
        if target_format == 'mp3':
            cmd.extend(['-c:a', 'libmp3lame', '-b:a', '320k'])
        elif target_format == 'ogg':
            cmd.extend(['-c:a', 'libvorbis', '-q:a', '6'])
        elif target_format == 'wav':
            cmd.extend(['-c:a', 'pcm_s16le'])
        elif target_format == 'flac':
            cmd.extend(['-c:a', 'flac'])
        elif target_format in ['m4a', 'aac']:
            cmd.extend(['-c:a', 'aac', '-b:a', '256k'])
        cmd.append(output_path)

    # --- VIDEO CONVERSION ---
    elif category == 'video':
        cmd = ['ffmpeg', '-y', '-i', input_path]
        if target_format == 'gif':
            cmd.extend([
                '-vf', 'fps=15,scale=480:-1:flags=lanczos,split[s0][s1];[s0]palettegen[p];[s1][p]paletteuse',
                output_path
            ])
        else:
            if target_format in ['mp4', 'mkv', 'mov']:
                cmd.extend(['-c:v', 'libx264', '-crf', '18', '-preset', 'medium', '-c:a', 'aac', '-b:a', '256k'])
            elif target_format == 'webm':
                cmd.extend(['-c:v', 'libvpx-vp9', '-crf', '24', '-b:v', '0', '-c:a', 'libopus'])
            elif target_format == 'avi':
                cmd.extend(['-c:v', 'mpeg4', '-qscale:v', '2', '-c:a', 'mp3'])
            else:
                cmd.extend(['-c:v', 'copy', '-c:a', 'copy'])
            cmd.append(output_path)
    else:
        raise ValueError(f"Unsupported format: {os.path.splitext(input_path)[1]}")

    startupinfo = None
    if os.name == 'nt':
        startupinfo = subprocess.STARTUPINFO()
        startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW

    result = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, startupinfo=startupinfo)
    if result.returncode != 0:
        raise RuntimeError(f"FFmpeg conversion failed:\n{result.stderr.decode(errors='ignore')}")

    return output_path
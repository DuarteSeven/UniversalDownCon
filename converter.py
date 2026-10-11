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
    """Verifica se o ficheiro contém um fluxo de áudio."""
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
    """Extrai a duração do vídeo em segundos."""
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

def compress_video_to_size(input_file: str, target_mb: float = 19.0, gif_mode: str = "quality") -> str:
    """
    Comprime vídeos ou GIFs para ficarem estritamente abaixo de target_mb.
    Para GIFs:
      - 'quality': Mantém FPS altos e fluidez, reduzindo resolução/cores.
      - 'frames': Mantém nitidez/resolução, reduzindo a taxa de fotogramas.
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

# --- COMPRESSÃO INTELIGENTE DE GIF ---
    if ext == '.gif':
        base = os.path.splitext(input_file)[0]
        temp_gif = f"{base}_temp_comp.gif"
        
        duration = _get_duration(input_file, startupinfo)
        if duration <= 0:
            duration = 10.0

        # Orçamento de bytes/s
        bytes_per_sec = (target_mb * 1024 * 1024 * 0.85) / duration

        if gif_mode == "frames":  # Prioritize Sharpness
            if bytes_per_sec > 1_500_000:
                steps = [(480, 16, 128), (420, 12, 96), (360, 10, 64)]
            elif bytes_per_sec > 700_000:
                steps = [(400, 12, 96), (320, 10, 64), (260, 8, 48)]
            else:
                steps = [(320, 10, 64), (240, 8, 48), (200, 6, 32), (160, 5, 24)]

        elif gif_mode == "smooth":  # Prioritize Smoothness
            if bytes_per_sec > 1_500_000:
                steps = [(380, 24, 128), (320, 22, 96), (260, 20, 64)]
            elif bytes_per_sec > 700_000:
                steps = [(300, 20, 96), (240, 18, 64), (200, 15, 48)]
            else:
                steps = [(220, 18, 64), (180, 15, 48), (140, 12, 32), (120, 10, 24)]

        else:  # "balanced" (Default inteligente)
            if bytes_per_sec > 1_500_000:
                steps = [(420, 20, 128), (360, 18, 96), (300, 15, 64)]
            elif bytes_per_sec > 700_000:
                steps = [(340, 16, 96), (280, 14, 64), (240, 12, 48)]
            else:
                steps = [(260, 12, 64), (200, 10, 48), (160, 8, 32), (140, 6, 24)]

        for width, fps, colors in steps:
            vf = (
                f"fps={fps},scale={width}:-1:flags=lanczos,"
                f"split[s0][s1];"
                f"[s0]palettegen=max_colors={colors}:stats_mode=diff[p];"
                f"[s1][p]paletteuse=dither=bayer:bayer_scale=3"
            )
            subprocess.run([
                'ffmpeg', '-y', '-i', input_file,
                '-vf', vf, temp_gif
            ], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, startupinfo=startupinfo)

            if os.path.exists(temp_gif) and (os.path.getsize(temp_gif) / (1024 * 1024)) <= target_mb:
                break

        # Garantia final para ficheiros extremos
        if os.path.exists(temp_gif) and (os.path.getsize(temp_gif) / (1024 * 1024)) > target_mb:
            vf = "fps=8,scale=160:-1:flags=lanczos,split[s0][s1];[s0]palettegen=max_colors=32[p];[s1][p]paletteuse"
            subprocess.run([
                'ffmpeg', '-y', '-i', input_file,
                '-vf', vf, temp_gif
            ], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, startupinfo=startupinfo)

        if os.path.exists(temp_gif) and os.path.getsize(temp_gif) > 0:
            os.replace(temp_gif, input_file)
        return input_file

    # --- COMPRESSÃO DE VÍDEO (2-PASS) ---
    duration = _get_duration(input_file, startupinfo)
    if duration <= 0:
        return input_file

    has_audio = _has_audio_stream(input_file, startupinfo)
    target_total_kbps = (target_mb * 8192 * 0.85) / duration

    if has_audio:
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

    for log_f in ['ffmpeg2pass-0.log', 'ffmpeg2pass-0.log.mbtree']:
        if os.path.exists(log_f):
            try:
                os.remove(log_f)
            except Exception:
                pass

    if os.path.exists(output_temp) and os.path.getsize(output_temp) > 0:
        try:
            os.replace(output_temp, input_file)
        except Exception:
            pass

    return input_file

def convert_media(input_path: str, output_dir: str, target_format: str, legacy_webm: bool = False, progress_callback=None) -> str:
    """
    Conversão com fidelidade máxima. Sem compressão ativa, preserva todos os fotogramas.
    """
    if not os.path.isfile(input_path):
        raise FileNotFoundError(f"Input file not found: {input_path}")

    os.makedirs(output_dir, exist_ok=True)
    base_name = os.path.splitext(os.path.basename(input_path))[0]
    target_format = target_format.lower().lstrip('.')
    output_path = os.path.join(output_dir, f"{base_name}.{target_format}")

    if os.path.abspath(input_path) == os.path.abspath(output_path):
        output_path = os.path.join(output_dir, f"{base_name}_converted.{target_format}")

    category = detect_file_category(input_path)

    # --- IMAGEM ---
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

    # --- ÁUDIO ---
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

    # --- VÍDEO & GIF ---
    elif category == 'video':
        cmd = ['ffmpeg', '-y', '-i', input_path]
        if target_format == 'gif':
            # Sem limitação de fps -> preserva todos os fotogramas originais para fluidez total
            cmd.extend([
                '-vf', 'split[s0][s1];[s0]palettegen=stats_mode=diff[p];[s1][p]paletteuse=dither=bayer:bayer_scale=3',
                output_path
            ])
        else:
            if target_format in ['mp4', 'mkv', 'mov']:
                cmd.extend(['-c:v', 'libx264', '-crf', '18', '-preset', 'medium', '-c:a', 'aac', '-b:a', '256k'])
            elif target_format == 'webm':
                if legacy_webm:
                    cmd.extend(['-c:v', 'libvpx', '-crf', '10', '-b:v', '2M', '-c:a', 'libvorbis', '-q:a', '4'])
                else:
                    cmd.extend(['-c:v', 'libvpx-vp9', '-crf', '24', '-b:v', '0', '-c:a', 'libopus'])
            elif target_format == 'avi':
                cmd.extend(['-c:v', 'mpeg4', '-qscale:v', '2', '-c:a', 'mp3'])
            else:
                cmd.extend(['-c:v', 'copy', '-c:a', 'copy'])
            cmd.append(output_path)
    else:
        raise ValueError(f"Formato não suportado: {os.path.splitext(input_path)[1]}")

    startupinfo = None
    if os.name == 'nt':
        startupinfo = subprocess.STARTUPINFO()
        startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW

    result = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, startupinfo=startupinfo)
    if result.returncode != 0:
        raise RuntimeError(f"FFmpeg conversion failed:\n{result.stderr.decode(errors='ignore')}")

    return output_path
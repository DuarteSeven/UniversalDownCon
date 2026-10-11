import os
import subprocess
from PIL import Image
import pillow_heif
pillow_heif.register_heif_opener()

# Updated IMAGE_EXTENSIONS in converter.py
IMAGE_EXTENSIONS = {
    '.jpg', '.jpeg', '.jfif', '.pjpeg', '.pjp',  # JPEG variants
    '.png', '.webp', '.bmp', '.ico', '.tiff', '.tif', '.gif', '.heic', '.heif', '.avif'}
AUDIO_EXTENSIONS = {'.mp3', '.wav', '.ogg', '.flac', '.m4a', '.aac', '.wma', '.opus'}
VIDEO_EXTENSIONS = {'.mp4', '.mkv', '.webm', '.avi', '.mov', '.flv', '.wmv'}

def detect_file_category(filepath: str) -> str:
    """Returns 'image', 'audio', 'video', or 'unknown' based on file extension."""
    ext = os.path.splitext(filepath)[1].lower()
    if ext in IMAGE_EXTENSIONS:
        return 'image'
    elif ext in AUDIO_EXTENSIONS:
        return 'audio'
    elif ext in VIDEO_EXTENSIONS:
        return 'video'
    return 'unknown'

def convert_media(input_path: str, output_dir: str, target_format: str, progress_callback=None) -> str:
    """
    Converts an image, audio, or video file into the target format.
    Saves the new file to output_dir and returns the output file path.
    """
    if not os.path.isfile(input_path):
        raise FileNotFoundError(f"Input file not found: {input_path}")

    os.makedirs(output_dir, exist_ok=True)
    base_name = os.path.splitext(os.path.basename(input_path))[0]
    target_format = target_format.lower().lstrip('.')
    output_path = os.path.join(output_dir, f"{base_name}.{target_format}")

    category = detect_file_category(input_path)

    # --- IMAGE CONVERSION (Using Pillow) ---
    if category == 'image':
        with Image.open(input_path) as img:
            # JPEG/BMP do not support RGBA transparency; flatten to RGB
            if target_format in ['jpg', 'jpeg', 'bmp'] and img.mode in ('RGBA', 'LA', 'P'):
                bg = Image.new("RGB", img.size, (255, 255, 255))
                alpha = img.split()[-1] if 'A' in img.getbands() else None
                bg.paste(img, mask=alpha)
                bg.save(output_path, quality=95)
            elif target_format == 'ico':
                img.save(output_path, sizes=[(256, 256), (128, 128), (64, 64), (32, 32), (16, 16)])
            else:
                img.save(output_path)
        return output_path

    # --- AUDIO CONVERSION (Using FFmpeg) ---
    elif category == 'audio':
        cmd = [
            'ffmpeg', '-y',
            '-i', input_path,
            '-vn',  # Drop any embedded album covers
        ]
        if target_format == 'mp3':
            cmd.extend(['-c:a', 'libmp3lame', '-q:a', '2'])
        elif target_format == 'ogg':
            cmd.extend(['-c:a', 'libvorbis', '-q:a', '4'])
        elif target_format == 'wav':
            cmd.extend(['-c:a', 'pcm_s16le'])
        elif target_format == 'flac':
            cmd.extend(['-c:a', 'flac'])
        elif target_format in ['m4a', 'aac']:
            cmd.extend(['-c:a', 'aac', '-b:a', '192k'])

        cmd.append(output_path)

    # --- VIDEO CONVERSION (Using FFmpeg) ---
    elif category == 'video':
        cmd = [
            'ffmpeg', '-y',
            '-i', input_path,
        ]
        if target_format == 'mp4':
            cmd.extend(['-c:v', 'libx264', '-crf', '22', '-preset', 'fast', '-c:a', 'aac', '-b:a', '192k'])
        elif target_format == 'webm':
            cmd.extend(['-c:v', 'libvpx-vp9', '-crf', '30', '-b:v', '0', '-c:a', 'libopus'])
        elif target_format == 'mkv':
            cmd.extend(['-c:v', 'copy', '-c:a', 'copy'])
        elif target_format == 'avi':
            cmd.extend(['-c:v', 'mpeg4', '-qscale:v', '3', '-c:a', 'mp3'])
        elif target_format == 'gif':
            # Create smooth GIFs using a palette filter
            cmd = [
                'ffmpeg', '-y', '-i', input_path,
                '-vf', 'fps=12,scale=480:-1:flags=lanczos,split[s0][s1];[s0]palettegen[p];[s1][p]paletteuse',
                output_path
            ]
        else:
            cmd.append(output_path)

        if target_format != 'gif':
            cmd.append(output_path)

    else:
        raise ValueError(f"Unsupported file format: {os.path.splitext(input_path)[1]}")

    # Run FFmpeg process hidden without console windows on Windows
    startupinfo = None
    if os.name == 'nt':
        startupinfo = subprocess.STARTUPINFO()
        startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW

    result = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, startupinfo=startupinfo)
    if result.returncode != 0:
        raise RuntimeError(f"FFmpeg conversion failed:\n{result.stderr.decode(errors='ignore')}")

    return output_path
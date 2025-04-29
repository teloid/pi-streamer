# config.py
import os
import sys
import logging

# --- Basic App Configuration ---
APP_DIR = os.path.dirname(os.path.abspath(__file__))
SECRET_KEY = os.urandom(24) # IMPORTANT: Generate a real secret key for production
PASSWORD_HASH = "pbkdf2:sha256:1000000$QEcVPMpd6XB5QzRY$5078abcb1f23f1b1c424d39796e500ce3c3a20e7017b84603b03e89d6ea16cb8" # Replace with a real hash generated using auth.py's helper

# --- Media & File Configuration ---
MEDIA_DIR_BASE = os.path.abspath(os.path.join(APP_DIR, 'media')) # Renamed from videos
CHUNK_SIZE = 1024 * 1024  # 1 MB
FILESYSTEM_ENCODING = sys.getfilesystemencoding() or 'utf-8'

# --- File Type Recognition ---
# Keep original video/audio lists for playlist/playall compatibility for now
VIDEO_EXTENSIONS = ('.mp4', '.mkv', '.avi', '.mov', '.webm')
AUDIO_EXTENSIONS = ('.mp3', '.ogg', '.wav', '.flac', '.m4a', '.aac', '.opus')
# Add image and text types
IMAGE_EXTENSIONS = ('.jpg', '.jpeg', '.png', '.gif', '.bmp', '.webp', '.svg')
TEXT_EXTENSIONS = ('.txt', '.md', '.log', '.py', '.js', '.css', '.html', '.sh', '.json', '.xml', '.yaml', '.csv') # Add more as needed

# Map extensions to types for easier classification
EXTENSION_TYPE_MAP = {ext: 'video' for ext in VIDEO_EXTENSIONS}
EXTENSION_TYPE_MAP.update({ext: 'audio' for ext in AUDIO_EXTENSIONS})
EXTENSION_TYPE_MAP.update({ext: 'image' for ext in IMAGE_EXTENSIONS})
EXTENSION_TYPE_MAP.update({ext: 'text' for ext in TEXT_EXTENSIONS})

# --- Video Quality Suffixes (Keep if needed for video player) ---
QUALITY_SUFFIXES = {'_1080p': '1080p', '_720p': '720p', '_480p': '480p', '_360p': '360p'}

# --- Logging Configuration ---
LOG_LEVEL = logging.DEBUG # Change to INFO for production

# --- Sanity Checks ---
if not MEDIA_DIR_BASE or not os.path.isdir(MEDIA_DIR_BASE):
    logging.critical(f"CRITICAL: Base media directory not found or not set: {MEDIA_DIR_BASE}")
    # In a real app, you might want to create it or exit more gracefully
    try:
        os.makedirs(MEDIA_DIR_BASE, exist_ok=True)
        logging.warning(f"Created missing media directory: {MEDIA_DIR_BASE}")
    except OSError as e:
        logging.critical(f"Failed to create media directory {MEDIA_DIR_BASE}: {e}")
        sys.exit(1)

if 'UTF-8' not in FILESYSTEM_ENCODING.upper():
    logging.critical("!!! WARNING: System locale is NOT UTF-8 (%s). Non-ASCII filenames may cause errors. Configure locale to UTF-8 and reboot. !!!", FILESYSTEM_ENCODING)

# Placeholder check for password hash - REMOVE THIS LINE after setting a real hash
if PASSWORD_HASH.startswith("pbkdf2:sha256:..."):
     logging.warning("!!! SECURITY WARNING: Default password hash detected. Please generate and set a real password hash in config.py !!!")
     # You might want to exit here in production if no password is set.
     # sys.exit("Please set a password hash in config.py")
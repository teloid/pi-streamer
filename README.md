# Pi Streamer: Your Personal Media Hub

Pi Streamer transforms your server (like a Raspberry Pi, but any computer running Python works!) into a personal, web-accessible hub for your media files and documents. It provides a simple and clean interface to:

*   **Browse:** Easily navigate folders containing your videos, music, photos, and documents directly through your web browser.
*   **Stream:** Play video and audio files instantly without needing to download them first. Ideal for accessing your media library from different devices on your local network.
*   **View:** Quickly preview images (individually or in a grid) and read text files directly in the browser using convenient pop-up modals.
*   **Manage:** Perform basic file management tasks like uploading new files (including multiple at once), creating folders, and deleting unwanted items (files or entire folders).
*   **Access:** Keep your files private with simple password protection for the main interface.
*   **Share (Locally):** Generate `.m3u` playlists for media folders or use the (optional) public download links to easily grab files onto other devices.

Think of it as a lightweight, self-hosted alternative to cloud storage or complex media servers, focused on straightforward access and playback of your own files from your own hardware.

## Key Features

*   **Web-Based File Browser:** Clean interface for directory navigation.
*   **Media Streaming:**
    *   Video playback via Video.js (supports quality selection if files prepared).
    *   Audio playback via HTML5 audio player.
    *   Auto-advance to the next track/video within players.
*   **Image Viewing:**
    *   Optimized grid view for image-only folders.
    *   Modal pop-up viewer with Prev/Next navigation.
*   **Text File Viewing:** Modal pop-up for `.txt`, `.log`, `.md`, etc.
*   **File Management:** Multi-file upload, folder creation, file/folder deletion (with confirmation).
*   **Download Options:** Individual file downloads (publicly accessible by default) and M3U playlist generation (containing download links).
*   **Password Protection:** Secures access to the main browser interface.
*   **Responsive (Basic):** Functional on desktop and mobile browsers.

## Project Structure

pi-streamer/
├── app.py # Main Flask app setup, routes
├── auth.py # Authentication logic & password helper
├── config.py # Configuration (paths, password hash, etc.)
├── file_utils.py # File system interaction, path safety, item listing
├── templates/
│ ├── _base.html # Base layout template
│ ├── browse.html # Main file browser
│ ├── login.html # Login page (optional, if not using string in auth.py)
│ ├── player_audio.html # Simple audio player
│ ├── player_video.html # Video.js video player
│ └── welcome.html # Initial welcome/entry page
├── static/
│ └── style.css # Main CSS styles (includes themes)
│ └── browse.js # JavaScript for Text Modal (optional, can be inline)
└── README.md # This file

## Setup & Running

**(Keep the existing Setup & Running steps 1-10 as they were)**
*   1. Prerequisites
*   2. Clone Repository
*   3. Create & Activate Virtual Environment
*   4. Install Dependencies
*   5. Configure `config.py` (Especially `MEDIA_DIR_BASE`)
*   6. Generate Password Hash
*   7. Update `config.py` with Hash
*   8. Ensure Media Directory Permissions
*   9. Run the Application (Development)
*   10. Access

## Setting the Password

**(Keep the existing Setting the Password steps as they were)**
*   1. Activate venv
*   2. Choose password
*   3. Run `python -c "import auth; auth.print_hash(...)"`
*   4. Copy hash
*   5. Paste into `config.py` `PASSWORD_HASH`
*   6. Save `config.py`
*   7. Restart app

## Production Deployment

The Flask development server (`flask run`) is **not suitable for production**. Use a production-ready WSGI server like Gunicorn or Waitress, often behind a reverse proxy like Nginx or Apache.

**Example using Gunicorn:**

```bash
# Install Gunicorn
pip install gunicorn

# Run (adjust workers as needed)
gunicorn --workers 4 --bind 0.0.0.0:5000 app:application
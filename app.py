#!/usr/bin/python3
# -*- coding: utf-8 -*-

# --- Imports ---
import os
import re
import mimetypes
import logging
from datetime import timedelta
import datetime # Needed for context processor
import shutil

from flask import (
    Flask, Response, request, render_template, abort,
    send_from_directory, url_for, jsonify, redirect, make_response,
    flash, session, send_file
)
from werkzeug.utils import secure_filename
from urllib.parse import quote, unquote, urljoin

# --- Local Imports ---
import config  # Import configuration
import auth    # Import authentication logic
import file_utils # Import file system utilities
import sys

# --- Flask App Initialization & Configuration ---
app = Flask(__name__, template_folder='templates', static_folder='static')
app.config['SECRET_KEY'] = config.SECRET_KEY
app.config['PERMANENT_SESSION_LIFETIME'] = timedelta(days=30)

# --- Logging Setup ---
logging.basicConfig(level=config.LOG_LEVEL, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
app.logger.setLevel(config.LOG_LEVEL)
if __name__ != '__main__':
    gunicorn_logger = logging.getLogger('gunicorn.error')
    app.logger.handlers.extend(gunicorn_logger.handlers)

app.logger.info(f"Flask App Initialized. App Dir: {config.APP_DIR}, Media Dir: {config.MEDIA_DIR_BASE}")
app.logger.info(f"File System Encoding: {config.FILESYSTEM_ENCODING}")
if config.PASSWORD_HASH.startswith("pbkdf2:sha256:..."):
    app.logger.critical("!!! SECURITY WARNING: Default password hash detected. App is insecure. Generate and set a real hash in config.py !!!")

# --- Context Processor to Inject Variables into Templates ---
@app.context_processor
def inject_now():
    """Makes the current timezone-aware UTC datetime available to all templates."""
    return {'now': datetime.datetime.now(datetime.timezone.utc)}

# --- Helper Function ---
def get_relative_path_from_request(path_arg=""):
    """Safely decodes and normalizes the path from the request argument."""
    try:
        if path_arg is None: path_arg = ""
        # Decode URL encoding (e.g., %2F -> /, %20 -> space)
        decoded_path = unquote(str(path_arg)) # Ensure it's a string
        # Normalize (e.g., A//B -> A/B, A/. -> A) and remove leading/trailing slashes/backslashes
        normalized = os.path.normpath(decoded_path).strip('/\\')
        # Ensure consistent forward slashes for internal use and url_for generation
        result = normalized.replace("\\", "/")
        # Return empty string if path is just '.' after normalization or empty
        return "" if result == "." else result
    except Exception as e:
        app.logger.error(f"Error decoding/normalizing path argument '{path_arg}': {e}", exc_info=True)
        return "" # Return empty string on error

# --- Core Routes ---

@app.route('/')
def welcome():
    """Shows the initial welcome page (no auth required)."""
    if 'logged_in' in session: return redirect(url_for('browse'))
    return render_template('welcome.html')

@app.route('/login', methods=['GET', 'POST'])
def login():
    """Handles the login process using logic from auth.py."""
    if 'logged_in' in session: return redirect(url_for('browse'))
    return auth.handle_login_request() # Returns Response

@app.route('/logout')
def logout():
    """Logs the user out."""
    return auth.handle_logout() # Returns Response

# --- Main Browser Route ---
@app.route('/browse/', defaults={'subpath': ''})
@app.route('/browse/<path:subpath>')
@auth.login_required
def browse(subpath):
    """Lists contents of a directory. Handles all file types."""
    current_path = get_relative_path_from_request(subpath) # Use consistent name
    app.logger.info(f"Request browse: Current Path = '{current_path}'")

    target_dir_abs = file_utils.get_safe_fullpath(current_path)
    if target_dir_abs is None or not os.path.isdir(target_dir_abs):
         app.logger.error(f"Browse: Invalid or non-existent directory requested: '{current_path}'")
         flash(f"Error: Directory not found or access denied: '{current_path or '/'}'", "error")
         parent_of_invalid = os.path.dirname(current_path) if current_path else ''
         return redirect(url_for('browse', subpath=parent_of_invalid))

    all_items, is_image_only_folder = file_utils.get_folder_contents_with_ids(current_path)

    # Prepare breadcrumbs
    breadcrumbs = [{'name': 'Home', 'url': url_for('browse')}]
    current_folder_name = 'Home'
    if current_path:
        parts = current_path.strip('/').split('/')
        path_accum = ''
        for i, part in enumerate(parts):
            # Ensure part is decoded for display if needed (should be via get_relative...)
            display_part = part
            # Accumulate path using forward slashes for url_for
            path_accum = f"{path_accum}{part}" if not path_accum else f"{path_accum}/{part}"
            breadcrumb_url = url_for('browse', subpath=path_accum)
            is_last = (i == len(parts) - 1)
            breadcrumbs.append({'name': display_part, 'url': None if is_last else breadcrumb_url})
            if is_last: current_folder_name = display_part

    # Parent directory link
    parent_dir_path = os.path.dirname(current_path).replace("\\", "/") if current_path else ''
    # Handle case where parent is root '.' -> becomes ''
    if parent_dir_path == ".": parent_dir_path = ""
    up_link_url = url_for('browse', subpath=parent_dir_path) if current_path else None

    # Check for playable media for playlist download button
    has_media = any(item['type'] in ('video', 'audio') for item in all_items)
    # Removed Play All link
    download_playlist_link = url_for('download_playlist', subpath=current_path) if has_media else None

    return render_template(
        'browse.html',
        items=all_items,
        current_path=current_path, # Pass the cleaned current path
        current_folder_name=current_folder_name,
        breadcrumbs=breadcrumbs,
        up_link_url=up_link_url,
        is_image_only_folder=is_image_only_folder,
        download_playlist_link=download_playlist_link, # Keep playlist download
        video_ext=config.VIDEO_EXTENSIONS,
        audio_ext=config.AUDIO_EXTENSIONS
    )

# --- File Action Routes ---

# Common logic for finding file path and validating
def get_validated_item_paths(parent_path_from_url, item_id):
    cleaned_parent_path = get_relative_path_from_request(parent_path_from_url)
    app.logger.debug(f"Action Request: URL Parent='{parent_path_from_url}', Item='{item_id}'. Clean Parent='{cleaned_parent_path}'")

    item_full_relative_path = file_utils.find_path_by_id(cleaned_parent_path, item_id)
    if item_full_relative_path is None:
        app.logger.error(f"Action failed: Could not find ID '{item_id}' in parent '{cleaned_parent_path}'")
        abort(404, description="Item ID not found in the specified path.")

    target_file_abs = file_utils.get_safe_fullpath(item_full_relative_path)
    if target_file_abs is None or not os.path.isfile(target_file_abs):
        app.logger.error(f"Action failed: Path unsafe or not file. Relative='{item_full_relative_path}', Absolute='{target_file_abs}'")
        abort(404, description="File not found or access denied.")

    return cleaned_parent_path, item_full_relative_path, target_file_abs


@app.route('/download/<item_id>', defaults={'parent_path_in_url': ''})
@app.route('/download/<path:parent_path_in_url>/<item_id>')
# @auth.login_required <--- REMOVE THIS LINE
def download_file(parent_path_in_url, item_id):
    """Provides a file for download by its ID. (Now public)"""
    # ... rest of function remains the same ...
    cleaned_parent_path, item_full_relative_path, target_file_abs = get_validated_item_paths(parent_path_in_url, item_id)
    try:
        filename = os.path.basename(item_full_relative_path)
        # Add cache control headers if desired for public downloads
        response = make_response(send_file(target_file_abs, as_attachment=True, download_name=filename))
        # response.headers['Cache-Control'] = 'public, max-age=3600' # Example: cache for 1 hour
        return response
    except Exception as e:
        app.logger.error(f"Error sending file for download '{target_file_abs}': {e}", exc_info=True)
        abort(500, description="Server error during file download.")


@app.route('/view_text/<item_id>', defaults={'parent_path_in_url': ''})
@app.route('/view_text/<path:parent_path_in_url>/<item_id>')
@auth.login_required
def view_text_content(parent_path_in_url, item_id):
    """Returns the content of a text file as JSON."""
    _, item_full_relative_path, target_file_abs = get_validated_item_paths(parent_path_in_url, item_id)

    if file_utils.get_file_type(item_full_relative_path) != 'text':
        return jsonify({"error": "File is not a text file."}), 400

    content, error_message = file_utils.read_text_file_safe(target_file_abs)

    if error_message:
        return jsonify({"error": error_message}), 400
    else:
        return jsonify({"content": content, "filename": os.path.basename(item_full_relative_path)})


@app.route('/view_image/<item_id>', defaults={'parent_path_in_url': ''})
@app.route('/view_image/<path:parent_path_in_url>/<item_id>')
@auth.login_required
def view_image_file(parent_path_in_url, item_id):
    """Serves an image file directly for display."""
    _, item_full_relative_path, target_file_abs = get_validated_item_paths(parent_path_in_url, item_id)

    if file_utils.get_file_type(item_full_relative_path) != 'image':
         app.logger.warning(f"Image view denied: Item ID '{item_id}' ({item_full_relative_path}) is not an image.")
         abort(400, description="Requested item is not an image file.")

    try:
        return send_file(target_file_abs, as_attachment=False)
    except Exception as e:
        app.logger.error(f"Error sending image file '{target_file_abs}': {e}", exc_info=True)
        abort(500)


# --- Media Streaming & Playback ---

@app.route('/stream/<item_id>', defaults={'parent_path_in_url': ''})
@app.route('/stream/<path:parent_path_in_url>/<item_id>', methods=['GET', 'HEAD'])
@auth.login_required
def stream_media_by_id(parent_path_in_url, item_id):
    """Streams media (video/audio) looked up by ID, handles range requests."""
    _, item_full_relative_path, target_file_abs = get_validated_item_paths(parent_path_in_url, item_id)
    app.logger.debug(f"Stream: Attempting to serve absolute path '{target_file_abs}' for relative '{item_full_relative_path}'")

    range_header = request.headers.get('Range', None)
    size = os.path.getsize(target_file_abs)
    byte1, byte2 = 0, None

    if range_header:
        match = re.match(r'bytes=(\d+)-(\d*)', range_header)
        if match:
            try:
                byte1 = int(match.group(1))
                rg2 = match.group(2)
                if rg2: byte2 = int(rg2)
            except ValueError: abort(400, description="Invalid Range header.")
        else: abort(400, description="Malformed Range header.")

    if byte2 is None or byte2 >= size: byte2 = size - 1
    if byte1 < 0 or byte1 >= size or byte1 > byte2:
        resp = Response("Range Not Satisfiable", 416, headers={'Content-Range': f'bytes */{size}'})
        app.logger.warning(f"Range Not Satisfiable: {range_header!r}, size: {size}, requested: {byte1}-{byte2}")
        return resp

    length = byte2 - byte1 + 1
    app.logger.debug(f"Streaming '{item_full_relative_path}': Range {byte1}-{byte2}/{size} (Length: {length})")

    def generate_chunks():
        try:
            with open(target_file_abs, 'rb') as f:
                f.seek(byte1)
                remaining = length
                while remaining > 0:
                    read_size = min(config.CHUNK_SIZE, remaining)
                    chunk = f.read(read_size)
                    if not chunk:
                        app.logger.warning(f"Stream EOF reached unexpectedly for {item_full_relative_path!r} at offset {f.tell()}. Expected {remaining} more bytes.")
                        break
                    yield chunk
                    remaining -= len(chunk)
            # app.logger.debug(f"Stream generator finished for range {byte1}-{byte2} of {item_full_relative_path!r}")
        except IOError as e: app.logger.error(f"IOError during streaming chunk generation for {item_full_relative_path!r}: {e}")
        except Exception as e_gen: app.logger.error(f"Unexpected error in stream generator for {item_full_relative_path!r}: {e_gen}", exc_info=True)

    mime_type, _ = mimetypes.guess_type(target_file_abs)
    if not mime_type:
        file_type = file_utils.get_file_type(item_full_relative_path)
        mime_type = 'video/mp4' if file_type == 'video' else 'audio/mpeg' if file_type == 'audio' else 'application/octet-stream'

    rv = Response(generate_chunks(), 206, mimetype=mime_type, direct_passthrough=True)
    rv.headers.set('Content-Range', f'bytes {byte1}-{byte2}/{size}')
    rv.headers.set('Accept-Ranges', 'bytes')
    rv.headers.set('Content-Length', str(length))
    return rv


@app.route('/play_video/<item_id>', defaults={'parent_path_in_url': ''})
@app.route('/play_video/<path:parent_path_in_url>/<item_id>')
@auth.login_required
def play_video_page(parent_path_in_url, item_id):
    """Renders the video player page."""
    cleaned_parent_path, item_full_relative_path, target_file_abs = get_validated_item_paths(parent_path_in_url, item_id)

    if file_utils.get_file_type(item_full_relative_path) != 'video':
        flash(f"Item '{os.path.basename(item_full_relative_path)}' is not a video file.", "warning")
        return redirect(url_for('browse', subpath=cleaned_parent_path))

    quality_options = file_utils.get_quality_options(item_full_relative_path)
    if not quality_options:
         quality_options = [{'label': 'Original', 'path': item_full_relative_path, 'id': item_id}]

    previous_id, next_id = file_utils.find_prev_next_ids(item_full_relative_path, item_type_filter='video')
    prev_link_url = url_for('play_video_page', parent_path_in_url=cleaned_parent_path, item_id=previous_id) if previous_id else None
    next_link_url = url_for('play_video_page', parent_path_in_url=cleaned_parent_path, item_id=next_id) if next_id else None
    back_link_url = url_for('browse', subpath=cleaned_parent_path)
    initial_stream_url = url_for('stream_media_by_id', parent_path_in_url=cleaned_parent_path, item_id=item_id)

    display_filename = os.path.basename(item_full_relative_path)
    is_problematic_filename = None # Basic non-ascii check for problematic flag

    mime_type, _ = mimetypes.guess_type(target_file_abs)
    if not mime_type or mime_type == 'video/x-matroska': # Treat MKV as video/mp4 for broader compatibility if needed, or handle properly
        mime_type = 'video/mp4' # Or keep 'video/x-matroska' if clients support it

    return render_template(
        'player_video.html',
        display_filename=display_filename,
        back_link_url=back_link_url,
        quality_options=quality_options,
        is_problematic_filename=is_problematic_filename,
        prev_link_url=prev_link_url,
        next_link_url=next_link_url,
        initial_stream_url=initial_stream_url,
        initial_mime_type=mime_type,
        parent_path_json=cleaned_parent_path # Pass cleaned parent for JS quality switcher
    )


@app.route('/play_audio/<item_id>', defaults={'parent_path_in_url': ''})
@app.route('/play_audio/<path:parent_path_in_url>/<item_id>')
@auth.login_required
def play_audio_page(parent_path_in_url, item_id):
    """Renders the simplified audio player page."""
    cleaned_parent_path, item_full_relative_path, target_file_abs = get_validated_item_paths(parent_path_in_url, item_id)

    if file_utils.get_file_type(item_full_relative_path) != 'audio':
        flash(f"Item '{os.path.basename(item_full_relative_path)}' is not an audio file.", "warning")
        return redirect(url_for('browse', subpath=cleaned_parent_path))

    previous_id, next_id = file_utils.find_prev_next_ids(item_full_relative_path, item_type_filter='audio')
    prev_link_url = url_for('play_audio_page', parent_path_in_url=cleaned_parent_path, item_id=previous_id) if previous_id else None
    next_link_url = url_for('play_audio_page', parent_path_in_url=cleaned_parent_path, item_id=next_id) if next_id else None
    back_link_url = url_for('browse', subpath=cleaned_parent_path)
    stream_url = url_for('stream_media_by_id', parent_path_in_url=cleaned_parent_path, item_id=item_id)

    display_filename = os.path.basename(item_full_relative_path)
    is_problematic_filename = any(c > '\x7f' for c in display_filename)

    mime_type, _ = mimetypes.guess_type(target_file_abs)
    if not mime_type or not mime_type.startswith('audio/'):
        mime_type = 'audio/mpeg' # Default audio mime type

    return render_template(
        'player_audio.html',
        display_filename=display_filename,
        back_link_url=back_link_url,
        stream_url=stream_url,
        mime_type=mime_type,
        is_problematic_filename=is_problematic_filename,
        prev_link_url=prev_link_url,
        next_link_url=next_link_url
    )


# --- Playlist Download ---
@app.route('/download_playlist/', defaults={'subpath': ''})
@app.route('/download_playlist/<path:subpath>')
@auth.login_required # Keep login required for generating playlist? Optional.
def download_playlist(subpath):
    """Generates an M3U playlist file for download containing DIRECT DOWNLOAD links."""
    current_path = get_relative_path_from_request(subpath)
    app.logger.info(f"Request M3U playlist (Download Links) for: '{current_path}'")

    target_dir_abs = file_utils.get_safe_fullpath(current_path)
    if target_dir_abs is None or not os.path.isdir(target_dir_abs):
        flash(f"Cannot generate playlist: Directory not found: '{current_path or '/'}'", "error")
        return redirect(url_for('browse', subpath=current_path))

    all_items, _ = file_utils.get_folder_contents_with_ids(current_path)
    media_items = [item for item in all_items if item['type'] in ('video', 'audio')]

    if not media_items:
        flash("No playable media found in this folder for playlist.", "info")
        return redirect(url_for('browse', subpath=current_path))

    folder_name_base = os.path.basename(current_path) if current_path else "media_root"
    # Add suffix to filename to indicate download links
    playlist_filename_base = secure_filename(f"{folder_name_base}") or "playlist"
    playlist_filename = f"{playlist_filename_base}.m3u"
    app.logger.debug(f"Generated download playlist filename: {playlist_filename}")

    m3u_content = ["#EXTM3U"]
    for item in media_items:
        # Use display name, replace characters potentially problematic in EXTINF
        display_name_for_m3u = item['display_name'].replace(',', ';').replace('\n', ' ').replace('\r', '')
        # --- CHANGE HERE: Use url_for for the download endpoint ---
        # Generate absolute download URL using the item ID and the *current path* as parent context
        # Ensure 'download_file' route accepts 'parent_path_in_url'
        download_url = url_for('download_file',
                             parent_path_in_url=current_path, # Use current path as parent context
                             item_id=item['id'],
                             _external=True) # Generate full absolute URL
        # --- END CHANGE ---

        m3u_content.append(f"#EXTINF:-1,{display_name_for_m3u}")
        m3u_content.append(download_url) # Add the download URL

    m3u_data = "\n".join(m3u_content) + "\n"
    response = make_response(m3u_data.encode('utf-8'))
    response.headers["Content-Type"] = "audio/x-mpegurl; charset=utf-8"
    try:
        # Use RFC 5987 encoding for filename in Content-Disposition
        encoded_filename = quote(playlist_filename, safe="")
        response.headers["Content-Disposition"] = f"attachment; filename*=UTF-8''{encoded_filename}"
    except Exception:
        ascii_filename = playlist_filename.encode('ascii', 'ignore').decode('ascii') or "playlist.m3u"
        response.headers["Content-Disposition"] = f"attachment; filename=\"{ascii_filename}\"" # Fallback

    app.logger.info(f"Generated M3U playlist with download links: {playlist_filename}")
    return response


# --- File Upload & Folder Creation ---

@app.route('/upload/', defaults={'subpath': ''}, methods=['POST'])
@app.route('/upload/<path:subpath>', methods=['POST'])
@auth.login_required
def upload_file_handler(subpath):
    target_folder_path = get_relative_path_from_request(subpath)
    app.logger.debug(f"Upload Handler: Target Folder = '{target_folder_path}'")
    target_dir_abs = file_utils.get_safe_fullpath(target_folder_path)

    if target_dir_abs is None or not os.path.isdir(target_dir_abs):
        # ... (error handling) ...
        return jsonify({"success": False, "error": f"Target directory '{target_folder_path or '/'}' not found or invalid."}), 400

    if 'file' not in request.files: # ... (error handling) ...
        return jsonify({"success": False, "error": "No 'file' part in the request."}), 400

    file = request.files['file'] # This is a FileStorage object
    if not file or not file.filename: # ... (error handling) ...
        return jsonify({"success": False, "error": "No file selected or filename is empty."}), 400

    # --- Filename Handling (Keep previous cleaning logic) ---
    original_filename = file.filename
    # ... (keep cleaning logic for final_filename) ...
    cleaned_filename = original_filename.strip('. ')
    if os.path.sep != '/': cleaned_filename = cleaned_filename.replace(os.path.sep, '_')
    cleaned_filename = cleaned_filename.replace('/', '_')
    if ".." in cleaned_filename:
         return jsonify({"success": False, "error": f"Filename '{original_filename}' contains invalid components ('..')."}), 400
    if not cleaned_filename:
         return jsonify({"success": False, "error": f"Filename '{original_filename}' is invalid."}), 400
    final_filename = cleaned_filename
    app.logger.info(f"Using final filename for saving: '{final_filename}' (Original: '{original_filename}')")
    # --- End Filename Handling ---

    destination_abs_str = os.path.join(target_dir_abs, final_filename) # Get destination as string
    app.logger.debug(f"Calculated destination absolute path string: '{destination_abs_str}'")

    # Check existence using the string path
    if os.path.exists(destination_abs_str):
        app.logger.warning(f"Upload skipped: File '{final_filename}' already exists.")
        return jsonify({"success": False, "error": f"File '{final_filename}' already exists."}), 409

    # --- Attempt to save MANUALLY ---
    try:
        # --- MODIFICATION START ---
        # Manually open the destination file in binary write mode ('wb')
        # Python's open() SHOULD handle the Unicode string path correctly on modern OS/filesystems
        app.logger.debug(f"Attempting to open destination '{destination_abs_str}' in 'wb' mode.")
        with open(destination_abs_str, "wb") as f_dst:
            # Copy the content from the uploaded file's stream to the destination file
            # file.stream provides the incoming data stream
            shutil.copyfileobj(file.stream, f_dst)
        # --- MODIFICATION END ---

        app.logger.info(f"File '{final_filename}' uploaded successfully to '{target_folder_path}' via manual copy.")
        return jsonify({"success": True, "filename": final_filename}), 201

    except OSError as e:
        # Catch OS errors during open() or writing
        app.logger.error(f"OSError saving file '{final_filename}' to path '{destination_abs_str}': {e}", exc_info=True)
        error_msg = f"OS error saving '{final_filename}': {e.strerror}. Check permissions/filesystem/encoding."
        # Attempt to remove partially written file if creation failed midway (optional)
        if os.path.exists(destination_abs_str):
            try: os.remove(destination_abs_str)
            except Exception: pass # Ignore errors during cleanup
        return jsonify({"success": False, "error": error_msg}), 500
    except Exception as e:
        # Catch other unexpected errors
        app.logger.error(f"Unexpected error saving file '{final_filename}' to path '{destination_abs_str}': {e}", exc_info=True)
        error_msg = f"Server error saving '{final_filename}'. Check logs."
        # Attempt cleanup
        if os.path.exists(destination_abs_str):
            try: os.remove(destination_abs_str)
            except Exception: pass
        return jsonify({"success": False, "error": error_msg}), 500


@app.route('/create_folder/', defaults={'subpath': ''}, methods=['POST'])
@app.route('/create_folder/<path:subpath>', methods=['POST'])
@auth.login_required
def create_folder_handler(subpath):
    """Handles creation of a new folder within the specified subpath."""
    parent_folder_path = get_relative_path_from_request(subpath)
    app.logger.debug(f"Create Folder Handler: Parent Folder = '{parent_folder_path}'")
    parent_dir_abs = file_utils.get_safe_fullpath(parent_folder_path)

    if parent_dir_abs is None or not os.path.isdir(parent_dir_abs):
        flash(f"Folder creation failed: Parent directory '{parent_folder_path or '/'}' not found or invalid.", "error")
        return redirect(request.headers.get("Referer") or url_for('browse'))

    folder_name = request.form.get('foldername', '').strip()
    if not folder_name or '/' in folder_name or '\\' in folder_name or folder_name.startswith('.') or folder_name == '..':
        flash(f"Invalid folder name: '{folder_name}'. Use simple names without slashes or leading dots.", "error")
        return redirect(url_for('browse', subpath=parent_folder_path))

    # Use the original valid name, secure_filename can be too strict
    safe_folder_name = folder_name
    new_folder_abs = os.path.join(parent_dir_abs, safe_folder_name)

    if os.path.exists(new_folder_abs):
        flash(f"Folder creation failed: '{safe_folder_name}' already exists.", "error")
    else:
        try:
            os.makedirs(new_folder_abs)
            app.logger.info(f"Folder '{safe_folder_name}' created successfully in '{parent_folder_path}'")
            flash(f"Folder '{safe_folder_name}' created successfully.", 'success')
        except OSError as e:
            app.logger.error(f"Failed to create folder '{new_folder_abs}': {e}")
            flash(f"OS error creating folder '{safe_folder_name}'. Check permissions/name.", 'error')
        except Exception as e:
             app.logger.error(f"Unexpected error creating folder '{new_folder_abs}': {e}", exc_info=True)
             flash(f"Unexpected error creating folder '{safe_folder_name}'.", 'error')
    return redirect(url_for('browse', subpath=parent_folder_path)) # Redirect back


@app.route('/delete/<item_id>', defaults={'parent_path_in_url': ''}, methods=['POST'])
@app.route('/delete/<path:parent_path_in_url>/<item_id>', methods=['POST'])
@auth.login_required # Deletion MUST require login
def delete_item(parent_path_in_url, item_id):
    """Deletes a file or folder."""
    cleaned_parent_path = get_relative_path_from_request(parent_path_in_url)
    app.logger.info(f"Delete Request: Parent='{cleaned_parent_path}', Item ID='{item_id}'")

    # Find the item's relative path first to know what we're deleting
    item_full_relative_path = file_utils.find_path_by_id(cleaned_parent_path, item_id)
    if item_full_relative_path is None:
        flash(f"Error: Item to delete not found.", "error")
        # Redirect back to the parent folder where the delete was attempted
        return redirect(url_for('browse', subpath=cleaned_parent_path))

    target_item_abs = file_utils.get_safe_fullpath(item_full_relative_path)
    item_name = os.path.basename(item_full_relative_path)

    # Important safety check: ensure the path is still valid and within MEDIA_DIR_BASE
    if target_item_abs is None or not target_item_abs.startswith(os.path.abspath(config.MEDIA_DIR_BASE)):
        app.logger.error(f"Deletion blocked: Unsafe path detected. Relative='{item_full_relative_path}', Absolute='{target_item_abs}'")
        flash(f"Error: Cannot delete '{item_name}' due to invalid path.", "error")
        return redirect(url_for('browse', subpath=cleaned_parent_path))

    try:
        if os.path.isfile(target_item_abs):
            os.remove(target_item_abs)
            app.logger.info(f"Successfully deleted file: '{target_item_abs}'")
            flash(f"File '{item_name}' deleted successfully.", "success")
        elif os.path.isdir(target_item_abs):
            # Use shutil.rmtree to delete directory and all its contents
            shutil.rmtree(target_item_abs)
            app.logger.info(f"Successfully deleted folder and contents: '{target_item_abs}'")
            flash(f"Folder '{item_name}' and its contents deleted successfully.", "success")
        else:
            app.logger.warning(f"Attempted to delete non-file/non-dir item: '{target_item_abs}'")
            flash(f"Cannot delete '{item_name}': Item is not a file or folder.", "warning")

    except OSError as e:
        app.logger.error(f"OS error deleting '{target_item_abs}': {e}", exc_info=True)
        flash(f"Error deleting '{item_name}': {e.strerror}. Check permissions.", "error")
    except Exception as e:
        app.logger.error(f"Unexpected error deleting '{target_item_abs}': {e}", exc_info=True)
        flash(f"An unexpected error occurred while deleting '{item_name}'.", "error")

    # Redirect back to the parent folder after attempting deletion
    return redirect(url_for('browse', subpath=cleaned_parent_path))


# --- WSGI Entry Point / Direct Execution ---
# Add route map logging just before main execution
try:
    with app.app_context():
        app.logger.debug(f"REGISTERED ROUTES:\n{app.url_map}")
except Exception as map_e:
    app.logger.error(f"Could not log URL map: {map_e}")

application = app
if __name__ == '__main__':
    print("--- Development Server ---")
    # ... (rest of your main block) ...
    app.run(host='0.0.0.0', port=5000, debug=True)
#!/usr/bin/python3
# -*- coding: utf-8 -*-

# --- Imports ---
import os
import re
import mimetypes
import logging
from datetime import timedelta
import datetime # Needed for context processor
import shutil # Needed for delete folder
from math import ceil # Needed for pagination

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

# --- Flask App Initialization & Configuration ---
app = Flask(__name__, template_folder='templates', static_folder='static')
app.config['SECRET_KEY'] = config.SECRET_KEY
app.config['PERMANENT_SESSION_LIFETIME'] = timedelta(days=30)
# Suppress excessive Werkzeug path decoding logs if desired
# logging.getLogger('werkzeug').setLevel(logging.WARNING)


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
        decoded_path = unquote(str(path_arg))
        normalized = os.path.normpath(decoded_path).strip('/\\')
        result = normalized.replace("\\", "/")
        return "" if result == "." else result
    except Exception as e:
        app.logger.error(f"Error decoding/normalizing path argument '{path_arg}': {e}", exc_info=True)
        return ""

# --- Common Function for Action Routes ---
def get_validated_item_paths(parent_path_from_url, item_id):
    """Gets cleaned parent path, item's full relative path, and item's absolute path."""
    cleaned_parent_path = get_relative_path_from_request(parent_path_from_url)
    app.logger.debug(f"Action Request: URL Parent='{parent_path_from_url}', Item='{item_id}'. Clean Parent='{cleaned_parent_path}'")

    item_full_relative_path = file_utils.find_path_by_id(cleaned_parent_path, item_id)
    if item_full_relative_path is None:
        app.logger.error(f"Action failed: Could not find ID '{item_id}' in parent '{cleaned_parent_path}'")
        abort(404, description="Item ID not found in the specified path.")

    # Determine if it's a file or dir using relative path BEFORE getting absolute
    # This avoids issues if absolute path fails temporarily
    is_dir = None
    temp_abs_path_check = file_utils.get_safe_fullpath(item_full_relative_path)
    if temp_abs_path_check:
         try:
            if os.path.isdir(temp_abs_path_check): is_dir = True
            elif os.path.isfile(temp_abs_path_check): is_dir = False
         except OSError: pass # Handle potential race condition/error

    # Now get final absolute path for return
    target_item_abs = file_utils.get_safe_fullpath(item_full_relative_path)

    # Check if it exists and is the expected type (file for most actions, dir/file for delete)
    # Allow None for is_dir if check failed, but target_item_abs must exist
    if target_item_abs is None or not os.path.exists(target_item_abs):
         app.logger.error(f"Action failed: Path unsafe or item does not exist. Relative='{item_full_relative_path}', Absolute='{target_item_abs}'")
         abort(404, description="Item not found or access denied.")

    # Specific check for file actions
    if is_dir == True and not request.endpoint == 'delete_item': # Allow delete for dirs
         app.logger.error(f"Action failed: Expected file but got directory. Relative='{item_full_relative_path}'")
         abort(400, description="Action requires a file, but a directory was specified.")

    return cleaned_parent_path, item_full_relative_path, target_item_abs, is_dir


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
    return auth.handle_login_request()

@app.route('/logout')
def logout():
    """Logs the user out."""
    return auth.handle_logout()

# --- Main Browser Route ---
@app.route('/browse/', defaults={'subpath': ''})
@app.route('/browse/<path:subpath>')
@auth.login_required
def browse(subpath):
    """Lists contents of a directory. Handles all file types, sorting, and pagination."""
    # --- Get Sort Parameters ---
    sort_by = request.args.get('sort_by', 'name')
    sort_order = request.args.get('sort_order', 'asc')
    if sort_by not in ['name', 'type', 'size', 'date']: sort_by = 'name'
    if sort_order not in ['asc', 'desc']: sort_order = 'asc'

    # --- Pagination Params ---
    try: page = int(request.args.get('page', 1))
    except ValueError: page = 1
    if page < 1: page = 1
    items_per_page = 198 # Configurable

    current_path = get_relative_path_from_request(subpath)
    app.logger.info(f"Request browse: Path='{current_path}', SortBy='{sort_by}', Order='{sort_order}', Page='{page}'")

    target_dir_abs = file_utils.get_safe_fullpath(current_path)
    if target_dir_abs is None or not os.path.isdir(target_dir_abs):
         flash(f"Error: Directory not found: '{current_path or '/'}'", "error")
         # Try redirecting to parent if current path exists somewhat
         parent_of_invalid = os.path.dirname(current_path).replace("\\","/") if current_path else ''
         if parent_of_invalid == ".": parent_of_invalid = ""
         return redirect(url_for('browse', subpath=parent_of_invalid))

    # Get sorted items list from file_utils
    all_items_unpaginated, is_image_only_folder = file_utils.get_folder_contents_with_ids(
        current_path, sort_by=sort_by, sort_order=sort_order
    )

    # Apply Pagination ONLY for Image Grid
    total_items = len(all_items_unpaginated)
    total_pages = 1
    items_to_display = all_items_unpaginated

    if is_image_only_folder and total_items > items_per_page:
        total_pages = ceil(total_items / items_per_page)
        if page > total_pages: page = total_pages # Adjust page if out of bounds
        start_index = (page - 1) * items_per_page
        end_index = start_index + items_per_page
        items_to_display = all_items_unpaginated[start_index:end_index]
        app.logger.debug(f"Image grid pagination: Page {page}/{total_pages}, Items {start_index}-{end_index-1} of {total_items}")
    elif is_image_only_folder:
         app.logger.debug(f"Image grid: {total_items} items, no pagination.")

    # Prepare Breadcrumbs (Ensure sort/page params are included)
    breadcrumbs = []
    home_url_params = {'sort_by': sort_by, 'sort_order': sort_order}
    breadcrumbs.append({'name': 'Home', 'url': url_for('browse', **home_url_params)})
    current_folder_name = 'Home'
    if current_path:
        parts = current_path.strip('/').split('/')
        path_accum = ''
        for i, part in enumerate(parts):
            display_part = part
            path_accum = f"{path_accum}{part}" if not path_accum else f"{path_accum}/{part}"
            # Include sort params in breadcrumb links
            breadcrumb_url_params = {'subpath': path_accum, 'sort_by': sort_by, 'sort_order': sort_order}
            breadcrumb_url = url_for('browse', **breadcrumb_url_params)
            is_last = (i == len(parts) - 1)
            breadcrumbs.append({'name': display_part, 'url': None if is_last else breadcrumb_url})
            if is_last: current_folder_name = display_part

    # Parent directory link (Include sort params)
    parent_dir_path = os.path.dirname(current_path).replace("\\", "/") if current_path else ''
    if parent_dir_path == ".": parent_dir_path = ""
    up_link_url = url_for('browse', subpath=parent_dir_path, sort_by=sort_by, sort_order=sort_order) if current_path else None

    # Download Playlist Link (Include sort params - maybe not necessary?)
    has_media = any(item['type'] in ('video', 'audio') for item in items_to_display) # Check displayed items
    download_playlist_link = url_for('download_playlist', subpath=current_path) if has_media else None

    return render_template(
        'browse.html',
        items=items_to_display,
        current_path=current_path,
        current_folder_name=current_folder_name,
        breadcrumbs=breadcrumbs, # Pass calculated breadcrumbs
        up_link_url=up_link_url,
        is_image_only_folder=is_image_only_folder,
        download_playlist_link=download_playlist_link,
        video_ext=config.VIDEO_EXTENSIONS,
        audio_ext=config.AUDIO_EXTENSIONS,
        current_sort_by=sort_by,
        current_sort_order=sort_order,
        pagination={ # Pass pagination info
            'current_page': page, 'total_pages': total_pages, 'has_prev': page > 1,
            'has_next': page < total_pages, 'total_items': total_items, 'per_page': items_per_page
        }
    )

# --- File Action Routes ---

@app.route('/download/<item_id>', defaults={'parent_path_in_url': ''})
@app.route('/download/<path:parent_path_in_url>/<item_id>')
# @auth.login_required # REMOVED for public download
def download_file(parent_path_in_url, item_id):
    """Provides a file for download by its ID. (Public)"""
    _, item_full_relative_path, target_file_abs, is_dir = get_validated_item_paths(parent_path_in_url, item_id)
    if is_dir: abort(400, "Cannot download a directory.") # Should not happen if called from UI correctly
    try:
        filename = os.path.basename(item_full_relative_path)
        response = make_response(send_file(target_file_abs, as_attachment=True, download_name=filename))
        return response
    except Exception as e: app.logger.error(f"Error sending file '{target_file_abs}': {e}", exc_info=True); abort(500)


@app.route('/view_text/<item_id>', defaults={'parent_path_in_url': ''})
@app.route('/view_text/<path:parent_path_in_url>/<item_id>')
@auth.login_required
def view_text_content(parent_path_in_url, item_id):
    """Returns the content of a text file as JSON."""
    _, item_full_relative_path, target_file_abs, is_dir = get_validated_item_paths(parent_path_in_url, item_id)
    if is_dir: abort(400, "Cannot view directory content this way.")
    if file_utils.get_file_type(item_full_relative_path) != 'text': return jsonify({"error": "File is not a text file."}), 400
    content, error_message = file_utils.read_text_file_safe(target_file_abs)
    if error_message: return jsonify({"error": error_message}), 400
    else: return jsonify({"content": content, "filename": os.path.basename(item_full_relative_path)})


@app.route('/view_image/<item_id>', defaults={'parent_path_in_url': ''})
@app.route('/view_image/<path:parent_path_in_url>/<item_id>')
@auth.login_required # Keep login for direct image viewing? Optional.
def view_image_file(parent_path_in_url, item_id):
    """Serves an image file directly for display."""
    _, item_full_relative_path, target_file_abs, is_dir = get_validated_item_paths(parent_path_in_url, item_id)
    if is_dir: abort(400, "Cannot view directory as image.")
    if file_utils.get_file_type(item_full_relative_path) != 'image': abort(400, description="Requested item is not an image file.")
    try: return send_file(target_file_abs, as_attachment=False)
    except Exception as e: app.logger.error(f"Error sending image file '{target_file_abs}': {e}", exc_info=True); abort(500)


# --- Media Streaming & Playback ---

@app.route('/stream/<item_id>', defaults={'parent_path_in_url': ''})
@app.route('/stream/<path:parent_path_in_url>/<item_id>', methods=['GET', 'HEAD'])
@auth.login_required
def stream_media_by_id(parent_path_in_url, item_id):
    """Streams media (video/audio) looked up by ID, handles range requests."""
    _, item_full_relative_path, target_file_abs, is_dir = get_validated_item_paths(parent_path_in_url, item_id)
    if is_dir: abort(400, "Cannot stream directory.")
    app.logger.debug(f"Stream: Serving abs path '{target_file_abs}' for rel '{item_full_relative_path}'")
    # ... (Range handling and response generation - keep existing correct version) ...
    range_header = request.headers.get('Range', None); size = os.path.getsize(target_file_abs)
    byte1, byte2 = 0, None
    if range_header:
        m = re.match(r'bytes=(\d+)-(\d*)', range_header)
        if m:
            try: byte1 = int(m.group(1)); rg2 = m.group(2); byte2 = int(rg2) if rg2 else None
            except ValueError: abort(400)
        else: abort(400)
    if byte2 is None or byte2 >= size: byte2 = size - 1
    if byte1 < 0 or byte1 >= size or byte1 > byte2:
        resp = Response("Range Not Satisfiable", 416, headers={'Content-Range': f'bytes */{size}'}); return resp
    length = byte2 - byte1 + 1
    def generate_chunks():
        try:
            with open(target_file_abs, 'rb') as f:
                f.seek(byte1); remaining = length
                while remaining > 0:
                    read_size=min(config.CHUNK_SIZE,remaining); chunk=f.read(read_size)
                    if not chunk: break
                    yield chunk; remaining-=len(chunk)
        except Exception as e_gen: app.logger.error(f"Stream generator error: {e_gen}", exc_info=True)
    mime_type, _ = mimetypes.guess_type(target_file_abs)
    if not mime_type: file_type = file_utils.get_file_type(item_full_relative_path); mime_type = 'video/mp4' if file_type == 'video' else 'audio/mpeg' if file_type == 'audio' else 'application/octet-stream'
    rv = Response(generate_chunks(), 206, mimetype=mime_type, direct_passthrough=True)
    rv.headers.set('Content-Range', f'bytes {byte1}-{byte2}/{size}'); rv.headers.set('Accept-Ranges', 'bytes'); rv.headers.set('Content-Length', str(length))
    return rv


@app.route('/play_video/<item_id>', defaults={'parent_path_in_url': ''})
@app.route('/play_video/<path:parent_path_in_url>/<item_id>')
@auth.login_required
def play_video_page(parent_path_in_url, item_id):
    """Renders the video player page."""
    cleaned_parent_path, item_full_relative_path, target_file_abs, is_dir = get_validated_item_paths(parent_path_in_url, item_id)
    if is_dir: abort(400, "Cannot play directory.")
    if file_utils.get_file_type(item_full_relative_path) != 'video':
        flash(f"'{os.path.basename(item_full_relative_path)}' is not a video file.", "warning")
        return redirect(url_for('browse', subpath=cleaned_parent_path))
    quality_options = file_utils.get_quality_options(item_full_relative_path) or [{'label': 'Original', 'path': item_full_relative_path, 'id': item_id}]
    previous_id, next_id = file_utils.find_prev_next_ids(item_full_relative_path, item_type_filter='video')
    prev_link_url = url_for('play_video_page', parent_path_in_url=cleaned_parent_path, item_id=previous_id) if previous_id else None
    next_link_url = url_for('play_video_page', parent_path_in_url=cleaned_parent_path, item_id=next_id) if next_id else None
    back_link_url = url_for('browse', subpath=cleaned_parent_path)
    initial_stream_url = url_for('stream_media_by_id', parent_path_in_url=cleaned_parent_path, item_id=item_id)
    display_filename = os.path.basename(item_full_relative_path); is_problematic_filename = any(c > '\x7f' for c in display_filename)
    mime_type, _ = mimetypes.guess_type(target_file_abs); mime_type = mime_type or 'video/mp4'
    return render_template('player_video.html', display_filename=display_filename, back_link_url=back_link_url, quality_options=quality_options, is_problematic_filename=is_problematic_filename, prev_link_url=prev_link_url, next_link_url=next_link_url, initial_stream_url=initial_stream_url, initial_mime_type=mime_type, parent_path_json=cleaned_parent_path)


@app.route('/play_audio/<item_id>', defaults={'parent_path_in_url': ''})
@app.route('/play_audio/<path:parent_path_in_url>/<item_id>')
@auth.login_required
def play_audio_page(parent_path_in_url, item_id):
    """Renders the simplified audio player page."""
    cleaned_parent_path, item_full_relative_path, target_file_abs, is_dir = get_validated_item_paths(parent_path_in_url, item_id)
    if is_dir: abort(400, "Cannot play directory.")
    if file_utils.get_file_type(item_full_relative_path) != 'audio':
        flash(f"'{os.path.basename(item_full_relative_path)}' is not an audio file.", "warning")
        return redirect(url_for('browse', subpath=cleaned_parent_path))
    previous_id, next_id = file_utils.find_prev_next_ids(item_full_relative_path, item_type_filter='audio')
    prev_link_url = url_for('play_audio_page', parent_path_in_url=cleaned_parent_path, item_id=previous_id) if previous_id else None
    next_link_url = url_for('play_audio_page', parent_path_in_url=cleaned_parent_path, item_id=next_id) if next_id else None
    back_link_url = url_for('browse', subpath=cleaned_parent_path)
    stream_url = url_for('stream_media_by_id', parent_path_in_url=cleaned_parent_path, item_id=item_id)
    display_filename = os.path.basename(item_full_relative_path); is_problematic_filename = any(c > '\x7f' for c in display_filename)
    mime_type, _ = mimetypes.guess_type(target_file_abs); mime_type = mime_type or 'audio/mpeg'
    return render_template('player_audio.html', display_filename=display_filename, back_link_url=back_link_url, stream_url=stream_url, mime_type=mime_type, is_problematic_filename=is_problematic_filename, prev_link_url=prev_link_url, next_link_url=next_link_url)


# --- Playlist Download ---
@app.route('/download_playlist/', defaults={'subpath': ''})
@app.route('/download_playlist/<path:subpath>')
@auth.login_required
def download_playlist(subpath):
    """Generates M3U playlist with direct download links."""
    current_path = get_relative_path_from_request(subpath)
    # ... (keep existing correct logic using url_for('download_file',...) ...
    target_dir_abs = file_utils.get_safe_fullpath(current_path)
    if target_dir_abs is None or not os.path.isdir(target_dir_abs): flash(f"Dir not found: '{current_path or '/'}'", "error"); return redirect(url_for('browse', subpath=current_path))
    all_items, _ = file_utils.get_folder_contents_with_ids(current_path) # Use default sort for playlist
    media_items = [item for item in all_items if item['type'] in ('video', 'audio')]
    if not media_items: flash("No media found for playlist.", "info"); return redirect(url_for('browse', subpath=current_path))
    folder_name_base = os.path.basename(current_path) if current_path else "media_root"; playlist_filename_base = secure_filename(f"{folder_name_base}") or "playlist"; playlist_filename = f"{playlist_filename_base}.m3u"
    m3u_content = ["#EXTM3U"]
    for item in media_items:
        display_name_for_m3u = item['display_name'].replace(',', ';').replace('\n', ' ').replace('\r', '')
        download_url = url_for('download_file', parent_path_in_url=current_path, item_id=item['id'], _external=True)
        m3u_content.append(f"#EXTINF:-1,{display_name_for_m3u}"); m3u_content.append(download_url)
    m3u_data = "\n".join(m3u_content) + "\n"; response = make_response(m3u_data.encode('utf-8')); response.headers["Content-Type"] = "audio/x-mpegurl; charset=utf-8"
    try: encoded_filename = quote(playlist_filename, safe=""); response.headers["Content-Disposition"] = f"attachment; filename*=UTF-8''{encoded_filename}"
    except Exception: ascii_filename = playlist_filename.encode('ascii', 'ignore').decode('ascii') or "playlist.m3u"; response.headers["Content-Disposition"] = f"attachment; filename=\"{ascii_filename}\""
    return response


# --- File/Folder Management ---

@app.route('/upload/', defaults={'subpath': ''}, methods=['POST'])
@app.route('/upload/<path:subpath>', methods=['POST'])
@auth.login_required
def upload_file_handler(subpath):
    """Handles single file uploads via JS fetch, returns JSON."""
    target_folder_path = get_relative_path_from_request(subpath)
    # ... (keep existing corrected logic using manual open/copyfileobj and returning JSON) ...
    target_dir_abs = file_utils.get_safe_fullpath(target_folder_path)
    if target_dir_abs is None or not os.path.isdir(target_dir_abs): return jsonify({"success": False, "error": f"Target directory '{target_folder_path or '/'}' not found."}), 400
    if 'file' not in request.files: return jsonify({"success": False, "error": "No 'file' part."}), 400
    file = request.files['file']; original_filename = file.filename
    if not file or not original_filename: return jsonify({"success": False, "error": "No file or filename."}), 400
    cleaned_filename = original_filename.strip('. ');
    if os.path.sep != '/': cleaned_filename = cleaned_filename.replace(os.path.sep, '_')
    cleaned_filename = cleaned_filename.replace('/', '_')
    if ".." in cleaned_filename: return jsonify({"success": False, "error": f"Filename '{original_filename}' contains '..'."}), 400
    if not cleaned_filename: return jsonify({"success": False, "error": f"Filename '{original_filename}' is invalid."}), 400
    final_filename = cleaned_filename; destination_abs_str = os.path.join(target_dir_abs, final_filename)
    if os.path.exists(destination_abs_str): return jsonify({"success": False, "error": f"File '{final_filename}' already exists."}), 409
    try:
        app.logger.debug(f"Attempting to open destination '{destination_abs_str}' in 'wb' mode.")
        with open(destination_abs_str, "wb") as f_dst: shutil.copyfileobj(file.stream, f_dst)
        app.logger.info(f"File '{final_filename}' uploaded via manual copy.")
        return jsonify({"success": True, "filename": final_filename}), 201
    except OSError as e:
        logger.error(f"OSError saving file '{final_filename}' to path '{destination_abs_str}': {e}", exc_info=True)
        error_msg = f"OS error saving '{final_filename}': {e.strerror}. Check permissions/filesystem/encoding."
        # Attempt to remove partially written file if creation failed midway (optional)
        if os.path.exists(destination_abs_str):
            try:
                os.remove(destination_abs_str)
                logger.debug(f"Removed partially written file on OSError: {destination_abs_str}")
            except Exception as cleanup_e:
                logger.warning(f"Could not remove partially written file '{destination_abs_str}' after OSError: {cleanup_e}")
        # Return the error response
        return jsonify({"success": False, "error": error_msg}), 500

    except Exception as e:
        # ... (keep the general Exception block separate) ...
        logger.error(f"Unexpected error saving file '{final_filename}' to path '{destination_abs_str}': {e}", exc_info=True)
        error_msg = f"Server error saving '{final_filename}'. Check logs."
        if os.path.exists(destination_abs_str):
            try:
                os.remove(destination_abs_str)
                logger.debug(f"Removed partially written file on Exception: {destination_abs_str}")
            except Exception as cleanup_e:
                 logger.warning(f"Could not remove partially written file '{destination_abs_str}' after Exception: {cleanup_e}")
        return jsonify({"success": False, "error": error_msg}), 500

@app.route('/create_folder/', defaults={'subpath': ''}, methods=['POST'])
@app.route('/create_folder/<path:subpath>', methods=['POST'])
@auth.login_required
def create_folder_handler(subpath):
    """Handles creation of a new folder."""
    parent_folder_path = get_relative_path_from_request(subpath)
    # ... (keep existing correct logic) ...
    parent_dir_abs = file_utils.get_safe_fullpath(parent_folder_path)
    if parent_dir_abs is None or not os.path.isdir(parent_dir_abs): flash(f"Parent directory '{parent_folder_path or '/'}' not found.", "error"); return redirect(request.headers.get("Referer") or url_for('browse'))
    folder_name = request.form.get('foldername', '').strip()
    if not folder_name or '/' in folder_name or '\\' in folder_name or folder_name.startswith('.') or folder_name == '..': flash(f"Invalid folder name: '{folder_name}'.", "error"); return redirect(url_for('browse', subpath=parent_folder_path))
    safe_folder_name = folder_name; new_folder_abs = os.path.join(parent_dir_abs, safe_folder_name)
    if os.path.exists(new_folder_abs): flash(f"Folder '{safe_folder_name}' already exists.", "error")
    else:
        try: os.makedirs(new_folder_abs); flash(f"Folder '{safe_folder_name}' created.", 'success')
        except OSError as e: logger.error(f"Failed to create folder '{new_folder_abs}': {e}"); flash(f"OS error creating folder '{safe_folder_name}'.", 'error')
        except Exception as e: logger.error(f"Error creating folder '{new_folder_abs}': {e}", exc_info=True); flash(f"Error creating folder '{safe_folder_name}'.", 'error')
    return redirect(url_for('browse', subpath=parent_folder_path))


@app.route('/delete/<item_id>', defaults={'parent_path_in_url': ''}, methods=['POST'])
@app.route('/delete/<path:parent_path_in_url>/<item_id>', methods=['POST'])
@auth.login_required
def delete_item(parent_path_in_url, item_id):
    """Deletes a file or folder."""
    # Use the common validation function, also getting is_dir
    cleaned_parent_path, item_full_relative_path, target_item_abs, is_dir = get_validated_item_paths(parent_path_in_url, item_id)
    item_name = os.path.basename(item_full_relative_path)
    app.logger.info(f"Delete Request: Parent='{cleaned_parent_path}', Item Rel='{item_full_relative_path}', Item Abs='{target_item_abs}', IsDir={is_dir}")

    # Path validation is done in get_validated_item_paths now

    try:
        if is_dir == False: # It's a file
            os.remove(target_item_abs)
            app.logger.info(f"Successfully deleted file: '{target_item_abs}'")
            flash(f"File '{item_name}' deleted successfully.", "success")
        elif is_dir == True: # It's a directory
            shutil.rmtree(target_item_abs)
            app.logger.info(f"Successfully deleted folder and contents: '{target_item_abs}'")
            flash(f"Folder '{item_name}' and its contents deleted successfully.", "success")
        else:
            # Should not happen if get_validated_item_paths worked, but handle defensively
            app.logger.warning(f"Attempted to delete non-file/non-dir item: '{target_item_abs}'")
            flash(f"Cannot delete '{item_name}': Item not found or invalid type.", "warning")
    except OSError as e: logger.error(f"OS error deleting '{target_item_abs}': {e}"); flash(f"Error deleting '{item_name}': {e.strerror}.", "error")
    except Exception as e: logger.error(f"Error deleting '{target_item_abs}': {e}", exc_info=True); flash(f"Error deleting '{item_name}'.", "error")

    # Redirect back to the parent folder
    return redirect(url_for('browse', subpath=cleaned_parent_path))


# --- WSGI Entry Point / Direct Execution ---
try:
    with app.app_context(): app.logger.debug(f"REGISTERED ROUTES:\n{app.url_map}")
except Exception as map_e: app.logger.error(f"Could not log URL map: {map_e}")

application = app
if __name__ == '__main__':
    print("--- Development Server ---"); print(f"Media Directory: {config.MEDIA_DIR_BASE}"); print(f"Access URL: http://0.0.0.0:5000"); print("---")
    app.run(host='0.0.0.0', port=5000, debug=True) # Use debug=False in production
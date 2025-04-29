# file_utils.py
import os
import hashlib
import logging
import mimetypes
import re
from werkzeug.utils import safe_join, secure_filename
from urllib.parse import quote

import config # Use our config file

# Initialize logging
logger = logging.getLogger(__name__)
logger.setLevel(config.LOG_LEVEL)

# Ensure mimetypes knows about common types if needed
mimetypes.add_type("video/x-matroska", ".mkv")
mimetypes.add_type("video/mp4", ".m4a")

# --- Identifier Generation ---
def generate_item_id(original_relative_path):
    """Generates a stable SHA1 hash ID (truncated) for a given relative path."""
    # Ensure consistent format (forward slashes, no leading/trailing)
    clean_path = str(original_relative_path or "").strip('/').replace("\\", "/")
    try:
        path_bytes = clean_path.encode(config.FILESYSTEM_ENCODING, 'surrogateescape')
    except Exception:
        path_bytes = clean_path.encode('utf-8', 'replace')
        logger.warning(f"Falling back to UTF-8 encoding for ID generation of: {clean_path!r}")
    return hashlib.sha1(path_bytes).hexdigest()[:16]

# --- Security Helper ---
def get_safe_fullpath(original_relative_path=""):
    """
    Safely joins the base media directory with the relative path, preventing traversal.
    Returns the absolute path if safe, otherwise None. Accepts empty path for base dir.
    """
    try:
        # Clean the input relative path FIRST
        clean_relative_path = str(original_relative_path or "").strip('/\\').replace("\\", "/")
        if clean_relative_path == ".": clean_relative_path = ""

        # Check for potentially malicious parts before joining
        parts = clean_relative_path.split('/')
        if any(p == '..' for p in parts) or \
           any(p.startswith('.') and p not in ('', '.') for p in parts): # Allow '.' part if it results from normalization
            logger.warning(f"Blocked potentially unsafe path component in relative path: {original_relative_path!r}")
            return None

        # Use safe_join for the primary check
        # Handle empty path case explicitly to return base dir
        if not clean_relative_path:
            full_path = config.MEDIA_DIR_BASE
        else:
            full_path = safe_join(config.MEDIA_DIR_BASE, clean_relative_path)

        # Final check: ensure the resolved absolute path is truly within the base directory
        if full_path:
             abs_path = os.path.abspath(full_path)
             # Check startswith on the normalized, absolute base path
             if not abs_path.startswith(os.path.abspath(config.MEDIA_DIR_BASE)):
                 logger.error(f"Path traversal attempt detected (post safe_join): Rel='{original_relative_path!r}' resolved to Abs='{abs_path!r}' which is outside Base='{os.path.abspath(config.MEDIA_DIR_BASE)}'")
                 return None
             # logger.debug(f"get_safe_fullpath: Rel='{original_relative_path}' -> CleanRel='{clean_relative_path}' -> Abs='{abs_path}' (SAFE)")
             return abs_path
        else:
             # safe_join returned None, likely due to path components like '..' or absolute paths
             logger.warning(f"safe_join rejected relative path: {original_relative_path!r} (Cleaned: {clean_relative_path!r})")
             return None

    except ValueError as e: # safe_join can raise ValueError
        logger.error(f"Path construction ValueError for relative path {original_relative_path!r}: {e}")
        return None
    except Exception as e:
        logger.error(f"Unexpected safe path error for relative path {original_relative_path!r}: {e}", exc_info=True)
        return None

# --- File Type Classification ---
def get_file_type(filename):
    """Determines the general file type based on extension."""
    if not filename: return 'other'
    _, ext = os.path.splitext(filename)
    return config.EXTENSION_TYPE_MAP.get(ext.lower(), 'other')

# --- Content Listing Helper ---
def get_folder_contents_with_ids(current_relative_path=""):
    """
    Lists contents of a directory specified by its relative path from base.
    Assigns types and IDs. Includes all non-hidden files.
    """
    # Clean the input path
    clean_current_path = str(current_relative_path or "").strip('/\\').replace("\\", "/")
    if clean_current_path == ".": clean_current_path = ""

    target_dir_abs = get_safe_fullpath(clean_current_path)
    if target_dir_abs is None or not os.path.isdir(target_dir_abs):
        logger.error(f"Cannot list contents: Invalid or non-existent directory. Relative='{current_relative_path}', Clean='{clean_current_path}', Abs='{target_dir_abs}'")
        return [], False # Return empty list and 'is_image_only' as False

    items = []
    is_image_only = True
    try:
        raw_entry_names = os.listdir(target_dir_abs)
        for item_name_orig in raw_entry_names:
            if item_name_orig.startswith('.'): continue # Skip hidden

            display_name = item_name_orig
            is_problematic = False
            item_type = 'other'
            item_size = 0
            full_item_path_abs = None

            # --- Construct FULL RELATIVE path for this item ---
            # Join the clean *relative* parent path with the item name
            item_full_relative_path = os.path.join(clean_current_path, item_name_orig).replace("\\", "/")
            # ---

            # Check for problematic encoding for display name
            try: item_name_orig.encode('utf-8')
            except UnicodeEncodeError: is_problematic = True; display_name = repr(item_name_orig)

            # Get absolute path for type/size check
            try: full_item_path_abs = safe_join(target_dir_abs, item_name_orig)
            except Exception: continue # Skip if path construction fails

            if not full_item_path_abs: continue # Skip if safe_join fails

            # Determine type and size
            try:
                if os.path.isdir(full_item_path_abs):
                    item_type = 'folder'
                    is_image_only = False # Folder means not image-only
                elif os.path.isfile(full_item_path_abs):
                    item_type = get_file_type(item_name_orig)
                    item_size = os.path.getsize(full_item_path_abs)
                    if item_type != 'image': is_image_only = False
                else: continue # Skip non-file/non-dir
            except OSError as e: logger.error(f"OS error accessing item '{full_item_path_abs}': {e}"); continue

            # Generate ID using the FULL RELATIVE path
            item_id = generate_item_id(item_full_relative_path)

            items.append({
                'type': item_type,
                'display_name': display_name,
                'id': item_id,
                'path': item_full_relative_path, # Store the full relative path
                'encoded_path': quote(item_full_relative_path), # URL-encoded full relative path
                'size': item_size,
                'is_problematic': is_problematic,
            })

    except OSError as e: logger.error(f"Error listing directory '{target_dir_abs}': {e}", exc_info=True); return [], False
    except Exception as e: logger.error(f"Unexpected error scanning directory '{target_dir_abs}': {e}", exc_info=True); return [], False

    # --- Sorting --- (Keep existing sort logic)
    def sort_key(item):
        type_order = {'folder': 0, 'video': 1, 'audio': 2, 'image': 3, 'text': 4, 'other': 5}.get(item['type'], 99)
        name = item['display_name']
        name_lower = str(name).lower()
        match = re.match(r'(\d+)', name_lower)
        num_prefix = int(match.group(1)) if match else float('inf')
        return (type_order, num_prefix, name_lower)
    try: items.sort(key=sort_key)
    except Exception as e: logger.error(f"Sorting error in directory '{clean_current_path}': {e}", exc_info=True)

    if not items: is_image_only = False
    logger.debug(f"Listed {len(items)} items in '{clean_current_path}'. Image only: {is_image_only}")
    return items, is_image_only


# --- Helper to find original path by ID ---
def find_path_by_id(parent_relative_path, item_id):
    """Finds the full original relative path of an item given its ID and parent's relative path."""
    clean_parent_path = str(parent_relative_path or "").strip('/\\').replace("\\", "/")
    if clean_parent_path == ".": clean_parent_path = ""

    target_dir_abs = get_safe_fullpath(clean_parent_path)
    if target_dir_abs is None or not os.path.isdir(target_dir_abs):
        logger.warning(f"find_path_by_id: Invalid parent directory '{clean_parent_path}' (original: '{parent_relative_path}')")
        return None

    logger.debug(f"find_path_by_id: Searching for ID '{item_id}' in parent '{clean_parent_path}' (Abs: '{target_dir_abs}')")
    try:
        for item_name_orig in os.listdir(target_dir_abs):
            if item_name_orig.startswith('.'): continue

            # --- Construct the item's full relative path ---
            item_full_relative_path = os.path.join(clean_parent_path, item_name_orig).replace("\\", "/")
            # ---

            current_id = generate_item_id(item_full_relative_path)
            # logger.debug(f"find_path_by_id: Checking '{item_name_orig}' -> path '{item_full_relative_path}' -> ID '{current_id}'")

            if current_id == item_id:
                logger.info(f"find_path_by_id: Match found for ID '{item_id}': Path='{item_full_relative_path}'")
                # Final safety check on the found path itself
                if get_safe_fullpath(item_full_relative_path):
                    return item_full_relative_path
                else:
                    logger.error(f"find_path_by_id: Found path '{item_full_relative_path}' for ID '{item_id}' but it failed safety check.")
                    return None
    except OSError as e: logger.error(f"find_path_by_id: OSError listing directory '{target_dir_abs}' for ID '{item_id}': {e}")
    except Exception as e: logger.error(f"find_path_by_id: Unexpected error searching for ID '{item_id}' in '{clean_parent_path}': {e}", exc_info=True)

    logger.warning(f"Could not find item ID '{item_id}' in directory '{clean_parent_path}'")
    return None

# --- Quality Options Helper ---
def get_quality_options(item_full_relative_path):
    """Finds alternative quality versions using the item's full relative path."""
    options = []
    logger.debug(f"Getting quality options for: {item_full_relative_path!r}")
    try:
        # Get directory part (relative) and filename from the full relative path
        dir_part_relative = os.path.dirname(item_full_relative_path).replace("\\","/")
        filename = os.path.basename(item_full_relative_path)
        if not filename: return [] # Cannot process if no filename
        base_name, ext = os.path.splitext(filename)
        ext_lower = ext.lower()
    except Exception as e: logger.error(f"Error parsing path for quality options '{item_full_relative_path}': {e}"); return []

    # Check original file exists and is safe
    original_file_abs = get_safe_fullpath(item_full_relative_path)
    if not original_file_abs or not os.path.isfile(original_file_abs):
         logger.warning(f"Original file invalid for quality check: {item_full_relative_path}")
         return []

    options.append({'label': 'Original', 'path': item_full_relative_path, 'id': generate_item_id(item_full_relative_path)})

    # Get absolute path of the parent directory
    full_dir_path_abs = get_safe_fullpath(dir_part_relative)
    if full_dir_path_abs is None or not os.path.isdir(full_dir_path_abs):
        logger.warning(f"Parent directory invalid for quality check: {dir_part_relative}")
        return options # Return only original

    try:
        for item_name_scan in os.listdir(full_dir_path_abs):
            if item_name_scan.startswith('.'): continue

            # Construct full relative path for the potential variant
            variant_full_relative_path = os.path.join(dir_part_relative, item_name_scan).replace("\\", "/")

            # Quick check: skip self
            if variant_full_relative_path == item_full_relative_path: continue

            # Basic check: must be a file
            variant_abs_path = safe_join(full_dir_path_abs, item_name_scan)
            if not variant_abs_path or not os.path.isfile(variant_abs_path): continue

            item_base, item_ext = os.path.splitext(item_name_scan)
            if item_ext.lower() == ext_lower and item_base.startswith(base_name):
                suffix = item_base[len(base_name):]
                if suffix in config.QUALITY_SUFFIXES:
                    # Ensure the variant path is safe before adding
                    if get_safe_fullpath(variant_full_relative_path):
                         options.append({
                             'label': config.QUALITY_SUFFIXES[suffix],
                             'path': variant_full_relative_path, # Store full relative path
                             'id': generate_item_id(variant_full_relative_path) # Generate ID
                         })
                    else: logger.warning(f"Skipping unsafe quality option path: {variant_full_relative_path}")
    except OSError as e: logger.error(f"Error scanning directory for quality options '{full_dir_path_abs}': {e}")
    except Exception as e: logger.error(f"Unexpected error finding quality options: {e}", exc_info=True)

    # Sorting (keep existing)
    def sort_key_q(opt): # ... (keep sort logic) ...
        label = opt['label']; return 9999 if label=='Original' else int(m.group(1)) if (m:=re.match(r'(\d+)p',label)) else 10000
    try: options.sort(key=sort_key_q, reverse=True)
    except Exception as e: logger.error(f"Quality options sort error: {e}")

    # Deduplicate (should be less needed with IDs, but safe)
    seen_paths=set(); unique_options=[opt for opt in options if opt['path'] not in seen_paths and not seen_paths.add(opt['path'])]
    logger.debug(f"Found {len(unique_options)} quality options for {item_full_relative_path}")
    return unique_options

# --- Prev/Next ID Helper ---
def find_prev_next_ids(current_item_full_relative_path, item_type_filter=None):
    """
    Finds the previous and next item IDs using the current item's full relative path.
    Can filter by specific item type ('video', 'audio', etc.).
    """
    prev_id, next_id = None, None
    logger.debug(f"find_prev_next_ids: called for '{current_item_full_relative_path}', filter='{item_type_filter}'")
    try:
        # Derive parent's relative path from the current item's path
        parent_relative_path = os.path.dirname(current_item_full_relative_path).replace("\\", "/")
        if parent_relative_path == ".": parent_relative_path = ""
        logger.debug(f"find_prev_next_ids: Derived parent path: '{parent_relative_path}'")

        # Get siblings by listing the parent directory
        all_items, _ = get_folder_contents_with_ids(parent_relative_path)

        relevant_items = [item for item in all_items if item_type_filter is None or item['type'] == item_type_filter]
        if not relevant_items:
            logger.debug("find_prev_next_ids: No relevant items found in parent."); return None, None

        # Find the current item's index in the *relevant* list
        current_id = generate_item_id(current_item_full_relative_path)
        current_index = -1
        for i, item in enumerate(relevant_items):
            # logger.debug(f"Comparing current_id {current_id} with item id {item['id']} ({item['path']})")
            if item['id'] == current_id:
                current_index = i
                logger.debug(f"find_prev_next_ids: Found current item at index {i}"); break

        if current_index != -1:
            if current_index > 0: prev_id = relevant_items[current_index - 1]['id']
            if current_index < len(relevant_items) - 1: next_id = relevant_items[current_index + 1]['id']
        else: logger.warning(f"find_prev_next_ids: Current item ID '{current_id}' (path '{current_item_full_relative_path}') not found among relevant siblings in parent '{parent_relative_path}'.")

    except Exception as e: logger.error(f"Error finding prev/next IDs for '{current_item_full_relative_path}' (type: {item_type_filter}): {e}", exc_info=True)

    logger.debug(f"Prev/Next for '{current_item_full_relative_path}' (type: {item_type_filter}): Prev ID={prev_id}, Next ID={next_id}")
    return prev_id, next_id

# --- Text File Reader ---
def read_text_file_safe(file_path_abs, max_size=1024*1024):
     """Safely reads the content of a text file, limiting size."""
     # ... (Keep existing implementation, it uses absolute path already) ...
     try:
        if not file_path_abs or not os.path.isfile(file_path_abs) or not file_path_abs.startswith(os.path.abspath(config.MEDIA_DIR_BASE)):
             logger.warning(f"read_text_file_safe: Invalid path provided: {file_path_abs}")
             return None, "Error: Invalid file path."
        size = os.path.getsize(file_path_abs)
        if size > max_size: return None, f"Error: File too large ({size / (1024*1024):.2f} MB)."
        encodings_to_try = [config.FILESYSTEM_ENCODING, 'utf-8', 'latin-1']
        content = None; detected_encoding = None
        for enc in encodings_to_try:
            try:
                with open(file_path_abs, 'r', encoding=enc) as f: content = f.read()
                detected_encoding = enc; break
            except UnicodeDecodeError: continue
            except Exception as e: return None, f"Error reading file (encoding: {enc})."
        if content is None: return None, "Error: Could not determine file encoding."
        return content, None
     except OSError as e: logger.error(f"OS error reading text file '{file_path_abs}': {e}"); return None, "Error: Could not access file."
     except Exception as e: logger.error(f"Unexpected error reading text file '{file_path_abs}': {e}", exc_info=True); return None, "Error reading file."
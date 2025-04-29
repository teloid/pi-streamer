# auth.py
from functools import wraps
from flask import session, redirect, url_for, request, flash, render_template_string
from werkzeug.security import generate_password_hash, check_password_hash
import config # Use our config file

# --- Password Hashing Helper (Run this once manually to generate the hash) ---
def create_password_hash(password):
    """Generates a secure hash for the given password."""
    # Adjust salt_length and iterations as needed for your security requirements
    return generate_password_hash(password, method='pbkdf2:sha256', salt_length=16)

# Example usage (run python -c 'import auth; auth.print_hash("your_password")'):
def print_hash(password):
    print(f"Password: {password}")
    print(f"Hash: {create_password_hash(password)}")
    print("Copy the hash (starting with pbkdf2:...) into config.py's PASSWORD_HASH variable.")

# --- Authentication Check ---
def check_password(password):
    """Checks if the provided password matches the stored hash."""
    if not config.PASSWORD_HASH or config.PASSWORD_HASH.startswith("pbkdf2:sha256:..."):
        # Handle case where password isn't set (maybe allow access in debug mode?)
        # For security, it's better to deny access if no password is set.
        # flash("Password not configured on the server.", "error") # Optional: inform user
        return False # Deny access if no hash is set properly
    return check_password_hash(config.PASSWORD_HASH, password)

# --- Login Required Decorator ---
def login_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if 'logged_in' not in session:
            flash("Please log in to access this page.", "warning")
            return redirect(url_for('login', next=request.url))
        return f(*args, **kwargs)
    return decorated_function

# --- Login Route Logic (can be called from app.py) ---
def handle_login_request():
    error = None
    if request.method == 'POST':
        password_attempt = request.form.get('password')
        if password_attempt and check_password(password_attempt):
            session['logged_in'] = True
            session.permanent = True # Make session last longer (e.g., 30 days)
            flash('You were successfully logged in.', 'success')
            next_url = request.args.get('next') or url_for('browse')
            return redirect(next_url)
        else:
            error = 'Invalid password. Please try again.'
            flash(error, 'error') # Flash the error message

    # Simple login form template string (or use render_template('login.html'))
    login_form_template = """
    <!doctype html>
    <html lang="en">
    <head>
      <meta charset="utf-8">
      <meta name="viewport" content="width=device-width, initial-scale=1, shrink-to-fit=no">
      <title>Login Required</title>
      <style>
        body { font-family: sans-serif; background-color: #222; color: #eee; display: flex; justify-content: center; align-items: center; height: 100vh; margin: 0; }
        .login-container { background-color: #333; padding: 30px 40px; border-radius: 8px; box-shadow: 0 4px 15px rgba(0,0,0,0.5); text-align: center; }
        h2 { margin-top: 0; margin-bottom: 20px; color: #00bcd4; }
        label { display: block; margin-bottom: 5px; text-align: left; font-size: 0.9em; color: #ccc; }
        input[type="password"] { width: 100%; padding: 10px; margin-bottom: 20px; border: 1px solid #555; background-color: #444; color: #eee; border-radius: 4px; box-sizing: border-box; }
        input[type="submit"] { background-color: #009688; color: white; padding: 10px 20px; border: none; border-radius: 4px; cursor: pointer; font-size: 1em; transition: background-color 0.2s; }
        input[type="submit"]:hover { background-color: #00796b; }
        .flash { padding: 10px; margin-bottom: 15px; border-radius: 4px; font-size: 0.9em; }
        .flash.error { background-color: #d9534f; color: white; }
        .flash.warning { background-color: #f0ad4e; color: #333; }
        .flash.success { background-color: #5cb85c; color: white; }
      </style>
    </head>
    <body>
      <div class="login-container">
        <h2>Login Required</h2>
        {% with messages = get_flashed_messages(with_categories=true) %}
          {% if messages %}
            {% for category, message in messages %}
              <div class="flash {{ category }}">{{ message }}</div>
            {% endfor %}
          {% endif %}
        {% endwith %}
        <form method="post">
          <label for="password">Password:</label>
          <input type="password" id="password" name="password" required autofocus>
          <input type="submit" value="Login">
        </form>
      </div>
    </body>
    </html>
    """
    # Use render_template('login.html') if you create the file
    return render_template_string(login_form_template)

# --- Logout Route Logic ---
def handle_logout():
    session.pop('logged_in', None)
    flash('You have been logged out.', 'info')
    return redirect(url_for('welcome')) # Redirect to welcome page after logout
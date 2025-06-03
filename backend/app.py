from flask import Flask, request, jsonify, session, redirect, url_for, render_template_string
from flask_cors import CORS
from werkzeug.utils import secure_filename
import os
import datetime
import subprocess
import pandas as pd
import socket

app = Flask(__name__)
app.secret_key = 'super-secret-key'  # TODO: Use environment variable in production
CORS(app)

# Constants
USERNAME = 'admin'
PASSWORD = 'B2010luetooth5!'
UPLOAD_FOLDER = 'static'
CARFAX_FOLDER = os.path.join(UPLOAD_FOLDER, 'carfax')
INVENTORY_FILE = 'inventory.csv'
LOG_FILE = 'update_log.txt'
ALLOWED_EXTENSIONS = {'png', 'jpg', 'jpeg', 'svg'}
os.makedirs(CARFAX_FOLDER, exist_ok=True)

def get_server_ip():
    """Returns the IP address of the server."""
    return socket.gethostbyname(socket.gethostname())

def allowed_file(filename, allowed_ext=ALLOWED_EXTENSIONS):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in allowed_ext

@app.route('/')
def home():
    return redirect('/admin')

# --- Login and Admin Panel ---
LOGIN_HTML = '''
<!doctype html>
<title>Admin Login</title>
<h2>Login</h2>
<form method="POST" action="/login">
  <input type="text" name="username" placeholder="Username"><br>
  <input type="password" name="password" placeholder="Password"><br>
  <button type="submit">Login</button>
</form>
'''

@app.route('/admin')
def admin_panel():
    if 'logged_in' not in session:
        return render_template_string(LOGIN_HTML)

    last_update = 'Never'
    if os.path.exists(LOG_FILE):
        with open(LOG_FILE) as f:
            lines = f.readlines()
            if lines:
                last_update = lines[-1]

    return render_template_string("""
    <!DOCTYPE html>
    <html>
    <head>
      <title>Admin Panel</title>
      <style>
        body { background-color: #121212; color: white; font-family: Arial; padding: 2rem; }
        .status, .log, form { margin-top: 1.5rem; }
        .log { white-space: pre-wrap; background: #1e1e1e; padding: 1rem; border-radius: 8px; }
        .btn, button { padding: 10px 20px; background: orange; border: none; color: black; font-weight: bold; cursor: pointer; }
        label { display: block; margin-top: 1rem; }
        select, input[type="file"] { margin-top: 0.5rem; }
      </style>
    </head>
    <body>
      <h1>Admin Panel</h1>
      <p>Last update: {{ last_update }}</p>

      <button class="btn" onclick="triggerUpdate()">Update Website Inventory</button>
      <div class="status" id="status">Status: Ready</div>
      <div class="log" id="log" style="display: none;"></div>

      <hr>

      <form action="/upload-logo" method="POST" enctype="multipart/form-data">
        <label>Upload Logo</label>
        <input type="file" name="file" required>
        <label>Logo Size</label>
        <select name="size">
          <option>100%</option>
          <option>90%</option>
          <option>80%</option>
          <option>70%</option>
          <option>60%</option>
        </select><br><br>
        <button type="submit">Upload Logo</button>
      </form>

      <form action="/upload-background" method="POST" enctype="multipart/form-data">
        <label>Upload Background</label>
        <input type="file" name="file" required><br><br>
        <button type="submit">Upload Background</button>
      </form>

      <form action="/upload-coming-soon" method="POST" enctype="multipart/form-data">
        <label>Upload Coming Soon Image</label>
        <input type="file" name="file" required><br><br>
        <button type="submit">Upload Image</button>
      </form>

      <hr>

      <a href="/admin-carfax" class="btn">Manage Carfax Links</a>
      <a href="/logout" class="btn">Logout</a>

      <script>
        async function triggerUpdate() {
          const status = document.getElementById('status');
          const log = document.getElementById('log');
          status.innerText = 'Status: Uploading...';
          log.style.display = 'none';

          try {
            const res = await fetch('/trigger-update', { method: 'POST' });
            const data = await res.json();
            status.innerText = data.status === 'Update triggered' ? '✅ Update Complete' : '❌ Update Failed';
            log.innerText = data.output || data.error || 'No details.';
            log.style.display = 'block';
          } catch (err) {
            status.innerText = '❌ Error';
            log.innerText = err.message;
            log.style.display = 'block';
          }

          setTimeout(() => { status.innerText = 'Status: Ready'; }, 5000);
        }
      </script>
    </body>
    </html>
    """, last_update=last_update)

@app.route('/login', methods=['POST'])
def login():
    username = request.form.get('username')
    password = request.form.get('password')
    if username == USERNAME and password == PASSWORD:
        session['logged_in'] = True
        return redirect('/admin')
    return 'Invalid credentials', 401

@app.route('/logout')
def logout():
    session.pop('logged_in', None)
    return redirect('/admin')

# --- Trigger Inventory Update ---
@app.route('/trigger-update', methods=['POST'])
def trigger_update():
    try:
        result = subprocess.run(
            ["python3", "/root/inventory-sync/backend/update_inventory.py"],
            capture_output=True,
            text=True,
            check=True
        )
        with open(LOG_FILE, 'a') as f:
            f.write(f"{datetime.datetime.now()} - Inventory updated\n")
        return jsonify({
            "status": "Update triggered",
            "output": result.stdout
        })
    except subprocess.CalledProcessError as e:
        return jsonify({
            "status": "Failed",
            "error": e.stderr
        }), 500

# --- Upload Images ---
def handle_file_upload(request_file, name):
    if request_file and allowed_file(request_file.filename):
        filename = secure_filename(name)
        path = os.path.join(app.config['UPLOAD_FOLDER'], filename)
        request_file.save(path)
        return True
    return False

@app.route('/upload-logo', methods=['POST'])
def upload_logo():
    if 'logged_in' not in session:
        return 'Unauthorized', 403

    file = request.files.get('file')
    size = request.form.get('size', '100%')
    if handle_file_upload(file, 'logo.svg'):
        return redirect('/admin')
    return 'Failed to upload logo', 400

@app.route('/upload-background', methods=['POST'])
def upload_background():
    if 'logged_in' not in session:
        return 'Unauthorized', 403

    file = request.files.get('file')
    if handle_file_upload(file, 'background.jpg'):
        return redirect('/admin')
    return 'Failed to upload background', 400

@app.route('/upload-coming-soon', methods=['POST'])
def upload_coming_soon():
    if 'logged_in' not in session:
        return 'Unauthorized', 403

    file = request.files.get('file')
    if handle_file_upload(file, 'coming-soon.png'):
        return redirect('/admin')
    return 'Failed to upload image', 400

# --- Carfax Management ---
@app.route('/admin-carfax')
def admin_carfax():
    if 'logged_in' not in session:
        return redirect('/admin')

    if os.path.exists(INVENTORY_FILE):
        df = pd.read_csv(INVENTORY_FILE)
        cars = df.to_dict(orient='records')
    else:
        cars = []

    return render_template_string("""
    <!DOCTYPE html>
    <html>
    <head>
      <title>Carfax Links</title>
      <style>
        body { background-color: #121212; color: white; font-family: Arial; padding: 2rem; }
        table { border-collapse: collapse; width: 100%; }
        th, td { border: 1px solid #555; padding: 8px; text-align: left; }
        th { background-color: #333; }
        form { display: inline-block; }
        .btn { background: orange; color: black; padding: 5px 10px; border: none; cursor: pointer; }
      </style>
    </head>
    <body>
      <h1>Carfax Links</h1>
      <table>
        <tr><th>VIN</th><th>Links</th><th>Upload</th></tr>
        {% for car in cars %}
        <tr>
          <td>{{ car['VIN'] }}</td>
          <td>
            {% if car['Links'] and car['Links']|length > 0 %}
              <a href="{{ car['Links'] }}" target="_blank">View Carfax</a>
            {% else %}
              No Carfax URL
            {% endif %}
          </td>
          <td>
            {% if not car['Links'] or car['Links']|length == 0 %}
            <form method="POST" action="/upload-carfax" enctype="multipart/form-data">
              <input type="hidden" name="vin" value="{{ car['VIN'] }}">
              <input type="file" name="file" accept=".pdf" required>
              <button class="btn" type="submit">Upload Carfax</button>
            </form>
            {% endif %}
          </td>
        </tr>
        {% endfor %}
      </table>
      <br>
      <a class="btn" href="/admin">Back to Admin</a>
    </body>
    </html>
    """, cars=cars)

@app.route('/upload-carfax', methods=['POST'])
def upload_carfax():
    if 'logged_in' not in session:
        return 'Unauthorized', 403

    vin = request.form.get('vin')
    file = request.files.get('file')

    if vin and file and allowed_file(file.filename, allowed_ext={'pdf'}):
        last4 = vin[-4:]
        filename = f"{last4}_carfax.pdf"
        path = os.path.join(CARFAX_FOLDER, filename)
        file.save(path)

        # Update CSV
        if os.path.exists(INVENTORY_FILE):
            df = pd.read_csv(INVENTORY_FILE)
            server_ip = get_server_ip()
            carfax_url = f"http://{server_ip}:5000/static/carfax/{filename}"
            df.loc[df['VIN'] == vin, 'Links'] = carfax_url
            df.to_csv(INVENTORY_FILE, index=False)
            print(f"✅ Updated CSV for VIN {vin} with Carfax URL: {carfax_url}")

        return redirect('/admin-carfax')

    return 'Invalid upload', 400

if __name__ == '__main__':
    app.run(host="0.0.0.0", port=5000, debug=True)

from flask import Flask, request, jsonify, session, redirect, url_for, render_template_string
from flask_cors import CORS
from werkzeug.utils import secure_filename
import os
import datetime
import subprocess
import pandas as pd
import socket
from ftplib import FTP

app = Flask(__name__)
app.secret_key = 'super-secret-key'  # TODO: Use environment variable in production
CORS(app)

# Constants
USERNAME = 'admin'
PASSWORD = 'B2010luetooth5!'
UPLOAD_FOLDER = 'static'
CARFAX_FOLDER = os.path.join(UPLOAD_FOLDER, 'carfax')
INVENTORY_FILE = os.path.join(UPLOAD_FOLDER, 'inventory.csv')
LOG_FILE = 'update_log.txt'
FTP_HOST = "ftp.eddysauto.ca"
FTP_USER = "berlinautosales.ca@berlinautosales.ca"
FTP_PASS = "B2010luetooth5!"
ALLOWED_EXTENSIONS = {'pdf'}
os.makedirs(CARFAX_FOLDER, exist_ok=True)

def get_server_ip():
    return socket.gethostbyname(socket.gethostname())

def allowed_file(filename, allowed_ext=ALLOWED_EXTENSIONS):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in allowed_ext

@app.route('/')
def home():
    return redirect('/admin')

# --- Login & Admin Panel ---
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
      </style>
    </head>
    <body>
      <h1>Admin Panel</h1>
      <p>Last update: {{ last_update }}</p>

      <button class="btn" onclick="triggerUpdate()">Update Website Inventory</button>
      <div class="status" id="status">Status: Ready</div>
      <div class="log" id="log" style="display: none;"></div>

      <hr>
      <a href="/admin-carfax" class="btn">Manage Carfax Links</a>
      <a href="/logout" class="btn">Logout</a>

      <script>
        async function triggerUpdate() {
          const status = document.getElementById('status');
          const log = document.getElementById('log');
          status.innerText = 'Status: Updating...';
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

          setTimeout(() => { status.innerText = 'Status: Ready'; }, 10000);
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

@app.route('/trigger-update', methods=['POST'])
def trigger_update():
    try:
        result = subprocess.run(
            ["python3", "/root/inventory-sync/backend/update_inventory.py"],
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
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
            "error": e.stdout
        }), 500

@app.route('/admin-carfax')
def admin_carfax():
    if 'logged_in' not in session:
        return redirect('/admin')

    if not os.path.exists(INVENTORY_FILE):
        return "⚠️ Inventory file not found. Please run 'Update Website Inventory' first."

    # Load cars from CSV
    df = pd.read_csv(INVENTORY_FILE, on_bad_lines='skip')
    df.columns = [c.strip().lower() for c in df.columns]
    cars = df.to_dict(orient='records')

    # Check FTP for Carfax PDFs
    carfax_files = set()
    with FTP(FTP_HOST) as ftp:
        ftp.login(FTP_USER, FTP_PASS)
        try:
            ftp.cwd('carfax')
            carfax_files = set(ftp.nlst())
        except Exception:
            pass

    # Add Carfax link status
    for car in cars:
        vin = str(car['vin'])
        last6 = vin[-6:]
        filename = f"{last6}_carfax.pdf"
        if filename in carfax_files:
            car['carfax_url'] = f"http://{FTP_HOST}/carfax/{filename}"
        else:
            car['carfax_url'] = None

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
        .btn { background: orange; color: black; padding: 5px 10px; border: none; cursor: pointer; }
      </style>
    </head>
    <body>
      <h1>Carfax Links</h1>
      <table>
        <tr><th>VIN</th><th>Carfax PDF</th><th>Upload</th></tr>
        {% for car in cars %}
        <tr>
          <td>{{ car['vin'] }}</td>
          <td>
            {% if car['carfax_url'] %}
              <a href="{{ car['carfax_url'] }}" target="_blank">View Carfax</a>
            {% else %}
              No Carfax
            {% endif %}
          </td>
          <td>
            {% if not car['carfax_url'] %}
            <form method="POST" action="/upload-carfax" enctype="multipart/form-data">
              <input type="hidden" name="vin" value="{{ car['vin'] }}">
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

    if vin and file and allowed_file(file.filename):
        last6 = vin[-6:]
        filename = f"{last6}_carfax.pdf"
        path = os.path.join(CARFAX_FOLDER, filename)
        file.save(path)
        return redirect('/admin-carfax')

    return 'Invalid upload', 400

if __name__ == '__main__':
    app.run(host="0.0.0.0", port=5000, debug=True)

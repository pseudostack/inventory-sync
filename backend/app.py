# --- app.py ---
from flask import Flask, request, jsonify, session, redirect, url_for, render_template_string
from flask_cors import CORS
from werkzeug.utils import secure_filename
import os
import datetime
import subprocess
import pandas as pd
import socket
import logging
from ftplib import FTP

app = Flask(__name__)
app.secret_key = 'super-secret-key'
CORS(app)

USERNAME = 'admin'
PASSWORD = 'B2010luetooth5!'
UPLOAD_FOLDER = 'static'
CARFAX_FOLDER = os.path.join(UPLOAD_FOLDER, 'carfax')
INVENTORY_FILE = os.path.join(UPLOAD_FOLDER, 'inventory.csv')
AVAILABLE_CARFAX_CSV = os.path.join(UPLOAD_FOLDER, 'available_carfax.csv')
LOG_FILE = 'update_log.txt'
FTP_HOST = "ftp.eddysauto.ca"
FTP_USER = "berlinautosales.ca@berlinautosales.ca"
FTP_PASS = "B2010luetooth5!"
FTP_CARFAX_DIR = "carfax"
FTP_TARGET_PATH = "inventory.csv"
ALLOWED_EXTENSIONS = {'pdf'}
os.makedirs(CARFAX_FOLDER, exist_ok=True)

logging.basicConfig(level=logging.INFO)

def allowed_file(filename):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS

@app.route('/')
def home():
    return redirect('/admin')

@app.route('/admin')
def admin_panel():
    if 'logged_in' not in session:
        return render_template_string("""
        <h2>Login</h2>
        <form method="POST" action="/login">
            <input name="username"><br>
            <input type="password" name="password"><br>
            <button type="submit">Login</button>
        </form>
        """)

    last_update = 'Never'
    if os.path.exists(LOG_FILE):
        with open(LOG_FILE) as f:
            lines = f.readlines()
            if lines:
                last_update = lines[-1]

    return render_template_string("""
    <h1>Admin Panel</h1>
    <p>Last update: {{ last_update }}</p>
    <button onclick="triggerUpdate()">Update Website Inventory</button>
    <div id="status">Status: Ready</div>
    <pre id="log" style="display:none;"></pre>
    <a href="/admin-carfax">Manage Carfax Links</a> | <a href="/logout">Logout</a>
    <script>
        async function triggerUpdate() {
            const status = document.getElementById('status');
            const log = document.getElementById('log');
            status.innerText = 'Updating...';
            try {
                const res = await fetch('/trigger-update', { method: 'POST' });
                const json = await res.json();
                status.innerText = json.status;
                log.innerText = json.output || json.error || 'Done';
                log.style.display = 'block';
            } catch (e) {
                status.innerText = 'Error';
                log.innerText = e.toString();
                log.style.display = 'block';
            }
        }
    </script>
    """, last_update=last_update)

@app.route('/login', methods=['POST'])
def login():
    if request.form['username'] == USERNAME and request.form['password'] == PASSWORD:
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
        return jsonify({"status": "Update Complete", "output": result.stdout})
    except subprocess.CalledProcessError as e:
        return jsonify({"status": "Failed", "error": e.stdout}), 500

@app.route('/admin-carfax')
def admin_carfax():
    if 'logged_in' not in session:
        return redirect('/admin')

    # --- Pull inventory and list of carfax files ---
    with FTP(FTP_HOST) as ftp:
        ftp.login(FTP_USER, FTP_PASS)
        with open(INVENTORY_FILE, 'wb') as f:
            ftp.retrbinary(f"RETR {FTP_TARGET_PATH}", f.write)

        try:
            ftp.cwd(FTP_CARFAX_DIR)
            carfax_files = set(ftp.nlst())
        except:
            carfax_files = set()

    df = pd.read_csv(INVENTORY_FILE, on_bad_lines='skip')
    df.columns = [c.strip().lower() for c in df.columns]
    cars = df.to_dict(orient='records')

    for car in cars:
        vin = str(car.get("vin", ""))
        last6 = vin[-6:]
        filename = f"{last6}_carfax.pdf"
        car['carfax_url'] = f"https://berlinautosales.ca/carfax/{filename}" if filename in carfax_files else None

    return render_template_string("""
    <h1>Carfax Links</h1>
    <table border=1>
      <tr><th>VIN</th><th>Carfax</th><th>Upload</th></tr>
      {% for car in cars %}
        <tr>
          <td>{{ car['vin'] }}</td>
          <td>
            {% if car['carfax_url'] %}
              <a href="{{ car['carfax_url'] }}" target="_blank">View</a>
            {% else %}
              No Carfax
            {% endif %}
          </td>
          <td>
            <form method="POST" action="/upload-carfax" enctype="multipart/form-data">
              <input type="hidden" name="vin" value="{{ car['vin'] }}">
              <input type="file" name="file" accept=".pdf">
              <button type="submit">Upload</button>
            </form>
          </td>
        </tr>
      {% endfor %}
    </table>
    <br><a href="/admin">Back</a>
    """, cars=cars)

@app.route('/upload-carfax', methods=['POST'])
def upload_carfax():
    if 'logged_in' not in session:
        return 'Unauthorized', 403

    vin = request.form.get('vin')
    file = request.files.get('file')
    if not (vin and file and allowed_file(file.filename)):
        return 'Invalid upload', 400

    last6 = vin[-6:]
    filename = f"{last6}_carfax.pdf"
    local_path = os.path.join(CARFAX_FOLDER, filename)
    file.save(local_path)

    # Upload to FTP
    with FTP(FTP_HOST) as ftp:
        ftp.login(FTP_USER, FTP_PASS)
        try:
            ftp.cwd(FTP_CARFAX_DIR)
        except:
            ftp.mkd(FTP_CARFAX_DIR)
            ftp.cwd(FTP_CARFAX_DIR)
        with open(local_path, 'rb') as f:
            ftp.storbinary(f"STOR {filename}", f)

    return redirect('/admin-carfax')

if __name__ == '__main__':
    app.run(host="0.0.0.0", port=5000, debug=True)

from flask import Flask, render_template, request, jsonify, send_file, Response, redirect, url_for, session
import os
import time
import psutil
import pyautogui
from wakeonlan import send_magic_packet
from pycaw.pycaw import AudioUtilities, IAudioEndpointVolume
from comtypes import CLSCTX_ALL
from ctypes import POINTER, cast
import io
import spotipy
from spotipy.oauth2 import SpotifyOAuth
import json

app = Flask(__name__)
app.secret_key = os.urandom(24)

# ============================
# CONFIGURACIÓN DE SPOTIFY
# ============================
SPOTIPY_CLIENT_ID = "13b12dda749c4b16bf5c7fda2e5dd4e5"
SPOTIPY_CLIENT_SECRET = "395bd163c7ac4e468fc3b123628f031c"
SPOTIPY_REDIRECT_URI = "http://127.0.0.1:5000/callback"
SPOTIPY_SCOPE = "user-library-read user-read-playback-state user-modify-playback-state"

sp_oauth = SpotifyOAuth(client_id=SPOTIPY_CLIENT_ID,
                        client_secret=SPOTIPY_CLIENT_SECRET,
                        redirect_uri=SPOTIPY_REDIRECT_URI,
                        scope=SPOTIPY_SCOPE)

# ============================
# CONFIGURACIÓN DEL AUDIO Y SISTEMA
# ============================
devices = AudioUtilities.GetSpeakers()
interface = devices.Activate(IAudioEndpointVolume._iid_, CLSCTX_ALL, None)
volume = cast(interface, POINTER(IAudioEndpointVolume))

MAC_ADDRESS = "C8-15-DC-FB-51-81"
IPV4_LOCAL = "192.168.1.27"

start_time = time.time()
last_check = 0
music_time_accum = 0

# ============================
# SISTEMA DE ACCESO POR KEY
# ============================
KEYS_FILE = 'valid_keys.json'

def load_keys():
    if not os.path.exists(KEYS_FILE):
        return {}
    with open(KEYS_FILE, 'r') as f:
        return json.load(f)

def save_keys(keys):
    with open(KEYS_FILE, 'w') as f:
        json.dump(keys, f, indent=4)

@app.route('/login_key', methods=['GET', 'POST'])
def login_key():
    if request.method == 'POST':
        key = request.form.get('key')
        keys = load_keys()
        if key in keys and not keys[key]:
            keys[key] = True
            save_keys(keys)
            session['paid_key'] = True
            return redirect(url_for('index'))
        else:
            error = "Key no válida o ya usada."
            return render_template('login_key.html', error=error)
    return render_template('login_key.html')

@app.route('/key_status')
def key_status():
    return jsonify({"authenticated": bool(session.get('paid_key'))})

@app.route('/logout_key')
def logout_key():
    session.pop('paid_key', None)
    return redirect(url_for('login_key'))

# ============================
# FILTRO DE TIEMPO
# ============================
@app.template_filter('format_time')
def format_time_filter(seconds):
    minutes = int(seconds // 60)
    remaining = int(seconds % 60)
    return f"{minutes}:{remaining:02}"

# ============================
# RUTAS PRINCIPALES
# ============================
@app.route('/')
def index():
    if not session.get('paid_key'):
        return redirect(url_for('login_key'))

    current_volume = int(volume.GetMasterVolumeLevelScalar() * 100)
    cpu = psutil.cpu_percent()
    memory = psutil.virtual_memory().percent
    disk = psutil.disk_usage('/').percent

    token_info = session.get('token_info', None)
    track = None
    if token_info:
        sp = spotipy.Spotify(auth=token_info['access_token'])
        current_playback = sp.current_playback()
        if current_playback and current_playback['is_playing']:
            track = {
                "name": current_playback['item']['name'],
                "artist": current_playback['item']['artists'][0]['name'],
                "time_played": current_playback['progress_ms'] / 1000,
                "duration": current_playback['item']['duration_ms'] / 1000,
                "album_image": current_playback['item']['album']['images'][0]['url']
            }

    return render_template('index.html', volume=current_volume, cpu=cpu, memory=memory, disk=disk, track=track, token_info=token_info)

@app.route('/start_screen_stream')
def start_screen_stream():
    return redirect(url_for('live_screen'))

@app.route('/live_screen')
def live_screen():
    def generate():
        while True:
            screenshot = pyautogui.screenshot()
            img_byte_array = io.BytesIO()
            screenshot.save(img_byte_array, format='JPEG')
            img_byte_array.seek(0)
            yield (b'--frame\r\n'
                   b'Content-Type: image/jpeg\r\n\r\n' + img_byte_array.read() + b'\r\n\r\n')
            time.sleep(0.1)
    return Response(generate(), mimetype='multipart/x-mixed-replace; boundary=frame')

# ============================
# SPOTIFY AUTH
# ============================
@app.route('/login')
def login():
    auth_url = sp_oauth.get_authorize_url()
    return redirect(auth_url)

@app.route('/callback')
def callback():
    token_info = sp_oauth.get_access_token(request.args['code'])
    session['token_info'] = token_info
    return redirect(url_for('index'))

@app.route('/current_track')
def current_track():
    global last_check, music_time_accum
    token_info = session.get('token_info')
    if not token_info:
        return jsonify({"error": "User not logged in"})

    sp = spotipy.Spotify(auth=token_info['access_token'])
    playback = sp.current_playback()

    if playback and playback.get("item"):
        if playback['is_playing']:
            now = time.time()
            if last_check > 0:
                delta = now - last_check
                music_time_accum += delta
            last_check = now
        else:
            last_check = 0

        return jsonify({
            "name": playback['item']['name'],
            "artist": playback['item']['artists'][0]['name'],
            "album": playback['item']['album']['name'],
            "time_played": playback['progress_ms'] / 1000,
            "duration": playback['item']['duration_ms'] / 1000,
            "album_image": playback['item']['album']['images'][0]['url'],
            "playing": playback['is_playing']
        })
    else:
        last_check = 0
        return jsonify({"error": "No track is currently playing"})

@app.route('/stats')
def get_stats():
    uptime = int(time.time() - start_time)
    music = int(music_time_accum)
    cpu = psutil.cpu_percent()
    memory = psutil.virtual_memory().percent
    disk = psutil.disk_usage('/').percent
    return jsonify({
        "uptime": uptime,
        "music_time": music,
        "cpu": cpu,
        "memory": memory,
        "disk": disk
    })

@app.route('/pause', methods=['POST'])
@app.route('/play_pause', methods=['POST'])
@app.route('/next', methods=['POST'])
@app.route('/previous', methods=['POST'])
def control_music():
    token_info = session.get('token_info')
    if not token_info:
        return jsonify({"error": "User not logged in"})
    sp = spotipy.Spotify(auth=token_info['access_token'])

    if request.path == '/pause':
        sp.pause_playback()
    elif request.path == '/play_pause':
        playback = sp.current_playback()
        if playback and playback['is_playing']:
            sp.pause_playback()
        else:
            sp.start_playback()
    elif request.path == '/next':
        sp.next_track()
    elif request.path == '/previous':
        sp.previous_track()

    return jsonify(success=True)

@app.route('/set_volume', methods=['POST'])
def set_volume():
    level = float(request.json['level']) / 100
    volume.SetMasterVolumeLevelScalar(level, None)
    return jsonify(success=True)

@app.route('/vol_up', methods=['POST'])
def vol_up():
    current = volume.GetMasterVolumeLevelScalar()
    volume.SetMasterVolumeLevelScalar(min(current + 0.05, 1.0), None)
    return jsonify(success=True)

@app.route('/vol_down', methods=['POST'])
def vol_down():
    current = volume.GetMasterVolumeLevelScalar()
    volume.SetMasterVolumeLevelScalar(max(current - 0.05, 0.0), None)
    return jsonify(success=True)

@app.route('/vol_max', methods=['POST'])
def vol_max():
    volume.SetMasterVolumeLevelScalar(1.0, None)
    return jsonify(success=True)

@app.route('/vol_min', methods=['POST'])
def vol_min():
    volume.SetMasterVolumeLevelScalar(0.0, None)
    return jsonify(success=True)

@app.route('/mute', methods=['POST'])
def mute():
    volume.SetMasterVolumeLevelScalar(0.0, None)
    return jsonify(success=True)

@app.route('/poweroff', methods=['POST'])
def poweroff():
    os.system("shutdown /s /t 0")
    return jsonify(success=True)

@app.route('/reboot', methods=['POST'])
def reboot():
    os.system("shutdown /r /t 0")
    return jsonify(success=True)

@app.route('/logout', methods=['POST'])
def logout():
    os.system("shutdown /l")
    return jsonify(success=True)

@app.route('/hibernate', methods=['POST'])
def hibernate():
    os.system("shutdown /h")
    return jsonify(success=True)

@app.route('/wol', methods=['POST'])
def wol():
    send_magic_packet(MAC_ADDRESS, ip_address=IPV4_LOCAL)
    return jsonify(success=True)

@app.route('/lock', methods=['POST'])
def lock():
    os.system("rundll32.exe user32.dll,LockWorkStation")
    return jsonify(success=True)

@app.route('/screen_off', methods=['POST'])
def screen_off():
    import ctypes
    ctypes.windll.user32.SendMessageW(65535, 274, 61808, 2)
    return jsonify(success=True)

@app.route('/screenshot')
def screenshot():
    filename = f"screenshot_{int(time.time())}.png"
    pyautogui.screenshot(filename)
    return send_file(filename, mimetype='image/png')

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000)

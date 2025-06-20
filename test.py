from flask import Flask, render_template_string, Response, request, jsonify, send_file, redirect, url_for, session, send_file
import io
import time
import threading
import os
import psutil
import base64
from picamera import PiCamera
import tempfile
import shutil
from datetime import datetime

app = Flask(__name__)
app.secret_key = 'supersecretkey'  # Needed for session

g_camera = None
g_recording = False
g_recording_file = None
g_recording_lock = threading.Lock()
g_streaming = False
g_connected = False  # Camera is disconnected by default
g_monitoring = False  # Monitoring mode flag
last_image_data = None
last_image_size = None
last_image_width = None
last_image_height = None

# Set high performance as default
DEFAULT_SETTINGS = {
    'resolution': [3200, 2400],  # Highest supported
    'compression': 'Very High',  # Highest quality
    'fps': '90',                # High FPS (adjust if your camera supports higher)
    'image': 'Color',
    'rotation': '0',
    'effect': 'Normal',
    'sharpness': 'High'
}

TEMP_DIR = os.path.join(tempfile.gettempdir(), 'smartcam_images')
os.makedirs(TEMP_DIR, exist_ok=True)

# Template for the UI
TEMPLATE = '''
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>StarckCam - Raspberry Pi Camera Control</title>
    <style>
        * {
            margin: 0;
            padding: 0;
            box-sizing: border-box;
        }

        body {
            font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
            background: linear-gradient(to bottom right, #0f172a, #1e3a8a, #0f172a);
            min-height: 100vh;
            color: white;
        }

        .container {
            max-width: 1280px;
            margin: 0 auto;
            padding: 1rem;
        }

        /* Header Styles */
        .header {
            margin-bottom: 2rem;
        }

        .header-content {
            display: flex;
            align-items: center;
            justify-content: space-between;
        }

        .title-section {
            display: flex;
            flex-direction: column;
        }

        .main-title {
            font-size: 1.875rem;
            font-weight: bold;
            color: white;
            margin-bottom: 0.25rem;
        }

        .subtitle {
            color: #bfdbfe;
            font-size: 0.875rem;
        }

        .connect-btn {
            background: #3b82f6;
            color: white;
            border: none;
            padding: 0.625rem 1rem;
            border-radius: 0.375rem;
            font-weight: 500;
            font-size: 0.875rem;
            cursor: pointer;
            transition: background-color 0.2s;
        }

        .connect-btn:hover {
            background: #2563eb;
        }

        /* Main Content Layout */
        .main-content {
            display: grid;
            grid-template-columns: 2fr 1fr;
            gap: 1.5rem;
            margin-bottom: 1.5rem;
        }

        /* Card Styles */
        .card {
            background: rgba(30, 41, 59, 0.5);
            border: 1px solid #475569;
            border-radius: 0.5rem;
            backdrop-filter: blur(8px);
            box-shadow: 0 4px 6px -1px rgba(0, 0, 0, 0.1);
        }

        .card-header {
            padding: 1.5rem;
            border-bottom: 1px solid rgba(71, 85, 105, 0.3);
        }

        .card-title {
            font-size: 1.25rem;
            font-weight: 600;
            color: white;
        }

        .card-content {
            padding: 1.5rem;
        }

        /* Live Preview Section */
        .live-preview-section {
            grid-column: 1;
        }

        .video-container {
            position: relative;
            width: 100%;
            aspect-ratio: 16/9;
            background: #0f172a;
            border-radius: 0.5rem;
            border: 1px solid #475569;
            overflow: hidden;
        }

        .camera-disconnected {
            position: absolute;
            inset: 0;
            display: flex;
            flex-direction: column;
            align-items: center;
            justify-content: center;
            color: #94a3b8;
        }

        .disconnected-title {
            font-size: 1.125rem;
            font-weight: 500;
            margin-bottom: 0.5rem;
        }

        .disconnected-subtitle {
            font-size: 0.875rem;
            opacity: 0.75;
        }

        /* Sidebar */
        .sidebar {
            display: flex;
            flex-direction: column;
            gap: 1.5rem;
        }

        /* System Status Styles */
        .status-item {
            display: flex;
            align-items: center;
            justify-content: space-between;
            margin-bottom: 1rem;
        }

        .status-label {
            display: flex;
            align-items: center;
            gap: 0.5rem;
        }

        .status-icon {
            width: 1rem;
            height: 1rem;
        }

        .status-text {
            font-size: 0.875rem;
            font-weight: 500;
        }

        .status-text.cpu {
            color: #60a5fa;
        }

        .status-text.memory {
            color: #a78bfa;
        }

        .status-text.temperature {
            color: #fb923c;
        }

        .status-text.network {
            color: #4ade80;
        }

        .status-value {
            font-family: 'Courier New', monospace;
            font-size: 0.875rem;
            color: white;
        }

        .status-footer {
            border-top: 1px solid #475569;
            padding-top: 0.75rem;
            margin-top: 0.75rem;
            display: grid;
            grid-template-columns: 1fr 1fr;
            gap: 0.5rem;
        }

        .footer-item {
            font-size: 0.75rem;
        }

        .footer-label {
            color: #94a3b8;
            margin-bottom: 0.25rem;
        }

        .footer-value {
            color: white;
            font-family: 'Courier New', monospace;
        }

        /* Camera Settings Styles */
        .setting-group {
            margin-bottom: 1.5rem;
        }

        .setting-label {
            display: block;
            color: #cbd5e1;
            font-size: 0.875rem;
            font-weight: 500;
            margin-bottom: 0.5rem;
        }

        .select-container {
            position: relative;
        }

        .custom-select {
            width: 100%;
            height: 2.5rem;
            background: #374151;
            border: 1px solid #475569;
            border-radius: 0.375rem;
            color: white;
            padding: 0 0.75rem;
            font-size: 0.875rem;
            cursor: pointer;
            appearance: none;
            background-image: url("data:image/svg+xml,%3csvg xmlns='http://www.w3.org/2000/svg' fill='none' viewBox='0 0 20 20'%3e%3cpath stroke='%236b7280' stroke-linecap='round' stroke-linejoin='round' stroke-width='1.5' d='m6 8 4 4 4-4'/%3e%3c/svg%3e");
            background-position: right 0.5rem center;
            background-repeat: no-repeat;
            background-size: 1.5rem 1.5rem;
        }

        .custom-select:focus {
            outline: 2px solid #3b82f6;
            outline-offset: 2px;
        }

        .slider-container {
            padding: 0.5rem 0;
        }

        .custom-slider {
            width: 100%;
            height: 0.5rem;
            background: #374151;
            border-radius: 0.25rem;
            outline: none;
            appearance: none;
            cursor: pointer;
        }

        .custom-slider::-webkit-slider-thumb {
            appearance: none;
            width: 1.25rem;
            height: 1.25rem;
            background: white;
            border: 2px solid #3b82f6;
            border-radius: 50%;
            cursor: pointer;
        }

        .custom-slider::-moz-range-thumb {
            width: 1.25rem;
            height: 1.25rem;
            background: white;
            border: 2px solid #3b82f6;
            border-radius: 50%;
            cursor: pointer;
            border: none;
        }

        .settings-footer {
            border-top: 1px solid #475569;
            padding-top: 0.75rem;
            display: grid;
            grid-template-columns: 1fr 1fr;
            gap: 0.5rem;
            font-size: 0.75rem;
        }

        /* Controls Section */
        .controls-section {
            margin-top: 1.5rem;
        }

        .controls-grid {
            display: flex;
            flex-wrap: wrap;
            gap: 1rem;
            justify-content: center;
        }

        .control-btn {
            background: #3b82f6;
            color: white;
            border: none;
            padding: 0.625rem 1.25rem;
            border-radius: 0.375rem;
            font-weight: 600;
            font-size: 1rem;
            cursor: pointer;
            transition: background-color 0.2s;
            box-shadow: 0 2px 4px rgba(30,41,59,0.08);
            flex: 1 1 200px;
            min-width: 180px;
            max-width: 100%;
            margin: 0;
        }
        .control-btn:hover:not(:disabled) {
            background: #2563eb;
        }
        .control-btn:disabled {
            opacity: 0.5;
            cursor: not-allowed;
        }

        .control-btn.primary {
            background: #3b82f6;
            color: white;
        }

        .control-btn.primary:hover:not(:disabled) {
            background: #2563eb;
        }

        .control-btn.secondary {
            background: #6b7280;
            color: white;
        }

        .control-btn.secondary:hover:not(:disabled) {
            background: #4b5563;
        }

        .control-btn.outline-blue {
            background: transparent;
            border-color: #2563eb;
            color: #60a5fa;
        }

        .control-btn.outline-blue:hover:not(:disabled) {
            background: #2563eb;
            color: white;
        }

        .control-btn.outline-gray {
            background: transparent;
            border-color: #475569;
            color: #94a3b8;
        }

        .control-btn.outline-gray:hover:not(:disabled) {
            background: #475569;
            color: white;
        }

        /* Responsive Design */
        @media (max-width: 1024px) {
            .main-content {
                grid-template-columns: 1fr;
            }
        }

        @media (max-width: 640px) {
            .container {
                padding: 0.5rem;
            }

            .header-content {
                flex-direction: column;
                gap: 1rem;
                align-items: flex-start;
            }

            .main-title {
                font-size: 1.5rem;
            }

            .card-header,
            .card-content {
                padding: 1rem;
            }

            .controls-grid {
                flex-direction: column;
                gap: 0.75rem;
            }

            .control-btn {
                min-width: 0;
                width: 100%;
            }

            .status-footer {
                grid-template-columns: 1fr;
                gap: 1rem;
            }
        }

        .nav-buttons {
            display: flex;
            justify-content: flex-start;
            gap: 1rem;
            margin-bottom: 1rem;
        }
        .nav-btn {
            background: #3b82f6;
            color: white;
            border: none;
            padding: 0.625rem 1.25rem;
            border-radius: 0.375rem;
            font-weight: 600;
            font-size: 1rem;
            cursor: pointer;
            transition: background-color 0.2s;
            box-shadow: 0 2px 4px rgba(30,41,59,0.08);
        }
        .nav-btn:hover {
            background: #2563eb;
        }
        .captured-image-container {
            margin-top: 1.5rem;
            display: flex;
            flex-direction: column;
            align-items: center;
        }
        #captured-image {
            width: 100%;
            max-width: 480px;
            height: 270px;
            object-fit: contain;
            border-radius: 0.5rem;
            border: 1px solid #475569;
            background: #0f172a;
            margin-bottom: 0.5rem;
        }
    </style>
</head>
<body>
    <div class="container">
        <!-- Header -->
        <header class="header">
            <div class="header-content">
                <div class="title-section">
                    <h1 class="main-title">StarckCam</h1>
                    <p class="subtitle">Raspberry Pi Camera Control</p>
                </div>
               
                <form method="POST" action="/connect" style="display:inline;">
                    <button type="submit" class="connect-btn" {% if g_connected %}disabled{% endif %}>Connect Camera</button>
                </form>
            </div>
        </header>
        <!-- Main Content -->
        <main class="main-content">
            <section class="live-preview-section">
                <div class="card">
                    <div class="card-header">
                        <h3 class="card-title">Live Preview</h3>
                    </div>
                    <div class="card-content">
                        <div class="video-container">
                            {% if not g_connected %}
                            <div class="camera-disconnected">
                                <p class="disconnected-title">Camera Disconnected</p>
                                <p class="disconnected-subtitle">Connect your camera to start streaming</p>
                            </div>
                            {% elif monitoring %}
                            <img id="video-stream" src="/video_stream" style="max-width:100%; height:auto;" />
                            {% else %}
                            <div class="camera-disconnected">
                                <p class="disconnected-title">Monitor Mode Off</p>
                                <p class="disconnected-subtitle">Start monitor mode to view live stream</p>
                            </div>
                            {% endif %}
                        </div>
                    </div>
                </div>
                {% if last_image %}
                <div class="captured-image-container">
                    <img id="captured-image" src="/get_image/{{ last_image }}" />
                    <div class="status-footer">
                        <div class="footer-item">
                            <div class="footer-label">Size</div>
                            <div class="footer-value">{{ last_image_size }} KB</div>
                        </div>
                        <div class="footer-item">
                            <div class="footer-label">Dimensions</div>
                            <div class="footer-value">{{ last_image_width }}×{{ last_image_height }}</div>
                        </div>
                    </div>
                </div>
                {% endif %}
                <section class="controls-section">
                    <div class="card">
                        <div class="card-header">
                            <h3 class="card-title">Camera Controls</h3>
                        </div>
                        <div class="card-content">
                            <div class="controls-grid">
                                <form method="POST" action="/start_monitor" style="display:inline;">
                                    <button type="submit" class="control-btn" {% if not g_connected or monitoring %}disabled{% endif %}>Start Stream</button>
                                </form>
                                <form method="POST" action="/stop_monitor" style="display:inline;">
                                    <button type="submit" class="control-btn" {% if not g_connected or not monitoring %}disabled{% endif %}>Stop Stream</button>
                                </form>
                                <form method="POST" action="/capture" style="display:inline;">
                                    <button type="submit" class="control-btn" {% if not g_connected %}disabled{% endif %}>Capture Image</button>
                                </form>
                                <form method="POST" action="/disconnect" style="display:inline;">
                                    <button type="submit" class="control-btn" {% if not g_connected %}disabled{% endif %}>Disconnect</button>
                                </form>
                            </div>
                        </div>
                    </div>
                </section>
            </section>
            <aside class="sidebar">
                <!-- System Status Card -->
                <div class="card">
                    <div class="card-header">
                        <h3 class="card-title">System Status</h3>
                    </div>
                    <div class="card-content">
                        <div class="status-item">
                            <div class="status-label">
                                <span class="status-text cpu">CPU Usage</span>
                            </div>
                            <span class="status-value">{{ status.cpu }}%</span>
                        </div>
                        <div class="status-item">
                            <div class="status-label">
                                <span class="status-text memory">Memory</span>
                            </div>
                            <span class="status-value">{{ status.mem }}%</span>
                        </div>
                        <div class="status-item">
                            <div class="status-label">
                                <span class="status-text temperature">Temperature</span>
                            </div>
                            <span class="status-value">{% if status.temperature %}{{ status.temperature }}°C{% else %}N/A{% endif %}</span>
                        </div>
                        <div class="status-item">
                            <div class="status-label">
                                <span class="status-text network">Network</span>
                            </div>
                            <span class="status-value">{{ status.transmitted }}</span>
                        </div>
                        <div class="status-footer">
                            <div class="footer-item">
                                <div class="footer-label">Last Access</div>
                                <div class="footer-value">{{ status.last_access }}</div>
                            </div>
                            <div class="footer-item">
                                <div class="footer-label">Latency</div>
                                <div class="footer-value">{{ status.latency }} ms</div>
                            </div>
                        </div>
                    </div>
                </div>
                <!-- Camera Settings Card -->
                <div class="card">
                    <div class="card-header">
                        <h3 class="card-title">Camera Settings</h3>
                    </div>
                    <div class="card-content">
                        <form method="POST" action="/capture">
                            <div class="setting-group">
                                <label class="setting-label">Resolution</label>
                                <div class="select-container">
                                    <select name="resolution" class="custom-select">
                                        <option value="1280x960" {% if settings.resolution[0] == 1280 and settings.resolution[1] == 960 %}selected{% endif %}>1280×960</option>
                                        <option value="1920x1080" {% if settings.resolution[0] == 1920 and settings.resolution[1] == 1080 %}selected{% endif %}>1920×1080</option>
                                        <option value="1920x1440" {% if settings.resolution[0] == 1920 and settings.resolution[1] == 1440 %}selected{% endif %}>1920×1440</option>
                                        <option value="2592x1944" {% if settings.resolution[0] == 2592 and settings.resolution[1] == 1944 %}selected{% endif %}>2592×1944</option>
                                        <option value="3200x2400" {% if settings.resolution[0] == 3200 and settings.resolution[1] == 2400 %}selected{% endif %}>3200×2400</option>
                                    </select>
                                </div>
                            </div>

                            <div class="setting-group">
                                <label class="setting-label">Compression</label>
                                <div class="select-container">
                                    <select name="compression" class="custom-select">
                                        <option value="Low" {% if settings.compression == 'Low' %}selected{% endif %}>Low</option>
                                        <option value="Medium" {% if settings.compression == 'Medium' %}selected{% endif %}>Medium</option>
                                        <option value="High" {% if settings.compression == 'High' %}selected{% endif %}>High</option>
                                        <option value="Very High" {% if settings.compression == 'Very High' %}selected{% endif %}>Very High</option>
                                    </select>
                                </div>
                            </div>

                            <div class="setting-group">
                                <label class="setting-label">FPS</label>
                                <div class="select-container">
                                    <select name="fps" class="custom-select">
                                        <option value="30" {% if settings.fps == '30' %}selected{% endif %}>30</option>
                                        <option value="60" {% if settings.fps == '60' %}selected{% endif %}>60</option>
                                        <option value="120" {% if settings.fps == '120' %}selected{% endif %}>120</option>
                                        <option value="240" {% if settings.fps == '240' %}selected{% endif %}>240</option>
                                        <option value="480" {% if settings.fps == '480' %}selected{% endif %}>480</option>
                                        <option value="960" {% if settings.fps == '960' %}selected{% endif %}>960</option>
                                    </select>
                                </div>
                            </div>

                            <div class="setting-group">
                                <label class="setting-label">Image Mode</label>
                                <div class="select-container">
                                    <select name="image" class="custom-select">
                                        <option value="Color" {% if settings.image == 'Color' %}selected{% endif %}>Color</option>
                                        <option value="Gray" {% if settings.image == 'Gray' %}selected{% endif %}>Gray</option>
                                    </select>
                                </div>
                            </div>

                            <div class="setting-group">
                                <label class="setting-label">Rotation</label>
                                <div class="select-container">
                                    <select name="rotation" class="custom-select">
                                        <option value="0" {% if settings.rotation == '0' %}selected{% endif %}>0°</option>
                                        <option value="90" {% if settings.rotation == '90' %}selected{% endif %}>90°</option>
                                        <option value="180" {% if settings.rotation == '180' %}selected{% endif %}>180°</option>
                                        <option value="270" {% if settings.rotation == '270' %}selected{% endif %}>270°</option>
                                    </select>
                                </div>
                            </div>

                            <div class="setting-group">
                                <label class="setting-label">Effect</label>
                                <div class="select-container">
                                    <select name="effect" class="custom-select">
                                        <option value="Normal" {% if settings.effect == 'Normal' %}selected{% endif %}>Normal</option>
                                        <option value="Negative" {% if settings.effect == 'Negative' %}selected{% endif %}>Negative</option>
                                    </select>
                                </div>
                            </div>

                            <div class="setting-group">
                                <label class="setting-label">Sharpness</label>
                                <div class="select-container">
                                    <select name="sharpness" class="custom-select">
                                        <option value="Normal" {% if settings.sharpness == 'Normal' %}selected{% endif %}>Normal</option>
                                        <option value="Medium" {% if settings.sharpness == 'Medium' %}selected{% endif %}>Medium</option>
                                        <option value="High" {% if settings.sharpness == 'High' %}selected{% endif %}>High</option>
                                    </select>
                                </div>
                            </div>
                        </form>
                    </div>
                </div>
            </aside>
        </main>
        {% if warning %}
        <div class="card" style="margin-top: 1rem; background: rgba(220, 38, 38, 0.1); border-color: #dc2626;">
            <div class="card-content">
                <p style="color: #fca5a5;">{{ warning }}</p>
            </div>
        </div>
        {% endif %}
        {% if success %}
        <div class="card" style="margin-top: 1rem; background: rgba(34, 197, 94, 0.1); border-color: #22c55e;">
            <div class="card-content">
                <p style="color: #86efac;">{{ success }}</p>
            </div>
        </div>
        {% endif %}
    </div>
</body>
</html>
'''

def get_camera_settings():
    """Get camera settings from session or return defaults"""
    return session.get('camera_settings', DEFAULT_SETTINGS)

def save_camera_settings(settings):
    """Save camera settings to session"""
    session['camera_settings'] = settings

def get_camera():
    global g_camera
    if g_camera is None:
        try:
            # Add safety delay before camera initialization
            time.sleep(0.5)
            g_camera = PiCamera()
            # Initialize with safe default values
            g_camera.resolution = (1920, 1080)
            g_camera.framerate = 30
            g_camera.rotation = 0
            g_camera.image_effect = 'none'
            g_camera.color_effects = None
            g_camera.sharpness = 0
            # Set conservative camera settings
            g_camera.exposure_mode = 'auto'
            g_camera.awb_mode = 'auto'
            g_camera.meter_mode = 'average'
            g_camera.iso = 0  # Auto ISO
            g_camera.video_stabilization = False
            # Apply saved settings after initialization
            settings = get_camera_settings()
            apply_camera_settings(g_camera, settings)
        except Exception as e:
            print(f"Error initializing camera: {e}")
            g_camera = None
    return g_camera

def get_temp():
    # Try to get CPU temp (Pi)
    try:
        with open('/sys/class/thermal/thermal_zone0/temp', 'r') as f:
            temp = int(f.read()) / 1000.0
        return temp
    except Exception:
        return None

def get_status():
    temp = get_temp()
    # Get network stats
    net = psutil.net_io_counters()
    # Get CPU usage
    cpu = psutil.cpu_percent(interval=0.1)
    # Get memory usage
    mem = psutil.virtual_memory().percent
    return {
        'temperature': temp,
        'signal': 'Excellent',
        'latency': round(80 + 40 * (1 - min(1, cpu / 100)), 1),
        'status': 'Connected (Online)' if g_connected else 'Disconnected (Offline)',
        'last_access': time.strftime('%d/%m/%Y %H:%M:%S'),
        'transmitted': f"{round(net.bytes_sent / 1024, 1)} KiB",
        'cpu': cpu,
        'mem': mem
    }

def apply_camera_settings(cam, settings):
    """Apply camera settings with proper validation and error handling"""
    try:
        # Set resolution first (must be a tuple)
        if isinstance(settings['resolution'], list) and len(settings['resolution']) == 2:
            width, height = settings['resolution']
            if width > 0 and height > 0:
                cam.resolution = (int(width), int(height))
        
        # Set rotation (must be 0, 90, 180, or 270)
        try:
            rotation = int(settings['rotation'])
            if rotation in [0, 90, 180, 270]:
                cam.rotation = rotation
        except (ValueError, TypeError):
            pass

        # Set image effect
        effect = settings['effect'].lower()
        if effect == 'normal':
            cam.image_effect = 'none'
        elif effect == 'negative':
            cam.image_effect = 'negative'
        else:
            cam.image_effect = 'none'

        # Set sharpness (-100 to 100)
        try:
            sharpness = 0
            if settings['sharpness'] == 'Medium':
                sharpness = 50
            elif settings['sharpness'] == 'High':
                sharpness = 100
            cam.sharpness = sharpness
        except (ValueError, TypeError):
            pass

        # Set framerate if specified
        try:
            if settings['fps'] != 'Auto':
                fps = int(settings['fps'])
                if 1 <= fps <= 90:  # PiCamera typically supports up to 90fps
                    cam.framerate = fps
        except (ValueError, TypeError):
            pass

        # Set color mode
        if settings['image'].lower() == 'gray':
            cam.color_effects = (128, 128)  # Grayscale
        else:
            cam.color_effects = None  # Color

        # Set JPEG quality based on compression setting
        quality = 85  # Default
        if settings['compression'] == 'Low':
            quality = 40
        elif settings['compression'] == 'Medium':
            quality = 60
        elif settings['compression'] == 'High':
            quality = 85
        elif settings['compression'] == 'Very High':
            quality = 100

        return quality

    except Exception as e:
        print(f"Error applying camera settings: {e}")
        # Return default quality if settings fail
        return 85

def cleanup_camera():
    """Safely cleanup camera resources"""
    global g_camera, g_monitoring
    try:
        if g_camera is not None:
            if g_monitoring:
                try:
                    g_camera.stop_preview()
                except:
                    pass
            try:
                g_camera.close()
            except:
                pass
    except Exception as e:
        print(f"Error cleaning up camera: {e}")
    finally:
        g_camera = None
        g_monitoring = False
        # Add safety delay after cleanup
        time.sleep(0.5)

def cleanup_old_images():
    """Clean up images older than 1 hour"""
    try:
        current_time = time.time()
        for filename in os.listdir(TEMP_DIR):
            filepath = os.path.join(TEMP_DIR, filename)
            if os.path.getmtime(filepath) < current_time - 3600:  # 1 hour
                os.remove(filepath)
    except Exception as e:
        print(f"Error cleaning up old images: {e}")

def format_size(size_kb):
    """Format size in KB to human readable format"""
    if size_kb < 1024:
        return f"{size_kb:.1f} KB"
    elif size_kb < 1024 * 1024:
        return f"{size_kb/1024:.1f} MB"
    else:
        return f"{size_kb/(1024*1024):.1f} GB"

@app.route('/video_stream')
def video_stream():
    def gen_frames():
        try:
            cam = get_camera()
            if cam is None:
                return
            stream = io.BytesIO()
            while g_monitoring and g_connected:
                try:
                    stream.seek(0)
                    stream.truncate()
                    # Use video port for streaming
                    cam.capture(stream, format='jpeg', use_video_port=True)
                    frame = stream.getvalue()
                    yield (b'--frame\r\n'
                           b'Content-Type: image/jpeg\r\n\r\n' + frame + b'\r\n')
                    time.sleep(0.1)  # Reduced delay for smoother streaming
                except Exception as e:
                    print(f"Error in video stream: {e}")
                    break
        except Exception as e:
            print(f"Error in video stream generator: {e}")

    return Response(gen_frames(), mimetype='multipart/x-mixed-replace; boundary=frame')

@app.route('/')
def index():
    status = get_status()
    warning = session.pop('warning', None)
    success = session.pop('success', None)
    cam = get_camera() if g_connected else None
    monitoring = g_monitoring
    settings = get_camera_settings()

    # Get image data from query string, not session
    last_image = request.args.get('last_image')
    last_image_size = request.args.get('last_image_size')
    last_image_width = request.args.get('last_image_width')
    last_image_height = request.args.get('last_image_height')
    last_capture_time = request.args.get('last_capture_time')

    return render_template_string(TEMPLATE, status=status, last_image=last_image,
        last_image_size=last_image_size, last_image_width=last_image_width,
        last_image_height=last_image_height, last_capture_time=last_capture_time,
        warning=warning, success=success, monitoring=monitoring,
        g_connected=g_connected, settings=settings)

@app.route('/capture', methods=['GET', 'POST'])
def capture():
    if request.method == 'GET':
        return '<h2>This page is for image capture only. Please use the main form to capture an image.</h2><a href="/">Back to main page</a>'

    warning = None
    if not g_connected:
        warning = 'Camera is not connected.'
        session['warning'] = warning
        return redirect(request.referrer or url_for('index'))

    try:
        # Parse and persist settings
        res = request.form.get('resolution', '3200x2400').split('x')
        try:
            width = int(res[0])
            height = int(res[1])
            if width <= 0 or height <= 0:
                width, height = 3200, 2400
        except (ValueError, IndexError):
            width, height = 3200, 2400
        compression = request.form.get('compression', 'Very High')
        fps = request.form.get('fps', '90')
        image = request.form.get('image', 'Color')
        rotation = request.form.get('rotation', '0')
        effect = request.form.get('effect', 'Normal')
        sharpness = request.form.get('sharpness', 'High')
        # Save settings to session
        session['camera_settings'] = {
            'resolution': [width, height],
            'compression': compression,
            'fps': fps,
            'image': image,
            'rotation': rotation,
            'effect': effect,
            'sharpness': sharpness
        }

        cam = get_camera()
        if cam is None:
            raise Exception("Failed to initialize camera")

        # Only set camera parameters if not streaming
        if not g_monitoring:
            try:
                cam.resolution = (width, height)
                cam.rotation = int(rotation)
                cam.image_effect = 'negative' if effect == 'Negative' else 'none'
                cam.sharpness = 100 if sharpness == 'High' else (50 if sharpness == 'Medium' else 0)
                # Fix grayscale
                if image == 'Gray':
                    cam.color_effects = (128, 128)
                else:
                    cam.color_effects = None
            except Exception as e:
                print(f"Error setting camera parameters: {e}")
                cleanup_camera()
                time.sleep(1)
                cam = get_camera()
                if cam is None:
                    raise Exception("Failed to recover camera after parameter error")
        else:
            # Always set color_effects for streaming too
            if image == 'Gray':
                cam.color_effects = (128, 128)
            else:
                cam.color_effects = None

        quality = 85  # Default
        if compression == 'Low':
            quality = 40
        elif compression == 'Medium':
            quality = 60
        elif compression == 'High':
            quality = 85
        elif compression == 'Very High':
            quality = 100

        stream = io.BytesIO()
        max_retries = 3
        for attempt in range(max_retries):
            try:
                stream.seek(0)
                stream.truncate()
                cam.capture(stream, format='jpeg', quality=quality, use_video_port=g_monitoring)
                break
            except Exception as e:
                print(f"Capture attempt {attempt + 1} failed: {e}")
                if attempt == max_retries - 1:
                    raise Exception("Failed to capture image after multiple attempts")
                cleanup_camera()
                time.sleep(1)
                cam = get_camera()
                if cam is None:
                    raise Exception("Failed to recover camera after capture error")

        stream.seek(0)
        image_bytes = stream.read()
        if len(image_bytes) == 0:
            raise Exception("Captured image is empty")

        timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
        filename = f"server1_{timestamp}.jpg"
        filepath = os.path.join(TEMP_DIR, filename)

        with open(filepath, 'wb') as f:
            f.write(image_bytes)

        image_size_kb = len(image_bytes) / 1024
        cleanup_old_images()

        redirect_url = url_for('index',
            last_image=filename,
            last_image_size=f"{image_size_kb:.1f}",
            last_image_width=width,
            last_image_height=height,
            last_capture_time=time.strftime('%Y-%m-%d %H:%M:%S')
        )
        return redirect(redirect_url)
    except Exception as e:
        warning = f'Error capturing image: {str(e)}'
        session['warning'] = warning
        return redirect(request.referrer or url_for('index'))

@app.route('/start_monitor', methods=['POST'])
def start_monitor():
    global g_monitoring
    warning = None
    cam = get_camera() if g_connected else None
    if not g_connected:
        warning = 'Camera is not connected. Cannot start monitor mode.'
        session['warning'] = warning
        return redirect(request.referrer or url_for('index'))
    if g_monitoring:
        warning = 'Monitor mode is already running.'
        session['warning'] = warning
        return redirect(request.referrer or url_for('index'))

    try:
        res = request.form.get('resolution', '1920x1080').split('x')
        width, height = int(res[0]), int(res[1])
        # Restrict monitor mode to 1920x1080 or lower
        if width > 1920 or height > 1080:
            width, height = 1920, 1080
            warning = 'Monitor mode only supports up to 1920x1080. Using 1920x1080.'
        
        settings = {
            'resolution': [width, height],
            'compression': request.form.get('compression', 'High'),
            'fps': request.form.get('fps', 'Auto'),
            'image': request.form.get('image', 'Color'),
            'rotation': request.form.get('rotation', '0'),
            'effect': request.form.get('effect', 'Normal'),
            'sharpness': request.form.get('sharpness', 'Medium')
        }
        # Apply settings for the preview stream
        apply_camera_settings(cam, settings)
        g_monitoring = True
    except Exception as e:
         warning = (warning or '') + f' Error starting monitor mode: {e}'

    if warning:
        session['warning'] = warning
    return redirect(request.referrer or url_for('index'))

@app.route('/stop_monitor', methods=['POST'])
def stop_monitor():
    global g_monitoring
    warning = None
    if not g_monitoring:
        warning = 'Monitor mode is not running.'
        session['warning'] = warning
        return redirect(request.referrer or url_for('index'))
    
    try:
        # Stop the preview (if running)
        cam = get_camera() if g_connected else None
        if cam:
             cam.stop_preview()
        g_monitoring = False
    except Exception as e:
        warning = f'Error stopping monitor mode: {e}'
        session['warning'] = warning

    if warning:
        session['warning'] = warning
    return redirect(request.referrer or url_for('index'))

@app.route('/start_record', methods=['POST'])
def start_record():
    res = request.form.get('resolution', '2592x1944').split('x')
    width, height = int(res[0]), int(res[1])
    # Limit video resolution to 1920x1080
    warning = None
    if width > 1920 or height > 1080:
        width, height = 1920, 1080
        warning = 'Video resolution too high for PiCamera. Using 1920x1080 for recording.'
    cam = get_camera()
    with g_recording_lock:
        if getattr(cam, 'recording', False):
            warning = (warning or '') + ' Cannot change settings while recording is running. Please stop recording first.'
        else:
            settings = {
                'resolution': [width, height],
                'compression': request.form.get('compression', 'High'),
                'fps': request.form.get('fps', 'Auto'),
                'image': request.form.get('image', 'Color'),
                'rotation': request.form.get('rotation', '0'),
                'effect': request.form.get('effect', 'Normal'),
                'sharpness': request.form.get('sharpness', 'Medium')
            }
            apply_camera_settings(cam, settings)
            filename = f'/tmp/record_{int(time.time())}.h264'
            cam.start_recording(filename)
            session['video_path'] = filename
    if warning:
        session['warning'] = warning
    return redirect(url_for('index'))

@app.route('/stop_record', methods=['POST'])
def stop_record():
    cam = get_camera()
    with g_recording_lock:
        if getattr(cam, 'recording', False):
            cam.stop_recording()
            session['video_ready'] = True
    return redirect(url_for('index'))

@app.route('/get_video')
def get_video():
    path = request.args.get('path')
    if path and os.path.exists(path):
        return send_file(path, as_attachment=False)
    return "Video not found", 404

@app.route('/connect', methods=['POST'])
def connect():
    global g_connected, g_camera
    warning = None
    if not g_connected:
        try:
            g_camera = get_camera()  # Initialize camera with saved settings
            g_connected = True
        except Exception as e:
            warning = f'Error connecting camera: {e}'
            session['warning'] = warning
    else:
        warning = 'Camera is already connected.'
        session['warning'] = warning
    return redirect(request.referrer or url_for('index'))

@app.route('/disconnect', methods=['POST'])
def disconnect():
    global g_camera, g_connected, g_monitoring
    warning = None
    if g_connected:
        try:
            cleanup_camera()
            g_connected = False
        except Exception as e:
            warning = f'Error disconnecting camera: {e}'
            session['warning'] = warning
    else:
        warning = 'Camera is already disconnected.'
        session['warning'] = warning
    return redirect(request.referrer or url_for('index'))

@app.route('/get_image/<filename>')
def get_image(filename):
    """Serve captured images"""
    try:
        filepath = os.path.join(TEMP_DIR, filename)
        if not os.path.exists(filepath):
            return "Image not found", 404
        return send_file(filepath, mimetype='image/jpeg', cache_timeout=0)
    except Exception as e:
        print(f"Error serving image {filename}: {e}")
        return str(e), 404

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000, debug=True) 



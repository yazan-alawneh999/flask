from flask import Flask, render_template_string, Response, request, jsonify, send_file, redirect, url_for, session, send_file
import io
import time
import threading
import os
import psutil
import base64
try:
    from picamera import PiCamera
except ImportError:
    print("WARN: picamera library not found. Camera functionality will be disabled.")
    # Define a dummy PiCamera class to avoid NameErrors later
    class PiCamera:
        def __init__(self, *args, **kwargs):
            self.resolution = (0,0)
            self.framerate = 0
            self.recording = False
            # Add any other attributes or methods that are accessed on g_camera
            # before it's confirmed to be a real PiCamera instance.
            # This is a minimal mock.
            print("Initialized DUMMY PiCamera")

        def close(self):
            print("DUMMY PiCamera closed")
            pass

        def stop_preview(self):
            pass

        def stop_recording(self):
            self.recording = False
            pass

        def capture(self, *args, **kwargs):
            raise RuntimeError("Dummy PiCamera: Cannot capture.")

        # Add other methods that might be called on a None camera or before full init
        def __getattr__(self, name):
            # Catch any other attribute access and return a dummy function or None
            print(f"DUMMY PiCamera: __getattr__ for {name}")
            return lambda *args, **kwargs: None


import tempfile
import shutil
from datetime import datetime

app = Flask(__name__)
app.secret_key = 'supersecretkey'  # Needed for session

g_camera = None
g_recording = False
g_recording_file = None
g_recording_lock = threading.Lock()
g_stream_lock = threading.Lock() # Lock for synchronizing stream and still capture
g_streaming = False
g_connected = False  # Camera is disconnected by default
g_monitoring = False  # Monitoring mode flag
# last_image_data, last_image_size, last_image_width, last_image_height were here, removed as unused globals.
# Image details are passed via URL parameters.

# Default settings prioritizing performance and quick startup
DEFAULT_SETTINGS = {
    'resolution': [1920, 1080],  # Moderate HD resolution
    'compression': 'Medium',      # Balanced quality and size/speed
    'fps': '30',                 # Standard video FPS
    'image': 'Color',
    'rotation': '0',
    'effect': 'Normal',
    'sharpness': 'Normal'        # Changed from 'High' to 'Normal'
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
            /* grid-template-columns: 2fr 1fr; */ /* Changed to single column for main items */
            grid-template-columns: 1fr;
            gap: 1.5rem;
            margin-bottom: 1.5rem;
        }

        .settings-card { /* Specific class for the settings card if needed for width */
            grid-column: 1 / -1; /* Span full width if main-content was multi-column */
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
            /* grid-column: 1; */ /* No longer needed if main-content is single column */
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
        .settings-grid-container {
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(220px, 1fr)); /* Adjusted minmax */
            gap: 1rem;
        }
        .setting-group {
            margin-bottom: 1rem; /* Reduced margin-bottom */
        }

        .setting-label {
            display: block;
            color: #cbd5e1;
            font-size: 0.8rem; /* Slightly reduced font size */
            font-weight: 500;
            margin-bottom: 0.4rem; /* Adjusted margin */
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
            margin-top: 1.5rem; /* This margin is between this section and the one above it (Preview) */
        }

        .controls-grid {
            display: grid;
            gap: 1rem; /* Default gap for larger screens */
            grid-template-columns: repeat(auto-fit, minmax(180px, 1fr)); /* 2-4 buttons */
        }

        .control-btn {
            background: #3b82f6;
            color: white;
            border: none;
            padding: 0.625rem 1.25rem; /* Keep padding for button size */
            border-radius: 0.375rem;
            font-weight: 600;
            font-size: 0.9rem; /* Slightly reduce font size for compactness */
            cursor: pointer;
            transition: background-color 0.2s;
            box-shadow: 0 2px 4px rgba(30,41,59,0.08);
            width: 100%; /* Make button take full width of grid cell */
            /* flex: 1 1 200px; */ /* Remove flex properties */
            /* min-width: 180px; */ /* Remove min-width */
            /* max-width: 100%; */ /* Already handled by width: 100% in grid */
            margin: 0; /* Keep margin 0 */
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
                /* grid-template-columns: 1fr; */ /* Already 1fr by default now */
            }
            .settings-grid-container {
                grid-template-columns: repeat(auto-fit, minmax(200px, 1fr));
            }
            .controls-grid {
                grid-template-columns: repeat(2, 1fr); /* 2 buttons per row on medium screens */
                gap: 1rem;
            }
            /* The single column rule for settings-grid-container is correctly in max-width: 640px */
        }

        @media (max-width: 640px) {
            .container {
                padding: 0.5rem;
            }
            /* General card content: default for mobile, overridden by more specific below */
             .card-content { 
                padding: 0.75rem; 
            }
            /* General card header: default for mobile, overridden by more specific below */
            .card-header {
                padding: 0.75rem 1rem;
            }

            /* Settings Card & Content */
            .settings-card {
                margin-bottom: 0.35rem; /* Other existing styles for .settings-card might be here too */
            }
            .settings-card .card-header {
                padding: 0.3rem 0.5rem;
            }
            .settings-card .card-title {
                font-size: 0.9rem;
            }
            .settings-card .card-content {
                padding: 0.25rem;
            }
            .settings-grid-container { 
                display: grid;
                grid-template-columns: repeat(4, 1fr); /* 4 items per row */
                gap: 0.25rem; 
            }
            .setting-group { 
                margin-bottom: 0.25rem; 
            }
            .setting-label {
                font-size: 0.65rem; 
                margin-bottom: 0.1rem; 
                display: block; 
                white-space: nowrap; 
                overflow: hidden; 
                text-overflow: ellipsis;
                line-height: 1.2;
            }
            .custom-select { 
                height: 1.8rem; 
                font-size: 0.65rem; 
                padding: 0 0.2rem; 
                line-height: 1.2;
            }

            /* Controls Section */
            .controls-section {
                margin-bottom: 0.5rem;
            }
            .controls-section .card-header {
                padding: 0.3rem 0.5rem;
            }
            .controls-section .card-title {
                font-size: 0.9rem;
            }
            .controls-section .card-content {
                padding: 0.25rem;
            }
            .controls-grid { 
                display: flex; 
                flex-wrap: wrap; 
                justify-content: space-around;
                gap: 0.25rem; 
            }
            .control-btn { 
                padding: 0.3rem 0.5rem; 
                font-size: 0.7rem; 
                flex-grow: 0; 
                flex-shrink: 1;
                line-height: 1.2;
                min-width: 0; /* From previous mobile styles */
                width: 100%;  /* For flex items, this might make them full width. Consider adjusting if needed. */
            }

            /* Live Preview Section */
            .live-preview-section .card-header {
                padding: 0.3rem 0.5rem;
            }
            .live-preview-section .card-title {
                font-size: 0.9rem;
            }
            .live-preview-section .card-content { 
                padding: 0.25rem; 
            }
            .live-preview-section .video-container {
                max-height: 150px; 
            }
            
            /* Preserved essential general mobile styles */
            .header-content {
                flex-direction: column;
                gap: 1rem;
                align-items: flex-start;
            }
            .main-title {
                font-size: 1.5rem;
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
            <!-- Camera Settings Card - MOVED HERE -->
            <div class="card settings-card"> <!-- Added settings-card class for potential specific styling -->
                <div class="card-header">
                    <h3 class="card-title">Camera Settings</h3>
                </div>
                <div class="card-content"> 
                    <form id="settings-form">
                        <div class="settings-grid-container">
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
                        </div>

                    </form>
                </div>
            </div>

            <!-- Camera Controls Card - MOVED HERE -->
            <section class="controls-section">
                <div class="card">
                    <div class="card-header">
                        <h3 class="card-title">Camera Controls</h3>
                    </div>
                    <div class="card-content">
                        <div class="controls-grid">
                            <form method="POST" action="/start_monitor" style="display:contents;"> 
                                <button type="submit" class="control-btn" {% if not g_connected or monitoring %}disabled{% endif %}>Start Stream</button>
                            </form>
                            <form method="POST" action="/stop_monitor" style="display:contents;">
                                <button type="submit" class="control-btn" {% if not g_connected or not monitoring %}disabled{% endif %}>Stop Stream</button>
                            </form>
                            <form method="POST" action="/capture" style="display:contents;">
                                <button type="submit" class="control-btn" {% if not g_connected %}disabled{% endif %}>Capture Image</button>
                            </form>
                            <form method="POST" action="/disconnect" style="display:contents;">
                                <button type="submit" class="control-btn" {% if not g_connected or monitoring %}disabled{% endif %}>Disconnect</button>
                            </form>
                        </div>
                    </div>
                </div>
            </section>

            <section class="live-preview-section">
                {% if monitoring %}
                <div class="card"> <!-- Live Preview card -->
                    <div class="card-header">
                        <h3 class="card-title">Live Preview</h3>
                    </div>
                    <div class="card-content">
                        <div class="video-container">
                            {# If monitoring is true, g_connected should also be true as per start_monitor logic #}
                            {# The 'not g_connected' case here is unlikely if monitoring is true, but kept for robustness #}
                            {% if not g_connected %}
                            <div class="camera-disconnected">
                                <p class="disconnected-title">Camera Disconnected</p>
                                <p class="disconnected-subtitle">Cannot stream if camera is not connected.</p>
                            </div>
                            {% else %}
                            <img id="video-stream" src="/video_stream" style="width: 100%; height: 100%; object-fit: contain;" />
                            {% endif %}
                        </div>
                    </div>
                </div>
                {% endif %} {# End of if monitoring for the live preview card #}

                {% if last_image %} <!-- Captured image display remains, independent of monitoring state -->
                <div class="captured-image-container">
                    <img id="captured-image" src="/get_image/{{ last_image }}" />
                    <div class="status-footer">
                        <div class="footer-item">
                            <div class="footer-label">Size</div>
                            <div class="footer-value">{{ last_image_size }}</div>
                        </div>
                        <div class="footer-item">
                            <div class="footer-label">Dimensions</div>
                            <div class="footer-value">{{ last_image_width }}×{{ last_image_height }}</div>
                        </div>
                    </div>
                </div>
                {% endif %}
                <!-- Original Camera Controls Section - REMOVED from here -->
            </section>
            <aside class="sidebar">
                <!-- System Status Card - REMAINS IN SIDEBAR -->
                <div class="card">
                    <div class="card-header">
                        <h3 class="card-title">System Status</h3>
                    </div>
                    <div class="card-content">
                        <div class="status-item">
                            <div class="status-label">
                                <span class="status-text cpu">CPU Usage</span>
                            </div>
                            <span class="status-value" id="status-cpu">{{ status.cpu }}%</span>
                        </div>
                        <div class="status-item">
                            <div class="status-label">
                                <span class="status-text memory">Memory</span>
                            </div>
                            <span class="status-value" id="status-mem">{{ status.mem }}%</span>
                        </div>
                        <div class="status-item">
                            <div class="status-label">
                                <span class="status-text temperature">Temperature</span>
                            </div>
                            <span class="status-value" id="status-temp">{% if status.temperature %}{{ status.temperature }}°C{% else %}N/A{% endif %}</span>
                        </div>
                        <div class="status-item">
                            <div class="status-label">
                                <span class="status-text network">Network</span>
                            </div>
                            <span class="status-value" id="status-transmitted">{{ status.transmitted }}</span>
                        </div>
                        <div class="status-footer">
                            <div class="footer-item">
                                <div class="footer-label">Last Access</div>
                                <div class="footer-value" id="status-last-access">{{ status.last_access }}</div>
                            </div>
                            <div class="footer-item">
                                <div class="footer-label">Latency</div>
                                <div class="footer-value" id="status-latency">{{ status.latency }} ms</div>
                            </div>
                        </div>
                    </div>
                </div>
                <!-- Camera Settings Card was here, now moved to main content area -->
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
    <script>
        const settingsForm = document.getElementById('settings-form');
        if (settingsForm) {
            const selectElements = settingsForm.querySelectorAll('select');
            selectElements.forEach(select => {
                select.addEventListener('change', function() {
                    const formData = new FormData(settingsForm);
                    fetch('/update_stream_settings', {
                        method: 'POST',
                        body: formData
                    })
                    .then(response => response.json())
                    .then(data => {
                        if (data.success) {
                            const videoStreamImage = document.getElementById('video-stream');
                            if (videoStreamImage) {
                                // Reload the image by changing the src attribute
                                const originalSrc = videoStreamImage.src.split('?')[0];
                                videoStreamImage.src = originalSrc + '?' + new Date().getTime();
                            }
                            // Optionally, display a success message or update UI
                            console.log('Settings updated successfully.');
                        } else {
                            // Optionally, display an error message
                            console.error('Error updating settings:', data.error);
                            if (data.warning) {
                                alert('Warning: ' + data.warning);
                            }
                        }
                    })
                    .catch(error => {
                        console.error('Error submitting settings form:', error);
                        alert('Error submitting settings. Please try again.');
                    });
                });
            });
        }

        function updateStatus() {
            fetch('/status_api')
                .then(response => response.json())
                .then(data => {
                    document.getElementById('status-cpu').textContent = data.cpu + '%';
                    document.getElementById('status-mem').textContent = data.mem + '%';
                    document.getElementById('status-temp').textContent = data.temperature ? data.temperature + '°C' : 'N/A';
                    document.getElementById('status-transmitted').textContent = data.transmitted;
                    document.getElementById('status-last-access').textContent = data.last_access;
                    document.getElementById('status-latency').textContent = data.latency + ' ms';
                })
                .catch(error => console.error('Error updating status:', error));
        }

        // Update status every 5 seconds
        setInterval(updateStatus, 5000);
        // Initial call to populate status immediately
        document.addEventListener('DOMContentLoaded', updateStatus);

    </script>
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
        print("get_camera(): Attempting to initialize PiCamera...")
        try:
            # Add safety delay before camera initialization
            time.sleep(0.5)
            g_camera = PiCamera()
            # Set truly minimal, non-session-dependent defaults
            g_camera.resolution = (1280, 720) # Example basic default
            g_camera.framerate = 30 # Example basic default
            print("get_camera(): PiCamera initialized with minimal defaults.")
        except Exception as e:
            print(f"get_camera(): Error initializing PiCamera: {e}")
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
    cpu = psutil.cpu_percent(interval=None)  # Use non-blocking
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
        # Apply resolution, potentially capping it if monitoring is active
        if isinstance(settings['resolution'], list) and len(settings['resolution']) == 2:
            width, height = settings['resolution']
            if width > 0 and height > 0:
                if g_monitoring: # If streaming, cap resolution to 1080p for video port
                    if width > 1920 or height > 1080:
                        original_res_for_warning = f"{width}x{height}"
                        width, height = 1920, 1080
                        # Consider adding a persistent warning/feedback mechanism if needed
                        print(f"[SETTINGS WARNING] Stream resolution capped to {width}x{height} from {original_res_for_warning}")
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

        # This quality is implicitly used by camera unless overridden in capture
        # For consistency, capture methods should determine their own quality based on settings

    except Exception as e:
        print(f"Error applying camera settings: {e}")

def cleanup_camera():
    """Safely cleanup camera resources"""
    global g_camera, g_monitoring
    try:
        if g_camera is not None:
            if g_monitoring:
                try:
                    g_camera.stop_preview()
                except Exception as e:
                    print(f"[CAMERA CLEANUP] stop_preview failed: {e}")
            try:
                # Attempt to stop recording if it's running (just in case)
                if hasattr(g_camera, 'recording') and g_camera.recording:
                    g_camera.stop_recording()
            except Exception as e:
                print(f"[CAMERA CLEANUP] stop_recording failed: {e}")
            try:
                g_camera.close()
                print("[CAMERA CLEANUP] g_camera closed successfully.")
            except Exception as e:
                print(f"[CAMERA CLEANUP] camera.close() failed: {e}")
    except Exception as e:
        print(f"[CAMERA CLEANUP ERROR] Unexpected error: {e}")
    finally:
        g_camera = None
        g_monitoring = False
        time.sleep(0.5)  # Let hardware fully release

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

def format_file_size(size_bytes):
    """Format size in bytes to human readable format (B, KB, MB, GB)"""
    if size_bytes < 1024:
        return f"{size_bytes} B"
    size_kb = size_bytes / 1024
    if size_kb < 1024:
        return f"{size_kb:.1f} KB"
    size_mb = size_kb / 1024
    if size_mb < 1024:
        return f"{size_mb:.1f} MB"
    size_gb = size_mb / 1024
    return f"{size_gb:.1f} GB"

# Moved gen_frames to be a module-level function that accepts cam and quality
def gen_frames(cam, quality_value):
    print("[STREAM DEBUG] gen_frames started")
    if cam is None:
        print("[STREAM DEBUG] gen_frames received None camera object. Aborting.")
        return

    # The diagnostic print for quality can be updated or removed if video_stream logs it
    # Original status print, ensuring g_monitoring and g_connected are still relevant for the loop condition
    print(f"gen_frames(): Status: g_camera_exists={cam is not None}, g_monitoring={g_monitoring}, g_connected={g_connected}")

    # Get the configured framerate for adaptive sleep
    try:
        target_fps = float(cam.framerate)
        if target_fps <= 0:  # Framerate must be positive
            print(f"[STREAM WARNING] Camera framerate is {target_fps}, falling back to 20 FPS for stream timing.")
            target_fps = 20.0  # Fallback FPS
    except TypeError:  # Handles case where cam.framerate might be None or not convertible
        print(f"[STREAM WARNING] Could not determine camera framerate, falling back to 20 FPS for stream timing.")
        target_fps = 20.0
    
    ideal_frame_interval = 1.0 / target_fps
    print(f"[STREAM DEBUG] gen_frames using quality: {quality_value}, Target FPS: {target_fps:.1f}, Ideal Interval: {ideal_frame_interval:.4f}s")
    
    stream = io.BytesIO()
    print(f"[STREAM DEBUG] Entering while loop: g_monitoring={g_monitoring}, g_connected={g_connected}")
    while g_monitoring and g_connected: # These globals still control the loop
        try:
            # print("gen_frames(): Inside loop, before cam.capture()") # Too verbose for regular operation
            stream.seek(0)
            stream.truncate()

            t_before_capture = time.time()
            with g_stream_lock: # Acquire lock before capturing frame
                if not g_monitoring: # Double check g_monitoring after acquiring lock
                    break
                cam.capture(stream, format='jpeg', use_video_port=True, quality=quality_value)
            t_after_capture = time.time()
            
            frame = stream.getvalue()
            yield (b'--frame\r\n'
                   b'Content-Type: image/jpeg\r\n\r\n' + frame + b'\r\n')

            capture_duration = t_after_capture - t_before_capture
            
            # Adaptive sleep to try and match the camera's configured framerate
            sleep_duration = ideal_frame_interval - capture_duration
            if sleep_duration < 0:
                sleep_duration = 0.001 # Minimal sleep if we're behind, to yield control
            
            effective_loop_time = capture_duration + sleep_duration
            effective_fps = 1.0 / effective_loop_time if effective_loop_time > 0 else float('inf')
            # print(f"[STREAM TIMING] Capture: {capture_duration:.4f}s, Sleep: {sleep_duration:.4f}s, Effective FPS: {effective_fps:.1f}") # Verbose
            
            time.sleep(sleep_duration)
        except Exception as e:
            print(f"[STREAM DEBUG] Error in video stream loop: {e}")
            # print(f"gen_frames(): Exception in loop: {e}") # Redundant with above
            break
    print(f"[STREAM DEBUG] Exited while loop: g_monitoring={g_monitoring}, g_connected={g_connected}")
    # Removed the outer try/except from original nested gen_frames as it's now simpler
    # If specific cleanup for gen_frames is needed, a finally block can be added here.
    print("[STREAM DEBUG] gen_frames finished")

@app.route('/video_stream')
def video_stream():
    cam = get_camera()
    if cam is None:
        print("[STREAM ERROR] Camera not available for video_stream route.")
        return "Camera not available", 503 # HTTP 503 Service Unavailable

    current_settings = get_camera_settings() # Fetches from session
    apply_camera_settings(cam, current_settings) # Apply ALL settings from session
       
    quality = 85 # Default
    if current_settings['compression'] == 'Low': quality = 40
    elif current_settings['compression'] == 'Medium': quality = 60
    elif current_settings['compression'] == 'High': quality = 85
    elif current_settings['compression'] == 'Very High': quality = 100
       
    print(f"[video_stream] Applied settings. Quality for stream: {quality}")

    return Response(gen_frames(cam, quality), mimetype='multipart/x-mixed-replace; boundary=frame')

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
    global g_monitoring # Declare g_monitoring as global at the top of the function
    if request.method == 'GET':
        return '<h2>This page is for image capture only. Please use the main form to capture an image.</h2><a href="/">Back to main page</a>'

    warning = None
    if not g_connected:
        warning = 'Camera is not connected.'
        session['warning'] = warning
        return redirect(request.referrer or url_for('index'))

    cam = get_camera()
    if cam is None:
        session['warning'] = "Failed to initialize camera for capture."
        return redirect(request.referrer or url_for('index'))

    original_monitoring_state = g_monitoring
    
    try:
        with g_stream_lock: # Acquire lock to pause the stream
            print(f"[CAPTURE DEBUG] Acquired g_stream_lock. Original monitoring state: {original_monitoring_state}")
            
            # Temporarily set g_monitoring to False to allow high-res still capture settings
            # and use of the still port.
            if original_monitoring_state:
                # global g_monitoring # No longer needed here
                g_monitoring = False 
                print(f"[CAPTURE DEBUG] Temporarily set g_monitoring to False.")

            # Get settings for high-quality still
            # These are the general settings chosen by the user, which we want for the still.
            still_settings = get_camera_settings()
            
            # Apply these settings. Since g_monitoring is now false (if it was true),
            # resolution capping in apply_camera_settings will not occur.
            print(f"[CAPTURE DEBUG] Applying settings for still capture: {still_settings}")
            apply_camera_settings(cam, still_settings)
            
            # Read the actual resolution that will be used for the still capture
            capture_width, capture_height = cam.resolution.width, cam.resolution.height
            print(f"[CAPTURE DEBUG] Actual camera resolution for still capture: {capture_width}x{capture_height}")

            # Determine capture quality for the still image
            quality = 85  # Default
            if still_settings['compression'] == 'Low': quality = 40
            elif still_settings['compression'] == 'Medium': quality = 60
            elif still_settings['compression'] == 'High': quality = 85
            elif still_settings['compression'] == 'Very High': quality = 100

            stream = io.BytesIO()
            max_retries = 3
            for attempt in range(max_retries):
                try:
                    stream.seek(0)
                    stream.truncate()
                    t_before_still_capture = time.time()
                    # Always use the still port for this capture route
                    cam.capture(stream, format='jpeg', quality=quality, use_video_port=False)
                    t_after_still_capture = time.time()
                    print(f"[CAPTURE TIMING] Still image cam.capture() (use_video_port=False) took: {t_after_still_capture - t_before_still_capture:.4f}s")
                    break
                except Exception as e:
                    print(f"Still capture attempt {attempt + 1} failed: {e}")
                    if attempt == max_retries - 1:
                        raise Exception("Failed to capture image after multiple attempts using still port")
                    time.sleep(0.5) # Brief pause before retry

            stream.seek(0)
            image_bytes = stream.read()
            if len(image_bytes) == 0:
                raise Exception("Captured image is empty")

            # --- Image saving and redirect logic (remains largely the same) ---
            timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
            filename = f"server1_{timestamp}.jpg"
            filepath = os.path.join(TEMP_DIR, filename)

            t_before_write = time.time()
            with open(filepath, 'wb') as f:
                f.write(image_bytes)
            t_after_write = time.time()
            print(f"[CAPTURE TIMING] File write for {filename} took: {t_after_write - t_before_write:.4f}s")

            image_size_bytes = len(image_bytes)
            formatted_size = format_file_size(image_size_bytes)
            cleanup_old_images()

            redirect_url = url_for('index',
                last_image=filename,
                last_image_size=formatted_size,
                last_image_width=capture_width,
                last_image_height=capture_height,
                last_capture_time=time.strftime('%Y-%m-%d %H:%M:%S')
            )
            # --- End of image saving and redirect logic ---

    except Exception as e:
        warning = f'Error capturing image: {str(e)}'
        session['warning'] = warning
        # Ensure g_monitoring is restored even if an error occurs within the lock
        if original_monitoring_state and not g_monitoring:
            # global g_monitoring # No longer needed here
            g_monitoring = True
            print(f"[CAPTURE DEBUG] Restored g_monitoring to True due to exception.")
            # Re-apply stream settings if monitoring was active
            # This is important if apply_camera_settings for still changed things
            stream_settings_for_resume = get_camera_settings().copy()
            # Apply stream-specific overrides if any (e.g., resolution cap, FPS for stream)
            # This logic mirrors what's in /start_monitor
            if stream_settings_for_resume['resolution'][0] > 1920 or stream_settings_for_resume['resolution'][1] > 1080:
                stream_settings_for_resume['resolution'] = [1920, 1080]
            stream_settings_for_resume['fps'] = '30' # Consistent stream FPS
            apply_camera_settings(cam, stream_settings_for_resume)
            print(f"[CAPTURE DEBUG] Re-applied stream settings after exception.")

        return redirect(request.referrer or url_for('index'))
    finally:
        # This block executes whether an exception occurred or not.
        # Crucially, restore g_monitoring state and re-apply stream settings if it was originally active.
        if original_monitoring_state:
            if not g_monitoring: # If it was changed
                # global g_monitoring # No longer needed here
                g_monitoring = True
                print(f"[CAPTURE DEBUG] Restored g_monitoring to True in finally block.")
            
            # Re-apply settings for the stream to ensure it resumes correctly.
            # This is important because the still capture might have used different settings.
            stream_settings_for_resume = get_camera_settings().copy()
            # Apply stream-specific overrides (e.g., resolution cap at 1080p for video port, specific FPS for streaming)
            # This logic should be consistent with how /start_monitor sets up the stream.
            if stream_settings_for_resume['resolution'][0] > 1920 or stream_settings_for_resume['resolution'][1] > 1080:
                stream_settings_for_resume['resolution'] = [1920, 1080]
            stream_settings_for_resume['fps'] = '30' # Example: ensure stream FPS is 30
            
            print(f"[CAPTURE DEBUG] Re-applying stream settings in finally block: {stream_settings_for_resume}")
            apply_camera_settings(cam, stream_settings_for_resume)
            print(f"[CAPTURE DEBUG] Stream settings re-applied. Releasing g_stream_lock.")
        # The lock is released automatically by the 'with g_stream_lock:' statement upon exiting the block.
        
    return redirect(redirect_url) # redirect_url is defined within the try block

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
        # Get current saved settings
        saved_settings = get_camera_settings()
        stream_settings = saved_settings.copy() # Work with a copy for stream-specific overrides

        # Override resolution for streaming: max 1920x1080
        original_res_w, original_res_h = stream_settings['resolution']
        res_width, res_height = original_res_w, original_res_h
        resolution_capped = False
        if res_width > 1920 or res_height > 1080:
            res_width, res_height = 1920, 1080
            # warning = 'Monitor mode resolution capped to 1920x1080 for performance.' # This line is removed/commented
            resolution_capped = True
        stream_settings['resolution'] = [res_width, res_height]

        # Override FPS for streaming for stability, e.g., cap at 30 FPS
        original_fps = stream_settings['fps']
        STREAM_FPS = '30' # Define a stream-specific FPS
        fps_changed = False
        if stream_settings['fps'] != STREAM_FPS:
            # warning = (warning + " " if warning else "") + f"Stream FPS set to {STREAM_FPS} for stability."
            fps_changed = True # We'll note if it was different, even if not higher
        stream_settings['fps'] = STREAM_FPS
        
        # Other settings like compression, image mode, rotation, effect, sharpness
        # will be taken from the user's saved_settings via stream_settings.copy()
        # and applied by apply_camera_settings.

        # Apply the potentially modified settings for the preview stream
        apply_camera_settings(cam, stream_settings)
        g_monitoring = True
        print(f"start_monitor_route(): Monitor mode started.")
        if resolution_capped or fps_changed:
            print(f"start_monitor_route(): Stream-specific settings applied: Resolution original={original_res_w}x{original_res_h}, new={res_width}x{res_height}. FPS original={original_fps}, new={STREAM_FPS}")

    except Exception as e:
         warning = (warning or '') + f' Error starting monitor mode: {str(e)}'
         print(f"start_monitor_route(): Error starting monitor: {e}")

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
            print("connect_route(): Successfully connected camera.")
        except Exception as e:
            warning = f'Error connecting camera: {e}'
            print(f"connect_route(): Error connecting camera: {e}")
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
            # Force stop stream first
            if g_monitoring:
                try:
                    g_monitoring = False
                    time.sleep(0.25)  # Let the stream loop exit
                    if g_camera:
                        g_camera.stop_preview()  # Just in case
                    print("[DISCONNECT DEBUG] Stream stopped before camera cleanup.")
                except Exception as e:
                    print(f"[DISCONNECT WARNING] Failed to stop monitoring cleanly: {e}")

            cleanup_camera()
            g_connected = False
            print("[DISCONNECT DEBUG] Camera successfully disconnected.")
        except Exception as e:
            warning = f'Error disconnecting camera: {e}'
            print(f"[DISCONNECT ERROR] {e}")
            session['warning'] = warning
    else:
        warning = 'Camera is already disconnected.'
        session['warning'] = warning

    return redirect(request.referrer or url_for('index'))

@app.route('/update_stream_settings', methods=['POST'])
def update_stream_settings():
    global g_camera, g_monitoring, g_connected
    try:
        current_settings = get_camera_settings()

        # Update settings from form data
        res_str = request.form.get('resolution', f"{current_settings['resolution'][0]}x{current_settings['resolution'][1]}")
        res = res_str.split('x')
        try:
            width = int(res[0])
            height = int(res[1])
            if width <= 0 or height <= 0: # Basic validation
                raise ValueError("Invalid resolution dimensions")
            current_settings['resolution'] = [width, height]
        except (ValueError, IndexError, TypeError):
            return jsonify({'success': False, 'error': 'Invalid resolution format. Expected WxH (e.g., 1920x1080).'}), 400

        current_settings['compression'] = request.form.get('compression', current_settings['compression'])
        current_settings['fps'] = request.form.get('fps', current_settings['fps'])
        current_settings['image'] = request.form.get('image', current_settings['image'])
        current_settings['rotation'] = request.form.get('rotation', current_settings['rotation'])
        current_settings['effect'] = request.form.get('effect', current_settings['effect'])
        current_settings['sharpness'] = request.form.get('sharpness', current_settings['sharpness'])

        save_camera_settings(current_settings)

        warning_message = None
        if g_connected and g_monitoring:
            cam = get_camera() # Ensure camera is initialized
            if cam:
                # Special handling for resolution and FPS during active streaming if needed
                # For now, apply_camera_settings should handle it.
                # Example: Check if resolution is too high for streaming
                if current_settings['resolution'][0] > 1920 or current_settings['resolution'][1] > 1080:
                    warning_message = "High resolutions might impact streaming performance."

                apply_camera_settings(cam, current_settings)
                # No need to restart stream explicitly, gen_frames uses the updated g_camera object
            else:
                return jsonify({'success': False, 'error': 'Camera not available for applying settings.'}), 500

        response_data = {'success': True, 'message': 'Settings updated successfully.'}
        if warning_message:
            response_data['warning'] = warning_message
        return jsonify(response_data)

    except ValueError as ve: # Catch specific validation errors
        return jsonify({'success': False, 'error': str(ve)}), 400
    except Exception as e:
        print(f"Error updating stream settings: {e}")
        return jsonify({'success': False, 'error': f'An unexpected error occurred: {str(e)}'}), 500

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

@app.route('/status_api')
def status_api():
    """API endpoint to get system status."""
    return jsonify(get_status())

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5001, debug=True) 



from flask import Flask, render_template_string, Response, request, jsonify, send_file, redirect, url_for, session
import io
import time
import threading
import os
import psutil
# import base64 # No longer used directly
from picamera import PiCamera, exc as PiCameraExc
import tempfile
import shutil
from datetime import datetime
import logging

app = Flask(__name__)
app.secret_key = 'supersecretkey'  # Needed for session

# Configure logging
# Basic config for stdout, good for Docker/systemd
logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s: %(name)s: %(message)s')
# Use Flask's logger for application-specific logs. It's already configured by Flask.
# You can add handlers to it if you want to log to files, etc.
# Example:
# file_handler = logging.FileHandler('flask_app.log')
# file_handler.setLevel(logging.INFO)
# app.logger.addHandler(file_handler)
# For now, default Flask logger (to stderr) is fine.
logger = app.logger # Use Flask's logger directly for convenience

g_camera = None
# g_recording = False # Replaced by direct check on camera.recording
# g_recording_file = None # Managed within start_record and related routes
g_camera_init_lock = threading.Lock() # Dedicated lock for camera initialization
g_recording_lock = threading.Lock() # Lock for recording operations
# g_streaming = False # Replaced by g_monitoring
g_connected = False
g_monitoring = False
# last_image_data, last_image_size etc. are passed via URL params now

DEFAULT_SETTINGS = {
    'resolution': [1920, 1080],
    'compression': 'Medium',
    'fps': '30',
    'image': 'Color',
    'rotation': '0',
    'effect': 'Normal',
    'sharpness': 'Normal'
}

TEMP_DIR = os.path.join(tempfile.gettempdir(), 'smartcam_images')
os.makedirs(TEMP_DIR, exist_ok=True)

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
                                <button type="submit" class="control-btn" {% if not g_connected %}disabled{% endif %}>Disconnect</button>
                            </form>
                        </div>
                    </div>
                </div>
            </section>

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
                            <img id="video-stream" src="/video_stream" style="width: 100%; height: 100%; object-fit: contain;" />
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
    # Use a dedicated lock for camera initialization to prevent race conditions.
    # This is crucial if multiple requests/threads might try to initialize simultaneously.
    with g_camera_init_lock:
        if g_camera is not None:
            logger.info("get_camera(): Camera already initialized, returning existing instance.")
            return g_camera

        logger.info("get_camera(): ENTERING CRITICAL SECTION - Attempting PiCamera initialization.")
        temp_cam = None # Temporary variable for initialization
        try:
            logger.info("get_camera(): Sleeping for 0.5s before PiCamera()...")
            time.sleep(0.5) # Safety delay, consider if still needed or if lock is enough

            logger.info("get_camera(): Calling PiCamera()...")
            temp_cam = PiCamera()
            logger.info(f"get_camera(): PiCamera() call successful. Object: {temp_cam}")

            # Apply absolutely minimal, non-configurable defaults necessary for basic operation
            # More extensive settings are applied by apply_camera_settings later.
            logger.info("get_camera(): Setting minimal default resolution (1280x720) and framerate (30).")
            temp_cam.resolution = (1280, 720)
            temp_cam.framerate = 30
            logger.info("get_camera(): Minimal defaults applied to new PiCamera instance.")

            g_camera = temp_cam # Assign to global g_camera only after successful initialization
            logger.info(f"get_camera(): SUCCESS - PiCamera initialized and assigned to g_camera. g_camera is now {g_camera}")

        except PiCameraExc.PiCameraMMALError as e_mmal:
            logger.error(f"get_camera(): PiCameraMMALError during PiCamera initialization: {e_mmal}", exc_info=True)
            logger.error(f"    MMAL Error Details: {e_mmal.args}") # Log specific MMAL error details if available
            if temp_cam: # If PiCamera object was created but failed during setup
                try: temp_cam.close()
                except: pass
            g_camera = None
        except PiCameraExc.PiCameraError as e_picam:
            logger.error(f"get_camera(): PiCameraError (other than MMAL) during PiCamera initialization: {e_picam}", exc_info=True)
            if temp_cam:
                try: temp_cam.close()
                except: pass
            g_camera = None
        except Exception as e_generic:
            logger.error(f"get_camera(): Generic Exception during PiCamera initialization: {e_generic}", exc_info=True)
            if temp_cam:
                try: temp_cam.close()
                except: pass
            g_camera = None # Ensure g_camera is None on any failure
        finally:
            logger.info(f"get_camera(): EXITING CRITICAL SECTION - PiCamera initialization attempt finished. g_camera is {g_camera}")

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
    """Apply camera settings with proper validation and error handling
    Returns True on success, False on failure to apply settings.
    """
    if cam is None:
        logger.warning("apply_camera_settings: Camera object is None. Cannot apply settings.")
        return False

    logger.info(f"apply_camera_settings: Attempting to apply to camera {cam} with settings: {settings}")

    try:
        # Resolution
        if 'resolution' in settings and isinstance(settings['resolution'], list) and len(settings['resolution']) == 2:
            width, height = settings['resolution']
            if width > 0 and height > 0:
                effective_width, effective_height = width, height
                if g_monitoring:  # If streaming, cap resolution for video port
                    if width > 1920 or height > 1080:
                        original_res_for_warning = f"{width}x{height}"
                        effective_width, effective_height = 1920, 1080
                        logger.warning(f"apply_camera_settings: Stream resolution capped to {effective_width}x{effective_height} from {original_res_for_warning}")
                logger.info(f"apply_camera_settings: Setting resolution to {effective_width}x{effective_height}")
                cam.resolution = (int(effective_width), int(effective_height))
            else:
                logger.warning(f"apply_camera_settings: Invalid resolution values: {width}x{height}")

        # Rotation
        if 'rotation' in settings:
            try:
                rotation = int(settings['rotation'])
                if rotation in [0, 90, 180, 270]:
                    logger.info(f"apply_camera_settings: Setting rotation to {rotation}")
                    cam.rotation = rotation
                else:
                    logger.warning(f"apply_camera_settings: Invalid rotation value: {rotation}")
            except (ValueError, TypeError) as e:
                logger.warning(f"apply_camera_settings: Error parsing rotation value '{settings['rotation']}': {e}")

        # Image Effect
        if 'effect' in settings:
            effect = str(settings['effect']).lower()
            selected_effect = 'none'
            if effect == 'negative':
                selected_effect = 'negative'
            # Add more effects here if supported and desired
            logger.info(f"apply_camera_settings: Setting image_effect to {selected_effect} (original: {settings['effect']})")
            cam.image_effect = selected_effect

        # Sharpness
        if 'sharpness' in settings:
            try:
                sharpness_val = 0  # Default to normal
                if settings['sharpness'] == 'Medium':
                    sharpness_val = 50
                elif settings['sharpness'] == 'High':
                    sharpness_val = 100
                # PiCamera sharpness is -100 to 100.
                logger.info(f"apply_camera_settings: Setting sharpness to {sharpness_val} (original: {settings['sharpness']})")
                cam.sharpness = sharpness_val
            except (ValueError, TypeError) as e:
                 logger.warning(f"apply_camera_settings: Error processing sharpness value '{settings['sharpness']}': {e}")

        # Framerate
        if 'fps' in settings:
            try:
                if str(settings['fps']).lower() != 'auto':
                    fps_val = int(settings['fps']) # Renamed to avoid conflict
                    # Add reasonable bounds for FPS, e.g., 1 to 90 for PiCamera
                    # Higher FPS might be possible with sensor_mode or smaller resolutions
                    if 1 <= fps_val <= 240:
                        logger.info(f"apply_camera_settings: Setting framerate to {fps_val}")
                        cam.framerate = fps_val
                    else:
                        logger.warning(f"apply_camera_settings: Framerate {fps_val} out of typical bounds (1-240). Not applying.")
                # else: logger.info("apply_camera_settings: FPS set to 'Auto', camera manages framerate.") # Handled by camera
            except (ValueError, TypeError) as e:
                logger.warning(f"apply_camera_settings: Error parsing FPS value '{settings['fps']}': {e}")

        # Color Mode (Image)
        if 'image' in settings:
            if str(settings['image']).lower() == 'gray':
                logger.info("apply_camera_settings: Setting color_effects to (128,128) for Grayscale.")
                cam.color_effects = (128, 128)
            else: # Default to Color
                logger.info("apply_camera_settings: Setting color_effects to None for Color.")
                cam.color_effects = None

        # JPEG Quality (Compression) - This is for information, actual quality is set at capture time.
        if 'compression' in settings:
            intended_jpeg_quality = 85  # Default
            if settings['compression'] == 'Low': intended_jpeg_quality = 40
            elif settings['compression'] == 'Medium': intended_jpeg_quality = 60
            elif settings['compression'] == 'High': intended_jpeg_quality = 85
            elif settings['compression'] == 'Very High': intended_jpeg_quality = 100
            logger.info(f"apply_camera_settings: Intended JPEG quality for captures will be {intended_jpeg_quality} (based on compression: '{settings['compression']}')")

        logger.info("apply_camera_settings: All specified settings processed successfully.")
        return True # Settings processed (individual errors logged as warnings, but function itself succeeded)

    except PiCameraExc.PiCameraMMALError as e_mmal:
        logger.error(f"apply_camera_settings: PiCameraMMALError applying settings: {e_mmal}", exc_info=True)
        logger.error(f"    MMAL Error Details: {e_mmal.args}")
        return False
    except PiCameraExc.PiCameraError as e_picam:
        logger.error(f"apply_camera_settings: PiCameraError applying settings: {e_picam}", exc_info=True)
        return False
    except Exception as e_generic:
        logger.error(f"apply_camera_settings: Generic Exception applying settings: {e_generic}", exc_info=True)
        return False

def cleanup_camera():
    """Safely cleanup camera resources"""
    global g_camera, g_monitoring, g_connected

    # Attempt to acquire the init lock briefly. This is a defensive measure.
    # If another thread is actively initializing, we might not want to interfere aggressively,
    # but typically cleanup is called when we intend to shut down the camera instance.
    lock_acquired = g_camera_init_lock.acquire(timeout=0.1)

    try:
        logger.info("cleanup_camera(): Starting camera cleanup procedure.")
        cam_instance_to_clean = g_camera # Work with a local reference

        if cam_instance_to_clean is not None:
            logger.info(f"cleanup_camera(): Found active camera instance {cam_instance_to_clean}.")
            # Stop recording if active
            if cam_instance_to_clean.recording:
                logger.info("cleanup_camera(): Camera is recording. Attempting to stop recording.")
                try:
                    cam_instance_to_clean.stop_recording()
                    logger.info("cleanup_camera(): stop_recording() called.")
                except PiCameraExc.PiCameraNotRecording as e_not_rec:
                    logger.warning(f"cleanup_camera(): stop_recording() called but camera was not recording: {e_not_rec}")
                except Exception as e_rec:
                    logger.error(f"cleanup_camera(): Exception while stopping recording: {e_rec}", exc_info=True)

            # Conceptual preview stopping (if applicable in other contexts)
            # if g_monitoring: # Or a more direct check like cam_instance_to_clean.previewing if that attribute exists/is used
            #     logger.info("cleanup_camera(): Monitoring was active. Conceptual preview stop.")
            #     # If cam_instance_to_clean.stop_preview() was used, it would be here.
            #     pass

            # Close the camera
            logger.info(f"cleanup_camera(): Attempting to close camera instance {cam_instance_to_clean}.")
            try:
                cam_instance_to_clean.close()
                logger.info(f"cleanup_camera(): Camera instance {cam_instance_to_clean} closed.")
            except PiCameraExc.PiCameraRuntimeError as e_runtime: # e.g. camera already closed
                logger.warning(f"cleanup_camera(): PiCameraRuntimeError while closing camera (possibly already closed): {e_runtime}")
            except Exception as e_close:
                logger.error(f"cleanup_camera(): Exception while closing camera: {e_close}", exc_info=True)
        else:
            logger.info("cleanup_camera(): No active camera instance (g_camera was None). Nothing to clean from instance perspective.")

    except Exception as e_outer:
        # This catches errors in the logic of cleanup_camera itself, not just PiCamera errors.
        logger.error(f"cleanup_camera(): Outer exception during cleanup process: {e_outer}", exc_info=True)
    finally:
        # Reset global state regardless of how cleanup went
        g_camera = None
        g_monitoring = False
        g_connected = False
        logger.info("cleanup_camera(): Global states reset: g_camera=None, g_monitoring=False, g_connected=False.")

        if lock_acquired:
            g_camera_init_lock.release()
            logger.info("cleanup_camera(): Released g_camera_init_lock.")
        else:
            logger.warning("cleanup_camera(): g_camera_init_lock was not acquired or timed out; not releasing.")

        logger.info("cleanup_camera(): Cleanup procedure finished. Sleeping for 0.5s.")
        time.sleep(0.5) # Safety delay

def cleanup_old_images():
    """Clean up images older than 1 hour"""
    try:
        current_time = time.time()
        for filename in os.listdir(TEMP_DIR):
            filepath = os.path.join(TEMP_DIR, filename)
            if os.path.getmtime(filepath) < current_time - 3600:  # 1 hour
                logger.info(f"Cleaning up old image: {filepath}")
                os.remove(filepath)
    except Exception as e:
        logger.error(f"Error cleaning up old images: {e}", exc_info=True)

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
    logger.info("[STREAM DEBUG] gen_frames started")
    if cam is None:
        logger.warning("[STREAM DEBUG] gen_frames received None camera object. Aborting.")
        return

    logger.info(f"[STREAM DEBUG] gen_frames using quality: {quality_value}")
    logger.info(f"gen_frames(): Status: g_camera_exists={cam is not None}, g_monitoring={g_monitoring}, g_connected={g_connected}")

    stream = io.BytesIO()
    logger.info(f"[STREAM DEBUG] Entering while loop: g_monitoring={g_monitoring}, g_connected={g_connected}")
    while g_monitoring and g_connected: # These globals still control the loop
        try:
            # logger.debug("gen_frames(): Inside loop, before cam.capture()") # Potentially too verbose
            stream.seek(0)
            stream.truncate()

            t_before_capture = time.time()
            cam.capture(stream, format='jpeg', use_video_port=True, quality=quality_value)
            t_after_capture = time.time()

            frame = stream.getvalue()
            yield (b'--frame\r\n'
                   b'Content-Type: image/jpeg\r\n\r\n' + frame + b'\r\n')

            capture_duration = t_after_capture - t_before_capture
            current_sleep_duration = 0.05 # This is the value we're setting

            logger.debug(f"[STREAM TIMING] Capture: {capture_duration:.4f}s, Sleep: {current_sleep_duration:.2f}s, Target FPS: {1/current_sleep_duration:.1f}")

            time.sleep(current_sleep_duration) # Use the variable for clarity
        except Exception as e:
            logger.error(f"[STREAM DEBUG] Error in video stream loop: {e}", exc_info=True)
            break
    logger.info(f"[STREAM DEBUG] Exited while loop: g_monitoring={g_monitoring}, g_connected={g_connected}")
    logger.info("[STREAM DEBUG] gen_frames finished")

@app.route('/video_stream')
def video_stream():
    cam = get_camera()
    if cam is None:
        logger.error("[STREAM ERROR] Camera not available for video_stream route.")
        return "Camera not available or failed to initialize.", 503

    current_settings = get_camera_settings()
    if not apply_camera_settings(cam, current_settings):
        session['warning'] = "Failed to apply camera settings for video stream."
        logger.error("[STREAM ERROR] Failed to apply camera settings for video_stream route.")
        return "Error applying camera settings.", 500

    quality = 85 # Default
    if current_settings['compression'] == 'Low': quality = 40
    elif current_settings['compression'] == 'Medium': quality = 60
    elif current_settings['compression'] == 'High': quality = 85
    elif current_settings['compression'] == 'Very High': quality = 100

    logger.info(f"[video_stream] Applied settings. Quality for stream: {quality}")

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
    if request.method == 'GET':
        return '<h2>This page is for image capture only. Please use the main form to capture an image.</h2><a href="/">Back to main page</a>'

    warning = None
    if not g_connected:
        session['warning'] = 'Camera is not connected. Please connect the camera first.'
        return redirect(request.referrer or url_for('index'))

    cam = get_camera()
    if cam is None:
        session['warning'] = 'Camera is connected but not available. Try reconnecting.'
        return redirect(request.referrer or url_for('index'))

    try:
        # Get settings from session (updated by /update_stream_settings)
        current_settings = get_camera_settings()
        save_camera_settings(current_settings) # Ensure these are affirmed as the settings for this capture

        width = current_settings['resolution'][0]
        height = current_settings['resolution'][1]
        compression = current_settings['compression']
        # fps = current_settings['fps'] # FPS is not directly used for single capture like this typically
        image_mode = current_settings['image'] # Renamed to avoid conflict with image variable later
        # rotation = current_settings['rotation'] # Will be handled by apply_camera_settings
        # effect = current_settings['effect'] # Will be handled by apply_camera_settings
        # sharpness = current_settings['sharpness'] # Will be handled by apply_camera_settings

        # cam = get_camera() # Already retrieved and checked before this try block
        # if cam is None:
        #     raise Exception("Failed to initialize camera for capture") # This path should not be reached

        # === This is the key part ===
        logger.info(f"Applying settings before capture: {current_settings}")
        if not apply_camera_settings(cam, current_settings):
            logger.error("Failed to apply camera settings for capture.")
            session['warning'] = "Failed to apply camera settings for capture."
            return redirect(request.referrer or url_for('index'))

        # Determine capture quality (compression is part of current_settings)
        quality = 85  # Default for capture quality
        if current_settings['compression'] == 'Low':
            quality = 40
        elif current_settings['compression'] == 'Medium':
            quality = 60
        elif current_settings['compression'] == 'High':
            quality = 85
        elif current_settings['compression'] == 'Very High':
            quality = 100

        stream = io.BytesIO()
        max_retries = 3
        capture_success = False
        for attempt in range(max_retries):
            try:
                stream.seek(0)
                stream.truncate()
                # If g_monitoring is true, use_video_port=True is used.
                # This is generally for continuous streaming. For a single high-res capture,
                # it might be better to use the still port if settings (like resolution)
                # significantly differ from stream settings.
                # However, changing resolution on the fly while streaming is complex.
                # For now, we assume capture during monitoring uses video port and current stream settings.
                use_still_port_for_capture = not g_monitoring

                t_before_still_capture = time.time()
                cam.capture(stream, format='jpeg', quality=quality, use_video_port=not use_still_port_for_capture)
                t_after_still_capture = time.time()
                logger.info(f"[CAPTURE TIMING] Still image cam.capture() took: {t_after_still_capture - t_before_still_capture:.4f}s")
                capture_success = True
                break
            except PiCameraExc.PiCameraError as e_picam:
                logger.error(f"Capture attempt {attempt + 1} (PiCameraError): {e_picam}", exc_info=True)
                if "failed to enable output port" in str(e_picam).lower() and attempt < max_retries -1:
                    logger.info("Retrying capture after brief delay for port issue.")
                    time.sleep(0.5) # Brief delay before retry for port issues
                    # Consider re-initializing camera if this is a common recovery path:
                    # cleanup_camera()
                    # cam = get_camera()
                    # if cam is None: raise Exception("Camera unavailable after re-init attempt.")
                    # apply_camera_settings(cam, current_settings) # Re-apply settings
                elif attempt == max_retries -1:
                    raise # Re-raise the last exception if all retries fail
            except Exception as e:
                logger.error(f"Capture attempt {attempt + 1} failed (Generic Error): {e}", exc_info=True)
                if attempt == max_retries - 1:
                    raise
                # Optional: could also re-init camera here for generic errors if deemed useful

        if not capture_success: # Should be caught by re-raise above, but as a safeguard
            raise Exception("Failed to capture image after multiple attempts, capture_success is false.")

        stream.seek(0)
        image_bytes = stream.read()
        if len(image_bytes) == 0:
            logger.error("Captured image is empty")
            raise Exception("Captured image is empty")

        timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
        filename = f"server1_{timestamp}.jpg"
        filepath = os.path.join(TEMP_DIR, filename)

        t_before_write = time.time()
        with open(filepath, 'wb') as f:
            f.write(image_bytes)
        t_after_write = time.time()
        logger.info(f"[CAPTURE TIMING] File write for {filename} took: {t_after_write - t_before_write:.4f}s")

        image_size_bytes = len(image_bytes)
        formatted_size = format_file_size(image_size_bytes)
        cleanup_old_images()
        session['success'] = f"Image {filename} captured successfully."
        redirect_url = url_for('index',
            last_image=filename,
            last_image_size=formatted_size,
            last_image_width=width,
            last_image_height=height,
            last_capture_time=time.strftime('%Y-%m-%d %H:%M:%S')
        )
        return redirect(redirect_url)
    except Exception as e:
        logger.error(f"Error in capture route: {e}", exc_info=True)
        session['warning'] = f'Error capturing image: {str(e)}'
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
        if not apply_camera_settings(cam, stream_settings):
            warning = (warning or "") + " Failed to apply settings for monitor mode."
            session['warning'] = warning
            return redirect(request.referrer or url_for('index'))

        g_monitoring = True
        session['success'] = "Monitor mode started." # Provide success feedback
        logger.info(f"start_monitor_route(): Monitor mode started.")
        if resolution_capped or fps_changed:
            logger.info(f"start_monitor_route(): Stream-specific settings applied: Resolution original={original_res_w}x{original_res_h}, new={res_width}x{res_height}. FPS original={original_fps}, new={STREAM_FPS}")

    except Exception as e:
         warning = (warning or '') + f' Error starting monitor mode: {str(e)}'
         logger.error(f"start_monitor_route(): Error starting monitor: {e}", exc_info=True)

    if warning:
        session['warning'] = warning
    return redirect(request.referrer or url_for('index'))

@app.route('/stop_monitor', methods=['POST'])
def stop_monitor():
    global g_monitoring
    warning = None
    if not g_monitoring:
        session['warning'] = 'Monitor mode is not running.'
        return redirect(request.referrer or url_for('index'))

    try:
        cam = get_camera() # Get camera instance
        if cam and g_connected : # Check if camera is available and connected
            # No explicit stop_preview needed for PiCamera if just stopping frame generation
            pass
        g_monitoring = False
        session['success'] = "Monitor mode stopped."
    except Exception as e:
        warning = f'Error stopping monitor mode: {e}'
        session['warning'] = warning

    return redirect(request.referrer or url_for('index'))

@app.route('/start_record', methods=['POST'])
def start_record():
    if not g_connected:
        session['warning'] = 'Camera not connected. Cannot start recording.'
        return redirect(request.referrer or url_for('index'))

    cam = get_camera()
    if cam is None:
        session['warning'] = 'Camera is connected but not available. Try reconnecting.'
        return redirect(request.referrer or url_for('index'))

    res = request.form.get('resolution', '1920x1080').split('x') # Default to 1080p
    width, height = int(res[0]), int(res[1])
    warning = None
    if width > 1920 or height > 1080: # Max resolution for h264 recording
        width, height = 1920, 1080
        warning = 'Video resolution capped to 1920x1080 for H.264 recording.'

    with g_recording_lock:
        if cam.recording: # Use the direct attribute
            new_warning = 'Cannot change settings or start new recording while recording is running.'
            warning = f"{warning} {new_warning}" if warning else new_warning
            session['warning'] = warning
            return redirect(request.referrer or url_for('index'))
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
    if not g_connected:
        session['warning'] = 'Camera not connected. Cannot stop recording.'
        return redirect(request.referrer or url_for('index'))

    cam = get_camera()
    if cam is None: # Should ideally not happen if g_connected is true, but good practice
        session['warning'] = 'Camera unavailable. Cannot stop recording.'
        return redirect(request.referrer or url_for('index'))

    with g_recording_lock:
        if cam.recording:
            try:
                cam.stop_recording()
                session['success'] = 'Recording stopped.'
                session['video_ready'] = True # Assuming this is for UI to show download link
            except Exception as e:
                session['warning'] = f"Error stopping recording: {e}"
        else:
            session['info'] = 'Recording was not active.' # Use 'info' or 'warning' as appropriate
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
        # Attempt to initialize and connect the camera
        initialized_cam = get_camera()
        if initialized_cam is not None:
            g_camera = initialized_cam # Assign to global only if successful
            g_connected = True
            session['success'] = 'Camera connected successfully.'
            print("connect_route(): Successfully connected camera.")
        else:
            # get_camera() failed, g_camera is None
            warning = 'Failed to initialize camera. Please check logs and ensure it is connected properly.'
            print(f"connect_route(): Error connecting camera: {warning}")
            session['warning'] = warning
            g_connected = False # Ensure this is false if connection failed
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
                if current_settings['resolution'][0] > 1920 or current_settings['resolution'][1] > 1080:
                    # This warning is now more informational as apply_camera_settings will cap it.
                    warning_message = "High resolutions are capped to 1920x1080 for streaming."

                if not apply_camera_settings(cam, current_settings):
                    return jsonify({'success': False, 'error': 'Failed to apply new settings to camera.'}), 500
                # No need to restart stream explicitly, gen_frames uses the updated g_camera object
            else:
                # This case should ideally be prevented by g_connected and g_monitoring checks,
                # but as a fallback:
                return jsonify({'success': False, 'error': 'Camera not available for applying settings.'}), 500
        else: # Not connected or not monitoring - settings are saved, will apply on next stream/capture
            pass


        response_data = {'success': True, 'message': 'Settings updated and saved.'}
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

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000, debug=True)

[end of app.py]

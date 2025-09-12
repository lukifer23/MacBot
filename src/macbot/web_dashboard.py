#!/usr/bin/env python3
"""
MacBot Web Dashboard - Live monitoring and control interface
"""
import os
import sys
import time
import json
import psutil
import threading
import subprocess
import uuid
from collections import deque
from datetime import datetime
from typing import Optional

# Add src/ to path for imports
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..'))
import requests
from flask import Flask, render_template_string, jsonify, request, Response, stream_with_context
from flask_socketio import SocketIO, emit  # type: ignore
import logging
from .logging_utils import setup_logger

from .health_monitor import get_health_monitor

# Configure logging (unified)
logger = setup_logger("macbot.web_dashboard", "logs/web_dashboard.log")

app = Flask(__name__, static_folder='static', static_url_path='/static')

# Configure CORS properly
try:
    from flask_cors import CORS
    CORS(app, origins=["http://localhost:3000", "http://127.0.0.1:3000", "http://192.168.1.38:3000"])
    print("✅ CORS enabled for web dashboard")
except ImportError:
    print("⚠️ flask-cors not available, CORS may not work properly")

socketio = SocketIO(app, cors_allowed_origins="*", async_mode='threading')
from . import config as CFG

# Global state
system_stats = {
    'cpu': 0,
    'ram': 0,
    'disk': 0,
    'network': {'bytes_sent': 0, 'bytes_recv': 0},
    'timestamp': datetime.now().isoformat()
}

# Conversation state management
conversation_state = {
    'active': False,
    'current_speaker': None,  # 'user' or 'assistant'
    'last_activity': datetime.now(),
    'message_count': 0,
    'interruption_count': 0,
    'conversation_id': None
}

# Conversation history (in-memory for now, could be persisted to database)
conversation_history = deque(maxlen=100)  # Keep last 100 messages

# Connected WebSocket clients
websocket_clients = set()

llm_models_endpoint = CFG.get_llm_models_endpoint()
rag_host, rag_port = CFG.get_rag_host_port()
wd_host, wd_port = CFG.get_web_dashboard_host_port()
va_host, va_port = CFG.get_voice_assistant_host_port()
orc_host, orc_port = CFG.get_orchestrator_host_port()

service_status = {
    'llama': {'status': 'unknown', 'port': None, 'endpoint': llm_models_endpoint.rsplit('/v1', 1)[0]},
    'voice_assistant': {'status': 'unknown', 'port': va_port, 'endpoint': f'http://{va_host}:{va_port}'},
    'rag': {'status': 'unknown', 'port': rag_port, 'endpoint': f'http://{rag_host}:{rag_port}'},
    'web_gui': {'status': 'running', 'port': wd_port, 'endpoint': f'http://{wd_host}:{wd_port}'}
}

# API response helpers (non-breaking): include normalized fields while preserving legacy keys
def _api_ok(payload: dict | None = None, message: str = "OK", extra: dict | None = None, status: int = 200):
    resp = {
        'success': True,
        'message': message,
    }
    if payload is not None:
        resp['data'] = payload
    if extra:
        resp.update(extra)
    return jsonify(resp), status

def _api_error(message: str, code: str = 'bad_request', status: int = 400, details: dict | None = None, extra: dict | None = None):
    resp = {
        'success': False,
        'error': message,
        'code': code,
    }
    if details:
        resp['details'] = details
    if extra:
        resp.update(extra)
    return jsonify(resp), status

# HTML template for the dashboard
DASHBOARD_HTML = """
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>MacBot Dashboard</title>
    <style>
        * { margin: 0; padding: 0; box-sizing: border-box; }
        body { font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif; background: #f5f5f7; color: #1d1d1f; }
        .container { max-width: 95vw; margin: 0 auto; padding: 20px; }
        .header { text-align: center; margin-bottom: 30px; }
        .header h1 { font-size: clamp(2em, 4vw, 3em); color: #1d1d1f; margin-bottom: 10px; }
        .header p { font-size: clamp(1em, 2vw, 1.2em); color: #6e6e73; }
        
        .main-content { 
            display: grid; 
            grid-template-columns: 1fr 1fr; 
            gap: 20px; 
            margin-bottom: 20px; 
            height: calc(100vh - 200px);
        }
        
        .left-panel { 
            display: flex; 
            flex-direction: column; 
            gap: 15px; 
            height: 100%;
        }
        .right-panel { 
            display: flex; 
            flex-direction: column; 
            gap: 15px; 
            height: 100%;
        }
        
        .stats-grid { 
            display: grid; 
            grid-template-columns: repeat(2, 1fr); 
            gap: 10px; 
            flex-shrink: 0;
        }
        .stat-card { 
            background: white; 
            padding: 15px; 
            border-radius: 8px; 
            box-shadow: 0 2px 8px rgba(0,0,0,0.1); 
            text-align: center; 
        }
        .stat-card h3 { 
            color: #1d1d1f; 
            margin-bottom: 8px; 
            font-size: 0.9em; 
        }
        .stat-value { 
            font-size: 1.5em; 
            font-weight: bold; 
            color: #007aff; 
            margin-bottom: 3px; 
        }
        .stat-label { 
            color: #6e6e73; 
            font-size: 0.8em; 
        }
        
        .services-grid { 
            display: grid; 
            grid-template-columns: 1fr; 
            gap: 10px; 
            flex: 1;
        }
        .service-card { 
            background: white; 
            padding: 15px; 
            border-radius: 8px; 
            box-shadow: 0 2px 8px rgba(0,0,0,0.1); 
        }
        .service-card h3 { 
            color: #1d1d1f; 
            margin-bottom: 10px; 
            font-size: 1em; 
        }
        .service-status { 
            margin-bottom: 8px; 
            font-weight: bold; 
            font-size: 0.9em;
        }
        .status-dot { 
            font-size: 1em; 
        }
        .service-info { 
            color: #6e6e73; 
            margin-bottom: 3px; 
            font-size: 0.8em;
        }
        .service-url { 
            color: #007aff; 
            font-family: monospace; 
            font-size: 0.8em; 
        }
        
        .chat-section { 
            background: white; 
            padding: 20px; 
            border-radius: 8px; 
            box-shadow: 0 2px 8px rgba(0,0,0,0.1); 
            flex: 1;
        }
        .chat-section h3 { 
            color: #1d1d1f; 
            margin-bottom: 15px; 
            font-size: 1.1em; 
        }
        .chat-input-container { 
            display: flex; 
            gap: 8px; 
            margin-bottom: 15px; 
            align-items: center; 
        }
        .mode-toggle { display: flex; gap: 6px; align-items: center; }
        .mode-btn { background: #e5e5ea; color: #1d1d1f; border: none; padding: 8px 10px; border-radius: 6px; cursor: pointer; font-size: 0.85em; }
        .mode-btn.active { background: #007aff; color: white; }
        .end-voice-btn { background: #ff3b30; color: white; border: none; padding: 8px 10px; border-radius: 6px; cursor: pointer; font-size: 0.85em; display: none; }
        .waveform-wrap { margin-top: 8px; height: 36px; background: #f2f2f7; border-radius: 6px; display: none; align-items: center; }
        #waveform-canvas { width: 100%; height: 36px; }
        .chat-input { 
            flex: 1; 
            padding: 10px; 
            border: 2px solid #e5e5ea; 
            border-radius: 6px; 
            font-size: 0.9em; 
            outline: none; 
        }
        .chat-input:focus { 
            border-color: #007aff; 
        }
        .chat-button {
            background: #007aff;
            color: white;
            border: none;
            padding: 10px 16px;
            border-radius: 6px;
            font-size: 0.9em;
            cursor: pointer;
            transition: background-color 0.2s;
        }
        .chat-button:hover {
            background: #0056cc;
        }
        .chat-button:active {
            background: #004499;
        }
        .voice-button {
            background: #34c759;
            color: white;
            border: none;
            padding: 10px 12px;
            border-radius: 6px;
            font-size: 0.9em;
            cursor: pointer;
            transition: all 0.2s;
        }
        .voice-button:hover {
            background: #28a745;
            transform: scale(1.05);
        }
        .voice-button:active {
            background: #1e7e34;
            transform: scale(0.95);
        }
        .voice-button.recording {
            background: #ff3b30;
            animation: pulse 1s infinite;
        }
        .voice-button.recording:hover {
            background: #e03128;
        }
        .stop-conversation-btn { 
            background: #ff9500; 
            color: white; 
            border: none; 
            padding: 8px 12px; 
            border-radius: 6px; 
            font-size: 0.9em; 
            cursor: pointer; 
        }
        .stop-conversation-btn:hover { 
            background: #e6850e; 
        }
        .chat-history { 
            height: calc(100% - 80px); 
            overflow-y: auto; 
            background: #f2f2f7; 
            padding: 10px; 
            border-radius: 6px; 
        }
        .chat-message { 
            margin-bottom: 6px; 
            padding: 5px 8px; 
            border-radius: 4px; 
            font-size: 0.85em; 
        }
        .chat-user { 
            background: #007aff; 
            color: white; 
            text-align: right; 
        }
        .chat-assistant { 
            background: #e5e5ea; 
            color: #1d1d1f; 
        }
        .chat-system { 
            background: #ff9500; 
            color: white; 
            font-size: 0.75em; 
        }
        
        .tools-legend { 
            background: white; 
            padding: 15px; 
            border-radius: 8px; 
            box-shadow: 0 2px 8px rgba(0,0,0,0.1); 
            flex-shrink: 0;
        }
        .tools-legend h3 { 
            color: #1d1d1f; 
            margin-bottom: 10px; 
            font-size: 1em; 
        }
        .tool-categories { 
            display: grid; 
            grid-template-columns: 1fr; 
            gap: 10px; 
        }
        .tool-category h4 { 
            color: #007aff; 
            margin-bottom: 5px; 
            font-size: 0.9em; 
        }
        .tool-category ul { 
            list-style: none; 
            padding-left: 0; 
        }
        .tool-category li { 
            margin-bottom: 3px; 
            padding: 3px 0; 
            border-bottom: 1px solid #f0f0f0; 
            font-size: 0.8em;
        }
        
        .document-section { 
            background: white; 
            padding: 20px; 
            border-radius: 8px; 
            box-shadow: 0 2px 8px rgba(0,0,0,0.1); 
            margin-top: 20px;
        }
        .document-section h3 { 
            color: #1d1d1f; 
            margin-bottom: 15px; 
            font-size: 1.1em; 
        }
        .drag-drop-area { 
            border: 2px dashed #007aff; 
            border-radius: 8px; 
            padding: 25px; 
            text-align: center; 
            background: #f8f9ff; 
            margin-bottom: 15px;
            transition: all 0.3s ease;
        }
        .drag-drop-area:hover { 
            background: #e8f0ff; 
            border-color: #0056cc; 
        }
        .drag-drop-area.dragover { 
            background: #d0e7ff; 
            border-color: #0056cc; 
            transform: scale(1.02); 
        }
        .drag-drop-content p { 
            margin-bottom: 8px; 
            color: #6e6e73; 
            font-size: 0.9em;
        }
        .browse-btn { 
            background: #007aff; 
            color: white; 
            border: none; 
            padding: 10px 20px; 
            border-radius: 6px; 
            font-size: 0.9em; 
            cursor: pointer; 
            margin-top: 8px;
        }
        .browse-btn:hover { 
            background: #0056cc; 
        }
        
        .document-list { 
            background: #f8f9ff; 
            padding: 15px; 
            border-radius: 6px; 
        }
        .document-list h4 { 
            color: #1d1d1f; 
            margin-bottom: 10px; 
            font-size: 0.9em;
        }
        
        @keyframes pulse {
            0% { opacity: 1; }
            50% { opacity: 0.7; }
            100% { opacity: 1; }
        }
        
        .refresh-btn { background: #34c759; color: white; border: none; padding: 8px 16px; border-radius: 8px; cursor: pointer; margin-bottom: 15px; }
        .refresh-btn:hover { background: #28a745; }
        
        /* Responsive design - ensure no scrolling on any screen size */
        @media (max-width: 1200px) {
            .main-content { 
                grid-template-columns: 1fr; 
                gap: 15px; 
                height: calc(100vh - 180px);
            }
            .container { max-width: 98vw; padding: 15px; }
            .stats-grid { grid-template-columns: repeat(2, 1fr); }
        }
        
        @media (max-width: 768px) {
            .container { max-width: 100vw; padding: 10px; }
            .header h1 { font-size: 1.6em; }
            .header p { font-size: 0.9em; }
            .main-content { 
                height: calc(100vh - 160px);
                gap: 10px;
            }
            .stats-grid { grid-template-columns: repeat(2, 1fr); gap: 8px; }
            .stat-card { padding: 12px; }
            .service-card { padding: 12px; }
            .chat-section { padding: 15px; }
            .tools-legend { padding: 12px; }
            .document-section { padding: 15px; }
        }
        
        @media (max-width: 480px) {
            .container { padding: 8px; }
            .header h1 { font-size: 1.4em; }
            .header p { font-size: 0.8em; }
            .main-content { 
                height: calc(100vh - 140px);
                gap: 8px;
            }
            .stats-grid { grid-template-columns: 1fr; gap: 6px; }
            .stat-card { padding: 10px; }
            .service-card { padding: 10px; }
            .chat-section { padding: 12px; }
            .tools-legend { padding: 10px; }
            .document-section { padding: 12px; }
            .chat-input-container { flex-direction: column; gap: 6px; }
            .chat-input { margin-bottom: 6px; }
        }
        
        /* Allow vertical scrolling for long chats */
        html, body { 
            overflow-x: hidden; 
            overflow-y: auto; 
            min-height: 100vh; 
        }
        
        .container { 
            min-height: 100vh; 
            overflow: visible; 
        }

        /* Status banner */
        .status-banner {
            margin-top: 10px;
            padding: 8px 12px;
            border-radius: 6px;
            font-size: 0.9em;
            display: inline-block;
        }
        .status-info { background: #e7f1ff; color: #004085; border: 1px solid #b8daff; }
        .status-listening { background: #e6ffed; color: #155724; border: 1px solid #c3e6cb; }
        .status-speaking { background: #fff3cd; color: #856404; border: 1px solid #ffeeba; }
        .status-interrupted { background: #fdecea; color: #721c24; border: 1px solid #f5c6cb; }
    </style>
</head>
<body>
    <div class="container">
        <div class="header">
            <h1>🤖 MacBot Dashboard</h1>
            <p>Local Voice Assistant with AI Tools & Live Monitoring</p>
            <button class="refresh-btn" id="refresh-stats-btn">🔄 Refresh Stats</button>
            <button class="refresh-btn" id="clear-chat-btn" style="margin-left: 10px;">🧹 Clear Chat</button>
            <button class="refresh-btn" id="mic-access-btn" style="margin-left: 10px;">🎙️ Request Mic Access</button>
            <button class="refresh-btn" id="self-check-btn" style="margin-left: 10px; background:#5856d6;">🧪 Self-Check</button>
            <label style="margin-left: 10px; font-size: 0.9em;">
              <input type="checkbox" id="speak-toggle" checked /> Speak replies
            </label>
            <label style="margin-left: 10px; font-size: 0.9em;">
              <input type="checkbox" id="speak-toggle" checked /> Speak replies
            </label>
            <div id="status-banner" class="status-banner status-info" style="display:none; margin-left:10px;">Ready</div>
        </div>
        
        <div class="main-content">
            <!-- Left Side: Dashboard Stats -->
            <div class="left-panel">
                <div class="stats-grid">
                    <div class="stat-card">
                        <h3>CPU Usage</h3>
                        <div class="stat-value" id="cpu-usage">--</div>
                        <div class="stat-label">Current CPU utilization</div>
                    </div>
                    <div class="stat-card">
                        <h3>Memory Usage</h3>
                        <div class="stat-value" id="ram-usage">--</div>
                        <div class="stat-label">RAM consumption</div>
                    </div>
                    <div class="stat-card">
                        <h3>Disk Usage</h3>
                        <div class="stat-value" id="disk-usage">--</div>
                        <div class="stat-label">Storage utilization</div>
                    </div>
                    <div class="stat-card">
                        <h3>Network</h3>
                        <div class="stat-value" id="network-usage">--</div>
                        <div class="stat-label">Data transfer</div>
                    </div>
                </div>
                
                <div class="services-grid">
                    <div class="service-card">
                        <h3>📦 Model Status</h3>
                        <div class="service-info" id="model-llm">LLM: —</div>
                        <div class="service-info" id="model-ctx">Context: — • Threads: — • GPU layers: —</div>
                        <div class="service-info" id="model-mem">Memory: —</div>
                        <div class="service-info" id="model-stt">STT: —</div>
                        <div class="service-info" id="model-tts">TTS: —</div>
                    </div>
                    <div class="service-card">
                        <h3>🚀 LLM Server (llama.cpp)</h3>
                        <div class="service-status" id="llm-status">Status: <span class="status-dot">🟡</span> Checking...</div>
                        <div class="service-info">Port: 8080</div>
                        <div class="service-url">http://localhost:8080</div>
                    </div>
                    <div class="service-card">
                        <h3>🎤 Voice Assistant</h3>
                        <div class="service-status" id="voice-status">Status: <span class="status-dot">🟡</span> Checking...</div>
                        <div class="service-info">Endpoint: {{ services['voice_assistant']['endpoint'] }}</div>
                    </div>
                    <div class="service-card">
                        <h3>🔍 RAG System</h3>
                        <div class="service-status" id="rag-status">Status: <span class="status-dot">🟡</span> Checking...</div>
                        <div class="service-info">Endpoint: {{ services['rag']['endpoint'] }}</div>
                    </div>
                    <div class="service-card">
                        <h3>🌐 Web Dashboard</h3>
                        <div class="service-status" id="web-status">Status: <span class="status-dot">🟡</span> Checking...</div>
                        <div class="service-info">Port: 3000</div>
                        <div class="service-url">http://localhost:3000</div>
                    </div>
                </div>
            </div>
            
            <!-- Right Side: Chat/Transcript/Voice -->
            <div class="right-panel">
                <div class="chat-section">
                    <h3>💬 Chat Interface</h3>
                    <div class="chat-input-container">
                        <input type="text" class="chat-input" id="chat-input" placeholder="Type your message here...">
                        <button class="voice-button" id="voice-button" title="Click to start/stop voice recording">🎤</button>
                        <div class="mode-toggle">
                            <button class="mode-btn" id="ptt-btn" title="Push-to-talk mode">PTT</button>
                            <button class="mode-btn" id="conv-btn" title="Conversational mode">Conversational</button>
                            <button class="end-voice-btn" id="end-voice-btn" title="End conversation">End</button>
                            <span id="mode-badge" style="margin-left:6px; font-size:0.85em; color:#6e6e73;">Mode: PTT</span>
                        </div>
                        <button class="chat-button" id="chat-button">Send</button>
                        <button class="stop-conversation-btn" id="stop-conversation-btn" title="Stop conversational mode" style="display: none;">🛑</button>
                    </div>
                    <div class="waveform-wrap" id="waveform-wrap">
                        <canvas id="waveform-canvas"></canvas>
                    </div>
                    <div class="chat-history" id="chat-history">
                        <div class="chat-message chat-assistant">Hello! I am MacBot. How can I help you today?</div>
                    </div>
                </div>
                
                <!-- Tool Options Legend -->
                <div class="tools-legend">
                    <h3>🛠️ Available Tools</h3>
                    <div class="tool-categories">
                        <div class="tool-category">
                            <h4>🌐 Web & Search</h4>
                            <ul>
                                <li><strong>Web Search:</strong> "search for [topic]" or "find [information]"</li>
                                <li><strong>Browse Website:</strong> "open [website]" or "go to [url]"</li>
                            </ul>
                        </div>
                        <div class="tool-category">
                            <h4>💻 System & Apps</h4>
                            <ul>
                                <li><strong>Open App:</strong> "open [app name]" or "launch [application]"</li>
                                <li><strong>Screenshot:</strong> "take screenshot" or "capture screen"</li>
                                <li><strong>System Info:</strong> "system status" or "show stats"</li>
                            </ul>
                        </div>
                        <div class="tool-category">
                            <h4>🔍 Knowledge Base</h4>
                            <ul>
                                <li><strong>RAG Search:</strong> "search knowledge base for [topic]"</li>
                                <li><strong>Add Document:</strong> Use drag & drop below</li>
                            </ul>
                        </div>
                    </div>
                </div>
            </div>
        </div>
        
        <!-- Bottom: Document Input/RAG -->
        <div class="document-section">
            <h3>📄 Document Input & RAG</h3>
            <div class="drag-drop-area" id="drag-drop-area">
                <div class="drag-drop-content">
                    <p>📁 Drag & drop documents here to add to knowledge base</p>
                    <p>Supported: PDF, TXT, DOC, DOCX</p>
                    <input type="file" id="file-input" multiple accept=".pdf,.txt,.doc,.docx" style="display: none;">
                    <button class="browse-btn" onclick="document.getElementById('file-input').click()">Browse Files</button>
                </div>
            </div>
            <div class="document-list" id="document-list">
                <h4>📚 Current Documents in Knowledge Base:</h4>
                <div id="documents-container">Loading documents...</div>
            </div>
        </div>
    </div>
    
    <!-- Load Socket.IO from CDN (optional) -->
    <script src="https://cdn.socket.io/4.7.2/socket.io.min.js"></script>
    <!-- Robust external script to avoid inline parse issues -->
    <script src="/static/dashboard.js"></script>
    <script type="application/json" id="legacy-inline-js">
        // Global variables
        let isRecording = false;
        let mediaRecorder = null;
        let audioChunks = [];
        let isConversationalMode = false;
        let conversationTimeout = null;
        let silenceThreshold = 2000;
        let lastVoiceActivity = 0;
        let socket = null;

        console.log('🔧 JavaScript loaded successfully');

        // Status banner helpers
        window.setStatus = function(text, type) {
            const el = document.getElementById('status-banner');
            if (!el) return;
            el.style.display = text ? 'inline-block' : 'none';
            el.textContent = text || '';
            el.className = 'status-banner';
            if (type) el.classList.add('status-' + type);
        };



        // Global function for adding chat messages
        window.addChatMessage = function(message, sender) {
            const history = document.getElementById('chat-history');
            const messageDiv = document.createElement('div');
            messageDiv.className = 'chat-message chat-' + sender;
            messageDiv.textContent = message;
            history.appendChild(messageDiv);
            history.scrollTop = history.scrollHeight;
        };

        // Global function for HTTP message sending
        window.sendMessageHTTP = async function(message) {
            try {
                console.log('Sending to LLM via HTTP:', message);

                const response = await fetch('/api/chat', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ message: message })
                });

                console.log('HTTP response status:', response.status);

                if (response.ok) {
                    const data = await response.json();
                    console.log('HTTP response data:', data);
                    window.addChatMessage(data.response || 'No response', 'assistant');
                } else {
                    const errorText = await response.text();
                    console.error('HTTP error response:', errorText);
                    window.addChatMessage('❌ Error: ' + errorText, 'system');
                }
            } catch (error) {
                console.error('HTTP fetch error:', error);
                window.addChatMessage('❌ Network error: ' + error.message, 'system');
            }
        };

        // Global button handlers
        window.sendMessage = function() {
            console.log('=== SEND BUTTON CLICKED ===');
            const input = document.getElementById('chat-input');
            if (!input) {
                console.error('Chat input not found');
                return;
            }
            const message = input.value.trim();
            if (!message) {
                console.log('Empty message, not sending');
                return;
            }

            console.log('Sending message:', message);

            // Add user message to local display
            window.addChatMessage(message, 'user');
            input.value = '';

            // Indicate assistant is thinking/responding
            window.setStatus('Assistant is thinking...', 'speaking');

            // Send via WebSocket if connected, otherwise fallback to HTTP
            if (socket && socket.connected) {
                console.log('Sending via WebSocket');
                socket.emit('chat_message', { message: message });
            } else {
                console.log('WebSocket not connected, using HTTP fallback');
                window.sendMessageHTTP(message);
            }
        };

        window.toggleVoiceRecording = function() {
            console.log('=== VOICE BUTTON CLICKED ===');
            if (!isRecording) {
                console.log('Starting voice recording');
                window.startVoiceRecording();
            } else {
                console.log('Stopping voice recording');
                window.stopVoiceRecording();
            }
        };

        window.updateStats = function() {
            console.log('🔄 Updating stats...');
            fetch('/api/stats')
                .then(response => {
                    console.log('📊 Stats response status:', response.status);
                    return response.json();
                })
                .then(data => {
                    console.log('📊 Stats data received:', data);
                    window.renderStats(data);
                })
                .catch(error => {
                    console.error('❌ Stats update error:', error);
                });
        };

        window.updateServiceStatus = function() {
            console.log('Updating service status...');
            fetch('/api/services')
                .then(response => response.json())
                .then(data => {
                    window.renderServiceStatus(data);
                })
                .catch(error => {
                    console.error('Service status update error:', error);
                });
        };

        window.clearConversation = function() {
            if (confirm('Are you sure you want to clear the conversation history?')) {
                if (socket && socket.connected) {
                    socket.emit('clear_conversation');
                }
                // Clear local display
                const chatHistory = document.getElementById('chat-history');
                if (chatHistory) {
                    chatHistory.innerHTML = `<div class="chat-message chat-assistant">Hello! I'm MacBot. How can I help you today?</div>`;
                }
                // Reset conversation state
                isConversationalMode = false;
                if (conversationTimeout) {
                    clearTimeout(conversationTimeout);
                    conversationTimeout = null;
                }
            }
        };


        // Initialize WebSocket connection
        let eventSource = null;

        function initWebSocket() {
            try {
                if (typeof io === 'function') {
                    if (!socket) {
                        socket = io({
                            transports: ['websocket', 'polling'],
                            reconnection: true,
                            reconnectionAttempts: 5,
                            reconnectionDelay: 1000
                        });
                        console.log('WebSocket initialized');
                    }

                    socket.on('connect', function() {
                        console.log('WebSocket connected');
                    });

                    socket.on('disconnect', function() {
                        console.log('WebSocket disconnected');
                    });

                    socket.on('conversation_update', function(data) {
                        console.log('Received conversation update:', data);
                        handleConversationUpdate(data);
                    });

                    socket.on('voice_processed', function(data) {
                        console.log('Voice processed:', data);
                        if (data.transcription) {
                            window.addChatMessage(data.transcription, 'user');
                        }
                    });
                } else {
                    console.warn('Socket.IO not available; operating in HTTP/SSE mode');
                }
            } catch (err) {
                console.error('WebSocket initialization failed:', err);
            }
        }

        function initEventSource() {
            try {
                eventSource = new EventSource('/stream');
                eventSource.onmessage = function(event) {
                    try {
                        const payload = JSON.parse(event.data);
                        if (payload.system_stats) {
                            window.renderStats(payload.system_stats);
                        }
                        if (payload.service_status) {
                            window.renderServiceStatus(payload.service_status);
                        }
                    } catch (err) {
                        console.error('Failed to parse SSE data', err);
                    }
                };
                eventSource.onerror = function() {
                    console.warn('SSE connection failed, falling back to polling');
                    eventSource.close();
                    startPolling();
                };
            } catch (err) {
                console.warn('SSE not supported, falling back to polling');
                startPolling();
            }
        }

        function startPolling() {
            window.updateStats();
            window.updateServiceStatus();
            setInterval(window.updateStats, 5000);
            setInterval(window.updateServiceStatus, 5000);
        }

        // Voice recording variables are now declared at the top
        
        window.renderStats = function(data) {
            console.log('🎨 Rendering stats:', data);
            if (data.cpu !== undefined) {
                document.getElementById('cpu-usage').textContent = data.cpu + '%';
                console.log('📈 CPU updated to:', data.cpu + '%');
            }
            if (data.ram !== undefined) {
                document.getElementById('ram-usage').textContent = data.ram + '%';
                console.log('📈 RAM updated to:', data.ram + '%');
            }
            if (data.disk !== undefined) {
                document.getElementById('disk-usage').textContent = data.disk + '%';
                console.log('📈 Disk updated to:', data.disk + '%');
            }
            if (data.network && data.network.bytes_sent !== undefined) {
                document.getElementById('network-usage').textContent = formatBytes(data.network.bytes_sent + data.network.bytes_recv);
            }
        }

        function formatBytes(bytes) {
            if (bytes === 0) return '0 B';
            const k = 1024;
            const sizes = ['B', 'KB', 'MB', 'GB'];
            const i = Math.floor(Math.log(bytes) / Math.log(k));
            return parseFloat((bytes / Math.pow(k, i)).toFixed(1)) + ' ' + sizes[i];
        }

        window.renderServiceStatus = function(data) {
            const llmStatus = document.getElementById('llm-status');
            if (data.llama && data.llama.status === 'running') {
                llmStatus.innerHTML = 'Status: <span class="status-dot">🟢</span> Running';
            } else {
                llmStatus.innerHTML = 'Status: <span class="status-dot">🔴</span> Stopped';
            }

            const voiceStatus = document.getElementById('voice-status');
            if (data.voice_assistant && data.voice_assistant.status === 'running') {
                voiceStatus.innerHTML = 'Status: <span class="status-dot">🟢</span> Running';
            } else {
                voiceStatus.innerHTML = 'Status: <span class="status-dot">🔴</span> Stopped';
            }

            const ragStatus = document.getElementById('rag-status');
            if (data.rag && data.rag.status === 'running') {
                ragStatus.innerHTML = 'Status: <span class="status-dot">🟢</span> Running';
            } else {
                ragStatus.innerHTML = 'Status: <span class="status-dot">🔴</span> Stopped';
            }

            const webStatus = document.getElementById('web-status');
            if (data.web_gui && data.web_gui.status === 'running') {
                webStatus.innerHTML = 'Status: <span class="status-dot">🟢</span> Running';
            } else {
                webStatus.innerHTML = 'Status: <span class="status-dot">🔴</span> Stopped';
            }
        }



        window.clearConversation = function() {
            if (confirm('Are you sure you want to clear the conversation history?')) {
                if (socket && socket.connected) {
                    socket.emit('clear_conversation');
                }
                // Clear local display
                const chatHistory = document.getElementById('chat-history');
                if (chatHistory) {
                    chatHistory.innerHTML = '<div class="chat-message chat-assistant">Hello! I\'m MacBot. How can I help you today?</div>';
                }
                // Reset conversation state
                isConversationalMode = false;
                if (conversationTimeout) {
                    clearTimeout(conversationTimeout);
                    conversationTimeout = null;
                }
            }
        }
        
        // handleKeyPress function removed - now handled by event listener

        async function toggleVoiceRecording() {
            const voiceButton = document.getElementById('voice-button');
            
            if (!isRecording) {
                // Start recording
                startVoiceRecording();
            } else {
                // Stop recording
                stopVoiceRecording();
            }
        }
        
        window.startVoiceRecording = async function() {
            const voiceButton = document.getElementById('voice-button');
            
            try {
                const stream = await navigator.mediaDevices.getUserMedia({ 
                    audio: {
                        echoCancellation: true,
                        noiseSuppression: true,
                        autoGainControl: true
                    }
                });
                
                mediaRecorder = new MediaRecorder(stream);
                audioChunks = [];
                
                mediaRecorder.ondataavailable = (event) => {
                    audioChunks.push(event.data);
                    // Handle voice activity for turn detection
                    handleVoiceActivity();
                };
                
                mediaRecorder.onstop = async () => {
                    const audioBlob = new Blob(audioChunks, { type: 'audio/wav' });
                    await processVoiceInput(audioBlob);
                };
                
                // Start recording with smaller chunks for real-time processing
                mediaRecorder.start(100); // 100ms chunks
                isRecording = true;
                voiceButton.classList.add('recording');
                voiceButton.textContent = '⏹️';
                
                if (isConversationalMode) {
                    window.addChatMessage('🎤 Listening... Speak naturally.', 'system');
                    window.setStatus('Listening...', 'listening');
                } else {
                    window.addChatMessage('🎤 Recording... Click again to stop.', 'system');
                    window.setStatus('Listening...', 'listening');
                }
                
            // Emit WebSocket event
            if (socket && socket.connected) {
                socket.emit('start_voice_recording');
            }
                
            } catch (error) {
                window.addChatMessage('❌ Microphone access denied. Please allow microphone access.', 'system');
                console.error('Voice recording error:', error);
            }
        }
        
        window.stopVoiceRecording = function() {
            const voiceButton = document.getElementById('voice-button');
            
            if (mediaRecorder && isRecording) {
                mediaRecorder.stop();
                mediaRecorder.stream.getTracks().forEach(track => track.stop());
            }
            
            isRecording = false;
            voiceButton.classList.remove('recording');
            voiceButton.textContent = '🎤';
            
            // Emit WebSocket event
            if (socket && socket.connected) {
                socket.emit('stop_voice_recording');
            }
            window.setStatus('Processing voice...', 'info');
        }
        
        async function processVoiceInput(audioBlob) {
            window.addChatMessage('🎵 Processing voice input...', 'system');

            try {
                // Convert audio to base64
                const reader = new FileReader();
                reader.onload = async () => {
                    const base64Audio = reader.result.split(',')[1];

                    // Send via HTTP API (more reliable than WebSocket for large audio data)
                    try {
                        const response = await fetch('/api/voice', {
                            method: 'POST',
                            headers: { 'Content-Type': 'application/json' },
                            body: JSON.stringify({ audio: base64Audio })
                        });

                    if (response.ok) {
                        const data = await response.json();
                        if (data.transcription) {
                            window.addChatMessage(data.transcription, 'user');
                            // Now send the transcription to LLM
                            if (socket && socket.connected) {
                                socket.emit('chat_message', { message: data.transcription });
                            } else {
                                sendMessageHTTP(data.transcription);
                            }
                            window.setStatus('Assistant is thinking...', 'speaking');
                        } else {
                            window.addChatMessage('❌ No speech detected', 'system');
                            window.setStatus('Ready', 'info');
                        }
                    } else {
                        const errorText = await response.text();
                        window.addChatMessage('❌ Voice processing failed: ' + errorText, 'system');
                        window.setStatus('Ready', 'info');
                    }
                } catch (httpError) {
                    window.addChatMessage('❌ Voice processing error: ' + httpError.message, 'system');
                    window.setStatus('Ready', 'info');
                }

                    // Start conversational mode for natural flow
                    startConversationalMode();
                };
                reader.readAsDataURL(audioBlob);

            } catch (error) {
                window.addChatMessage('❌ Voice processing error: ' + error.message, 'system');
                console.error('Voice processing error:', error);
            }
        }
        
        function startConversationalMode() {
            isConversationalMode = true;
            window.addChatMessage('🎤 Conversational mode active. Speak naturally - I\'ll listen for your response.', 'system');
            window.setStatus('Listening...', 'listening');
            
            // Show stop conversation button
            document.getElementById('stop-conversation-btn').style.display = 'inline-block';
            
            // Update UI
            updateConversationalUI();
            
            // Start listening for the next voice input
            setTimeout(() => {
                if (isConversationalMode) {
                    startVoiceRecording();
                }
            }, 1000); // Wait 1 second after response before listening again
        }
        
        function stopConversationalMode() {
            isConversationalMode = false;
            if (conversationTimeout) {
                clearTimeout(conversationTimeout);
                conversationTimeout = null;
            }
            
            // Hide stop conversation button
            document.getElementById('stop-conversation-btn').style.display = 'none';
            
            // Stop recording if active
            if (isRecording) {
                stopVoiceRecording();
            }
            
            // Update UI
            updateConversationalUI();
            
            window.addChatMessage('🎤 Conversational mode stopped. Click the microphone to start again.', 'system');
            window.setStatus('Ready', 'info');
        }
        
        function handleVoiceActivity() {
            lastVoiceActivity = Date.now();
            
            // Reset the silence timeout
            if (conversationTimeout) {
                clearTimeout(conversationTimeout);
            }
            
            // Set new timeout for auto-send
            conversationTimeout = setTimeout(() => {
                if (isConversationalMode && isRecording) {
                    // Auto-send after silence threshold
                    stopVoiceRecording();
                }
            }, silenceThreshold);
        }
        
        async function sendMessageToLLM(message) {
            try {
                console.log('Sending to LLM:', message); // Debug log
                
                const response = await fetch('/api/llm', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ message: message })
                });
                
                console.log('LLM response status:', response.status); // Debug log
                
                if (response.ok) {
                    const data = await response.json();
                    console.log('LLM response data:', data); // Debug log
                    window.addChatMessage(data.response, 'assistant');
                } else {
                    const errorText = await response.text();
                    console.error('LLM error response:', errorText); // Debug log
                    window.addChatMessage('❌ LLM processing failed: ' + errorText, 'system');
                }
            } catch (error) {
                console.error('LLM fetch error:', error); // Debug log
                window.addChatMessage('❌ LLM error: ' + error.message, 'system');
            }
        }
        
        // Drag and drop functionality
        const dragDropArea = document.getElementById('drag-drop-area');
        const fileInput = document.getElementById('file-input');
        
        dragDropArea.addEventListener('dragover', (e) => {
            e.preventDefault();
            dragDropArea.classList.add('dragover');
        });
        
        dragDropArea.addEventListener('dragleave', () => {
            dragDropArea.classList.remove('dragover');
        });
        
        dragDropArea.addEventListener('drop', (e) => {
            e.preventDefault();
            dragDropArea.classList.remove('dragover');
            const files = e.dataTransfer.files;
            handleFiles(files);
        });
        
        fileInput.addEventListener('change', (e) => {
            const files = e.target.files;
            handleFiles(files);
        });
        
        function handleFiles(files) {
            // Basic file upload implementation for RAG system
            console.log('Files to upload:', files);
            
            const formData = new FormData();
            for (let i = 0; i < files.length; i++) {
                formData.append('files', files[i]);
            }
            
            // Send files to RAG server for processing
            fetch('/api/upload-documents', {
                method: 'POST',
                body: formData
            })
            .then(response => response.json())
            .then(data => {
                if (data.success) {
                    window.addChatMessage(`✅ ${files.length} file(s) uploaded to knowledge base successfully`, 'system');
                } else {
                    window.addChatMessage(`❌ Failed to upload files: ${data.error}`, 'system');
                }
            })
            .catch(error => {
                console.error('Upload error:', error);
                window.addChatMessage(`❌ Upload failed: ${error.message}`, 'system');
            });
        }
        
        // Handle window resize for better scaling
        window.addEventListener('resize', function() {
            // Adjust chat history height based on viewport
            const chatHistory = document.getElementById('chat-history');
            const viewportHeight = window.innerHeight;
            const newHeight = Math.max(200, viewportHeight * 0.4);
            chatHistory.style.maxHeight = newHeight + 'px';
        });
        
        // Add visual feedback for conversational mode
        function updateConversationalUI() {
            const voiceButton = document.getElementById('voice-button');
            
            if (isConversationalMode) {
                voiceButton.title = 'Conversational mode active - listening automatically';
                voiceButton.style.border = '2px solid #34c759';
            } else {
                voiceButton.title = 'Click to start/stop voice recording';
                voiceButton.style.border = 'none';
            }
        }
        
        // Initial stats and service status load
        window.updateStats();
        window.updateServiceStatus();
        
        // Trigger initial resize
        window.dispatchEvent(new Event('resize'));
        
        // Add error handling for missing elements
        window.addEventListener('error', function(e) {
            console.error('JavaScript error:', e.error);
        });
        
        // WebSocket event handlers (guard if Socket.IO is unavailable)
        if (socket) {
            socket.on('conversation_update', function(data) {
                console.log('Received conversation update:', data);
                handleConversationUpdate(data);
            });
            
            socket.on('voice_processed', function(data) {
                console.log('Voice processed:', data);
                if (data.transcription) {
                    window.addChatMessage(data.transcription, 'user');
                }
            });

            // Assistant state events (speaking/listening/interrupt)
            socket.on('assistant_state', function(data) {
                if (!data || !data.type) return;
                if (data.type === 'speaking_started') {
                    window.setStatus('Speaking...', 'speaking');
                } else if (data.type === 'speaking_ended') {
                    window.setStatus('Ready', 'info');
                } else if (data.type === 'speaking_interrupted') {
                    window.setStatus('Interrupted', 'interrupted');
                }
            });
        }
        
        function handleConversationUpdate(data) {
            if (data.type === 'user_message') {
                window.addChatMessage(data.content, 'user');
            } else if (data.type === 'assistant_message') {
                window.addChatMessage(data.content, 'assistant');
                window.setStatus('Ready', 'info');
            } else if (data.type === 'voice_transcription') {
                window.addChatMessage(data.transcription, 'user');
            } else if (data.type === 'error_message') {
                window.addChatMessage(data.content, 'system');
                window.setStatus('Ready', 'info');
            } else if (data.type === 'interruption') {
                window.addChatMessage('🔇 Conversation interrupted', 'system');
                window.setStatus('Interrupted', 'interrupted');
            } else if (data.type === 'voice_recording_started') {
                window.addChatMessage('🎤 Voice recording started...', 'system');
                window.setStatus('Listening...', 'listening');
            } else if (data.type === 'voice_recording_stopped') {
                window.addChatMessage('🎤 Voice recording stopped', 'system');
                window.setStatus('Processing voice...', 'info');
            } else if (data.type === 'conversation_cleared') {
                const chatHistory = document.getElementById('chat-history');
                chatHistory.innerHTML = '';
                window.addChatMessage('🧹 Conversation history cleared', 'system');
                window.setStatus('Ready', 'info');
            }
        }
        
        // Initialize everything when script loads
        console.log('Dashboard loading, initializing...');

        // Initialize connections
        initWebSocket();
        initEventSource();

        // Set up event listeners immediately (DOM is already loaded)
        function setupEventListeners() {
            console.log('🎧 Setting up event listeners...');

            const sendButton = document.getElementById('chat-button');
            const voiceButton = document.getElementById('voice-button');
            const chatInput = document.getElementById('chat-input');
            const stopButton = document.getElementById('stop-conversation-btn');
            const micBtn = document.getElementById('mic-access-btn');
            const refreshBtn = document.getElementById('refresh-stats-btn');
            const clearBtn = document.getElementById('clear-chat-btn');

            console.log('🎯 Elements found:', {
                sendButton: !!sendButton,
                voiceButton: !!voiceButton,
                chatInput: !!chatInput,
                stopButton: !!stopButton
            });

            if (sendButton) {
                console.log('✅ Attaching send button listener');
                sendButton.addEventListener('click', window.sendMessage);
            }

            if (voiceButton) {
                console.log('✅ Attaching voice button listener');
                voiceButton.addEventListener('click', window.toggleVoiceRecording);
            }

            // Duplicate listeners removed - using global function listeners above

            if (chatInput) {
                console.log('Setting up chat input listener');
                chatInput.addEventListener('keypress', function(event) {
                    if (event.key === 'Enter' && !event.shiftKey) {
                        event.preventDefault();
                        console.log('Enter pressed in chat input');
                        window.sendMessage();
                    }
                });
            }

            if (stopButton) {
                console.log('Setting up stop conversation button listener');
                stopButton.addEventListener('click', async function(event) {
                    event.preventDefault();
                    console.log('Stop conversation button clicked!');
                    if (socket && socket.connected) {
                        socket.emit('interrupt_conversation');
                    } else {
                        // HTTP fallback
                        try {
                            const resp = await fetch('/api/interrupt', { method: 'POST' });
                            if (!resp.ok) {
                                console.warn('HTTP interrupt failed', resp.status);
                            }
                        } catch (e) {
                            console.warn('Interrupt fallback error:', e);
                        }
                    }
                });
                stopButton.style.cursor = 'pointer';
            }

            if (micBtn) {
                micBtn.addEventListener('click', async function() {
                    try {
                        // Browser permission first
                        await navigator.mediaDevices.getUserMedia({ audio: true });
                        window.setStatus('Browser mic permission granted', 'info');
                    } catch (e) {
                        console.warn('Browser mic permission denied:', e);
                        window.setStatus('Browser mic permission denied', 'interrupted');
                    }

                    try {
                        // Trigger OS-level prompt via assistant
                        const resp = await fetch('/api/mic-check', { method: 'POST' });
                        const data = await resp.json();
                        if (resp.ok && data.ok) {
                            window.setStatus('Assistant mic ready', 'info');
                        } else {
                            window.setStatus('Assistant mic check failed', 'interrupted');
                        }
                    } catch (e) {
                        console.warn('Assistant mic check error:', e);
                        window.setStatus('Assistant mic check error', 'interrupted');
                    }
                });
            }

            if (refreshBtn) {
                refreshBtn.addEventListener('click', function() {
                    try { window.updateStats(); } catch (e) { console.warn('updateStats failed', e); }
                });
            }

            if (clearBtn) {
                clearBtn.addEventListener('click', function() {
                    try { window.clearConversation(); } catch (e) { console.warn('clearConversation failed', e); }
                });
            }

            console.log('Event listeners setup complete');
        }

        // Setup listeners when DOM is ready
        if (document.readyState === 'loading') {
            document.addEventListener('DOMContentLoaded', setupEventListeners);
        } else {
            setupEventListeners();
        }
    </script>
</body>
</html>
"""

def get_system_stats():
    """Get current system statistics"""
    try:
        cpu_percent = psutil.cpu_percent(interval=1)
        memory = psutil.virtual_memory()
        disk = psutil.disk_usage('/')
        network = psutil.net_io_counters()
        
        return {
            'cpu': round(cpu_percent, 1),
            'ram': round(memory.percent, 1),
            'disk': round(disk.percent, 1),
            'network': {
                'bytes_sent': network.bytes_sent,
                'bytes_recv': network.bytes_recv
            },
            'timestamp': datetime.now().isoformat()
        }
    except Exception as e:
        logger.error(f"Error getting system stats: {e}")
        return system_stats

def check_service_health():
    """Check health of all services"""
    try:
        # Check llama.cpp server
        try:
            response = requests.get(llm_models_endpoint, timeout=2)
            service_status['llama']['status'] = 'running' if response.status_code == 200 else 'stopped'
        except:
            service_status['llama']['status'] = 'stopped'
        
        # Check voice assistant via control health endpoint
        try:
            va_resp = requests.get(f"http://{va_host}:{va_port}/health", timeout=2)
            service_status['voice_assistant']['status'] = 'running' if va_resp.status_code == 200 else 'stopped'
        except Exception:
            service_status['voice_assistant']['status'] = 'stopped'
        
        # Check RAG service
        try:
            response = requests.get(f"http://{rag_host}:{rag_port}/health", timeout=2)
            service_status['rag']['status'] = 'running' if response.status_code == 200 else 'stopped'
        except:
            service_status['rag']['status'] = 'stopped'
        
    except Exception as e:
        logger.error(f"Error checking service health: {e}")

@app.route('/')
def dashboard():
    """Main dashboard page"""
    print("🌐 WEB DASHBOARD: Serving main dashboard page")
    check_service_health()
    return render_template_string(DASHBOARD_HTML, services=service_status)

@app.route('/favicon.ico')
def favicon():
    """Serve favicon - return empty response to avoid 404"""
    return '', 204

@app.route('/api/stats')
def api_stats():
    """API endpoint for system statistics"""
    global system_stats
    system_stats = get_system_stats()
    print(f"📊 WEB DASHBOARD: Stats API called - CPU: {system_stats.get('cpu', 'N/A')}%, RAM: {system_stats.get('ram', 'N/A')}%, Disk: {system_stats.get('disk', 'N/A')}%")
    return jsonify(system_stats)

@app.route('/api/services')
def api_services():
    """API endpoint for service status"""
    print("🔍 WEB DASHBOARD: Services API called")
    # Check actual service health
    try:
        # Prefer orchestrator aggregation if available
        orchestrator_ok = False
        try:
            orc_resp = requests.get(f"http://{orc_host}:{orc_port}/status", timeout=1.5)
            if orc_resp.status_code == 200:
                data = orc_resp.json().get('processes', {})
                # Map orchestrator process names to dashboard services
                service_status['llama']['status'] = 'running' if data.get('llama', {}).get('running') else 'stopped'
                service_status['web_gui']['status'] = 'running' if data.get('web_gui', {}).get('running') else 'stopped'
                service_status['rag']['status'] = 'running' if data.get('rag', {}).get('running') else 'stopped'
                service_status['voice_assistant']['status'] = 'running' if data.get('voice_assistant', {}).get('running') else 'stopped'
                orchestrator_ok = True
        except Exception:
            orchestrator_ok = False

        if not orchestrator_ok:
            # Direct checks as fallback
            # LLM server
            try:
                response = requests.get(llm_models_endpoint, timeout=2)
                service_status['llama']['status'] = 'running' if response.status_code == 200 else 'stopped'
            except:
                service_status['llama']['status'] = 'stopped'

            # RAG server
            try:
                response = requests.get(f"http://{rag_host}:{rag_port}/health", timeout=2)
                service_status['rag']['status'] = 'running' if response.status_code == 200 else 'stopped'
            except:
                service_status['rag']['status'] = 'stopped'

            # Voice assistant via control endpoint
            try:
                va_resp = requests.get(f"http://{va_host}:{va_port}/health", timeout=2)
                service_status['voice_assistant']['status'] = 'running' if va_resp.status_code == 200 else 'stopped'
            except Exception:
                service_status['voice_assistant']['status'] = 'stopped'
        
        # Web GUI is always running if this endpoint is accessible
        service_status['web_gui']['status'] = 'running'
        
    except Exception as e:
        logger.error(f"Error checking service status: {e}")

    return jsonify(service_status)

@app.route('/api/metrics')
def api_metrics():
    """Proxy to orchestrator metrics for UI consumption"""
    try:
        host, port = CFG.get_orchestrator_host_port()
        # Allow a slightly higher timeout due to process introspection
        r = requests.get(f"http://{host}:{port}/metrics", timeout=5)
        if r.ok:
            return jsonify(r.json())
        return jsonify({'success': False, 'error': f'orc responded {r.status_code}'}), 502
    except Exception as e:
        logger.error(f"Metrics proxy error: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500

@app.route('/api/pipeline-check')
def api_pipeline_check():
    """Proxy orchestrator pipeline-check for UI."""
    try:
        host, port = CFG.get_orchestrator_host_port()
        r = requests.get(f"http://{host}:{port}/pipeline-check", timeout=5)
        if r.ok:
            return jsonify(r.json())
        return jsonify({'success': False, 'error': f'orc responded {r.status_code}'}), 502
    except Exception as e:
        logger.error(f"Pipeline proxy error: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500

@app.route('/stream')
def stream():
    """SSE stream for system stats and service status"""
    def event_stream():
        while True:
            stats = get_system_stats()
            check_service_health()
            data = json.dumps({
                'system_stats': stats,
                'service_status': service_status
            })
            yield f"data: {data}\n\n"
            time.sleep(5)

    return Response(stream_with_context(event_stream()), mimetype='text/event-stream')

@app.route('/test')
def test_endpoint():
    """Test endpoint to verify web dashboard is responding"""
    print("🧪 TEST ENDPOINT CALLED - Web dashboard is working!")
    return jsonify({"status": "ok", "message": "Web dashboard test endpoint working", "timestamp": datetime.now().isoformat()})

@app.route('/api/chat', methods=['POST'])
def api_chat():
    """API endpoint for chat messages (legacy)"""
    try:
        data = request.get_json() or {}
        message = data.get('message', '').strip()
        print(f"🔵 WEB DASHBOARD: Chat API called with message: '{message}'")
        logger.info(f"Chat API called with message: {message}")

        if not message:
            return jsonify({'success': False, 'error': 'Message is required', 'code': 'validation_error'}), 400

        # Use unified processing path used by WebSocket handler
        response = _handle_chat_message_and_broadcast(message)
        print(f"🟢 WEB DASHBOARD: Chat API response: '{response}'")
        return jsonify({'success': True, 'message': 'ok', 'data': {'response': response}, 'response': response})
    except Exception as e:
        print(f"🔴 WEB DASHBOARD: Chat API error: {e}")
        logger.error(f"Chat API error: {e}")
        return jsonify({'success': False, 'error': 'Internal server error', 'code': 'internal_error'}), 500

@app.route('/api/llm', methods=['POST'])
def api_llm():
    """API endpoint for LLM processing"""
    try:
        data = request.get_json()
        message = data.get('message', '')
        
        if not message:
            return jsonify({'success': False, 'error': 'Message is required', 'code': 'validation_error'}), 400
        
        response = process_with_llm(message)
        return jsonify({'success': True, 'message': 'ok', 'data': {'response': response}, 'response': response})
        
    except Exception as e:
        logger.error(f"LLM API error: {e}")
        return jsonify({'success': False, 'error': str(e), 'code': 'internal_error'}), 500

@app.route('/api/mic-check', methods=['POST'])
def api_mic_check():
    """Proxy mic check to the voice assistant control server.
    Helps trigger OS mic permission prompt and report status to UI."""
    try:
        r = requests.post(f"http://{va_host}:{va_port}/mic-check", timeout=3)
        return (jsonify(r.json()), r.status_code)
    except Exception as e:
        logger.warning(f"Mic check proxy failed: {e}")
        return jsonify({'success': False, 'error': str(e), 'code': 'proxy_error'}), 500

@app.route('/api/assistant-speak', methods=['POST'])
def api_assistant_speak():
    """Proxy text-to-speech request to the voice assistant control server."""
    try:
        data = request.get_json() or {}
        text = (data.get('text') or '').strip()
        if not text:
            return jsonify({'success': False, 'error': 'text required', 'code': 'validation_error'}), 400
        r = requests.post(f"http://{va_host}:{va_port}/speak", json={'text': text}, timeout=5)
        return jsonify(r.json()), r.status_code
    except Exception as e:
        logger.warning(f"assistant speak proxy failed: {e}")
        return jsonify({'success': False, 'error': str(e), 'code': 'proxy_error'}), 500

@app.route('/api/assistant-event', methods=['POST'])
def api_assistant_event():
    """Internal endpoint for the Voice Assistant to push state events.
    Expected JSON: { "type": "speaking_started|speaking_ended|speaking_interrupted", "message": optional }
    """
    try:
        data = request.get_json() or {}
        event_type = str(data.get('type', '')).strip()
        message = str(data.get('message', '')).strip()

        if not event_type:
            return jsonify({'success': False, 'error': 'type is required', 'code': 'validation_error'}), 400

        # Broadcast to clients
        payload = {'type': event_type, 'timestamp': datetime.now().isoformat()}
        if message:
            payload['message'] = message
        try:
            socketio.emit('assistant_state', payload)
        except Exception as e:
            logger.warning(f"Failed to emit assistant_state: {e}")

        return jsonify({'success': True, 'status': 'ok'})
    except Exception as e:
        logger.error(f"Assistant event error: {e}")
        return jsonify({'success': False, 'error': str(e), 'code': 'internal_error'}), 500

@app.route('/api/voice', methods=['POST'])
def api_voice():
    """API endpoint for voice processing using Whisper"""
    try:
        data = request.get_json()
        audio_data = data.get('audio', '')
        
        if not audio_data:
            return jsonify({'success': False, 'error': 'Audio data is required', 'code': 'validation_error'}), 400
        
        # Process audio with Whisper
        transcription = process_voice_with_whisper(audio_data)
        
        # Update conversation state
        conversation_state['last_activity'] = datetime.now()
        conversation_state['current_speaker'] = 'user'
        conversation_state['message_count'] += 1
        
        # Broadcast conversation update
        socketio.emit('conversation_update', {
            'type': 'voice_transcription',
            'transcription': transcription,
            'timestamp': datetime.now().isoformat()
        })
        
        return jsonify({'success': True, 'message': 'ok', 'data': {'transcription': transcription}, 'transcription': transcription})
        
    except Exception as e:
        logger.error(f"Voice API error: {e}")
        return jsonify({'success': False, 'error': str(e), 'code': 'internal_error'}), 500

@app.route('/api/upload-documents', methods=['POST'])
def upload_documents():
    """Upload documents and forward to RAG server.
    Supports .txt natively; attempts .pdf (PyPDF2) and .docx (python-docx) when available.
    """
    try:
        if 'files' not in request.files:
            return jsonify({'success': False, 'error': 'No files provided'}), 400

        files = request.files.getlist('files')
        if not files or files[0].filename == '':
            return jsonify({'success': False, 'error': 'No files selected'}), 400

        success_count = 0
        errors = []
        forwarded = []
        token_list = CFG.get_rag_api_tokens()
        api_token = token_list[0] if token_list else 'change-me'

        for file in files:
            if not file or not file.filename:
                continue
            filename = file.filename
            name_lower = filename.lower()
            try:
                if name_lower.endswith('.txt'):
                    # Read text content
                    content_bytes = file.read()
                    try:
                        content = content_bytes.decode('utf-8', errors='ignore')
                    except Exception:
                        content = content_bytes.decode('latin-1', errors='ignore')

                    rag_url = f"http://{rag_host}:{rag_port}/api/documents"
                    resp = requests.post(
                        rag_url,
                        json={'content': content, 'title': filename, 'type': 'text'},
                        headers={'Authorization': f'Bearer {api_token}'},
                        timeout=10
                    )
                    if resp.status_code == 200:
                        success_count += 1
                        forwarded.append(filename)
                    else:
                        errors.append(f"{filename}: RAG responded {resp.status_code}")
                elif name_lower.endswith('.pdf'):
                    try:
                        import PyPDF2  # type: ignore
                    except Exception:
                        errors.append(f"{filename}: PDF support requires PyPDF2")
                        continue
                    # Read PDF bytes and extract text
                    from io import BytesIO
                    pdf_reader = PyPDF2.PdfReader(BytesIO(file.read()))
                    content = "\n".join([page.extract_text() or '' for page in pdf_reader.pages])
                    content = content.strip()
                    if not content:
                        errors.append(f"{filename}: no extractable text")
                        continue
                    rag_url = f"http://{rag_host}:{rag_port}/api/documents"
                    resp = requests.post(
                        rag_url,
                        json={'content': content, 'title': filename, 'type': 'pdf'},
                        headers={'Authorization': f'Bearer {api_token}'},
                        timeout=15
                    )
                    if resp.status_code == 200:
                        success_count += 1
                        forwarded.append(filename)
                    else:
                        errors.append(f"{filename}: RAG responded {resp.status_code}")
                elif name_lower.endswith('.docx'):
                    try:
                        import docx  # type: ignore
                    except Exception:
                        errors.append(f"{filename}: DOCX support requires python-docx")
                        continue
                    document = docx.Document(file)
                    paras = [p.text for p in document.paragraphs]
                    content = "\n".join([p for p in paras if p]).strip()
                    if not content:
                        errors.append(f"{filename}: empty or unreadable document")
                        continue
                    rag_url = f"http://{rag_host}:{rag_port}/api/documents"
                    resp = requests.post(
                        rag_url,
                        json={'content': content, 'title': filename, 'type': 'docx'},
                        headers={'Authorization': f'Bearer {api_token}'},
                        timeout=15
                    )
                    if resp.status_code == 200:
                        success_count += 1
                        forwarded.append(filename)
                    else:
                        errors.append(f"{filename}: RAG responded {resp.status_code}")
                else:
                    errors.append(f"{filename}: unsupported file type (txt, pdf, docx supported)")
            except Exception as e:
                logger.error(f"Failed to forward {filename} to RAG: {e}")
                errors.append(f"{filename}: {e}")

        return jsonify({
            'success': success_count > 0 and len(errors) == 0,
            'uploaded': forwarded,
            'errors': errors,
            'message': f'Processed {success_count} file(s). {len(errors)} error(s).'
        })

    except Exception as e:
        logger.error(f"Document upload error: {e}")
        return jsonify({'success': False, 'error': str(e), 'code': 'internal_error'}), 500

@app.route('/health')
def health_check():
    """Health check endpoint for service monitoring"""
    try:
        health_monitor = get_health_monitor()
        health_status = health_monitor.get_health_status()
        return jsonify(health_status)
    except Exception as e:
        logger.error(f"Health check error: {e}")
        return jsonify({
            'overall_status': 'unhealthy',
            'error': str(e),
            'timestamp': datetime.now().isoformat()
        }), 500

# WebSocket Event Handlers
def _serialize_conversation_state():
    try:
        cs = dict(conversation_state)
        la = cs.get('last_activity')
        if isinstance(la, datetime):
            cs['last_activity'] = la.isoformat()
        return cs
    except Exception:
        return conversation_state

@socketio.on('connect')
def handle_connect(auth=None):
    """Handle WebSocket connection"""
    # In Flask-SocketIO, we can get the client ID from the socketio context
    logger.info("Client connected")
    websocket_clients.add(id(request))
    
    # Send current state to new client
    emit('state_update', {
        'conversation_state': _serialize_conversation_state(),
        'system_stats': get_system_stats(),
        'service_status': service_status,
        'conversation_history': list(conversation_history)
    })

@socketio.on('disconnect')
def handle_disconnect():
    """Handle WebSocket disconnection"""
    logger.info("Client disconnected")
    websocket_clients.discard(id(request))

def _handle_chat_message_and_broadcast(message: str) -> str:
    """Unified chat processing path. Updates state/history and emits WebSocket events.
    Returns assistant response or error message."""
    try:
        # Correlation IDs
        user_msg_id = str(uuid.uuid4())
        # Update user message state
        conversation_state['active'] = True
        conversation_state['current_speaker'] = 'user'
        conversation_state['last_activity'] = datetime.now()
        conversation_state['message_count'] += 1

        conversation_history.append({
            'type': 'user_message',
            'content': message,
            'timestamp': datetime.now().isoformat(),
            'source': 'web',
            'id': user_msg_id
        })

        try:
            socketio.emit('conversation_update', {
                'type': 'user_message',
                'content': message,
                'timestamp': datetime.now().isoformat(),
                'id': user_msg_id
            })
        except Exception:
            pass

        logger.info(f"chat_in id={user_msg_id} len={len(message)} preview={message[:80]!r}")

        # Process message
        response = process_with_llm(message)

        # Update assistant state
        conversation_state['current_speaker'] = 'assistant'
        assistant_msg_id = str(uuid.uuid4())
        conversation_history.append({
            'type': 'assistant_message',
            'content': response,
            'timestamp': datetime.now().isoformat(),
            'source': 'llm',
            'id': assistant_msg_id,
            'reply_to': user_msg_id
        })

        try:
            socketio.emit('conversation_update', {
                'type': 'assistant_message',
                'content': response,
                'timestamp': datetime.now().isoformat(),
                'id': assistant_msg_id,
                'reply_to': user_msg_id
            })
        except Exception:
            pass

        logger.info(f"chat_out id={assistant_msg_id} reply_to={user_msg_id} len={len(response)} preview={response[:80]!r}")

        return response
    except Exception as e:
        logger.error(f"Chat processing error: {e}")
        error_msg = f"Error processing message: {str(e)}"
        conversation_history.append({
            'type': 'error_message',
            'content': error_msg,
            'timestamp': datetime.now().isoformat()
        })
        try:
            socketio.emit('conversation_update', {
                'type': 'error_message',
                'content': error_msg,
                'timestamp': datetime.now().isoformat()
            })
        except Exception:
            pass
        return error_msg

@socketio.on('chat_message')
def handle_chat_message(data):
    """Handle chat messages from web interface (WebSocket)"""
    message = (data or {}).get('message', '').strip()
    if not message:
        return
    _handle_chat_message_and_broadcast(message)

@socketio.on('interrupt_conversation')
def handle_interrupt_conversation():
    """Handle manual conversation interruption from web interface"""
    logger.info("Manual conversation interruption requested from web interface")
    
    conversation_state['interruption_count'] += 1
    conversation_state['last_activity'] = datetime.now()
    
    # Broadcast interruption event
    socketio.emit('conversation_update', {
        'type': 'interruption',
        'source': 'web_manual',
        'timestamp': datetime.now().isoformat()
    })
    
    # Preferred: HTTP call to voice assistant control endpoint
    sent = False
    try:
        va_url = f"http://{va_host}:{va_port}/interrupt"
        r = requests.post(va_url, timeout=2)
        if r.status_code == 200:
            sent = True
            logger.info("Interruption sent via HTTP to voice assistant")
    except Exception as e:
        logger.warning(f"HTTP interruption failed: {e}")

    if not sent:
        # Fallback: in-process message bus (likely a no-op across processes)
        try:
            from .message_bus_client import MessageBusClient
            bus_client = MessageBusClient(service_type="web_dashboard")
            bus_client.start()
            interruption_msg = {
                'type': 'interruption',
                'target': 'voice_assistant',
                'source': 'web_dashboard',
                'timestamp': datetime.now().isoformat(),
                'reason': 'user_interrupt_from_web'
            }
            bus_client.send_message(interruption_msg)
            bus_client.stop()
            logger.info("Interruption signal attempted via message bus")
        except Exception as e:
            logger.warning(f"Failed to send interruption via message bus: {e}")
    
    emit('system_status', {
        'type': 'interruption_sent',
        'message': 'Interruption signal sent to voice assistant',
        'timestamp': datetime.now().isoformat()
    })

@app.route('/api/interrupt', methods=['POST'])
def api_interrupt():
    """HTTP fallback to request conversation interruption"""
    try:
        handle_interrupt_conversation()
        return jsonify({'success': True, 'status': 'ok'}), 200
    except Exception as e:
        logger.error(f"HTTP interrupt error: {e}")
        return jsonify({'success': False, 'status': 'error', 'error': str(e), 'code': 'internal_error'}), 500

@socketio.on('start_voice_recording')
def handle_start_voice_recording():
    """Handle voice recording start from web interface"""
    conversation_state['active'] = True
    conversation_state['current_speaker'] = 'user'
    
    emit('conversation_update', {
        'type': 'voice_recording_started',
        'timestamp': datetime.now().isoformat()
    })

@socketio.on('stop_voice_recording')
def handle_stop_voice_recording():
    """Handle voice recording stop from web interface"""
    conversation_state['last_activity'] = datetime.now()
    
    emit('conversation_update', {
        'type': 'voice_recording_stopped',
        'timestamp': datetime.now().isoformat()
    })

@socketio.on('clear_conversation')
def handle_clear_conversation():
    """Handle conversation history clearing"""
    global conversation_history, conversation_state
    
    conversation_history.clear()
    conversation_state['message_count'] = 0
    conversation_state['interruption_count'] = 0
    conversation_state['active'] = False
    conversation_state['current_speaker'] = None
    conversation_state['conversation_id'] = str(uuid.uuid4())
    
    emit('conversation_update', {
        'type': 'conversation_cleared',
        'timestamp': datetime.now().isoformat()
    })

def process_voice_with_whisper(base64_audio: str) -> str:
    """Process voice input. Accepts browser-recorded audio (WebM/Opus or similar),
    converts to 16kHz mono WAV via ffmpeg, and transcribes with Whisper CLI if available
    (falls back to Python whisper if installed)."""
    import base64
    import tempfile
    import os

    try:
        # Decode incoming base64
        audio_bytes = base64.b64decode(base64_audio.split(',')[1] if ',' in base64_audio else base64_audio)

        # Write raw container to a temp file (assume webm if unknown)
        with tempfile.NamedTemporaryFile(suffix='.webm', delete=False) as src_file:
            src_file.write(audio_bytes)
            src_path = src_file.name

        # Convert to WAV (16kHz mono PCM16) using ffmpeg
        with tempfile.NamedTemporaryFile(suffix='.wav', delete=False) as wav_file:
            wav_path = wav_file.name

        ffmpeg_cmd = [
            'ffmpeg', '-y', '-i', src_path,
            '-ac', '1', '-ar', '16000', '-f', 'wav', wav_path
        ]
        try:
            conv = subprocess.run(ffmpeg_cmd, capture_output=True, text=True, timeout=30)
            if conv.returncode != 0:
                logger.error(f"ffmpeg conversion failed: {conv.stderr}")
                return "Audio conversion failed (ffmpeg). Ensure ffmpeg is installed."
        except FileNotFoundError:
            return "ffmpeg not found. Please install ffmpeg for voice input."

        # Prefer Whisper CLI if available
        whisper_bin = os.path.abspath("models/whisper.cpp/build/bin/whisper-cli")
        whisper_model = os.path.abspath("models/whisper.cpp/models/ggml-base.en.bin")

        if os.path.exists(whisper_bin) and os.path.exists(whisper_model):
            try:
                # Use whisper.cpp proper flags: -otxt to write txt, -of to set output base (without extension)
                base = os.path.splitext(wav_path)[0]
                result = subprocess.run([
                    whisper_bin, '-m', whisper_model, '-f', wav_path, '-otxt', '-of', base
                ], capture_output=True, text=True, timeout=60)

                if result.returncode == 0:
                    txt_file = base + '.txt'
                    if os.path.exists(txt_file):
                        with open(txt_file, 'r') as f:
                            transcription = (f.read() or '').strip()
                        # Treat special token from whisper.cpp as no-speech
                        if '[BLANK_AUDIO]' in transcription:
                            transcription = ''
                        os.unlink(txt_file)
                        return transcription or "No speech detected"
                    else:
                        return "Speech detected but no transcription generated"
                else:
                    logger.error(f"Whisper CLI failed: {result.stderr}")
                    return f"Transcription failed: {result.stderr}"
            finally:
                try:
                    os.unlink(wav_path)
                except Exception:
                    pass
        else:
            # Fallback to Python whisper if available
            try:
                import numpy as np
                import soundfile as sf
                try:
                    import whisper
                except Exception:
                    return "Neither Whisper CLI nor python-whisper are available."

                # Load wav and transcribe
                data, sr = sf.read(wav_path)
                if sr != 16000:
                    # Should not happen due to ffmpeg, but guard anyway
                    logger.warning(f"Unexpected sample rate {sr}, continuing")
                model = whisper.load_model("tiny")
                result = model.transcribe(data, language="en")
                transcription = result.get("text", "").strip()
                return transcription or "No speech detected"
            finally:
                try:
                    os.unlink(wav_path)
                except Exception:
                    pass
    except subprocess.TimeoutExpired:
        return "Transcription timed out. Audio might be too long."
    except Exception as e:
        logger.error(f"Voice processing error: {e}")
        return f"Voice processing error: {str(e)}"
    finally:
        try:
            if 'src_path' in locals() and os.path.exists(src_path):
                os.unlink(src_path)
        except Exception:
            pass

def process_with_llm(message: str) -> str:
    """Process message with the LLM and tools"""
    try:
        # Check if llama server is running
        try:
            response = requests.get(llm_models_endpoint, timeout=2)
            if response.status_code != 200:
                return "LLM server is not running. Please start the orchestrator first."
        except:
            return "LLM server is not accessible. Please start the orchestrator first."
        
        # Check if user is requesting tool usage
        tool_result = process_tools(message)
        if tool_result:
            return tool_result
        
        # Check RAG system for relevant context
        rag_context = get_rag_context(message)
        
        # Send to llama.cpp server for general conversation
        system_prompt = "You are MacBot, a helpful AI assistant with access to macOS tools. Be concise and helpful."
        if rag_context:
            system_prompt += f"\n\nRelevant context from knowledge base: {rag_context}"
        
        payload = {
            "model": "local",
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": message}
            ],
            "temperature": 0.7,
            "max_tokens": 500
        }
        
        chat_endpoint = CFG.get_llm_chat_endpoint()
        response = requests.post(
            chat_endpoint,
            json=payload,
            timeout=30
        )
        
        if response.status_code == 200:
            result = response.json()
            return result['choices'][0]['message']['content']
        else:
            logger.error(f"LLM request failed: {response.status_code}")
            return f"LLM processing failed (HTTP {response.status_code})"
            
    except requests.exceptions.Timeout:
        return "LLM request timed out. The model might be processing a large request."
    except Exception as e:
        logger.error(f"LLM processing error: {e}")
        return f"LLM processing error: {str(e)}"

def process_tools(message: str) -> Optional[str]:
    """Process message for tool usage"""
    message_lower = message.lower()
    
    try:
        # Web search
        if "search" in message_lower and ("weather" in message_lower or "for" in message_lower):
            # Extract search query
            if "weather" in message_lower:
                # Extract location from weather search
                if "st louis" in message_lower or "st. louis" in message_lower:
                    location = "St. Louis, MO"
                elif "weather for" in message_lower:
                    location = message_lower.split("weather for")[-1].strip()
                else:
                    location = "current location"
                
                # Use Safari to search weather
                search_url = f"https://www.google.com/search?q=weather+{location.replace(' ', '+')}"
                subprocess.run(['open', '-a', 'Safari', search_url], check=True)  # type: ignore
                return f"I've opened Safari and searched for weather in {location}. The results should be displayed in your browser."
            
            # General web search
            query = message_lower.replace("search", "").replace("for", "").replace("the", "").strip()
            search_url = f"https://www.google.com/search?q={query.replace(' ', '+')}"
            subprocess.run(['open', '-a', 'Safari', search_url], check=True)  # type: ignore
            return f"I've opened Safari and searched for '{query}'. The results should be displayed in your browser."
        
        # Website browsing
        elif "browse" in message_lower or "open website" in message_lower or "go to" in message_lower:
            # Extract URL
            words = message.split()
            for word in words:
                if word.startswith(("http://", "https://", "www.")):
                    subprocess.run(['open', '-a', 'Safari', word], check=True)  # type: ignore
                    return f"I've opened {word} in Safari for you to browse."
            
            # If no URL found, try to construct one
            if "weather.com" in message_lower:
                subprocess.run(['open', '-a', 'Safari', 'https://www.weather.com'], check=True)  # type: ignore
                return "I've opened Weather.com in Safari for you."
            elif "accuweather" in message_lower:
                subprocess.run(['open', '-a', 'Safari', 'https://www.accuweather.com'], check=True)  # type: ignore
                return "I've opened AccuWeather in Safari for you."
        
        # App opening
        elif "open app" in message_lower or "launch" in message_lower:
            app_name = message_lower.replace("open app", "").replace("launch", "").strip()
            subprocess.run(['open', '-a', app_name], check=True)  # type: ignore
            return f"I've opened {app_name} for you."
        
        # Screenshot
        elif "screenshot" in message_lower or "take picture" in message_lower:
            import time
            timestamp = int(time.time())
            filename = f"screenshot_{timestamp}.png"
            filepath = os.path.expanduser(f"~/Desktop/{filename}")
            subprocess.run(['screencapture', filepath], check=True)  # type: ignore
            return f"I've taken a screenshot and saved it to your Desktop as {filename}"
        
        # System info
        elif "system info" in message_lower or "system status" in message_lower:
            import psutil
            cpu_percent = psutil.cpu_percent(interval=1)
            memory = psutil.virtual_memory()
            disk = psutil.disk_usage('/')
            return f"System Status: CPU {cpu_percent}%, RAM {memory.percent}%, Disk {disk.percent}%"
        
        # No tool match found
        return None
        
    except Exception as e:
        logger.error(f"Tool processing error: {e}")
        return f"Tool execution failed: {str(e)}"

def get_rag_context(query: str) -> Optional[str]:
    """Get relevant context from RAG system"""
    try:
        # Check if RAG service is running
        try:
            response = requests.get(f"http://{rag_host}:{rag_port}/health", timeout=2)
            if response.status_code != 200:
                return None
        except:
            return None
        
        # Search RAG system
        rag_response = requests.post(
            f"http://{rag_host}:{rag_port}/api/search",
            json={'query': query},
            timeout=5
        )
        
        if rag_response.status_code == 200:
            results = rag_response.json().get('results', [])
            if results:
                # Return the most relevant result
                best_result = results[0]
                return f"{best_result['metadata']['title']}: {best_result['content'][:200]}..."
        
        return None
        
    except Exception as e:
        logger.error(f"RAG context error: {e}")
        return None

def start_dashboard(host='0.0.0.0', port=3000):
    """Start the web dashboard with WebSocket support"""
    logger.info(f"Starting MacBot Web Dashboard on http://{host}:{port}")
    
    # Start background monitoring
    def background_monitor():
        while True:
            try:
                check_service_health()
                # Emit real-time updates to all connected clients
                socketio.emit('state_update', {
                    'system_stats': get_system_stats(),
                    'service_status': service_status,
                    'conversation_state': _serialize_conversation_state(),
                    'conversation_history': list(conversation_history)
                })
                time.sleep(5)  # Update every 5 seconds
            except Exception as e:
                logger.error(f"Background monitoring error: {e}")
                time.sleep(10)
    
    monitor_thread = threading.Thread(target=background_monitor, daemon=True)
    monitor_thread.start()
    
    try:
        socketio.run(app, host=host, port=port, debug=False, use_reloader=False, allow_unsafe_werkzeug=True)
    except Exception as e:
        logger.error(f"Failed to start web dashboard: {e}")
        raise

def main():
    host, port = CFG.get_web_dashboard_host_port()
    try:
        start_dashboard(host=host, port=port)
    except Exception:
        start_dashboard()

if __name__ == '__main__':
    main()

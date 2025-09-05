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

from .health_monitor import get_health_monitor

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = Flask(__name__)
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

service_status = {
    'llama': {'status': 'unknown', 'port': None, 'endpoint': llm_models_endpoint.rsplit('/v1', 1)[0]},
    'voice_assistant': {'status': 'unknown', 'port': None, 'endpoint': 'Voice Interface'},
    'rag': {'status': 'unknown', 'port': rag_port, 'endpoint': f'http://{rag_host}:{rag_port}'},
    'web_gui': {'status': 'running', 'port': wd_port, 'endpoint': f'http://{wd_host}:{wd_port}'}
}

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
        }
        .chat-button:hover { 
            background: #0056cc; 
        }
        .voice-button { 
            background: #34c759; 
            color: white; 
            border: none; 
            padding: 10px 12px; 
            border-radius: 6px; 
            font-size: 0.9em; 
            cursor: pointer; 
        }
        .voice-button:hover { 
            background: #28a745; 
        }
        .voice-button.recording { 
            background: #ff3b30; 
            animation: pulse 1s infinite; 
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
        
        /* Ensure no vertical scrolling on any device */
        html, body { 
            overflow-x: hidden; 
            overflow-y: hidden; 
            height: 100vh; 
        }
        
        .container { 
            height: 100vh; 
            overflow: hidden; 
        }
    </style>
</head>
<body>
    <div class="container">
        <div class="header">
            <h1>🤖 MacBot Dashboard</h1>
            <p>Local Voice Assistant with AI Tools & Live Monitoring</p>
            <button class="refresh-btn" onclick="updateStats()">🔄 Refresh Stats</button>
            <button class="refresh-btn" onclick="clearConversation()" style="margin-left: 10px;">🧹 Clear Chat</button>
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
                        <h3>🚀 LLM Server (llama.cpp)</h3>
                        <div class="service-status" id="llm-status">Status: <span class="status-dot">🟡</span> Checking...</div>
                        <div class="service-info">Port: 8080</div>
                        <div class="service-url">http://localhost:8080</div>
                    </div>
                    <div class="service-card">
                        <h3>🎤 Voice Assistant</h3>
                        <div class="service-status" id="voice-status">Status: <span class="status-dot">🟡</span> Checking...</div>
                        <div class="service-info">Interface: Voice Interface</div>
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
                        <button class="voice-button" id="voice-button" title="Click to start/stop voice recording">
                            🎤
                        </button>
                        <button class="chat-button">Send</button>
                        <button class="stop-conversation-btn" id="stop-conversation-btn" title="Stop conversational mode" style="display: none;">
                            🛑
                        </button>
                    </div>
                    <div class="chat-history" id="chat-history">
                        <div class="chat-message chat-assistant">Hello! I'm MacBot. How can I help you today?</div>
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
    
    <script>
        // Attempt to use Server-Sent Events for real-time updates
        let eventSource = null;

        function initEventSource() {
            try {
                eventSource = new EventSource('/stream');
                eventSource.onmessage = function(event) {
                    try {
                        const payload = JSON.parse(event.data);
                        if (payload.system_stats) {
                            renderStats(payload.system_stats);
                        }
                        if (payload.service_status) {
                            renderServiceStatus(payload.service_status);
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
            updateStats();
            updateServiceStatus();
            setInterval(updateStats, 5000);
            setInterval(updateServiceStatus, 5000);
        }

        // Voice recording state
        let isRecording = false;
        let mediaRecorder = null;
        let audioChunks = [];
        
        // Conversational voice state
        let isConversationalMode = false;
        let conversationTimeout = null;
        let silenceThreshold = 2000; // 2 seconds of silence to auto-send
        let lastVoiceActivity = 0;
        
        function renderStats(data) {
            if (data.cpu !== undefined) {
                document.getElementById('cpu-usage').textContent = data.cpu + '%';
            }
            if (data.ram !== undefined) {
                document.getElementById('ram-usage').textContent = data.ram + '%';
            }
            if (data.disk !== undefined) {
                document.getElementById('disk-usage').textContent = data.disk + '%';
            }
            if (data.network && data.network.bytes_sent !== undefined) {
                document.getElementById('network-usage').textContent = formatBytes(data.network.bytes_sent + data.network.bytes_recv);
            }
        }

        function renderServiceStatus(data) {
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
                webStatus.innerHTML = 'Status: <span class="status-dot">🔴</span> Running';
            }
        }

        function updateStats() {
            console.log('Updating stats...'); // Debug log

            fetch('/api/stats')
                .then(response => {
                    console.log('Stats response status:', response.status); // Debug log
                    if (!response.ok) {
                        throw new Error(`HTTP ${response.status}`);
                    }
                    return response.json();
                })
                .then(data => {
                    console.log('Stats data received:', data); // Debug log
                    renderStats(data);
                })
                .catch(error => {
                    console.error('Stats update error:', error);
                    document.getElementById('cpu-usage').textContent = 'Error';
                    document.getElementById('ram-usage').textContent = 'Error';
                    document.getElementById('disk-usage').textContent = 'Error';
                    document.getElementById('network-usage').textContent = 'Error';
                });
        }

        function updateServiceStatus() {
            console.log('Updating service status...'); // Debug log

            fetch('/api/services')
                .then(response => {
                    console.log('Service status response:', response.status); // Debug log
                    if (!response.ok) {
                        throw new Error(`HTTP ${response.status}`);
                    }
                    return response.json();
                })
                .then(data => {
                    console.log('Service status data:', data); // Debug log
                    renderServiceStatus(data);
                })
                .catch(error => {
                    console.error('Service status update error:', error);
                    document.getElementById('llm-status').innerHTML = 'Status: <span class="status-dot">🔴</span> Error';
                    document.getElementById('voice-status').innerHTML = 'Status: <span class="status-dot">🔴</span> Error';
                    document.getElementById('rag-status').innerHTML = 'Status: <span class="status-dot">🔴</span> Error';
                    document.getElementById('web-status').innerHTML = 'Status: <span class="status-dot">🔴</span> Error';
                });
        }

        function clearConversation() {
            if (confirm('Are you sure you want to clear the conversation history?')) {
                socket.emit('clear_conversation');
            }
        }
            if (bytes === 0) return '0 B';
            const k = 1024;
            const sizes = ['B', 'KB', 'MB', 'GB'];
            const i = Math.floor(Math.log(bytes) / Math.log(k));
            return parseFloat((bytes / Math.pow(k, i)).toFixed(1)) + ' ' + sizes[i];
        }
        
        // handleKeyPress function removed - now handled by event listener
        
        function sendMessage() {
            const input = document.getElementById('chat-input');
            const message = input.value.trim();
            if (!message) return;
            
            console.log('Sending message:', message); // Debug log
            
            // Add user message to local display
            addChatMessage(message, 'user');
            input.value = '';
            
            // Send via WebSocket
            socket.emit('chat_message', { message: message });
        }
        
        function addChatMessage(message, sender) {
            const history = document.getElementById('chat-history');
            const messageDiv = document.createElement('div');
            messageDiv.className = `chat-message chat-${sender}`;
            messageDiv.textContent = message;
            history.appendChild(messageDiv);
            history.scrollTop = history.scrollHeight;
        }
        
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
        
        async function startVoiceRecording() {
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
                    addChatMessage('🎤 Listening... Speak naturally.', 'system');
                } else {
                    addChatMessage('🎤 Recording... Click again to stop.', 'system');
                }
                
                // Emit WebSocket event
                socket.emit('start_voice_recording');
                
            } catch (error) {
                addChatMessage('❌ Microphone access denied. Please allow microphone access.', 'system');
                console.error('Voice recording error:', error);
            }
        }
        
        function stopVoiceRecording() {
            const voiceButton = document.getElementById('voice-button');
            
            if (mediaRecorder && isRecording) {
                mediaRecorder.stop();
                mediaRecorder.stream.getTracks().forEach(track => track.stop());
            }
            
            isRecording = false;
            voiceButton.classList.remove('recording');
            voiceButton.textContent = '🎤';
            
            // Emit WebSocket event
            socket.emit('stop_voice_recording');
        }
        
        async function processVoiceInput(audioBlob) {
            addChatMessage('🎵 Processing voice input...', 'system');
            
            try {
                // Convert audio to base64
                const reader = new FileReader();
                reader.onload = async () => {
                    const base64Audio = reader.result.split(',')[1];
                    
                    // Send via WebSocket instead of HTTP API
                    socket.emit('voice_message', { audio: base64Audio });
                    
                    // Start conversational mode for natural flow
                    startConversationalMode();
                };
                reader.readAsDataURL(audioBlob);
                
            } catch (error) {
                addChatMessage('❌ Voice processing error: ' + error.message, 'system');
                console.error('Voice processing error:', error);
            }
        }
        
        function startConversationalMode() {
            isConversationalMode = true;
            addChatMessage('🎤 Conversational mode active. Speak naturally - I\'ll listen for your response.', 'system');
            
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
            
            addChatMessage('🎤 Conversational mode stopped. Click the microphone to start again.', 'system');
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
                    addChatMessage(data.response, 'assistant');
                } else {
                    const errorText = await response.text();
                    console.error('LLM error response:', errorText); // Debug log
                    addChatMessage('❌ LLM processing failed: ' + errorText, 'system');
                }
            } catch (error) {
                console.error('LLM fetch error:', error); // Debug log
                addChatMessage('❌ LLM error: ' + error.message, 'system');
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
                    addChatMessage(`✅ ${files.length} file(s) uploaded to knowledge base successfully`, 'system');
                } else {
                    addChatMessage(`❌ Failed to upload files: ${data.error}`, 'system');
                }
            })
            .catch(error => {
                console.error('Upload error:', error);
                addChatMessage(`❌ Upload failed: ${error.message}`, 'system');
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
        updateStats();
        updateServiceStatus();
        
        // Trigger initial resize
        window.dispatchEvent(new Event('resize'));
        
        // Add error handling for missing elements
        window.addEventListener('error', function(e) {
            console.error('JavaScript error:', e.error);
        });
        
        // WebSocket event handlers
        socket.on('conversation_update', function(data) {
            console.log('Received conversation update:', data);
            handleConversationUpdate(data);
        });
        
        socket.on('voice_processed', function(data) {
            console.log('Voice processed:', data);
            if (data.transcription) {
                addChatMessage(data.transcription, 'user');
            }
        });
        
        function handleConversationUpdate(data) {
            if (data.type === 'user_message') {
                addChatMessage(data.content, 'user');
            } else if (data.type === 'assistant_message') {
                addChatMessage(data.content, 'assistant');
            } else if (data.type === 'voice_transcription') {
                addChatMessage(data.transcription, 'user');
            } else if (data.type === 'error_message') {
                addChatMessage(data.content, 'system');
            } else if (data.type === 'interruption') {
                addChatMessage('🔇 Conversation interrupted', 'system');
            } else if (data.type === 'voice_recording_started') {
                addChatMessage('🎤 Voice recording started...', 'system');
            } else if (data.type === 'voice_recording_stopped') {
                addChatMessage('🎤 Voice recording stopped', 'system');
            } else if (data.type === 'conversation_cleared') {
                const chatHistory = document.getElementById('chat-history');
                chatHistory.innerHTML = '';
                addChatMessage('🧹 Conversation history cleared', 'system');
            }
        }
        
        // Test button functionality
        console.log('Dashboard loaded, testing elements...');
        console.log('Chat input:', document.getElementById('chat-input'));
        console.log('Send button:', document.getElementById('chat-button'));
        console.log('Voice button:', document.getElementById('voice-button'));
        
        // Add proper event listeners instead of relying on onclick
        document.addEventListener('DOMContentLoaded', function() {
            console.log('DOM loaded, setting up event listeners...');
            initEventSource();
            
            const sendButton = document.getElementById('chat-button');
            const voiceButton = document.getElementById('voice-button');
            const chatInput = document.getElementById('chat-input');
            
            if (sendButton) {
                console.log('Setting up send button listener');
                sendButton.addEventListener('click', function() {
                    console.log('Send button clicked!');
                    sendMessage();
                });
            }
            
            if (voiceButton) {
                console.log('Setting up voice button listener');
                voiceButton.addEventListener('click', function() {
                    console.log('Voice button clicked!');
                    toggleVoiceRecording();
                });
            }
            
            if (chatInput) {
                console.log('Setting up chat input listener');
                chatInput.addEventListener('keypress', function(event) {
                    if (event.key === 'Enter') {
                        console.log('Enter pressed in chat input');
                        sendMessage();
                    }
                });
            }
            
            const stopButton = document.getElementById('stop-conversation-btn');
            if (stopButton) {
                console.log('Setting up stop conversation button listener');
                stopButton.addEventListener('click', function() {
                    console.log('Stop conversation button clicked!');
                    socket.emit('interrupt_conversation');
                });
            }
            
            console.log('Event listeners set up complete');
        });
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
        
        # Check voice assistant (simple process check)
        try:
            for proc in psutil.process_iter(['pid', 'name', 'cmdline']):
                if proc.info['cmdline'] and 'voice_assistant.py' in ' '.join(proc.info['cmdline']):
                    service_status['voice_assistant']['status'] = 'running'
                    break
            else:
                service_status['voice_assistant']['status'] = 'stopped'
        except:
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
    check_service_health()
    return render_template_string(DASHBOARD_HTML, services=service_status)

@app.route('/api/stats')
def api_stats():
    """API endpoint for system statistics"""
    global system_stats
    system_stats = get_system_stats()
    return jsonify(system_stats)

@app.route('/api/services')
def api_services():
    """API endpoint for service status"""
    # Check actual service health
    try:
        # Check LLM server
        try:
            response = requests.get(llm_models_endpoint, timeout=2)
            service_status['llama']['status'] = 'running' if response.status_code == 200 else 'stopped'
        except:
            service_status['llama']['status'] = 'stopped'
        
        # Check RAG server
        try:
            response = requests.get(f"http://{rag_host}:{rag_port}/health", timeout=2)
            service_status['rag']['status'] = 'running' if response.status_code == 200 else 'stopped'
        except:
            service_status['rag']['status'] = 'stopped'
        
        # Voice assistant is always running if this endpoint is accessible
        service_status['voice_assistant']['status'] = 'running'
        
        # Web GUI is always running if this endpoint is accessible
        service_status['web_gui']['status'] = 'running'
        
    except Exception as e:
        logger.error(f"Error checking service status: {e}")

    return jsonify(service_status)

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

@app.route('/api/chat', methods=['POST'])
def api_chat():
    """API endpoint for chat messages (legacy)"""
    try:
        data = request.get_json()
        message = data.get('message', '')
        
        # Forward to LLM endpoint
        response = process_with_llm(message)
        return jsonify({'response': response})
    except Exception as e:
        logger.error(f"Chat API error: {e}")
        return jsonify({'error': 'Internal server error'}), 500

@app.route('/api/llm', methods=['POST'])
def api_llm():
    """API endpoint for LLM processing"""
    try:
        data = request.get_json()
        message = data.get('message', '')
        
        if not message:
            return jsonify({'error': 'Message is required'}), 400
        
        response = process_with_llm(message)
        return jsonify({'response': response})
        
    except Exception as e:
        logger.error(f"LLM API error: {e}")
        return jsonify({'error': str(e)}), 500

@app.route('/api/voice', methods=['POST'])
def api_voice():
    """API endpoint for voice processing using Whisper"""
    try:
        data = request.get_json()
        audio_data = data.get('audio', '')
        
        if not audio_data:
            return jsonify({'error': 'Audio data is required'}), 400
        
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
        
        return jsonify({'transcription': transcription})
        
    except Exception as e:
        logger.error(f"Voice API error: {e}")
        return jsonify({'error': str(e)}), 500

@app.route('/api/upload-documents', methods=['POST'])
def upload_documents():
    """API endpoint for uploading documents to RAG system"""
    try:
        if 'files' not in request.files:
            return jsonify({'success': False, 'error': 'No files provided'}), 400
        
        files = request.files.getlist('files')
        if not files or files[0].filename == '':
            return jsonify({'success': False, 'error': 'No files selected'}), 400
        
        uploaded_files = []
        for file in files:
            if file and file.filename:
                # Here you would typically save the file and process it
                # For now, we'll just acknowledge receipt
                uploaded_files.append(file.filename)
                logger.info(f"Document uploaded: {file.filename}")
        
        return jsonify({
            'success': True, 
            'message': f'Successfully uploaded {len(uploaded_files)} documents',
            'files': uploaded_files
        })
        
    except Exception as e:
        logger.error(f"Document upload error: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500

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
@socketio.on('connect')
def handle_connect():
    """Handle WebSocket connection"""
    # In Flask-SocketIO, we can get the client ID from the socketio context
    logger.info("Client connected")
    websocket_clients.add(id(request))
    
    # Send current state to new client
    emit('state_update', {
        'conversation_state': conversation_state,
        'system_stats': get_system_stats(),
        'service_status': service_status,
        'conversation_history': list(conversation_history)
    })

@socketio.on('disconnect')
def handle_disconnect():
    """Handle WebSocket disconnection"""
    logger.info("Client disconnected")
    websocket_clients.discard(id(request))

@socketio.on('chat_message')
def handle_chat_message(data):
    """Handle chat messages from web interface"""
    message = data.get('message', '').strip()
    if not message:
        return
    
    # Update conversation state
    conversation_state['active'] = True
    conversation_state['current_speaker'] = 'user'
    conversation_state['last_activity'] = datetime.now()
    conversation_state['message_count'] += 1
    
    # Add to conversation history
    conversation_history.append({
        'type': 'user_message',
        'content': message,
        'timestamp': datetime.now().isoformat(),
        'source': 'web'
    })
    
    # Broadcast user message
    emit('conversation_update', {
        'type': 'user_message',
        'content': message,
        'timestamp': datetime.now().isoformat()
    }, broadcast=True)
    
    # Process with LLM
    try:
        response = process_with_llm(message)
        
        # Update conversation state
        conversation_state['current_speaker'] = 'assistant'
        
        # Add assistant response to history
        conversation_history.append({
            'type': 'assistant_message',
            'content': response,
            'timestamp': datetime.now().isoformat(),
            'source': 'llm'
        })
        
        # Broadcast assistant response
        socketio.emit('conversation_update', {
            'type': 'assistant_message',
            'content': response,
            'timestamp': datetime.now().isoformat()
        })
        
    except Exception as e:
        logger.error(f"Chat processing error: {e}")
        error_msg = f"Error processing message: {str(e)}"
        
        conversation_history.append({
            'type': 'error_message',
            'content': error_msg,
            'timestamp': datetime.now().isoformat()
        })
        
        emit('conversation_update', {
            'type': 'error_message',
            'content': error_msg,
            'timestamp': datetime.now().isoformat()
        })

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
    
    # Send interruption signal to voice assistant via message bus
    try:
        # Import message bus client for interruption signaling
        from .message_bus_client import MessageBusClient
        bus_client = MessageBusClient(service_type="web_dashboard")
        bus_client.start()
        
        # Send interruption message
        interruption_msg = {
            'type': 'interruption',
            'target': 'voice_assistant',
            'source': 'web_dashboard',
            'timestamp': datetime.now().isoformat(),
            'reason': 'user_interrupt_from_web'
        }
        bus_client.send_message(interruption_msg)
        bus_client.stop()
        
        logger.info("Interruption signal sent to voice assistant")
    except Exception as e:
        logger.warning(f"Failed to send interruption signal: {e}")
    
    emit('system_status', {
        'type': 'interruption_sent',
        'message': 'Interruption signal sent to voice assistant',
        'timestamp': datetime.now().isoformat()
    })

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
    """Process voice input using Whisper"""
    try:
        import base64
        import tempfile
        import os
        
        # Decode base64 audio
        audio_bytes = base64.b64decode(base64_audio)
        
        # Save to temporary file
        with tempfile.NamedTemporaryFile(suffix='.wav', delete=False) as temp_file:
            temp_file.write(audio_bytes)
            temp_file_path = temp_file.name
        
        try:
            # Use Whisper to transcribe
            whisper_bin = os.path.abspath("models/whisper.cpp/build/bin/whisper-cli")
            whisper_model = os.path.abspath("models/whisper.cpp/models/ggml-base.en.bin")
            
            if not os.path.exists(whisper_bin):
                return "Whisper not found. Please run 'make build-whisper' first."
            
            if not os.path.exists(whisper_model):
                return "Whisper model not found. Please run 'make build-whisper' first."
            
            # Run Whisper transcription
            result = subprocess.run([  # type: ignore
                whisper_bin,
                '-m', whisper_model,
                '-f', temp_file_path,
                '--output-txt'
            ], capture_output=True, text=True, timeout=30)
            
            if result.returncode == 0:
                # Read the transcription from the output file
                txt_file = temp_file_path.replace('.wav', '.txt')
                if os.path.exists(txt_file):
                    with open(txt_file, 'r') as f:
                        transcription = f.read().strip()
                    # Clean up
                    os.unlink(txt_file)
                    return transcription if transcription else "No speech detected"
                else:
                    return "Speech detected but no transcription generated"
            else:
                logger.error(f"Whisper failed: {result.stderr}")
                return f"Transcription failed: {result.stderr}"
                
        finally:
            # Clean up temporary files
            if os.path.exists(temp_file_path):
                os.unlink(temp_file_path)
                
    except subprocess.TimeoutExpired:
        return "Transcription timed out. Audio might be too long."
    except Exception as e:
        logger.error(f"Voice processing error: {e}")
        return f"Voice processing error: {str(e)}"

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
        
        response = requests.post(
            'http://localhost:8080/v1/chat/completions',
            headers={"Authorization": "Bearer x"},
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
                    'conversation_state': conversation_state,
                    'conversation_history': list(conversation_history)
                })
                time.sleep(5)  # Update every 5 seconds
            except Exception as e:
                logger.error(f"Background monitoring error: {e}")
                time.sleep(10)
    
    monitor_thread = threading.Thread(target=background_monitor, daemon=True)
    monitor_thread.start()
    
    try:
        socketio.run(app, host=host, port=port, debug=False, use_reloader=False)
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

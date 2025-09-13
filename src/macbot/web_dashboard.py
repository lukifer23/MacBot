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
from flask import Flask, render_template, jsonify, request, Response, stream_with_context
from flask_socketio import SocketIO, emit  # type: ignore
import logging
from .logging_utils import setup_logger
from . import tools as tools_mod

from .health_monitor import get_health_monitor

# Configure logging (unified)
logger = setup_logger("macbot.web_dashboard", "logs/web_dashboard.log")

app = Flask(__name__, static_folder='static', static_url_path='/static')

# Configure CORS properly
try:
    from flask_cors import CORS
    CORS(app, origins=["http://localhost:3000", "http://127.0.0.1:3000", "http://192.168.1.38:3000"])
    # CORS enabled for web dashboard
except ImportError:
    pass

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

# Small helper to improve resiliency of proxied HTTP calls
def _request_with_retry(method: str, url: str, *, json_body=None, timeout: float = 5.0, retries: int = 2, backoff: float = 0.2):
    last_exc = None
    for i in range(max(1, retries + 1)):
        try:
            if method == 'GET':
                return requests.get(url, timeout=timeout)
            elif method == 'POST':
                return requests.post(url, json=json_body or {}, timeout=timeout)
            else:
                raise ValueError('unsupported method')
        except Exception as e:
            last_exc = e
            try:
                time.sleep(backoff)
            except Exception:
                pass
            backoff *= 2
    raise last_exc if last_exc else RuntimeError('request failed')

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
        orchestrator_ok = False
        try:
            orc_resp = _request_with_retry('GET', f"http://{orc_host}:{orc_port}/services", timeout=1.5, retries=1)
            if orc_resp.status_code == 200:
                data = orc_resp.json().get('services', {})
                for name in service_status.keys():
                    if name in data:
                        service_status[name]['status'] = 'running' if data[name].get('running') else 'stopped'
                orchestrator_ok = True
        except Exception:
            orchestrator_ok = False

        if not orchestrator_ok:
            try:
                response = requests.get(llm_models_endpoint, timeout=2)
                service_status['llama']['status'] = 'running' if response.status_code == 200 else 'stopped'
            except Exception:
                service_status['llama']['status'] = 'stopped'

            try:
                va_resp = requests.get(f"http://{va_host}:{va_port}/health", timeout=2)
                service_status['voice_assistant']['status'] = 'running' if va_resp.status_code == 200 else 'stopped'
            except Exception:
                service_status['voice_assistant']['status'] = 'stopped'

            try:
                response = requests.get(f"http://{rag_host}:{rag_port}/health", timeout=2)
                service_status['rag']['status'] = 'running' if response.status_code == 200 else 'stopped'
            except Exception:
                service_status['rag']['status'] = 'stopped'

            # Web GUI is this dashboard
            service_status['web_gui']['status'] = 'running'

    except Exception as e:
        logger.error(f"Error checking service health: {e}")

@app.route('/')
def dashboard():
    """Main dashboard page"""
    # Serve main dashboard page
    check_service_health()
    return render_template('dashboard.html', services=service_status, wd_host=wd_host, wd_port=wd_port)

@app.route('/favicon.ico')
def favicon():
    """Serve favicon - return empty response to avoid 404"""
    return '', 204

@app.route('/api/stats')
def api_stats():
    """API endpoint for system statistics"""
    global system_stats
    system_stats = get_system_stats()
    # lightweight stats API
    return jsonify(system_stats)

@app.route('/api/services')
def api_services():
    """API endpoint for service status"""
    try:
        check_service_health()
    except Exception as e:
        logger.error(f"Error checking service status: {e}")
    return jsonify(service_status)

@app.route('/api/metrics')
def api_metrics():
    """Proxy to orchestrator metrics for UI consumption"""
    try:
        host, port = CFG.get_orchestrator_host_port()
        # Allow a slightly higher timeout due to process introspection
        r = _request_with_retry('GET', f"http://{host}:{port}/metrics", timeout=5, retries=1)
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
        r = _request_with_retry('GET', f"http://{host}:{port}/pipeline-check", timeout=5, retries=1)
        if r.ok:
            return jsonify(r.json())
        return jsonify({'success': False, 'error': f'orc responded {r.status_code}'}), 502
    except Exception as e:
        logger.error(f"Pipeline proxy error: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500


@app.route('/api/service/<name>/restart', methods=['POST'])
def api_service_restart(name: str):
    """Proxy restart requests to orchestrator."""
    try:
        host, port = CFG.get_orchestrator_host_port()
        r = _request_with_retry('POST', f"http://{host}:{port}/service/{name}/restart", timeout=10, retries=1)
        return jsonify(r.json()), r.status_code
    except Exception as e:
        logger.error(f"Service restart proxy error: {e}")
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
    # test endpoint
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
        # Avoid duplicating the user message in UI for HTTP fallback
        response = _handle_chat_message_and_broadcast(message, emit_user=False)
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
        r = _request_with_retry('POST', f"http://{va_host}:{va_port}/mic-check", timeout=3, retries=1)
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
        r = _request_with_retry('POST', f"http://{va_host}:{va_port}/speak", json_body={'text': text}, timeout=5, retries=1)
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
        
        # Check if transcription is actually an error message
        if transcription and transcription.startswith('Audio conversion failed'):
            logger.error(f"FFmpeg error: {transcription}")
            return jsonify({'success': False, 'error': 'Audio processing failed', 'code': 'audio_processing_error'}), 500
        
        if transcription and transcription.startswith('ffmpeg not found'):
            logger.error(f"FFmpeg not found: {transcription}")
            return jsonify({'success': False, 'error': 'Audio processing not available', 'code': 'ffmpeg_not_found'}), 503
        
        # Update conversation state
        conversation_state['last_activity'] = datetime.now()
        conversation_state['current_speaker'] = 'user'
        conversation_state['message_count'] += 1
        
        # Broadcast conversation update only if we have speech-like content
        if transcription and transcription.strip().lower() != 'no speech detected':
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

def _handle_chat_message_and_broadcast(message: str, emit_user: bool = True) -> str:
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

        if emit_user:
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
    _handle_chat_message_and_broadcast(message, emit_user=True)

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
    """Process voice input.
    - Accepts either a raw base64 string or a full DataURL (e.g. data:audio/webm;...;base64,XXXX)
    - Detects container type for better ffmpeg compatibility
    - Converts to 16kHz mono WAV and transcribes with whisper.cpp CLI when available,
      otherwise falls back to python-whisper if installed.
    """
    import base64
    import tempfile
    import os

    try:
        # Determine if this is a DataURL and extract mime + payload
        src_suffix = '.webm'
        payload = base64_audio
        if ',' in base64_audio and base64_audio.strip().startswith('data:'):
            header, payload = base64_audio.split(',', 1)
            # Try to recognize container extension from header
            if 'audio/ogg' in header:
                src_suffix = '.ogg'
            elif 'audio/mp4' in header or 'audio/m4a' in header:
                src_suffix = '.mp4'
            elif 'audio/mpeg' in header or 'audio/mp3' in header:
                src_suffix = '.mp3'
            elif 'audio/wav' in header or 'audio/x-wav' in header:
                src_suffix = '.wav'
            else:
                src_suffix = '.webm'

        # Decode incoming base64 payload
        audio_bytes = base64.b64decode(payload)

        # Write raw container to a temp file
        with tempfile.NamedTemporaryFile(suffix=src_suffix, delete=False) as src_file:
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
            "max_tokens": int(CFG.get_llm_max_tokens())
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
    """Process message for tool usage via shared tools module.

    This avoids duplicating macOS integration logic here and keeps behavior
    consistent with the voice assistant.
    """
    msg = (message or "").strip()
    if not msg:
        return None

    ml = msg.lower()
    try:
        # Weather-specific search
        if "weather" in ml and "search" in ml:
            # Delegate to search; tools.get_weather uses configured default location
            return tools_mod.get_weather()

        # Generic web search
        if "search" in ml and ("for" in ml or "web" in ml):
            q = ml.replace("search", "").replace("for", "").replace("web", "").strip()
            return tools_mod.web_search(q)

        # Website browsing
        if any(k in ml for k in ["browse", "open website", "go to"]):
            for word in msg.split():
                if word.startswith(("http://", "https://", "www.")):
                    return tools_mod.browse_website(word)
            # If no explicit URL, fall back to search
            return tools_mod.web_search(msg.replace("browse", "").replace("open website", "").replace("go to", "").strip())

        # App opening
        if ("open app" in ml) or (ml.startswith("open ") and " app" in ml):
            app_name = ml.replace("open app", "").strip()
            return tools_mod.open_app(app_name)

        # Screenshot
        if ("screenshot" in ml) or ("take picture" in ml):
            return tools_mod.take_screenshot()

        # System info
        if "system info" in ml or "system status" in ml or ("system" in ml and "info" in ml):
            return tools_mod.get_system_info()

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

#!/usr/bin/env python3
"""
MacBot RAG Server - Real document processing and retrieval
"""
import os
import sys
import json
import time
import logging
from .logging_utils import setup_logger
from pathlib import Path
from typing import List, Dict, Optional, Set, Tuple
from datetime import datetime

import chromadb
from sentence_transformers import SentenceTransformer
import requests
from flask import Flask, request, jsonify, render_template
import threading

# Configure logging (unified)
logger = setup_logger("macbot.rag_server", "logs/rag_server.log")

app = Flask(__name__)
from . import config as CFG
from .auth import get_auth_manager, require_api_key

RATE_LIMIT_PER_MINUTE: int = CFG.get_rag_rate_limit_per_minute()
_request_counts: Dict[str, tuple[int, float]] = {}
_rate_lock = threading.Lock()
auth_manager = get_auth_manager()

class RAGServer:
    def __init__(self, data_dir: str = "rag_data"):
        self.data_dir = Path(data_dir)
        self.data_dir.mkdir(exist_ok=True)
        
        # Initialize ChromaDB
        self.client = chromadb.PersistentClient(path=str(self.data_dir / "chroma_db"))
        self.collection = self.client.get_or_create_collection(
            name="macbot_documents",
            metadata={"description": "MacBot knowledge base"}
        )
        
        # Initialize sentence transformer
        logger.info("Loading sentence transformer model...")
        self.embedding_model = SentenceTransformer('all-MiniLM-L6-v2')
        logger.info("✅ Sentence transformer loaded")
        
        # Document store
        self.documents = {}
        self.document_metadata = {}
        
        # Load existing documents
        self.load_existing_documents()
    
    def load_existing_documents(self):
        """Load documents from the data directory"""
        try:
            docs_file = self.data_dir / "documents.json"
            if docs_file.exists():
                with open(docs_file, 'r') as f:
                    data = json.load(f)
                    self.documents = data.get('documents', {})
                    self.document_metadata = data.get('metadata', {})
                logger.info(f"Loaded {len(self.documents)} existing documents")
        except Exception as e:
            logger.warning(f"Failed to load existing documents: {e}")
    
    def save_documents(self):
        """Save documents to disk"""
        try:
            docs_file = self.data_dir / "documents.json"
            with open(docs_file, 'w') as f:
                json.dump({
                    'documents': self.documents,
                    'metadata': self.document_metadata
                }, f, indent=2)
        except Exception as e:
            logger.error(f"Failed to save documents: {e}")
    
    def add_document(self, content: str, title: str, doc_type: str = "text", metadata: Optional[Dict] = None) -> str:
        """Add a document to the knowledge base"""
        try:
            # Generate document ID
            doc_id = f"doc_{int(time.time())}_{len(self.documents)}"
            
            # Store document
            self.documents[doc_id] = content
            self.document_metadata[doc_id] = {
                'title': title,
                'type': doc_type,
                'added': datetime.now().isoformat(),
                'length': len(content),
                **(metadata or {})
            }
            
            # Add to ChromaDB
            self.collection.add(
                documents=[content],
                metadatas=[self.document_metadata[doc_id]],
                ids=[doc_id]
            )
            
            # Save to disk
            self.save_documents()
            
            logger.info(f"Added document: {title} (ID: {doc_id})")
            return doc_id
            
        except Exception as e:
            logger.error(f"Failed to add document: {e}")
            raise
    
    def search(self, query: str, top_k: int = 5) -> List[Dict]:
        """Search documents using semantic similarity"""
        try:
            # Search in ChromaDB
            results = self.collection.query(
                query_texts=[query],
                n_results=top_k
            )
            
            # Format results
            formatted_results = []
            if results and isinstance(results, dict):
                documents = results.get('documents')
                ids = results.get('ids')
                metadatas = results.get('metadatas')
                distances = results.get('distances')
                
                if documents and documents[0]:
                    for i, doc_id in enumerate(ids[0] if ids and ids[0] else []):
                        doc_content = documents[0][i] if i < len(documents[0]) else ""
                        doc_metadata = metadatas[0][i] if metadatas and metadatas[0] and i < len(metadatas[0]) else {}
                        doc_score = distances[0][i] if distances and distances[0] and i < len(distances[0]) else None
                        
                        formatted_results.append({
                            'id': doc_id,
                            'content': doc_content,
                            'metadata': doc_metadata,
                            'score': doc_score
                        })
            
            logger.info(f"Search query '{query}' returned {len(formatted_results)} results")
            return formatted_results
            
        except Exception as e:
            logger.error(f"Search failed: {e}")
            return []
    
    def get_document(self, doc_id: str) -> Optional[Dict]:
        """Get a specific document by ID"""
        if doc_id in self.documents:
            return {
                'id': doc_id,
                'content': self.documents[doc_id],
                'metadata': self.document_metadata[doc_id]
            }
        return None
    
    def list_documents(self) -> List[Dict]:
        """List all documents"""
        return [
            {
                'id': doc_id,
                'title': meta.get('title', 'Untitled'),
                'type': meta.get('type', 'unknown'),
                'added': meta.get('added', ''),
                'length': meta.get('length', 0)
            }
            for doc_id, meta in self.document_metadata.items()
        ]
    
    def delete_document(self, doc_id: str) -> bool:
        """Delete a document"""
        try:
            if doc_id in self.documents:
                del self.documents[doc_id]
                del self.document_metadata[doc_id]
                
                # Remove from ChromaDB
                self.collection.delete(ids=[doc_id])
                
                # Save to disk
                self.save_documents()
                
                logger.info(f"Deleted document: {doc_id}")
                return True
            return False
        except Exception as e:
            logger.error(f"Failed to delete document: {e}")
            return False
    
    def get_stats(self) -> Dict:
        """Get RAG system statistics"""
        return {
            'total_documents': len(self.documents),
            'total_chunks': len(self.documents),
            'embedding_model': 'all-MiniLM-L6-v2',
            'vector_db': 'ChromaDB',
            'last_updated': datetime.now().isoformat()
        }

# Lazy RAG server instance
_rag_server: Optional[RAGServer] = None


def get_rag_server() -> RAGServer:
    """Lazily initialize and return the shared RAG server instance."""
    global _rag_server
    if _rag_server is None:
        _rag_server = RAGServer()
    return _rag_server

# Add some sample documents
def add_sample_documents():
    """Add sample documents for testing"""
    server = get_rag_server()

    if not server.documents:
        logger.info("Adding sample documents...")

        # Sample documents
        samples = [
            {
                'content': """MacBot is a local voice assistant that runs entirely offline on macOS. 
                It uses llama.cpp for language processing, Whisper for speech recognition, and Kokoro for text-to-speech. 
                The system includes tool calling capabilities for native macOS functions like opening apps, taking screenshots, 
                and web browsing through Safari.""",
                'title': 'MacBot Overview',
                'type': 'system_info'
            },
            {
                'content': """Voice Commands Available:
                - "search for [query]" - Web search using Safari
                - "browse [url]" - Open website in Safari
                - "open app [name]" - Launch macOS applications
                - "take screenshot" - Capture screen
                - "weather" - Open Weather app
                - "system info" - Get system statistics""",
                'title': 'Voice Commands Reference',
                'type': 'user_guide'
            },
            {
                'content': """System Requirements:
                - macOS 12.0+ (Monterey)
                - Python 3.11+
                - 8GB RAM minimum, 16GB recommended
                - Apple Silicon (M1/M2/M3) with Metal support
                - 5GB storage for models and dependencies""",
                'title': 'System Requirements',
                'type': 'technical'
            }
        ]
        
        for sample in samples:
            server.add_document(
                content=sample['content'],
                title=sample['title'],
                doc_type=sample['type']
            )
        
        logger.info("✅ Sample documents added")

# Authentication & rate limiting
@app.before_request
def _check_auth_and_rate_limit() -> Optional[Tuple[Dict[str, str], int]]:
    if request.path.startswith('/api/'):
        # Check for API key in multiple possible headers/locations
        auth_header = request.headers.get('Authorization', '')
        token = ''
        if auth_header.startswith('Bearer '):
            token = auth_header.split(' ', 1)[1].strip()
        else:
            token = (request.args.get('token') or
                    request.headers.get('X-API-Token') or
                    request.headers.get('X-API-Key', ''))

        if not token or not auth_manager.verify_api_token(token):
            return {'success': False, 'error': 'Unauthorized', 'code': 'unauthorized'}, 401

        now = time.time()
        with _rate_lock:
            count, start = _request_counts.get(token, (0, now))
            if now - start >= 60:
                count = 0
                start = now
            if count >= RATE_LIMIT_PER_MINUTE:
                return {'success': False, 'error': 'Too many requests', 'code': 'rate_limited'}, 429
            _request_counts[token] = (count + 1, start)

# Flask routes
@app.route('/')
def index():
    """RAG server status page"""
    server = get_rag_server()
    stats = server.get_stats()
    docs = server.list_documents()

    return render_template('rag_dashboard.html', stats=stats, docs=docs)

@app.route('/health')
def health():
    """Health check endpoint"""
    return jsonify({'status': 'healthy', 'timestamp': datetime.now().isoformat()})

@app.route('/api/search', methods=['POST'])
def api_search():
    """Search API endpoint"""
    try:
        data = request.get_json()
        query = data.get('query', '')
        
        if not query:
            return jsonify({'success': False, 'error': 'Query is required', 'code': 'validation_error'}), 400
        
        results = get_rag_server().search(query, top_k=5)
        return jsonify({'query': query, 'results': results})
        
    except Exception as e:
        logger.error(f"Search API error: {e}")
        return jsonify({'success': False, 'error': str(e), 'code': 'internal_error'}), 500

@app.route('/api/documents', methods=['GET'])
def api_documents():
    """List documents API endpoint"""
    try:
        docs = get_rag_server().list_documents()
        return jsonify({'documents': docs})
    except Exception as e:
        logger.error(f"Documents API error: {e}")
        return jsonify({'success': False, 'error': str(e), 'code': 'internal_error'}), 500

@app.route('/api/documents', methods=['POST'])
def api_add_document():
    """Add document API endpoint"""
    try:
        data = request.get_json()
        content = data.get('content', '')
        title = data.get('title', 'Untitled')
        doc_type = data.get('type', 'text')
        metadata = data.get('metadata', {})
        
        if not content:
            return jsonify({'success': False, 'error': 'Content is required', 'code': 'validation_error'}), 400
        
        doc_id = get_rag_server().add_document(content, title, doc_type, metadata)
        return jsonify({'id': doc_id, 'message': 'Document added successfully'})
        
    except Exception as e:
        logger.error(f"Add document API error: {e}")
        return jsonify({'success': False, 'error': str(e), 'code': 'internal_error'}), 500

@app.route('/api/documents/<doc_id>', methods=['GET'])
def api_get_document(doc_id):
    """Get document API endpoint"""
    try:
        doc = get_rag_server().get_document(doc_id)
        if doc:
            return jsonify(doc)
        else:
            return jsonify({'success': False, 'error': 'Document not found', 'code': 'not_found'}), 404
    except Exception as e:
        logger.error(f"Get document API error: {e}")
        return jsonify({'success': False, 'error': str(e), 'code': 'internal_error'}), 500

@app.route('/api/documents/<doc_id>', methods=['DELETE'])
def api_delete_document(doc_id):
    """Delete document API endpoint"""
    try:
        success = get_rag_server().delete_document(doc_id)
        if success:
            return jsonify({'message': 'Document deleted successfully'})
        else:
            return jsonify({'success': False, 'error': 'Document not found', 'code': 'not_found'}), 404
    except Exception as e:
        logger.error(f"Delete document API error: {e}")
        return jsonify({'success': False, 'error': str(e), 'code': 'internal_error'}), 500

@app.route('/api/stats')
def api_stats():
    """Get RAG system statistics"""
    try:
        stats = get_rag_server().get_stats()
        return jsonify(stats)
    except Exception as e:
        logger.error(f"Stats API error: {e}")
        return jsonify({'success': False, 'error': str(e), 'code': 'internal_error'}), 500

def start_rag_server(host='0.0.0.0', port=8001):
    """Start the RAG server"""
    logger.info(f"Starting MacBot RAG Server on http://{host}:{port}")
    
    # Add sample documents if none exist
    add_sample_documents()
    
    try:
        app.run(host=host, port=port, debug=False, use_reloader=False)
    except Exception as e:
        logger.error(f"Failed to start RAG server: {e}")
        raise

def main():
    host, port = CFG.get_rag_host_port()
    try:
        start_rag_server(host=host, port=port)
    except Exception:
        # Fallback to defaults if config missing
        start_rag_server()

if __name__ == '__main__':
    main()

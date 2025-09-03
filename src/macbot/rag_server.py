#!/usr/bin/env python3
"""
MacBot RAG Server - Real document processing and retrieval
"""
import os
import sys
import json
import time
import logging
from pathlib import Path
from typing import List, Dict, Optional
from datetime import datetime

import chromadb
from sentence_transformers import SentenceTransformer
import requests
from flask import Flask, request, jsonify, render_template_string
import threading

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = Flask(__name__)
from . import config as CFG

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

# Initialize RAG server
rag_server = RAGServer()

# Add some sample documents
def add_sample_documents():
    """Add sample documents for testing"""
    if not rag_server.documents:
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
            rag_server.add_document(
                content=sample['content'],
                title=sample['title'],
                doc_type=sample['type']
            )
        
        logger.info("✅ Sample documents added")

# Flask routes
@app.route('/')
def index():
    """RAG server status page"""
    stats = rag_server.get_stats()
    docs = rag_server.list_documents()
    
    html = f"""
    <!DOCTYPE html>
    <html>
    <head>
        <title>MacBot RAG Server</title>
        <style>
            body {{ font-family: -apple-system, sans-serif; margin: 40px; }}
            .container {{ max-width: 800px; margin: 0 auto; }}
            .card {{ background: white; padding: 20px; margin: 20px 0; border-radius: 8px; box-shadow: 0 2px 10px rgba(0,0,0,0.1); }}
            .stats {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(200px, 1fr)); gap: 20px; }}
            .stat {{ text-align: center; }}
            .stat-value {{ font-size: 2em; font-weight: bold; color: #007aff; }}
            .doc-item {{ border: 1px solid #e0e0e0; padding: 15px; margin: 10px 0; border-radius: 6px; }}
            .search-box {{ width: 100%; padding: 10px; margin: 20px 0; border: 1px solid #ccc; border-radius: 4px; }}
            .search-results {{ margin-top: 20px; }}
        </style>
    </head>
    <body>
        <div class="container">
            <h1>🤖 MacBot RAG Server</h1>
            
            <div class="card">
                <h2>System Statistics</h2>
                <div class="stats">
                    <div class="stat">
                        <div class="stat-value">{stats['total_documents']}</div>
                        <div>Documents</div>
                    </div>
                    <div class="stat">
                        <div class="stat-value">{stats['total_chunks']}</div>
                        <div>Chunks</div>
                    </div>
                    <div class="stat">
                        <div class="stat-value">{stats['embedding_model']}</div>
                        <div>Model</div>
                    </div>
                </div>
            </div>
            
            <div class="card">
                <h2>Search Documents</h2>
                <input type="text" class="search-box" id="searchInput" placeholder="Enter your search query...">
                <button onclick="search()">Search</button>
                <div id="searchResults" class="search-results"></div>
            </div>
            
            <div class="card">
                <h2>Available Documents ({len(docs)})</h2>
                {''.join([f'<div class="doc-item"><strong>{doc["title"]}</strong> ({doc["type"]}) - {doc["length"]} chars</div>' for doc in docs])}
            </div>
        </div>
        
        <script>
        function search() {{
            const query = document.getElementById('searchInput').value;
            if (!query) return;
            
            fetch('/api/search', {{
                method: 'POST',
                headers: {{'Content-Type': 'application/json'}},
                body: JSON.stringify({{query: query}})
            }})
            .then(response => response.json())
            .then(data => {{
                const resultsDiv = document.getElementById('searchResults');
                if (data.results && data.results.length > 0) {{
                    resultsDiv.innerHTML = '<h3>Search Results:</h3>' + 
                        data.results.map(r => 
                            `<div class="doc-item"><strong>${{r.metadata.title}}</strong><br>${{r.content.substring(0, 200)}}...</div>`
                        ).join('');
                }} else {{
                    resultsDiv.innerHTML = '<p>No results found.</p>';
                }}
            }});
        }}
        </script>
    </body>
    </html>
    """
    return html

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
            return jsonify({'error': 'Query is required'}), 400
        
        results = rag_server.search(query, top_k=5)
        return jsonify({'query': query, 'results': results})
        
    except Exception as e:
        logger.error(f"Search API error: {e}")
        return jsonify({'error': str(e)}), 500

@app.route('/api/documents', methods=['GET'])
def api_documents():
    """List documents API endpoint"""
    try:
        docs = rag_server.list_documents()
        return jsonify({'documents': docs})
    except Exception as e:
        logger.error(f"Documents API error: {e}")
        return jsonify({'error': str(e)}), 500

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
            return jsonify({'error': 'Content is required'}), 400
        
        doc_id = rag_server.add_document(content, title, doc_type, metadata)
        return jsonify({'id': doc_id, 'message': 'Document added successfully'})
        
    except Exception as e:
        logger.error(f"Add document API error: {e}")
        return jsonify({'error': str(e)}), 500

@app.route('/api/documents/<doc_id>', methods=['GET'])
def api_get_document(doc_id):
    """Get document API endpoint"""
    try:
        doc = rag_server.get_document(doc_id)
        if doc:
            return jsonify(doc)
        else:
            return jsonify({'error': 'Document not found'}), 404
    except Exception as e:
        logger.error(f"Get document API error: {e}")
        return jsonify({'error': str(e)}), 500

@app.route('/api/documents/<doc_id>', methods=['DELETE'])
def api_delete_document(doc_id):
    """Delete document API endpoint"""
    try:
        success = rag_server.delete_document(doc_id)
        if success:
            return jsonify({'message': 'Document deleted successfully'})
        else:
            return jsonify({'error': 'Document not found'}), 404
    except Exception as e:
        logger.error(f"Delete document API error: {e}")
        return jsonify({'error': str(e)}), 500

@app.route('/api/stats')
def api_stats():
    """Get RAG system statistics"""
    try:
        stats = rag_server.get_stats()
        return jsonify(stats)
    except Exception as e:
        logger.error(f"Stats API error: {e}")
        return jsonify({'error': str(e)}), 500

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

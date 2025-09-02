#!/usr/bin/env python3
"""
MacBot Conversation Manager
Handles conversation state management and context preservation across interruptions
"""
import time
import threading
import json
from typing import Dict, List, Optional, Any, Callable
from enum import Enum
from dataclasses import dataclass, asdict
import logging

logger = logging.getLogger(__name__)

class ConversationState(Enum):
    """Conversation states"""
    IDLE = "idle"
    LISTENING = "listening"
    PROCESSING = "processing"
    SPEAKING = "speaking"
    INTERRUPTED = "interrupted"
    ERROR = "error"

class ResponseState(Enum):
    """Response states for interruption handling"""
    NOT_STARTED = "not_started"
    STREAMING = "streaming"
    COMPLETED = "completed"
    INTERRUPTED = "interrupted"
    BUFFERED = "buffered"

@dataclass
class ConversationContext:
    """Conversation context data"""
    conversation_id: str
    start_time: float
    last_activity: float
    turn_count: int
    current_state: ConversationState
    user_input: str = ""
    ai_response: str = ""
    response_state: ResponseState = ResponseState.NOT_STARTED
    buffered_response: str = ""
    interrupted_at: Optional[float] = None
    metadata: Optional[Dict[str, Any]] = None

    def __post_init__(self):
        if self.metadata is None:
            self.metadata = {}

@dataclass
class Message:
    """Message in conversation history"""
    timestamp: float
    sender: str  # "user" or "assistant"
    content: str
    message_type: str = "text"  # "text", "audio", "system"
    metadata: Optional[Dict[str, Any]] = None

class ConversationManager:
    """Manages conversation state and history"""

    def __init__(self, max_history: int = 100, context_timeout: float = 300.0):
        """
        Initialize conversation manager

        Args:
            max_history: Maximum number of messages to keep in history
            context_timeout: Seconds before conversation context expires
        """
        self.max_history = max_history
        self.context_timeout = context_timeout

        # Current conversation context
        self.current_context: Optional[ConversationContext] = None

        # Conversation history
        self.conversation_history: List[Message] = []

        # Thread safety
        self.lock = threading.Lock()

        # Callbacks
        self.state_change_callbacks: List[Callable] = []

        logger.info("Conversation manager initialized")

    def start_conversation(self, conversation_id: Optional[str] = None) -> str:
        """Start a new conversation or resume existing one"""
        with self.lock:
            if conversation_id is None:
                conversation_id = f"conv_{int(time.time())}"

            current_time = time.time()

            # Check if we have an existing context that's still valid
            if (self.current_context and
                self.current_context.conversation_id == conversation_id and
                (current_time - self.current_context.last_activity) < self.context_timeout):

                # Resume existing conversation
                self.current_context.last_activity = current_time
                self.current_context.current_state = ConversationState.IDLE
                logger.info(f"Resumed conversation: {conversation_id}")
            else:
                # Start new conversation
                self.current_context = ConversationContext(
                    conversation_id=conversation_id,
                    start_time=current_time,
                    last_activity=current_time,
                    turn_count=0,
                    current_state=ConversationState.IDLE
                )
                logger.info(f"Started new conversation: {conversation_id}")

            self._notify_state_change()
            return conversation_id

    def update_state(self, new_state: ConversationState, metadata: Optional[Dict[str, Any]] = None):
        """Update conversation state"""
        with self.lock:
            if self.current_context:
                old_state = self.current_context.current_state
                self.current_context.current_state = new_state
                self.current_context.last_activity = time.time()

                if metadata:
                    if self.current_context.metadata is None:
                        self.current_context.metadata = {}
                    self.current_context.metadata.update(metadata)

                logger.info(f"State changed: {old_state.value} -> {new_state.value}")
                self._notify_state_change()

    def add_user_input(self, text: str, metadata: Optional[Dict[str, Any]] = None):
        """Add user input to conversation"""
        with self.lock:
            if not self.current_context:
                self.start_conversation()

            if self.current_context:
                self.current_context.user_input = text
                self.current_context.turn_count += 1
                self.current_context.last_activity = time.time()

                # Add to history
                message = Message(
                    timestamp=time.time(),
                    sender="user",
                    content=text,
                    message_type="text",
                    metadata=metadata or {}
                )
                self._add_to_history(message)

                self.update_state(ConversationState.PROCESSING)
                logger.info(f"User input added: {text[:50]}...")

    def start_response(self, response_text: str = ""):
        """Start AI response"""
        with self.lock:
            if self.current_context:
                self.current_context.ai_response = response_text
                self.current_context.response_state = ResponseState.STREAMING
                self.current_context.last_activity = time.time()

                self.update_state(ConversationState.SPEAKING)

    def update_response(self, new_text: str, is_complete: bool = False):
        """Update AI response (for streaming)"""
        with self.lock:
            if self.current_context:
                self.current_context.ai_response = new_text
                self.current_context.last_activity = time.time()

                if is_complete:
                    self.current_context.response_state = ResponseState.COMPLETED
                    self._add_response_to_history(new_text)

    def interrupt_response(self):
        """Handle response interruption"""
        with self.lock:
            if self.current_context:
                # Buffer the current response for potential resumption
                if self.current_context.ai_response:
                    self.current_context.buffered_response = self.current_context.ai_response
                    self.current_context.response_state = ResponseState.INTERRUPTED
                    self.current_context.interrupted_at = time.time()

                self.update_state(ConversationState.INTERRUPTED)
                logger.info("Response interrupted and buffered")

    def resume_response(self) -> Optional[str]:
        """Resume interrupted response"""
        with self.lock:
            if (self.current_context and
                self.current_context.response_state == ResponseState.INTERRUPTED and
                self.current_context.buffered_response):

                buffered_text = self.current_context.buffered_response
                self.current_context.buffered_response = ""
                self.current_context.response_state = ResponseState.STREAMING
                self.current_context.interrupted_at = None

                self.update_state(ConversationState.SPEAKING)
                logger.info("Resumed buffered response")
                return buffered_text

            return None

    def complete_response(self):
        """Mark response as completed"""
        with self.lock:
            if self.current_context:
                self.current_context.response_state = ResponseState.COMPLETED
                self.current_context.last_activity = time.time()

                # Add to history
                if self.current_context.ai_response:
                    self._add_response_to_history(self.current_context.ai_response)

                self.update_state(ConversationState.IDLE)

    def _add_response_to_history(self, response_text: str):
        """Add AI response to conversation history"""
        message = Message(
            timestamp=time.time(),
            sender="assistant",
            content=response_text,
            message_type="text"
        )
        self._add_to_history(message)

    def _add_to_history(self, message: Message):
        """Add message to history with size management"""
        self.conversation_history.append(message)

        # Maintain history size limit
        if len(self.conversation_history) > self.max_history:
            removed_count = len(self.conversation_history) - self.max_history
            self.conversation_history = self.conversation_history[removed_count:]
            logger.debug(f"Trimmed history: removed {removed_count} old messages")

    def get_conversation_summary(self) -> Dict[str, Any]:
        """Get summary of current conversation"""
        with self.lock:
            if not self.current_context:
                return {"status": "no_active_conversation"}

            return {
                "conversation_id": self.current_context.conversation_id,
                "start_time": self.current_context.start_time,
                "duration": time.time() - self.current_context.start_time,
                "turn_count": self.current_context.turn_count,
                "current_state": self.current_context.current_state.value,
                "response_state": self.current_context.response_state.value,
                "last_activity": self.current_context.last_activity,
                "has_buffered_response": bool(self.current_context.buffered_response),
                "history_length": len(self.conversation_history)
            }

    def get_recent_history(self, limit: int = 10) -> List[Dict[str, Any]]:
        """Get recent conversation history"""
        with self.lock:
            recent_messages = self.conversation_history[-limit:]
            return [asdict(msg) for msg in recent_messages]

    def clear_conversation(self):
        """Clear current conversation context"""
        with self.lock:
            if self.current_context:
                logger.info(f"Clearing conversation: {self.current_context.conversation_id}")
                self.current_context = None
                self.update_state(ConversationState.IDLE)

    def register_state_callback(self, callback: Callable):
        """Register callback for state changes"""
        self.state_change_callbacks.append(callback)

    def _notify_state_change(self):
        """Notify registered callbacks of state change"""
        if self.current_context:
            for callback in self.state_change_callbacks:
                try:
                    callback(self.current_context)
                except Exception as e:
                    logger.error(f"State change callback error: {e}")

    def export_conversation(self) -> Dict[str, Any]:
        """Export complete conversation data"""
        with self.lock:
            return {
                "conversation_context": asdict(self.current_context) if self.current_context else None,
                "conversation_history": [asdict(msg) for msg in self.conversation_history],
                "export_timestamp": time.time()
            }

    def import_conversation(self, data: Dict[str, Any]):
        """Import conversation data"""
        with self.lock:
            if "conversation_context" in data and data["conversation_context"]:
                context_data = data["conversation_context"]
                self.current_context = ConversationContext(**context_data)

            if "conversation_history" in data:
                self.conversation_history = [
                    Message(**msg_data) for msg_data in data["conversation_history"]
                ]

            logger.info("Conversation data imported")


# Global conversation manager instance
conversation_manager = ConversationManager()

def get_conversation_manager() -> ConversationManager:
    """Get the global conversation manager instance"""
    return conversation_manager

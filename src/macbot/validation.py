"""
MacBot Input Validation and Sanitization Module
"""
import re
import json
from typing import Any, Dict, List, Optional, Union
from .logging_utils import setup_logger

logger = setup_logger("macbot.validation", "logs/macbot.log")

class ValidationError(Exception):
    """Raised when input validation fails"""
    pass

class InputValidator:
    """Centralized input validation and sanitization"""

    def __init__(self):
        # Maximum lengths for different input types
        self.MAX_TEXT_LENGTH = 10000
        self.MAX_AUDIO_SIZE = 25 * 1024 * 1024  # 25MB
        self.MAX_FILENAME_LENGTH = 255
        self.MAX_PATH_LENGTH = 4096

        # Dangerous patterns
        self.DANGEROUS_PATTERNS = [
            r'<script[^>]*>.*?</script>',
            r'javascript:',
            r'vbscript:',
            r'on\w+\s*=',
            r'style\s*=.*expression',
            r'style\s*=.*javascript',
            r'<iframe[^>]*>.*?</iframe>',
            r'<object[^>]*>.*?</object>',
            r'<embed[^>]*>.*?</embed>',
            r'<form[^>]*>.*?</form>',
            r'<input[^>]*>',
            r'<meta[^>]*>',
            r'<link[^>]*>',
        ]

        self.COMPILED_PATTERNS = [re.compile(pattern, re.IGNORECASE | re.DOTALL) for pattern in self.DANGEROUS_PATTERNS]

    def validate_text_input(self, text: str, max_length: Optional[int] = None) -> str:
        """Validate and sanitize text input"""
        if not isinstance(text, str):
            raise ValidationError("Input must be a string")

        if not text.strip():
            raise ValidationError("Input cannot be empty")

        if max_length is None:
            max_length = self.MAX_TEXT_LENGTH

        if len(text) > max_length:
            raise ValidationError(f"Input exceeds maximum length of {max_length} characters")

        # Basic sanitization
        sanitized = self._sanitize_html(text)

        # Remove control characters except newlines and tabs
        sanitized = re.sub(r'[\x00-\x08\x0B\x0C\x0E-\x1F\x7F]', '', sanitized)

        return sanitized.strip()

    def validate_json_input(self, data: Union[str, Dict, List]) -> Dict:
        """Validate JSON input"""
        try:
            if isinstance(data, str):
                parsed = json.loads(data)
            elif isinstance(data, (dict, list)):
                parsed = data
            else:
                raise ValidationError("Invalid JSON input type")

            # Validate structure
            if not isinstance(parsed, dict):
                raise ValidationError("JSON input must be an object")

            return parsed
        except json.JSONDecodeError as e:
            raise ValidationError(f"Invalid JSON format: {e}")

    def validate_filename(self, filename: str) -> str:
        """Validate filename for safety"""
        if not isinstance(filename, str):
            raise ValidationError("Filename must be a string")

        if len(filename) > self.MAX_FILENAME_LENGTH:
            raise ValidationError(f"Filename too long: {len(filename)} > {self.MAX_FILENAME_LENGTH}")

        # Remove path separators and dangerous characters
        sanitized = re.sub(r'[<>:"/\\|?*]', '', filename)

        # Ensure it's not empty after sanitization
        if not sanitized.strip():
            raise ValidationError("Invalid filename after sanitization")

        return sanitized.strip()

    def validate_service_name(self, name: str) -> str:
        """Validate service name"""
        if not isinstance(name, str):
            raise ValidationError("Service name must be a string")

        if not name.strip():
            raise ValidationError("Service name cannot be empty")

        # Only allow alphanumeric, hyphens, and underscores
        if not re.match(r'^[a-zA-Z0-9_-]+$', name):
            raise ValidationError("Service name contains invalid characters")

        return name.strip()

    def validate_audio_data(self, audio_data: str, max_size: Optional[int] = None) -> str:
        """Validate audio data (base64 encoded)"""
        if not isinstance(audio_data, str):
            raise ValidationError("Audio data must be a string")

        if not audio_data.strip():
            raise ValidationError("Audio data cannot be empty")

        if max_size is None:
            max_size = self.MAX_AUDIO_SIZE

        # Approximate size (base64 is ~4/3 of original size)
        approx_size = len(audio_data) * 3 // 4
        if approx_size > max_size:
            raise ValidationError(f"Audio data too large: {approx_size} bytes > {max_size} bytes")

        # Basic base64 validation
        if not re.match(r'^[A-Za-z0-9+/]*={0,2}$', audio_data):
            raise ValidationError("Invalid base64 encoding")

        return audio_data.strip()

    def _sanitize_html(self, text: str) -> str:
        """Remove potentially dangerous HTML/JS"""
        for pattern in self.COMPILED_PATTERNS:
            text = pattern.sub('', text)
        return text

    def validate_request_data(self, data: Dict[str, Any], required_fields: Optional[List[str]] = None) -> Dict[str, Any]:
        """Validate request data structure"""
        if not isinstance(data, dict):
            raise ValidationError("Request data must be a dictionary")

        if required_fields:
            missing = [field for field in required_fields if field not in data]
            if missing:
                raise ValidationError(f"Missing required fields: {missing}")

        # Validate string fields
        for key, value in data.items():
            if isinstance(value, str):
                data[key] = self.validate_text_input(value)

        return data

_validator_instance: Optional[InputValidator] = None

def get_validator() -> InputValidator:
    """Get or create input validator instance"""
    global _validator_instance
    if _validator_instance is None:
        _validator_instance = InputValidator()
    return _validator_instance

def validate_chat_message(message: str) -> str:
    """Validate chat message input"""
    validator = get_validator()
    return validator.validate_text_input(message)

def validate_service_restart(service_name: str) -> str:
    """Validate service restart request"""
    validator = get_validator()
    return validator.validate_service_name(service_name)

def validate_tts_request(data: Dict[str, Any]) -> Dict[str, Any]:
    """Validate TTS request"""
    validator = get_validator()
    return validator.validate_request_data(data, ['text'])

def validate_voice_request(data: Dict[str, Any]) -> Dict[str, Any]:
    """Validate voice processing request"""
    validator = get_validator()
    required = ['audio']
    validated = validator.validate_request_data(data, required)

    # Validate audio data specifically
    if 'audio' in validated:
        validated['audio'] = validator.validate_audio_data(validated['audio'])

    return validated


# Validation decorators for common patterns

def validate_text_input_decorator(max_length: Optional[int] = None):
    """Decorator to validate text input parameters"""
    def decorator(func):
        def wrapper(*args, **kwargs):
            # Find text parameter (usually first positional or named 'text')
            text = None
            if args:
                text = args[0] if isinstance(args[0], str) else None
            if text is None and 'text' in kwargs:
                text = kwargs['text']
            if text is None and 'message' in kwargs:
                text = kwargs['message']

            if text is not None:
                validator = get_validator()
                validated_text = validator.validate_text_input(text, max_length)
                # Update kwargs or args
                if 'text' in kwargs:
                    kwargs['text'] = validated_text
                elif 'message' in kwargs:
                    kwargs['message'] = validated_text
                elif args:
                    new_args = list(args)
                    new_args[0] = validated_text
                    args = tuple(new_args)

            return func(*args, **kwargs)
        return wrapper
    return decorator


def validate_request_data_decorator(required_fields: Optional[List[str]] = None):
    """Decorator to validate request data"""
    def decorator(func):
        def wrapper(*args, **kwargs):
            # Find data parameter
            data = None
            if 'data' in kwargs:
                data = kwargs['data']
            elif len(args) > 0 and isinstance(args[0], dict):
                data = args[0]

            if data is not None:
                validator = get_validator()
                validated_data = validator.validate_request_data(data, required_fields)
                if 'data' in kwargs:
                    kwargs['data'] = validated_data
                elif args:
                    new_args = list(args)
                    new_args[0] = validated_data
                    args = tuple(new_args)

            return func(*args, **kwargs)
        return wrapper
    return decorator


def validate_audio_data_decorator(max_size: Optional[int] = None):
    """Decorator to validate audio data parameters"""
    def decorator(func):
        def wrapper(*args, **kwargs):
            # Find audio parameter
            audio_data = None
            if 'audio' in kwargs:
                audio_data = kwargs['audio']
            elif 'audio_data' in kwargs:
                audio_data = kwargs['audio_data']

            if audio_data is not None:
                validator = get_validator()
                validated_audio = validator.validate_audio_data(audio_data, max_size)
                if 'audio' in kwargs:
                    kwargs['audio'] = validated_audio
                elif 'audio_data' in kwargs:
                    kwargs['audio_data'] = validated_audio

            return func(*args, **kwargs)
        return wrapper
    return decorator

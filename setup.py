#!/usr/bin/env python3
"""
Setup script for MacBot - Local AI Voice Assistant for macOS
"""
from setuptools import setup, find_packages
import os
import re

# Read the contents of README file
this_directory = os.path.abspath(os.path.dirname(__file__))
with open(os.path.join(this_directory, 'README.md'), encoding='utf-8') as f:
    long_description = f.read()

# Read version from pyproject.toml or set default
def get_version():
    try:
        with open('pyproject.toml', 'r') as f:
            content = f.read()
            version_match = re.search(r'version = "([^"]+)"', content)
            if version_match:
                return version_match.group(1)
    except FileNotFoundError:
        pass
    return "1.0.0"

setup(
    name="macbot",
    version=get_version(),
    author="MacBot Team",
    author_email="info@macbot.local",
    description="Local AI Voice Assistant for macOS with offline LLM support",
    long_description=long_description,
    long_description_content_type="text/markdown",
    url="https://github.com/lukifer23/MacBot",
    packages=find_packages(where="src", exclude=["tests", "tests.*"]),
    package_dir={"": "src"},
    include_package_data=True,
    classifiers=[
        "Development Status :: 4 - Beta",
        "Intended Audience :: End Users/Desktop",
        "License :: OSI Approved :: MIT License",
        "Operating System :: MacOS :: MacOS X",
        "Programming Language :: Python :: 3",
        "Programming Language :: Python :: 3.9",
        "Programming Language :: Python :: 3.10",
        "Programming Language :: Python :: 3.11",
        "Programming Language :: Python :: 3.12",
        "Topic :: Multimedia :: Sound/Audio :: Speech",
        "Topic :: Scientific/Engineering :: Artificial Intelligence",
    ],
    keywords="ai voice-assistant llm macos offline whisper tts",
    python_requires=">=3.9",
    install_requires=[
        "numpy>=1.21.0",
        "sounddevice>=0.4.0",
        "soundfile>=0.10.0",
        "PyYAML>=6.0",
        "requests>=2.25.0",
        "psutil>=5.8.0",
        "flask>=2.0.0",
        "chromadb>=0.4.0",
        "sentence-transformers>=2.2.0",
        "kokoro>=0.9.4",
        "livekit-agents[turn-detector]>=1.2.0",
        "websockets>=10.0",
        "python-socketio>=5.0.0",
    ],
    extras_require={
        "dev": [
            "pytest>=7.0.0",
            "black>=22.0.0",
            "flake8>=4.0.0",
            "mypy>=0.950",
            "pre-commit>=2.17.0",
        ],
        "docs": [
            "sphinx>=4.0.0",
            "sphinx-rtd-theme>=1.0.0",
        ],
    },
    entry_points={
        "console_scripts": [
            "macbot=macbot.cli:main",
            "macbot-orchestrator=macbot.orchestrator:main",
            "macbot-dashboard=macbot.web_dashboard:main",
            "macbot-rag=macbot.rag_server:main",
        ],
    },
    project_urls={
        "Homepage": "https://github.com/lukifer23/MacBot",
        "Repository": "https://github.com/lukifer23/MacBot",
        "Issues": "https://github.com/lukifer23/MacBot/issues",
        "Documentation": "https://github.com/lukifer23/MacBot/blob/main/README.md",
    },
)

# Simple orchestrator Makefile for macOS (Apple Silicon)
SHELL := /bin/bash

# ---- Paths ----
PY := python3.11
VENV := .venv
ACT := source $(VENV)/bin/activate

WHISPER_DIR := models/whisper.cpp
LLAMA_DIR   := models/llama.cpp

# ---- Llama server defaults ----
LLAMA_THREADS := $(shell sysctl -n hw.physicalcpu)
LLAMA_PORT := 8080
LLAMA_CTX := 4096
LLAMA_PARALLEL := 2
LLAMA_NGL := 999   # offload max layers to Metal
LLAMA_MODEL := $(LLAMA_DIR)/models/Qwen_Qwen3-4B-Instruct-2507-Q4_K_M.gguf

# ---- Targets ----

.PHONY: all venv deps build-whisper build-llama run-llama run-assistant clean

all: venv build-whisper build-llama

venv:
	$(PY) -m venv $(VENV) && \
	$(ACT); pip install --upgrade pip

deps:
	$(ACT); pip install -r requirements.txt

build-whisper:
	@if [ ! -d $(WHISPER_DIR) ]; then git clone https://github.com/ggml-org/whisper.cpp $(WHISPER_DIR); fi
	cmake -S $(WHISPER_DIR) -B $(WHISPER_DIR)/build -DWHISPER_COREML=0
	cmake --build $(WHISPER_DIR)/build -j
	@cd $(WHISPER_DIR) && bash ./models/download-ggml-model.sh base.en

build-llama:
	@if [ ! -d $(LLAMA_DIR) ]; then git clone https://github.com/ggml-org/llama.cpp $(LLAMA_DIR); fi
	cmake -S $(LLAMA_DIR) -B $(LLAMA_DIR)/build -DLLAMA_METAL=ON
	cmake --build $(LLAMA_DIR)/build -j
	@mkdir -p $(LLAMA_DIR)/models
	@echo "Place a GGUF model in $(LLAMA_DIR)/models and set LLAMA_MODEL in Makefile if needed."

run-llama:
	@if [ ! -f $(LLAMA_MODEL) ]; then echo ">> Missing GGUF model: $(LLAMA_MODEL)"; exit 1; fi
	$(LLAMA_DIR)/build/bin/llama-server \
	  -m $(LLAMA_MODEL) \
	  -c $(LLAMA_CTX) \
	  -t $(LLAMA_THREADS) \
	  -ngl $(LLAMA_NGL) \
	  -np $(LLAMA_PARALLEL) \
	  --port $(LLAMA_PORT)

run-assistant:
	$(ACT); OMP_NUM_THREADS=$(LLAMA_THREADS) $(PY) src/macbot/voice_assistant.py

run-enhanced:
	$(ACT); OMP_NUM_THREADS=$(LLAMA_THREADS) $(PY) src/macbot/voice_assistant.py

run-orchestrator:
	$(ACT); $(PY) src/macbot/orchestrator.py

clean:
	rm -rf $(VENV) $(WHISPER_DIR) $(LLAMA_DIR) __pycache__ */__pycache__

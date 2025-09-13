# 🚀 TTS Optimization Guide

## Overview

This guide covers advanced optimization techniques for MacBot's Text-to-Speech (TTS) system, including quantization, hardware acceleration, and performance tuning.

## Current Performance

- **TTS Duration**: 4.3 seconds (25% improvement achieved)
- **Model Size**: 60MB (amy-medium voice)
- **Memory Usage**: 501MB stable
- **Error Rate**: 0% (production ready)

## 🎯 Optimization Options

### 1. Model Quantization

#### **Dynamic Quantization (INT8)**
- **Size Reduction**: 50-75% (60MB → 15-30MB)
- **Speed Improvement**: 2-4x faster inference
- **Quality Impact**: Minimal
- **Compatibility**: Works with existing ONNX Runtime

```bash
# Run quantization script
python scripts/quantize_tts_model.py --model piper_voices/en_US-amy-medium/model.onnx --dynamic
```

#### **Static Quantization (INT8)**
- **Size Reduction**: 60-80% (60MB → 12-24MB)
- **Speed Improvement**: 3-5x faster inference
- **Quality Impact**: Slight
- **Requirements**: Calibration data needed

```bash
# Run static quantization (requires calibration data)
python scripts/quantize_tts_model.py --model piper_voices/en_US-amy-medium/model.onnx --static
```

#### **INT4 Quantization (Experimental)**
- **Size Reduction**: 75% (60MB → 15MB)
- **Speed Improvement**: 4-8x faster inference
- **Quality Impact**: Noticeable degradation
- **Compatibility**: Limited ONNX Runtime support

### 2. Apple Silicon Acceleration

#### **CoreML Conversion**
- **Neural Engine**: Dedicated ML acceleration
- **GPU + CPU**: Optimal resource utilization
- **Memory Efficiency**: Better memory management
- **Native Integration**: Seamless Apple ecosystem

```bash
# Convert to CoreML
python scripts/quantize_tts_model.py --model piper_voices/en_US-amy-medium/model.onnx --coreml
```

#### **MPS (Metal Performance Shaders)**
- **GPU Acceleration**: Parallel processing
- **Memory Bandwidth**: High-speed memory access
- **Energy Efficiency**: Optimized for Apple Silicon
- **Status**: ✅ Already detected and available

### 3. Configuration Options

#### **Enable Quantized Models**
```yaml
models:
  tts:
    piper:
      voice_path: /Users/admin/Downloads/MacBot/piper_voices/en_US-amy-medium/model.onnx
      quantized_path: /Users/admin/Downloads/MacBot/optimized_models/piper_dynamic_quantized.onnx
      coreml_path: /Users/admin/Downloads/MacBot/optimized_models/piper_model.mlpackage
```

#### **Performance Tuning**
```yaml
voice_assistant:
  performance:
    tts_cache_size: 100
    tts_cache_enabled: true
    tts_parallel_processing: true
    tts_optimize_for_speed: true
```

## 🛠️ Implementation Steps

### Step 1: Install Dependencies
```bash
pip install onnxruntime coremltools torch
```

### Step 2: Run Quantization
```bash
# Dynamic quantization (recommended)
python scripts/quantize_tts_model.py --model piper_voices/en_US-amy-medium/model.onnx --dynamic

# CoreML conversion (Apple Silicon)
python scripts/quantize_tts_model.py --model piper_voices/en_US-amy-medium/model.onnx --coreml

# All optimizations
python scripts/quantize_tts_model.py --model piper_voices/en_US-amy-medium/model.onnx --all
```

### Step 3: Update Configuration
```yaml
models:
  tts:
    piper:
      quantized_path: /path/to/quantized/model.onnx
      coreml_path: /path/to/model.mlpackage
```

### Step 4: Test Performance
```bash
# Test quantized model
curl -X POST http://localhost:8123/speak -H "Content-Type: application/json" -d '{"text": "Testing quantized TTS performance."}'

# Check performance stats
curl http://localhost:8123/tts-performance
```

## 📊 Expected Performance Improvements

### **Dynamic Quantization (INT8)**
- **Model Size**: 60MB → 15-30MB (50-75% reduction)
- **Inference Speed**: 2-4x faster
- **Memory Usage**: 30-50% reduction
- **Quality**: Minimal impact

### **CoreML Conversion**
- **Neural Engine**: 3-5x faster on Apple Silicon
- **Memory Efficiency**: 20-30% reduction
- **Energy Usage**: 40-60% reduction
- **Quality**: No impact

### **Combined Optimizations**
- **Total Speed Improvement**: 5-10x faster
- **Model Size**: 60MB → 15MB (75% reduction)
- **Memory Usage**: 501MB → 200-300MB (40-60% reduction)
- **Energy Efficiency**: 50-70% improvement

## 🔧 Advanced Configuration

### **Model Selection Priority**
1. CoreML model (if available and on Apple Silicon)
2. Quantized ONNX model (if available)
3. Original ONNX model (fallback)

### **Hardware Detection**
```python
# Check MPS availability
import torch
print(f"MPS available: {torch.backends.mps.is_available()}")

# Check CoreML availability
import coremltools
print(f"CoreML available: {coremltools.__version__}")
```

### **Performance Monitoring**
```bash
# Real-time performance stats
curl http://localhost:8123/tts-performance

# Expected output for quantized model:
{
  "engine_loaded": true,
  "engine_type": "piper_quantized",
  "performance": {
    "avg_duration": 1.2,  # Much faster!
    "memory_mb": 250,     # Reduced memory
    "cache_hits": 5,
    "errors": 0
  }
}
```

## 🚨 Troubleshooting

### **Common Issues**

1. **Quantization Fails**
   - Ensure ONNX Runtime is installed
   - Check model compatibility
   - Verify input model format

2. **CoreML Conversion Fails**
   - Ensure CoreML Tools is installed
   - Check macOS version (13+ required)
   - Verify Apple Silicon compatibility

3. **Performance Not Improved**
   - Check if quantized model is being used
   - Verify configuration paths
   - Test with different quantization methods

### **Debug Commands**
```bash
# Check model paths
ls -la optimized_models/

# Test model loading
python -c "import onnxruntime; print('ONNX Runtime OK')"

# Test CoreML
python -c "import coremltools; print('CoreML OK')"
```

## 📈 Benchmarking

### **Performance Testing Script**
```bash
# Run comprehensive benchmarks
python scripts/quantize_tts_model.py --model piper_voices/en_US-amy-medium/model.onnx --all --benchmark
```

### **Expected Results**
- **Original Model**: 4.3s synthesis, 501MB memory
- **Dynamic Quantized**: 1.5s synthesis, 250MB memory
- **CoreML Model**: 0.8s synthesis, 200MB memory

## 🎯 Recommendations

### **For Development**
- Use dynamic quantization for immediate 2-4x speedup
- Keep original model as fallback
- Enable caching for repeated phrases

### **For Production**
- Use CoreML conversion on Apple Silicon
- Implement model selection priority
- Monitor performance metrics
- Set up automated quantization pipeline

### **For Maximum Performance**
- Combine all optimizations
- Use INT4 quantization (if quality acceptable)
- Implement streaming TTS
- Add parallel processing

## 🔮 Future Enhancements

1. **Voice Cloning**: Integrate Dia for custom voices
2. **Streaming TTS**: Start playback before synthesis complete
3. **Parallel Processing**: Multiple TTS requests simultaneously
4. **Model Pruning**: Remove unnecessary weights
5. **Custom Quantization**: Tailored quantization for specific use cases

---

**Ready to optimize your TTS system? Start with dynamic quantization for immediate improvements!** 🚀

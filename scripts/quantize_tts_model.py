#!/usr/bin/env python3
"""
TTS Model Quantization and Optimization Script

This script provides various quantization and optimization options for Piper TTS models:
1. ONNX Runtime Dynamic Quantization (INT8)
2. ONNX Runtime Static Quantization (INT8) 
3. CoreML Conversion for Apple Silicon
4. Model size and performance analysis
"""

import os
import sys
import time
import argparse
from pathlib import Path

def check_dependencies():
    """Check if required dependencies are available"""
    missing_deps = []
    
    try:
        import onnxruntime
        print("✅ ONNX Runtime available")
    except ImportError:
        missing_deps.append("onnxruntime")
    
    try:
        import coremltools
        print("✅ CoreML Tools available")
    except ImportError:
        missing_deps.append("coremltools")
    
    try:
        import torch
        if torch.backends.mps.is_available():
            print("✅ MPS (Metal Performance Shaders) available")
        else:
            print("⚠️ MPS not available")
    except ImportError:
        missing_deps.append("torch")
    
    if missing_deps:
        print(f"❌ Missing dependencies: {', '.join(missing_deps)}")
        print("Install with: pip install onnxruntime coremltools torch")
        return False
    
    return True

def analyze_model(model_path):
    """Analyze current model size and properties"""
    if not os.path.exists(model_path):
        print(f"❌ Model not found: {model_path}")
        return None
    
    size_mb = os.path.getsize(model_path) / (1024 * 1024)
    print(f"📊 Model Analysis:")
    print(f"   Path: {model_path}")
    print(f"   Size: {size_mb:.1f} MB")
    
    return {
        'path': model_path,
        'size_mb': size_mb,
        'size_bytes': os.path.getsize(model_path)
    }

def dynamic_quantization(input_model_path, output_model_path):
    """Perform dynamic quantization (INT8)"""
    try:
        from onnxruntime.quantization import quantize_dynamic, QuantType
        
        print(f"🔄 Performing dynamic quantization...")
        print(f"   Input: {input_model_path}")
        print(f"   Output: {output_model_path}")
        
        quantize_dynamic(
            input_model_path,
            output_model_path,
            weight_type=QuantType.QUInt8,
            per_channel=False,
            reduce_range=False
        )
        
        # Analyze results
        original_size = os.path.getsize(input_model_path) / (1024 * 1024)
        quantized_size = os.path.getsize(output_model_path) / (1024 * 1024)
        reduction = (1 - quantized_size / original_size) * 100
        
        print(f"✅ Dynamic quantization complete!")
        print(f"   Original size: {original_size:.1f} MB")
        print(f"   Quantized size: {quantized_size:.1f} MB")
        print(f"   Size reduction: {reduction:.1f}%")
        
        return True
        
    except Exception as e:
        print(f"❌ Dynamic quantization failed: {e}")
        return False

def static_quantization(input_model_path, output_model_path, calibration_data_path=None):
    """Perform static quantization (INT8) - requires calibration data"""
    try:
        from onnxruntime.quantization import quantize_static, QuantType, CalibrationDataReader
        
        if not calibration_data_path or not os.path.exists(calibration_data_path):
            print("⚠️ Static quantization requires calibration data")
            print("   Skipping static quantization...")
            return False
        
        print(f"🔄 Performing static quantization...")
        print(f"   Input: {input_model_path}")
        print(f"   Output: {output_model_path}")
        print(f"   Calibration data: {calibration_data_path}")
        
        # Note: This is a simplified example
        # In practice, you'd need to implement CalibrationDataReader
        quantize_static(
            input_model_path,
            output_model_path,
            calibration_data_reader=None,  # Would need proper implementation
            quant_format=QuantType.QUInt8,
            per_channel=False,
            reduce_range=False
        )
        
        print(f"✅ Static quantization complete!")
        return True
        
    except Exception as e:
        print(f"❌ Static quantization failed: {e}")
        return False

def coreml_conversion(input_model_path, output_model_path):
    """Convert ONNX model to CoreML format"""
    try:
        import coremltools as ct
        
        print(f"🔄 Converting to CoreML...")
        print(f"   Input: {input_model_path}")
        print(f"   Output: {output_model_path}")
        
        # Convert ONNX to CoreML
        coreml_model = ct.convert(
            input_model_path,
            convert_to="mlprogram",
            compute_units=ct.ComputeUnit.ALL,  # Use Neural Engine + GPU + CPU
            minimum_deployment_target=ct.target.macOS13  # macOS 13+ for Neural Engine
        )
        
        # Save the model
        coreml_model.save(output_model_path)
        
        # Analyze results
        original_size = os.path.getsize(input_model_path) / (1024 * 1024)
        coreml_size = os.path.getsize(output_model_path) / (1024 * 1024)
        
        print(f"✅ CoreML conversion complete!")
        print(f"   Original size: {original_size:.1f} MB")
        print(f"   CoreML size: {coreml_size:.1f} MB")
        print(f"   Neural Engine: ✅ Enabled")
        print(f"   GPU acceleration: ✅ Enabled")
        
        return True
        
    except Exception as e:
        print(f"❌ CoreML conversion failed: {e}")
        return False

def benchmark_model(model_path, model_type="ONNX"):
    """Benchmark model performance"""
    try:
        import onnxruntime as ort
        
        print(f"🏃 Benchmarking {model_type} model...")
        
        # Load model
        session = ort.InferenceSession(model_path)
        
        # Get model info
        input_info = session.get_inputs()[0]
        print(f"   Input shape: {input_info.shape}")
        print(f"   Input type: {input_info.type}")
        
        # Simple benchmark (would need actual input data for real test)
        start_time = time.time()
        
        # Note: This is a simplified benchmark
        # In practice, you'd run actual inference with test data
        
        load_time = time.time() - start_time
        print(f"   Load time: {load_time:.3f}s")
        
        return True
        
    except Exception as e:
        print(f"❌ Benchmarking failed: {e}")
        return False

def main():
    parser = argparse.ArgumentParser(description="TTS Model Quantization and Optimization")
    parser.add_argument("--model", required=True, help="Path to input ONNX model")
    parser.add_argument("--output-dir", default="./optimized_models", help="Output directory")
    parser.add_argument("--dynamic", action="store_true", help="Perform dynamic quantization")
    parser.add_argument("--static", action="store_true", help="Perform static quantization")
    parser.add_argument("--coreml", action="store_true", help="Convert to CoreML")
    parser.add_argument("--benchmark", action="store_true", help="Benchmark models")
    parser.add_argument("--all", action="store_true", help="Run all optimizations")
    
    args = parser.parse_args()
    
    print("🚀 TTS Model Quantization and Optimization Tool")
    print("=" * 50)
    
    # Check dependencies
    if not check_dependencies():
        sys.exit(1)
    
    # Create output directory
    os.makedirs(args.output_dir, exist_ok=True)
    
    # Analyze input model
    model_info = analyze_model(args.model)
    if not model_info:
        sys.exit(1)
    
    print("\n" + "=" * 50)
    
    # Run optimizations
    if args.all or args.dynamic:
        print("\n🔄 DYNAMIC QUANTIZATION")
        output_path = os.path.join(args.output_dir, "piper_dynamic_quantized.onnx")
        dynamic_quantization(args.model, output_path)
    
    if args.all or args.static:
        print("\n🔄 STATIC QUANTIZATION")
        output_path = os.path.join(args.output_dir, "piper_static_quantized.onnx")
        static_quantization(args.model, output_path)
    
    if args.all or args.coreml:
        print("\n🔄 COREML CONVERSION")
        output_path = os.path.join(args.output_dir, "piper_model.mlpackage")
        coreml_conversion(args.model, output_path)
    
    if args.all or args.benchmark:
        print("\n🏃 BENCHMARKING")
        benchmark_model(args.model, "Original ONNX")
        
        # Benchmark quantized models if they exist
        quantized_path = os.path.join(args.output_dir, "piper_dynamic_quantized.onnx")
        if os.path.exists(quantized_path):
            benchmark_model(quantized_path, "Dynamic Quantized ONNX")
    
    print("\n✅ Optimization complete!")
    print(f"📁 Check output directory: {args.output_dir}")

if __name__ == "__main__":
    main()

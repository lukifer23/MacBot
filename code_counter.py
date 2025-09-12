#!/usr/bin/env python3
"""
MacBot Code Counter - Counts total lines of code in the project
Excludes docs and whisper/llama.cpp integrations as requested
"""
import os
import sys
from pathlib import Path

def count_lines_in_file(filepath):
    """Count lines in a single file, handling encoding issues"""
    try:
        with open(filepath, 'r', encoding='utf-8', errors='ignore') as f:
            lines = f.readlines()
            # Count non-empty lines (excluding pure whitespace)
            code_lines = [line for line in lines if line.strip()]
            return len(lines), len(code_lines)
    except Exception as e:
        print(f"Warning: Could not read {filepath}: {e}")
        return 0, 0

# Global skip directories list
SKIP_DIRS = [
    'docs',           # Documentation
    'models',         # External models (whisper.cpp, llama.cpp)
    '__pycache__',    # Python cache
    '.git',           # Git repository data
    'data',           # Data files
    'logs',           # Log files
    'node_modules',   # Node.js dependencies
    '.pytest_cache',  # Test cache
]

def should_count_file(filepath):
    """Determine if a file should be counted based on extension"""
    # Convert to Path object for easier path operations
    path = Path(filepath)

    # Only count code files
    code_extensions = {
        '.py',    # Python
        '.js',    # JavaScript
        '.ts',    # TypeScript
        '.tsx',   # React TypeScript
        '.jsx',   # React JavaScript
        '.css',   # CSS
        '.scss',  # SCSS
        '.html',  # HTML
        '.json',  # JSON
        '.yaml',  # YAML
        '.yml',   # YAML
        '.toml',  # TOML
        '.md',    # Markdown
        '.txt',   # Text files
        '.sh',    # Shell scripts
        '.bash',  # Bash scripts
        '.zsh',   # Zsh scripts
    }

    return path.suffix.lower() in code_extensions

def count_project_code(project_root):
    """Count lines of code in the entire project"""
    total_files = 0
    total_lines = 0
    total_code_lines = 0

    file_counts = {}

    print("🔍 Scanning MacBot codebase...")
    print("=" * 60)

    # Only count files in these specific directories
    include_dirs = ['src', 'tests', 'config', 'scripts']
    include_root_files = ['*.py', '*.txt', '*.json', '*.yaml', '*.yml', '*.toml', '*.sh', '*.bash', '*.zsh']

    # Count files in specific directories
    for include_dir in include_dirs:
        dir_path = os.path.join(project_root, include_dir)
        if os.path.exists(dir_path):
            for root, dirs, files in os.walk(dir_path):
                for file in files:
                    filepath = os.path.join(root, file)
                    if should_count_file(filepath):
                        total_files += 1
                        lines, code_lines = count_lines_in_file(filepath)
                        if lines > 0:
                            total_lines += lines
                            total_code_lines += code_lines

                            # Store file info for breakdown
                            ext = Path(filepath).suffix.lower()
                            if ext not in file_counts:
                                file_counts[ext] = {'files': 0, 'lines': 0, 'code_lines': 0}
                            file_counts[ext]['files'] += 1
                            file_counts[ext]['lines'] += lines
                            file_counts[ext]['code_lines'] += code_lines

    # Count root-level files
    for pattern in include_root_files:
        for filepath in Path(project_root).glob(pattern):
            if should_count_file(str(filepath)):
                total_files += 1
                lines, code_lines = count_lines_in_file(str(filepath))
                if lines > 0:
                    total_lines += lines
                    total_code_lines += code_lines

                    # Store file info for breakdown
                    ext = Path(filepath).suffix.lower()
                    if ext not in file_counts:
                        file_counts[ext] = {'files': 0, 'lines': 0, 'code_lines': 0}
                    file_counts[ext]['files'] += 1
                    file_counts[ext]['lines'] += lines
                    file_counts[ext]['code_lines'] += code_lines

    print("\n" + "=" * 60)
    print("📊 MACBOT CODE STATISTICS")
    print("=" * 60)

    # Show breakdown by file type
    print("\n📁 File Type Breakdown:")
    print("-" * 40)

    for ext, counts in sorted(file_counts.items(), key=lambda x: x[1]['code_lines'], reverse=True):
        ext_name = {
            '.py': 'Python',
            '.js': 'JavaScript',
            '.ts': 'TypeScript',
            '.tsx': 'React TSX',
            '.jsx': 'React JSX',
            '.css': 'CSS',
            '.scss': 'SCSS',
            '.html': 'HTML',
            '.json': 'JSON',
            '.yaml': 'YAML',
            '.yml': 'YAML',
            '.toml': 'TOML',
            '.md': 'Markdown',
            '.txt': 'Text',
            '.sh': 'Shell',
            '.bash': 'Bash',
            '.zsh': 'Zsh',
        }.get(ext, ext.upper()[1:])

        print(f"{ext_name}: {counts['files']} files, {counts['code_lines']:,} code lines")

    print("-" * 40)

    # Show totals
    print("\n🎯 PROJECT TOTALS:")
    print("-" * 40)
    print(f"📁 Total files: {total_files:,}")
    print(f"📝 Total lines: {total_lines:,}")
    print(f"💻 Code lines: {total_code_lines:,}")

    # Fun facts
    print("\n🎉 FUN FACTS:")
    print("-" * 40)
    print(f"📝 Average lines per file: {total_lines // total_files if total_files > 0 else 0}")
    print(f"💻 Average code lines per file: {total_code_lines // total_files if total_files > 0 else 0}")
    print(f"📊 Largest file type: {max(file_counts.items(), key=lambda x: x[1]['code_lines'])[0].upper()[1:] if file_counts else 'None'}")
    print(f"🚀 Most numerous file type: {max(file_counts.items(), key=lambda x: x[1]['files'])[0].upper()[1:] if file_counts else 'None'}")

    # Compare to famous projects
    print("\n📚 COMPARISON TO FAMOUS PROJECTS:")
    print("-" * 40)

    comparisons = [
        ("Linux Kernel", "~30 million lines"),
        ("Windows 10", "~50 million lines"),
        ("Google Chrome", "~30 million lines"),
        ("Apache HTTP Server", "~200,000 lines"),
        ("Redis", "~150,000 lines"),
        ("PostgreSQL", "~1.2 million lines"),
        ("Node.js", "~2.2 million lines"),
        ("React", "~2.5 million lines"),
    ]

    print(f"MacBot: {total_lines:,} lines")
    for project, lines in comparisons:
        print(f"{project}: {lines}")

    return total_files, total_lines, total_code_lines

def main():
    """Main function"""
    project_root = os.path.dirname(os.path.abspath(__file__))

    print("🚀 MacBot Code Counter")
    print("Counting lines of code (excluding docs and external models)")
    print()

    try:
        files, lines, code_lines = count_project_code(project_root)

        if lines == 0:
            print("❌ No code files found to count!")
            return 1

        print("\n✅ Code counting complete!")
        print(f"🎊 MacBot contains {lines:,} lines of code across {files} files!")
        print("\n💡 Fun fact: That's enough code to fill a 200+ page novel!")
        return 0

    except Exception as e:
        print(f"❌ Error during code counting: {e}")
        return 1

if __name__ == "__main__":
    sys.exit(main())

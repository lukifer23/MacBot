"""Bounded document extraction in a disposable process, outside web workers."""

from __future__ import annotations

import io
import json
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import psutil


def extract(content: bytes, suffix: str) -> str:
    if not content or len(content) > 8 * 1024 * 1024:
        raise ValueError("Document must contain 1 byte to 8 MiB")
    if suffix not in {".txt", ".pdf", ".docx"}:
        raise ValueError("Supported document types: txt, pdf, docx")
    with tempfile.TemporaryDirectory(prefix="macbot-document-") as directory:
        path = Path(directory) / ("input" + suffix)
        path.write_bytes(content)
        process = subprocess.Popen(
            [sys.executable, "-m", "macbot.document_parser", str(path)],
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
        )
        deadline = time.monotonic() + 10
        try:
            while True:
                try:
                    output, _ = process.communicate(timeout=0.05)
                    break
                except subprocess.TimeoutExpired:
                    if time.monotonic() > deadline:
                        raise ValueError("Document extraction deadline exceeded")
                    try:
                        if psutil.Process(process.pid).memory_info().rss > 512 * 1024 * 1024:
                            raise ValueError("Document extraction memory limit exceeded")
                    except psutil.NoSuchProcess:
                        continue
            if process.returncode:
                raise ValueError("Document extraction failed or exceeded a resource limit")
            result = json.loads(output)
            if not isinstance(result, str) or not result.strip() or len(result) > 1_000_000:
                raise ValueError("Extracted document must contain 1–1000000 characters")
            return result
        finally:
            if process.poll() is None:
                process.kill()
                process.wait()
            for stream in [process.stdout, process.stderr]:
                if stream:
                    stream.close()


def worker(path: Path) -> str:
    content = path.read_bytes()
    if path.suffix == ".txt":
        text = content.decode("utf-8-sig")
    elif path.suffix == ".pdf":
        from pypdf import PdfReader

        pdf = PdfReader(io.BytesIO(content))
        if len(pdf.pages) > 500:
            raise ValueError("PDF page limit exceeded")
        parts = []
        length = 0
        for page in pdf.pages:
            part = page.extract_text() or ""
            length += len(part)
            if length > 1_000_000:
                raise ValueError("Extracted text exceeds limit")
            parts.append(part)
        text = "\n".join(parts)
    elif path.suffix == ".docx":
        import zipfile

        from docx import Document

        with zipfile.ZipFile(io.BytesIO(content)) as archive:
            if sum(f.file_size for f in archive.infolist()) > 32 * 1024 * 1024:
                raise ValueError("DOCX expansion limit exceeded")
        text = "\n".join(p.text for p in Document(io.BytesIO(content)).paragraphs)
    else:
        raise ValueError("Unsupported document type")
    if len(text) > 1_000_000:
        raise ValueError("Extracted text exceeds limit")
    return text


if __name__ == "__main__":
    import resource

    resource.setrlimit(resource.RLIMIT_CPU, (5, 6))
    # Darwin's DATA hard limit can be below the requested cap, and this limit
    # does not bound mapped allocations anyway. The parent enforces RSS and
    # wall time for every parser; CPU time is additionally bounded here.
    print(json.dumps(worker(Path(sys.argv[1]))))

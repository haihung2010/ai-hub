"""Unified runner for the 3 document-ingestion POC candidates.

Each measure function imports its library lazily inside the function body
so that importing this module in test contexts (without POC deps
installed) does NOT crash. The libraries (docling, marker, unstructured)
are heavy ML stacks that we deliberately isolate in venv_poc.

Usage:
    ./venv_poc/bin/python eval_runner.py --candidate docling --fixture path/to/file.pdf
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path
from typing import Callable, Dict


def measure_docling(pdf_path: str) -> Dict:
    """Run Docling on a PDF and return a metrics dict.

    Uses lazy imports so the module is safe to import in ai-hub's main venv
    (which does not have docling installed).
    """
    from docling.document_converter import DocumentConverter  # type: ignore

    t0 = time.perf_counter()
    converter = DocumentConverter()
    result = converter.convert(pdf_path)
    markdown = result.document.export_to_markdown()
    elapsed = time.perf_counter() - t0

    return {
        "candidate": "docling",
        "fixture": Path(pdf_path).name,
        "elapsed_seconds": round(elapsed, 3),
        "output_length_chars": len(markdown),
        "table_count_approx": markdown.count("|---"),
        "image_count_approx": markdown.count("!["),
    }


def measure_marker(pdf_path: str) -> Dict:
    """Run Marker on a PDF and return a metrics dict."""
    from marker.converters.pdf import PdfConverter  # type: ignore
    from marker.models import create_model_dict  # type: ignore

    t0 = time.perf_counter()
    converter = PdfConverter(artifact_dict=create_model_dict())
    rendered = converter(pdf_path)
    # marker returns a GenerationResult; markdown is on `.markdown`
    markdown = rendered.markdown
    elapsed = time.perf_counter() - t0

    return {
        "candidate": "marker",
        "fixture": Path(pdf_path).name,
        "elapsed_seconds": round(elapsed, 3),
        "output_length_chars": len(markdown),
        "table_count_approx": markdown.count("|---"),
        "image_count_approx": markdown.count("!["),
    }


def measure_unstructured(pdf_path: str) -> Dict:
    """Run Unstructured.io hi_res partitioner on a PDF and return a metrics dict.

    Element-based partitioning — we count elements by type rather than scanning
    markdown substrings.
    """
    from unstructured.partition.pdf import partition_pdf  # type: ignore

    t0 = time.perf_counter()
    elements = partition_pdf(
        filename=pdf_path,
        strategy="hi_res",
        languages=["vie", "eng"],
    )
    elapsed = time.perf_counter() - t0

    element_count = len(elements)
    table_count = sum(1 for e in elements if getattr(e, "category", None) == "Table")
    image_count = sum(1 for e in elements if getattr(e, "category", None) == "Image")
    output_length_chars = sum(len(getattr(e, "text", "") or "") for e in elements)

    return {
        "candidate": "unstructured",
        "fixture": Path(pdf_path).name,
        "elapsed_seconds": round(elapsed, 3),
        "output_length_chars": output_length_chars,
        "element_count": element_count,
        "table_count": table_count,
        "image_count": image_count,
    }


CANDIDATES: Dict[str, Callable[[str], Dict]] = {
    "docling": measure_docling,
    "marker": measure_marker,
    "unstructured": measure_unstructured,
}


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Document ingestion POC runner — measures one candidate on one fixture.",
    )
    parser.add_argument(
        "--candidate",
        required=True,
        choices=sorted(CANDIDATES.keys()),
        help="Which POC candidate to run.",
    )
    parser.add_argument(
        "--fixture",
        required=True,
        type=str,
        help="Path to a PDF file to ingest.",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)

    pdf_path = Path(args.fixture)
    if not pdf_path.is_file():
        print(f"ERROR: fixture not found: {pdf_path}", file=sys.stderr)
        return 2

    results_dir = Path(__file__).resolve().parent / "results"
    results_dir.mkdir(exist_ok=True)

    measure_fn = CANDIDATES[args.candidate]
    metrics = measure_fn(str(pdf_path))

    out_path = results_dir / f"{metrics['candidate']}_{pdf_path.stem}.json"
    out_path.write_text(json.dumps(metrics, indent=2, ensure_ascii=False))

    print(json.dumps(metrics, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
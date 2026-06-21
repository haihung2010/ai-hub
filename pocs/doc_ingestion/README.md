# Document Ingestion POC

Evaluate 3 candidate libraries for Vietnamese PDF document ingestion:
**Docling**, **Marker**, **Unstructured.io**.

The goal is to pick the best tool for extracting structured content
(Vietnamese text + tables + images) from real-world Vietnamese PDFs
that ai-hub users upload, then chunk the output for the existing RAG
knowledge base.

---

## Why this POC exists

ai-hub already has RAG ingestion via
`app/services/knowledge_ingestion_service.py`, but it only handles
plain text + markdown + DOCX. Real users (e.g. vehix fanpage, PDM
case attachments, Chatwoot contracts) ship **scanned Vietnamese PDFs**
with tables and embedded images that need proper layout analysis
before chunking.

We need to answer:
> *Which library gives the best Vietnamese OCR + table + image
> extraction on a 16GB GPU box, AND chunks cleanly for embedding?*

---

## Evaluation criteria

Each candidate will be scored on 5 axes. All measurements are written
to `results/{candidate}_{fixture_stem}.json`.

| ID    | Criterion                        | How we measure                                                    |
|-------|----------------------------------|-------------------------------------------------------------------|
| **B1** | Vietnamese OCR quality         | Output length vs input page count (chars/page), spot-checked prose |
| **B2** | Table extraction                | Markdown `|---` substring count for Docling/Marker, `Table` element count for Unstructured |
| **B3** | Image understanding             | Markdown `![` substring count for Docling/Marker, `Image` element count for Unstructured |
| **B4** | 16GB VRAM footprint             | Wall-clock time on `nvidia-smi`-logged RTX 5060 Ti 16GB            |
| **B5** | Multimodal-aware chunking       | Whether output keeps table cells aligned + image captions bound to source |

**Pass threshold:** candidate must score ≥ 3/5, with B1 (Vietnamese OCR)
being mandatory. If B1 fails, candidate is rejected regardless of other scores.

---

## Candidates

1. **Docling** (`docling>=2.0`) — IBM's doc-ai toolkit. Layout analysis
   via RT-DETR + TableFormer. Exports Markdown + JSON + DocTags.
2. **Marker** (`marker-pdf>=1.0`) — PDF → Markdown optimized for RAG.
   Uses surya + Texify for OCR + equation handling.
3. **Unstructured.io** (`unstructured>=0.16`) — Element-based partitioning.
   `hi_res` strategy uses detectron2 for layout detection.

---

## Test fixtures

Fixtures live in `fixtures/`. The full set will be added in Task 3.2:

| Fixture                     | Source                  | Tests                          |
|-----------------------------|-------------------------|--------------------------------|
| `vehix_policy_vi.pdf`       | vehix fanpage KB        | B1 Vietnamese OCR + B2 tables  |
| `chatwoot_contract_en.pdf`  | Chatwoot webhook sample | B1 English OCR + B2 tables     |
| `pdm_case_report_scanned.pdf` | PDM case attachment    | B1 Vietnamese OCR (scanned)   |
| `mixed_vi_en_image.pdf`     | Manual                  | B3 image understanding         |
| `multi_table_legal.pdf`     | Manual                  | B2 table extraction stress-test |

---

## Setup (operator runs in Task 3.2)

POC deps are isolated from ai-hub's `./venv/` to avoid breaking
existing models (docling/marker pull heavy deps). Use a separate venv:

```bash
cd /home/hung/ai-hub/pocs/doc_ingestion
python -m venv venv_poc
./venv_poc/bin/pip install -r requirements_poc.txt
```

Do **not** install these into `./venv/`.

---

## Run a single candidate

```bash
./venv_poc/bin/python eval_runner.py \
  --candidate docling \
  --fixture fixtures/vehix_policy_vi.pdf
```

Output:
- Writes `results/{candidate}_{fixture_stem}.json`
- Prints JSON to stdout
- Exit 0 on success, non-zero on error

## Run all candidates (Task 3.3)

```bash
for c in docling marker unstructured; do
  for f in fixtures/*.pdf; do
    ./venv_poc/bin/python eval_runner.py --candidate "$c" --fixture "$f"
  done
done
```

---

## Output JSON schema

```json
{
  "candidate": "docling",
  "fixture": "vehix_policy_vi.pdf",
  "elapsed_seconds": 12.34,
  "output_length_chars": 5432,
  "table_count_approx": 3,
  "image_count_approx": 1
}
```

Unstructured uses `element_count` / `table_count` / `image_count` keys
instead (element-based partitioning, not Markdown).

---

## Decision matrix (filled in Task 3.3)

| Candidate     | B1 VI OCR | B2 Tables | B3 Images | B4 VRAM | B5 Chunk | Score | Decision |
|---------------|-----------|-----------|-----------|---------|----------|-------|----------|
| Docling       | TBD       | TBD       | TBD       | TBD     | TBD      | TBD   | TBD      |
| Marker        | TBD       | TBD       | TBD       | TBD     | TBD      | TBD   | TBD      |
| Unstructured  | TBD       | TBD       | TBD       | TBD     | TBD      | TBD   | TBD      |

---

## Next steps after POC

- **Win**: integrate winner into `app/services/knowledge_ingestion_service.py`
  via a new `extract_pdf()` entrypoint, gated on a new `ENABLE_PDF_INGEST=true` env var.
- **Lose**: stay with current text-only ingestion + add a `pdftotext` fallback.
- **Either way**: ship a `POST /v1/admin/knowledge/upload` endpoint that accepts
  multipart PDFs and pipes them through the chosen extractor.
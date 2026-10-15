# Optional ML/OCR extras (#144)

Stage 1, Stage 2, and light Stage 3 (datasketch MinHash + extractive summary)
run on the core lock. Torch, transformers, sentence-transformers, scikit-learn,
easyocr, pdf2image, PyPDF2, and pillow are not in `requirements.txt`.

## Install

```bash
cd Scraping_project
pip install -r requirements.txt                      # lean / Stage 1–3
pip install -r requirements-ml.txt -r requirements-ocr.txt   # Stage 4
# packaging extras (pyproject.toml):
pip install -e ".[ml]"          # or ".[ocr]" or ".[stage3-4]"
```

Recompile locks from this directory (no machine-local paths):

```bash
make lock          # keep existing pins
make update-deps   # upgrade pins, including ML/OCR locks
```

## Docker targets

| Target | Contents | Compose |
|--------|----------|---------|
| `base` / `core` / `production` / `crawler` / `stage3` | core only | profile `core` (`scraper`, stage1–3) |
| `ml` / `stage4` | core + `requirements-ml.txt` + `requirements-ocr.txt` + poppler-utils | profiles `ml` or `full` (`stage4-worker`) |

```bash
docker compose --profile core build
docker compose --profile ml build stage4
docker compose --profile core up -d          # lean: no stage4
docker compose --profile ml up -d stage4-worker
```

Redis in Compose and Helm stays `REDIS_HOST` / `REDIS_PORT` / `REDIS_PASSWORD` /
`REDIS_DB`. Do not publish `REDIS_URL` (fallback only when `REDIS_HOST` is unset).

## Image size (AC4)

Not measured in this change (no image build recorded). Compare after a local rebuild:

```bash
docker build -t scrapy-core:local --target core .
docker build -t scrapy-stage3:local --target stage3 .
docker build -t scrapy-ml:local --target ml .
docker build -t scrapy-stage4:local --target stage4 .
docker images --format 'table {{.Repository}}\t{{.Tag}}\t{{.Size}}' \
  scrapy-core:local scrapy-stage3:local scrapy-ml:local scrapy-stage4:local
```

Qualitative expectation only: `core` / `stage3` omit the torch + transformers + easyocr trees (often on the order of a few GB). Paste real `docker images` output before treating AC4 as done.

## Quantized Stage 3 (CPU-only)

`stage3.model_variant` defaults to `full`. Set `quantized` for the CPU int8/gguf
distill path. That path is an offline stub in `src/stage3/quantized_model.py`.
It does not import torch, does not download weights, and does not use
`requirements-stage4.txt` (left unchanged while the pip lock bump is open).

Quality caveat: stage3.model_variant quantized is an int8/gguf distill stub, not the pinned full-precision sshleifer/distilbart-cnn-12-6 checkpoint. The offline stub keeps at most two sentences and can drop entities, numbers, and later context. It is not citation-grade. The default variant full keeps the existing five-sentence extractive summary and also does not load torch.

Image size: the stage3 image installs requirements.txt only and does not install torch or requirements-stage4.txt. models.lock.json pins pytorch_model.bin for sshleifer/distilbart-cnn-12-6 at 1222317369 bytes; that file is not copied into the stage3 image. This quantized option adds no GGUF blob either. A real int8 GGUF of that distill checkpoint is much smaller than the 1.2 GB fp32 bin and would still stay off the lean image until a later pin. No image was rebuilt for this note.

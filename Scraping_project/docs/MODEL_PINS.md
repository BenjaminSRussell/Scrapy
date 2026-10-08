# Pinned ML models (#486)

The summarization and embedding models are pinned in `models.lock.json`. Each entry records:

- the Hugging Face repo id;
- the full commit `revision` (40 hex characters);
- every file the pipeline loads, with its size and a digest:
  - `sha256` for LFS files (weights such as `model.safetensors` or `pytorch_model.bin`);
  - the git blob `sha1` for small files (configs and tokenizers).

These digests are the ones the Hub itself publishes.

| Entry | Repo | Used by |
|---|---|---|
| `stage3_summarizer` | `sshleifer/distilbart-cnn-12-6` | Stage 3 (`stage3.model_name`) |
| `stage4_summarizer` | `facebook/bart-large-cnn` | Stage 4 (`stage4.model_name`) |
| `stage4_entity_embeddings` | `sentence-transformers/all-MiniLM-L6-v2` | Stage 4 entity summarization |

## Download and verify

```bash
python cli.py setup                 # download the pinned revisions, then verify every file
python cli.py setup --verify-only   # verify the existing HF cache only (no network)
python cli.py setup --model stage3_summarizer
make models                         # same as `python cli.py setup`
```

`setup` **fails closed**: a missing file, a wrong size or a digest mismatch exits with status 1 and lists every problem. For example:

```
Model integrity check FAILED (refusing to use unverified models):
  facebook/bart-large-cnn@37f520fa92: model.safetensors: sha256 1a2b3c4d5e6f… != pinned 40041830399a…
```

If that happens, delete the bad snapshot from `~/.cache/huggingface/hub` and rerun `setup`. Don't edit the lock to match.

## Updating a pin

1. Run `python scripts/update_model_lock.py --check`. It exits with status 1 if upstream moved.
2. Run `python scripts/update_model_lock.py` to re-pin every entry to the current `main` revision of its repo.
3. Review the `models.lock.json` diff. Expect new revision and digest values; a new or removed file needs a reason.
4. Run `python cli.py setup` locally to verify, then commit the lock in its own PR.

To pin a new model or file, add an entry to `models.lock.json` (`name`, `repo_id`, `files`) and run the update script.

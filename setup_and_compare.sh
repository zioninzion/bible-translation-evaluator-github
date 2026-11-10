#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"
PYTHON_BIN="${PYTHON_BIN:-python3}"
if [[ ! -d .venv ]]; then
  "$PYTHON_BIN" -m venv .venv
fi
source .venv/bin/activate
python -m pip install --upgrade pip setuptools wheel
python -m pip install -r requirements.txt
python -m spacy download en_core_web_sm
python -m nltk.downloader brown
mkdir -p data cache results
if [[ ! -s data/macula_source.sqlite ]]; then
  python macula_pipeline.py fetch --nt-only --dest data/sources
  python macula_pipeline.py extract --greek data/sources/macula-greek --out data/macula_source.sqlite
fi
if [[ ! -s cache/brown.json.gz ]]; then
  python bible_eval.py build-reference --brown --model en_core_web_sm --max-n 8 --out cache/brown.json.gz
fi
python compare_chapters.py --passage both

#!/usr/bin/env python3
"""An auditable approximation of GBI's quantitative Bible-translation evaluation.

Paper: 'A Quantitative Evaluation of the Christian Standard Bible', Dr. Andi Wu.
This is an independent implementation, NOT GBI's unpublished software.
"""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
import csv
import gzip
import hashlib
import json
from pathlib import Path
import re
import sys
from typing import Any, Iterable, Iterator

MAX_N = 8
SCHEMA_VERSION = 1

# Transcribed from the supplied five-page report; values are PERCENTAGES.
# transfer, lexical, fluency, vocabulary, published_combined_literalness,
# published_combined_readability, published_FINAL_score
PUBLISHED = {
    "CSB": (67.62, 59.25, 60.44, 79.38, 64.83, 66.75, 70.3),
    "ESV": (69.67, 66.89, 54.80, 77.48, 68.74, 62.36, 69.3),
    "NASB": (66.63, 70.70, 53.66, 77.62, 67.99, 61.65, 68.7),
    "NKJV": (63.05, 69.52, 51.77, 77.42, 65.21, 60.32, 67.3),
    "NRSV": (59.32, 62.88, 57.12, 77.70, 60.51, 63.08, 67.0),
    "NET": (52.38, 57.06, 59.44, 79.96, 53.94, 66.28, 63.9),
    "NIV": (52.56, 54.19, 61.00, 79.60, 53.10, 67.20, 63.6),
    "KJV": (63.13, 73.48, 40.72, 65.06, 66.58, 48.83, 61.0),
    "NLT": (36.23, 47.25, 63.49, 83.27, 39.90, 70.08, 56.6),
}

# spaCy English parse labels normalized toward Universal Dependencies labels.
# Cross-linguistic relation equivalence remains a methodological approximation.
DEP_ALIASES = {
    "root": "root", "dobj": "obj", "pobj": "obl", "dative": "iobj",
    "nsubjpass": "nsubj", "csubjpass": "csubj", "auxpass": "aux",
    "relcl": "acl", "acomp": "xcomp", "attr": "xcomp",
    "prep": "case", "agent": "obl", "poss": "nmod", "compound": "compound",
    "npadvmod": "obl", "oprd": "xcomp", "prt": "compound",
    "neg": "advmod", "predet": "det", "nummod": "nummod",
    "punct": "punct", "cc": "cc", "conj": "conj", "amod": "amod",
    "appos": "appos", "advcl": "advcl", "advmod": "advmod",
    "det": "det", "mark": "mark", "aux": "aux", "cop": "cop",
    "xcomp": "xcomp", "ccomp": "ccomp", "nsubj": "nsubj",
    "csubj": "csubj", "iobj": "iobj", "obj": "obj",
    "acl": "acl", "case": "case", "nmod": "nmod", "obl": "obl",
    "flat": "flat", "fixed": "fixed", "expl": "expl", "discourse": "discourse",
    "parataxis": "parataxis", "list": "list", "vocative": "vocative",
}


def clean_relation(value: str) -> str:
    value = str(value or "dep").casefold().split(":", 1)[0]
    return DEP_ALIASES.get(value, value)


def is_word(token: dict) -> bool:
    upos = str(token.get("upos", "")).upper()
    if upos in {"PUNCT", "SYM", "SPACE", "X_PUNCT"}:
        return False
    form = str(token.get("form", ""))
    return any(ch.isalpha() for ch in form)


def lower_word(token: dict, field: str = "form") -> str:
    return str(token.get(field) or token.get("form") or "").casefold().strip()


def syntax_label(token: dict) -> str:
    return f"{str(token.get('upos', 'X')).upper()}:{clean_relation(token.get('deprel', 'dep'))}"


def ngram_hash(items: Iterable[str]) -> int:
    payload = "\x1f".join(items).encode("utf-8")
    return int.from_bytes(hashlib.blake2b(payload, digest_size=8).digest(), "big")


def pattern_hashes(tokens: list[dict], max_n: int = MAX_N) -> Iterator[int]:
    # A verse is treated as a sequence; sentence boundary information may
    # optionally be supplied as token.sent. No patterns cross a boundary.
    sequences: list[list[str]] = []
    prev_sent = object()
    current: list[str] = []
    for t in tokens:
        if not is_word(t):
            continue
        sent = t.get("sent", 0)
        if current and sent != prev_sent:
            sequences.append(current)
            current = []
        current.append(syntax_label(t))
        prev_sent = sent
    if current:
        sequences.append(current)
    for labels in sequences:
        for n in range(1, min(max_n, len(labels)) + 1):
            for start in range(len(labels) - n + 1):
                yield ngram_hash(labels[start:start+n])


def from_spacy_doc(doc: Any) -> list[dict]:
    result = []
    for t in doc:
        result.append({
            "id": t.i + 1, "form": t.text,
            "lemma": (t.lemma_ or t.text).casefold(),
            "upos": t.pos_ or "X",
            "head": (t.head.i + 1) if t.head.i != t.i else 0,
            "deprel": clean_relation(t.dep_),
            "sent": t.sent.start,
        })
    return result


def get_nlp(model: str):
    try:
        import spacy
    except ImportError as e:
        raise SystemExit("spaCy is required for parsing. Run: pip install -r requirements.txt") from e
    try:
        return spacy.load(model, disable=["ner"])
    except OSError as e:
        raise SystemExit(f"spaCy model {model!r} missing. Run: python -m spacy download {model}") from e


def make_reference(name: str, sentences: Iterable[list[dict]], max_n: int) -> dict:
    vocabulary: set[str] = set()
    patterns: set[int] = set()
    sent_count = 0
    token_count = 0
    for tokens in sentences:
        sent_count += 1
        for t in tokens:
            if is_word(t):
                vocabulary.add(lower_word(t))
                token_count += 1
        patterns.update(pattern_hashes(tokens, max_n))
    return {
        "schema": SCHEMA_VERSION, "name": name, "max_n": max_n,
        "sentence_count": sent_count, "word_count": token_count,
        "vocabulary": sorted(vocabulary),
        "patterns": sorted(patterns),
        "pattern_definition": "contiguous [UPOS:normalized_dependency_relation] token ngrams, 1..N",
    }


def save_reference(model: dict, path: Path):
    path.parent.mkdir(parents=True, exist_ok=True)
    with gzip.open(path, "wt", encoding="utf-8", compresslevel=5) as f:
        json.dump(model, f, ensure_ascii=False, separators=(",", ":"))


def load_reference(path: Path) -> dict:
    with gzip.open(path, "rt", encoding="utf-8") as f:
        model = json.load(f)
    if model.get("schema") != SCHEMA_VERSION:
        raise ValueError("Unknown reference-model schema")
    model["vocabulary"] = set(model["vocabulary"])
    model["patterns"] = set(model["patterns"])
    return model


def read_jsonl(path: Path) -> Iterator[dict]:
    with path.open("r", encoding="utf-8-sig") as f:
        for num, line in enumerate(f, 1):
            if line.strip():
                try:
                    obj = json.loads(line)
                    if not isinstance(obj, dict):
                        raise ValueError("JSONL lines must be JSON objects")
                    yield obj
                except Exception as e:
                    raise ValueError(f"{path}:{num}: {e}") from e


def build_reference_command(args):
    if not (args.brown or args.tagged_jsonl or args.text_file):
        raise SystemExit("Choose --brown, --tagged-jsonl, or --text-file")
    if sum(bool(x) for x in (args.brown, args.tagged_jsonl, args.text_file)) != 1:
        raise SystemExit("Specify exactly ONE reference corpus source")
    max_n = args.max_n
    if args.tagged_jsonl:
        sentences = (entry["tokens"] for entry in read_jsonl(Path(args.tagged_jsonl)))
        name = "User-supplied tagged sentences (NOT Brown)"
    else:
        nlp = get_nlp(args.model)
        if args.brown:
            try:
                from nltk.corpus import brown
                raw_sentences = brown.sents()
            except LookupError as e:
                raise SystemExit("NLTK Brown corpus missing. Run: python -m nltk.downloader brown") from e
            text_iter = (" ".join(sent) for sent in raw_sentences)
            name = "Brown Corpus (NLTK), parsed with " + args.model
        else:
            with Path(args.text_file).open("r", encoding="utf-8-sig") as f:
                texts = [line.strip() for line in f if line.strip()]
            text_iter = iter(texts)
            name = "User-supplied text file (NOT Brown), parsed with " + args.model
        if args.max_sentences:
            from itertools import islice
            text_iter = islice(text_iter, args.max_sentences)
        sentences = (from_spacy_doc(doc) for doc in nlp.pipe(text_iter, batch_size=64))
    if args.tagged_jsonl and args.max_sentences:
        from itertools import islice
        sentences = islice(sentences, args.max_sentences)
    result = make_reference(name, sentences, max_n)
    save_reference(result, Path(args.out))
    print(f"Saved {args.out}: {result['sentence_count']:,} sentences; "
          f"{result['word_count']:,} words; {len(result['patterns']):,} syntactic patterns.")


def load_verse_texts(path: Path) -> dict[str, str]:
    result = {}
    with path.open("r", encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f, delimiter="\t")
        if not {"verse", "text"} <= set(reader.fieldnames or []):
            raise ValueError("English TSV must contain columns 'verse' and 'text'")
        for row in reader:
            verse = row["verse"].strip()
            if not verse or verse in result:
                raise ValueError(f"Blank or duplicate verse in English TSV: {verse!r}")
            result[verse] = row["text"]
    return result


def read_conllu(path: Path) -> dict[str, list[dict]]:
    """Read one UD CoNLL-U block per Bible verse; explicit verse/sent_id comment."""
    result = {}
    block: list[str] = []

    def finish(lines: list[str]) -> None:
        if not lines:
            return
        verse = None
        toks = []
        for line in lines:
            if line.startswith("#"):
                m = re.match(r"#\s*(?:verse_id|sent_id)\s*=\s*(.*?)\s*$", line)
                if m:
                    verse = m.group(1)
                continue
            cols = line.split("\t")
            if len(cols) < 8:
                raise ValueError(f"Invalid CoNLL-U row (expected >=8 tabs): {line!r}")
            # Skip multiword tokens and empty nodes, per CoNLL-U format.
            if not cols[0].isdigit():
                continue
            idx = int(cols[0]); head = int(cols[6]) if cols[6].isdigit() else 0
            toks.append({"id": idx, "form": cols[1], "lemma": cols[2],
                         "upos": cols[3], "head": head, "deprel": clean_relation(cols[7]),
                         "sent": 0})
        if verse is None:
            raise ValueError("Every CoNLL-U verse block must have '# sent_id = VERSE_ID'")
        if verse in result:
            raise ValueError(f"Repeated verse in CoNLL-U: {verse!r}; combine sentences upstream")
        ids = {t["id"] for t in toks}
        if len(ids) != len(toks):
            raise ValueError(f"Duplicate source token ID in {verse}")
        if any(t["head"] not in ids and t["head"] != 0 for t in toks):
            raise ValueError(f"Invalid head in verse {verse}")
        result[verse] = toks

    with path.open("r", encoding="utf-8-sig") as f:
        for line in f:
            line = line.rstrip("\n\r")
            if not line:
                finish(block)
                block = []
            else:
                block.append(line)
    finish(block)
    return result


def read_alignments(path: Path) -> dict[str, list[list[int]]]:
    result: dict[str, list[list[int]]] = defaultdict(list)
    with path.open("r", encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f, delimiter="\t")
        if not {"verse", "source", "target"} <= set(reader.fieldnames or []):
            raise ValueError("Alignment TSV must have columns verse,source,target")
        for row in reader:
            result[row["verse"].strip()].append([int(row["source"]), int(row["target"])])
    return dict(result)


def prepare_command(args):
    sources = read_conllu(Path(args.source))
    english = load_verse_texts(Path(args.english))
    align = read_alignments(Path(args.alignments)) if args.alignments else {}
    if set(sources) != set(english):
        raise SystemExit(f"Verse IDs differ: {len(set(sources)-set(english))} source-only, "
                         f"{len(set(english)-set(sources))} translation-only. "
                         "Make both sets equal before comparing.")
    if args.alignments and (set(align) - set(english)):
        raise SystemExit("Alignment TSV contains verses missing from input texts")
    nlp = get_nlp(args.model)
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    with out.open("w", encoding="utf-8") as f:
        for verse, doc in zip(english, nlp.pipe(english.values(), batch_size=64)):
            target = from_spacy_doc(doc)
            pairs = align.get(verse, [])
            src_ids = {t["id"] for t in sources[verse]}
            tgt_ids = {t["id"] for t in target}
            bad = [p for p in pairs if p[0] not in src_ids or p[1] not in tgt_ids]
            if bad:
                raise ValueError(f"Alignment token IDs invalid for {verse}: {bad[:5]}")
            row = {"verse": verse, "source": sources[verse], "target": target,
                   "align": pairs}
            f.write(json.dumps(row, ensure_ascii=False) + "\n")
    print(f"Prepared {len(english):,} verses in {out}. "
          f"{'Alignments loaded.' if args.alignments else 'NO alignments: readability only.'}")



def prepare_english_command(args):
    """Useful preliminary comparison when Hebrew/Greek alignments are not available."""
    english = load_verse_texts(Path(args.english))
    nlp = get_nlp(args.model)
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    with out.open("w", encoding="utf-8") as f:
        for verse, doc in zip(english, nlp.pipe(english.values(), batch_size=64)):
            f.write(json.dumps({"verse": verse, "source": [],
                                "target": from_spacy_doc(doc), "align": []},
                               ensure_ascii=False) + "\n")
    print(f"Prepared {len(english):,} English verses: {out} (readability only)")


def tokens_command(args):
    verses = load_verse_texts(Path(args.english))
    if args.verse not in verses:
        raise SystemExit(f"Verse {args.verse!r} not in TSV")
    nlp = get_nlp(args.model)
    for t in from_spacy_doc(nlp(verses[args.verse])):
        print(f"{t['id']:>3} {t['form']:<19} lemma={t['lemma']:<15} "
              f"POS={t['upos']:<7} head={t['head']:<3} relation={t['deprel']}")


def relation_transfer(source: list[dict], target: list[dict], align: list[list[int]]) -> tuple[int,int,int]:
    """Count source dependency edges whose aligned endpoints form same UD edge in English.

    A source edge counts at most once. Multiple alignments may satisfy one edge.
    """
    target_edges = {(int(t["head"]), int(t["id"]), clean_relation(t["deprel"]))
                    for t in target if int(t.get("head", 0)) != 0
                    and is_word(t) and clean_relation(t["deprel"]) != "punct"}
    by_source: dict[int, set[int]] = defaultdict(set)
    for s, t in align:
        by_source[int(s)].add(int(t))
    total = 0
    matched = 0
    comparable = 0
    for src in source:
        relation = clean_relation(src.get("deprel", "dep"))
        h = int(src.get("head", 0)); d = int(src["id"])
        if h == 0 or relation == "punct" or not is_word(src):
            continue
        total += 1
        heads = by_source.get(h, set())
        deps = by_source.get(d, set())
        if heads and deps:
            comparable += 1
            if any((th, td, relation) in target_edges
                   for th in heads for td in deps):
                matched += 1
    return matched, total, comparable


def consistency_rates(pairs: Counter[tuple[str, str]]) -> dict:
    """Operationalize the report's underspecified 'one-to-one' word mapping.

    forward: percentage of observed aligned lemma-pairs consistent with the
    most common English lemma for each source lemma.
    reverse: analogous English -> source consistency.
    mutual: percentage of pairs chosen by BOTH modal maps (approx isomorphism).
    The default reported metric is mutual, intentionally conservative.
    """
    total = sum(pairs.values())
    if not total:
        return {"mutual": None, "forward": None, "reverse": None, "pairs": 0}
    forward_counts: dict[str, Counter] = defaultdict(Counter)
    reverse_counts: dict[str, Counter] = defaultdict(Counter)
    for (source, target), count in pairs.items():
        forward_counts[source][target] += count
        reverse_counts[target][source] += count

    def modal(counter: Counter) -> str:
        max_n = max(counter.values())
        return min(k for k, n in counter.items() if n == max_n)

    fwd = {s: modal(c) for s, c in forward_counts.items()}
    rev = {t: modal(c) for t, c in reverse_counts.items()}
    f_count = sum(n for (s,t),n in pairs.items() if fwd[s] == t)
    r_count = sum(n for (s,t),n in pairs.items() if rev[t] == s)
    mutual_count = sum(n for (s,t),n in pairs.items() if fwd[s] == t and rev[t] == s)
    return {"mutual": 100*mutual_count/total,
            "forward": 100*f_count/total, "reverse": 100*r_count/total,
            "pairs": total}


def percentage(num: int, den: int) -> float | None:
    return 100 * num / den if den else None


def combined(a: float | None, b: float | None) -> float | None:
    return (2*a+b)/3 if a is not None and b is not None else None


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for block in iter(lambda: f.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def numeric(x: float | None) -> str:
    return "" if x is None else f"{x:.2f}"


def validate_document(doc: dict, file: Path):
    if not {"verse", "target", "source", "align"} <= set(doc):
        raise ValueError(f"{file}: record missing one of verse/source/target/align")
    for side in ("source", "target"):
        ids = {int(t["id"]) for t in doc[side]}
        if len(ids) != len(doc[side]):
            raise ValueError(f"{doc['verse']}: duplicate token ID on {side}")
        for t in doc[side]:
            if int(t.get("head", 0)) not in ids | {0}:
                raise ValueError(f"{doc['verse']}: invalid head in {side}")
            if not {"form", "upos", "deprel"} <= set(t):
                raise ValueError(f"{doc['verse']}: token missing form/upos/deprel")
    sids = {int(t["id"]) for t in doc["source"]}
    tids = {int(t["id"]) for t in doc["target"]}
    for pair in doc["align"]:
        if len(pair) != 2 or int(pair[0]) not in sids or int(pair[1]) not in tids:
            raise ValueError(f"{doc['verse']}: invalid alignment {pair}")


def compute_score(name: str, path: Path, reference: dict,
                  balance_penalty: float = 0.25) -> dict:
    matched = total_source_rel = comparable = 0
    total_source_words = aligned_source_words = 0
    total_target_words = aligned_target_words = 0
    word_count = vocab_hits = pattern_count = pattern_hits = 0
    lemma_pairs: Counter = Counter()
    verses = 0
    saw_alignment = False
    for doc in read_jsonl(path):
        validate_document(doc, path)
        verses += 1
        source, target, align = doc["source"], doc["target"], doc["align"]
        if align:
            saw_alignment = True
        m, t, c = relation_transfer(source, target, align)
        matched += m; total_source_rel += t; comparable += c
        word_src = {int(t["id"]) for t in source if is_word(t)}
        word_dst = {int(t["id"]) for t in target if is_word(t)}
        pairs_word = [(int(s), int(t)) for s,t in align if int(s) in word_src and int(t) in word_dst]
        total_source_words += len(word_src)
        total_target_words += len(word_dst)
        aligned_source_words += len(word_src & {s for s,_ in pairs_word})
        aligned_target_words += len(word_dst & {t for _,t in pairs_word})
        source_by_id = {int(t["id"]): t for t in source}
        target_by_id = {int(t["id"]): t for t in target}
        for s, t in pairs_word:
            lemma_pairs[(lower_word(source_by_id[s], "lemma"),
                         lower_word(target_by_id[t], "lemma"))] += 1
        for tok in target:
            if is_word(tok):
                word_count += 1
                if lower_word(tok) in reference["vocabulary"]:
                    vocab_hits += 1
        for h in pattern_hashes(target, reference["max_n"]):
            pattern_count += 1
            if h in reference["patterns"]:
                pattern_hits += 1
    lex = consistency_rates(lemma_pairs)
    transfer = percentage(matched, total_source_rel) if saw_alignment else None
    consistency = lex["mutual"] if saw_alignment else None
    fluency = percentage(pattern_hits, pattern_count)
    vocabulary = percentage(vocab_hits, word_count)
    lit = combined(transfer, consistency)
    read = combined(fluency, vocabulary)
    balanced = ((lit+read)/2 - balance_penalty*abs(lit-read)
                if lit is not None and read is not None else None)
    return {
        "version": name, "verses": verses, "transfer": transfer,
        "consistency": consistency, "consistency_forward": lex["forward"],
        "consistency_reverse": lex["reverse"],
        "fluency": fluency, "vocabulary": vocabulary,
        "literalness": lit, "readability": read,
        "balance_proxy_NOT_GBI": balanced,
        "source_word_alignment_coverage": percentage(aligned_source_words, total_source_words),
        "target_word_alignment_coverage": percentage(aligned_target_words, total_target_words),
        "comparable_source_relation_coverage": percentage(comparable, total_source_rel),
        "source_relations": total_source_rel, "matched_relations": matched,
        "aligned_lemma_pairs": lex["pairs"],
        "target_word_count": word_count, "brown_vocabulary_hits": vocab_hits,
        "syntactic_ngrams": pattern_count, "brown_syntax_pattern_hits": pattern_hits,
        "input_sha256": sha256_file(path),
    }


def write_csv(rows: list[dict], path: Path):
    path.parent.mkdir(parents=True, exist_ok=True)
    if not rows:
        return
    with path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=list(rows[0]))
        writer.writeheader(); writer.writerows(rows)


def plot_scores(rows: list[dict], path: Path, benchmark: bool = False):
    try:
        import matplotlib.pyplot as plt
    except ImportError:
        print("matplotlib not installed: chart skipped", file=sys.stderr)
        return
    scatter = [r for r in rows if r.get("literalness") is not None and r.get("readability") is not None]
    if not scatter:
        print("No complete literalness+readability rows: chart skipped", file=sys.stderr)
        return
    fig, ax = plt.subplots(figsize=(9.5, 7))
    label_offsets = {  # only used to avoid collisions in the published nine-version chart
        "ESV": (10, 10), "NASB": (10, -13), "NKJV": (-41, -10),
        "NIV": (8, 7), "NET": (9, -15), "CSB": (7, 8),
    }
    for row in scatter:
        x = row["literalness"]; y = row["readability"]
        ax.scatter(x, y, s=65)
        xy = label_offsets.get(row["version"], (7, 7)) if benchmark else (7, 7)
        ax.annotate(row["version"], (x, y), xytext=xy,
                    textcoords="offset points", fontsize=10)
    ax.plot([0,100],[0,100], linestyle="--", linewidth=1, alpha=.35)
    ax.set_xlim(25,85); ax.set_ylim(25,90)
    ax.set_xlabel("Literalness (%) →")
    ax.set_ylabel("Readability (%) →")
    ax.set_title("Published GBI scores (not recomputed)" if benchmark
                 else "Calculated approximation: Bible literalness vs readability")
    ax.grid(alpha=.16)
    fig.tight_layout(); path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(path, dpi=170); plt.close(fig)
    print("Chart:", path)


def score_command(args):
    reference = load_reference(Path(args.reference))
    rows = []
    for spec in args.version:
        if "=" not in spec:
            raise SystemExit("--version must have format NAME=path/to/data.jsonl")
        name, filename = spec.split("=",1)
        if not name or not filename:
            raise SystemExit("Empty name or filename in --version argument")
        rows.append(compute_score(name, Path(filename), reference, args.balance_penalty))
    rows.sort(key=lambda r: (r["balance_proxy_NOT_GBI"] is not None,
                             r["balance_proxy_NOT_GBI"] or -1), reverse=True)
    out_dir = Path(args.outdir)
    write_csv(rows, out_dir / "scores.csv")
    summary = {"note": "INDEPENDENT APPROXIMATION, NOT the unpublished GBI algorithm",
               "reference_corpus": reference["name"],
               "reference_sha256": sha256_file(Path(args.reference)),
               "reference_sentences": reference["sentence_count"],
               "balance_formula": "(literalness+readability)/2 - lambda*abs(literalness-readability)",
               "balance_penalty_lambda": args.balance_penalty, "translations": rows}
    (out_dir / "scores.json").write_text(json.dumps(summary, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"Reference: {reference['name']} ({reference['sentence_count']:,} sentences)")
    print(f"{'Version':<15} {'Transfer':>9} {'Lexical':>9} {'Fluency':>9} {'Vocab':>9} {'Literal':>9} {'Readable':>9} {'Proxy*':>9}")
    for r in rows:
        print(f"{r['version']:<15} " + " ".join(f"{numeric(r[k]):>9}" for k in
              ("transfer", "consistency", "fluency", "vocabulary", "literalness", "readability", "balance_proxy_NOT_GBI")))
        if (r["source_word_alignment_coverage"] or 0) < 85:
            print(f"  WARNING: source word alignment coverage {numeric(r['source_word_alignment_coverage'])}% "
                  "(literalness likely unreliable)")
    print("* Proxy score is NOT GBI's unpublished final-score formula.")
    print("CSV:", out_dir / "scores.csv")
    if not args.no_chart:
        plot_scores(rows, out_dir / "literal_vs_readable.png")


def reference_command(args):
    rows = []
    for name, (transfer, consistency, fluency, vocab,
               lit_pub, read_pub, final) in PUBLISHED.items():
        lit_calc = round(combined(transfer, consistency), 2)
        read_calc = round(combined(fluency, vocab), 2)
        rows.append({"version": name, "transfer": transfer, "consistency": consistency,
                     "fluency": fluency, "vocabulary": vocab,
                     "literalness": lit_pub, "readability": read_pub,
                     "recalculated_literalness": lit_calc,
                     "recalculated_readability": read_calc,
                     "published_final_UNDERIVED": final,
                     "readability_discrepancy": round(read_calc-read_pub, 2)})
    rows.sort(key=lambda r: r["published_final_UNDERIVED"], reverse=True)
    outdir = Path(args.outdir)
    outdir.mkdir(parents=True, exist_ok=True)
    write_csv(rows, outdir / "published_reference.csv")
    print(f"{'Translation':<12} {'Lit given':>11} {'Read given':>11} {'Read calc':>11} {'Final given':>12}")
    for r in rows:
        print(f"{r['version']:<12} {r['literalness']:>11.2f} {r['readability']:>11.2f} "
              f"{r['recalculated_readability']:>11.2f} {r['published_final_UNDERIVED']:>12.1f}")
        if abs(r['readability_discrepancy']) > .011:
            print(f"  INCONSISTENCY in published report: {r['version']} readability "
                  f"components yield {r['recalculated_readability']:.2f}, "
                  f"not printed {r['readability']:.2f}.")
    print("Final values are copied from the report; the report does NOT specify their formula.")
    if not args.no_chart:
        plot_scores(rows, outdir / "published_scatter.png", benchmark=True)


def make_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="command", required=True)
    b = sub.add_parser("build-reference", help="Create cached syntactic+vocabulary model (ideally from Brown)")
    group = b.add_mutually_exclusive_group(required=True)
    group.add_argument("--brown", action="store_true", help="Use NLTK's full Brown Corpus")
    group.add_argument("--text-file", help="Alternate one-sentence-per-line text reference")
    group.add_argument("--tagged-jsonl", help="Already parsed sentences; useful for testing")
    b.add_argument("--out", required=True, help="Output .json.gz")
    b.add_argument("--model", default="en_core_web_sm", help="English spaCy parser")
    b.add_argument("--max-sentences", type=int, default=0, help="0 for full corpus")
    b.add_argument("--max-n", type=int, default=8, choices=range(1,9), metavar="1..8")
    b.set_defaults(func=build_reference_command)
    a = sub.add_parser("prepare", help="Combine source CoNLL-U + English TSV + alignment TSV into JSONL")
    a.add_argument("--source", required=True, help="Original Hebrew/Greek UD CoNLL-U (one verse per block)")
    a.add_argument("--english", required=True, help="TSV with columns verse, text")
    a.add_argument("--alignments", help="TSV with columns verse, source, target (1-based token IDs)")
    a.add_argument("--out", required=True)
    a.add_argument("--model", default="en_core_web_sm")
    a.set_defaults(func=prepare_command)
    e = sub.add_parser("prepare-english", help="Parse a translation without source/alignments (readability only)")
    e.add_argument("--english", required=True, help="TSV with columns verse, text")
    e.add_argument("--out", required=True)
    e.add_argument("--model", default="en_core_web_sm")
    e.set_defaults(func=prepare_english_command)
    t = sub.add_parser("tokens", help="Inspect English spaCy token IDs before preparing alignments")
    t.add_argument("--english", required=True)
    t.add_argument("--verse", required=True)
    t.add_argument("--model", default="en_core_web_sm")
    t.set_defaults(func=tokens_command)
    s = sub.add_parser("score", help="Score one or more version JSONL datasets")
    s.add_argument("--reference", required=True, help="Reference model output by build-reference")
    s.add_argument("--version", action="append", required=True,
                   help="Repeat for each translation: --version CSB=data/csb.jsonl")
    s.add_argument("--outdir", default="results")
    s.add_argument("--balance-penalty", type=float, default=.25,
                   help="Proxy penalty for literalness/readability imbalance; NOT from report")
    s.add_argument("--no-chart", action="store_true")
    s.set_defaults(func=score_command)
    r = sub.add_parser("reference", help="Recalculate 2:1 combined scores from report's published columns")
    r.add_argument("--outdir", default="published_reference")
    r.add_argument("--no-chart", action="store_true")
    r.set_defaults(func=reference_command)
    return p


def main(argv: list[str] | None = None):
    args = make_parser().parse_args(argv)
    args.func(args)


if __name__ == "__main__":
    main()

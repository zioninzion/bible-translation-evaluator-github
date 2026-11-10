# Bible Translation Quantitative Evaluator

A GitHub-ready **code-only**, independent experimental implementation inspired by Dr. Andi Wu's Bible-translation study. The runnable comparison covers John 1 and Romans 8 using the MACULA Greek treebank, a Brown Corpus language reference, and English translation text supplied **privately by each user**.

**No Bible translation texts, NWT excerpts, MACULA source data, Brown Corpus data, external research PDFs, or preprocessed verse datasets are included.** The interactive explorer includes only numerical scores from a prior experiment (no verse text). Those results are illustrative and not independently validated.

## Research basis and attribution

This project is **inspired by the research framework**, rather than a reproduction of the published numerical results, in:

> **Andi Wu**, *A Quantitative Evaluation of the Christian Standard Bible*. Report for the **Global Bible Initiative (GBI)**, January 2017. [Read the original five-page report on the Christian Standard Bible website](https://csbible.com/wp-content/uploads/2017/01/Quantitative-Translation-Evaluation-by-GBI.pdf).

Wu's report compared **nine English Bible translations**: CSB, ESV, KJV, NASB, NET, NIV, NKJV, NLT, and NRSV. It described a computerized approach using grammatical analyses of the original Hebrew and Greek, English parsing, and word alignment (reverse interlinear mapping). It measured **literalness**, **readability**, and the **balance** between them, using four component metrics:

| Component in Wu's report | Intended measurement | Analogous metric here |
| --- | --- | --- |
| Transfer rate of syntactic relations | How many original-language grammatical relationships are preserved in English | `transfer`: matched *direct* dependency links with matching normalized relation labels and direction |
| Consistency rate of word choices | Degree of one-to-one source-to-English and English-to-source word correspondence | `consistency` / explorer **Lexical**: mutual most-frequent **lemma** mappings among proposed word alignments |
| Syntactic fluency rate | Percentage of syntactic patterns (up to 8-grams) also appearing in a Brown Corpus reference | `fluency`: contiguous 1–8-grams of part-of-speech plus normalized dependency labels found in the locally parsed Brown Corpus |
| Common vocabulary rate | Percentage of translation words appearing in the Brown Corpus | `vocabulary`: percentage of English word **forms** in the locally generated Brown vocabulary |

The report explicitly used **2:1 weighting** for syntactic transfer vs. word-choice consistency to produce a literalness score, and **2:1 weighting** for syntactic fluency vs. common vocabulary to produce a readability score:

```text
Wu-style literalness = (2 × Transfer + Lexical) / 3
Wu-style readability = (2 × Fluency + Vocabulary) / 3
```

It then considered the difference between literalness and readability when reporting its final balance rankings. **The five-page report does not provide a fully specified formula for those published final scores.** This repository does not claim to reproduce that undisclosed calculation or the original GBI software.

### Where this project differs from the original study

| Aspect | Wu / GBI report (2017) | This repository |
| --- | --- | --- |
| Relationship to the research | Original study, with its own evaluation pipeline | **Independent, unvalidated approximation** of the high-level design, not the original source code |
| Source languages and breadth | Hebrew and Greek, whole-Bible evaluation | **Greek New Testament only** with the packaged setup; runnable chapter comparison is **John 1 (51 verses)** and **Romans 8 (39 verses)** |
| Translations | Nine versions: CSB, ESV, KJV, NASB, NET, NIV, NKJV, NLT, NRSV | Up to 12 locally provided versions: ASV, CSB, ESV, KJV, LSV, NASB, NIV, NKJV, NLT, RCV, YLT, NWT; NET and NRSV are not among the configured inputs |
| Original-language syntax | Source syntactic trees and relationships; the public report does not fully specify its parser and conversion details | MACULA **Nestle 1904** Greek constituency/lowfat material projected into **approximate dependency trees** |
| English analysis | Automatic English parser; implementation details not supplied in the report | `spaCy` with `en_core_web_sm`, standardized dependency labels and lemmas |
| Word alignment | Reverse-interlinear links from automatic source-to-English alignment | Heuristic, **unverified** links from MACULA English glosses and a per-passage learned dictionary; no guaranteed word-for-word gold alignment |
| Syntactic transfer | Original study's own relationship-transfer procedure | Exact direct dependency matches on aligned word endpoints with the same normalized relation and direction; unaligned source relations contribute to the denominator |
| Lexical consistency | One-to-one correspondence aggregated across the whole Bible; no sense-level disambiguation | Bidirectional modal **lemma** matches recomputed on each evaluated chapter, making shorter samples liable to inflated scores |
| Fluency and vocabulary | Brown Corpus comparison using the original study's unspecified encoding/tokenization | Brown Corpus re-parsed with spaCy; custom POS/dependency n-grams and word-form overlap; percentages are **not calibrated to the original report** |
| Literalness / readability weights | Fixed **2:1** within each pair of measurements | Scoring script stores 2:1 combined scores; interactive explorer allows adjustable **1:2 to 2:1**, defaulting to **1:1** |
| Final / overall score | Final rankings account for balance between literalness and readability; **exact formula not specified** | Explorer uses an **adjustable weighted average** of the two axes, not Wu's final score. `bible_eval.py` separately calculates `balance_proxy_NOT_GBI = (L + R)/2 − 0.25 × abs(L − R)` by default. **Neither is GBI's published final formula** |
| Supporting data | Proprietary/unpublished implementation details cannot be reconstructed from the report alone | Downloaded MACULA and NLTK Brown data stay local; English translation texts are supplied by the user and excluded from Git |

### Limitations and how to interpret the numbers

1. **This is not a validated replication.** Differences in source trees, tree conversion, English parsing, tokenization, alignment, and formula details can produce large differences from Wu's values. The project's percentages should **never be presented as GBI's scores**.
2. **Transfer is especially alignment-sensitive.** A low `transfer` percentage may mean the current aligner failed to link the words, or that English and Greek express the same relationship using different tree structures. It does **not** establish that the translation omitted that proportion of meaning. Dependency matching also does not measure semantic accuracy.
3. **Alignment coverage is not alignment precision.** The `source_word_alignment_coverage` column is the percentage of source word tokens assigned at least one proposed English link. It says nothing about whether those links are correct. `comparable_source_relation_coverage` separately reports how many dependency edges have aligned endpoints.
4. **Chapter lexical scores are not calibrated to full-Bible lexical scores.** The chapter runner calls `macula_pipeline.py prepare --learn` for **each chapter and translation**, building a small local alignment dictionary; `bible_eval.py` also recalculates modal lemma correspondences on the evaluated sample. A lemma occurring only once or twice can appear perfectly consistent. A fixed-reference dictionary trained on an independent corpus, with held-out verses and coverage reports, is a **recommended future improvement and is not implemented**.
5. **Corpus and sample effects are substantial.** John 1 and Romans 8 are small, stylistically unusual theological passages. Their metric levels and translation rankings may diverge from large-corpus or whole-Bible rankings, even if the program were otherwise unchanged. The interactive explorer's **preloaded, previously calculated whole-Bible-labeled scores** are not regenerated by this chapter-only runner; the precise provenance and sampling of those historical scores should be audited before calling them a complete full-Bible evaluation.
6. **Brown Corpus overlap is not direct human readability.** The Brown Corpus mainly represents American English from the early 1960s; vocabulary membership and syntax-pattern overlap do not test comprehension, naturalness, cultural accessibility, or present-day usage directly. Vocabulary scores can cluster near 100% on short passages.
7. **Word-level consistency is not translation fidelity.** Both Wu's report and this implementation work at the word/lemma level rather than fully resolving word senses and context. Legitimate synonyms, idioms, added English function words, Greek polysemy, changes in word order, and differences in source textual editions can affect the proxies without implying errors in translation.
8. **The final rankings are weight-sensitive.** Equal 1:1 sliders do not reproduce Wu's 2:1 axis formulas, and the explorer's overall weighted mean **does not penalize imbalance** the way the original study says its final scores did. Small rank differences are not evidence of objectively superior translations, especially without uncertainty bounds or human validation.
9. **Source editions can differ.** The packaged MACULA Greek data are based on the Nestle 1904 edition. Different English translations may follow different underlying Greek readings in certain passages; comparisons must distinguish textual variants from translators' choices.
10. **Reproducibility requires recording dependencies and sources.** When publishing numerical findings, record Python/spaCy/model versions, MACULA commit, Brown Corpus preparation method, exact passage and version coverage, aligner coverage, all three slider settings, and the input-text editions—without redistributing copyrighted verse text.

**Recommended use:** Explore hypotheses and compare translations **within the same passage and same settings**; review verse-level alignments manually before claiming differences in translation quality. The results are **experimental proxies**, not theological verdicts or independently established measures of accuracy or readability.

---

## Requirements

- macOS or Linux, Git, internet access, Python 3.11 or 3.12 recommended
- English translation TSVs you are authorized to use, kept **locally** in `translations/private/`
- `bash`, `python3`

## Setup

```bash
git clone YOUR_REPO_URL
cd bible-translation-evaluator-github
# Supply your own authorized English translations first; see format below.
bash setup_and_compare.sh
```

On macOS with Homebrew Python 3.11, you may use:

```bash
PYTHON_BIN="$(brew --prefix python@3.11)/bin/python3.11" bash setup_and_compare.sh
```

The bootstrap creates `.venv`, installs spaCy, downloads the Brown Corpus and MACULA Greek treebank, and computes chapter scores. These third-party datasets have their **own terms and licenses**; they are downloaded by users and excluded from the repository.

## Private input format

Create a separate file for each translation you are permitted to analyze:

`translations/private/ESV.tsv`, `translations/private/RCV.tsv`, etc.

Files must be UTF-8, tab-separated, with the exact header:

```text
verse<TAB>text
```

The first column uses canonical keys `JHN.1.1` through `JHN.1.51` and `ROM.8.1` through `ROM.8.39`. The second column contains your locally supplied verse text. `<TAB>` above means an actual tab character. **Do not commit these TSV files.**

Supported translation identifiers: `ASV`, `CSB`, `ESV`, `KJV`, `LSV`, `NASB`, `NIV`, `NKJV`, `NLT`, `RCV`, `YLT`, `NWT`.

The script **skips translations without local TSV files**. For each supplied translation, it requires all verses in the selected chapter unless `--skip-missing` is explicitly set.

If you have only NWT John 1 and Romans 8, put those 90 verses in `translations/private/NWT.tsv` with the same format.

## Running individual chapters

```bash
source .venv/bin/activate
python compare_chapters.py --passage john1
python compare_chapters.py --passage romans8
python compare_chapters.py --passage both
```

Outputs:

- `results/john1/john1_scores_for_explorer.csv`
- `results/romans8/romans8_scores_for_explorer.csv`

Open `explorer.html`, select the passage, and import the corresponding results CSV to replace the illustrative embedded scores. The browser works offline. It has three weighting controls (all default to 1:1), sortable score columns, translation toggles, and exports.

The whole-Bible scores in the explorer are **historical numerical results**; this chapter-only runner does not generate a new whole-Bible evaluation.

## How it works

- `macula_pipeline.py` downloads/parses MACULA and automatically aligns source-language tokens with English tokens.
- `bible_eval.py` calculates experimental syntactic transfer, lexical consistency, fluency and vocabulary familiarity proxies.
- `compare_chapters.py` filters the same passage for each locally supplied translation, prepares it, runs the evaluation, and produces explorer-ready CSV files.
- `explorer.html` visualizes numeric results and applies interactive weighting.

**Methodological limitation:** The current lexical-consistency metric is learned independently on each evaluated chapter. Therefore chapter scores are not directly calibrated against the whole-Bible scores. A fixed-reference training/evaluation split is a proposed improvement, **not yet implemented in this package**. Alignment coverage is not alignment accuracy.

## Publication and privacy

- `.gitignore` excludes translation TSVs, downloaded corpora, raw/prepared verse datasets, and evaluation outputs.
- Do **not** force-add files from `translations/private/`, `data/`, `cache/`, or `results/`.
- Git ignore rules do not remove files already committed to Git history. Before pushing, inspect `git status` and `git ls-files`.
- This project includes no `LICENSE` because the original authorship/licensing of all inherited code has not been established. Review rights to the scripts and choose a license only when authorized.
- If you publish the explorer, its embedded **numerical** historical results are not Bible verse texts; remove them too if you prefer a blank public demonstration.

## Checks

```bash
python -m unittest discover -s tests
bash -n setup_and_compare.sh
```

These checks do not substitute for an end-to-end run against downloaded corpora and your private translations.

#!/usr/bin/env python3
"""Recalculate 12-translation metrics for John 1 or Romans 8 from supplied text.

Uses original bible_eval.py and macula_pipeline.py. No whole-Bible score reuse.
"""
import argparse
import csv
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
VERSIONS = ("ASV","CSB","ESV","KJV","LSV","NASB","NIV","NKJV","NLT","RCV","YLT","NWT")
PASSAGES = {"john1":("JHN",1,51), "romans8":("ROM",8,39)}
def read_translation(version):
    """Load a user-provided local TSV, never committed to the repository."""
    path = ROOT / "translations" / "private" / f"{version}.tsv"
    if not path.exists():
        return None
    data = {}
    with path.open(encoding="utf-8-sig", newline="") as fh:
        reader = csv.DictReader(fh, delimiter="\t")
        if not {"verse", "text"} <= set(reader.fieldnames or []):
            raise ValueError(f"{path}: expected TSV columns verse and text")
        for row in reader:
            key = row["verse"].strip()
            if not key or key in data:
                raise ValueError(f"{path}: blank or duplicate verse: {key}")
            data[key] = row["text"]
    return data

def run(*args):
    print("+",sys.executable,*args,flush=True)
    subprocess.run([sys.executable,*map(str,args)],cwd=ROOT,check=True)

def main():
    ap=argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--passage",choices=(*PASSAGES,"both"),default="both")
    ap.add_argument("--skip-missing",action="store_true",
                    help="Allow incomplete translations; default requires every supplied version to cover the selected passage")
    args=ap.parse_args()
    db=ROOT/"data/macula_source.sqlite"
    ref=ROOT/"cache/brown.json.gz"
    if not db.is_file() or not ref.is_file():
        sys.exit("Missing source database or Brown reference. First run: bash setup_and_compare.sh")
    for passage in (PASSAGES if args.passage=="both" else (args.passage,)):
        book,chapter,last=PASSAGES[passage]
        ids=[f"{book}.{chapter}.{v}" for v in range(1,last+1)]
        out=ROOT/"results"/passage
        out.mkdir(parents=True,exist_ok=True)
        version_args=[]
        available=0
        for name in VERSIONS:
            data=read_translation(name)
            if data is None:
                print(f"SKIP {name}: translations/private/{name}.tsv not supplied", flush=True)
                continue
            missing=[k for k in ids if k not in data or not data[k].strip()]
            if missing and not args.skip_missing:
                raise ValueError(f"{name} {passage}: missing {len(missing)} verses: {', '.join(missing[:10])}. Use --skip-missing only if intentional.")
            selected=[(k,data[k]) for k in ids if k in data and data[k].strip()]
            if not selected:
                print(f"SKIP {name}: no verses")
                continue
            tsv=out/f"{name}.tsv"
            with tsv.open("w",newline="",encoding="utf-8") as fh:
                w=csv.writer(fh,delimiter="\t",lineterminator="\n")
                w.writerow(["verse", "text"])
                w.writerows(selected)
            prep=out/f"{name}.jsonl"
            run("macula_pipeline.py","prepare","--source-db",db,
                "--english",tsv,"--out",prep,"--learn")
            version_args.extend(("--version",f"{name}={prep}"))
            available += 1
            print(f"{passage}: {name} {len(selected)}/{last} English verses",flush=True)
        if not available:
            raise SystemExit("No local translation TSVs found. Add files under translations/private/.")
        run("bible_eval.py","score","--reference",ref,*version_args,
            "--outdir",out,"--no-chart")
        scorefile=out/"scores.csv"
        export=out/f"{passage}_scores_for_explorer.csv"
        with scorefile.open(encoding="utf-8",newline="") as f,export.open("w",encoding="utf-8",newline="") as g:
            rows=csv.DictReader(f)
            columns=["version","transfer","lexical","fluency","vocabulary","align_coverage"]
            w=csv.DictWriter(g,fieldnames=columns)
            w.writeheader()
            for r in rows:
                w.writerow(dict(version=r["version"],transfer=r["transfer"],
                                lexical=r["consistency"],fluency=r["fluency"],
                                vocabulary=r["vocabulary"],
                                align_coverage=r.get("source_word_alignment_coverage","")))
        print(f"\nREADY: {export}\n",flush=True)

if __name__=="__main__":main()

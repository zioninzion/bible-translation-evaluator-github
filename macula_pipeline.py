#!/usr/bin/env python3
"""MACULA Hebrew/Greek -> verse-aligned Bible evaluation data.

Independent reproducible approximations. The constituent->dependency projection
and gloss-based English word alignment are NOT MACULA's native dependency tree
nor the unpublished original GBI alignment algorithm.

Commands:
    fetch       shallow sparse-clone MACULA into the local machine
    extract     convert XML constituency trees to a searchable SQLite source DB
    prepare     parse English TSV and automatically align source glosses to English
    inspect     inspect original-language tokens for a single verse
    audit       report coverage of source vs English verse sets

Use python macula_pipeline.py --help for details.
"""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
import csv
import hashlib
import json
from pathlib import Path
import re
import sqlite3
import subprocess
import sys
import unicodedata
import xml.etree.ElementTree as ET

from bible_eval import from_spacy_doc, get_nlp, is_word, read_jsonl, load_verse_texts

BOOK_ORDER = 'GEN EXO LEV NUM DEU JOS JDG RUT 1SA 2SA 1KI 2KI 1CH 2CH EZR NEH EST JOB PSA PRO ECC SNG ISA JER LAM EZK DAN HOS JOL AMO OBA JON MIC NAM HAB ZEP HAG ZEC MAL MAT MRK LUK JHN ACT ROM 1CO 2CO GAL EPH PHP COL 1TH 2TH 1TI 2TI TIT PHM HEB JAS 1PE 2PE 1JN 2JN 3JN JUD REV'.split()
BOOK_INDEX = {v:i for i,v in enumerate(BOOK_ORDER)}
BOOK_ALIASES = {"PS": "PSA", "PSM": "PSA", "SON": "SNG", "SOS": "SNG", "CANT": "SNG", "JONAH": "JON", "NAH": "NAM", "OBD": "OBA", "JDG": "JDG", "PHIL": "PHP", "JHN": "JHN", "JOH": "JHN", "MAR": "MRK", "1KI": "1KI", "2KI": "2KI", "1SAM": "1SA", "2SAM": "2SA", "1KGS": "1KI", "2KGS": "2KI", "1CHR": "1CH", "2CHR": "2CH"}
REF_RE = re.compile(r'^\s*([\w\d]+)\s+(\d+):(\d+)(?:!\d+)?\s*$', re.I)
LETTER_WORD = re.compile(r"[^\W\d_]+(?:['’][^\W\d_]+)?", re.UNICODE)
# Bible versions use the canonical English chapter numbering. These exceptions
# have NON-IDENTICAL Hebrew/English versification and must be audited separately.
KNOWN_VERSIFICATION_WARNINGS = "WLC Psalm superscriptions, some Psalm headings, Joel, Malachi, and other Hebrew-English chapter/verse differences require a mapping, not simple ID equality."


def verse_id(raw: str) -> str | None:
    match = REF_RE.match(raw or '')
    if not match:
        return None
    book, chapter, verse = match.groups()
    book = BOOK_ALIASES.get(book.upper(), book.upper())
    return f"{book}.{int(chapter)}.{int(verse)}"


def localtag(elem):
    return elem.tag.split('}', 1)[-1]


def book_sort(vid: str):
    b, c, v = vid.split('.')
    return (BOOK_INDEX.get(b, 999), int(c), int(v))


def upos_of(elem):
    cl = (elem.get('class') or elem.get('pos') or '').lower()
    return {
        'noun':'NOUN','proper':'PROPN','verb':'VERB','adj':'ADJ','adv':'ADV',
        'pron':'PRON','det':'DET','art':'DET','prep':'ADP','conj':'CCONJ',
        'cj':'CCONJ','particle':'PART','ptcl':'PART','om':'PART','num':'NUM',
        'quant':'DET','intj':'INTJ','interj':'INTJ','rel':'PRON',
    }.get(cl, 'X')


def phrase_head_pref(group, child):
    role = (child.get('role') or '').lower()
    cls = (child.get('class') or child.get('pos') or '').lower()
    grp = (group.get('class') or '').lower()
    # Prefer explicit predicate/verb roles over MACULA constituent 'head=true':
    # the latter marks phrase structure, not necessarily a UD dependency head.
    if grp in ('cl','clause'):
        return (100 if role == 'v' else 90 if role in ('p','pred') else 75 if cls == 'verb' else 50 if role == 's' else 0)
    if grp in ('pp','prep'):
        return (100 if cls in ('noun','pron','adj','np','det') or role in ('o','obj') else 0 if cls == 'prep' else 30)
    if grp in ('np','noun','adjp','num','quant'):
        return (100 if cls in ('noun','proper','pron','np') else 80 if cls in ('adj','adjp') else 5 if cls in ('art','det','prep','conj','cj','om') else 15)
    if grp in ('vp','verb'):
        return (100 if cls == 'verb' or role == 'v' else 15)
    return (95 if role == 'v' else 75 if cls in ('verb','noun','proper') else 20)


def dependency_role(group, child):
    role = (child.get('role') or '').lower()
    cls = (child.get('class') or child.get('pos') or '').lower()
    parentclass = (group.get('class') or '').lower()
    by_role = {'s':'nsubj','subj':'nsubj','o':'obj','o2':'obj','io':'iobj',
               'adv':'obl','pp':'obl','p':'xcomp','pred':'xcomp','aux':'aux',
               'rel':'acl','voc':'vocative','mod':'amod', 'appos':'appos'}
    if role in by_role:
        return by_role[role]
    if cls in ('conj','cj','conjunction'):
        return 'cc'
    if cls in ('det','art','article'):
        return 'det'
    if cls in ('prep','preposition'):
        return 'case'
    if cls in ('om','particle','ptcl'):
        return 'mark'
    if cls in ('adj','adjp') and parentclass == 'np':
        return 'amod'
    if (child.get('type') or '').lower() == 'apposition':
        return 'appos'
    if parentclass in ('np','noun'):
        return 'nmod'
    if parentclass in ('cl','clause'):
        return 'parataxis' if cls in ('cl','clause') else 'obl'
    return 'dep'


def parse_sentence(elem) -> dict[str, list[dict]]:
    """Flatten one MACULA sentence, keeping each morpheme (Hebrew) separately.

    Span-overlapping sentences are split per verse, dropping edges crossing
    verse boundaries. All token IDs are stable per-verse 1-based positions.
    """
    nodes: list[tuple[ET.Element,str]] = []
    raw_edges: list[tuple[str,str,str]] = []
    def walk(e):
        tag = localtag(e)
        if tag == 'w':
            ref = verse_id(e.get('ref',''))
            if not ref:
                return None
            key = e.get('{http://www.w3.org/XML/1998/namespace}id') or e.get('id')
            if not key:
                key = f'auto-{len(nodes)}'
            nodes.append((e,key))
            return key
        if tag not in ('wg','sentence'):
            return None
        children = [x for x in e if localtag(x) in ('wg','w')]
        if not children:
            return None
        heads = [(x, walk(x)) for x in children]
        heads = [(x,h) for x,h in heads if h is not None]
        if not heads:
            return None
        # Choose the syntactic head of THIS group using explicit grammatical
        # function and word class. Constituent head flags alone can mislead.
        chosen = max(range(len(heads)), key=lambda i: (phrase_head_pref(e,heads[i][0]),
                     1 if heads[i][0].get('head')=='true' else 0, -i))
        head = heads[chosen][1]
        for i, (child,dep) in enumerate(heads):
            if i == chosen or dep == head:
                continue
            raw_edges.append((head,dep,dependency_role(e,child)))
        return head
    top = [x for x in elem if localtag(x)=='wg']
    for grp in top:
        walk(grp)
    # Sort by numeric original lexical IDs, NOT the order of MACULA groups,
    # which can reorder discontinuous constituents.
    def keyfunc(item):
        e,k = item
        ref = e.get('ref','')
        loc = re.search(r'!(\d+)',ref)
        base = int(loc.group(1)) if loc else 999999
        suffix = re.search(r'(\d+)$',k)
        return (book_sort(verse_id(ref)), base, int(suffix.group(1)) if suffix else 0)
    nodes.sort(key=keyfunc)
    by_verse: dict[str, list[dict]] = defaultdict(list)
    by_key: dict[str, tuple[str,int]] = {}
    for e,key in nodes:
        vid = verse_id(e.get('ref',''))
        idx = len(by_verse[vid]) + 1
        by_key[key] = (vid,idx)
        by_verse[vid].append({
            'id':idx,'form':(e.text or e.get('unicode') or '').strip(),
            'lemma':e.get('lemma') or e.get('stronglemma') or (e.text or '').strip(),
            'upos':upos_of(e),'head':0,'deprel':'root',
            'gloss':e.get('english') or e.get('gloss') or '',
            'reference':e.get('ref') or '', 'xml_id':key,
            'macula_role':e.get('role') or '',
            'strong':e.get('strongnumberx') or e.get('strong') or '',
            'lang':e.get('lang') or '',
        })
    for key_h, key_d, label in raw_edges:
        if key_h not in by_key or key_d not in by_key:
            continue
        hv,hi = by_key[key_h]; dv,di = by_key[key_d]
        if hv != dv:
            continue  # boundaries can span source-syntax constituents
        token = by_verse[dv][di-1]
        if token['head'] == 0 and token['deprel'] == 'root':
            token['head'] = hi
            token['deprel'] = label
    return dict(by_verse)


def xml_files(base: Path):
    """Accept either repository roots or the actual /lowfat directory."""
    candidates = (base, base/'WLC'/'lowfat', base/'Nestle1904'/'lowfat')
    for candidate in candidates:
        if candidate.exists() and list(candidate.glob('*.xml')):
            return sorted(candidate.glob('*.xml'))
    raise FileNotFoundError(f'No MACULA lowfat XML files under {base}; expected WLC/lowfat or Nestle1904/lowfat')


def read_macula_file(file: Path):
    # Iterparse keeps even a huge Hebrew chapter memory-bounded. A Greek book
    # can include many sentences, but we clear the consumed sentence elements.
    current = None
    root = None
    for event, elem in ET.iterparse(file, events=('start','end')):
        if root is None:
            root = elem
        if event == 'end' and localtag(elem)=='sentence':
            yield parse_sentence(elem)
            elem.clear()


def fetch_command(args):
    dest = Path(args.dest)
    dest.mkdir(parents=True,exist_ok=True)
    for name,folders in [('macula-hebrew','WLC/lowfat'),('macula-greek','Nestle1904/lowfat')]:
        if (name == 'macula-hebrew' and args.nt_only) or (name == 'macula-greek' and args.ot_only):
            continue
        target = dest/name
        url = f'https://github.com/Clear-Bible/{name}.git'
        if not (target/'.git').exists():
            subprocess.run(['git','clone','--depth','1','--filter=blob:none','--sparse',url,str(target)],check=True)
        subprocess.run(['git','-C',str(target),'sparse-checkout','set',folders],check=True)
        revision = subprocess.check_output(['git','-C',str(target),'rev-parse','HEAD'],text=True).strip()
        print(name, 'revision',revision)
        paths = xml_files(target)
        print(f'  {len(paths)} XML files; {sum(f.stat().st_size for f in paths)/1024/1024:.1f} MiB')


def make_db(path: Path):
    path.parent.mkdir(parents=True,exist_ok=True)
    conn = sqlite3.connect(path)
    conn.execute('CREATE TABLE IF NOT EXISTS verses (verse TEXT PRIMARY KEY, source_json TEXT NOT NULL, testament TEXT NOT NULL)')
    conn.execute('CREATE TABLE IF NOT EXISTS metadata (name TEXT PRIMARY KEY, value TEXT NOT NULL)')
    return conn


def merge_tokens(prior: list[dict], additional: list[dict]) -> list[dict]:
    # Sentences may span, repeat, or cut across verse boundaries. Re-index
    # merged spans, deduplicating exact MACULA xml IDs.
    unique = {t['xml_id']:t for t in prior}
    for t in additional:
        if t['xml_id'] not in unique:
            unique[t['xml_id']]=t
    tokens = list(unique.values())
    tokens.sort(key=lambda t: (int(re.search(r'!(\d+)',t['reference']).group(1)) if re.search(r'!(\d+)',t['reference']) else 99999,
                               int(re.search(r'(\d+)$',t['xml_id']).group(1)) if re.search(r'(\d+)$',t['xml_id']) else 0))
    old = {t['xml_id']:i+1 for i,t in enumerate(tokens)}
    # preserve raw head via xml_id for proper recombination if source has 2 sentences
    # in same verse. Old numeric ids are LOCAL to each sentence's verse.
    head_key_by_old = {}
    for group in (prior,additional):
        id_to_key={t['id']:t['xml_id'] for t in group}
        for t in group:
            if t['head']:
                head_key_by_old[t['xml_id']]=id_to_key.get(t['head'])
    result=[]
    for idx,t in enumerate(tokens,1):
        x=dict(t);x['id']=idx
        k=head_key_by_old.get(x['xml_id'])
        x['head']=old.get(k,0)
        if x['head']==0:x['deprel']='root'
        result.append(x)
    return result


def extract_command(args):
    conn=make_db(Path(args.out))
    conn.execute('DELETE FROM verses')
    conn.execute('DELETE FROM metadata')
    count_sentences=0
    for lang,base in [('OT',args.hebrew),('NT',args.greek)]:
        if not base:
            continue
        files=xml_files(Path(base))
        total=0
        print(f'Reading {lang}: {len(files)} MACULA XML files',flush=True)
        for f_i,file in enumerate(files,1):
            for group in read_macula_file(file):
                count_sentences+=1
                for v, toks in group.items():
                    if not toks:
                        continue
                    was=conn.execute('SELECT source_json FROM verses WHERE verse=?',(v,)).fetchone()
                    if was:
                        toks=merge_tokens(json.loads(was[0]),toks)
                    conn.execute('INSERT OR REPLACE INTO verses (verse,source_json,testament) VALUES (?,?,?)',
                                 (v,json.dumps(toks,ensure_ascii=False,separators=(',',':')),lang))
            conn.commit()
            if f_i%100==0: print(f'  {lang}: {f_i}/{len(files)} files',flush=True)
        total=conn.execute('SELECT count(*) FROM verses WHERE testament=?',(lang,)).fetchone()[0]
        print(f'  {lang}: {total:,} verse IDs')
    conn.execute('INSERT INTO metadata VALUES (?,?)',('provenance','MACULA Hebrew/Gk, Clear-Bible / Biblica, CC BY 4.0; source syntactic projection is approximate'))
    conn.execute('INSERT INTO metadata VALUES (?,?)',('sentences_processed',str(count_sentences)))
    conn.execute('INSERT INTO metadata VALUES (?,?)',('versification_warning',KNOWN_VERSIFICATION_WARNINGS))
    for lang, base in [('OT',args.hebrew),('NT',args.greek)]:
        if base:
            try:
                revision=subprocess.check_output(['git','-C',str(base),'rev-parse','HEAD'],text=True,stderr=subprocess.DEVNULL).strip()
                conn.execute('INSERT INTO metadata VALUES (?,?)',(lang+'_macula_commit',revision))
            except (subprocess.CalledProcessError,FileNotFoundError):
                pass
    conn.commit();conn.close()
    print(f'Source database ready: {args.out}')


def gloss_terms(s: str):
    # Conservative normalization: distinguish surface words from a translated
    # gloss phrase (e.g. Greek gloss "of Christ" -> {of,christ}).
    # Parenthesized morph hints are not evidence of translated English words.
    s=re.sub(r'\([^)]*\)',' ',s)
    s=re.sub(r'\[[^]]*\]',' ',s)
    out=set()
    for word in LETTER_WORD.findall(s.casefold()):
        out.add(word)
        if word.endswith('s') and len(word)>4: out.add(word[:-1])
        if word.endswith('ing') and len(word)>5:out.add(word[:-3])
        if word.endswith('ed') and len(word)>4:out.add(word[:-2])
    return out


ARCHAIC = {'thou':'you','thee':'you','ye':'you','thy':'your','thine':'your',
           'hath':'have','doth':'do','shalt':'shall','wilt':'will','art':'be',
           'wast':'be','were':'be','unto':'to','wherefore':'why',
           'saith':'say','spake':'speak','hast':'have','dost':'do'}


def canonical_english(w: str):
    w=(w or '').casefold().strip('.,;:!?)(')
    return ARCHAIC.get(w,w)


def alignment_scores(source: list[dict], target: list[dict], dictionary=None):
    """Produce sparse, evidence-based alignment candidates with scores.

    No positional alignment is invented for glossless/unmatched words. Only a
    glossary or a learned high-confidence lemma pair creates an alignment.
    """
    dictionary=dictionary or {}
    real_tgt = [t for t in target if is_word(t)]
    candidates=[]
    for src in source:
        if not is_word(src): continue
        hints=gloss_terms(src.get('gloss',''))
        src_lemma=src['lemma'].casefold()
        learned=dictionary.get(src_lemma, {})
        for dst in real_tgt:
            form=canonical_english(dst['form'])
            lemma=canonical_english(dst.get('lemma',''))
            evidence=0
            if form in hints or lemma in hints: evidence=5.0
            if form in learned or lemma in learned:
                evidence=max(evidence, min(5.2,2.0+3.0*learned.get(form,learned.get(lemma,0))))
            if evidence<=0:continue
            # Position is tie-break, NOT evidence for an unglossed alignment.
            sp=src['id']/max(1,len(source));tp=dst['id']/max(1,len(target))
            score=evidence - .12*abs(sp-tp)
            candidates.append((src['id'],dst['id'],score))
    return candidates


def choose_pairs(source,target,dictionary=None):
    candidates=alignment_scores(source,target,dictionary)
    by_src=defaultdict(list);by_dst=defaultdict(list)
    for s,t,w in candidates:
        by_src[s].append((t,w)); by_dst[t].append((s,w))
    pairs=set()
    for s,options in by_src.items():
        best=max(v for _,v in options)
        for t,val in options:
            if val >= best-0.015:
                pairs.add((s,t))
    # A source word can align to multiple target tokens (multiword glosses),
    # but each English token should not align to a large number of unrelated
    # source lemmas solely because a common gloss appears repeatedly.
    by_target=defaultdict(list)
    for s,t in pairs:
        sc=next(w for ss,tt,w in candidates if ss==s and tt==t)
        by_target[t].append((s,sc))
    final=set()
    for t,poss in by_target.items():
        top=max(sc for s,sc in poss)
        for s,sc in poss:
            if sc>=top-.015:
                final.add((s,t))
    return [list(pair) for pair in sorted(final)]


def build_dictionary(source_db: Path, prelim: Path, min_occurrences:int=3):
    """Learn per-translation source-lemma -> English-lemma preferred mappings.

    Only exact matching MACULA glosses are training labels, not guessed
    positional pairs. This can improve coverage of recurring KJV word choices.
    """
    mapping=defaultdict(Counter)
    for line in read_jsonl(prelim):
        src=line['source'];dst=line['target']
        sid={x['id']:x for x in src};tid={x['id']:x for x in dst}
        for i,j in choose_pairs(src,dst):
            x=sid[i];y=tid[j]
            mapping[x['lemma'].casefold()][canonical_english(y.get('lemma') or y['form'])]+=1
    result={}
    for lemma,c in mapping.items():
        n=sum(c.values());best=c.most_common(6)
        result[lemma]={k:v/n for k,v in best if v>=min_occurrences and v/n>=.10}
    return result


def parse_english_to_prelim(conn,verses:dict[str,str],model:str,out:Path,max_verses:int):
    nlp=get_nlp(model)
    out.parent.mkdir(parents=True,exist_ok=True)
    verse_list=[(v,t) for v,t in verses.items() if conn.execute('SELECT 1 FROM verses WHERE verse=?',(v,)).fetchone()]
    if max_verses and len(verse_list) > max_verses:
        # A small smoke test must include both OT and NT, not only Genesis.
        # Deterministic evenly spaced sampling across the whole canon.
        positions = [round(i * (len(verse_list)-1)/(max_verses-1)) for i in range(max_verses)] if max_verses > 1 else [len(verse_list)//2]
        verse_list = [verse_list[i] for i in positions]
    # Preserve a strict one-verse paired input; all skipped verses are audited.
    with out.open('w',encoding='utf8') as f:
        for (v,_),doc in zip(verse_list,nlp.pipe((t for _,t in verse_list),batch_size=64)):
            s=json.loads(conn.execute('SELECT source_json FROM verses WHERE verse=?',(v,)).fetchone()[0])
            target=from_spacy_doc(doc)
            obj={'verse':v,'source':s,'target':target,'align':[]}
            f.write(json.dumps(obj,ensure_ascii=False,separators=(',',':'))+'\n')
    return len(verse_list)


def prepare_command(args):
    db=Path(args.source_db)
    conn=sqlite3.connect(db)
    translations=load_verse_texts(Path(args.english))
    out=Path(args.out)
    prelim=out.with_name(out.stem+'.preliminary.jsonl')
    n=parse_english_to_prelim(conn,translations,args.model,prelim,args.max_verses)
    if not n:raise SystemExit('No matching verses in MACULA source database and English TSV')
    dictionary=build_dictionary(db,prelim,args.min_count) if args.learn else {}
    count_pair=0;total_src=0;aligned_src=0
    out.parent.mkdir(parents=True,exist_ok=True)
    with out.open('w',encoding='utf8') as f:
        for doc in read_jsonl(prelim):
            links=choose_pairs(doc['source'],doc['target'],dictionary)
            doc['align']=links
            count_pair+=len(links)
            total_src+=sum(is_word(t) for t in doc['source'])
            aligned_src+=len({s for s,t in links})
            f.write(json.dumps(doc,ensure_ascii=False,separators=(',',':'))+'\n')
    if not args.keep_preliminary:prelim.unlink()
    conn.close()
    print(f'Prepared {n:,} verse pairs: {out}')
    print(f'Alignment candidate pairs {count_pair:,}; source word coverage {100*aligned_src/max(1,total_src):.1f}%')
    print('IMPORTANT: these are automatic gloss/lexicon alignments, not verified reverse interlinear alignments.')


def inspect_command(args):
    conn=sqlite3.connect(args.source_db)
    row=conn.execute('SELECT source_json FROM verses WHERE verse=?',(args.verse,)).fetchone()
    if not row:raise SystemExit(f'No MACULA verse: {args.verse}')
    tokens=json.loads(row[0])
    for x in tokens:
        print(f"{x['id']:>3} {x['form']:<18} {x['upos']:<6} head={x['head']:<3} {x['deprel']:<12} gloss={x['gloss']!r} ref={x['reference']}")


def audit_command(args):
    conn=sqlite3.connect(args.source_db)
    src={r[0] for r in conn.execute('SELECT verse FROM verses')}
    print('MACULA source verse IDs:',len(src))
    for path in args.english:
        eng=set(load_verse_texts(Path(path)))
        common=src&eng
        print(f'{path}: source matched {len(common):,}; English only {len(eng-src):,}; MACULA only {len(src-eng):,}')
        print('  Sample English-only:', ', '.join(sorted(eng-src,key=book_sort)[:14]))
        print('  Sample MACULA-only:', ', '.join(sorted(src-eng,key=book_sort)[:14]))
    print('NOTICE:',KNOWN_VERSIFICATION_WARNINGS)
    conn.close()


def main(argv=None):
    p=argparse.ArgumentParser(description=__doc__,formatter_class=argparse.RawDescriptionHelpFormatter)
    sub=p.add_subparsers(dest='command',required=True)
    f=sub.add_parser('fetch',help='Download MACULA sources using git sparse checkout (internet needed)')
    f.add_argument('--dest',default='data/sources')
    g=f.add_mutually_exclusive_group();g.add_argument('--ot-only',action='store_true');g.add_argument('--nt-only',action='store_true')
    f.set_defaults(func=fetch_command)
    e=sub.add_parser('extract',help='Convert both lowfat XML treebanks into indexed SQLite corpus')
    e.add_argument('--hebrew',default='data/sources/macula-hebrew')
    e.add_argument('--greek',default='data/sources/macula-greek')
    e.add_argument('--out',default='data/macula_source.sqlite')
    e.set_defaults(func=extract_command)
    a=sub.add_parser('prepare',help='Parse English verses and create automatic source/target alignments')
    a.add_argument('--source-db',default='data/macula_source.sqlite')
    a.add_argument('--english',required=True)
    a.add_argument('--out',required=True)
    a.add_argument('--model',default='en_core_web_sm')
    a.add_argument('--max-verses',type=int,default=0)
    a.add_argument('--min-count',type=int,default=3)
    a.add_argument('--learn',action='store_true',help='Learn additional alignments from high-confidence gloss examples')
    a.add_argument('--keep-preliminary',action='store_true')
    a.set_defaults(func=prepare_command)
    x=sub.add_parser('inspect',help='Show original MACULA words, head projections, glosses')
    x.add_argument('--source-db',default='data/macula_source.sqlite');x.add_argument('--verse',required=True)
    x.set_defaults(func=inspect_command)
    au=sub.add_parser('audit',help='Compare verse coverage with translation TSVs')
    au.add_argument('--source-db',default='data/macula_source.sqlite');au.add_argument('--english',action='append',required=True)
    au.set_defaults(func=audit_command)
    args=p.parse_args(argv)
    if args.command=='extract':
        args.hebrew=args.hebrew if Path(args.hebrew).exists() else None
        args.greek=args.greek if Path(args.greek).exists() else None
        if not args.hebrew and not args.greek:raise SystemExit('No MACULA repositories found; run fetch first')
    args.func(args)

if __name__=='__main__':main()

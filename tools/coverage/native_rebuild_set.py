#!/usr/bin/env python3
"""Keep generation choices while rebuilding a public set on another platform.

The input is regenerated from committed recipes, not native XML: native XML
can omit an explicitly supplied default such as UserVisible=true. Both output
rounds, and the saved fixture CF, are produced by the target native oracle.
"""
import argparse
import base64
import datetime
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parent))
from combine import assemble_tree
from native_round_trip import inputs_digest, run_ibcmd, tree_files, first_error

def sha(data):
    return hashlib.sha256(data).hexdigest()

def promote_format_version(data):
    """Change only a platform XML root's XML format attribute."""
    text = data.decode('utf-8-sig')
    root = re.match(r'\s*(?:<\?xml[^>]*\?>\s*)?(<(?:\w+:)?(?:MetaDataObject|Form|GraphicalSchema|Rights|AdditionalIndexes)\b[^>]*>)', text)
    if root is None:
        return data
    header = root.group(1)
    promoted = re.sub(r'\bversion="2\.20"', 'version="2.21"', header)
    if promoted == header:
        return data
    changed = text[:root.start(1)] + promoted + text[root.end(1):]
    return (b'\xef\xbb\xbf' if data.startswith(b'\xef\xbb\xbf') else b'') + changed.encode('utf-8')

def native(args, executable, work, label, *command):
    work.mkdir(parents=True, exist_ok=True)
    ok, output = run_ibcmd(args, str(executable), work, *map(str, command))
    (work / (label + '.log')).write_text(output, encoding='utf-8')
    if not ok:
        raise RuntimeError(first_error(output, [args.work]))

def validate_source(manifest):
    if manifest.get('evidence', {}).get('status') != 'platform_proven':
        raise ValueError('requires a public native-proven source set')
    if not manifest.get('variants') or any(v.get('status') != 'platform_proven' for v in manifest['variants']):
        raise ValueError('isolate accepted recipes before rebuilding a source with refused variants')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ['source', 'set', 'work', 'producer', 'ibcmd']:
        parser.add_argument('--' + name, type=Path, required=True)
    for name in ['producer-platform', 'platform', 'xml-version']:
        parser.add_argument('--' + name, required=True)
    args = parser.parse_args()
    if args.work.exists() or args.set.exists():
        raise SystemExit('use new work and set directories')
    if not args.producer_platform.startswith('8.5.'):
        raise SystemExit('this recipe measures native 8.5 storage generation')
    source = json.loads((args.source / 'manifest.json').read_text(encoding='utf-8'))
    validate_source(source)
    args.work.mkdir(parents=True)
    generation = args.work / 'generation'
    generation.mkdir()
    if source.get('kind', 'configuration') == 'combined':
        selected = []
        coverage_root = args.source.parent.parent.resolve()
        for dependency in source['sources']:
            directory = (args.source.parent / dependency['path']).resolve()
            if not directory.is_relative_to(coverage_root):
                raise ValueError('combined source escapes its public corpus')
            data = (directory / 'manifest.json').read_bytes()
            if sha(data) != dependency['manifest_sha256']:
                raise ValueError('combined source is stale')
            selected.append((directory, json.loads(data)))
        context = SimpleNamespace(root=args.source.parent, include_root=[], xml_version=source['evidence']['source_version'])
        tree, _, _, _ = assemble_tree(context, generation, selected_sets=selected)
    elif source.get('kind', 'configuration') == 'configuration':
        tree = generation / 'tree'
        subprocess.run([sys.executable, '-I', str(Path(__file__).with_name('generate.py')), str(args.source), str(tree)], check=True)
    else:
        raise ValueError('requires an XML-generatable class or combined source')
    promoted = 0
    for path in tree.rglob('*.xml'):
        original = path.read_bytes()
        changed = promote_format_version(original)
        if changed != original:
            path.write_bytes(changed)
            promoted += 1
    tree_hash = hashlib.sha256()
    for path in sorted(tree.rglob('*')):
        if path.is_file():
            tree_hash.update((path.relative_to(tree).as_posix() + '\0' + sha(path.read_bytes()) + '\n').encode('utf-8'))
    producer = args.work / 'producer'
    oracle = args.work / 'oracle'
    native(args, args.producer, producer, 'create', 'infobase', 'create', '--db-path=' + str(producer / 'ib'),
           '--create-database', '--locale=ru_RU', '--import=' + str(tree), '--apply', '--force')
    native(args, args.producer, producer, 'save', 'infobase', 'config', 'save', '--db-path=' + str(producer / 'ib'), producer / 'storage.cf')
    native(args, args.ibcmd, oracle, 'create-1', 'infobase', 'create', '--db-path=' + str(oracle / 'ib1'),
           '--create-database', '--locale=ru_RU', '--load=' + str(producer / 'storage.cf'), '--apply', '--force')
    native(args, args.ibcmd, oracle, 'export-1', 'infobase', 'config', 'export', '--db-path=' + str(oracle / 'ib1'), oracle / 'round1')
    native(args, args.ibcmd, oracle, 'save', 'infobase', 'config', 'save', '--db-path=' + str(oracle / 'ib1'), oracle / 'native.cf')
    native(args, args.ibcmd, oracle, 'create-2', 'infobase', 'create', '--db-path=' + str(oracle / 'ib2'),
           '--create-database', '--locale=ru_RU', '--load=' + str(oracle / 'native.cf'), '--apply', '--force')
    native(args, args.ibcmd, oracle, 'export-2', 'infobase', 'config', 'export', '--db-path=' + str(oracle / 'ib2'), oracle / 'round2')
    first, second = tree_files(oracle / 'round1'), tree_files(oracle / 'round2')
    if first != second:
        raise ValueError('native target rounds differ outside ConfigDumpInfo.xml')
    publish = args.work / 'publish'
    (publish / 'input').mkdir(parents=True)
    dependency = {'set': source['set'], 'path': Path(os.path.relpath(args.source, args.set.parent)).as_posix(),
                  'manifest_sha256': sha((args.source / 'manifest.json').read_bytes())}
    recipe = {'schema_version': 1, 'method': 'regenerate original input recipes; promote only XML format attributes; native 8.5 import/save; target native load/export/save/load/export',
              'producer_platform': args.producer_platform, 'producer_sha256': sha(args.producer.read_bytes()),
              'promoted_format_headers': promoted, 'generated_input_tree_sha256': tree_hash.hexdigest()}
    (publish / 'input/source.json').write_text(json.dumps(dependency, indent=2) + '\n', encoding='utf-8')
    (publish / 'input/recipe.json').write_text(json.dumps(recipe, indent=2) + '\n', encoding='utf-8')
    shutil.copytree(oracle / 'round2', publish / 'native', ignore=shutil.ignore_patterns('ConfigDumpInfo.xml'))
    raw = (oracle / 'native.cf').read_bytes()
    encoded = base64.encodebytes(raw)
    (publish / 'configuration.cf.b64').write_bytes(encoded)
    variants, claimed = [], set()
    for variant in source['variants']:
        record = dict(variant)
        owned = {item['path'] for item in variant.get('files', [])}
        record['files'] = [{'path': path, 'size': len(data), 'sha256': sha(data)} for path, data in second.items() if path in owned]
        claimed.update(item['path'] for item in record['files'])
        variants.append(record)
    for path in sorted(second.keys() - claimed):
        variants[0]['files'].append({'path': path, 'size': len(second[path]), 'sha256': sha(second[path])})
    manifest = {'schema_version': 1, 'kind': 'native_rebuild',
                'set': args.platform + '-xml-' + args.xml_version + '-' + args.set.name,
                'evidence': {'status': 'platform_proven', 'created_on': datetime.date.today().isoformat(),
                            'platform_version': args.platform, 'source_version': args.xml_version,
                            'ibcmd_sha256': sha(args.ibcmd.read_bytes()), 'database_kind': 'isolated_file_infobase',
                            'storage_producer_platform': args.producer_platform,
                            'storage_producer_ibcmd_sha256': sha(args.producer.read_bytes()),
                            'rounds': 'native producer imports original generated recipes; target native load/export/save/load/export; identical outside ConfigDumpInfo.xml'},
                'inputs_sha256': inputs_digest(publish), 'sources': [dependency],
                'configuration_cf': {'path': 'configuration.cf.b64', 'decoded_size': len(raw), 'decoded_sha256': sha(raw), 'encoded_sha256': sha(encoded)},
                'variants': variants}
    if source.get('collection'):
        manifest['collection'] = source['collection']
    (publish / 'manifest.json').write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    shutil.copytree(publish, args.set)
    print('Native storage rebuilt by ' + args.producer_platform + ' and proven by ' + args.platform)

if __name__ == '__main__':
    main()

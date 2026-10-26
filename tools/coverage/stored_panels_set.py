#!/usr/bin/env python3
"""Prove optional stored form command bars using only a public native CF.

XML does not retain the presence of these internal, generated command bars.
The storage assembler changes their presence flags and clones the same form's
native empty AutoCommandBar. Expected XML always comes from native ibcmd.
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
import xml.etree.ElementTree as ET

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from native_round_trip import inputs_digest, run_ibcmd, tree_files, first_error

PANELS = ((4, -2, 'CovUpperCommandBar'), (5, -3, 'CovLowerCommandBar'), (11, -4, 'CovFABCommandBar'))

def sha(data):
    return hashlib.sha256(data).hexdigest()

def tuple_nodes(text):
    """Ranges and immediate members; quoted braces/commas are opaque."""
    nodes, stack = [], []
    quoted, i = False, 0
    while i < len(text):
        char = text[i]
        if char == '"':
            if quoted and i + 1 < len(text) and text[i + 1] == '"':
                i += 2
                continue
            quoted = not quoted
        elif not quoted:
            if char == '{':
                node = {'start': i, 'last': i + 1, 'fields': []}
                nodes.append(node)
                stack.append(node)
            elif char in ',}' and stack:
                node = stack[-1]
                node['fields'].append(text[node['last']:i].strip())
                node['last'] = i + 1
                if char == '}':
                    node['end'] = i + 1
                    stack.pop()
        i += 1
    if stack or quoted:
        raise ValueError('unterminated stored tuple/string')
    return nodes

def add_panels(plain, command_bar_name, donor=None):
    nodes = tuple_nodes(plain)
    if len(nodes) < 2 or nodes[0]['fields'][0] != '4':
        raise ValueError('requires the measured form-body container revision 4')
    root = nodes[1]
    fields = list(root['fields'])
    if fields[0] != '59' or len(fields) < 15 or not fields[-14].startswith('{59,'):
        raise ValueError('requires a measured revision-59 root without optional command bars')
    bars = [node for node in nodes if '"' + command_bar_name + '"' in node['fields']]
    if len(bars) != 1 or bars[0]['fields'][0] != '22':
        raise ValueError('requires exactly one native AutoCommandBar record')
    original = list(donor) if donor is not None else bars[0]['fields']
    donor_name = next((field for field in original if field.startswith('"')), None)
    if len(original) < 7 or original[5] != '9' or donor_name != original[6]:
        raise ValueError('unmeasured command-bar header')
    if not re.fullmatch(r'\{-1,\s*02023637-7868-4a5f-8576-835a76e0c9ba\}', original[1]):
        raise ValueError('unexpected native command-bar identity')
    start = len(fields) - 14
    for slot, identifier, name in reversed(PANELS):
        if fields[start + slot] != '0':
            raise ValueError('source already carries an optional stored panel')
        bar = list(original)
        bar[1] = '{' + str(identifier) + ',' + original[1].strip('{}').split(',', 1)[1] + '}'
        bar[2] = '1'  # generated item; never an ordinary XML ChildItems member
        bar[6] = '"' + name + '"'
        fields[start + slot] = '1'
        fields.insert(start + slot + 1, '{' + ','.join(bar) + '}')
    return plain[:root['start']] + '{' + ','.join(fields) + '}' + plain[root['end']:]

def panel_count(plain):
    nodes = tuple_nodes(plain)
    if len(nodes) < 2:
        raise ValueError('missing form root')
    count = 0
    for value in nodes[1]['fields']:
        if not value.startswith('{22,'):
            continue
        fields = tuple_nodes(value)[0]['fields']
        if len(fields) > 6 and fields[2] == '1' and fields[5] == '9' and any(
                fields[1].startswith('{' + str(identifier) + ',') for _, identifier, _ in PANELS):
            count += 1
    return count

def assembler(exe, work, label, *arguments):
    done = subprocess.run([str(exe), *map(str, arguments)], capture_output=True)
    (work / (label + '.stdout')).write_bytes(done.stdout)
    (work / (label + '.stderr')).write_bytes(done.stderr)
    if done.returncode:
        raise RuntimeError('storage assembler failed: ' + label)

def native(args, work, label, *arguments):
    ok, output = run_ibcmd(args, args.ibcmd, work, *map(str, arguments))
    (work / (label + '.log')).write_text(output, encoding='utf-8')
    if not ok:
        raise RuntimeError(first_error(output, [work]))

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ['source', 'set', 'work', 'assembler']:
        parser.add_argument('--' + name, type=Path, required=True)
    for name in ['ibcmd', 'platform', 'xml-version', 'assembler-source-commit']:
        parser.add_argument('--' + name, required=True)
    parser.add_argument('--verify-xml-normalization', action='store_true')
    args = parser.parse_args()
    if args.work.exists() or args.set.exists():
        raise SystemExit('use new work and set directories')
    if not re.fullmatch('[0-9a-f]{40}', args.assembler_source_commit):
        raise SystemExit('pin the storage assembler source commit')
    args.work.mkdir(parents=True)
    work = args.work
    source = json.loads((args.source / 'manifest.json').read_text(encoding='utf-8'))
    if source['evidence']['status'] != 'platform_proven':
        raise ValueError('requires a public native-proven source set')
    encoded = (args.source / 'configuration.cf.b64').read_bytes()
    cf = base64.b64decode(encoded)
    if sha(cf) != source['configuration_cf']['decoded_sha256']:
        raise SystemExit('source CF hash differs from its public native manifest')
    (work / 'base.cf').write_bytes(cf)
    (work / 'extract').mkdir()
    (work / 'patches').mkdir()
    patches, records, candidates = [], [], []
    donor = None
    for descriptor in sorted((args.source / 'native').rglob('*.xml')):
        if 'Ext' in descriptor.relative_to(args.source / 'native').parts:
            continue
        try:
            md = ET.parse(descriptor).getroot()
        except ET.ParseError:
            continue
        if not md.tag.endswith('}MetaDataObject') or not len(md) or md[0].tag.split('}')[-1] not in ('Form', 'CommonForm'):
            continue
        form_type = next((node.text for node in md[0].iter() if node.tag.endswith('}FormType')), None)
        if form_type != 'Managed':
            continue
        xml_path = descriptor.with_suffix('') / 'Ext/Form.xml'
        if not xml_path.exists():
            continue
        form = ET.parse(xml_path).getroot()
        bar = next((item for item in form if item.tag.endswith('}AutoCommandBar')), None)
        if bar is None:
            raise ValueError('native form has no AutoCommandBar')
        owner = md[0].get('uuid')
        key = owner + '.0'
        extracted = work / 'extract' / owner
        assembler(args.assembler, work, 'extract-' + owner, 'cf', 'extract', work / 'base.cf', key, extracted)
        plain = (extracted / 'unpacked.bin').read_bytes().decode('utf-8-sig')
        candidates.append((descriptor, owner, key, xml_path, extracted, plain, bar.get('name')))
        children = next((child for child in bar if child.tag.endswith('}ChildItems')), None)
        if donor is None and (children is None or not len(children)):
            donor = next(node['fields'] for node in tuple_nodes(plain) if '"' + bar.get('name') + '"' in node['fields'])
    if donor is None:
        raise ValueError('source has no natively empty AutoCommandBar donor')
    for descriptor, owner, key, xml_path, extracted, plain, name in candidates:
        changed = add_panels(plain, name, donor).encode('utf-8-sig')
        if panel_count(changed.decode('utf-8-sig')) != 3:
            raise ValueError('synthetic presence flags do not name exactly three generated bars')
        patch = work / 'patches' / key
        patch.write_bytes(changed)
        patches.append((key, patch))
        records.append({'entry': key, 'source_sha256': sha((extracted / 'unpacked.bin').read_bytes()),
                        'stored_sha256': sha(changed), 'native_xml': xml_path.relative_to(args.source / 'native').as_posix()})
    if not patches:
        raise ValueError('source has no native managed form bodies')
    current = work / 'base.cf'
    for index in range(0, len(patches), 16):
        output = work / ('assembled-' + str(index) + '.cf')
        arguments = ['cf', 'overlay', current, output, '--platform', source['evidence']['platform_version']]
        for key, patch in patches[index:index + 16]:
            arguments.extend(['--raw-asset', key + '=' + str(patch)])
        assembler(args.assembler, work, 'overlay-' + str(index), *arguments)
        current = output
    native(args, work, 'create-1', 'infobase', 'create', '--db-path=' + str(work / 'ib1'),
           '--create-database', '--locale=ru_RU', '--load=' + str(current), '--apply', '--force')
    native(args, work, 'export-1', 'infobase', 'config', 'export', '--db-path=' + str(work / 'ib1'), work / 'round1')
    native(args, work, 'save', 'infobase', 'config', 'save', '--db-path=' + str(work / 'ib1'), work / 'native.cf')
    native(args, work, 'create-2', 'infobase', 'create', '--db-path=' + str(work / 'ib2'),
           '--create-database', '--locale=ru_RU', '--load=' + str(work / 'native.cf'), '--apply', '--force')
    native(args, work, 'export-2', 'infobase', 'config', 'export', '--db-path=' + str(work / 'ib2'), work / 'round2')
    first, second = tree_files(work / 'round1'), tree_files(work / 'round2')
    if first != second:
        raise ValueError('native load/save/load exports differ outside ConfigDumpInfo.xml')
    (work / 'verify').mkdir()
    for record in records:
        target = work / 'verify' / record['entry']
        assembler(args.assembler, work, 'verify-' + record['entry'], 'cf', 'extract', work / 'native.cf', record['entry'], target)
        if sha((target / 'unpacked.bin').read_bytes()) != record['stored_sha256']:
            raise ValueError('native save did not retain the exact synthetic stored form')
    normalization = None
    if args.verify_xml_normalization:
        native(args, work, 'xml-create', 'infobase', 'create', '--db-path=' + str(work / 'ib3'), '--create-database',
               '--locale=ru_RU', '--import=' + str(work / 'round2'), '--apply', '--force')
        native(args, work, 'xml-save', 'infobase', 'config', 'save', '--db-path=' + str(work / 'ib3'), work / 'normalized.cf')
        native(args, work, 'xml-export', 'infobase', 'config', 'export', '--db-path=' + str(work / 'ib3'), work / 'round3')
        if tree_files(work / 'round3') != second:
            raise ValueError('XML normalization changed native observable sources')
        (work / 'normalized').mkdir()
        for record in records:
            target = work / 'normalized' / record['entry']
            assembler(args.assembler, work, 'normalized-' + record['entry'], 'cf', 'extract', work / 'normalized.cf', record['entry'], target)
            normalized = (target / 'unpacked.bin').read_bytes()
            if panel_count(normalized.decode('utf-8-sig')) != 0:
                raise ValueError('XML import retained the storage profile; use an ordinary XML fixture')
        normalization = 'native XML export/import/export is identical; all three stored generated command bars disappear on native XML reconstruction'
    publish = work / 'publish'
    (publish / 'input').mkdir(parents=True)
    dependency = {'set': source['set'], 'path': Path(os.path.relpath(args.source, args.set.parent)).as_posix(),
                  'manifest_sha256': sha((args.source / 'manifest.json').read_bytes())}
    recipe = {'schema_version': 1, 'profile': 'generated-root-command-panels',
              'origin': 'clones each public native empty AutoCommandBar; adds three generated internal panel records',
              'panels': [{'trailer_slot': slot, 'id': identifier, 'name': name} for slot, identifier, name in PANELS],
              'records': records}
    (publish / 'input/source.json').write_text(json.dumps(dependency, indent=2) + '\n', encoding='utf-8')
    (publish / 'input/recipe.json').write_text(json.dumps(recipe, indent=2) + '\n', encoding='utf-8')
    shutil.copytree(work / 'round2', publish / 'native', ignore=shutil.ignore_patterns('ConfigDumpInfo.xml'))
    raw = (work / 'native.cf').read_bytes()
    output = base64.encodebytes(raw)
    (publish / 'configuration.cf.b64').write_bytes(output)
    variants, claimed = [], set()
    for variant in source['variants']:
        record = dict(variant)
        owned = {item['path'] for item in variant.get('files', [])}
        record['files'] = [{'path': path, 'size': len(data), 'sha256': sha(data)} for path, data in second.items() if path in owned]
        claimed.update(item['path'] for item in record['files'])
        variants.append(record)
    for path in sorted(second.keys() - claimed):
        variants[0]['files'].append({'path': path, 'size': len(second[path]), 'sha256': sha(second[path])})
    manifest = {'schema_version': 1, 'kind': 'stored_layout',
                'set': args.platform + '-xml-' + args.xml_version + '-' + args.set.name,
                'evidence': {'status': 'platform_proven', 'created_on': datetime.date.today().isoformat(),
                            'platform_version': args.platform, 'source_version': args.xml_version,
                            'ibcmd_sha256': sha(Path(args.ibcmd).read_bytes()), 'database_kind': 'isolated_file_infobase',
                            'rounds': 'synthetic public storage overlay -> native load/export -> native save -> native load/export; identical outside ConfigDumpInfo.xml; every altered form retained byte-for-byte by native save',
                            'origin': 'synthetic stored profile, not a native XML writer fixture', 'xml_normalization': normalization,
                            'storage_assembler': {'sha256': sha(args.assembler.read_bytes()), 'source_commit': args.assembler_source_commit,
                                                  'scope': 'CF extraction/container assembly only; native ibcmd provides all expected XML'}},
                'inputs_sha256': inputs_digest(publish), 'sources': [dependency],
                'configuration_cf': {'path': 'configuration.cf.b64', 'decoded_size': len(raw),
                                     'decoded_sha256': sha(raw), 'encoded_sha256': sha(output)}, 'variants': variants}
    if source.get('collection'):
        manifest['collection'] = source['collection']
    (publish / 'manifest.json').write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    shutil.copytree(publish, args.set)
    print(str(len(records)) + ' synthetic stored form profiles proven on ' + args.platform)

if __name__ == '__main__':
    main()

#!/usr/bin/env python3
"""Prove a generated, versioned extension schema-update pair in fresh file infobases.

Inputs contain a minimal base and independently generated baseline/update XML.
Each image is imported/applied, exported, saved as CFE, then loaded/applied in
another fresh base and exported again. No converter output is an oracle.
SQL schema/data publication is measured separately by the consumer's lab.
"""
import argparse
import base64
import copy
import datetime
import json
import pathlib
import re
import shutil
import xml.etree.ElementTree as ET

from native_round_trip import create_from_tree, first_error, inputs_digest, run_ibcmd, sha256, tree_files


def prove(args, label, image, work, base):
    name = 'CovCombined' if label == 'combined' else 'CovRestructure' + label.title().replace('-', '')
    records = []
    cfe = work / (name + '.cfe')
    for round_number in (1, 2):
        ib = work / (label + '-ib-' + str(round_number))
        shutil.copytree(base, ib)
        commands = [('extension', 'create', '--name=CovCombined', '--name-prefix=Cov_', '--purpose=add-on')]
        if round_number == 1:
            commands.append(('config', 'import', '--extension=CovCombined', '--out=' + str(cfe), str(image)))
        commands.extend([
            ('config', 'load', '--extension=CovCombined', str(cfe)),
            ('config', 'apply', '--extension=CovCombined', '--force'),
        ])
        for command in commands:
            ok, output = run_ibcmd(args, args.ibcmd, work, *command, '--db-path=' + str(ib))
            if not ok:
                raise SystemExit(f'{name} round {round_number}: {first_error(output, (work, args.set))}')
        target = work / ('round-' + str(round_number)) / name
        ok, output = run_ibcmd(args, args.ibcmd, work, 'config', 'export', '--extension=CovCombined',
                               '--threads=1', '--db-path=' + str(ib), str(target))
        if not ok:
            raise SystemExit(f'{name} export: {first_error(output, (work, args.set))}')
        records.append(tree_files(target))
        if round_number == 1:
            ok, output = run_ibcmd(args, args.ibcmd, work, 'config', 'save', '--extension=CovCombined',
                                   '--db-path=' + str(ib), str(cfe))
            if not ok:
                raise SystemExit(f'{name} save: {first_error(output, (work, args.set))}')
    if records[0] != records[1]:
        differing = sorted(k for k in records[0].keys() | records[1].keys()
                           if records[0].get(k) != records[1].get(k))
        raise SystemExit(f'{name}: native rounds differ: {differing[:5]}')
    native_target = args.set / 'native' / name
    if native_target.exists():
        shutil.rmtree(native_target)
    for relative, data in records[1].items():
        target = args.set / 'native' / name / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
    artifact = pathlib.Path('variants') / (name + '.cfe.b64')
    (args.set / artifact).parent.mkdir(parents=True, exist_ok=True)
    (args.set / artifact).write_bytes(base64.encodebytes(cfe.read_bytes()))
    return {'name': name, 'property': 'SchemaUpdate', 'value': label, 'source': 'generated',
            'status': 'platform_proven',
            'file': {'path': artifact.as_posix(), 'decoded_sha256': sha256(cfe.read_bytes())},
            'files': [{'path': name + '/' + path, 'size': len(data), 'sha256': sha256(data)}
                      for path, data in records[1].items()]}



def adopted_fingerprint(obj):
    """Compare controlled content while allowing its generated base alias."""
    obj = copy.deepcopy(obj)
    ns = '{http://v8.1c.ru/8.3/MDClasses}'
    props = obj.find(ns + 'Properties')
    name_node = props.find(ns + 'Name')
    name = name_node.text
    name_node.text = '$alias'
    reference = props.find(ns + 'ExtendedConfigurationObject')
    if reference is not None:
        reference.text = '$base'
    for node in obj.iter():
        if node.tag.endswith('}GeneratedType') and node.attrib.get('name', '').endswith('.' + name):
            node.attrib['name'] = node.attrib['name'][:-len(name)] + '$alias'
        if len(node) and node.text and node.text.isspace():
            node.text = None
        if node.tail and node.tail.isspace():
            node.tail = None
    return ET.tostring(obj)


def merge_images(legacy, additions, target):
    """Keep the previous adopted identity when an input renames that same UUID.

    The same root roster entry cannot occur twice. A changed own descriptor
    under the same path is accepted only when its canonical XML is identical;
    the new image supplies the extension version and additional root children.
    """
    shutil.copytree(legacy, target)
    ns = '{http://v8.1c.ru/8.3/MDClasses}'
    identities = {}
    for path in legacy.glob('*/*.xml'):
        obj = ET.fromstring(path.read_bytes())[0]
        identities[obj.attrib.get('uuid')] = (path.relative_to(legacy), obj)
    skipped = set()
    for source in sorted(additions.rglob('*')):
        if not source.is_file() or source.name in ('ConfigDumpInfo.xml', 'Configuration.xml'):
            continue
        relative = source.relative_to(additions)
        if source.suffix == '.xml' and len(relative.parts) == 2:
            obj = ET.fromstring(source.read_bytes())[0]
            previous = identities.get(obj.attrib.get('uuid'))
            if previous and previous[0] != relative:
                belonging = obj.find(ns + 'Properties/' + ns + 'ObjectBelonging')
                old_belonging = previous[1].find(ns + 'Properties/' + ns + 'ObjectBelonging')
                if (obj.tag != previous[1].tag or belonging is None or old_belonging is None
                        or belonging.text != 'Adopted' or old_belonging.text != 'Adopted'):
                    raise ValueError('conflicting own metadata identities')
                if adopted_fingerprint(obj) != adopted_fingerprint(previous[1]):
                    raise ValueError('conflicting adopted properties')
                skipped.add((obj.tag.removeprefix(ns), source.stem))
                continue
        dest = target / relative
        if dest.exists() and dest.read_bytes() != source.read_bytes():
            try:
                equal = ET.tostring(ET.fromstring(dest.read_bytes())) == ET.tostring(ET.fromstring(source.read_bytes()))
            except ET.ParseError:
                equal = False
            if not equal:
                raise ValueError('conflicting combined input: ' + relative.as_posix())
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, dest)
    config = (additions / 'Configuration.xml').read_text(encoding='utf-8-sig')
    old_config = (legacy / 'Configuration.xml').read_text(encoding='utf-8-sig')
    pattern = r'(<ChildObjects>)(.*?)(</ChildObjects>)'
    old_roster = re.search(pattern, old_config, re.S)
    roster = re.search(pattern, config, re.S)
    if not old_roster or not roster:
        raise ValueError('both combined inputs need a root roster')
    entries = list(dict.fromkeys(re.findall(r'<(\w+)>([^<]+)</\1>', roster[2]) +
                                 re.findall(r'<(\w+)>([^<]+)</\1>', old_roster[2])))
    entries = [entry for entry in entries if entry not in skipped]
    content = '\n' + ''.join('\t\t\t<' + tag + '>' + name + '</' + tag + '>\n' for tag, name in entries) + '\t\t'
    config = config[:roster.start(2)] + content + config[roster.end(2):]
    (target / 'Configuration.xml').write_text(config, encoding='utf-8-sig')


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument('--ibcmd', required=True)
    parser.add_argument('--set', required=True, type=pathlib.Path)
    parser.add_argument('--work', required=True, type=pathlib.Path)
    parser.add_argument('--platform', required=True)
    parser.add_argument('--xml-version', required=True)
    parser.add_argument('--locale', default='ru_RU')
    parser.add_argument('--combined', action='store_true', help='merge input/legacy and input/additions into one extension')
    parser.add_argument('--variant', action='append', choices=('baseline', 'update', 'two-jobs', 'two-plain-jobs', 'dialog-baseline', 'dialog-update'),
                        help='prove only the selected input; repeat for several variants')
    parser.add_argument('--append', action='store_true', help='append new proven variants without rebuilding previous native images')
    args = parser.parse_args()
    if args.combined and args.variant:
        parser.error('--combined cannot select individual variants')
    if args.variant and len(set(args.variant)) != len(args.variant):
        parser.error('each variant can be selected only once')
    if args.append and (not args.variant or args.combined):
        parser.error('--append requires --variant and cannot be combined with --combined')
    previous = json.loads((args.set / 'manifest.json').read_text(encoding='utf-8')) if args.append else None
    if previous and (previous['evidence']['platform_version'] != args.platform
                     or previous['evidence']['source_version'] != args.xml_version
                     or previous['evidence']['ibcmd_sha256'] != sha256(pathlib.Path(args.ibcmd).read_bytes())):
        raise SystemExit('appended proof must use the same exact native profile and binary')
    labels = args.variant or ['baseline', 'update']
    if not args.variant:
        for optional in ('two-jobs', 'two-plain-jobs', 'dialog-baseline', 'dialog-update'):
            if (args.set / 'input' / optional).is_dir():
                labels.append(optional)
    if previous:
        existing = {variant['name'] for variant in previous['variants']}
        if any('CovRestructure' + label.title().replace('-', '') in existing for label in labels):
            raise SystemExit('append refuses to replace an existing proven variant')
    if args.work.exists():
        raise SystemExit('work directory must be new')
    args.work.mkdir(parents=True)
    base = args.work / 'base-ib'
    ok, output = create_from_tree(args, args.work, args.set / 'input/base', base)
    if not ok:
        raise SystemExit('base: ' + first_error(output, (args.work, args.set)))
    if args.combined:
        image = args.work / 'combined-src'
        merge_images(args.set / 'input/legacy', args.set / 'input/additions', image)
        variants = [prove(args, 'combined', image, args.work, base)]
    else:
        variants = [prove(args, label, args.set / 'input' / label, args.work, base)
                    for label in labels]
    if previous:
        variants = previous['variants'] + variants
    manifest = {
        'schema_version': 1, 'kind': 'extension',
        'set': f'{args.platform}-xml-{args.xml_version}-extensions-{args.set.name}',
        'evidence': {'status': 'platform_proven', 'created_on': datetime.date.today().isoformat(),
                     'platform_version': args.platform, 'source_version': args.xml_version,
                     'ibcmd_sha256': sha256(pathlib.Path(args.ibcmd).read_bytes()),
                     'database_kind': 'isolated_file_infobase',
                     'rounds': 'fresh base -> build CFE from XML -> load/apply -> export (1) -> save CFE -> '
                               'another fresh base -> load/apply CFE -> export (2); identical outside ConfigDumpInfo.xml'},
        'inputs_sha256': inputs_digest(args.set),
        'sources': json.loads((args.set / 'input/values.json').read_text(encoding='utf-8')).get('sources', []),
        'variants': variants,
    }
    (args.set / 'manifest.json').write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    print(f'{args.platform}: {len(variants)} extension schema-update images platform-proven')


if __name__ == '__main__':
    main()

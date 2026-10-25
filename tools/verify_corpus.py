"""Check the complete public fixture inventory and native proof manifests."""
import argparse
import base64
import hashlib
import json
import pathlib
import sys
import subprocess
sys.path.insert(0, str(pathlib.Path(__file__).parent / 'coverage'))
from native_round_trip import inputs_digest

ROOT = pathlib.Path(__file__).resolve().parents[1]
DATA_ROOTS = ('coverage', 'coverage-records')


def digest(data):
    return hashlib.sha256(data).hexdigest()


def inventory(root):
    files = {}
    for name in DATA_ROOTS:
        folder = root / name
        if folder.is_symlink():
            raise ValueError(f'symlink corpus directory: {name}')
        if not folder.is_dir():
            raise ValueError(f'missing corpus directory: {name}')
        for path in sorted(folder.rglob('*')):
            if path.is_symlink():
                raise ValueError(f'symlink in corpus: {path}')
            if path.is_file():
                if path.name in ('baseline.json', 'storage-baseline.json'):
                    raise ValueError('converter baselines belong in the consumer repository')
                files[path.relative_to(root).as_posix()] = digest(path.read_bytes())
    if not files:
        raise ValueError('empty corpus')
    return files


def verify_inventory(root):
    document = json.loads((root / 'corpus-index.json').read_text(encoding='utf-8'))
    if document['schema_version'] != 1:
        raise ValueError('unsupported corpus inventory schema')
    actual = inventory(root)
    expected = document['files']
    missing = sorted(expected.keys() - actual.keys())
    extra = sorted(actual.keys() - expected.keys())
    changed = sorted(k for k in actual.keys() & expected.keys() if actual[k] != expected[k])
    if missing or extra or changed:
        raise ValueError(f'fixture inventory differs: missing={missing[:3]}, extra={extra[:3]}, changed={changed[:3]}')
    return len(actual)


def verify_proofs(root):
    count = 0
    identifiers = set()
    for path in sorted((root / 'coverage').rglob('manifest.json')):
        data = json.loads(path.read_text(encoding='utf-8'))
        name = data['set']
        if name in identifiers:
            raise ValueError(f'duplicate set identifier: {name}')
        identifiers.add(name)
        source = path.parent
        if data['evidence']['status'] != 'platform_proven':
            raise ValueError(f'unproven native fixture: {name}')
        if inputs_digest(source) != data['inputs_sha256']:
            raise ValueError(f'changed native inputs: {name}')
        for dependency in data.get('sources', []):
            target = (source.parent / dependency['path'] / 'manifest.json').resolve()
            if not target.is_relative_to((root / 'coverage').resolve()):
                raise ValueError(f'source reference escapes the corpus: {name}')
            if digest(target.read_bytes()) != dependency['manifest_sha256']:
                raise ValueError(f'stale combined/cross-version source: {name}')
        variants = data['variants']
        if data.get('kind') in ('external', 'extension'):
            artifacts = [(v['file']['path'], v['file']['decoded_sha256']) for v in variants if v['status'] == 'platform_proven']
        else:
            artifacts = [('configuration.cf.b64', data['configuration_cf']['decoded_sha256'])]
        for relative, expected in artifacts:
            target = (source / relative).resolve()
            if not target.is_relative_to(source.resolve()):
                raise ValueError(f'artifact escapes its set: {name}')
            if digest(base64.b64decode(target.read_bytes())) != expected:
                raise ValueError(f'CF/EPF/ERF hash differs: {name}/{relative}')
        for variant in variants:
            if variant['status'] not in ('platform_proven', 'platform_refused'):
                raise ValueError(f'unknown native status: {name}')
            for item in variant.get('files', []):
                target = (source / 'native' / item['path']).resolve()
                if not target.is_relative_to((source / 'native').resolve()):
                    raise ValueError(f'native path escapes its set: {name}')
                if digest(target.read_bytes()) != item['sha256']:
                    raise ValueError(f'native oracle hash differs: {name}/{item["path"]}')
        count += 1
    if not count:
        raise ValueError('no platform-proven set')
    return count


def verify_tracked_modes(root):
    records = subprocess.check_output(['git', '-C', str(root), 'ls-files', '--stage', '-z']).split(b'\0')
    for record in records:
        if not record:
            continue
        mode = record.split(b' ', 1)[0]
        if mode not in (b'100644', b'100755'):
            raise ValueError(f'non-regular tracked corpus path: {record.decode("utf-8", errors="replace")}')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--write-index', action='store_true', help='Record hashes after reviewing/proving a fixture change; does not update native manifests')
    args = parser.parse_args()
    verify_tracked_modes(ROOT)
    sets = verify_proofs(ROOT)
    if args.write_index:
        (ROOT / 'corpus-index.json').write_text(json.dumps({'schema_version': 1, 'files': inventory(ROOT)}, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    files = verify_inventory(ROOT)
    print(f'Corpus integrity: PASS; {files} files, {sets} native-proven sets')


if __name__ == '__main__':
    main()

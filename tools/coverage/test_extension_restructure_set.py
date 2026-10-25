"""Combining extension images preserves prior identities and refuses collisions."""
import pathlib
import json
from unittest import mock
import tempfile
import unittest
import xml.etree.ElementTree as ET

from extension_restructure_set import main, merge_images
from native_round_trip import sha256

NS = 'http://v8.1c.ru/8.3/MDClasses'


def descriptor(kind, name, identity, belonging='Own'):
    return (f'<MetaDataObject xmlns="{NS}"><{kind} uuid="{identity}">'
            f'<Properties><ObjectBelonging>{belonging}</ObjectBelonging>'
            f'<Name>{name}</Name></Properties><ChildObjects/></{kind}></MetaDataObject>')


def configuration(children, version):
    roster = ''.join(f'<{kind}>{name}</{kind}>' for kind, name in children)
    return (f'<MetaDataObject xmlns="{NS}"><Configuration uuid="root">'
            f'<Properties><Name>Coverage</Name><Version>{version}</Version></Properties>'
            f'<ChildObjects>{roster}</ChildObjects></Configuration></MetaDataObject>')


class MergeExtensionImages(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        root = pathlib.Path(self.temp.name)
        self.legacy, self.additions, self.target = (root / x for x in ('legacy', 'additions', 'target'))
        self.legacy.mkdir()
        self.additions.mkdir()

    def put(self, root, path, text):
        dest = root / path
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text(text, encoding='utf-8')

    def test_adopted_alias_keeps_previous_identity_and_new_roster(self):
        self.put(self.legacy, 'Configuration.xml', configuration([('Catalog', 'OldName')], '1.0.0.0'))
        self.put(self.legacy, 'Catalogs/OldName.xml', descriptor('Catalog', 'OldName', 'same-id', 'Adopted'))
        self.put(self.additions, 'Configuration.xml', configuration([('Catalog', 'Renamed'), ('Catalog', 'NewOwn')], '1.0.0.1'))
        self.put(self.additions, 'Catalogs/Renamed.xml', descriptor('Catalog', 'Renamed', 'same-id', 'Adopted'))
        self.put(self.additions, 'Catalogs/NewOwn.xml', descriptor('Catalog', 'NewOwn', 'new-id'))
        merge_images(self.legacy, self.additions, self.target)
        self.assertFalse((self.target / 'Catalogs/Renamed.xml').exists())
        self.assertEqual((self.target / 'Catalogs/OldName.xml').read_bytes(),
                         (self.legacy / 'Catalogs/OldName.xml').read_bytes())
        node = ET.fromstring((self.target / 'Configuration.xml').read_bytes())[0]
        self.assertEqual(node.find(f'{{{NS}}}Properties/{{{NS}}}Version').text, '1.0.0.1')
        roster = node.find(f'{{{NS}}}ChildObjects')
        self.assertEqual([(x.tag.split('}')[-1], x.text) for x in roster],
                         [('Catalog', 'NewOwn'), ('Catalog', 'OldName')])

    def test_conflicting_own_identity_is_rejected(self):
        self.put(self.legacy, 'Catalogs/Old.xml', descriptor('Catalog', 'Old', 'same-id'))
        self.put(self.additions, 'Catalogs/New.xml', descriptor('Catalog', 'New', 'same-id'))
        with self.assertRaisesRegex(ValueError, 'conflicting own metadata identities'):
            merge_images(self.legacy, self.additions, self.target)

    def test_adopted_alias_cannot_hide_changed_controlled_properties(self):
        old = descriptor('Catalog', 'Old', 'same-id', 'Adopted').replace('</Properties>', '<CodeLength>9</CodeLength></Properties>')
        new = descriptor('Catalog', 'New', 'same-id', 'Adopted').replace('</Properties>', '<CodeLength>10</CodeLength></Properties>')
        self.put(self.legacy, 'Catalogs/Old.xml', old)
        self.put(self.additions, 'Catalogs/New.xml', new)
        with self.assertRaisesRegex(ValueError, 'conflicting adopted properties'):
            merge_images(self.legacy, self.additions, self.target)

    def test_changed_existing_descriptor_is_rejected(self):
        self.put(self.legacy, 'Catalogs/Shared.xml', descriptor('Catalog', 'Shared', 'old-id'))
        self.put(self.additions, 'Catalogs/Shared.xml', descriptor('Catalog', 'Shared', 'different-id'))
        with self.assertRaisesRegex(ValueError, 'conflicting combined input'):
            merge_images(self.legacy, self.additions, self.target)

    def test_body_collision_is_rejected(self):
        self.put(self.legacy, 'Catalogs/Shared/Ext/ObjectModule.bsl', 'before')
        self.put(self.additions, 'Catalogs/Shared/Ext/ObjectModule.bsl', 'after')
        with self.assertRaisesRegex(ValueError, 'conflicting combined input'):
            merge_images(self.legacy, self.additions, self.target)



class AppendNativeProof(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = pathlib.Path(temp.name)
        self.set = self.root / 'set'
        (self.set / 'input').mkdir(parents=True)
        (self.set / 'input/values.json').write_text('{}', encoding='utf-8')
        for label in ('base', 'baseline', 'dialog-update'):
            target = self.set / 'input' / label
            target.mkdir()
            (target / 'Configuration.xml').write_text(label + '-original', encoding='utf-8')
        self.binary = self.root / 'native'
        self.binary.write_bytes(b'controlled-native-binary')
        self.old = {'name': 'CovRestructureBaseline', 'value': 'baseline', 'files': [{'path': 'Roles/ОсновнаяРоль.xml'}]}
        self.previous = {
            'evidence': {'platform_version': '8.5.1.1150', 'source_version': '2.21',
                         'ibcmd_sha256': sha256(self.binary.read_bytes())},
            'variants': [self.old],
            'proven_inputs_sha256': {
                'input/base': sha256(b'Configuration.xml\0base-original'),
                'input/baseline': sha256(b'Configuration.xml\0baseline-original'),
            },
        }
        self.save_manifest()
        self.argv = ['extension_restructure_set', '--ibcmd', str(self.binary),
                     '--platform', '8.5.1.1150', '--xml-version', '2.21',
                     '--set', str(self.set), '--work', str(self.root / 'work'),
                     '--append', '--variant', 'dialog-update']

    def save_manifest(self):
        (self.set / 'manifest.json').write_text(
            json.dumps(self.previous, ensure_ascii=False), encoding='utf-8')

    def test_append_preserves_previous_native_proof_and_unicode(self):
        new = {'name': 'CovRestructureDialogUpdate', 'value': 'dialog-update'}
        (self.set / 'input/dialog-update/Configuration.xml').write_text('new-case', encoding='utf-8')
        with mock.patch('sys.argv', self.argv), \
                mock.patch('extension_restructure_set.create_from_tree', return_value=(True, '')), \
                mock.patch('extension_restructure_set.prove', return_value=new):
            main()
        result = json.loads((self.set / 'manifest.json').read_text(encoding='utf-8'))
        self.assertEqual(result['variants'], [self.old, new])
        for relative, expected in self.previous['proven_inputs_sha256'].items():
            self.assertEqual(result['proven_inputs_sha256'][relative], expected)
        self.assertEqual(result['proven_inputs_sha256']['input/dialog-update'],
                         sha256(b'Configuration.xml\0new-case'))

    def test_changed_native_binary_is_rejected_before_creating_a_base(self):
        self.previous['evidence']['ibcmd_sha256'] = 'different-binary'
        self.save_manifest()
        with mock.patch('sys.argv', self.argv), \
                mock.patch('extension_restructure_set.create_from_tree') as create:
            with self.assertRaisesRegex(SystemExit, 'same exact native profile'):
                main()
        create.assert_not_called()
        self.assertFalse((self.root / 'work').exists())

    def test_existing_variant_cannot_be_replaced_or_run_again(self):
        self.argv[-1] = 'baseline'
        with mock.patch('sys.argv', self.argv), \
                mock.patch('extension_restructure_set.create_from_tree') as create:
            with self.assertRaisesRegex(SystemExit, 'replace an existing proven variant'):
                main()
        create.assert_not_called()
        result = json.loads((self.set / 'manifest.json').read_text(encoding='utf-8'))
        self.assertEqual(result, self.previous)

    def assert_retained_input_change_rejected(self, label):
        path = self.set / 'input' / label / 'Configuration.xml'
        path.write_text('mutated-after-native-proof', encoding='utf-8')
        before = (self.set / 'manifest.json').read_bytes()
        with mock.patch('sys.argv', self.argv), \
                mock.patch('extension_restructure_set.create_from_tree', return_value=(True, '')) as create, \
                mock.patch('extension_restructure_set.prove', return_value={'name': 'CovRestructureDialogUpdate'}):
            with self.assertRaisesRegex(SystemExit, 'retained native inputs changed: input/' + label):
                main()
        create.assert_not_called()
        self.assertFalse((self.root / 'work').exists())
        self.assertEqual((self.set / 'manifest.json').read_bytes(), before)

    def test_changed_retained_variant_cannot_receive_a_fresh_proof_digest(self):
        self.assert_retained_input_change_rejected('baseline')

    def test_changed_shared_base_cannot_keep_retained_native_proofs(self):
        self.assert_retained_input_change_rejected('base')

    def test_legacy_proof_without_source_fingerprints_requires_full_reproof(self):
        del self.previous['proven_inputs_sha256']
        self.save_manifest()
        with mock.patch('sys.argv', self.argv), \
                mock.patch('extension_restructure_set.create_from_tree') as create:
            with self.assertRaisesRegex(SystemExit, 'reprove the full set'):
                main()
        create.assert_not_called()
        self.assertFalse((self.root / 'work').exists())


if __name__ == '__main__':
    unittest.main()

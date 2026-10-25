"""Combining extension images preserves prior identities and refuses collisions."""
import pathlib
import tempfile
import unittest
import xml.etree.ElementTree as ET

from extension_restructure_set import merge_images

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


if __name__ == '__main__':
    unittest.main()

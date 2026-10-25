import unittest
from native_rebuild_set import promote_format_version, native, validate_source
from unittest.mock import patch
from tempfile import TemporaryDirectory
from pathlib import Path
from types import SimpleNamespace
import subprocess
import json

class FormatPromotion(unittest.TestCase):
    def test_changes_schema_attribute_and_preserves_release_and_literal_text(self):
        original = b'\xef\xbb\xbf<?xml version="1.0"?><MetaDataObject version="2.20"><Version>2.20</Version><Comment>version="2.20"</Comment></MetaDataObject>'
        result = promote_format_version(original)
        self.assertEqual(result, original.replace(b'<MetaDataObject version="2.20">', b'<MetaDataObject version="2.21">'))

    def test_does_not_change_a_template_or_its_embedded_form_text(self):
        original = b'<?xml version="1.0"?><document><text><![CDATA[<Form version="2.20">]]></text></document>'
        self.assertEqual(promote_format_version(original), original)

    def test_promotes_other_platform_roots_without_touching_content(self):
        for root in ['GraphicalSchema', 'Rights', 'AdditionalIndexes']:
            original = ('<' + root + ' version="2.20"><literal>2.20</literal></' + root + '>').encode()
            self.assertEqual(promote_format_version(original), original.replace(b'version="2.20"', b'version="2.21"'))

    def test_leaves_current_format_bytes_untouched(self):
        original = b'<?xml version="1.0"?>\r\n<Form version="2.21"/>\r\n'
        self.assertEqual(promote_format_version(original), original)

class SourceAcceptance(unittest.TestCase):
    def test_refused_source_choices_cannot_keep_a_stale_status_on_another_writer(self):
        manifest = {'evidence': {'status': 'platform_proven'}, 'variants': [{'status': 'platform_proven'}, {'status': 'platform_refused'}]}
        with self.assertRaisesRegex(ValueError, 'isolate accepted recipes'):
            validate_source(manifest)

    def test_accepts_a_wholly_proven_source(self):
        validate_source({'evidence': {'status': 'platform_proven'}, 'variants': [{'status': 'platform_proven'}]})

class NativeRefusal(unittest.TestCase):
    def test_path_executable_keeps_the_actual_refusal_and_json_receipt(self):
        with TemporaryDirectory() as tmp:
            work = Path(tmp)
            result = subprocess.CompletedProcess([], 1, '[ERROR] unsupported native input', '')
            with patch('native_round_trip.subprocess.run', return_value=result):
                with self.assertRaisesRegex(RuntimeError, 'unsupported native input'):
                    native(SimpleNamespace(work=work), Path('native/ibcmd'), work, 'create', 'infobase', 'create')
            receipt = json.loads((work / 'native-refusals.jsonl').read_text(encoding='utf-8'))
            self.assertEqual(receipt['command'][0], str(Path('native/ibcmd')))
            self.assertEqual(receipt['exit_code'], 1)

if __name__ == '__main__':
    unittest.main()

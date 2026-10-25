import importlib.util
import json
import pathlib
import tempfile
import unittest
from unittest.mock import patch

spec = importlib.util.spec_from_file_location("verify_corpus", pathlib.Path(__file__).resolve().parents[1]/"verify_corpus.py")
verify = importlib.util.module_from_spec(spec)
spec.loader.exec_module(verify)

class Inventory(unittest.TestCase):
    def check(self, mutate):
        with tempfile.TemporaryDirectory() as td:
            root=pathlib.Path(td)
            (root/"coverage").mkdir();(root/"coverage-records").mkdir()
            data=root/"coverage/probe.bin";data.write_bytes(b"native")
            (root/"coverage-records/anchor.bin").write_bytes(b"anchor")
            (root/"corpus-index.json").write_text(json.dumps({"schema_version":1,"files":verify.inventory(root)}),encoding="utf-8")
            mutate(root,data)
            return verify.verify_inventory(root)
    def test_tracked_symlink_is_rejected(self):
        with patch.object(verify.subprocess, 'check_output', return_value=b'120000 abc 0\ttools/link\0'):
            with self.assertRaisesRegex(ValueError, 'non-regular'):
                verify.verify_tracked_modes(pathlib.Path('.'))
    def test_regular_tracked_files_are_accepted(self):
        with patch.object(verify.subprocess, 'check_output', return_value=b'100644 abc 0\tcoverage/file\0'):
            verify.verify_tracked_modes(pathlib.Path('.'))
    def test_complete_inventory_passes(self):
        self.assertEqual(self.check(lambda *_:None),2)
    def test_changed_bytes_fail(self):
        with self.assertRaisesRegex(ValueError,"changed"):
            self.check(lambda root,data:data.write_bytes(b"changed"))
    def test_missing_file_fails(self):
        with self.assertRaisesRegex(ValueError,"missing"):
            self.check(lambda root,data:data.unlink())
    def test_extra_file_fails(self):
        with self.assertRaisesRegex(ValueError,"extra"):
            self.check(lambda root,data:(root/"coverage/extra.bin").write_bytes(b"extra"))
    def test_converter_baseline_is_rejected(self):
        with self.assertRaisesRegex(ValueError,"consumer repository"):
            self.check(lambda root,data:(root/"coverage/baseline.json").write_text("{}",encoding="utf-8"))

if __name__=="__main__":unittest.main()

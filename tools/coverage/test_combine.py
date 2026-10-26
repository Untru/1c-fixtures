"""Root assets and non-default root properties must survive combination."""
import json,pathlib,tempfile,unittest
from combine import merge_root_files,declared_configuration_properties,configuration_base,merge_object_inputs

class RootAssets(unittest.TestCase):
    def test_base_prunes_children_without_removing_properties(self):
        text = '<Configuration>\n\t\t<Properties>\n\t\t\t<DefaultLanguage>Language.English</DefaultLanguage>\n\t\t\t<CompatibilityMode>Version8_3_27</CompatibilityMode>\n\t\t</Properties>\n\t\t<ChildObjects>\n\t\t\t<Language>English</Language>\n\t\t\t<Catalog>Old</Catalog>\n\t\t</ChildObjects>\n</Configuration>'
        result = configuration_base(text)
        self.assertIn('<DefaultLanguage>Language.English</DefaultLanguage>', result)
        self.assertIn('<CompatibilityMode>Version8_3_27</CompatibilityMode>', result)
        self.assertIn('<Language>English</Language>', result)
        self.assertNotIn('<Catalog>Old</Catalog>', result)

    def test_configuration_sidecars_are_copied_and_conflicts_refused(self):
        with tempfile.TemporaryDirectory() as td:
            root=pathlib.Path(td);src=root/'Ext';src.mkdir();(src/'ClientApplicationInterface.xml').write_text('<ClientApplicationInterface/>',encoding='utf-8')
            target=root/'tree/Ext';merge_root_files(src,target)
            self.assertEqual((target/'ClientApplicationInterface.xml').read_bytes(),(src/'ClientApplicationInterface.xml').read_bytes())
            merge_root_files(src,target)
            (src/'ClientApplicationInterface.xml').write_text('<different/>',encoding='utf-8')
            with self.assertRaises(SystemExit):merge_root_files(src,target)

    def test_explicit_root_properties_keep_structured_children(self):
        with tempfile.TemporaryDirectory() as td:
            root=pathlib.Path(td);inputs=root/'input';(inputs/'base').mkdir(parents=True)
            (inputs/'values.json').write_text(json.dumps({'configuration_properties':['Permissions']}),encoding='utf-8')
            (inputs/'base/Configuration.xml').write_text('<Configuration><Permissions><Permission>Camera</Permission><Description>Coverage</Description></Permissions></Configuration>',encoding='utf-8')
            self.assertEqual(declared_configuration_properties(root),{'Permissions':'<Permissions><Permission>Camera</Permission><Description>Coverage</Description></Permissions>'})
            (inputs/'base/Configuration.xml').write_text('<Configuration><Permissions/><Permissions/></Configuration>',encoding='utf-8')
            with self.assertRaises(SystemExit):declared_configuration_properties(root)

class RepeatedLogicalObjects(unittest.TestCase):
    def test_identical_declarations_union_sidecars_without_deleting_an_input(self):
        with tempfile.TemporaryDirectory() as td:
            root=pathlib.Path(td);one=root/'one';two=root/'two';one.mkdir();two.mkdir()
            (one/'Form.xml').write_bytes(b'<Form/>');(two/'Module.bsl').write_bytes(b'// synthetic')
            target=root/'tree/CommonForms/CovProbe.xml'
            merge_object_inputs(b'<CommonForm/>',one,target)
            merge_object_inputs(b'<CommonForm/>',two,target)
            merge_object_inputs(b'<CommonForm/>',one,target)
            self.assertEqual((target.with_suffix('')/'Form.xml').read_bytes(),b'<Form/>')
            self.assertEqual((target.with_suffix('')/'Module.bsl').read_bytes(),b'// synthetic')

    def test_different_object_declarations_are_rejected(self):
        with tempfile.TemporaryDirectory() as td:
            root=pathlib.Path(td);target=root/'CovProbe.xml';missing=root/'absent'
            merge_object_inputs(b'<CommonForm/>',missing,target)
            with self.assertRaises(ValueError):merge_object_inputs(b'<Catalog/>',missing,target)
            self.assertEqual(target.read_bytes(),b'<CommonForm/>')

    def test_conflicting_sidecar_is_rejected_before_another_file_is_added(self):
        with tempfile.TemporaryDirectory() as td:
            root=pathlib.Path(td);one=root/'one';two=root/'two';one.mkdir();two.mkdir()
            (one/'Form.xml').write_bytes(b'<Form/>');(two/'Form.xml').write_bytes(b'<different/>')
            (two/'Before.xml').write_bytes(b'<new/>');target=root/'tree/CovProbe.xml'
            merge_object_inputs(b'<CommonForm/>',one,target)
            with self.assertRaises(ValueError):merge_object_inputs(b'<CommonForm/>',two,target)
            self.assertFalse((target.with_suffix('')/'Before.xml').exists())
            self.assertEqual((target.with_suffix('')/'Form.xml').read_bytes(),b'<Form/>')

if __name__=='__main__':unittest.main()

import unittest
from stored_panels_set import add_panels, tuple_nodes

CLASS_ID = '02023637-7868-4a5f-8576-835a76e0c9ba'

def body():
    bar = '{22,{-1,' + CLASS_ID + '},0,0,0,9,"FormCommandBar",{0},0}'
    trailer = ['{59,0}', '1', '{1,0}', '{4,0}', '0', '0', '3', '0', '2', '4', '0', '0', '2', '0']
    root = '{' + ','.join(['59', bar] + trailer) + '}'
    return '{4,' + root + ',"Procedure Probe()\n X = ""{,} quoted"";\nEndProcedure",{0}}'

class StoredProfiles(unittest.TestCase):
    def test_quoted_module_is_not_interpreted_as_layout(self):
        original = body()
        changed = add_panels(original, 'FormCommandBar')
        self.assertEqual(tuple_nodes(original)[0]['fields'][2:], tuple_nodes(changed)[0]['fields'][2:])
        self.assertEqual(tuple_nodes(changed)[0]['fields'][0], '4')

    def test_refuses_an_already_augmented_root(self):
        with self.assertRaises(ValueError):
            add_panels(add_panels(body(), 'FormCommandBar'), 'FormCommandBar')

    def test_refuses_an_unmeasured_root_revision(self):
        with self.assertRaises(ValueError):
            add_panels(body().replace('{59,', '{60,', 1), 'FormCommandBar')

    def test_refuses_a_non_native_bar_identity(self):
        with self.assertRaises(ValueError):
            add_panels(body().replace('{-1,' + CLASS_ID, '{7,' + CLASS_ID), 'FormCommandBar')

    def test_unclosed_quoted_input_is_refused(self):
        with self.assertRaises(ValueError):
            tuple_nodes('{4,"unterminated}')

if __name__ == '__main__':
    unittest.main()

from cStringIO import StringIO
from textwrap import dedent
from tempfile import NamedTemporaryFile
import json
import sys

import unittest2 as unittest

from cfn_pyplates import cli, core, functions

try:
    from mock import patch
    mock_error = None
except ImportError:
    patch = None
    mock_error = 'skipped -- install mock to run this test'

class TestResource(core.JSONableDict):
    pass

class JSONableDictTestCase(unittest.TestCase):
    def test_crazy_addchild_ordering(self):
        # We should be able to add children to any element at any time,
        # before or after that element has been added to a parent.
        bm = core.JSONableDict()
        tr1 = TestResource({'Id': 1})
        tr2 = TestResource({'Id': 2})
        # Tack the TestResource onto the template
        bm.add(tr1)
        # Add a child to tr1 *after* adding it to bm
        tr1.add(tr2)
        # Then make sure tr2 is visible from the template
        self.assertIn('TestResource', bm['TestResource'])
        # Double check Ids...
        self.assertEqual(bm['TestResource']['Id'], 1)
        self.assertEqual(bm['TestResource']['TestResource']['Id'], 2)

        # Now mess with the nested child, and make sure those changes
        # are still seen in the template
        tr2.update({'This Is': 'A Test'})
        self.assertEqual(bm['TestResource']['TestResource']['This Is'], 'A Test')

    def test_contructor_dict_arg(self):
        # Making sure that a resource instantiated with a params dict is updated correctly
        update_dict = {'Test': 'A custom custructydict'}
        bm = core.JSONableDict()
        bm.add(TestResource(update_dict))
        self.assertEqual(bm['TestResource'], update_dict)

    def test_naming(self):
        class NamedClass(core.JSONableDict): pass

        obj = NamedClass(name='NormalName')
        # Get works?
        self.assertEqual(obj.name, 'NormalName')

        # Set works?
        obj.name = 'DifferentName'
        self.assertEqual(obj.name, 'DifferentName')

        # Del works?
        del(obj.name)
        self.assertEqual(obj.name, 'NamedClass')
        self.assertIsNone(obj._name)

    def test_getsetattr(self):
        bm = core.JSONableDict()

        self.assertNotIn('TestResource', bm)
        bm.test_resource = TestResource()
        self.assertIn('TestResource', bm)
        del(bm.test_resource)
        self.assertNotIn('TestResource', bm)


class CloudFormationTemplateTestCase(unittest.TestCase):
    def test_has_template_attrs(self):
        description = 'Test Description!'
        cft = core.CloudFormationTemplate(description)
        self.assertEqual(cft['Description'], description)

        self.assertIn('Parameters', cft)
        self.assertIsInstance(cft.parameters, core.Parameters)

        self.assertIn('Mappings', cft)
        self.assertIsInstance(cft.mappings, core.Mappings)

        self.assertIn('Resources', cft)
        self.assertIsInstance(cft.resources, core.Resources)

        self.assertIn('Outputs', cft)
        self.assertIsInstance(cft.outputs, core.Outputs)

    def test_no_description(self):
        cft = core.CloudFormationTemplate()
        self.assertNotIn('Description', cft)

    def test_jsonification(self):
        cft = core.CloudFormationTemplate('This is a test')
        cft.parameters.update({'These': 'are awesome!'})

        # We expect to see the AWSTemplateFormatVersion, the provided
        # description, and the provided parameters. We do not expect to
        # see empty Mappings, Resources, or Outputs.
        expected_out = dedent(u'''\
        {
          "AWSTemplateFormatVersion": "2010-09-09",
          "Description": "This is a test",
          "Parameters": {
            "These": "are awesome!"
          }
        }''')
        self.assertEqual(unicode(cft), expected_out)


class ResourcesTestCase(unittest.TestCase):
    def test_resource(self):
        # No properties for this one, just make sure the resource comes
        # out right
        cft = core.CloudFormationTemplate()
        cft.resources.test = core.Resource('TestResource', 'AWS::Resource::Test')

        # Should have a new 'TestResource' key in our template resources
        self.assertIn('TestResource', cft.resources)

        # And it should look like this...
        expected_out = dedent(u'''\
        {
          "Type": "AWS::Resource::Test"
        }''')
        self.assertEqual(unicode(cft.resources.test), expected_out)

    def test_resource_with_properties(self):
        properties_dict = {'Key1': 'Value1', 'Key2': 'Value2'}
        properties = core.Properties(properties_dict)
        res = core.Resource('TestResource', 'AWS::Resource::Test', properties)
        cft = core.CloudFormationTemplate()
        cft.resources.test = res

        # The output should have the properties attached
        expected_out = dedent(u'''\
        {
          "Type": "AWS::Resource::Test",
          "Properties": {
            "Key2": "Value2",
            "Key1": "Value1"
          }
        }''')
        self.assertEqual(unicode(cft.resources.test), expected_out)



@unittest.skipIf(patch is None, mock_error)
class CLITestCase(unittest.TestCase):
    def setUp(self):
        # Patch out argv, stdin, and stdout so that we can do some
        # useful things, like:
        # - Fake arguments to be used by a CLI function
        # - Write to stding, simulating user input
        # - Suppress stdout to hide prompts during the test run
        argv_patcher = patch('sys.argv')
        argv_patcher.start()
        self.addCleanup(argv_patcher.stop)
        stdin_patcher = patch('sys.stdin', new=StringIO())
        stdin_patcher.start()
        self.addCleanup(stdin_patcher.stop)
        stdout_patcher = patch('sys.stdout', new=StringIO())
        stdout_patcher.start()
        self.addCleanup(stdout_patcher.stop)

    def test_generate(self):
        # Make a pyplate that uses the options mapping
        pyplate_contents = dedent(u'''\
        cft = CloudFormationTemplate('This is a test')
        cft.parameters.update({
            'Exists': options['ThisKeyExists'],
            'DoesNotExist': options['ThisKeyDoesNotExist']
        })''')
        pyplate = NamedTemporaryFile()
        pyplate.write(pyplate_contents)
        pyplate.flush()

        # Now make an options mapping with only one of those keys in it
        # to simultaneously test options interpolation and
        # user-prompted input
        options_mapping_contents = dedent(u'''\
        {
            'ThisKeyExists': true
        }
        ''')
        options_mapping = NamedTemporaryFile()
        options_mapping.write(options_mapping_contents)
        options_mapping.flush()

        # The outfile which will receive the rendered json
        outfile = NamedTemporaryFile()

        # Populate sys.argv with something reasonable based on all the
        # tempfiles. On the command line this would look like
        # "cfn_py_generate pyplate outfile -o options_mapping"
        sys.argv = ['cfn_py_generate', pyplate.name, outfile.name,
            '-o', options_mapping.name]
        # Prime stdin with the answer to our interactive question
        input = 'Test'
        sys.stdin.write('{0}\n'.format(input))
        sys.stdin.seek(0)

        # Run the command, catch it if it tries to exit the interpreter
        return_code = cli.generate()
        if return_code != 0:
            sys.stdout.seek(0)
            message = sys.stdout.read()
            self.fail('generate failed, stdout dump follows:\n{0}'.format(
                message)
            )

        expected_output = dedent(u'''\
        {
          "AWSTemplateFormatVersion": "2010-09-09",
          "Description": "This is a test",
          "Parameters": {
            "DoesNotExist": "this is a test",
            "Exists": true
        }
        ''')

        # Load the output back into python for assertions
        output = json.load(outfile)

        # The Exists parameter should evaluate to bool True...
        # If so, then options_mapping interpolation works
        self.assertTrue(output['Parameters']['Exists'])

        # The DoesNotExist parameter should be what was injected to stdin
        # If so, then prompts to populate missing options_mapping entries work
        self.assertEqual(output['Parameters']['DoesNotExist'], input)

if __name__ == '__main__':
    unittest.main()


# pylint: disable=missing-docstring
from __future__ import absolute_import, division, print_function, unicode_literals

import six
from mock import MagicMock, patch

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError

from resolwe.flow.models import Collection, Data, DescriptorSchema, Entity, Process, Storage
from resolwe.flow.models.utils import validate_schema
from resolwe.test import TestCase


class ValidationTest(TestCase):

    def setUp(self):
        super(ValidationTest, self).setUp()

        self.user = get_user_model().objects.create(username='test_user')

    def test_validating_data_object(self):
        """Diferent validations are performed depending on status"""
        proc = Process.objects.create(
            name='Test process',
            contributor=self.user,
            input_schema=[
                {'name': 'value', 'type': 'basic:integer:', 'required': True}
            ],
            output_schema=[
                {'name': 'result', 'type': 'basic:string:', 'required': True}
            ]
        )

        data = {
            'name': 'Test data',
            'contributor': self.user,
            'process': proc,
        }

        with six.assertRaisesRegex(self, ValidationError, '"value" not given'):
            Data.objects.create(input={}, **data)

        with six.assertRaisesRegex(self, ValidationError, 'Required fields .* not given'):
            Data.objects.create(input={}, **data)

        d = Data.objects.create(input={'value': 42}, **data)

        d.status = Data.STATUS_DONE
        with six.assertRaisesRegex(self, ValidationError, '"result" not given'):
            d.save()

        d.output = {'result': 'forty-two'}
        d.save()

    def test_validate_data_descriptor(self):
        proc = Process.objects.create(name='Test process', contributor=self.user)
        descriptor_schema = DescriptorSchema.objects.create(
            name='Test descriptor schema',
            contributor=self.user,
            schema=[
                {'name': 'description', 'type': 'basic:string:', 'required': True}
            ]
        )

        data = Data.objects.create(
            name='Test descriptor',
            contributor=self.user,
            process=proc,
            descriptor={},
            descriptor_schema=descriptor_schema
        )
        self.assertEqual(data.descriptor_dirty, True)

        data.descriptor = {'description': 'some value'}
        data.save()
        self.assertEqual(data.descriptor_dirty, False)

        data.descriptor = {}
        data.save()
        self.assertEqual(data.descriptor_dirty, True)

        data.descriptor = {'description': 42}
        with six.assertRaisesRegex(self, ValidationError, 'is not valid'):
            data.save()

    def test_validate_collection_descriptor(self):  # pylint: disable=invalid-name
        descriptor_schema = DescriptorSchema.objects.create(
            name='Test descriptor schema',
            contributor=self.user,
            schema=[
                {'name': 'description', 'type': 'basic:string:', 'required': True}
            ]
        )

        collection = Collection.objects.create(
            name='Test descriptor',
            contributor=self.user,
            descriptor_schema=descriptor_schema
        )
        self.assertEqual(collection.descriptor_dirty, True)

        collection.descriptor = {'description': 'some value'}
        collection.save()
        self.assertEqual(collection.descriptor_dirty, False)

        collection.descriptor = {}
        collection.save()
        self.assertEqual(collection.descriptor_dirty, True)

        collection.descriptor = {'description': 42}
        with six.assertRaisesRegex(self, ValidationError, 'is not valid'):
            collection.save()

    def test_validate_entity_descriptor(self):
        descriptor_schema = DescriptorSchema.objects.create(
            name='Test descriptor schema',
            contributor=self.user,
            schema=[
                {'name': 'description', 'type': 'basic:string:', 'required': True}
            ]
        )

        entity = Entity.objects.create(
            name='Test descriptor',
            contributor=self.user,
            descriptor_schema=descriptor_schema
        )
        self.assertEqual(entity.descriptor_dirty, True)

        entity.descriptor = {'description': 'some value'}
        entity.save()
        self.assertEqual(entity.descriptor_dirty, False)

        entity.descriptor = {}
        entity.save()
        self.assertEqual(entity.descriptor_dirty, True)

        entity.descriptor = {'description': 42}
        with six.assertRaisesRegex(self, ValidationError, 'is not valid'):
            entity.save()

    def test_referenced_storage(self):
        proc = Process.objects.create(
            name='Test process',
            contributor=self.user,
            output_schema=[
                {'name': 'big_result', 'type': 'basic:json:', 'required': True}
            ]
        )

        data = {
            'name': 'Test data',
            'contributor': self.user,
            'process': proc,
        }

        d = Data.objects.create(**data)
        d.status = Data.STATUS_DONE

        # `Data` object with referenced non-existing `Storage`
        d.output = {'big_result': 245}
        with six.assertRaisesRegex(self, ValidationError, '`Storage` object does not exist'):
            d.save()

        storage = Storage.objects.create(
            name="storage",
            contributor=self.user,
            data_id=d.pk,
            json={'value': 42}
        )
        d.output = {'big_result': storage.pk}
        d.save()

    def test_referenced_data(self):
        proc1 = Process.objects.create(
            name='Referenced process',
            contributor=self.user,
            type='data:referenced:object:'
        )
        proc2 = Process.objects.create(
            name='Test process',
            contributor=self.user,
            input_schema=[
                {'name': 'data_object', 'type': 'data:referenced:object:'}
            ]
        )
        d = Data.objects.create(
            name='Referenced object',
            contributor=self.user,
            process=proc1
        )

        data = {
            'name': 'Test data',
            'contributor': self.user,
            'process': proc2,
            'input': {'data_object': d.pk}
        }

        Data.objects.create(**data)

        # less specific type
        proc2.input_schema = [
            {'name': 'data_object', 'type': 'data:referenced:'}
        ]
        Data.objects.create(**data)

        # wrong type
        proc2.input_schema = [
            {'name': 'data_object', 'type': 'data:wrong:type:'}
        ]
        with six.assertRaisesRegex(self, ValidationError, 'Data object of type .* is required'):
            Data.objects.create(**data)

        # non-existing `Data` object
        data['input'] = {'data_object': 631}
        with six.assertRaisesRegex(self, ValidationError, '`Data` object does not exist'):
            Data.objects.create(**data)

    def test_delete_input(self):
        proc1 = Process.objects.create(
            name='Referenced process',
            contributor=self.user,
            type='data:referenced:object:'
        )
        proc2 = Process.objects.create(
            name='Test process',
            contributor=self.user,
            input_schema=[
                {'name': 'data_object', 'type': 'data:referenced:object:'}
            ]
        )
        data1 = Data.objects.create(
            name='Referenced object',
            contributor=self.user,
            process=proc1
        )
        data2 = Data.objects.create(
            name='Test data',
            contributor=self.user,
            process=proc2,
            input={'data_object': data1.pk}
        )

        data1.delete()
        data2.name = 'New name'
        data2.save()


class ValidationUnitTest(TestCase):

    def test_required(self):
        schema = [
            {'name': 'value', 'type': 'basic:integer:', 'required': True},
            {'name': 'description', 'type': 'basic:string:'},  # implicit `required=True`
            {'name': 'comment', 'type': 'basic:string:', 'required': False},
        ]

        instance = {'description': 'test'}
        with six.assertRaisesRegex(self, ValidationError, '"value" not given.'):
            validate_schema(instance, schema)

        instance = {'value': 42}
        with six.assertRaisesRegex(self, ValidationError, '"description" not given.'):
            validate_schema(instance, schema)

        instance = {'value': 42, 'description': 'universal answer'}
        validate_schema(instance, schema)

        instance = {'value': 42, 'description': 'universal answer', 'comment': None}
        validate_schema(instance, schema)

        instance = {'value': 42, 'description': 'test', 'comment': 'Lorem ipsum'}
        validate_schema(instance, schema)

        instance = {}
        validate_schema(instance, schema, test_required=False)

    def test_choices(self):
        schema = [
            {
                'name': 'value',
                'type': 'basic:integer:',
                'choices': [{'value': 7, 'label': '7'}, {'value': 13, 'label': '13'}]
            },
        ]

        instance = {'value': 7}
        validate_schema(instance, schema)

        instance = {'value': 8}
        error_msg = "Value of field 'value' must match one of predefined choices. Current value: 8"
        with six.assertRaisesRegex(self, ValidationError, error_msg):
            validate_schema(instance, schema)

        schema = [
            {
                'name': 'value',
                'type': 'basic:integer:',
                'choices': [{'value': 7, 'label': '7'}, {'value': 13, 'label': '13'}],
                'allow_custom_choice': True
            },
        ]

        instance = {'value': 7}
        validate_schema(instance, schema)

        instance = {'value': 8}
        validate_schema(instance, schema)

    def test_missing_in_schema(self):
        schema = [
            {'name': 'result', 'type': 'basic:integer:', 'required': False}
        ]

        instance = {'res': 42}
        with six.assertRaisesRegex(self, ValidationError, r'\(res\) missing in schema'):
            validate_schema(instance, schema)

    def test_file_prefix(self):
        schema = [
            {'name': 'result', 'type': 'basic:file:'},
        ]
        instance = {'result': {'file': 'result.txt'}}

        with patch('resolwe.flow.models.utils.os') as os_mock:
            validate_schema(instance, schema)
            self.assertEqual(os_mock.path.isfile.call_count, 0)

        # missing file
        with patch('resolwe.flow.models.utils.os') as os_mock:
            os_mock.path.isfile = MagicMock(return_value=False)
            with six.assertRaisesRegex(self, ValidationError, 'Referenced file .* does not exist'):
                validate_schema(instance, schema, path_prefix='/home/genialis/')
            self.assertEqual(os_mock.path.isfile.call_count, 1)

        with patch('resolwe.flow.models.utils.os') as os_mock:
            os_mock.path.isfile = MagicMock(return_value=True)
            validate_schema(instance, schema, path_prefix='/home/genialis/')
            self.assertEqual(os_mock.path.isfile.call_count, 1)

        instance = {'result': {'file': 'result.txt', 'refs': ['user1.txt', 'user2.txt']}}

        with patch('resolwe.flow.models.utils.os') as os_mock:
            os_mock.path.isfile = MagicMock(return_value=True)
            validate_schema(instance, schema, path_prefix='/home/genialis/')
            self.assertEqual(os_mock.path.isfile.call_count, 3)

        # missing second `refs` file
        with patch('resolwe.flow.models.utils.os') as os_mock:
            os_mock.path.isfile = MagicMock(side_effect=[True, True, False])
            os_mock.path.isdir = MagicMock(return_value=False)
            with six.assertRaisesRegex(self, ValidationError,
                                       'File referenced in `refs` .* does not exist'):
                validate_schema(instance, schema, path_prefix='/home/genialis/')
            self.assertEqual(os_mock.path.isfile.call_count, 3)
            self.assertEqual(os_mock.path.isdir.call_count, 1)

    def test_dir_prefix(self):
        schema = [
            {'name': 'result', 'type': 'basic:dir:'},
        ]
        instance = {'result': {'dir': 'results'}}

        # dir validation is not called if `path_prefix` is not given
        with patch('resolwe.flow.models.utils.os') as os_mock:
            validate_schema(instance, schema)
            self.assertEqual(os_mock.path.isdir.call_count, 0)

        with patch('resolwe.flow.models.utils.os') as os_mock:
            os_mock.path.isdir = MagicMock(return_value=True)
            validate_schema(instance, schema, path_prefix='/home/genialis/')
            self.assertEqual(os_mock.path.isdir.call_count, 1)

        # missing dir
        with patch('resolwe.flow.models.utils.os') as os_mock:
            os_mock.path.isdir = MagicMock(return_value=False)
            with six.assertRaisesRegex(self, ValidationError, 'Referenced dir .* does not exist'):
                validate_schema(instance, schema, path_prefix='/home/genialis/')
            self.assertEqual(os_mock.path.isdir.call_count, 1)

        instance = {'result': {'dir': 'results', 'refs': ['file01.txt', 'file02.txt']}}

        with patch('resolwe.flow.models.utils.os') as os_mock:
            os_mock.path.isdir = MagicMock(return_value=True)
            os_mock.path.isfile = MagicMock(return_value=True)
            validate_schema(instance, schema, path_prefix='/home/genialis/')
            self.assertEqual(os_mock.path.isdir.call_count, 1)
            self.assertEqual(os_mock.path.isfile.call_count, 2)

        # missing second `refs` file
        with patch('resolwe.flow.models.utils.os') as os_mock:
            os_mock.path.isdir = MagicMock(side_effect=[True, False])
            os_mock.path.isfile = MagicMock(side_effect=[True, False])
            with six.assertRaisesRegex(self, ValidationError,
                                       'File referenced in `refs` .* does not exist'):
                validate_schema(instance, schema, path_prefix='/home/genialis/')
            self.assertEqual(os_mock.path.isdir.call_count, 2)
            self.assertEqual(os_mock.path.isfile.call_count, 2)

    def test_string_field(self):
        schema = [
            {'name': 'string', 'type': 'basic:string:'},
            {'name': 'text', 'type': 'basic:text:'},
        ]

        instance = {'string': 'Test string', 'text': 'Test text'}
        validate_schema(instance, schema)

        instance = {'string': 42, 'text': 42}
        with six.assertRaisesRegex(self, ValidationError, 'is not valid'):
            validate_schema(instance, schema)

    def test_boolean_field(self):
        schema = [
            {'name': 'true', 'type': 'basic:boolean:'},
            {'name': 'false', 'type': 'basic:boolean:'},
        ]

        instance = {'true': True, 'false': False}
        validate_schema(instance, schema)

        instance = {'true': 'true', 'false': 'false'}
        with six.assertRaisesRegex(self, ValidationError, 'is not valid'):
            validate_schema(instance, schema)

        instance = {'true': 1, 'false': 0}
        with six.assertRaisesRegex(self, ValidationError, 'is not valid'):
            validate_schema(instance, schema)

        instance = {'true': 'foo', 'false': 'bar'}
        with six.assertRaisesRegex(self, ValidationError, 'is not valid'):
            validate_schema(instance, schema)

    def test_integer_field(self):
        schema = [
            {'name': 'value', 'type': 'basic:integer:'},
        ]

        instance = {'value': 42}
        validate_schema(instance, schema)

        instance = {'value': 42.0}
        with six.assertRaisesRegex(self, ValidationError, 'is not valid'):
            validate_schema(instance, schema)

        instance = {'value': 'forty-two'}
        with six.assertRaisesRegex(self, ValidationError, 'is not valid'):
            validate_schema(instance, schema)

    def test_decimal_field(self):
        schema = [
            {'name': 'value', 'type': 'basic:decimal:'},
        ]

        instance = {'value': 42}
        validate_schema(instance, schema)

        instance = {'value': 42.0}
        validate_schema(instance, schema)

        instance = {'value': 'forty-two'}
        with six.assertRaisesRegex(self, ValidationError, 'is not valid'):
            validate_schema(instance, schema)

    def test_date_field(self):
        schema = [
            {'name': 'date', 'type': 'basic:date:'},
        ]

        instance = {'date': '2000-12-31'}
        validate_schema(instance, schema)

        instance = {'date': '2000/01/01'}
        with six.assertRaisesRegex(self, ValidationError, 'is not valid'):
            validate_schema(instance, schema)

        instance = {'date': '31 04 2000'}
        with six.assertRaisesRegex(self, ValidationError, 'is not valid'):
            validate_schema(instance, schema)

        instance = {'date': '21.06.2000'}
        with six.assertRaisesRegex(self, ValidationError, 'is not valid'):
            validate_schema(instance, schema)

        instance = {'date': '2000-1-1'}
        with six.assertRaisesRegex(self, ValidationError, 'is not valid'):
            validate_schema(instance, schema)

        instance = {'date': '2000 apr 8'}
        with six.assertRaisesRegex(self, ValidationError, 'is not valid'):
            validate_schema(instance, schema)

    def test_datetime_field(self):
        schema = [
            {'name': 'date', 'type': 'basic:datetime:'},
        ]

        instance = {'date': '2000-06-21 00:00'}
        validate_schema(instance, schema)

        instance = {'date': '2000 06 21 24:00'}
        with self.assertRaises(ValidationError):
            validate_schema(instance, schema)

        instance = {'date': '2000/06/21 2:03'}
        with six.assertRaisesRegex(self, ValidationError, 'is not valid'):
            validate_schema(instance, schema)

        instance = {'date': '2000-06-21 2:3'}  # XXX: Is this ok?
        validate_schema(instance, schema)

        instance = {'date': '2000-06-21'}
        with six.assertRaisesRegex(self, ValidationError, 'is not valid'):
            validate_schema(instance, schema)

        instance = {'date': '2000-06-21 12pm'}
        with six.assertRaisesRegex(self, ValidationError, 'is not valid'):
            validate_schema(instance, schema)

    def test_data_field(self):
        schema = [
            {'name': 'data_list', 'type': 'data:test:upload:'}
        ]
        instance = {
            'data_list': 1
        }

        with patch('resolwe.flow.models.data.Data') as data_mock:
            value_mock = MagicMock(**{
                'exists.return_value': True,
                'first.return_value': {'process__type': 'data:test:upload:'},
            })
            filter_mock = MagicMock(**{'values.return_value': value_mock})
            data_mock.objects.filter.return_value = filter_mock

            validate_schema(instance, schema)

            self.assertEqual(value_mock.exists.call_count, 1)
            self.assertEqual(value_mock.first.call_count, 1)

        # subtype is OK
        with patch('resolwe.flow.models.data.Data') as data_mock:
            value_mock = MagicMock(**{
                'exists.return_value': True,
                'first.return_value': {'process__type': 'data:test:upload:subtype:'},
            })
            filter_mock = MagicMock(**{'values.return_value': value_mock})
            data_mock.objects.filter.return_value = filter_mock

            validate_schema(instance, schema)

            self.assertEqual(value_mock.exists.call_count, 1)
            self.assertEqual(value_mock.first.call_count, 1)

        # missing `Data` object
        with patch('resolwe.flow.models.data.Data') as data_mock:
            value_mock = MagicMock(**{
                'exists.return_value': False,
                'first.return_value': {'process__type': 'data:test:upload:'},
            })
            filter_mock = MagicMock(**{'values.return_value': value_mock})
            data_mock.objects.filter.return_value = filter_mock

            with six.assertRaisesRegex(self, ValidationError, '`Data` object does not exist'):
                validate_schema(instance, schema)

            self.assertEqual(value_mock.exists.call_count, 1)
            self.assertEqual(value_mock.first.call_count, 0)

        # `Data` object of wrong type
        with patch('resolwe.flow.models.data.Data') as data_mock:
            value_mock = MagicMock(**{
                'exists.return_value': True,
                'first.return_value': {'process__type': 'data:test:wrong:'},
            })
            filter_mock = MagicMock(**{'values.return_value': value_mock})
            data_mock.objects.filter.return_value = filter_mock

            with six.assertRaisesRegex(self, ValidationError, 'Data object of type .* is required'):
                validate_schema(instance, schema)

            self.assertEqual(value_mock.exists.call_count, 1)
            self.assertEqual(value_mock.first.call_count, 1)

        # data `id` shouldn't be string
        instance = {
            'data_list': "1"
        }

        with six.assertRaisesRegex(self, ValidationError, 'is not valid'):
            validate_schema(instance, schema)

    def test_file_field(self):
        schema = [
            {'name': 'result', 'type': 'basic:file:', 'validate_regex': r'^.*\.txt$'},
        ]

        instance = {'result': {
            'file': 'result_file.txt',
            'size': 13
        }}
        validate_schema(instance, schema)

        instance = {'result': {
            'file_temp': '12345',
            'file': 'result_file.txt',
        }}
        validate_schema(instance, schema)

        instance = {'result': {
            'file_temp': '12345',
            'is_remote': True,
            'file': 'result_file.txt',
        }}
        validate_schema(instance, schema)

        instance = {'result': {
            'file': 'result_file.txt',
            'refs': ['01.txt', '02.txt'],
        }}
        validate_schema(instance, schema)

        # non-boolean `is_remote`
        instance = {'result': {
            'file_temp': '12345',
            'is_remote': 'ftp',
            'file': 'result_file.txt',
        }}
        with six.assertRaisesRegex(self, ValidationError, 'is not valid'):
            validate_schema(instance, schema)

        # missing `file`
        instance = {'result': {
            'file_temp': '12345',
        }}
        with six.assertRaisesRegex(self, ValidationError, 'is not valid'):
            validate_schema(instance, schema)

        # wrong file extension
        instance = {'result': {
            'file': 'result_file.tar.gz',
        }}
        with six.assertRaisesRegex(self, ValidationError, 'File name .* does not match regex'):
            validate_schema(instance, schema)

    def test_html_file_field(self):
        schema = [
            {'name': 'html_result', 'type': 'basic:file:html:'},
        ]

        instance = {'html_result': {
            'file': 'index.htmls',
            'refs': ['some.js', 'some.css']
        }}
        validate_schema(instance, schema)

    def test_dir_field(self):
        schema = [
            {'name': 'result', 'type': 'basic:dir:'},
        ]

        instance = {'result': {
            'dir': 'results',
            'size': 32156
        }}
        validate_schema(instance, schema)

        instance = {'result': {
            'dir': 'result',
            'refs': ['01.txt', '02.txt']
        }}
        validate_schema(instance, schema)

        # missing `dir`
        instance = {'result': {
            'refs': ['01.txt', '02.txt'],
        }}
        with six.assertRaisesRegex(self, ValidationError, 'is not valid'):
            validate_schema(instance, schema)

    def test_url_field(self):
        schema = [
            {'name': 'webpage', 'type': 'basic:url:view:'},
        ]

        instance = {'webpage': {'url': 'http://www.genialis.com'}}
        validate_schema(instance, schema)

        instance = {'webpage': {
            'url': 'http://www.genialis.com',
            'name': 'Genialis',
            'refs': ['http://www.genialis.com/jobs']
        }}
        validate_schema(instance, schema)

        # wrong type
        schema = [
            {'name': 'webpage', 'type': 'basic:url:'},
        ]
        instance = {'webpage': {'url': 'http://www.genialis.com'}}
        with six.assertRaisesRegex(self, ValidationError, 'is not valid'):
            validate_schema(instance, schema)

    def test_json_field(self):
        schema = [
            {'name': 'big_dict', 'type': 'basic:json:'}
        ]

        # json not saved in `Storage`
        instance = {'big_dict': {'foo': 'bar'}}
        with six.assertRaisesRegex(self, ValidationError, 'is not valid'):
            validate_schema(instance, schema)

        with patch('resolwe.flow.models.utils.Storage') as storage_mock:
            filter_mock = MagicMock()
            filter_mock.exists.return_value = True
            storage_mock.objects.filter.return_value = filter_mock

            instance = {'big_dict': 5}
            validate_schema(instance, schema)

            self.assertEqual(filter_mock.exists.call_count, 1)

        # non existing `Storage`
        with patch('resolwe.flow.models.utils.Storage') as storage_mock:
            filter_mock = MagicMock()
            filter_mock.exists.return_value = False
            storage_mock.objects.filter.return_value = filter_mock

            instance = {'big_dict': 5}
            with six.assertRaisesRegex(self, ValidationError, '`Storage` object does not exist'):
                validate_schema(instance, schema)

            self.assertEqual(filter_mock.exists.call_count, 1)

    def test_list_string_field(self):
        schema = [
            {'name': 'list', 'type': 'list:basic:string:'}
        ]

        instance = {'list': ['foo', 'bar']}
        validate_schema(instance, schema)

        instance = {'list': []}
        validate_schema(instance, schema)

        instance = {'list': ''}
        with six.assertRaisesRegex(self, ValidationError, 'is not valid'):
            validate_schema(instance, schema)

        instance = {'list': 'foo bar'}
        with six.assertRaisesRegex(self, ValidationError, 'is not valid'):
            validate_schema(instance, schema)

    def test_list_integer_field(self):
        schema = [
            {'name': 'value', 'type': 'list:basic:integer:'},
        ]

        instance = {'value': [42, 43]}
        validate_schema(instance, schema)

        instance = {'value': 42}
        with six.assertRaisesRegex(self, ValidationError, 'is not valid'):
            validate_schema(instance, schema)

        instance = {'value': [42, 43.0]}
        with six.assertRaisesRegex(self, ValidationError, 'is not valid'):
            validate_schema(instance, schema)

        instance = {'value': [42, "43"]}
        with six.assertRaisesRegex(self, ValidationError, 'is not valid'):
            validate_schema(instance, schema)

    def test_list_data_field(self):
        schema = [
            {'name': 'data_list', 'type': 'list:data:test:upload:'}
        ]
        instance = {
            'data_list': [1, 3, 4]
        }

        with patch('resolwe.flow.models.data.Data') as data_mock:
            value_mock = MagicMock(**{
                'exists.return_value': True,
                'first.return_value': {'process__type': 'data:test:upload:'},
            })
            filter_mock = MagicMock(**{'values.return_value': value_mock})
            data_mock.objects.filter.return_value = filter_mock

            validate_schema(instance, schema)

            self.assertEqual(value_mock.exists.call_count, 3)
            self.assertEqual(value_mock.first.call_count, 3)

        # subtypes are OK
        with patch('resolwe.flow.models.data.Data') as data_mock:
            value_mock = MagicMock(**{
                'exists.return_value': True,
                'first.side_effect': [
                    {'process__type': 'data:test:upload:subtype1:'},
                    {'process__type': 'data:test:upload:'},
                    {'process__type': 'data:test:upload:subtype2:'},
                ],
            })
            filter_mock = MagicMock(**{'values.return_value': value_mock})
            data_mock.objects.filter.return_value = filter_mock

            validate_schema(instance, schema)

            self.assertEqual(value_mock.exists.call_count, 3)
            self.assertEqual(value_mock.first.call_count, 3)

        # one object does not exist
        with patch('resolwe.flow.models.data.Data') as data_mock:
            value_mock = MagicMock(**{
                'exists.side_effect': [True, False, True],
                'first.return_value': {'process__type': 'data:test:upload:'},
            })
            filter_mock = MagicMock(**{'values.return_value': value_mock})
            data_mock.objects.filter.return_value = filter_mock

            with six.assertRaisesRegex(self, ValidationError, '`Data` object does not exist'):
                validate_schema(instance, schema)

            self.assertEqual(value_mock.exists.call_count, 2)
            self.assertEqual(value_mock.first.call_count, 1)

        # one object of wrong type
        with patch('resolwe.flow.models.data.Data') as data_mock:
            value_mock = MagicMock(**{
                'exists.return_value': True,
                'first.side_effect': [
                    {'process__type': 'data:test:upload:'},
                    {'process__type': 'data:test:upload:'},
                    {'process__type': 'data:test:wrong:'},
                ],
            })
            filter_mock = MagicMock(**{'values.return_value': value_mock})
            data_mock.objects.filter.return_value = filter_mock

            with six.assertRaisesRegex(self, ValidationError, 'Data object of type .* is required'):
                validate_schema(instance, schema)

            self.assertEqual(value_mock.exists.call_count, 3)
            self.assertEqual(value_mock.first.call_count, 3)

    def test_list_file_field(self):
        schema = [
            {'name': 'result', 'type': 'list:basic:file:', 'validate_regex': r'^.*\.txt$'},
        ]
        instance = {'result': [
            {'file': 'result01.txt'},
            {'file': 'result02.txt', 'size': 14, 'refs': ['results.tar.gz']},
        ]}
        validate_schema(instance, schema)

        # wrong extension
        instance = {'result': [
            {'file': 'result01.txt'},
            {'file': 'result02.tar.gz'},
        ]}
        with six.assertRaisesRegex(self, ValidationError, 'File name .* does not match regex'):
            validate_schema(instance, schema)

    def test_list_dir_field(self):
        schema = [
            {'name': 'result', 'type': 'list:basic:dir:'},
        ]

        instance = {'result': [
            {'dir': 'results01', 'size': 32156, 'refs': ['result01.txt', 'result02.txt']},
            {'dir': 'results02'},
        ]}
        validate_schema(instance, schema)

        # missing `dir`
        instance = {'result': [
            {'size': 32156, 'refs': ['result01.txt', 'result02.txt']},
        ]}
        with six.assertRaisesRegex(self, ValidationError, 'is not valid'):
            validate_schema(instance, schema)

    def test_list_url_field(self):
        schema = [
            {'name': 'webpage', 'type': 'list:basic:url:view:'},
        ]

        instance = {'webpage': [
            {'url': 'http://www.genialis.com', 'refs': ['http://www.genialis.com/jobs']},
            {'url': 'http://www.dictyexpress.org'},
        ]}
        validate_schema(instance, schema)

        instance = {'webpage': {'url': 'http://www.dictyexpress.org'}}
        with six.assertRaisesRegex(self, ValidationError, 'is not valid'):
            validate_schema(instance, schema)

    def test_groups(self):
        schema = [
            {'name': 'test_group', 'group': [
                {'name': 'result_file', 'type': 'basic:file:', 'validate_regex': r'^.*\.txt$'},
                {'name': 'description', 'type': 'basic:string:', 'required': True}
            ]}
        ]

        instance = {'test_group': {
            'result_file': {'file': 'results.txt'},
            'description': 'This are results',
        }}
        validate_schema(instance, schema)

        # wrong file extension
        instance = {'test_group': {
            'result_file': {'file': 'results.tar.gz'},
            'description': 'This are results',
        }}
        with six.assertRaisesRegex(self, ValidationError, 'File name .* does not match regex'):
            validate_schema(instance, schema)

        # wrong description type
        instance = {'test_group': {
            'result_file': {'file': 'results.txt'},
            'description': 6,
        }}
        with six.assertRaisesRegex(self, ValidationError, 'is not valid'):
            validate_schema(instance, schema)

        # missing description
        instance = {'test_group': {
            'result_file': {'file': 'results.txt'},
        }}
        with six.assertRaisesRegex(self, ValidationError, '"description" not given'):
            validate_schema(instance, schema)

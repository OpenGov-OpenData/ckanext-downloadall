import builtins
import zipfile
import json
import tempfile
import re
import copy

import mock
import pytest
from pyfakefs import fake_filesystem
import responses
import requests

from ckan.common import config
from ckan.tests import factories, helpers
import ckan.lib.uploader
from ckanext.downloadall.tasks import (
    update_zip, canonized_datapackage, save_local_path_in_datapackage_resource,
    hash_datapackage, generate_datapackage_json, populate_schema_from_datastore)
from ckanext.downloadall.tests import TestBase


# Uploads are put in this fake file system
# Copied from ckan/tests/logic/action/test_create.py
real_open = open
fs = fake_filesystem.FakeFilesystem()
fake_os = fake_filesystem.FakeOsModule(fs)
fake_open = fake_filesystem.FakeFileOpen(fs)


def mock_open_if_open_fails(*args, **kwargs):
    try:
        return real_open(*args, **kwargs)
    except (OSError, IOError):
        return fake_open(*args, **kwargs)


def mock_populate_schema_from_datastore(res, datapackage_res):
    res['datastore_fields'] = [{'type': 'int', 'id': '_id'},
                               {'type': 'text', 'id': 'Date'},
                               {'type': 'text', 'id': 'Price'}]
    populate_schema_from_datastore(res, datapackage_res)


@pytest.mark.ckan_config('ckan.plugins', 'datastore downloadall')
@mock.patch.object(ckan.lib.uploader, 'os', fake_os)
@mock.patch.object(builtins, 'open', side_effect=mock_open_if_open_fails)
class TestUpdateZip(TestBase):

    @pytest.mark.ckan_config('ckan.storage_path', '/doesnt_exist')
    @pytest.mark.usefixtures('with_request_context')
    @responses.activate
    def test_simple(self, _):
        responses.add(
            responses.GET,
            'https://example.com/data.csv',
            body='a,b,c'
        )
        responses.add_passthru(config['solr_url'])
        dataset = factories.Dataset(owner_org=self.org['id'], resources=[{
            'url': 'https://example.com/data.csv',
            'format': 'csv',
            }])

        update_zip(dataset['id'])

        dataset = helpers.call_action('package_show', id=dataset['id'])
        zip_resources = [res for res in dataset['resources']
                         if res['name'] == 'All resource data']
        assert len(zip_resources) == 1
        zip_resource = zip_resources[0]
        assert zip_resource['url_type'] == 'upload'

        uploader = ckan.lib.uploader.get_resource_uploader(zip_resource)
        filepath = uploader.get_path(zip_resource['id'])
        csv_filename_in_zip = '{}.csv'.format(dataset['resources'][0]['id'])
        with fake_open(filepath, 'rb') as f:
            with zipfile.ZipFile(f) as zip_:
                assert zip_.namelist() == [csv_filename_in_zip, 'datapackage.json']
                assert zip_.read(csv_filename_in_zip) == 'a,b,c'.encode()
                datapackage_json = zip_.read('datapackage.json').decode()
                assert '{\n  "description"' in datapackage_json
                datapackage = json.loads(datapackage_json)
                assert datapackage['name'][:12] == 'test_dataset'
                assert datapackage['title'] == 'Test Dataset'
                assert datapackage['description'] == 'Just another test dataset.'
                assert datapackage['resources'][0]['format'] == 'CSV'
                assert datapackage['resources'][0]['name'] == dataset['resources'][0]['id']
                assert datapackage['resources'][0]['path'] == csv_filename_in_zip
                assert datapackage['resources'][0]['sources'] == [{'path': 'https://example.com/data.csv',
                                                                   'title': None}]

    @pytest.mark.ckan_config('ckan.storage_path', '/doesnt_exist')
    @responses.activate
    def test_update_twice(self, _):
        responses.add(
            responses.GET,
            'https://example.com/data.csv',
            body='a,b,c'
        )
        responses.add_passthru(config['solr_url'])
        dataset = factories.Dataset(owner_org=self.org['id'], resources=[{
            'url': 'https://example.com/data.csv',
            'format': 'csv',
            }])

        update_zip(dataset['id'])
        update_zip(dataset['id'], skip_if_no_changes=False)

        # ensure a second zip hasn't been added
        dataset = helpers.call_action('package_show', id=dataset['id'])
        zip_resources = [res for res in dataset['resources']
                         if res['name'] == 'All resource data']
        assert len(zip_resources) == 1

    @pytest.mark.ckan_config('ckan.storage_path', '/doesnt_exist')
    @responses.activate
    def test_dont_skip_if_no_changes(self, _):
        # i.e. testing skip_if_no_changes=False
        responses.add(
            responses.GET,
            'https://example.com/data.csv',
            body='a,b,c'
        )
        responses.add_passthru(config['solr_url'])
        dataset = factories.Dataset(owner_org=self.org['id'], resources=[{
            'url': 'https://example.com/data.csv',
            'format': 'csv',
            }])

        update_zip(dataset['id'])
        with mock.patch('ckanext.downloadall.tasks.write_zip') as write_zip_:
            update_zip(dataset['id'], skip_if_no_changes=False)
            # ensure zip would be rewritten in this case - not letting it skip
            assert write_zip_.called

    @pytest.mark.ckan_config('ckan.storage_path', '/doesnt_exist')
    @responses.activate
    def test_update_twice_skipping_second_time(self, _):
        # i.e. testing skip_if_no_changes=False
        responses.add(
            responses.GET,
            'https://example.com/data.csv',
            body='a,b,c'
        )
        responses.add_passthru(config['solr_url'])
        dataset = factories.Dataset(owner_org=self.org['id'], resources=[{
            'url': 'https://example.com/data.csv',
            'format': 'csv',
            }])

        update_zip(dataset['id'])
        with mock.patch('ckanext.downloadall.tasks.write_zip') as write_zip_:
            update_zip(dataset['id'], skip_if_no_changes=True)
            # nothings changed, so it shouldn't rewrite the zip
            assert not write_zip_.called

    @pytest.mark.ckan_config('ckan.storage_path', '/doesnt_exist')
    @responses.activate
    def test_changing_description_causes_zip_to_update(self, _):
        responses.add(
            responses.GET,
            'https://example.com/data.csv',
            body='a,b,c'
        )
        responses.add_passthru(config['solr_url'])
        dataset = factories.Dataset(owner_org=self.org['id'], resources=[{
            'url': 'https://example.com/data.csv',
            'format': 'csv',
            }])

        update_zip(dataset['id'])
        dataset = helpers.call_action('package_patch', id=dataset['id'],
                                      notes='New notes')
        with mock.patch('ckanext.downloadall.tasks.write_zip') as write_zip_:
            update_zip(dataset['id'], skip_if_no_changes=True)
            # ensure zip would be rewritten in this case - not letting it skip
            assert write_zip_.called

    @pytest.mark.ckan_config('ckan.storage_path', '/doesnt_exist')
    @responses.activate
    def test_deleting_resource_causes_zip_to_update(self, _):
        responses.add(
            responses.GET,
            'https://example.com/data.csv',
            body='a,b,c'
        )
        responses.add_passthru(config['solr_url'])
        dataset = factories.Dataset(owner_org=self.org['id'], resources=[{
            'url': 'https://example.com/data.csv',
            'format': 'csv',
            }])

        update_zip(dataset['id'])
        dataset = helpers.call_action('package_patch', id=dataset['id'],
                                      resources=[])
        with mock.patch('ckanext.downloadall.tasks.write_zip') as write_zip_:
            update_zip(dataset['id'], skip_if_no_changes=True)
            # ensure zip would be rewritten in this case - not letting it skip
            assert write_zip_.called

    @pytest.mark.ckan_config('ckan.storage_path', '/doesnt_exist')
    @responses.activate
    def test_uploaded_resource(self, _):
        responses.add_passthru(config['solr_url'])
        csv_content = 'Test,csv'
        responses.add(
            responses.GET,
            re.compile(r'http://test.ckan.net/dataset/.*/download/.*'),
            body=csv_content
        )
        dataset = factories.Dataset(owner_org=self.org['id'])
        # add a resource which is an uploaded file
        with tempfile.NamedTemporaryFile() as fp:
            fp.write(csv_content.encode())
            fp.seek(0)
            # registry = ckanapi.LocalCKAN()
            resource = dict(
                package_id=dataset[u'id'],
                url='http://test.ckan.net/dataset/1/download/1',
                upload=fp,
                name='Rainfall',
                format='CSV'
            )
            user = helpers.call_action('get_site_user', {'ignore_auth': True})
            ctx = dict()
            ctx['user'] = user['name']
            helpers.call_action('resource_create', context=ctx, **resource)

            # registry.action.resource_create(**resource)

        update_zip(dataset['id'])

        dataset = helpers.call_action('package_show', id=dataset['id'])
        zip_resources = [res for res in dataset['resources']
                         if res['name'] == 'All resource data']
        zip_resource = zip_resources[0]
        uploader = ckan.lib.uploader.get_resource_uploader(zip_resource)
        filepath = uploader.get_path(zip_resource['id'])
        csv_filename_in_zip = 'rainfall.csv'
        with fake_open(filepath, 'rb') as f:
            with zipfile.ZipFile(f) as zip_:
                # Check uploaded file
                assert zip_.namelist() == [csv_filename_in_zip, 'datapackage.json']
                assert zip_.read(csv_filename_in_zip) == 'Test,csv'.encode()
                # Check datapackage.json
                datapackage_json = zip_.read('datapackage.json')
                datapackage = json.loads(datapackage_json)
                assert datapackage['resources'] == [{
                    'format': 'CSV',
                    'name': 'rainfall',
                    'path': csv_filename_in_zip,
                    'sources': [{'path': dataset['resources'][0]['url'],
                                 'title': 'Rainfall'}],
                    'title': 'Rainfall',
                    }]

    @mock.patch('ckanext.downloadall.tasks.populate_schema_from_datastore',
                side_effect=mock_populate_schema_from_datastore)
    @pytest.mark.ckan_config('ckan.storage_path', '/doesnt_exist')
    @responses.activate
    def test_data_dictionary(self, _, __):
        responses.add(
            responses.GET,
            'https://example.com/data.csv',
            body='Date,Price\n1/6/2017,4.00\n2/6/2017,4.12'
        )
        responses.add_passthru(config['solr_url'])
        dataset = factories.Dataset(owner_org=self.org['id'], resources=[{
            'url': 'https://example.com/data.csv',
            'format': 'csv',
            }])

        update_zip(dataset['id'])

        dataset = helpers.call_action('package_show', id=dataset['id'])
        zip_resources = [res for res in dataset['resources']
                         if res['name'] == 'All resource data']
        assert len(zip_resources) == 1
        zip_resource = zip_resources[0]
        assert zip_resource['url_type'] == 'upload'

        uploader = ckan.lib.uploader.get_resource_uploader(zip_resource)
        filepath = uploader.get_path(zip_resource['id'])
        csv_filename_in_zip = '{}.csv'.format(dataset['resources'][0]['id'])
        with fake_open(filepath, 'rb') as f:
            with zipfile.ZipFile(f) as zip_:
                assert zip_.namelist() == [csv_filename_in_zip, 'datapackage.json']
                datapackage_json = zip_.read('datapackage.json').decode()
                assert datapackage_json.startswith('{\n  "description"')
                datapackage = json.loads(datapackage_json)
                assert datapackage['resources'][0]['schema'] == {
                    'fields': [{'type': 'string', 'name': 'Date'},
                               {'type': 'string', 'name': 'Price'}]}

    @pytest.mark.ckan_config('ckan.storage_path', '/doesnt_exist')
    @responses.activate
    def test_resource_url_with_connection_error(self, _):
        responses.add_passthru(config['solr_url'])
        responses.add(
            responses.GET,
            'https://example.com/data.csv',
            body=requests.ConnectionError('Some network trouble...')
        )
        dataset = factories.Dataset(owner_org=self.org['id'], resources=[{
            'url': 'https://example.com/data.csv',
            'name': 'rainfall',
            'format': 'csv',
            }])

        update_zip(dataset['id'])

        dataset = helpers.call_action('package_show', id=dataset['id'])
        zip_resources = [res for res in dataset['resources']
                         if res['name'] == 'All resource data']
        zip_resource = zip_resources[0]
        uploader = ckan.lib.uploader.get_resource_uploader(zip_resource)
        filepath = uploader.get_path(zip_resource['id'])
        with fake_open(filepath, 'rb') as f:
            with zipfile.ZipFile(f) as zip_:
                # Zip doesn't contain the data, just the json file
                assert zip_.namelist() == ['datapackage.json']
                # Check datapackage.json
                datapackage_json = zip_.read('datapackage.json')
                datapackage = json.loads(datapackage_json)
                assert datapackage['resources'] == [{
                    'format': 'CSV',
                    'name': 'rainfall',
                    # path is to the URL - an 'external resource'
                    'path': 'https://example.com/data.csv',
                    'title': 'rainfall',
                    }]

    @pytest.mark.ckan_config('ckan.storage_path', '/doesnt_exist')
    @responses.activate
    def test_resource_url_with_404_error(self, _):
        responses.add_passthru(config['solr_url'])
        responses.add(
            responses.GET,
            'https://example.com/data.csv',
            status=404
        )
        dataset = factories.Dataset(owner_org=self.org['id'], resources=[{
            'url': 'https://example.com/data.csv',
            'name': 'rainfall',
            'format': 'csv',
            }])

        update_zip(dataset['id'])

        dataset = helpers.call_action('package_show', id=dataset['id'])
        zip_resources = [res for res in dataset['resources']
                         if res['name'] == 'All resource data']
        zip_resource = zip_resources[0]
        uploader = ckan.lib.uploader.get_resource_uploader(zip_resource)
        filepath = uploader.get_path(zip_resource['id'])
        with fake_open(filepath, 'rb') as f:
            with zipfile.ZipFile(f) as zip_:
                # Zip doesn't contain the data, just the json file
                assert zip_.namelist() == ['datapackage.json']
                # Check datapackage.json
                datapackage_json = zip_.read('datapackage.json')
                datapackage = json.loads(datapackage_json)
                assert datapackage['resources'] == [{
                    'format': 'CSV',
                    'name': 'rainfall',
                    # path is to the URL - an 'external resource'
                    'path': 'https://example.com/data.csv',
                    'title': 'rainfall',
                    }]


local_datapackage = {
    'license': {
        'title': 'Creative Commons Attribution',
        'type': 'cc-by',
        'url': 'http://www.opendefinition.org/licenses/cc-by'
    },
    'name': 'test',
    'resources': [
        {
            'format': 'CSV',
            'name': 'annual-csv',
            'path': 'annual-.csv',
            'schema': {
                'fields': [
                    {
                        'description': 'Some description here!',
                        'name': 'Date',
                        'title': 'The Date',
                        'type': 'datetime'
                    },
                    {
                        'name': 'Price',
                        'type': 'number'
                    }
                ]
            },
            'sources': [
                {
                    'path': 'https://sample.com/annual.csv',
                    'title': 'annual.csv'
                }
            ],
            'title': 'annual.csv'
        },
        {
            'format': 'CSV',
            'name': 'annual-csv0',
            'path': 'annual-csv0.csv',
            'schema': {
                'fields': [
                    {
                        'name': 'Date',
                        'type': 'string'
                    },
                    {
                        'name': 'Price',
                        'type': 'string'
                    }
                ]
            },
            'sources': [
                {
                    'path': 'https://sample.com/annual.csv',
                    'title': 'annual.csv'
                }
            ],
            'title': 'annual.csv'
        }
    ],
    'title': 'Gold Prices'
}
remote_datapackage = {
    'license': {
        'title': 'Creative Commons Attribution',
        'type': 'cc-by',
        'url': 'http://www.opendefinition.org/licenses/cc-by'
    },
    'name': 'test',
    'resources': [
        {
            'format': 'CSV',
            'name': 'annual-csv',
            'path': 'https://sample.com/annual.csv',
            'schema': {
                'fields': [
                    {
                        'description': 'Some description here!',
                        'name': 'Date',
                        'title': 'The Date',
                        'type': 'datetime'
                    },
                    {
                        'name': 'Price',
                        'type': 'number'
                    }
                ]
            },
            'title': 'annual.csv'
        },
        {
            'format': 'CSV',
            'name': 'annual-csv0',
            'path': 'https://sample.com/annual.csv',
            'schema': {
                'fields': [
                    {
                        'name': 'Date',
                        'type': 'string'
                    },
                    {
                        'name': 'Price',
                        'type': 'string'
                    }
                ]
            },
            'title': 'annual.csv'
        }
    ],
    'title': 'Gold Prices'
}


class TestCanonizedDataPackage(object):
    def test_canonize_local_datapackage(self):
        assert canonized_datapackage(local_datapackage) == remote_datapackage

    def test_canonize_remote_datapackage(self):
        assert canonized_datapackage(remote_datapackage) == remote_datapackage


class TestSaveLocalPathInDatapackageResource(object):
    def test_convert_remote_to_local(self):
        datapackage = copy.deepcopy(remote_datapackage)
        res = {'title': 'Gold Price Annual'}
        save_local_path_in_datapackage_resource(
            datapackage['resources'][0], res, 'annual-.csv')
        save_local_path_in_datapackage_resource(
            datapackage['resources'][1], res, 'annual-csv0.csv')
        assert datapackage == local_datapackage


class TestHashDataPackage(object):
    def test_repeatability(self):
        # value of the hash shouldn't change between machines or python
        # versions etc
        assert hash_datapackage({'resources': []}) == '25335c0285e0cfdeb66a0b3f0cfe7a242cb0f084682704bc1fafbc72'

    def test_dict_ordering(self):
        assert hash_datapackage({'resources': [{'format': 'CSV', 'name': 'a'}]}) == hash_datapackage(
            {'resources': [{'name': 'a', 'format': 'CSV'}]})


class TestGenerateDatapackageJson(TestBase):
    def test_simple(self):
        dataset = factories.Dataset(
            owner_org=self.org['id'],
            resources=[{
                'url': 'https://example.com/data.csv',
                'format': 'csv',
                }])

        datapackage, ckan_and_datapackage_resources, existing_zip_resource = generate_datapackage_json(dataset['id'])

        replace_number_suffix(datapackage, 'name')
        replace_uuid(datapackage['resources'][0], 'name')
        assert datapackage == {
            'description': 'Just another test dataset.',
            'name': 'test_dataset_num',
            'resources': [{'format': 'CSV',
                           'name': '<SOME-UUID>',
                           'path': 'https://example.com/data.csv'}],
            'title': 'Test Dataset'
            }
        assert ckan_and_datapackage_resources[0][0]['url'] == 'https://example.com/data.csv'
        assert not ckan_and_datapackage_resources[0][0]['description']
        assert ckan_and_datapackage_resources[0][1] == {
            'format': 'CSV',
            'name': '<SOME-UUID>',
            'path': 'https://example.com/data.csv'
        }
        assert existing_zip_resource is None

    def test_extras(self):
        dataset = factories.Dataset(
            owner_org=self.org['id'], extras=[
                {'key': 'extra1', 'value': '1'},
                {'key': 'extra2', 'value': '2'},
                {'key': 'extra3', 'value': '3'},
            ])

        datapackage, _, __ = \
            generate_datapackage_json(dataset['id'])

        replace_number_suffix(datapackage, 'name')
        assert datapackage == {
            'description': 'Just another test dataset.',
            'name': 'test_dataset_num',
            'title': 'Test Dataset',
            'extras': {'extra1': 1, 'extra2': 2, 'extra3': 3},
            }

    @pytest.mark.ckan_config(
        'ckanext.downloadall.dataset_fields_to_add_to_datapackage',
        'num_resources type')
    def test_added_fields(self):
        dataset = factories.Dataset(owner_org=self.org['id'])

        datapackage, _, __ = \
            generate_datapackage_json(dataset['id'])

        replace_number_suffix(datapackage, 'name')
        assert datapackage == {
            'description': 'Just another test dataset.',
            'name': 'test_dataset_num',
            'title': 'Test Dataset',
            'num_resources': 0,
            'type': 'dataset',
            }


# helpers
def replace_uuid(dict_, key):
    assert key in dict_
    dict_[key] = '<SOME-UUID>'


def replace_number_suffix(dict_, key):
    # e.g. "Test Dataset 23" -> "Test Dataset "
    assert key in dict_
    dict_[key] = re.sub(r'\d+$', 'num', dict_[key])

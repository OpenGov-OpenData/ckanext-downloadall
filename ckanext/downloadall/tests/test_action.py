"""Tests for plugin.py."""
import pytest
from ckan.tests import factories
from ckan.tests import helpers
from ckanext.downloadall.tests import TestBase


@pytest.mark.usefixtures('clean_db', 'with_plugins', 'with_request_context')
@pytest.mark.ckan_config('ckan.plugins', 'datastore downloadall')
class TestDatastoreCreate(TestBase):
    def test_datastore_create(self):
        dataset = factories.Dataset(
            owner_org=self.org['id'],
            resources=[{'url': 'http://some.image.png', 'format': 'png'}])
        helpers.call_action('job_clear')

        helpers.call_action('datastore_create',
                            resource_id=dataset['resources'][0]['id'],
                            force=True)

        # Check the chained action caused the zip to be queued for update
        assert [job['title'] for job in helpers.call_action('job_list')] == [
            'DownloadAll datastore_create "{}" {}'.format(dataset['name'], dataset['id'])]

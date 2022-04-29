import pytest
from ckan.tests import helpers, factories


@pytest.mark.usefixtures('clean_db', 'with_plugins', 'clean_index')
@pytest.mark.ckan_config('ckan.plugins', 'downloadall')
class TestBase(object):
    def setup(self):
        helpers.call_action('job_clear')
        self.org = factories.Organization()

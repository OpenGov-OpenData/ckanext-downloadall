import pytest
from ckan.tests import helpers, factories


@pytest.mark.usefixtures('clean_db', 'with_plugins', 'with_request_context')
@pytest.mark.ckan_config('ckan.plugins', 'downloadall')
class TestBase(object):
    def setup(self):
        helpers.call_action('job_clear')
        self.org = factories.Organization()

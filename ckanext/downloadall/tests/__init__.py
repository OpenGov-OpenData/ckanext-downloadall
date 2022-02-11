import pytest
from ckan.tests import helpers


@pytest.mark.ckan_config('ckan.plugins', 'downloadall')
@pytest.mark.usefixtures('clean_db', 'with_plugins', 'with_request_context')
class TestBase(object):
    def setup(self):
        helpers.call_action('job_clear')

import tempfile
import zipfile
import os
import hashlib
import math
import copy
import logging
import datetime

import requests
import six
import ckanapi
import ckanapi.datapackage

from ckan import model
from ckan.plugins import toolkit
from ckan.plugins.toolkit import get_action, config
from werkzeug.datastructures import FileStorage

log = logging.getLogger(__name__)

DATAPACKAGE_TYPES = {  # map datastore types to datapackage types
    'text': 'string',
    'numeric': 'number',
    'timestamp': 'datetime',
}


def update_zip(package_id, skip_if_no_changes=True):
    '''
    Create/update the a dataset's zip resource, containing the other resources
    and some metadata.

    :param skip_if_no_changes: If true, and there is an existing zip for this
        dataset, it will compare a freshly generated package.json against what
        is in the existing zip, and if there are no changes (ignoring the
        Download All Zip) then it will skip downloading the resources and
        updating the zip.
    '''
    # TODO deal with private datasets - 'ignore_auth': True
    context = {'model': model, 'session': model.Session}
    dataset = get_action('package_show')(context, {'id': package_id})
    log.debug('Updating zip: {}'.format(dataset['name']))

    datapackage, ckan_and_datapackage_resources, existing_zip_resource = \
        generate_datapackage_json(package_id)

    if skip_if_no_changes and existing_zip_resource and \
            not has_datapackage_changed_significantly(
                datapackage, ckan_and_datapackage_resources,
                existing_zip_resource):
        log.info('Skipping updating the zip - the datapackage.json is not '
                 'changed sufficiently: {}'.format(dataset['name']))
        return

    prefix = '{}-'.format(dataset['name'])
    with tempfile.NamedTemporaryFile(mode='w+b', prefix=prefix, suffix='.zip') as fp:
        write_zip(fp, datapackage, ckan_and_datapackage_resources)

        # Upload resource to CKAN as a new/updated resource
        fp.seek(0)
        fs = FileStorage(fp, filename=fp.name, name=fp.name, content_type='zip')
        resource = dict(
            package_id=dataset['id'],
            url='dummy-value',
            upload=fs,
            name='All resource data',
            format='ZIP',
            downloadall_metadata_modified=dataset['metadata_modified'],
            downloadall_datapackage_hash=hash_datapackage(datapackage)
        )
        user = toolkit.get_action('get_site_user')({'ignore_auth': True}, ())
        ctx = context.copy()
        ctx['user'] = user['name']

        if not existing_zip_resource:
            log.debug('Writing new zip resource - {}'.format(dataset['name']))
            toolkit.get_action('resource_create')(ctx, resource)
        else:
            # TODO update the existing zip resource (using patch?)
            log.debug('Updating zip resource - {}'.format(dataset['name']))
            resource['id'] = existing_zip_resource['id']
            toolkit.get_action('resource_patch')(ctx, resource)


class DownloadError(Exception):
    pass


def has_datapackage_changed_significantly(
        datapackage, ckan_and_datapackage_resources, existing_zip_resource):
    '''Compare the freshly generated datapackage with the existing one and work
    out if it is changed enough to warrant regenerating the zip.

    :returns bool: True if the data package has really changed and needs
        regenerating
    '''
    assert existing_zip_resource
    new_hash = hash_datapackage(datapackage)
    old_hash = existing_zip_resource.get('downloadall_datapackage_hash')
    return new_hash != old_hash


def hash_datapackage(datapackage):
    '''Returns a hash of the canonized version of the given datapackage
    (metadata).
    '''
    canonized = canonized_datapackage(datapackage)
    m = hashlib.sha224(six.text_type(make_hashable(canonized)).encode('utf8'))
    return m.hexdigest()


def make_hashable(obj):
    if isinstance(obj, (tuple, list)):
        return tuple((make_hashable(e) for e in obj))
    if isinstance(obj, dict):
        return tuple(sorted((k, make_hashable(v)) for k, v in obj.items()))
    return obj


def canonized_datapackage(datapackage):
    '''
    The given datapackage is 'canonized', so that an exsting one can be
    compared with a freshly generated one, to see if the zip needs
    regenerating.

    Datapackages resources have either:
    * local paths (downloaded into the package) OR
    * OR remote paths (URLs)
    To allow datapackages to be compared, the canonization converts local
    resources to remote ones.
    '''
    datapackage_ = copy.deepcopy(datapackage)
    # convert resources to remote paths
    # i.e.
    #
    #   "path": "annual-.csv", "sources": [
    #     {
    #       "path": "https://example.com/file.csv",
    #       "title": "annual.csv"
    #     }
    #   ],
    #
    # ->
    #
    #   "path": "https://example.com/file.csv",
    for res in datapackage_.get('resources', []):
        try:
            remote_path = res['sources'][0]['path']
        except KeyError:
            continue
        res['path'] = remote_path
        del res['sources']
    return datapackage_


def generate_datapackage_json(package_id):
    '''Generates the datapackage - metadata that would be saved as
    datapackage.json.
    '''
    context = {'model': model, 'session': model.Session}
    dataset = get_action('package_show')(context, {'id': package_id})

    # filter out resources that are not suitable for inclusion in the data
    # package
    dataset, resources_to_include, existing_zip_resource = \
        remove_resources_that_should_not_be_included_in_the_datapackage(
            dataset)

    # get the datapackage (metadata)
    datapackage = ckanapi.datapackage.dataset_to_datapackage(dataset)

    # populate datapackage with the schema from the Datastore data
    # dictionary
    ckan_and_datapackage_resources = zip(resources_to_include,
                                         datapackage.get('resources', []))
    # this line is only for backward compatibility with py2 style of zip function
    ckan_and_datapackage_resources = [a for a in ckan_and_datapackage_resources]
    for res, datapackage_res in ckan_and_datapackage_resources:
        data = {
            'resource_id': res['id'],
            'limit': 1,
            'include_total': True,
        }
        if res.get('datastore_active'):
            ds = toolkit.get_action('datastore_search')(context, data)
            res['datastore_fields'] = ds['fields']

        populate_schema_from_datastore(res, datapackage_res)

    # add in any other dataset fields, if configured
    fields_to_include = config.get(
        'ckanext.downloadall.dataset_fields_to_add_to_datapackage', '').split()
    for key in fields_to_include:
        datapackage[key] = dataset.get(key)

    return datapackage, ckan_and_datapackage_resources, existing_zip_resource


def populate_schema_from_datastore(res, datapackage_res):
    # convert datastore data dictionary to datapackage schema
    if 'schema' not in datapackage_res and 'datastore_fields' in res:
        fields = []
        for f in res['datastore_fields']:
            if f['id'] == '_id':
                continue
            df = {'name': f['id']}
            dtyp = DATAPACKAGE_TYPES.get(f['type'])
            if dtyp:
                df['type'] = dtyp
            dtit = f.get('info', {}).get('label', '')
            if dtit:
                df['title'] = dtit
            ddesc = f.get('info', {}).get('notes', '')
            if ddesc:
                df['description'] = ddesc
            fields.append(df)
        datapackage_res['schema'] = {'fields': fields}


def write_zip(fp, datapackage, ckan_and_datapackage_resources):
    '''
    Downloads resources and writes the zip file.

    :param fp: Open file that the zip can be written to
    '''
    with zipfile.ZipFile(fp, 'w', zipfile.ZIP_DEFLATED, allowZip64=True) as zipf:
        i = 0
        for res, dres in ckan_and_datapackage_resources:
            i += 1

            log.debug('Downloading resource {}/{}: {}'
                      .format(i, len(ckan_and_datapackage_resources), res['url']))
            filename = ckanapi.datapackage.resource_filename(dres)
            try:
                download_resource_into_zip(res['url'], filename, zipf)
            except DownloadError:
                # The dres['path'] is left as the url - i.e. an 'external
                # resource' of the data package.
                continue

            save_local_path_in_datapackage_resource(dres, res, filename)

            # TODO optimize using the file_hash

        # Add the datapackage.json
        write_datapackage_json(datapackage, zipf)

    statinfo = os.stat(fp.name)
    filesize = statinfo.st_size

    log.info('Zip created: {} {} bytes'.format(fp.name, filesize))

    return filesize


def save_local_path_in_datapackage_resource(datapackage_resource, res,
                                            filename):
    # save path in datapackage.json - i.e. now pointing at the file
    # bundled in the data package zip
    title = datapackage_resource.get('title') or res.get('title') \
        or res.get('name', '')
    datapackage_resource['sources'] = [
        {'title': title, 'path': datapackage_resource['path']}]
    datapackage_resource['path'] = filename


def download_resource_into_zip(url, filename, zipf):
    try:
        r = requests.get(url, stream=True, timeout=300)
        r.raise_for_status()
    except requests.ConnectionError:
        log.error('URL {url} refused connection. The resource will not'
                  ' be downloaded'.format(url=url))
        raise DownloadError()
    except requests.exceptions.HTTPError as e:
        log.error('URL {url} status error: {status}. The resource will'
                  ' not be downloaded'
                  .format(url=url, status=e.response.status_code))
        raise DownloadError()
    except requests.exceptions.RequestException as e:
        log.error('URL {url} download request exception: {error}'
                  .format(url=url, error=str(e)))
        raise DownloadError()
    except Exception as e:
        log.error('URL {url} download exception: {error}'
                  .format(url=url, error=str(e)))
        raise DownloadError()

    hash_object = hashlib.sha224()
    size = 0
    # Create a ZipInfo object for setting the file's modified date
    zip_info = zipfile.ZipInfo(filename)
    # Set the modified date
    zip_info.date_time = datetime.datetime.now().timetuple()[:6]
    zip_info.compress_type = zipfile.ZIP_DEFLATED
    try:
        # python3 syntax - stream straight into the zip
        with zipf.open(zip_info, 'w') as zf:
            for chunk in r.iter_content(chunk_size=128):
                zf.write(chunk)
                hash_object.update(chunk)
                size += len(chunk)
    except RuntimeError:
        # python2 syntax - need to save to disk first
        with tempfile.NamedTemporaryFile() as datafile:
            for chunk in r.iter_content(chunk_size=128):
                datafile.write(chunk)
                hash_object.update(chunk)
                size += len(chunk)
            datafile.flush()
            # .write() streams the file into the zip
            zipf.write(datafile.name, arcname=filename)
    file_hash = hash_object.hexdigest()
    log.debug('Downloaded {}, hash: {}'
              .format(format_bytes(size), file_hash))


def write_datapackage_json(datapackage, zipf):
    with tempfile.NamedTemporaryFile() as json_file:
        json_file.write(ckanapi.cli.utils.pretty_json(datapackage))
        json_file.flush()
        zipf.write(json_file.name, arcname='datapackage.json')
        log.debug('Added datapackage.json from {}'.format(json_file.name))


def format_bytes(size_bytes):
    if size_bytes == 0:
        return '0 bytes'
    size_name = ('bytes', 'KB', 'MB', 'GB', 'TB', 'PB', 'EB', 'ZB', 'YB')
    i = int(math.floor(math.log(size_bytes, 1024)))
    p = math.pow(1024, i)
    s = round(size_bytes / p, 1)
    return '{} {}'.format(s, size_name[i])


def remove_resources_that_should_not_be_included_in_the_datapackage(dataset):
    resource_formats_to_ignore = ['API', 'api']  # TODO make it configurable

    existing_zip_resource = None
    resources_to_include = []
    for i, res in enumerate(dataset.get('resources', [])):
        if res.get('downloadall_metadata_modified'):
            # this is an existing zip of all the other resources
            log.debug('Resource resource {}/{} skipped - is the zip itself'
                      .format(i + 1, len(dataset.get('resources', []))))
            existing_zip_resource = res
            continue

        if res['format'] in resource_formats_to_ignore:
            log.debug('Resource resource {}/{} skipped - because it is '
                      'format {}'.format(i + 1, len(dataset.get('resources', [])),
                                         res['format']))
            continue
        resources_to_include.append(res)
    dataset = dict(dataset, resources=resources_to_include)
    return dataset, resources_to_include, existing_zip_resource

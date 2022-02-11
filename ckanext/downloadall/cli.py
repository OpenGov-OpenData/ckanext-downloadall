# encoding: utf-8

import click

try:
    # CKAN 2.9+
    from ckan.cli import load_config
except ImportError:
    # CKAN 2.7, 2.8
    from ckan.lib.cli import _get_config as load_config, CkanCommand

import ckan.plugins.toolkit as toolkit
from ckan import model
from ckan.lib.jobs import DEFAULT_QUEUE_NAME

from ckanext.downloadall import tasks


@click.group(name='downloadall')
@click.help_option(u'-h', u'--help')
@click.pass_context
def cli(ctx):
    pass


@cli.command(u'update-zip', short_help=u'Update zip file for a dataset')
@click.argument('dataset_ref')
@click.option(u'--synchronous', u'-s',
              help=u'Do it in the same process (not the worker)',
              is_flag=True)
@click.option(u'--force', u'-f',
              help=u'Force generation of ZIP file',
              is_flag=True)
def update_zip(dataset_ref, synchronous, force):
    u''' update-zip <package-name>

    Generates zip file for a dataset, downloading its resources.'''
    skip_if_no_changes = True
    if force:
        skip_if_no_changes = False
    if synchronous:
        tasks.update_zip(dataset_ref, skip_if_no_changes)
    else:
        toolkit.enqueue_job(
            tasks.update_zip, [dataset_ref, skip_if_no_changes],
            title=u'DownloadAll {operation} "{name}" {id}'.format(
                operation='cli-requested', name=dataset_ref,
                id=dataset_ref),
            queue=DEFAULT_QUEUE_NAME)
    click.secho(u'update-zip: SUCCESS', fg=u'green', bold=True)


@cli.command(u'update-all-zips',
             short_help=u'Update zip files for all datasets')
@click.option(u'--synchronous', u'-s',
              help=u'Do it in the same process (not the worker)',
              is_flag=True)
@click.option(u'--force', u'-f',
              help=u'Force generation of ZIP file',
              is_flag=True)
def update_all_zips(synchronous, force):
    u''' update-all-zips <package-name>

    Generates zip file for all datasets. It is done synchronously.'''
    context = {'model': model, 'session': model.Session}
    datasets = toolkit.get_action('package_list')(context, {})
    skip_if_no_changes = True
    if force:
        skip_if_no_changes = False
    for i, dataset_name in enumerate(datasets):
        if synchronous:
            print('Processing dataset {}/{}'.format(i + 1, len(datasets)))
            tasks.update_zip(dataset_name, skip_if_no_changes)
        else:
            print('Queuing dataset {}/{}'.format(i + 1, len(datasets)))
            toolkit.enqueue_job(
                tasks.update_zip, [dataset_name, skip_if_no_changes],
                title=u'DownloadAll {operation} "{name}" {id}'.format(
                    operation='cli-requested', name=dataset_name,
                    id=dataset_name),
                queue=DEFAULT_QUEUE_NAME)

    click.secho(u'update-all-zips: SUCCESS', fg=u'green', bold=True)

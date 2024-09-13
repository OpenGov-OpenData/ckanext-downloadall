# encoding: utf-8
import click

from ckan.plugins import toolkit
from ckan import model
from ckan.lib.jobs import DEFAULT_QUEUE_NAME

from ckanext.downloadall import tasks


@click.group(name='downloadall')
@click.help_option('-h', '--help')
@click.pass_context
def cli(ctx):
    pass


@cli.command('update-zip', short_help='Update zip file for a dataset')
@click.argument('dataset_ref')
@click.option('--synchronous', '-s',
              help='Do it in the same process (not the worker)',
              is_flag=True)
@click.option('--force', '-f',
              help='Force generation of ZIP file',
              is_flag=True)
def update_zip(dataset_ref, synchronous, force):
    ''' update-zip <package-name>

    Generates zip file for a dataset, downloading its resources.'''
    skip_if_no_changes = True
    if force:
        skip_if_no_changes = False
    if synchronous:
        tasks.update_zip(dataset_ref, skip_if_no_changes)
    else:
        toolkit.enqueue_job(
            tasks.update_zip, [dataset_ref, skip_if_no_changes],
            title='DownloadAll {operation} "{name}" {id}'.format(
                operation='cli-requested', name=dataset_ref,
                id=dataset_ref),
            queue=DEFAULT_QUEUE_NAME,
            rq_kwargs={"timeout": 1800})
    click.secho('update-zip: SUCCESS', fg='green', bold=True)


@cli.command('update-all-zips',
             short_help='Update zip files for all datasets')
@click.option('--synchronous', '-s',
              help='Do it in the same process (not the worker)',
              is_flag=True)
@click.option('--force', '-f',
              help='Force generation of ZIP file',
              is_flag=True)
def update_all_zips(synchronous, force):
    ''' update-all-zips <package-name>

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
                title='DownloadAll {operation} "{name}" {id}'.format(
                    operation='cli-requested', name=dataset_name,
                    id=dataset_name),
                queue=DEFAULT_QUEUE_NAME,
                rq_kwargs={"timeout": 1800})

    click.secho('update-all-zips: SUCCESS', fg='green', bold=True)

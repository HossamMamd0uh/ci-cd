from fabric.connection import Connection
from invoke import task, run
import dotenv
import os

import colorlog
handler = colorlog.StreamHandler()
handler.setFormatter(colorlog.ColoredFormatter('%(log_color)s%(levelname)s:%(name)s:%(message)s'))
logger = colorlog.getLogger('task')
logger.addHandler(handler)
logger.setLevel('DEBUG')

dotenv.load_dotenv('.env')

remote_prod_dir = os.getenv('REMOTE_PROD_DIR')
remote_prod_virtualenv = os.getenv('REMOTE_PROD_VIRTUALENV')

remote_stage_dir = os.getenv('REMOTE_STAGE_DIR')
remote_stage_virtualenv = os.getenv('REMOTE_STAGE_VIRTUALENV')

prod_server = os.getenv('PROD_SERVER')
prod_user = os.getenv('PROD_USER')
prod_super_user = os.getenv('PROD_SUPERUSER')

development_repo = os.getenv('DEV_REPO_REMOTE')

local_virtualenv = os.getenv('LOCAL_VIRTUALENV')

database = os.getenv('DATABASE')
database_local = os.getenv('LOCAL_DATABASE')
database_local_user = os.getenv('LOCAL_DATABASE_USER')

git_repo = os.getenv('GIT_REPO')

static_root = os.getenv('STATIC_ROOT')
media_root = os.getenv('MEDIA_ROOT')

@task(help={'dev-repo-push': 'Push the changes to the development repo as well.'})
def deploy(c, dev_repo_push=True):
    """
    Deploy the master branch to production server
    """
    with Connection(prod_server, user=prod_super_user) as r:
        logger.info("Backing up the database")
        with r.cd(git_repo):
            ret = r.run('git rev-parse --short HEAD')
            hash = ret.stdout.strip(u'\n')

            ret = r.run('date -u "+%Y-%m-%d_%H:%M:%S"')
            datetime = ret.stdout.strip(u'\n')

            filename = 'data/backup/{}-{}.sql'.format(datetime, hash)

        r.run("sudo su - postgres -c 'pg_dump " + database + " > " + remote_prod_dir + "/" + filename + "'")

    with Connection(prod_server, user=prod_user) as r:
        with r.prefix("source {0}/bin/activate".format(remote_prod_virtualenv)):

            if dev_repo_push and development_repo:
                logger.info("Pushing to development repo remote: " + development_repo)
                c.run("git push " + development_repo)

            logger.info("Pushing to production server")
            c.run("git push --push-option=live origin master")

            with r.cd(remote_prod_dir):
                logger.info("Installing requirements")
                r.run("pip install -r requirements.txt")

                logger.info("Migrating database schema")
                r.run("./manage.py migrate")

                logger.info("Collecting static files")
                r.run("./manage.py collectstatic --noinput")

                logger.info("Creating initial versions for models")
                r.run("./manage.py createinitialrevisions")

    with Connection(prod_server, user=prod_super_user) as r:
        logger.info("Restarting gunicorn service")
        r.run("systemctl restart gunicorn.service")


@task(iterable=['keyval'],
      optional=['key', 'val'],
      help={
          'set':    'Set a value',
          'get':    'Get a value',
          'unset':  'Unset a variable',
          'list':   'List all variables',
          'key':    'The key of the variable',
          'val':    'The value of the variable',
      })
def config(c, list=True, set=False, get=False, unset=False, key=None, val=None):
    """Manage project configuration via .env

    e.g: fab config --set -k <key> -v <value>
         fab config --get -k <key>
         fab config --unset --keyval <key>
         fab config [--list]
    """
    if set:
        action = "set"
        if not key and not val:
            return logger.error("Key and value expected")
    elif get:
        action = "get"
        if not key:
            return logger.error("Key expected")
    elif unset:
        action = "unset"
        if not key:
            return logger.error("Key expected")
    elif list:
        action = "list"

    with Connection(prod_server, user=prod_user) as r:
        with r.prefix("source {0}/bin/activate".format(remote_prod_virtualenv)):
            with r.cd(remote_prod_dir):
                command = dotenv.get_cli_string('.env', action, key, val)
                r.run(command)


@task()
def getdata(c):
    """
    Get remote data from the production sever
    """
    with Connection(prod_server, user=prod_user) as r:
        with r.cd(remote_prod_dir):

            with Connection(prod_server, user=prod_user) as r:
                with r.prefix("source {0}/bin/activate".format(remote_prod_virtualenv)):
                    with r.cd(remote_prod_dir):
                        command = dotenv.get_cli_string('.env', 'get', 'STATIC_ROOT',)
                        ret = r.run(command)
                        remote_static_root = ret.stdout.split('=')[1].strip()
                        command = dotenv.get_cli_string('.env', 'get', 'MEDIA_ROOT', )
                        ret = r.run(command)
                        remote_media_root = ret.stdout.split('=')[1].strip()
                        command = dotenv.get_cli_string('.env', 'get', 'DATABASE', )
                        ret = r.run(command)
                        remote_database = ret.stdout.split('=')[1].strip()
                        command = dotenv.get_cli_string('.env', 'get', 'USERNAME', )
                        ret = r.run(command)
                        remote_database_username = ret.stdout.split('=')[1].strip()

            logger.info("Backing up the database")
            r.run("pg_dump -U {} {} > {}/data/dump.sql".format(remote_database, remote_database_username, remote_prod_dir))

            logger.info("Getting remote data dump file")
            run("rsync -vzh --info=progress2 {}@{}:{}/data/dump.sql data/dump.sql".format(prod_user, prod_server,
                                                                                          remote_prod_dir,
                                                                                          ))
            #r.get(remote_prod_dir + '/data/dump.sql', local=os.getcwd() + "/data/dump.sql")

            logger.info("Recreating local database")
            run("dropdb {}".format(database_local))
            run("createdb {}".format(database_local))

            run("psql -U {} {} < data/dump.sql".format(database_local_user, database_local), warn=True)

            logger.info("Syncing static and media files")


            run("rsync -avzh --info=progress2 --delete {}@{}:{}/ {}/".format(prod_user, prod_server, remote_static_root, static_root))
            run("rsync -avzh --info=progress2 --delete --exclude='applications/*' {}@{}:{}/ {}/".format(prod_user, prod_server, remote_media_root, media_root))


@task(help={'push-local-sqlite': 'Push the local sqlite file'})
def stage(c, push_local_sqlite=False):
    """
    Push the dev branch to the staging website
    """
    with Connection(prod_server, user=prod_user) as r:
        with r.prefix("source {0}/bin/activate".format(remote_stage_virtualenv)):

            logger.info("Pushing to staging server")
            c.run("git push origin dev")

            with r.cd(remote_stage_dir):
                logger.info("Installing requirements")
                r.run("pip install -r requirements.txt")
                r.run("pip install -r requirements-local.txt")
            if push_local_sqlite:
                logger.info("Uploading the sqlite file")
                r.put(os.getcwd() + "/db.sqlite3", remote=remote_stage_dir + '/db.sqlite3')
            else:
                with r.cd(remote_stage_dir):
                    logger.info("Migrating database schema")
                    r.run("./manage.py migrate")

                    logger.info("Collecting static files")
                    r.run("./manage.py collectstatic --noinput")

    # with Connection(prod_server, user=prod_super_user) as r:
    #     logger.info("Restarting gunicorn service")
    #     r.run("systemctl restart gunicorn-stage.service")

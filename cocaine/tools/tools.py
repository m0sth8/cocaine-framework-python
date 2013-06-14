import json
import socket
import errno
import tarfile
from time import ctime
from cocaine.futures.chain import ChainFactory

from cocaine.services import Service
import msgpack

from cocaine.exceptions import ConnectionRefusedError, ConnectionError
from cocaine.exceptions import CocaineError


APPS_TAGS = ("app",)
RUNLISTS_TAGS = ("runlist",)
PROFILES_TAGS = ("profile",)


class ToolsError(Exception):
    pass


class StorageAction(object):
    def __init__(self, storage=None, **config):
        self.storage = storage

    def connect(self, host='localhost', port=10053):
        try:
            self.storage = Service('storage', host, port)
        except socket.error as err:
            if err.errno == errno.ECONNREFUSED:
                raise ConnectionRefusedError(host, port)
            else:
                raise ConnectionError('Unknown connection error: {0}'.format(err))

    def execute(self):
        raise NotImplementedError()

    def encodeJson(self, filename):
        """
        Tries to read json file with name 'filename' and to encode it with msgpack.

        :param filename: file name that need to be encoded
        :raises IOError: if file does not exists, you have not enough permissions to read it or something else
        :raises CocaineError: if file successfully read but cannot be parsed with json parser
        """
        try:
            with open(filename, 'rb') as fh:
                content = fh.read()
                data = json.loads(content)
                encoded = msgpack.packb(data)
                return encoded
        except IOError as err:
            raise CocaineError('Unable to open file - {0}'.format(err))
        except ValueError as err:
            raise CocaineError('File "{0}" is corrupted - {1}'.format(filename, err))


class ListAction(StorageAction):
    """
    Abstract storage action class which main aim is to provide find list action on 'key' and 'tags'.
    For example if key='manifests' and tags=('apps',) then class will try to find applications list
    """
    def __init__(self, key, tags, storage, **config):
        super(ListAction, self).__init__(storage, **config)
        self.key = key
        self.tags = tags

    def execute(self):
        future = self.storage.find(self.key, self.tags)
        return future


class AppListAction(ListAction):
    def __init__(self, storage, **config):
        super(AppListAction, self).__init__('manifests', APPS_TAGS, storage, **config)


class AppViewAction(StorageAction):
    def __init__(self, storage, **config):
        super(AppViewAction, self).__init__(storage, **config)
        self.name = config.get('name')
        if not self.name:
            raise ValueError('Specify name of application')

    def execute(self):
        future = self.storage.read('manifests', self.name)
        return future


class AppUploadAction(StorageAction):
    """
    Storage action class that tries to upload application into storage asynchronously
    """
    def __init__(self, storage, **config):
        super(AppUploadAction, self).__init__(storage, **config)
        self.name = config.get('name')
        self.manifest = config.get('manifest')
        self.package = config.get('package')
        if not self.name:
            raise ValueError('Please specify name of the app')
        if not self.manifest:
            raise ValueError('Please specify manifest of the app')
        if not self.package:
            raise ValueError('Please specify package of the app')

    def execute(self):
        """
        Encodes manifest and package files and (if successful) uploads them into storage

        :returns: list of two futures on each of them you can bind callback and errorback.
        Doneback is not supported but you can implement own counting cause it is only two futures returned
        """
        manifest = self.encodeJson(self.manifest)
        package = self.encodePackage()
        futures = self.upload(manifest, package)
        return futures

    def encodePackage(self):
        try:
            if not tarfile.is_tarfile(self.package):
                raise CocaineError('File "{0}" is ot tar file'.format(self.package))
            with open(self.package, 'rb') as archive:
                package = msgpack.packb(archive.read())
                return package
        except IOError as err:
            raise CocaineError('Error occurred while reading archive file "{0}" - {1}'.format(self.package, err))

    def upload(self, manifest, package):
        futures = [
            self.storage.write('manifests', self.name, manifest, APPS_TAGS),
            self.storage.write('apps', self.name, package, APPS_TAGS)
        ]
        return futures


class AppRemoveAction(StorageAction):
    """
    Storage action class that removes application 'name' from storage
    """
    def __init__(self, storage, **config):
        super(AppRemoveAction, self).__init__(storage, **config)
        self.name = config.get('name')
        if not self.name:
            raise ValueError('Empty application name')

    def execute(self):
        futures = [
            self.storage.remove("manifests", self.name),
            self.storage.remove("apps", self.name)
        ]
        return futures


class ProfileListAction(ListAction):
    def __init__(self, storage, **config):
        super(ProfileListAction, self).__init__('profiles', PROFILES_TAGS, storage, **config)


class SpecificProfileAction(StorageAction):
    def __init__(self, storage, **config):
        super(SpecificProfileAction, self).__init__(storage, **config)
        self.name = config.get('name')
        if not self.name:
            raise ValueError('Please specify profile name')


class ProfileUploadAction(SpecificProfileAction):
    def __init__(self, storage, **config):
        super(ProfileUploadAction, self).__init__(storage, **config)
        self.profile = config.get('manifest')
        if not self.profile:
            raise ValueError('Please specify profile file path')

    def execute(self):
        profile = self.encodeJson(self.profile)
        future = self.storage.write('profiles', self.name, profile, PROFILES_TAGS)
        return future


class ProfileRemoveAction(SpecificProfileAction):
    def execute(self):
        future = self.storage.remove('profiles', self.name)
        return future


class ProfileViewAction(SpecificProfileAction):
    def execute(self):
        future = self.storage.read('profiles', self.name)
        return future


class RunlistListAction(ListAction):
    def __init__(self, storage, **config):
        super(RunlistListAction, self).__init__('runlists', RUNLISTS_TAGS, storage, **config)


class SpecificRunlistAction(StorageAction):
    def __init__(self, storage, **config):
        super(SpecificRunlistAction, self).__init__(storage, **config)
        self.name = config.get('name')
        if not self.name:
            raise ValueError('Please specify runlist name')


class RunlistViewAction(SpecificRunlistAction):
    def execute(self):
        future = self.storage.read('runlists', self.name)
        return future


class RunlistUploadAction(SpecificRunlistAction):
    def __init__(self, storage, **config):
        super(RunlistUploadAction, self).__init__(storage, **config)
        self.runlist = config.get('manifest')
        self.runlist_raw = config.get('runlist-raw')
        if not any([self.runlist, self.runlist_raw]):
            raise ValueError('Please specify runlist profile file path')

    def execute(self):
        if self.runlist:
            runlist = self.encodeJson(self.runlist)
        else:
            runlist = msgpack.dumps(self.runlist_raw)
        future = self.storage.write('runlists', self.name, runlist, RUNLISTS_TAGS)
        return future


class RunlistRemoveAction(SpecificRunlistAction):
    def execute(self):
        future = self.storage.remove('runlists', self.name)
        return future


class AddApplicationToRunlistAction(SpecificRunlistAction):
    def __init__(self, storage, **config):
        super(AddApplicationToRunlistAction, self).__init__(storage, **config)
        self.app = config.get('app')
        self.profile = config.get('profile')
        if not self.app:
            raise ValueError('Please specify application name')
        if not self.profile:
            raise ValueError('Please specify profile')

    def execute(self):
        chain = ChainFactory().then(self.getRunlist).then(self.parseRunlist).then(self.uploadRunlist)
        return chain

    def getRunlist(self):
        action = RunlistViewAction(self.storage, **{'name': self.name})
        future = action.execute()
        return future

    def parseRunlist(self, result):
        runlist = msgpack.loads(result.get())
        runlist[self.app] = self.profile
        return runlist

    def uploadRunlist(self, runlist):
        action = RunlistUploadAction(self.storage, **{
            'name': self.name,
            'runlist-raw': runlist.get()
        })
        future = action.execute()
        return future


class CrashlogListAction(StorageAction):
    def __init__(self, storage, **config):
        super(CrashlogListAction, self).__init__(storage, **config)
        self.name = config.get('name')
        if not self.name:
            raise ValueError('Please specify crashlog name')

    def execute(self):
        future = self.storage.find('crashlogs', (self.name, ))
        return future


def parseCrashlogs(crashlogs, timestamp=None):
    flt = lambda x: (x == timestamp if timestamp else True)
    _list = (log.split(':') for log in crashlogs)
    return [(ts, ctime(float(ts) / 1000000), name) for ts, name in _list if flt(ts)]


class CrashlogViewOrRemoveAction(StorageAction):
    def __init__(self, storage, method, **config):
        super(CrashlogViewOrRemoveAction, self).__init__(storage, **config)
        self.name = config.get('name')
        self.timestamp = config.get('manifest')
        self.method = method
        if not self.name:
            raise ValueError('Please specify name')

    def execute(self):
        class Future(object):
            def __init__(self, action, future):
                self.action = action
                self.messagesLeft = 0
                future.bind(self.onChunk, self.onError, self.onDone)

            def bind(self, callback, errorback=None, doneback=None):
                self.callback = callback
                self.errorback = errorback
                self.doneback = doneback

            def onChunk(self, chunk):
                def countable(func):
                    def wrapper(*args, **kwargs):
                        func(*args, **kwargs)
                        self.messagesLeft -= 1
                        if not self.messagesLeft:
                            self.doneback()
                    return wrapper
                crashlogs = parseCrashlogs(chunk, timestamp=self.action.timestamp)
                self.messagesLeft = len(crashlogs)
                if len(crashlogs) == 0:
                    self.doneback()

                for crashlog in crashlogs:
                    key = "%s:%s" % (crashlog[0], crashlog[2])
                    method = getattr(self.action.storage, self.action.method)
                    future = method('crashlogs', key)
                    future.bind(countable(self.callback), countable(self.errorback), self.doneback)

            def onError(self, exception):
                self.errorback(exception)
                self.doneback()

            def onDone(self):
                self.doneback()

        findCrashlogsFuture = self.storage.find('crashlogs', (self.name,))
        readCrashlogFuture = Future(self, findCrashlogsFuture)
        return readCrashlogFuture


class CrashlogViewAction(CrashlogViewOrRemoveAction):
    def __init__(self, storage, **config):
        super(CrashlogViewAction, self).__init__(storage, 'read', **config)


class CrashlogRemoveAction(CrashlogViewOrRemoveAction):
    def __init__(self, storage, **config):
        super(CrashlogRemoveAction, self).__init__(storage, 'remove', **config)


class CrashlogRemoveAllAction(CrashlogViewOrRemoveAction):
    def __init__(self, storage, **config):
        config['manifest'] = None
        super(CrashlogRemoveAllAction, self).__init__(storage, 'remove', **config)


class NodeAction(object):
    def __init__(self, node=None, **config):
        self.node = node
        self.config = config

    def execute(self):
        raise NotImplementedError()


class NodeInfoAction(NodeAction):
    def execute(self):
        future = self.node.info()
        return future


class AppStartAction(NodeAction):
    def __init__(self, node, **config):
        super(AppStartAction, self).__init__(node, **config)
        self.name = config.get('name')
        self.profile = config.get('profile')
        if not self.name:
            raise ValueError('Please specify application name')
        if not self.profile:
            raise ValueError('Please specify profile name')

    def execute(self):
        apps = {
            self.name: self.profile
        }
        future = self.node.start_app(apps)
        return future


class AppPauseAction(NodeAction):
    def __init__(self, node, **config):
        super(AppPauseAction, self).__init__(node, **config)
        self.name = config.get('name')
        if not self.name:
            raise ValueError('Please specify application name')

    def execute(self):
        future = self.node.pause_app([self.name])
        return future


class AppRestartAction(NodeAction):
    def __init__(self, node, **config):
        super(AppRestartAction, self).__init__(node, **config)
        self.name = config.get('name')
        self.profile = config.get('profile')
        if not self.name:
            raise ValueError('Please specify application name')

    def execute(self):
        return ChainFactory().then(self.doAction)

    def doAction(self):
        try:
            info = yield NodeInfoAction(self.node, **self.config).execute()
            profile = self.profile or info['apps'][self.name]['profile']
            appStopStatus = yield AppPauseAction(self.node, **self.config).execute()
            appStartConfig = {
                'host': self.config['host'],
                'port': self.config['port'],
                'name': self.name,
                'profile': profile
            }
            appStartStatus = yield AppStartAction(self.node, **appStartConfig).execute()
            yield [appStopStatus, appStartStatus]
        except KeyError:
            raise ToolsError('Application "{0}" is not running and profile not specified'.format(self.name))
        except Exception as err:
            raise ToolsError('Unknown error - {0}'.format(err))


class AppCheckAction(NodeAction):
    def __init__(self, node, **config):
        super(AppCheckAction, self).__init__(node, **config)
        self.name = config.get('name')
        if not self.name:
            raise ValueError('Please specify application name')

    def execute(self):
        class Future(object):
            def __init__(self, action, future):
                self.action = action
                self.messagesLeft = 0
                future.bind(self.onChunk, self.onError)

            def bind(self, callback, errorback=None):
                self.callback = callback
                self.errorback = errorback

            def onChunk(self, chunk):
                state = 'stopped or missing'
                try:
                    apps = chunk['apps']
                    app = apps[self.action.name]
                    state = app['state']
                except KeyError:
                    pass
                finally:
                    self.callback({self.action.name: state})

            def onError(self, exception):
                self.errorback(exception)

        future = self.node.info()
        parseInfoFuture = Future(self, future)
        return parseInfoFuture

# -*- coding:utf-8 -*-

from threading import Thread
from queue import Queue, Empty
import logging
import time
import redis
import msgpack
from .uniqueid import UniqueID

__version = (0, 1, 0)
__version__ = version = '.'.join(map(str, __version))

'''
(call, Serial, Channel, Function, Arguments)
(reply, Serial, Result)
(error, Serial, (Code, Detail))

Error Codes
    1 = Unregistered
    2 = Call Failed
    3 = Protocol Error
    4 = Return Value Error
'''

logger = logging.getLogger('remrpc')


ERROR_UNREGISTERED = 1
ERROR_CALLFAILED = 2
ERROR_PROTOCOL = 3
ERROR_RETVAL = 4


class ExceptionRPC(Exception):
    pass


class TimedoutRPC(Exception):
    pass


class CallErrorRPC(Exception):
    def __init__(self, code, detail):
        self.code = code
        self.message = detail


class _Invoke:
    def __init__(self, invoker, func_name, timeout):
        self._invoker = invoker
        self._func_name = func_name
        self._timeout = timeout

    def __call__(self, *args, **kwargs):
        serial = self._invoker._rpc._unid.next()

        payload = msgpack.packb([
            'call', serial, self._invoker._rpc._channel,
            self._func_name, args, kwargs
        ], use_bin_type=True)

        self._invoker._rpc._pending[serial] = self._invoker._queue
        self._invoker._rpc._do_publish(self._invoker._channel, payload)

        try:
            reply = iter(self._invoker._queue.get(timeout=self._timeout))
        except Empty:
            raise TimedoutRPC(
                'Call function {0} is timedout.'.format(self._func_name))

        op = next(reply, None)

        if op is None:
            raise CallErrorRPC(ERROR_RETVAL, 'Get return value failed')

        if op == 'error':
            code, detail = next(reply)
            raise CallErrorRPC(code, detail)

        if op == 'reply':
            return next(reply, None)


class _Invoker:
    def __init__(self, rpc, channel, timeout):
        self._rpc = rpc
        self._channel = channel
        self._timeout = timeout
        self._queue = Queue()

    def __getattr__(self, attr):
        return _Invoke(self, attr, self._timeout)


class _Updater(Thread):
    def __init__(self, rpc):
        Thread.__init__(self)
        self._rpc = rpc
        self._quit = False

    def run(self):
        while not self._quit:
            self._rpc._do_update()
            time.sleep(0.01)

    def quit(self):
        self._quit = True
        self.join()


class RPC:
    def __init__(self, redis_conn, channel, timeout=3.0):
        self._invokers = {}
        self._pending = {}
        self._timeout = timeout
        self._unid = UniqueID()
        self._channel = channel
        self._redis = redis_conn
        self._pubsub = self._redis.pubsub(ignore_subscribe_messages=True)
        self._pubsub.subscribe(self._channel)
        self._updater = _Updater(self)
        self._updater.start()

    def __del__(self):
        if self._updater.is_alive():
            self.close()

    def close(self):
        self._pubsub.unsubscribe()
        self._pubsub.close()
        self._updater.quit()

    def register(self, func, name=None):
        if callable(func):
            if name is None:
                try:
                    name = func.__name__
                except Exception:
                    name = func.__class__.__name__

            self._invokers[name] = func

    def deregister(self, name):
        return self._invokers.pop(name)

    def invoker(self, channel):
        return _Invoker(self, channel, self._timeout)

    def _do_update(self):
        try:
            while True:
                rpcmsg = self._pubsub.get_message()
                if rpcmsg is None:
                    break
                self._do_message(rpcmsg['channel'], rpcmsg['data'])
        except redis.exceptions.ConnectionError as e:
            raise ExceptionRPC('Redis Connection Error: {0}'.format(e))

    def _do_publish(self, channel, message):
        self._redis.publish(channel, message)

    def _do_message(self, channel, message):
        try:
            payload = iter(msgpack.unpackb(message, raw=False))
            op = next(payload, None)

            if op is None:
                return

            serial = next(payload, None)

            if op == 'call':
                channel = next(payload, None)
                func_name = next(payload, '')
                args = next(payload, [])
                kwargs = next(payload, {})
                self._do_call(serial, channel, func_name, args, kwargs)
            elif op == 'reply':
                results = next(payload, None)
                self._do_reply(serial, results)
            elif op == 'error':
                errinfo = next(payload, (ERROR_PROTOCOL, 'Protocol error'))
                self._do_error(serial, errinfo)

        except msgpack.UnpackException as e:
            logger.error('Protocol unpack error: {0}'.format(e))
        except msgpack.UnpackValueError as e:
            logger.error('Protocol unpack error: {0}'.format(e))

    def _reply(self, serial, channel, results):
        if channel is None:
            return

        payload = msgpack.packb(['reply', serial, results],
                                use_bin_type=True)
        self._do_publish(channel, payload)

    def _error(self, serial, channel, code, detail):
        if channel is None:
            return

        payload = msgpack.packb(['error', serial, (code, detail)],
                                use_bin_type=True)
        self._do_publish(channel, payload)

    def _do_call(self, serial, channel, func_name, args, kwargs):
        if func_name not in self._invokers:
            self._error(serial, channel, ERROR_UNREGISTERED,
                        'Function not registered')
        try:
            results = self._invokers[func_name](*args, **kwargs)
            self._reply(serial, channel, results)
        except TypeError as e:
            self._error(serial, channel, ERROR_CALLFAILED, str(e))

    def _do_reply(self, serial, results):
        self._do_return(serial, ('reply', results))

    def _do_error(self, serial, errinfo):
        self._do_return(serial, ('error', errinfo))

    def _do_return(self, serial, retval):
        queue = self._pending.pop(serial)

        if queue is not None:
            queue.put(retval, block=False)

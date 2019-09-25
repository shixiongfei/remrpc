# -*- coding:utf-8 -*-

import gevent
import gevent.pool
import gevent.event
import logging
import traceback
import redis
import msgpack
from .uniqueid import UniqueID


__version = (0, 1, 11)
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


class _Invoker:
    def __init__(self, rpc, channel, timeout):
        self._rpc = rpc
        self._channel = channel
        self._timeout = timeout

    def __getattr__(self, fun):
        event = gevent.event.AsyncResult()

        def invoke(*args, **kwargs):
            serial = self._rpc._unid.next()

            payload = msgpack.packb([
                'call', serial, self._rpc._channel, fun, args, kwargs
            ], use_bin_type=True)

            self._rpc._pending[serial] = event
            self._rpc._do_publish(self._channel, payload)

            try:
                reply = iter(event.get(timeout=self._timeout))
            except gevent.timeout.Timeout:
                self._rpc._pending.pop(serial)
                raise TimedoutRPC("Call function {0} is timedout.".format(fun))

            op = next(reply, None)

            if op is None:
                raise CallErrorRPC(ERROR_RETVAL, "Get return value failed")

            if op == 'error':
                code, detail = next(reply)
                raise CallErrorRPC(code, detail)

            if op == 'reply':
                return next(reply, None)

        return invoke


class RPC:
    def __init__(self, redis_conn, channel, timeout=5.0, cosize=100):
        self._invokers = {}
        self._pending = {}
        self._timeout = timeout
        self._quit = False
        self._unid = UniqueID()
        self._channel = channel
        self._redis = redis_conn
        self._pubsub = self._redis.pubsub(ignore_subscribe_messages=True)
        self._pubsub.subscribe(self._channel)
        self._pool = gevent.pool.Pool(cosize)
        self._updater = self._pool.spawn(self._do_update).start()

    def __del__(self):
        self.close()

    def close(self):
        try:
            if not self._quit:
                self._quit = True
                self._pool.join()
                self._pubsub.unsubscribe()
                self._pubsub.close()
        except Exception:
            return

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
        def redis_reconnect():
            try:
                pubsub = self._redis.pubsub(
                    ignore_subscribe_messages=True
                )
                pubsub.subscribe(self._channel)
                self._pubsub = pubsub
            except redis.exceptions.ConnectionError:
                return False
            return True

        while not self._quit:
            try:
                while True:
                    rpcmsg = self._pubsub.get_message()
                    if rpcmsg is None:
                        break
                    self._do_message(rpcmsg['channel'], rpcmsg['data'])
            except redis.exceptions.ConnectionError as e:
                if not redis_reconnect():
                    logger.error("Redis PubSub Error: {0}".format(e))
                    gevent.sleep(0.2)
                    continue

            gevent.sleep(0.001)

    def _do_publish(self, channel, message):
        try:
            self._redis.publish(channel, message)
        except redis.exceptions.ConnectionError as e:
            logger.error("Redis Publish Error: {0}".format(e))

    def _do_message(self, channel, message):
        def do_return(serial, retval):
            if serial in self._pending:
                event = self._pending.pop(serial)
                if event is not None:
                    event.set(retval)
            else:
                logger.warning(
                    "Returns {0} serial {1} is not found.".format(
                        retval, serial)
                )

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
                self._pool.spawn(
                    self._do_call, serial, channel, func_name, args, kwargs
                ).start()
            elif op == 'reply':
                results = next(payload, None)
                do_return(serial, ('reply', results))
            elif op == 'error':
                errinfo = next(payload, (ERROR_PROTOCOL, "Protocol error"))
                do_return(serial, ('error', errinfo))
        except msgpack.UnpackException as e:
            logger.error("Protocol unpack error: {0}".format(e))
        except msgpack.UnpackValueError as e:
            logger.error("Protocol unpack error: {0}".format(e))

    def _do_call(self, serial, channel, func_name, args, kwargs):
        def do_error(code, detail):
            if channel is None:
                return

            payload = msgpack.packb(['error', serial, (code, detail)],
                                    use_bin_type=True)
            self._do_publish(channel, payload)

        if func_name not in self._invokers:
            do_error(ERROR_UNREGISTERED, "Function not registered")
            return

        try:
            results = self._invokers[func_name](*args, **kwargs)

            if channel is None:
                return

            payload = msgpack.packb(['reply', serial, results],
                                    use_bin_type=True)
            self._do_publish(channel, payload)
        except TypeError:
            do_error(ERROR_CALLFAILED, traceback.format_exc())

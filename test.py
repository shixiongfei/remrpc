# -*- coding:utf-8 -*-

import redis
import remrpc


def add(a, b):
    return a + b


def sub(a, b):
    return a - b


def multi():
    return "Hello", "World"


def kvfunc(k="key", v="val"):
    return "{0} = {1}".format(k, v)


def nonreturn():
    print("Non Return")


class CallObject:
    def __call__(self, name):
        return "Hello {0}".format(name)


if __name__ == "__main__":
    try:
        pool = redis.ConnectionPool(host="127.0.0.1",
                                    port=6379,
                                    password="123456")
        rpc1 = remrpc.RPC(redis.Redis(connection_pool=pool), "channel:rpc1")
        rpc2 = remrpc.RPC(redis.Redis(connection_pool=pool), "channel:rpc2")
    except redis.exceptions.ConnectionError as e:
        print("Redis connect error: {0}".format(e))
        exit(0)

    rpc1.register(add)
    rpc1.register(sub)
    rpc1.register(multi)
    rpc1.register(kvfunc)
    rpc1.register(CallObject(), 'sayhello')
    rpc1.register(nonreturn)

    invoker1 = rpc1.invoker("channel:rpc2")
    invoker2 = rpc2.invoker("channel:rpc1")

    def x2(x):
        return invoker2.add(x, x)

    rpc2.register(x2)

    try:
        print(invoker1.x2(3))
        print(invoker2.add(1, 2))
        print(invoker2.sub(9, 5))
        print(invoker2.multi())
        print(invoker2.kvfunc(k="KEY", v="VALUE"))
        print(invoker2.sayhello("World"))
        print(invoker2.nonreturn())
        print(invoker2.nonexistent())
    except remrpc.TimedoutRPC as e:
        print("> RPC Timedout: {0}".format(e))
    except remrpc.CallErrorRPC as e:
        print("> RPC Call Error ({0}): {1}".format(e.code, e.message))
    except remrpc.ExceptionRPC as e:
        print("> RPC Exception: {0}".format(e))

    rpc1.close()
    rpc2.close()

# remrpc
REdis Msgpack RPC

### Installation

```
pip install remrpc
```

### Example

```
import redis
import remrpc

def add(a, b):
    return a + b

def sub(a, b):
    return a - b

def multi():
    return 'Hello', 'World'

def kvfunc(k='key', v='val'):
    return '{0} = {1}'.format(k, v)

class CallObject:
    def __call__(self, name):
        return 'Hello {0}'.format(name)

if __name__ == "__main__":
    pool = redis.ConnectionPool(host='127.0.0.1',
                                port=6379,
                                password='123456')
    rpc1 = remrpc.RPC(redis.Redis(connection_pool=pool), 'channel:rpc1')
    rpc2 = remrpc.RPC(redis.Redis(connection_pool=pool), 'channel:rpc2')

    rpc1.register(add)
    rpc1.register(sub)
    rpc1.register(multi)
    rpc1.register(kvfunc)
    rpc1.register(CallObject(), 'sayhello')

    try:
        invoker = rpc2.invoker('channel:rpc1')

        print(invoker.add(1, 2))
        print(invoker.sub(9, 5))
        print(invoker.multi())
        print(invoker.kvfunc(k='KEY', v='VALUE'))
        print(invoker.sayhello('World'))
        # print(invoker.nonexistent())
    except remrpc.TimedoutRPC as e:
        print('> RPC Timedout: {0}'.format(e))
    except remrpc.CallErrorRPC as e:
        print('> RPC Call Error: {0}'.format(e))
    except remrpc.ExceptionRPC as e:
        print('> RPC Exception: {0}'.format(e))

    rpc1.close()
    rpc2.close()
```

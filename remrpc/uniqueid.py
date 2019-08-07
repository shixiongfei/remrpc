# -*- coding:utf-8 -*-

import math
import time
import random

# +-------------------------+--------------------+-----------------+
# |        timestamp        |      additional    |     counter     |
# |         41 bits         |  13 bits : 0~8191  | 10 bit : 0~1023 |
# +-------------------------+--------------------+-----------------+


class UniqueID:
    # Date starts 2019-01-01
    EPOCH = 1546300800000

    def __init__(self, additional=None):
        additional = additional or math.floor(random.random() * 10000)
        self.additional = (additional & 0x1FFF) << 10
        self.counter = 0
        self.lasttime = 0

    def base36encode(self, integer):
        chars = '0123456789abcdefghijklmnopqrstuvwxyz'

        sign = '-' if integer < 0 else ''
        integer = abs(integer)
        result = ''

        while integer > 0:
            integer, remainder = divmod(integer, 36)
            result = chars[remainder]+result

        return sign + result

    def next(self):
        now = int(time.time() * 1000)

        if self.lasttime == now:
            self.counter += 1

            if self.counter > 1023:
                time.sleep(1.0 / 1000.0)
                self.counter = 0
                now = int(time.time() * 1000)
        else:
            self.counter = 0

        self.lasttime = now
        timestamp = self.lasttime - UniqueID.EPOCH

        return self.base36encode(
            (timestamp << 23) | self.additional | (self.counter & 0x3FF)
        )


if __name__ == "__main__":
    uid = UniqueID()
    ids = [uid.next() for i in range(10000)]
    print(ids)
    print(len(ids))

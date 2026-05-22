"""math module — operasi matematika."""

import math as _math
import random as _random


def sqrt(x):
    return _math.sqrt(float(x))


def pow_(base, exp):
    return _math.pow(float(base), float(exp))


def log(x, base=None):
    if base is None:
        return _math.log(float(x))
    return _math.log(float(x), float(base))


def sin(x): return _math.sin(float(x))
def cos(x): return _math.cos(float(x))
def tan(x): return _math.tan(float(x))


def ceil(x):
    return _math.ceil(float(x))


def floor(x):
    return _math.floor(float(x))


def round_(x, digits=0):
    return round(float(x), int(digits))


def random():
    return _random.random()


def random_int(low, high):
    return _random.randint(int(low), int(high))


def random_choice(items):
    return _random.choice(items)


EXPORTS = {
    'pi':            _math.pi,
    'e':             _math.e,
    'sqrt':          sqrt,
    'pow':           pow_,
    'log':           log,
    'sin':           sin,
    'cos':           cos,
    'tan':           tan,
    'ceil':          ceil,
    'floor':         floor,
    'round':         round_,
    'random':        random,
    'random_int':    random_int,
    'random_choice': random_choice,
}

import functools
import inspect
import os
import sys
import traceback
try:
    import asyncio
except ImportError:
    asyncio = None

from . import compat
from . import events
from . import futures
from .log import logger

# If you set _DEBUG to true, @coroutine will wrap the resulting
# generator objects in a CoroWrapper instance (defined below).  That
# instance will log a message when the generator is never iterated
# over, which may happen when you forget to use "yield" with a
# coroutine call.  Note that the value of the _DEBUG flag is taken
# when the decorator is used, so to be of any use it must be set
# before you define your coroutines.  A downside of using this feature
# is that tracebacks show entries for the CoroWrapper.__next__ method
# when _DEBUG is true.
_DEBUG = bool(os.environ.get('TROLLIUSDEBUG'))

_PY35 = (sys.version_info >= (3, 5))


if compat.PY33:
    # Don't use the Return class on Python 3.3 and later to support asyncio
    # coroutines (to avoid the warning emited in Return destructor).
    #
    # The problem is that Return inherits from StopIteration.  "yield from
    # trollius_coroutine". Task._step() does not receive the Return exception,
    # because "yield from" handles it internally. So it's not possible to set
    # the raised attribute to True to avoid the warning in Return destructor.
    def Return(*args):
        if not args:
            value = None
        elif len(args) == 1:
            value = args[0]
        else:
            value = args
        return StopIteration(value)
else:
    class Return(StopIteration):
        def __init__(self, *args):
            StopIteration.__init__(self)
            if not args:
                self.value = None
            elif len(args) == 1:
                self.value = args[0]
            else:
                self.value = args
            self.raised = False

        def __del__(self):
            if not self.raised:
                logger.error('Return(%r) used without raise', self.value)


class CoroWrapper(object):
    # Wrapper for coroutine in _DEBUG mode.

    def __init__(self, gen, func):
        assert inspect.isgenerator(gen), gen
        self.gen = gen
        self.func = func
        self._source_traceback = traceback.extract_stack(sys._getframe(1))

    def __iter__(self):
        return self

    def __next__(self):
        return next(self.gen)
    next = __next__

    def send(self, *value):
        # We use `*value` because of a bug in CPythons prior
        # to 3.4.1. See issue #21209 and test_yield_from_corowrapper
        # for details.  This workaround should be removed in 3.5.0.
        if len(value) == 1:
            value = value[0]
        return self.gen.send(value)

    def throw(self, exc):
        return self.gen.throw(exc)

    def close(self):
        return self.gen.close()

    @property
    def gi_frame(self):
        return self.gen.gi_frame

    @property
    def gi_running(self):
        return self.gen.gi_running

    @property
    def gi_code(self):
        return self.gen.gi_code

    def __del__(self):
        # Be careful accessing self.gen.frame -- self.gen might not exist.
        gen = getattr(self, 'gen', None)
        frame = getattr(gen, 'gi_frame', None)
        if frame is not None and frame.f_lasti == -1:
            func = events._format_callback(self.func, ())
            tb = ''.join(traceback.format_list(self._source_traceback))
            message = ('Coroutine %s was never yielded from\n'
                       'Coroutine object created at (most recent call last):\n'
                       '%s'
                       % (func, tb.rstrip()))
            logger.error(message)

def coroutine(func):
    """Decorator to mark coroutines.

    If the coroutine is not yielded from before it is destroyed,
    an error message is logged.
    """
    if inspect.isgeneratorfunction(func):
        coro = func
    else:
        @functools.wraps(func)
        def coro(*args, **kw):
            res = func(*args, **kw)
            if isinstance(res, futures.Future) or inspect.isgenerator(res):
                res = yield From(res)
            raise Return(res)
        if not compat.PY3:
            coro.__wrapped__ = func

    if not _DEBUG:
        wrapper = coro
    else:
        @functools.wraps(func)
        def wrapper(*args, **kwds):
            w = CoroWrapper(coro(*args, **kwds), func)
            if w._source_traceback:
                del w._source_traceback[-1]
            w.__name__ = func.__name__
            if _PY35:
                w.__qualname__ = func.__qualname__
            w.__doc__ = func.__doc__
            return w
        if not compat.PY3:
            wrapper.__wrapped__ = func

    wrapper._is_coroutine = True  # For iscoroutinefunction().
    return wrapper


def iscoroutinefunction(func):
    """Return True if func is a decorated coroutine function."""
    return getattr(func, '_is_coroutine', False)


if asyncio is not None:
    # Accept also asyncio Future objects for interoperability
    _COROUTINE_TYPES = (CoroWrapper, asyncio.tasks.CoroWrapper)
else:
    _COROUTINE_TYPES = CoroWrapper


def iscoroutine(obj):
    """Return True if obj is a coroutine object."""
    return isinstance(obj, _COROUTINE_TYPES) or inspect.isgenerator(obj)

class FromWrapper(object):
    __slots__ = ('obj',)

    def __init__(self, obj):
        if isinstance(obj, FromWrapper):
            obj = obj.obj
            assert not isinstance(obj, FromWrapper)
        self.obj = obj

def From(obj):
    if not _DEBUG:
        return obj
    else:
        return FromWrapper(obj)

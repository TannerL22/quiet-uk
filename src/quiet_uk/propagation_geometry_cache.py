"""Per-path exact reuse of pure geometry across octave bands.

No persistent cache, smoothing, data mutation or frequency-dependent caching.
Containers must be read-only inside the explicit scope; retained references prevent
identity reuse. Normal calls execute the original functions without caching.
"""
from contextlib import contextmanager
from contextvars import ContextVar
from functools import wraps

_ACTIVE=ContextVar('quiet_uk_geometry_cache',default=None)


@contextmanager
def geometry_cache():
    state={'values':{},'references':{},'hits':0,'misses':0}
    token=_ACTIVE.set(state)
    try:yield state
    finally:_ACTIVE.reset(token)


def cached_geometry(function):
    @wraps(function)
    def call(*args,**kwargs):
        state=_ACTIVE.get()
        if state is None:return function(*args,**kwargs)
        def key(value):
            if isinstance(value,(list,tuple,dict)):
                if not isinstance(value,dict) and len(value)<=3 and all(isinstance(v,(int,float,str,type(None),bool)) for v in value):
                    return ('vector',tuple(value))
                state['references'][id(value)]=value
                return ('object',id(value))
            return ('scalar',value)
        identity=(function,tuple(key(a) for a in args),tuple((k,key(v)) for k,v in sorted(kwargs.items())))
        if identity in state['values']:
            state['hits']+=1;return state['values'][identity]
        result=function(*args,**kwargs);state['values'][identity]=result;state['misses']+=1
        return result
    return call

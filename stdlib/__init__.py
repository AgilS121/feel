"""Feel standard library — auto-loaded ke env saat interpreter start."""

from . import string_ext
from . import map_ops
from . import json_mod
from . import time_mod
from . import file_mod
from . import math_mod
from . import list_ops
from . import ai_mod
from . import db_mod
from . import security_mod
from . import crypto_mod
from . import validate_mod
from . import migrate_mod
from . import auth_mod
from . import cache_mod
from . import mail_mod
from . import queue_mod


def install_into(env):
    """Pasang module-module stdlib ke env sebagai namespace object."""
    from interpreter import FeelModule, Environment

    for mod_name, mod_funcs in [
        ('string', string_ext.EXPORTS),
        ('map', map_ops.EXPORTS),
        ('json', json_mod.EXPORTS),
        ('time', time_mod.EXPORTS),
        ('file', file_mod.EXPORTS),
        ('math', math_mod.EXPORTS),
        ('list', list_ops.EXPORTS),
        ('ai', ai_mod.EXPORTS),
        ('db', db_mod.EXPORTS),
        ('security', security_mod.EXPORTS),
        ('crypto', crypto_mod.EXPORTS),
        ('validate', validate_mod.EXPORTS),
        ('migrate', migrate_mod.EXPORTS),
        ('auth', auth_mod.EXPORTS_AUTH),
        ('session', auth_mod.EXPORTS_SESSION),
        ('cache', cache_mod.EXPORTS),
        ('mail', mail_mod.EXPORTS),
        ('queue', queue_mod.EXPORTS),
    ]:
        mod_env = Environment()
        for n, fn in mod_funcs.items():
            mod_env.set(n, fn)
        env.set(mod_name, FeelModule(mod_name, mod_env))

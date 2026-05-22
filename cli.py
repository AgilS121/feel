"""Polished CLI output helpers — colors, banners, summaries.

ANSI escape codes work on Windows 10+ Terminal/PowerShell out of the box.
For old cmd.exe we call os.system('') once to flip the console into VT mode.
TTY detection: when stdout/stderr is a pipe (not a real terminal), all
colour is dropped so logs stay clean.
"""

import os
import sys
import time

VERSION = "0.4-m3"
TAGLINE = "An AI-predictable backend language"

# Activate ANSI processing on legacy Windows consoles.
if os.name == 'nt':
    try:
        os.system('')
    except Exception:
        pass


def _color_enabled():
    return sys.stdout.isatty()


def _c(code, text):
    if not _color_enabled():
        return text
    return f'\033[{code}m{text}\033[0m'


def red(s):     return _c('31', s)
def green(s):   return _c('32', s)
def yellow(s):  return _c('33', s)
def blue(s):    return _c('34', s)
def magenta(s): return _c('35', s)
def cyan(s):    return _c('36', s)
def white(s):   return _c('37', s)
def dim(s):     return _c('2', s)
def bold(s):    return _c('1', s)
def bold_cyan(s):  return _c('1;36', s)
def bold_green(s): return _c('1;32', s)
def bold_red(s):   return _c('1;31', s)


BANNER = r"""
  ███████╗███████╗███████╗██╗
  ██╔════╝██╔════╝██╔════╝██║
  █████╗  █████╗  █████╗  ██║
  ██╔══╝  ██╔══╝  ██╔══╝  ██║
  ██║     ███████╗███████╗███████╗
  ╚═╝     ╚══════╝╚══════╝╚══════╝"""


def print_banner(short=False):
    """Render the Feel banner. `short=True` skips the ASCII art."""
    if short:
        print(f'{bold_cyan("Feel")} {dim("v" + VERSION)}  {dim("·")}  {TAGLINE}')
        return
    print(cyan(BANNER))
    line = f'  {bold("Feel")} v{VERSION}'.ljust(28) + dim(TAGLINE)
    print(line)
    print()


def print_help():
    """Polished help screen with grouped subcommands."""
    print()
    print(f'  {bold_cyan("Feel")} {dim("v" + VERSION)}  {dim("—")}  {TAGLINE}')
    print()
    print(bold('  USAGE'))
    print(f'    feel {dim("[COMMAND] [ARGS]")}')
    print()
    print(bold('  COMMANDS'))
    rows = [
        ('run',     'FILE',                 'Run a Feel script'),
        ('test',    '[DIR]',                'Run *_test.feel under DIR (default tests/)'),
        ('fmt',     'FILE',                 'Print canonical form of FILE'),
        ('fmt',     '--write FILE',         'Rewrite FILE in canonical form'),
        ('fmt',     '--check FILE [FILE…]', 'Exit 1 if any file is not formatted'),
        ('build',   'FILE [-o NAME]',       'Transpile to Go and produce a native binary'),
        ('version', '',                     'Print version'),
        ('help',    '',                     'Show this help'),
    ]
    cmd_w = max(len(c) for c, _, _ in rows) + 2
    arg_w = max(len(a) for _, a, _ in rows) + 2
    for cmd, args, desc in rows:
        print(f'    {green(cmd.ljust(cmd_w))}{yellow(args.ljust(arg_w))}{dim(desc)}')
    print()
    print(bold('  EXAMPLES'))
    examples = [
        ('feel',                              'start REPL'),
        ('feel hello.feel',                   'run a script (alias for "feel run hello.feel")'),
        ('feel test tests/',                  'run all tests'),
        ('feel fmt --write src/app.feel',     'reformat a file in place'),
        ('feel build server.feel -o api.exe', 'compile to native binary'),
    ]
    ex_w = max(len(e[0]) for e in examples) + 2
    for cmd, desc in examples:
        print(f'    {cmd.ljust(ex_w)}{dim(desc)}')
    print()
    print(bold('  ENVIRONMENT'))
    env_rows = [
        ('ANTHROPIC_API_KEY', 'enables real Claude provider for ai.* primitives'),
        ('FEEL_AI_PROVIDER',  'force provider: mock | claude'),
        ('FEEL_AI_MODEL',     'override model (default: claude-sonnet-4-6)'),
    ]
    ev_w = max(len(e[0]) for e in env_rows) + 2
    for k, v in env_rows:
        print(f'    {cyan(k.ljust(ev_w))}{dim(v)}')
    print()


def print_version():
    print(f'feel v{VERSION}')


# ---------- Test runner UI ----------

def test_header(file_count):
    print()
    print(f'  {bold("Running")} {file_count} test file{"s" if file_count != 1 else ""}')
    print()


def test_pass(name):
    print(f'  {bold_green("PASS")}  {name}')


def test_fail(name, detail=None):
    print(f'  {bold_red("FAIL")}  {name}')
    if detail:
        for line in detail.splitlines():
            print(f'        {dim(line)}')


def test_summary(passed, failed, elapsed):
    print()
    bar = '─' * 50
    print(f'  {dim(bar)}')
    parts = []
    if passed:
        parts.append(green(f'{passed} passed'))
    if failed:
        parts.append(red(f'{failed} failed'))
    parts.append(dim(f'{elapsed:.2f}s'))
    print(f'  ' + dim(' · ').join(parts))
    print()


# ---------- Build UI ----------

def build_step(label, ok=None, detail=None):
    """Emit a build progress line. ok=None means in-progress, True=done, False=fail."""
    if ok is None:
        marker = dim('•')
    elif ok:
        marker = bold_green('✓')
    else:
        marker = bold_red('✗')
    line = f'  {marker} {label}'
    if detail:
        line += f'  {dim(detail)}'
    print(line)


def build_done(out_path, elapsed):
    print()
    print(f'  {bold_green("Built")} {bold(out_path)}  {dim(f"in {elapsed:.2f}s")}')


def build_failed(reason):
    print()
    print(f'  {bold_red("Build failed")}')
    for line in reason.splitlines():
        print(f'    {dim(line)}')


# ---------- General ----------

def info(msg):
    print(f'  {cyan("info")}  {msg}')


def warn(msg):
    print(f'  {yellow("warn")}  {msg}', file=sys.stderr)


def error(msg):
    print(f'  {red("error")}  {msg}', file=sys.stderr)


def success(msg):
    print(f'  {green("ok")}    {msg}')

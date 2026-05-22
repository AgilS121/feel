#!/usr/bin/env python3
import sys
import os
import time

# Ensure UTF-8 on Windows (banner uses box-drawing characters)
if hasattr(sys.stdout, 'reconfigure'):
    try:
        sys.stdout.reconfigure(encoding='utf-8')
        sys.stderr.reconfigure(encoding='utf-8')
    except Exception:
        pass

sys.path.insert(0, os.path.dirname(__file__))

from interpreter import Interpreter, run_file
from errors import FeelError, FeelThrow
import cli


_OPEN_BRACKETS = {'{': '}', '(': ')', '[': ']'}


def _needs_continuation(buf):
    """True if buffer has unclosed brackets or trailing '|'/'->'."""
    stack = []
    in_string = False
    in_comment = False
    i = 0
    while i < len(buf):
        c = buf[i]
        if in_comment:
            if c == '\n':
                in_comment = False
            i += 1
            continue
        if c == '"' and not in_string:
            in_string = True
        elif c == '"' and in_string:
            in_string = False
        elif not in_string:
            if c == '-' and i + 1 < len(buf) and buf[i+1] == '-':
                in_comment = True
                i += 2
                continue
            if c in _OPEN_BRACKETS:
                stack.append(c)
            elif c in _OPEN_BRACKETS.values():
                if stack and _OPEN_BRACKETS[stack[-1]] == c:
                    stack.pop()
        i += 1
    if stack:
        return True
    stripped = buf.rstrip()
    if stripped.endswith('|') or stripped.endswith('->'):
        return True
    return False


def repl():
    cli.print_banner()
    print(f'  {cli.dim("Type Feel code below. .help for commands. .exit or Ctrl+D to quit.")}')
    print()

    try:
        import readline  # noqa: F401
    except ImportError:
        try:
            import pyreadline3  # noqa: F401
        except ImportError:
            pass

    interp = Interpreter(filename='<repl>')
    buf = ''
    prompt_main = cli.bold_cyan('feel') + cli.dim(' › ')
    prompt_cont = cli.dim('    › ')

    while True:
        try:
            prompt = prompt_main if not buf else prompt_cont
            line = input(prompt)
            stripped = line.strip()

            # REPL meta-commands
            if not buf and stripped in ('.exit', '.quit', 'exit', 'quit'):
                break
            if not buf and stripped == '.help':
                print()
                print(f'  {cli.bold("REPL commands:")}')
                print(f'    {cli.green(".help")}   show this list')
                print(f'    {cli.green(".clear")}  start a fresh interpreter')
                print(f'    {cli.green(".exit")}   quit')
                print()
                print(f'  Type any Feel expression or statement. Multi-line blocks')
                print(f'  ({{ }}, do-blocks, dangling | or ->) auto-continue.')
                print()
                continue
            if not buf and stripped == '.clear':
                interp = Interpreter(filename='<repl>')
                cli.info('interpreter reset')
                continue
            if not stripped and not buf:
                continue

            buf = (buf + '\n' + line) if buf else line
            if _needs_continuation(buf):
                continue
            try:
                interp.run(buf)
            except FeelError as e:
                print(str(e))
            except FeelThrow as ft:
                print(f"{cli.red('uncaught throw:')} {ft.value}")
            buf = ''
        except KeyboardInterrupt:
            print(f'\n{cli.dim("cancelled")}')
            buf = ''
            continue
        except EOFError:
            print()
            break


def run_tests(tests_dir='tests'):
    """Run all *_test.feel under DIR with polished output."""
    import glob
    if not os.path.isdir(tests_dir):
        cli.error(f"test directory not found: {tests_dir}")
        return 1
    files = sorted(glob.glob(os.path.join(tests_dir, '*_test.feel')))
    if not files:
        cli.error(f"no *_test.feel files in {tests_dir}")
        return 1

    cli.test_header(len(files))
    passed = 0
    failed = 0
    t0 = time.time()
    for f in files:
        name = os.path.basename(f)
        try:
            run_file(f)
            cli.test_pass(name)
            passed += 1
        except FeelError as e:
            cli.test_fail(name, detail=str(e))
            failed += 1
        except FeelThrow as ft:
            cli.test_fail(name, detail=f'uncaught throw: {ft.value}')
            failed += 1
        except Exception as e:
            cli.test_fail(name, detail=f'unexpected: {e}')
            failed += 1
    cli.test_summary(passed, failed, time.time() - t0)
    return 0 if failed == 0 else 1


def run_build(args):
    """Implements `feel build` — transpile Feel → Go → native binary."""
    from compile_go import build_feel

    feel_path = None
    out_path = None
    keep_go = False
    i = 0
    while i < len(args):
        a = args[i]
        if a == '-o' and i + 1 < len(args):
            out_path = args[i + 1]; i += 2; continue
        if a == '--keep-go':
            keep_go = True; i += 1; continue
        if a.startswith('-'):
            cli.error(f"build: unknown flag {a!r}")
            return 1
        if feel_path is None:
            feel_path = a
        i += 1

    if feel_path is None:
        cli.error("build: missing input .feel file")
        return 1
    if not os.path.exists(feel_path):
        cli.error(f"build: file not found: {feel_path}")
        return 1

    print()
    cli.build_step(f'Compiling {cli.bold(os.path.basename(feel_path))} → Go')
    t0 = time.time()
    try:
        ok, msg = build_feel(feel_path, out_path=out_path, keep_go=keep_go)
    except FeelError as e:
        cli.build_failed(str(e))
        return 1
    elapsed = time.time() - t0
    if not ok:
        cli.build_failed(msg)
        return 1
    actual = out_path or (os.path.splitext(os.path.basename(feel_path))[0] +
                          ('.exe' if os.name == 'nt' else ''))
    try:
        size = os.path.getsize(actual)
        size_mb = size / (1024 * 1024)
        cli.build_step(f'Linked native binary', ok=True, detail=f'{size_mb:.1f} MB')
    except OSError:
        cli.build_step('Linked native binary', ok=True)
    cli.build_done(actual, elapsed)
    return 0


def run_fmt(args):
    """Implements `feel fmt`. Returns exit code."""
    from formatter import format_file, format_source

    write = False
    check = False
    files = []
    i = 0
    while i < len(args):
        a = args[i]
        if a == '--write' or a == '-w':
            write = True
        elif a == '--check':
            check = True
        elif a.startswith('-'):
            cli.error(f"fmt: unknown flag {a!r}")
            return 1
        else:
            files.append(a)
        i += 1

    if not files:
        src = sys.stdin.read()
        try:
            print(format_source(src), end='')
        except FeelError as e:
            print(str(e), file=sys.stderr)
            return 1
        return 0

    not_formatted = []
    changed = []
    for f in files:
        if not os.path.exists(f):
            cli.error(f"fmt: file not found: {f}")
            return 1
        try:
            if check:
                ok, _ = format_file(f, check=True)
                if not ok:
                    not_formatted.append(f)
            elif write:
                with open(f, encoding='utf-8') as fh:
                    before = fh.read()
                _, out = format_file(f, write=True)
                if before != out:
                    changed.append(f)
            else:
                _, out = format_file(f)
                print(out, end='')
        except FeelError as e:
            cli.error(f"fmt: {f}: {e}")
            return 1

    if check:
        if not_formatted:
            print()
            for f in not_formatted:
                print(f'  {cli.red("not formatted")}  {f}')
            print()
            print(f'  {cli.dim(str(len(not_formatted)) + " file(s) need formatting. Run: feel fmt --write <file>")}')
            return 1
        cli.success(f'all {len(files)} file(s) are canonically formatted')
        return 0

    if write:
        if changed:
            print()
            for f in changed:
                print(f'  {cli.green("formatted")}  {f}')
            print()
            print(f'  {cli.dim(str(len(changed)) + " file(s) rewritten · " + str(len(files) - len(changed)) + " unchanged")}')
        else:
            cli.success(f'all {len(files)} file(s) already canonical')
    return 0


def main():
    args = sys.argv[1:]

    if not args:
        repl()
        return

    sub = args[0]

    if sub in ('-h', '--help', 'help'):
        cli.print_help()
        return

    if sub in ('-v', '--version', 'version'):
        cli.print_version()
        return

    if sub == 'test':
        tests_dir = args[1] if len(args) > 1 else 'tests'
        sys.exit(run_tests(tests_dir))

    if sub == 'fmt':
        sys.exit(run_fmt(args[1:]))

    if sub == 'build':
        sys.exit(run_build(args[1:]))

    if sub == 'run':
        if len(args) < 2:
            cli.error("run: missing FILE")
            sys.exit(1)
        path = args[1]
    elif sub == '--compile':
        from compiler import compile_file
        rest = args[1:]
        if not rest:
            cli.error("--compile requires a .feel file")
            sys.exit(1)
        feel_path = rest[0]
        out_path = None
        keep_c = False
        j = 1
        while j < len(rest):
            if rest[j] == '-o' and j + 1 < len(rest):
                out_path = rest[j+1]; j += 2
            elif rest[j] == '--keep-c':
                keep_c = True; j += 1
            else:
                j += 1
        if not os.path.exists(feel_path):
            cli.error(f"file '{feel_path}' not found")
            sys.exit(1)
        ok = compile_file(feel_path, out_path=out_path, keep_c=keep_c)
        sys.exit(0 if ok else 1)
    else:
        path = sub

    if not os.path.exists(path):
        cli.error(f"file '{path}' not found")
        sys.exit(1)
    try:
        run_file(path)
    except FeelError as e:
        print(str(e), file=sys.stderr)
        sys.exit(1)
    except FeelThrow as ft:
        print(f"{cli.red('uncaught throw:')} {ft.value}", file=sys.stderr)
        sys.exit(1)


if __name__ == '__main__':
    main()

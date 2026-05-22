#!/usr/bin/env python3
import sys
import os

# Pastikan UTF-8 di Windows (banner & error messages pakai box-drawing + emoji)
if hasattr(sys.stdout, 'reconfigure'):
    try:
        sys.stdout.reconfigure(encoding='utf-8')
        sys.stderr.reconfigure(encoding='utf-8')
    except Exception:
        pass

sys.path.insert(0, os.path.dirname(__file__))

from interpreter import Interpreter, run_file
from errors import FeelError, FeelThrow

BANNER = """
  ███████╗███████╗███████╗██╗
  ██╔════╝██╔════╝██╔════╝██║
  █████╗  █████╗  █████╗  ██║
  ██╔══╝  ██╔══╝  ██╔══╝  ██║
  ██║     ███████╗███████╗███████╗
  ╚═╝     ╚══════╝╚══════╝╚══════╝
  Feel v0.2  — code that flows
"""

USAGE = """Usage:
  python main.py                              interactive REPL
  python main.py file.feel                    run a file
  python main.py test [tests_dir]             run the test suite
  python main.py --compile file.feel          compile to a binary
  python main.py --compile file.feel -o name  compile to a binary with given name
  python main.py --compile file.feel --keep-c keep the intermediate .c file
"""


_OPEN_BRACKETS = {'{': '}', '(': ')', '[': ']'}


def _needs_continuation(buf):
    """Cek apakah buffer Feel tidak lengkap (open brace/paren, trailing |/->)."""
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
    # cek trailing operator: | atau ->
    stripped = buf.rstrip()
    if stripped.endswith('|') or stripped.endswith('->'):
        return True
    return False


def repl():
    print(BANNER)
    print('Type Feel code. Type "exit" to quit.\n')

    # readline untuk history
    try:
        import readline  # noqa: F401
    except ImportError:
        try:
            import pyreadline3  # noqa: F401
        except ImportError:
            pass

    interp = Interpreter(filename='<repl>')
    buf = ''
    while True:
        try:
            prompt = 'feel> ' if not buf else '....> '
            line = input(prompt)
            if not buf and line.strip() in ('exit', 'quit'):
                break
            if not line.strip() and not buf:
                continue
            buf = (buf + '\n' + line) if buf else line
            if _needs_continuation(buf):
                continue
            try:
                # tampilkan hasil ekspresi terakhir (jika bukan stmt yang sudah print sendiri)
                result = interp.run(buf)
                if result is not None and not buf.lstrip().startswith('show'):
                    # auto-display kalau bukan show stmt yang sudah print
                    pass  # disable auto-print supaya tidak duplicate
            except FeelError as e:
                print(str(e))
            except FeelThrow as ft:
                print(f"Uncaught throw: {ft.value}")
            buf = ''
        except KeyboardInterrupt:
            print('\nCancelled')
            buf = ''
            continue
        except EOFError:
            print()
            break


def run_tests(tests_dir='tests'):
    """Jalankan semua *_test.feel di folder tests/."""
    import glob
    if not os.path.isdir(tests_dir):
        print(f"Test directory not found: {tests_dir}")
        return 1
    files = sorted(glob.glob(os.path.join(tests_dir, '*_test.feel')))
    if not files:
        print(f"No *_test.feel files in {tests_dir}")
        return 1
    passed = 0
    failed = 0
    for f in files:
        name = os.path.basename(f)
        try:
            run_file(f)
            print(f"  PASS  {name}")
            passed += 1
        except FeelError as e:
            print(f"  FAIL  {name}")
            print(str(e))
            failed += 1
        except FeelThrow as ft:
            print(f"  FAIL  {name}  (uncaught throw: {ft.value})")
            failed += 1
        except Exception as e:
            print(f"  FAIL  {name}  (unexpected: {e})")
            failed += 1
    print(f"\n{passed} passed, {failed} failed out of {len(files)} tests.")
    return 0 if failed == 0 else 1


def main():
    args = sys.argv[1:]

    if not args:
        repl()
        return

    if args[0] == 'test':
        tests_dir = args[1] if len(args) > 1 else 'tests'
        sys.exit(run_tests(tests_dir))

    if args[0] in ('-h', '--help', 'help'):
        print(USAGE)
        return

    if args[0] == '--compile':
        from compiler import compile_file
        args = args[1:]
        if not args:
            print("Error: --compile requires a .feel file")
            sys.exit(1)
        feel_path = args[0]
        out_path = None
        keep_c = False
        i = 1
        while i < len(args):
            if args[i] == '-o' and i + 1 < len(args):
                out_path = args[i+1]; i += 2
            elif args[i] == '--keep-c':
                keep_c = True; i += 1
            else:
                i += 1
        if not os.path.exists(feel_path):
            print(f"Error: file '{feel_path}' not found")
            sys.exit(1)
        ok = compile_file(feel_path, out_path=out_path, keep_c=keep_c)
        sys.exit(0 if ok else 1)

    # Interpret mode
    path = args[0]
    if not os.path.exists(path):
        print(f"Error: file '{path}' not found")
        sys.exit(1)
    try:
        run_file(path)
    except FeelError as e:
        print(str(e), file=sys.stderr)
        sys.exit(1)
    except FeelThrow as ft:
        print(f"Uncaught throw: {ft.value}", file=sys.stderr)
        sys.exit(1)


if __name__ == '__main__':
    main()

#!/usr/bin/env python3
import sys
import os

sys.path.insert(0, os.path.dirname(__file__))

from interpreter import Interpreter, run_file

BANNER = """
  ███████╗███████╗███████╗██╗
  ██╔════╝██╔════╝██╔════╝██║
  █████╗  █████╗  █████╗  ██║
  ██╔══╝  ██╔══╝  ██╔══╝  ██║
  ██║     ███████╗███████╗███████╗
  ╚═╝     ╚══════╝╚══════╝╚══════╝
  Feel v0.1  — code that flows
"""

USAGE = """Usage:
  python main.py                    interactive REPL
  python main.py hello.feel         run with interpreter
  python main.py --compile file.feel         compile to binary
  python main.py --compile file.feel -o out  compile to named binary
  python main.py --compile file.feel --keep-c  keep generated .c file
"""

def repl():
    print(BANNER)
    print('Type your Feel code. Type "exit" to quit.\n')
    interp = Interpreter()
    while True:
        try:
            line = input('feel> ')
            if line.strip() in ('exit', 'quit'): break
            if not line.strip(): continue
            interp.run(line)
        except (SyntaxError, NameError, TypeError, RuntimeError) as e:
            print(f'Error: {e}')
        except KeyboardInterrupt:
            print('\nBye!')
            break
        except EOFError:
            break

def main():
    args = sys.argv[1:]

    if not args:
        repl()
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
            if args[i] == '-o' and i+1 < len(args):
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
    except (SyntaxError, NameError, TypeError, RuntimeError) as e:
        print(f'Error: {e}')
        sys.exit(1)

if __name__ == '__main__':
    main()

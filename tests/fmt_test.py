"""feelfmt tests: idempotency + format-preserves-parseability."""

import glob
import os
import sys
import subprocess

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from formatter import format_source, format_file
from parser import parse


def all_feel_files():
    paths = []
    paths.extend(glob.glob(os.path.join(ROOT, '*.feel')))
    paths.extend(glob.glob(os.path.join(ROOT, 'examples', '*.feel')))
    paths.extend(glob.glob(os.path.join(ROOT, 'tests', '*.feel')))
    return sorted(paths)


def test_idempotent():
    fails = []
    for p in all_feel_files():
        with open(p, encoding='utf-8') as f:
            src = f.read()
        once = format_source(src, filename=p)
        twice = format_source(once, filename=p)
        if once != twice:
            fails.append(p)
            print(f"    DIFF for {p}:")
            for i, (a, b) in enumerate(zip(once.splitlines(), twice.splitlines())):
                if a != b:
                    print(f"      line {i+1}: {a!r} -> {b!r}")
                    if i > 3:
                        break
    assert not fails, f"non-idempotent: {fails}"
    print(f"  PASS  idempotent on {len(all_feel_files())} files")


def test_formatted_parses():
    """Formatted output must still be valid Feel (parse without error)."""
    fails = []
    for p in all_feel_files():
        with open(p, encoding='utf-8') as f:
            src = f.read()
        formatted = format_source(src, filename=p)
        try:
            parse(formatted, filename=p)
        except Exception as e:
            fails.append((p, str(e)))
    assert not fails, f"format broke parse: {fails}"
    print(f"  PASS  formatted output parses on {len(all_feel_files())} files")


def test_basic_shape():
    """Spot-check that some specific shapes are correct."""
    cases = [
        ('let x = 5', 'let x = 5'),
        ('let  x   =   5', 'let x = 5'),
        ('let xs = [1,2,3]', 'let xs = [1, 2, 3]'),
        ('let m = map{a:1,b:2}', 'let m = map { a: 1, b: 2 }'),
        ('show -> "hi"', 'show -> "hi"'),
    ]
    for src, expected in cases:
        out = format_source(src).strip()
        assert out == expected, f'src={src!r}: got={out!r}, expected={expected!r}'
    print(f"  PASS  basic shape ({len(cases)} cases)")


def main():
    test_idempotent()
    test_formatted_parses()
    test_basic_shape()
    print('\nAll feelfmt tests passed.')


if __name__ == '__main__':
    main()

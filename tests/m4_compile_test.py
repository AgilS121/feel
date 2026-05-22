"""M4-A: verify compiled Go binary produces same output as interpreter."""

import os
import subprocess
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)


def _normalize(s):
    return s.replace('\r\n', '\n').replace('\r', '\n').rstrip() + '\n'


def _has_go():
    try:
        r = subprocess.run(['go', 'version'], capture_output=True, timeout=5)
        return r.returncode == 0
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return False


def _run_interp(feel_file):
    r = subprocess.run(
        [sys.executable, os.path.join(ROOT, 'main.py'), feel_file],
        capture_output=True, text=True, timeout=30, cwd=ROOT,
    )
    if r.returncode != 0:
        raise RuntimeError(f'interp failed: {r.stderr}')
    return _normalize(r.stdout)


def _run_compiled(feel_file):
    tmp_exe = tempfile.NamedTemporaryFile(suffix='.exe' if os.name == 'nt' else '',
                                          delete=False).name
    try:
        r = subprocess.run(
            [sys.executable, os.path.join(ROOT, 'main.py'), 'build', feel_file, '-o', tmp_exe],
            capture_output=True, text=True, timeout=60, cwd=ROOT,
        )
        if r.returncode != 0:
            raise RuntimeError(f'build failed: {r.stdout}\n{r.stderr}')
        r2 = subprocess.run([tmp_exe], capture_output=True, text=True, timeout=30)
        if r2.returncode != 0:
            raise RuntimeError(f'binary failed: {r2.stderr}')
        return _normalize(r2.stdout)
    finally:
        try:
            os.remove(tmp_exe)
        except OSError:
            pass


def parity(feel_file, label):
    interp_out = _run_interp(feel_file)
    compiled_out = _run_compiled(feel_file)
    if interp_out != compiled_out:
        print(f"  FAIL  {label}")
        print(f"    interp:   {interp_out!r}")
        print(f"    compiled: {compiled_out!r}")
        return False
    print(f"  PASS  {label}")
    return True


def main():
    if not _has_go():
        print('M4-A tests skipped (Go toolchain not installed)')
        return 0

    cases = [
        ('examples/m4_hello.feel', 'm4_hello (let, show, when, arithmetic)'),
        ('examples/m4_features.feel', 'm4_features (define, lambda, list, for, repeat)'),
        ('examples/m4_map_pipeline.feel', 'm4_map_pipeline (map, indexing, pipeline, nested when)'),
        ('examples/m4b_features.feel', 'm4b_features (try/catch, records, string/list/map/json stdlib)'),
    ]

    failed = 0
    for path, label in cases:
        full = os.path.join(ROOT, path)
        try:
            ok = parity(full, label)
            if not ok:
                failed += 1
        except Exception as e:
            print(f"  FAIL  {label}: {e}")
            failed += 1

    if failed:
        print(f'\n{failed} M4-A compile-parity tests failed.')
        return 1
    print(f'\nAll {len(cases)} M4-A compile-parity tests passed.')
    return 0


if __name__ == '__main__':
    sys.exit(main())

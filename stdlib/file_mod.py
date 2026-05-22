"""file module — IO ke filesystem."""

import os


def read(path):
    with open(path, encoding='utf-8') as f:
        return f.read()


def write(path, content):
    with open(path, 'w', encoding='utf-8') as f:
        f.write(str(content))
    return True


def append(path, content):
    with open(path, 'a', encoding='utf-8') as f:
        f.write(str(content))
    return True


def exists(path):
    return os.path.exists(path)


def is_file(path):
    return os.path.isfile(path)


def is_dir(path):
    return os.path.isdir(path)


def list_dir(path):
    return os.listdir(path)


def delete(path):
    if os.path.isfile(path):
        os.remove(path)
        return True
    return False


def basename(path):
    return os.path.basename(path)


def dirname(path):
    return os.path.dirname(path)


def join(*parts):
    return os.path.join(*[str(p) for p in parts])


EXPORTS = {
    'read':     read,
    'write':    write,
    'append':   append,
    'exists':   exists,
    'is_file':  is_file,
    'is_dir':   is_dir,
    'list_dir': list_dir,
    'delete':   delete,
    'basename': basename,
    'dirname':  dirname,
    'join':     join,
}

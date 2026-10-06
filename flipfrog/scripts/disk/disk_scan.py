#!/usr/bin/env python3
"""Escáner de uso de disco para disk_usage_popup.py -- sin GTK ni imports
del repo (lo corre `pkexec` como root cuando hace falta leer carpetas
protegidas). Recorre `root` sin salir del dispositivo de `root`: en btrfs
cada subvolumen (/home, /var/log...) tiene su propio st_dev, así que el
límite sale de /proc/self/mounts por dispositivo de origen, no de st_dev.

stdout: un JSON al terminar. Nodo = [nombre, bytes, n_archivos,
bytes_archivos, archivos_grandes, hijos, denegado, archivos_total] -- bytes asignados
(st_blocks), recursivos; archivos_grandes = [[nombre, bytes], ...] los
TOP_FILES más pesados de esa carpeta (sin contar subcarpetas); archivos_total es recursivo. Hardlinks
cuentan una sola vez. stderr: "<entradas>\\t<ruta>" cada PROGRESS_S."""

import json
import os
import stat
import sys
import time

TOP_FILES = 25
PROGRESS_S = 0.2
VIRTUAL_FS = {"proc", "sysfs", "devtmpfs", "devpts", "tmpfs", "cgroup", "cgroup2",
              "mqueue", "debugfs", "tracefs", "securityfs", "pstore", "bpf",
              "configfs", "fusectl", "hugetlbfs", "efivarfs", "autofs", "binfmt_misc",
              "ramfs", "overlay", "squashfs", "nsfs"}


def _unescape(path):
    return path.replace("\\040", " ").replace("\\011", "\t").replace("\\134", "\\")


def excluded_mounts(root):
    """Puntos de montaje dentro de `root` que NO son del mismo dispositivo
    (otras particiones, FUSE, sistemas de archivos virtuales)."""
    mounts = []
    with open("/proc/self/mounts") as f:
        for line in f:
            source, target, fstype = line.split()[:3]
            mounts.append((_unescape(source), _unescape(target), fstype))

    root = os.path.realpath(root)
    owner = max((m for m in mounts if root == m[1] or root.startswith(m[1].rstrip("/") + "/")),
                key=lambda m: len(m[1]), default=None)
    return {target for source, target, fstype in mounts
            if target != root and (owner is None or source != owner[0]
                                   or fstype in VIRTUAL_FS or fstype.startswith("fuse"))}


def scan(root):
    skip = excluded_mounts(root)
    seen_inodes = set()
    counter = [0]
    last = [0.0]

    def walk(path, name):
        size = files = fsize = total_files = 0
        top = []
        children = []
        try:
            it = os.scandir(path)
        except OSError:
            return [name, 0, 0, 0, [], [], 1, 0]
        with it:
            for entry in it:
                counter[0] += 1
                try:
                    st = entry.stat(follow_symlinks=False)
                except OSError:
                    continue
                if stat.S_ISDIR(st.st_mode):
                    if entry.path in skip:
                        continue
                    child = walk(entry.path, entry.name)
                    child[1] += st.st_blocks * 512
                    size += child[1]
                    total_files += child[7]
                    children.append(child)
                    continue
                if st.st_nlink > 1:
                    key = (st.st_dev, st.st_ino)
                    if key in seen_inodes:
                        continue
                    seen_inodes.add(key)
                used = st.st_blocks * 512
                files += 1
                fsize += used
                if len(top) < TOP_FILES or used > top[-1][1]:
                    top.append([entry.name, used])
                    top.sort(key=lambda f: f[1], reverse=True)
                    del top[TOP_FILES:]

        now = time.monotonic()
        if now - last[0] > PROGRESS_S:
            last[0] = now
            print(f"{counter[0]}\t{path}", file=sys.stderr, flush=True)
        children.sort(key=lambda c: c[1], reverse=True)
        return [name, size + fsize, files, fsize, top, children, 0, total_files + files]

    # Sin recursión de Python profunda (árboles de node_modules, etc.).
    sys.setrecursionlimit(20000)
    tree = walk(root, root)
    return {"root": root, "entries": counter[0], "time": time.time(),
            "as_root": os.geteuid() == 0, "tree": tree}


def main():
    root = sys.argv[1] if len(sys.argv) > 1 else "/"
    json.dump(scan(root), sys.stdout, separators=(",", ":"), ensure_ascii=False)


if __name__ == "__main__":
    main()

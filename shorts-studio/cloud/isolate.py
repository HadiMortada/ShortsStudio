"""Run a command without network access other than loopback, as an unprivileged user.

Usage (as root, normally via ``sudo -n unshare --net --``):
    python -I isolate.py UID GID ENV_JSON_FILE program [args...]

Remotion serves the bundle to Chromium over 127.0.0.1, so loopback is brought up inside the new
network namespace; nothing else is reachable. The child gets exactly the environment in
ENV_JSON_FILE, never the parent's secrets.
"""
import fcntl
import json
import os
import socket
import struct
import sys

SIOCGIFFLAGS, SIOCSIFFLAGS, IFF_UP = 0x8913, 0x8914, 0x1


def loopback_up():
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
        flags = struct.unpack('16sH22x', fcntl.ioctl(s, SIOCGIFFLAGS, struct.pack('16sH22x', b'lo', 0)))[1]
        fcntl.ioctl(s, SIOCSIFFLAGS, struct.pack('16sH22x', b'lo', flags | IFF_UP))


def main(argv):
    if len(argv) < 5:
        raise SystemExit(__doc__)
    uid, gid = int(argv[1]), int(argv[2])
    with open(argv[3], encoding='utf-8') as stream:
        env = json.load(stream)
    loopback_up()
    os.setgroups([])
    os.setgid(gid)
    os.setuid(uid)
    os.execve(argv[4], argv[4:], env)


if __name__ == '__main__':
    main(sys.argv)

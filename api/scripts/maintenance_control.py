"""Local root-only drain control. Never stops/restarts services or writes a DB."""
import argparse
import json
import os
from pathlib import Path
import secrets
import socket
import struct
import time


def control(path, pid, command, seconds=90):
    if not hasattr(socket, 'SO_PEERCRED') or os.geteuid() != 0 or pid <= 0:
        raise PermissionError('Linux root and expected API PID are required')
    path = Path(path)
    if not path.is_absolute() or '..' in path.parts or any(p.is_symlink() for p in (path, *path.parents)):
        raise ValueError('invalid control socket path')
    nonce = secrets.token_hex(16)
    with socket.socket(socket.AF_UNIX) as connection:
        connection.settimeout(4)
        connection.connect(str(path))
        peer = struct.unpack('3i', connection.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, struct.calcsize('3i')))
        if peer[0] != pid:
            raise PermissionError('control peer is not the expected API PID')
        connection.sendall((json.dumps({'command': command, 'timeout_seconds': seconds, 'nonce': nonce}) + '\n').encode())
        data = b''
        while not data.endswith(b'\n') and len(data) <= 16384:
            chunk = connection.recv(4096)
            if not chunk:
                break
            data += chunk
    reply = json.loads(data)
    if reply.get('pid') != pid or reply.get('nonce') != nonce:
        raise PermissionError('invalid control response identity')
    if reply.get('state') == 'SAFE_TO_STOP' and reply.get('deadline', 0) <= time.monotonic():
        return {'state': 'BLOCKED', 'reason': 'expired_response'}
    return reply


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command', choices=['begin', 'status', 'resume'])
    parser.add_argument('--socket', type=Path, required=True)
    parser.add_argument('--pid', type=int, required=True, help='fresh systemd MainPID, not a cached PID')
    parser.add_argument('--timeout-seconds', type=float, default=90)
    args = parser.parse_args()
    try:
        result = control(args.socket, args.pid, args.command, args.timeout_seconds)
    except (OSError, ValueError, PermissionError, KeyError, TypeError) as error:
        result = {'state': 'BLOCKED', 'reason': type(error).__name__}
    print(json.dumps(result, sort_keys=True))
    return 0 if result['state'] in {'SAFE_TO_STOP', 'RUNNING'} else 2


if __name__ == '__main__':
    raise SystemExit(main())

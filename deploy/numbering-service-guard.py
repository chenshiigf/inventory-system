#!/usr/bin/python3
"""Root-installed ExecStartPre guard; independent of the replaceable API code."""
import json
import os
from pathlib import Path
import sqlite3
import sys

DATA = Path('/var/lib/inventory-system')
CAPABILITY = Path('/srv/inventory-system/api/app/numbering-v2.json')


def verify(data=DATA, capability=CAPABILITY):
    database = data / 'inventory.db'
    if not database.is_file() or database.is_symlink():
        raise RuntimeError('Business database missing or aliased')
    directory = data / 'numbering-maintenance'
    if os.path.lexists(directory):
        # Hold all normal service starts, including reboot. Even compatible API
        # lifespan can initialize AI state; controlled read-only acceptance is a
        # separate 3B-2 prerequisite, never an implicit restart here.
        state_file = directory / 'state.json'
        if directory.is_symlink() or state_file.is_symlink():
            raise RuntimeError('Maintenance marker is aliased')
        state = json.loads(state_file.read_text())
        if state.get('format') != 'numbering-maintenance-v1' or state.get('state') != 'released':
            raise RuntimeError('Numbering maintenance remains held; normal API startup is forbidden')
    with sqlite3.connect(database.as_uri() + '?mode=ro', uri=True) as db:
        registered = db.execute("SELECT count(*) FROM sqlite_master WHERE name='production_numbering_cutovers'").fetchone()[0]
        committed = registered and db.execute('SELECT count(*) FROM production_numbering_cutovers').fetchone()[0]
    if os.path.lexists(directory) or committed:
        if capability.is_symlink() or json.loads(capability.read_text()) != {
                'scheme': 'product-numbering-v2', 'maintenance_protocol': 1}:
            raise RuntimeError('Maintenance or committed numbering forbids starting incompatible old code')


if __name__ == '__main__':
    try:
        verify()
    except Exception as error:
        print('API start refused: ' + str(error), file=sys.stderr)
        sys.exit(1)

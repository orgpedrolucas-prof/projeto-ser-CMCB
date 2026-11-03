"""Backup consistente do SQLite e copia de anexos locais, em ZIP.

Recomendado parar o servidor antes de copiar as fotos para evitar divergencias.
"""
from pathlib import Path
from datetime import datetime
import sqlite3
import tempfile
import zipfile
import shutil

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / 'instance'
BACKUPS = ROOT / 'BACKUPS_SER'


def backup():
    database = DATA / 'simr.db'
    if not database.is_file():
        raise RuntimeError('Banco inexistente: instale e abra o SER primeiro.')
    BACKUPS.mkdir(exist_ok=True)
    name = 'SER_Backup_' + datetime.now().strftime('%Y%m%d_%H%M%S') + '.zip'
    target = BACKUPS / name
    with tempfile.TemporaryDirectory() as temporary:
        snapshot = Path(temporary) / 'simr.db'
        with sqlite3.connect(database) as src, sqlite3.connect(snapshot) as dst:
            src.backup(dst)
        with zipfile.ZipFile(target, 'w', compression=zipfile.ZIP_DEFLATED, compresslevel=6) as result:
            result.write(snapshot, 'instance/simr.db')
            for file in DATA.rglob('*'):
                if file.is_file() and file != database and not file.name.endswith(('-wal','-shm','-journal','.lock')):
                    result.write(file, 'instance/' + str(file.relative_to(DATA)).replace('\\','/'))
    return target


if __name__ == '__main__':
    print('SER - Backup local (contem dados pessoais; armazene em local seguro).')
    try:
        path=backup()
        print('Backup gerado:', path)
        print('Copie para um pendrive/armazenamento protegido. Feche o SER para backup completo das fotos.')
    except Exception as ex:
        print('Falha no backup:', ex)
        raise SystemExit(1)

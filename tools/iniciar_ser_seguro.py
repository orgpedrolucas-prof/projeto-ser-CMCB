"""Inicializacao do SER com diagnosticos, sem alterar o banco de dados.

Modificado para aceitar conexões locais via Wi-Fi (celular e outros PCs).
"""
from __future__ import annotations
import os
import sys
import struct
import shutil
import subprocess
import traceback
from pathlib import Path
from datetime import datetime
import argparse
import socket
import time
import urllib.request
import webbrowser

ROOT = Path(__file__).resolve().parent.parent
VENV = ROOT / '.venv'
INSTANCE = ROOT / 'instance'
LOG = ROOT / 'SER_ERRO_INICIALIZACAO.log'
CHECK = 'import sys,struct;ok=sys.version_info[:2]==(3,15) and sys.version_info>=(3,15,2) and struct.calcsize("P")==8;print(sys.executable if ok else "");sys.exit(0 if ok else 1)'


def report(message: str):
    print(message, flush=True)
    try:
        with LOG.open('a', encoding='utf-8') as f:
            f.write(f'[{datetime.now().strftime("%Y-%m-%d %H:%M:%S")}] {message}\n')
    except OSError:
        pass


def is_315(executable: str, prefix=()):
    try:
        proc = subprocess.run([executable, *prefix, '-c', CHECK], capture_output=True,
                              text=True, timeout=20, check=False)
        result = proc.stdout.strip().splitlines()
        if proc.returncode == 0 and result:
            path = Path(result[-1].strip())
            if path.is_file():
                return path
    except (OSError, subprocess.TimeoutExpired, ValueError):
        pass
    return None


def locate_python():
    candidates = []
    launcher = shutil.which('py')
    if launcher:
        candidates.append((launcher, ('-3.15',)))
    candidates.append((sys.executable, ()))
    for name in ('python', 'python3'):
        exe = shutil.which(name)
        if exe:
            candidates.append((exe, ()))
    if os.name == 'nt':
        env = os.environ
        candidates.extend([
            (str(Path(env.get('LOCALAPPDATA', '')) / 'Programs' / 'Python' / 'Python315' / 'python.exe'), ()),
            (str(Path(env.get('ProgramFiles', '')) / 'Python315' / 'python.exe'), ()),
            (str(Path(env.get('ProgramFiles', '')) / 'Python' / 'Python315' / 'python.exe'), ()),
        ])
    for cmd, flags in candidates:
        result = is_315(cmd, flags)
        if result:
            return result
    return None


def run(args, desc, check=True, env=None):
    report(desc)
    code = subprocess.call([str(x) for x in args], cwd=str(ROOT), env=env)
    if check and code != 0:
        raise RuntimeError(f'{desc} falhou (codigo {code}). Veja a mensagem anterior.')
    return code


def requirements_ok(python: Path):
    proc = subprocess.run([str(python), '-c',
        "import fastapi,uvicorn,jinja2,multipart,itsdangerous,reportlab,openpyxl,PIL;"
        "assert 13 <= int(PIL.__version__.split('.')[0]) < 14"],
        cwd=str(ROOT), stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False)
    return proc.returncode == 0


def private_ipv4_addresses():
    import ipaddress
    results = []
    try:
        import subprocess
        if os.name == 'nt':
            cmd = ['powershell.exe', '-NoProfile', '-Command',
                   '(Get-NetIPAddress -AddressFamily IPv4 | Where-Object { $_.IPAddress -notlike "169.254*" -and $_.IPAddress -notlike "127.*" }).IPAddress']
            proc = subprocess.run(cmd, capture_output=True, text=True, timeout=6, check=False)
            choices = proc.stdout.splitlines()
        else:
            choices = [a[4][0] for a in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET)]
        for ip in choices:
            try:
                addr = ipaddress.ip_address(ip.strip())
                if addr.version == 4 and addr.is_private and not addr.is_loopback and str(addr) not in results:
                    results.append(str(addr))
            except ValueError:
                pass
    except Exception:
        pass
    return results


def port_available(host: str, port: int) -> bool:
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as test:
            test.bind((host, port))
        return True
    except OSError:
        return False


def startup(diagnostic_only=False):
    if not (ROOT / 'simr' / 'main.py').is_file():
        raise RuntimeError('Arquivos do SER incompletos: falta simr/main.py. Extraia o ZIP inteiro.')
    report('SER - inicializacao com diagnostico')
    if (INSTANCE / 'simr.db').exists():
        report('Banco existente identificado em instance. Nao sera apagado.')
    current_env_python = VENV / 'Scripts' / 'python.exe' if os.name == 'nt' else VENV / 'bin' / 'python'
    if current_env_python.is_file() and is_315(str(current_env_python)):
        report('Ambiente virtual Python 3.15 compativel encontrado.')
        python = current_env_python
    else:
        base = locate_python()
        if not base:
            report('Python disponivel no iniciador: '+sys.version.splitlines()[0])
            raise RuntimeError('Python 3.15.2 de 64 bits nao encontrado. No CMD, confira: py -3.15 --version e python --version.')
        report('Python 3.15 encontrado: '+str(base))
        if diagnostic_only:
            report('Diagnostico concluido: o Python base e compativel. Nenhuma pasta foi alterada.')
            return 0
        if VENV.exists():
            backup = ROOT / ('.venv_anterior_' + datetime.now().strftime('%Y%m%d_%H%M%S'))
            n = 0
            while backup.exists():
                n += 1
                backup = ROOT / ('.venv_anterior_'+datetime.now().strftime('%Y%m%d_%H%M%S')+'_'+str(n))
            report('Salvando ambiente anterior em '+backup.name)
            VENV.rename(backup)
        run([base, '-m', 'venv', VENV], 'Criando ambiente Python local .venv')
        python = current_env_python
        if not python.is_file():
            raise RuntimeError('Python nao foi criado dentro de .venv. Verifique permissao da pasta.')
    if diagnostic_only:
        report('Diagnostico: ambiente local compativel. Nenhum dado foi modificado.')
        return 0
    if not requirements_ok(python):
        report('Bibliotecas ausentes ou desatualizadas. Preparando dependencias.')
        pip = subprocess.run([str(python), '-m', 'pip', '--version'], cwd=str(ROOT), check=False,
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        if pip.returncode != 0:
            run([python, '-m', 'ensurepip', '--upgrade'], 'Habilitando pip')
        run([python, '-m', 'pip', 'install', '--disable-pip-version-check', '--prefer-binary', '-r', 'requirements.txt'],
            'Instalando bibliotecas (Internet necessaria na primeira vez)')
        if not requirements_ok(python):
            raise RuntimeError('As dependencias continuam incompletas apos a instalacao.')
    else:
        report('Bibliotecas do SER verificadas.')
    
    report('Iniciando configuracao administrativa / banco local')
    run([python, '-m', 'simr.init_admin'], 'Verificando administrador inicial')

    # Configurar porta e host 0.0.0.0 para acesso via Wi-Fi/celular
    port = next((p for p in range(8000, 8011) if port_available('0.0.0.0', p)), 8000)
    url_local = f'http://127.0.0.1:{port}/entrar'
    
    print('='*58, flush=True)
    print('SER - SISTEMA ESCOLAR DE RESULTADOS (Modo Wi-Fi Ativo)', flush=True)
    print('No computador: ' + url_local, flush=True)
    print('No telemóvel/celular (mesmo Wi-Fi):', flush=True)
    addresses = private_ipv4_addresses()
    if addresses:
        for address in addresses:
            print(f'  http://{address}:{port}/entrar', flush=True)
    else:
        print('  (Verifique o IP com ipconfig no terminal)', flush=True)
    print('Deixe esta janela aberta para manter o SER funcionando.', flush=True)
    print('='*58, flush=True)

    # Iniciar uvicorn diretamente em 0.0.0.0 para garantir acesso externo
    report('Iniciando servidor Uvicorn em 0.0.0.0:' + str(port))
    return run([python, '-m', 'uvicorn', 'simr.main:app', '--host', '0.0.0.0', '--port', str(port), '--log-level', 'info'], 
               'Servidor SER ativo; feche a janela para encerrar', check=False)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--diagnostico', action='store_true')
    args = parser.parse_args()
    try:
        return startup(args.diagnostico)
    except (Exception, KeyboardInterrupt) as exc:
        report('ERRO: '+str(exc))
        if not isinstance(exc, KeyboardInterrupt):
            traceback.print_exc()
        report('O ERRO acima foi registrado em SER_ERRO_INICIALIZACAO.log.')
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
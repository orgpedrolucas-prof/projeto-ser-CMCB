"""Inicia o SER no estilo da versao 01: local, porta 8000 e navegador.

O servidor permanece Python + Uvicorn; nao faz instalacao no Windows.
"""
from __future__ import annotations
import argparse
import os
from pathlib import Path
import socket
import threading
import time
import urllib.request
import webbrowser

ROOT = Path(__file__).resolve().parent


def port_available(host: str, port: int) -> bool:
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as test:
            test.bind((host, port))
        return True
    except OSError:
        return False


def private_ipv4_addresses():
    import ipaddress
    results=[]
    try:
        import subprocess
        if os.name == 'nt':
            cmd = ['powershell.exe','-NoProfile','-Command',
                   '(Get-NetIPAddress -AddressFamily IPv4 | Where-Object { $_.IPAddress -notlike "169.254*" -and $_.IPAddress -notlike "127.*" }).IPAddress']
            proc = subprocess.run(cmd,capture_output=True,text=True,timeout=6,check=False)
            choices=proc.stdout.splitlines()
        else:
            choices=[a[4][0] for a in socket.getaddrinfo(socket.gethostname(),None,socket.AF_INET)]
        for ip in choices:
            try:
                addr=ipaddress.ip_address(ip.strip())
                if addr.version==4 and addr.is_private and not addr.is_loopback and str(addr) not in results:
                    results.append(str(addr))
            except ValueError:
                pass
    except Exception:
        pass
    return results


def open_browser_after_start(url: str):
    def task():
        for _ in range(80):
            time.sleep(0.25)
            try:
                with urllib.request.urlopen(url, timeout=0.5) as res:
                    if res.status < 500:
                        webbrowser.open(url, new=2)
                        return
            except Exception:
                continue
        print('O navegador nao abriu sozinho. Copie o link:', url)
    threading.Thread(target=task,daemon=True).start()


def main():
    parser=argparse.ArgumentParser(description='SER no navegador')
    parser.add_argument('--wifi',action='store_true')
    args=parser.parse_args()
    host='0.0.0.0' if args.wifi else '127.0.0.1'
    port=next((p for p in range(8000,8011) if port_available(host,p)),None)
    if port is None:
        print('ERRO: todas as portas 8000 a 8010 estao ocupadas. Feche outras janelas do SER.')
        return 2
    url=f'http://127.0.0.1:{port}/entrar'
    print('='*58,flush=True)
    if port!=8000:
        print('AVISO: porta 8000 ocupada; usando a porta',port,flush=True)
    print('SER - SISTEMA ESCOLAR DE RESULTADOS',flush=True)
    print('No computador: '+url,flush=True)
    if args.wifi:
        print('No celular, usando o MESMO WI-FI:',flush=True)
        for address in private_ipv4_addresses():
            print(f'  http://{address}:{port}/entrar',flush=True)
        print('Se nao houver link, descubra o IPv4 com ipconfig.',flush=True)
        print('AVISO: conexao sem criptografia; somente testes ficticios.',flush=True)
    print('Deixe esta janela aberta para manter o SER funcionando.',flush=True)
    print('='*58,flush=True)
    open_browser_after_start(url)
    import uvicorn
    uvicorn.run('simr.main:app',host=host,port=port,log_level='info')
    return 0

if __name__=='__main__':
    raise SystemExit(main())

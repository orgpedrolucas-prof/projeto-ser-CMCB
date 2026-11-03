"""Bootstrap da primeira conta administrativa, sem credenciais padrão."""
import getpass
import re
from .db import initialize,connection,password_hash

def main():
    initialize()
    with connection() as db:
        count=db.execute('SELECT COUNT(*) FROM users').fetchone()[0]
    if count:
        print('Já existem usuários. Gerencie contas em /usuarios após entrar no sistema.')
        return
    print('\nSER — Configuração inicial do administrador\n')
    full_name=input('Nome completo: ').strip()
    username=input('Usuário (letras minúsculas, números, . _ -): ').strip().lower()
    password=getpass.getpass('Senha (mínimo de 12 caracteres): ')
    password2=getpass.getpass('Confirmar senha: ')
    if not full_name or len(full_name)>150 or not re.fullmatch(r'[a-z0-9._-]{3,50}',username):
        print('Nome ou usuário inválido.');raise SystemExit(1)
    if len(password)<12 or password!=password2:
        print('As senhas não coincidem ou têm menos de 12 caracteres.');raise SystemExit(1)
    with connection() as db:
        db.execute('INSERT INTO users(username,full_name,password_hash,role) VALUES(?,?,?,?)',
                   (username,full_name,password_hash(password),'admin'))
    print('\nAdministrador criado. Acesse o sistema com suas credenciais.')

if __name__=='__main__': main()

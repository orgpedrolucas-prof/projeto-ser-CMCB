"""Testes: API, banco, regras e geração de relatórios."""
import os
import tempfile
from pathlib import Path

# Instalação do SIMR_INSTANCE precisa ocorrer ANTES da importação dos módulos.
_test_instance=tempfile.TemporaryDirectory()
os.environ['SIMR_INSTANCE']=_test_instance.name
from fastapi.testclient import TestClient
from simr.db import connection,initialize,password_hash
from simr.main import app
from simr.logic import academic,class_metrics,rankings,elite_score


def seed():
    with connection() as db:
        db.executescript('''DELETE FROM audit_log; DELETE FROM behavior_deductions; DELETE FROM occurrence_attachments; DELETE FROM occurrence_actions; DELETE FROM occurrences; DELETE FROM adjustments; DELETE FROM decisions; DELETE FROM elite_evaluations;
        DELETE FROM plans; DELETE FROM evolution; DELETE FROM rubrics; DELETE FROM inspections; DELETE FROM assessment_results;
        DELETE FROM assessments; DELETE FROM enrollments; DELETE FROM students; DELETE FROM classes; DELETE FROM users;''')
        db.execute('INSERT INTO users(id,username,full_name,password_hash,role) VALUES(1,?,?,?,?)',
                   ('admin','Administrador de teste',password_hash('uma-senha-super-segura'),'admin'))
        db.execute("INSERT INTO classes(id,name,grade,school_year,shift) VALUES(1,'7º A',7,2027,'Matutino')")
        db.execute("INSERT INTO classes(id,name,grade,school_year,shift) VALUES(2,'7º B',7,2027,'Matutino')")
        db.execute("INSERT INTO students(id,registration,name) VALUES(1,'T001','Pessoa Teste 1'),(2,'T002','Pessoa Teste 2')")
        db.execute("INSERT INTO enrollments(student_id,class_id) VALUES(1,1),(2,2)")
        db.execute("INSERT INTO assessments(id,title,school_year,trimester,grade,status) VALUES(1,'T1 2027',2027,1,7,'validado')")
        db.execute("INSERT INTO assessment_results(assessment_id,student_id,class_id,attendance,correct_p,correct_m,correct_g) VALUES(1,1,1,'presente',20,20,20)")
        db.execute("INSERT INTO assessment_results(assessment_id,student_id,class_id,attendance,correct_p,correct_m,correct_g) VALUES(1,2,2,'justificada',NULL,NULL,NULL)")
        for cid in (1,2):
            db.execute("""INSERT INTO inspections(class_id,school_year,bimester,trimester,date,behavior,order_drill,punctuality,rules,conservation,cooperation,status)
                         VALUES(?,2027,1,1,'2027-02-01',25,20,15,10,10,10,'validado')""",(cid,))
        db.execute("INSERT INTO rubrics(id,title,description,status) VALUES(1,'Rubrica aprovada','Critérios de teste objetivos','aprovada')")
        for cid in (1,2):
            db.execute("INSERT INTO evolution(class_id,school_year,trimester,score,evidence,rubric_id,status) VALUES(?,2027,1,70,'Evidência registrada',1,'validado')",(cid,))


def test_formulas_and_justified_absence():
    seed()
    with connection() as db:
        first=academic(db,1,2027,1)
        assert first['p']==80 and first['m']==80 and first['g']==80
        metrics=class_metrics(db,db.execute('SELECT * FROM classes WHERE id=1').fetchone(),1)
        assert metrics['D']==90
        assert metrics['R']==82
        second=academic(db,2,2027,1)
        assert second['justified']==1 and second['a'] is None
        assert len(rankings(db,2027,1))==1


def test_elite_criteria_and_formula():
    seed()
    with connection() as db:
        db.execute("UPDATE settings SET value='1' WHERE key='elite_criteria_approved'")
        db.execute("""INSERT INTO elite_evaluations(student_id,school_year,trimester,conduct,drill,frequency,training,blocked)
            VALUES(1,2027,1,100,100,100,1,0)""")
        row=db.execute('SELECT * FROM elite_evaluations WHERE student_id=1').fetchone()
        result=elite_score(db,row)
        assert result['score']==92
        assert result['eligible'] is True


def test_http_login_csrf_and_report_exports():
    seed()
    client=TestClient(app)
    resp=client.get('/',follow_redirects=False);assert resp.status_code in (302,303)
    login=client.get('/entrar');assert login.status_code==200
    from bs4 import BeautifulSoup
    csrf=BeautifulSoup(login.text,'html.parser').select_one('input[name="_csrf"]')['value']
    resp=client.post('/entrar',data={'username':'admin','password':'uma-senha-super-segura','_csrf':csrf},follow_redirects=False)
    assert resp.status_code==303
    csrf=BeautifulSoup(client.get('/turmas').text,'html.parser').select_one('input[name="_csrf"]')['value']
    dash=client.get('/?ano=2027&trimestre=1')
    assert dash.status_code==200 and '82.0' in dash.text
    html=client.get('/ranking?ano=2027&trimestre=1')
    assert html.status_code==200 and '7º A' in html.text
    assert client.post('/turmas',data={'name':'8º A','grade':'8','school_year':'2027','shift':'Matutino'}).status_code==403
    report=client.get('/relatorios/pdf?ano=2027&trimestre=1')
    assert report.status_code==200 and report.content.startswith(b'%PDF')
    progress=client.get('/relatorios/analitico/pdf?ano=2027&inicio=1&fim=3')
    assert progress.status_code==200 and progress.content.startswith(b'%PDF')
    progressive_xlsx=client.get('/relatorios/analitico/xlsx?ano=2027&inicio=1&fim=3')
    assert progressive_xlsx.status_code==200 and progressive_xlsx.content.startswith(b'PK')
    xlsx=client.get('/relatorios/xlsx?ano=2027&trimestre=1')
    assert xlsx.status_code==200 and xlsx.content.startswith(b'PK')
    csv_resp=client.get('/relatorios/csv?ano=2027&trimestre=1')
    assert csv_resp.status_code==200 and '7º A' in csv_resp.content.decode('utf-8')


def test_create_assessment_and_inspection_flow():
    seed()
    client=TestClient(app)
    page=client.get('/entrar')
    from bs4 import BeautifulSoup
    csrf=BeautifulSoup(page.text,'html.parser').select_one('input[name="_csrf"]')['value']
    client.post('/entrar',data={'username':'admin','password':'uma-senha-super-segura','_csrf':csrf})
    csrf=BeautifulSoup(client.get('/simulados/novo').text,'html.parser').select_one('input[name="_csrf"]')['value']
    r=client.post('/simulados/novo',data={'_csrf':csrf,'title':'T2','school_year':'2027','trimester':'2','grade':'7'},follow_redirects=False)
    assert r.status_code==303
    with connection() as db:
        a=db.execute("SELECT * FROM assessments WHERE trimester=2").fetchone()
        assert a and a['status']=='rascunho'
    r=client.post(f'/simulados/{a["id"]}/resultado/1',data={'_csrf':csrf,'attendance':'presente','correct_p':'18','correct_m':'15','correct_g':'20'},follow_redirects=False)
    assert r.status_code==303
    # A validação deve exigir situação registrada para todos os matriculados.
    r=client.post(f'/simulados/{a["id"]}/validar',data={'_csrf':csrf},follow_redirects=False)
    assert r.status_code==400
    r=client.post(f'/simulados/{a["id"]}/resultado/2',data={'_csrf':csrf,'attendance':'justificada'},follow_redirects=False)
    assert r.status_code==303
    r=client.post(f'/simulados/{a["id"]}/validar',data={'_csrf':csrf},follow_redirects=False)
    assert r.status_code==303
    with connection() as db:
        assert db.execute('SELECT status FROM assessments WHERE id=?',(a['id'],)).fetchone()['status']=='validado'
    r=client.post('/vistorias',data={'_csrf':csrf,'class_id':'1','trimester':'2','bimester':'3','date':'2027-07-01','behavior':'25','order_drill':'25',
                         'punctuality':'15','rules':'15','conservation':'10','cooperation':'10','notes':'teste'},follow_redirects=False)
    assert r.status_code==303


def admin_client():
    from bs4 import BeautifulSoup
    client=TestClient(app)
    login=client.get('/entrar')
    token=BeautifulSoup(login.text,'html.parser').select_one('input[name="_csrf"]')['value']
    response=client.post('/entrar',data={'_csrf':token,'username':'admin','password':'uma-senha-super-segura'},follow_redirects=False)
    assert response.status_code==303
    token=BeautifulSoup(client.get('/alunos').text,'html.parser').select_one('input[name="_csrf"]')['value']
    return client,token


def test_student_profile_and_locked_grade_requires_admin_password():
    seed(); client,csrf=admin_client()
    detail=client.get('/alunos/1')
    assert detail.status_code==200
    assert 'Aluno' in detail.text and 'Pelotão de Elite' in detail.text
    assert 'Vistorias disciplinares das turmas' in detail.text
    assert 'Editar notas' in detail.text
    route='/simulados/1/resultado/1'
    base={'_csrf':csrf,'attendance':'presente','correct_p':'25','correct_m':'20','correct_g':'20',
          'justification':'Correção de prova após revisão docente'}
    assert client.post(route,data=base,follow_redirects=False).status_code==403
    assert client.post(route,data={**base,'admin_password':'errada'},follow_redirects=False).status_code==403
    with connection() as db:
        assert db.execute('SELECT correct_p FROM assessment_results WHERE student_id=1').fetchone()['correct_p']==20
    assert client.post(route,data={**base,'admin_password':'uma-senha-super-segura'},follow_redirects=False).status_code==303
    with connection() as db:
        assert db.execute('SELECT correct_p FROM assessment_results WHERE student_id=1').fetchone()['correct_p']==25
        records=db.execute("SELECT * FROM audit_log WHERE action='retificar_validado'").fetchall()
        assert len(records)==1
        assert '"antes": 20' in records[0]['details']
        assert '"depois": 25' in records[0]['details']
        assert 'uma-senha-super-segura' not in records[0]['details']
        metrics=class_metrics(db,db.execute('SELECT * FROM classes WHERE id=1').fetchone(),1)
        assert round(metrics['R'],2)==86.0


def test_edit_student_with_validated_results_requires_password():
    seed();client,csrf=admin_client()
    path='/alunos/1/editar'
    d={'_csrf':csrf,'name':'Nome Atualizado','registration':'T001','class_id':'1','active':'1',
       'justification':'Retificação do nome no cadastro escolar'}
    assert client.post(path,data=d,follow_redirects=False).status_code==403
    assert client.post(path,data={**d,'admin_password':'uma-senha-super-segura'},follow_redirects=False).status_code==303
    with connection() as db:
        assert db.execute('SELECT name FROM students WHERE id=1').fetchone()['name']=='Nome Atualizado'
        details=db.execute("SELECT details FROM audit_log WHERE object_type='students' AND action='editar'").fetchone()['details']
        assert 'Pessoa Teste 1' in details and 'Nome Atualizado' in details


def test_assessment_edit_archive_and_restore_are_controlled_and_audited():
    seed();client,csrf=admin_client()
    assert client.get('/simulados/1/editar').status_code==200
    params={'_csrf':csrf,'title':'Corrigido','school_year':'2027','trimester':'1','grade':'7',
            'date':'2027-04-01','justification':'Revisão e aprovação da equipe pedagógica'}
    path='/simulados/1/editar'
    assert client.post(path,data=params,follow_redirects=False).status_code==403
    assert client.post(path,data={**params,'admin_password':'uma-senha-super-segura'},follow_redirects=False).status_code==303
    with connection() as db:
        assert db.execute('SELECT title FROM assessments WHERE id=1').fetchone()['title']=='Corrigido'
    deletion={'_csrf':csrf,'justification':'Arquivamento por determinação da gestão'}
    assert client.post('/simulados/1/excluir',data=deletion,follow_redirects=False).status_code==403
    assert client.post('/simulados/1/excluir',data={**deletion,'admin_password':'uma-senha-super-segura'},follow_redirects=False).status_code==303
    with connection() as db:
        assert len(rankings(db,2027,1))==0
        assert db.execute('SELECT count(*) n FROM assessment_results WHERE assessment_id=1').fetchone()['n']==2
    assert client.post('/simulados/1/restaurar',data={**deletion,'admin_password':'uma-senha-super-segura'},follow_redirects=False).status_code==303
    with connection() as db:
        assert len(rankings(db,2027,1))==1
        actions=[r['action'] for r in db.execute('SELECT action FROM audit_log')]
        assert 'arquivar_simulado' in actions and 'restaurar_simulado' in actions


def test_validated_inspection_evolution_and_access_denial():
    seed();client,csrf=admin_client()
    with connection() as db:
        insp=db.execute('SELECT id FROM inspections LIMIT 1').fetchone()['id']
        ev=db.execute('SELECT id FROM evolution LIMIT 1').fetchone()['id']
        db.execute("INSERT INTO users(username,full_name,password_hash,role) VALUES('prof','Professor',?,'professor')",
                   (password_hash('uma-senha-super-segura'),))
    form={'_csrf':csrf,'date':'2027-02-01','bimester':'1','trimester':'1',
          'behavior':'20','order_drill':'20','punctuality':'15','rules':'10','conservation':'10','cooperation':'10',
          'notes':'Retificado','justification':'Conferência da vistoria na assembleia'}
    assert client.get('/vistorias').status_code==200
    assert client.post(f'/vistorias/{insp}/editar',data=form,follow_redirects=False).status_code==403
    assert client.post(f'/vistorias/{insp}/editar',data={**form,'admin_password':'uma-senha-super-segura'},follow_redirects=False).status_code==303
    ef={'_csrf':csrf,'score':'81','evidence':'Evidência revisada',
        'justification':'Retificação documentada da pontuação'}
    assert client.post(f'/evolucao/{ev}/editar',data=ef,follow_redirects=False).status_code==403
    assert client.post(f'/evolucao/{ev}/editar',data={**ef,'admin_password':'uma-senha-super-segura'},follow_redirects=False).status_code==303
    with connection() as db:
        assert db.execute('SELECT score FROM evolution WHERE id=?',(ev,)).fetchone()['score']==81
        assert db.execute('SELECT behavior FROM inspections WHERE id=?',(insp,)).fetchone()['behavior']==20
    client.post('/sair',data={'_csrf':csrf})
    login=client.get('/entrar')
    from bs4 import BeautifulSoup
    csrf2=BeautifulSoup(login.text,'html.parser').select_one('input[name="_csrf"]')['value']
    client.post('/entrar',data={'_csrf':csrf2,'username':'prof','password':'uma-senha-super-segura'})
    assert client.get('/simulados/1/editar').status_code==403
    assert client.post(f'/vistorias/{insp}/editar',data={**form,'_csrf':csrf2,'admin_password':'uma-senha-super-segura'},follow_redirects=False).status_code==403
    assert client.post('/simulados/1/resultado/1',data={'_csrf':csrf2,'attendance':'justificada'},follow_redirects=False).status_code==403


def test_gabarito_correction_recalculates_and_audits_each_changed_result():
    seed();client,csrf=admin_client()
    with connection() as db:
        db.execute('''UPDATE assessments SET key_p=?,key_m=?,key_g=? WHERE id=1''',('A'*25,'A'*25,'A'*25))
        db.execute('''UPDATE assessment_results SET answers_p=?,correct_p=25 WHERE assessment_id=1 AND student_id=1''',('A'*25,))
    page=client.get('/simulados/1/editar')
    assert page.status_code==200
    payload={'_csrf':csrf,'title':'Revisão de gabarito','school_year':'2027','trimester':'1','grade':'7',
             'date':'2027-03-15','key_p':'B'*25,'key_m':'A'*25,'key_g':'A'*25,
             'justification':'Gabarito reavaliado após conferência institucional',
             'admin_password':'uma-senha-super-segura'}
    response=client.post('/simulados/1/editar',data=payload,follow_redirects=False)
    assert response.status_code==303, response.text[:300]
    with connection() as db:
        row=db.execute('SELECT correct_p FROM assessment_results WHERE assessment_id=1 AND student_id=1').fetchone()
        assert row['correct_p']==0
        change=db.execute("SELECT details FROM audit_log WHERE action='recalcular_por_gabarito'").fetchone()['details']
        assert '"antes": 25' in change and '"depois": 0' in change


def test_approved_rubric_edit_requires_admin_reauthentication():
    seed();client,csrf=admin_client()
    form={'_csrf':csrf,'title':'Ficha corrigida','description':'Evolução aprovada e descrita em novos termos',
          'justification':'Correção de erro material devidamente documentada'}
    assert client.get('/evolucao').status_code==200
    assert client.post('/evolucao/rubrica/1/editar',data=form,follow_redirects=False).status_code==403
    assert client.post('/evolucao/rubrica/1/editar',data={**form,'admin_password':'uma-senha-super-segura'},follow_redirects=False).status_code==303
    with connection() as db:
        assert db.execute('SELECT title FROM rubrics WHERE id=1').fetchone()['title']=='Ficha corrigida'
        assert db.execute("SELECT COUNT(*) n FROM audit_log WHERE action='retificar_ficha_aprovada'").fetchone()['n']==1


def test_occurrence_create_validate_edit_and_pdf_are_audited():
    seed();client,csrf=admin_client()
    page=client.get('/ocorrencias/nova?aluno=1')
    assert page.status_code==200 and 'Nova ocorrência' in page.text
    payload={'_csrf':csrf,'student_id':'1','incident_date':'2027-04-03',
             'infraction_type':'media','description':'Relato objetivo de uma ocorrência escolar de teste.'}
    assert client.post('/ocorrencias',data=payload,follow_redirects=False).status_code==303
    with connection() as db:
        occ=db.execute('SELECT * FROM occurrences ORDER BY id DESC LIMIT 1').fetchone()
        oid=occ['id']
        assert occ['status']=='rascunho'
    route=f'/ocorrencias/{oid}'
    assert client.get(route).status_code==200
    warning={'_csrf':csrf,'action_type':'advertencia','description':'Advertência escrita para avaliação da gestão.','justification':'Registro conferido pela gestão escolar','admin_password':'uma-senha-super-segura'}
    assert client.post(route+'/acoes',data=warning,follow_redirects=False).status_code==303
    suspension={'_csrf':csrf,'action_type':'suspensao','suspension_days':'2',
                'description':'Suspensão proposta para análise institucional.','justification':'Registro conferido pela gestão escolar','admin_password':'uma-senha-super-segura'}
    assert client.post(route+'/acoes',data=suspension,follow_redirects=False).status_code==303
    with connection() as db:
        row=db.execute("SELECT * FROM occurrence_actions WHERE occurrence_id=? AND action_type='suspensao'",(oid,)).fetchone()
        assert row['suspension_days']==2
        warning_id=db.execute("SELECT id FROM occurrence_actions WHERE occurrence_id=? AND action_type='advertencia'",(oid,)).fetchone()['id']
    valid={'_csrf':csrf,'justification':'Registro conferido e aprovado pelo responsável'}
    assert client.post(route+'/validar',data=valid,follow_redirects=False).status_code==403
    assert client.post(route+'/validar',data={**valid,'admin_password':'uma-senha-super-segura'},follow_redirects=False).status_code==303
    revision={'_csrf':csrf,'incident_date':'2027-04-04','infraction_type':'grave',
              'description':'Correção da data e gravidade com evidência revisada.',
              'justification':'Alteração após análise documental completa'}
    assert client.post(route+'/editar',data=revision,follow_redirects=False).status_code==403
    assert client.post(route+'/editar',data={**revision,'admin_password':'errada'},follow_redirects=False).status_code==403
    with connection() as db:
        assert db.execute('SELECT infraction_type FROM occurrences WHERE id=?',(oid,)).fetchone()['infraction_type']=='media'
    assert client.post(route+'/editar',data={**revision,'admin_password':'uma-senha-super-segura'},follow_redirects=False).status_code==303
    assert client.post(route+'/acoes',data={**warning,'admin_password':'invalida'},follow_redirects=False).status_code==403
    assert client.post(route+f'/acoes/{warning_id}/editar',data={**warning,'justification':'Alterar registro já validado','admin_password':'incorreta'},follow_redirects=False).status_code==403
    assert client.post(route+f'/acoes/{warning_id}/editar',data={**warning,'justification':'Alterar registro já validado',
          'admin_password':'uma-senha-super-segura'},follow_redirects=False).status_code==303
    pdf=client.get(route+'/boletim.pdf')
    assert pdf.status_code==200 and pdf.content.startswith(b'%PDF')
    import fitz
    document=fitz.open(stream=pdf.content,filetype='pdf')
    extracted=' '.join(page.get_text() for page in document)
    assert 'BOLETIM DE OCORRÊNCIA ESCOLAR' in extracted
    assert 'Aluno(a)' in extracted and 'Responsável legal' in extracted
    assert 'Subcomandante do CMCB UNI LXI BANDEIRA TRIBUZZI' in extracted
    with connection() as db:
        logs=db.execute('SELECT action,details FROM audit_log WHERE object_type IN (\'occurrences\',\'occurrence_actions\')').fetchall()
        assert any(row['action']=='retificar_ocorrencia_validada' for row in logs)
        assert any(row['action']=='validar_ocorrencia' for row in logs)
        changes=[row['details'] for row in logs if row['action']=='retificar_ocorrencia_validada']
        assert '"antes": "media"' in changes[0] and '"depois": "grave"' in changes[0]
        assert all('uma-senha-super-segura' not in row['details'] for row in logs)


def test_secure_photo_and_evidence_access_and_file_validation():
    seed();client,csrf=admin_client()
    from PIL import Image
    from io import BytesIO
    img=Image.new('RGB',(120,140),'navy')
    image_file=BytesIO();img.save(image_file,format='PNG');image_file.seek(0)
    img_bytes=image_file.getvalue()
    photo='/alunos/1/foto'
    assert client.get(photo).status_code==404
    assert client.post(photo,data={'_csrf':csrf},files={'photo':('arquivo.jpg',b'<script>alert(1)</script>','image/jpeg')},follow_redirects=False).status_code==400
    upload={'_csrf':csrf,'justification':'Cadastro da fotografia na ficha individual'}
    assert client.post(photo,data=upload,files={'photo':('foto.png',img_bytes,'image/png')},follow_redirects=False).status_code==403
    assert client.post(photo,data={**upload,'admin_password':'uma-senha-super-segura'},
                       files={'photo':('foto.png',img_bytes,'image/png')},follow_redirects=False).status_code==303
    result=client.get(photo)
    assert result.status_code==200 and result.headers['content-type'].startswith('image/jpeg')
    assert result.content.startswith(b'\xff\xd8')
    assert result.headers['cache-control']=='private, no-store'
    assert 'Foto do aluno' in client.get('/alunos/1').text
    # Cria ocorrência e vincula anexo a uma advertência.
    create={'_csrf':csrf,'student_id':'1','incident_date':'2027-01-01',
            'infraction_type':'leve','description':'Registro escolar para avaliar anexos fotográficos.'}
    assert client.post('/ocorrencias',data=create,follow_redirects=False).status_code==303
    with connection() as db:
        oid=db.execute('SELECT id FROM occurrences ORDER BY id DESC LIMIT 1').fetchone()['id']
    action={'_csrf':csrf,'action_type':'advertencia','description':'Advertência de teste com prova fotográfica.','justification':'Anexo autorizado e conferido pela gestão','admin_password':'uma-senha-super-segura'}
    assert client.post(f'/ocorrencias/{oid}/acoes',data=action,
                       files={'attachment':('evidencia.png',img_bytes,'image/png')},follow_redirects=False).status_code==303
    with connection() as db:
        attachment=db.execute('SELECT * FROM occurrence_attachments WHERE occurrence_id=?',(oid,)).fetchone()
        assert len(attachment['sha256'])==64
        assert attachment['storage_path'].startswith('ocorrencias/')
        assert attachment['mime_type']=='image/jpeg'
    secure_url=f'/ocorrencias/anexos/{attachment["id"]}'
    assert client.get(secure_url).status_code==200
    # PDF inclui página(s) de anexos.
    pdf=client.get(f'/ocorrencias/{oid}/boletim.pdf')
    import fitz
    doc=fitz.open(stream=pdf.content,filetype='pdf')
    assert any('ANEXOS FOTOGRÁFICOS' in p.get_text() for p in doc)
    # Sem login, fotos e anexos não são acessíveis.
    client.post('/sair',data={'_csrf':csrf})
    assert client.get(photo,follow_redirects=False).status_code in (302,303)
    assert client.get(secure_url,follow_redirects=False).status_code in (302,303)


def test_occurrence_role_limits_and_photo_invalid_types():
    seed();client,csrf=admin_client()
    create={'_csrf':csrf,'student_id':'1','incident_date':'2027-01-01',
            'infraction_type':'leve','description':'Registro de teste para permissões.'}
    assert client.post('/ocorrencias',data=create,follow_redirects=False).status_code==303
    with connection() as db:
        oid=db.execute('SELECT id FROM occurrences LIMIT 1').fetchone()['id']
        db.execute("INSERT INTO users(username,full_name,password_hash,role) VALUES('prof','Professor',?,'professor')",
                   (password_hash('senha-valida-para-teste'),))
        db.execute("INSERT INTO users(username,full_name,password_hash,role) VALUES('mil','Gestão militar',?,'militar')",
                   (password_hash('senha-valida-para-teste'),))
    bad=client.post('/alunos/2/foto',data={'_csrf':csrf,
                'justification':'Inclusão fotográfica de documento revisado',
                'admin_password':'uma-senha-super-segura'},
                    files={'photo':('index.html',b'<script>oi</script>','image/png')},follow_redirects=False)
    assert bad.status_code==400
    client.post('/sair',data={'_csrf':csrf})
    from bs4 import BeautifulSoup
    token=BeautifulSoup(client.get('/entrar').text,'html.parser').select_one('input[name="_csrf"]')['value']
    client.post('/entrar',data={'_csrf':token,'username':'prof','password':'senha-valida-para-teste'})
    token=BeautifulSoup(client.get('/alunos').text,'html.parser').select_one('input[name="_csrf"]')['value']
    assert client.get('/ocorrencias').status_code==403
    assert client.get(f'/ocorrencias/{oid}').status_code==403
    assert client.get('/alunos/1').status_code==200
    assert 'Ocorrências disciplinares individuais' not in client.get('/alunos/1').text
    assert client.post(f'/ocorrencias/{oid}/validar',data={'_csrf':token},follow_redirects=False).status_code==403
    client.post('/sair',data={'_csrf':token})
    token=BeautifulSoup(client.get('/entrar').text,'html.parser').select_one('input[name="_csrf"]')['value']
    client.post('/entrar',data={'_csrf':token,'username':'mil','password':'senha-valida-para-teste'})
    token=BeautifulSoup(client.get('/ocorrencias').text,'html.parser').select_one('input[name="_csrf"]')['value']
    assert client.get('/ocorrencias').status_code==200
    assert client.get(f'/ocorrencias/{oid}/boletim.pdf').status_code==200
    assert client.post(f'/ocorrencias/{oid}/editar',data={'_csrf':token},follow_redirects=False).status_code==403


def test_upgrade_existing_v2_database_keeps_records(tmp_path,monkeypatch):
    """Simula esquema antigo, sem foto/ocorrências, e aplica migração automática."""
    import sqlite3
    import simr.db as data_layer
    legacy=tmp_path/'antigo.db'
    db=sqlite3.connect(legacy)
    db.executescript('''CREATE TABLE students (
        id INTEGER PRIMARY KEY, registration TEXT UNIQUE NOT NULL, name TEXT NOT NULL,
        active INTEGER NOT NULL DEFAULT 1, created_at TEXT DEFAULT CURRENT_TIMESTAMP);
        INSERT INTO students(id,registration,name) VALUES(42,'MANTER','Aluno Anterior');''')
    db.close()
    monkeypatch.setattr(data_layer,'DB_PATH',legacy)
    data_layer.initialize()
    with data_layer.connection() as upgraded:
        student=upgraded.execute('SELECT * FROM students WHERE id=42').fetchone()
        assert student['name']=='Aluno Anterior'
        assert student['registration']=='MANTER'
        assert student['photo_path'] is None
        assert upgraded.execute("SELECT COUNT(*) n FROM sqlite_master WHERE name='occurrences'").fetchone()['n']==1
        assert upgraded.execute("SELECT COUNT(*) n FROM sqlite_master WHERE name='occurrence_attachments'").fetchone()['n']==1


# SIMR 4: Segurança de arquivamento e indicador factual de ocorrências.
def test_admin_archive_restore_protects_calculations_and_history():
    seed();client,csrf=admin_client()
    assert client.get('/administracao?tipo=inspections').status_code==200
    with connection() as db:
        inspection_id=db.execute('SELECT id FROM inspections WHERE class_id=1').fetchone()['id']
    url=f'/administracao/inspections/{inspection_id}/arquivar'
    baseline={'_csrf':csrf,'justification':'Conferência administrativa da vistoria escolar'}
    assert client.post(url,data=baseline,follow_redirects=False).status_code==403
    assert client.post(url,data={**baseline,'admin_password':'erro'},follow_redirects=False).status_code==403
    assert client.post(url,data={**baseline,'admin_password':'uma-senha-super-segura'},follow_redirects=False).status_code==303
    with connection() as db:
        v=db.execute('SELECT deleted_at FROM inspections WHERE id=?',(inspection_id,)).fetchone()
        assert v['deleted_at'] is not None
        cl=db.execute('SELECT * FROM classes WHERE id=1').fetchone()
        assert class_metrics(db,cl,1)['D'] is None
        detail=db.execute("SELECT details FROM audit_log WHERE object_type='inspections' AND action='arquivar' ORDER BY id DESC LIMIT 1").fetchone()['details']
        assert 'Conferência administrativa' in detail and 'uma-senha-super-segura' not in detail
    restore=f'/administracao/inspections/{inspection_id}/restaurar'
    assert client.post(restore,data={**baseline,'admin_password':'uma-senha-super-segura'},follow_redirects=False).status_code==303
    with connection() as db:
        cl=db.execute('SELECT * FROM classes WHERE id=1').fetchone()
        assert class_metrics(db,cl,1)['D']==90
    cls_url='/administracao/classes/1/arquivar'
    assert client.post(cls_url,data={**baseline,'admin_password':'uma-senha-super-segura'},follow_redirects=False).status_code==303
    with connection() as db:
        assert all(r['id'] != 1 for r in rankings(db,2027,1))
    assert client.post('/administracao/classes/1/restaurar',data={**baseline,'admin_password':'uma-senha-super-segura'},follow_redirects=False).status_code==303
    with connection() as db:
        assert len(rankings(db,2027,1))==1


def test_behavior_chart_only_validated_occurrences_and_admin_deductions():
    seed();client,csrf=admin_client()
    from simr.behavior import behavior_summary,behavior_chart
    with connection() as db:
        assert behavior_summary(db,1)['label'].startswith('Estável')
    create={'_csrf':csrf,'student_id':'1','incident_date':'2027-04-03','infraction_type':'leve',
            'description':'Fato de teste, sem inferência psicológica'}
    assert client.post('/ocorrencias',data=create,follow_redirects=False).status_code==303
    with connection() as db:
        oid=db.execute('SELECT id FROM occurrences ORDER BY id DESC LIMIT 1').fetchone()['id']
        assert behavior_summary(db,1)['occurrences']==0  # Rascunhos não contam.
    reason={'_csrf':csrf,'justification':'Conferido no registro administrativo da gestão escolar',
            'admin_password':'uma-senha-super-segura'}
    assert client.post(f'/ocorrencias/{oid}/validar',data=reason,follow_redirects=False).status_code==303
    row={'_csrf':csrf,'event_date':'2027-04-04','points':'7.5',
         'reason':'Registro fundamentado da pontuação individual escolar',
         'justification':'Lançamento aprovado pela gestão escolar'}
    assert client.post('/alunos/1/descontos',data=row,follow_redirects=False).status_code==403
    assert client.post('/alunos/1/descontos',data={**row,'admin_password':'errada'},follow_redirects=False).status_code==403
    assert client.post('/alunos/1/descontos',data={**row,'admin_password':'uma-senha-super-segura'},follow_redirects=False).status_code==303
    with connection() as db:
        stats=behavior_summary(db,1)
        assert stats['occurrences']==1 and stats['points']==7.5 and stats['balance']==92.5
        assert len(stats['timeline'])==2 and behavior_chart(stats)['count']==2
        did=db.execute('SELECT id FROM behavior_deductions WHERE student_id=1').fetchone()['id']
    html=client.get('/alunos/1').text
    assert 'Evolução dos registros de acompanhamento individual' in html
    assert '7.5' in html and 'svg' in html
    path=f'/administracao/behavior_deductions/{did}/arquivar'
    assert client.post(path,data=reason,follow_redirects=False).status_code==303
    with connection() as db:
        assert behavior_summary(db,1)['points']==0
        aud=db.execute('SELECT details FROM audit_log WHERE object_type=? ORDER BY id DESC',('behavior_deductions',)).fetchall()
        assert all('uma-senha-super-segura' not in x['details'] for x in aud)
    assert client.post(f'/administracao/behavior_deductions/{did}/restaurar',data=reason,follow_redirects=False).status_code==303
    with connection() as db:
        assert behavior_summary(db,1)['points']==7.5


def test_admin_pages_and_permissions_for_all_archival_categories():
    seed();client,csrf=admin_client()
    from simr.main import ARCHIVE_TYPES
    for category in ARCHIVE_TYPES:
        response=client.get('/administracao?tipo='+category)
        assert response.status_code==200,(category,response.text[:300])
    for url in ('/alunos/1','/turmas','/vistorias','/simulados','/ocorrencias','/usuarios','/planos','/evolucao','/pelotao?ano=2027&trimestre=1','/assembleias','/auditoria'):
        response=client.get(url)
        assert response.status_code==200,(url,response.text[:300])

# SIMR 5 — redesign institucional e centralização do arquivamento.
def test_v5_new_identity_and_restricted_archived_center():
    seed();client,csrf=admin_client()
    assert 'brasao_cmcb_lxi.webp' in client.get('/').text
    assert 'Gerenciamento de Arquivados' in client.get('/').text
    assert client.get('/static/brasao_cmcb_lxi.webp').status_code==200
    assert client.get('/static/icons.svg').status_code==200
    result=client.get('/auditoria/arquivados?tipo=students&estado=ativos')
    assert result.status_code==200 and 'Pessoa Teste 1' in result.text
    assert 'Pessoa Teste 2' in result.text
    assert client.get('/auditoria/arquivados?tipo=students&estado=arquivados').status_code==200
    assert 'Pessoa Teste 1' not in client.get('/auditoria/arquivados?tipo=students&estado=arquivados').text
    assert client.get('/auditoria/arquivados?tipo=students&estado=qualquer').status_code==400
    assert client.get('/auditoria/arquivados?tipo=users').status_code==400


def test_v5_archived_students_classes_assessments_inspections_disappear_from_operations():
    seed();client,csrf=admin_client()
    admin={'_csrf':csrf,'admin_password':'uma-senha-super-segura',
           'justification':'Arquivamento formal após revisão da gestão'}
    assert client.post('/administracao/classes/1/arquivar',data=admin,follow_redirects=False).status_code==303
    assert '<strong>7º A</strong>' not in client.get('/turmas').text
    assert '<strong>7º B</strong>' in client.get('/turmas').text
    archived=client.get('/auditoria/arquivados?tipo=classes&estado=arquivados').text
    assert '7º A' in archived and '7º B' not in archived
    active=client.get('/auditoria/arquivados?tipo=classes&estado=ativos').text
    assert '7º A' not in active and '7º B' in active
    assert client.post('/administracao/classes/1/restaurar',data=admin,follow_redirects=False).status_code==303
    assert '<strong>7º A</strong>' in client.get('/turmas').text

    assert client.post('/administracao/students/1/arquivar',data=admin,follow_redirects=False).status_code==303
    assert 'Pessoa Teste 1' not in client.get('/alunos').text
    assert client.get('/alunos/1').status_code==404
    assert 'Pessoa Teste 1' in client.get('/auditoria/arquivados?tipo=students&estado=arquivados').text
    assert client.post('/administracao/students/1/restaurar',data=admin,follow_redirects=False).status_code==303
    assert 'Pessoa Teste 1' in client.get('/alunos').text

    assert client.post('/administracao/inspections/1/arquivar',data=admin,follow_redirects=False).status_code==303
    assert '<td><strong>7º A</strong></td>' not in client.get('/vistorias').text
    assert '7º B' in client.get('/vistorias').text
    assert 'Arquivado' in client.get('/auditoria/arquivados?tipo=inspections&estado=arquivados').text

    assert client.post('/administracao/assessments/1/arquivar',data=admin,follow_redirects=False).status_code==303
    assert 'T1 2027' not in client.get('/simulados').text
    assert 'T1 2027' not in client.get('/alunos/2').text
    assert 'T1 2027' in client.get('/auditoria/arquivados?tipo=assessments&estado=arquivados').text
    with connection() as db:
        actions=db.execute("SELECT action,details FROM audit_log WHERE action='arquivar' ORDER BY id").fetchall()
        assert len(actions)==4
        assert all('uma-senha-super-segura' not in x['details'] for x in actions)


def test_v5_archived_occurrences_hidden_and_restorable():
    seed();client,csrf=admin_client()
    new={'_csrf':csrf,'student_id':'1','incident_date':'2027-04-07',
         'infraction_type':'leve','description':'Fato administrativo descritivo para teste.'}
    assert client.post('/ocorrencias',data=new,follow_redirects=False).status_code==303
    with connection() as db:
        oid=db.execute('SELECT id FROM occurrences ORDER BY id DESC LIMIT 1').fetchone()['id']
    reason={'_csrf':csrf,'admin_password':'uma-senha-super-segura','justification':'Arquivamento após revisão administrativa'}
    assert client.post(f'/administracao/occurrences/{oid}/arquivar',data=reason,follow_redirects=False).status_code==303
    assert 'Fato administrativo descritivo para teste.' not in client.get('/ocorrencias').text
    assert client.get(f'/ocorrencias/{oid}').status_code==404
    archived=client.get('/auditoria/arquivados?tipo=occurrences&estado=arquivados').text
    assert f'#{oid}' in archived
    assert client.post(f'/administracao/occurrences/{oid}/restaurar',data=reason,follow_redirects=False).status_code==303
    assert client.get(f'/ocorrencias/{oid}').status_code==200

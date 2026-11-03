"""Aplicação web SER. Iniciar: uvicorn simr.main:app --reload."""
import csv
import io
import os
import re
import json
import secrets
import hashlib
import math
from datetime import date
from pathlib import Path
from urllib.parse import urlencode
from fastapi import FastAPI, Request, Depends, HTTPException
from fastapi.responses import RedirectResponse, HTMLResponse, StreamingResponse, FileResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.middleware.sessions import SessionMiddleware
from starlette.middleware.trustedhost import TrustedHostMiddleware
from .db import connection, initialize, password_hash, verify_password, audit, session_secret
from .logic import FIELDS, academic, class_metrics, rankings, student_academic, elite_score, skill_metrics
from .reports import build_pdf, build_xlsx, build_progress_pdf, build_progress_xlsx, spreadsheet_safe
from .occurrences import store_image, get_stored_image, occurrence_pdf, validate_incident_date, infraction_text
from .behavior import behavior_summary, behavior_chart

ROOT = Path(__file__).resolve().parent
app = FastAPI(title='SER | Sistema Escolar de Resultados', docs_url=None, redoc_url=None)
app.add_middleware(SessionMiddleware, secret_key=session_secret(), same_site='lax', https_only=os.getenv('SIMR_COOKIE_SECURE')=='1', max_age=60*60*8)
if os.getenv('SIMR_ALLOWED_HOSTS'):
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=os.environ['SIMR_ALLOWED_HOSTS'].split(','))
app.mount('/static', StaticFiles(directory=str(ROOT/'static')), name='static')
templates=Jinja2Templates(directory=str(ROOT/'templates'))
templates.env.filters['from_json']=lambda text: json.loads(text or '[]')
initialize()

ROLES = {'admin':'Administrador','militar':'Gestão Militar','pedagogia':'Gestão Pedagógica',
         'professor':'Professor','monitor':'Monitor','consulta':'Consulta institucional'}
ADMIN = {'admin'}
PED = {'admin','pedagogia'}
MIL = {'admin','militar'}
ACADEMIC = {'admin','pedagogia','professor'}
INSPECTION = {'admin','militar','monitor'}
WRITE = {'admin','militar','pedagogia'}


def get_user(request):
    uid=request.session.get('uid')
    if not uid: return None
    with connection() as db:
        row=db.execute('SELECT id,username,full_name,role,active FROM users WHERE id=?',(uid,)).fetchone()
    return dict(row) if row and row['active'] else None


def authenticated(request:Request):
    user=get_user(request)
    if not user: raise HTTPException(401,'É necessário entrar no sistema')
    return user


def allowed(user, roles):
    if user['role'] not in roles: raise HTTPException(403,'Seu perfil não tem permissão para esta operação')


def csrf_token(request):
    if 'csrf' not in request.session: request.session['csrf']=secrets.token_urlsafe(24)
    return request.session['csrf']


def check_post(request, data):
    if not secrets.compare_digest(str(data.get('_csrf','')),str(request.session.get('csrf',''))):
        raise HTTPException(403,'Token de segurança inválido. Atualize a página e tente novamente.')


def render(request, filename, **params):
    ctx={'request':request,'user':get_user(request),'csrf':csrf_token(request), 'roles':ROLES,
         'notice':request.session.pop('notice',None),'today':date.today().isoformat()}
    ctx.update(params)
    return templates.TemplateResponse(request, filename, ctx)


def go(path='/', message=None):
    response=RedirectResponse(path,status_code=303)
    return response


def notify(request, message, path):
    request.session['notice']=message
    return go(path)


def numeric(form,key, minimum=None, maximum=None, integer=False):
    raw=str(form.get(key,'')).strip()
    if not raw: raise HTTPException(400,f'Campo obrigatório: {key}')
    try: value=int(raw) if integer else float(raw.replace(',','.'))
    except ValueError: raise HTTPException(400,f'Valor inválido: {key}')
    if not math.isfinite(value) or (minimum is not None and value<minimum) or (maximum is not None and value>maximum):
        raise HTTPException(400,f'{key} deve estar entre {minimum} e {maximum}')
    return value


def nonempty(data,field,maxlen=1000):
    value=str(data.get(field,'')).strip()
    if not value or len(value)>maxlen: raise HTTPException(400,f'Campo obrigatório ou muito longo: {field}')
    return value


def require_admin_password(db, user, form, reason_field='justification'):
    """Reautenticação obrigatória para retificações de registros validados.

    Confere senha do próprio administrador autenticado (não de terceiro).
    Não grava senha nem token de reautenticação no log ou sessão.
    """
    allowed(user, ADMIN)
    justification=nonempty(form,reason_field,1500)
    if len(justification)<10:
        raise HTTPException(400,'Descreva a justificativa da retificação (mínimo 10 caracteres)')
    row=db.execute('SELECT password_hash FROM users WHERE id=? AND active=1 AND role=?',
                   (user['id'],'admin')).fetchone()
    if not row or not verify_password(str(form.get('admin_password','')),row['password_hash']):
        raise HTTPException(403,'Senha do administrador inválida. Alteração não realizada.')
    return justification


def changed_fields(old, new):
    """Guarda pares antes/depois, limitando-se aos campos efetivamente modificados."""
    before=dict(old) if old else {}
    after=dict(new) if new else {}
    return {k:{'antes':before.get(k),'depois':after.get(k)}
            for k in sorted(set(before)|set(after)) if before.get(k)!=after.get(k)
            and k not in ('password_hash',)}


def log_change(db,user,action,entity,entity_id,old,new,**extra):
    audit(db,user,action,entity,entity_id,{'alteracoes':changed_fields(old,new),**extra})


def year_trimester(request):
    year=int(request.query_params.get('ano',date.today().year))
    trimester=int(request.query_params.get('trimestre',1))
    if not 2000<=year<=2100 or trimester not in (1,2,3): raise HTTPException(400,'Período inválido')
    return year,trimester


@app.exception_handler(HTTPException)
async def handle_http(request, exc):
    if exc.status_code==401: return RedirectResponse('/entrar',status_code=303)
    if exc.status_code==403 or exc.status_code==400:
        response=render(request,'error.html',message=str(exc.detail),status=exc.status_code)
        response.status_code=exc.status_code
        return response
    return HTMLResponse('Página não encontrada',status_code=exc.status_code)


@app.get('/entrar',response_class=HTMLResponse)
def login_page(request:Request):
    if get_user(request): return go('/')
    return render(request,'login.html')


@app.post('/entrar')
async def login(request:Request):
    data=await request.form(); check_post(request,data)
    username=str(data.get('username','')).strip().lower()
    pwd=str(data.get('password',''))
    with connection() as db:
        user=db.execute('SELECT * FROM users WHERE username=? AND active=1',(username,)).fetchone()
        ok=user and verify_password(pwd,user['password_hash'])
        if ok: audit(db,user,'login','users',user['id'])
    if not ok: return notify(request,'Credenciais inválidas.','/entrar')
    request.session.clear(); request.session['uid']=user['id']; csrf_token(request)
    return go('/')


@app.post('/sair')
async def logout(request:Request):
    data=await request.form(); check_post(request,data)
    request.session.clear()
    return go('/entrar')


@app.get('/',response_class=HTMLResponse)
def dashboard(request:Request,user=Depends(authenticated)):
    year,tri=year_trimester(request)
    with connection() as db:
        classes=db.execute('SELECT * FROM classes WHERE school_year=? AND deleted_at IS NULL ORDER BY grade,name',(year,)).fetchall()
        metrics=[class_metrics(db,c,tri) for c in classes]
        total_students=db.execute("""SELECT COUNT(DISTINCT e.student_id) n FROM enrollments e JOIN classes c ON c.id=e.class_id
            JOIN students s ON s.id=e.student_id WHERE c.school_year=? AND c.deleted_at IS NULL AND e.deleted_at IS NULL AND e.status='ativa' AND s.active=1 AND s.deleted_at IS NULL""",(year,)).fetchone()['n']
        ranking=rankings(db,year,tri)
        actionable=sum(1 for x in metrics if x['R'] is not None)
        avgs={key:(sum(x[key] for x in metrics if x[key] is not None)/sum(x[key] is not None for x in metrics) if any(x[key] is not None for x in metrics) else None)
              for key in ('A','D','P','M','G')}
        participating=sum(x['participants'] for x in metrics)
        eligible=sum(x['enrolled'] for x in metrics)
        plans=db.execute("SELECT COUNT(*) n FROM plans WHERE school_year=? AND status!='concluida' AND deleted_at IS NULL",(year,)).fetchone()['n']
    return render(request,'dashboard.html',year=year,tri=tri,metrics=metrics,ranking=ranking[:5],
                  total_students=total_students,classes_total=len(classes),complete=actionable,averages=avgs,
                  participants=participating,participation=(participating/eligible*100 if eligible else None),plans=plans)


@app.get('/turmas')
def classes_page(request:Request,user=Depends(authenticated)):
    with connection() as db:
        classes=db.execute("""SELECT c.*, COUNT(CASE WHEN e.status='ativa' AND s.active=1 THEN 1 END) student_count,
            EXISTS(SELECT 1 FROM assessment_results ar JOIN assessments a ON a.id=ar.assessment_id
                   WHERE ar.class_id=c.id AND a.status='validado' AND a.deleted_at IS NULL AND ar.deleted_at IS NULL) has_validated
            FROM classes c LEFT JOIN enrollments e ON e.class_id=c.id AND e.deleted_at IS NULL LEFT JOIN students s ON s.id=e.student_id AND s.deleted_at IS NULL
            WHERE c.deleted_at IS NULL
            GROUP BY c.id ORDER BY c.school_year DESC, c.grade,c.name""").fetchall()
    return render(request,'classes.html',classes=classes,can_edit=user['role'] in PED,is_admin=user['role']=='admin')


@app.post('/turmas')
async def add_class(request:Request,user=Depends(authenticated)):
    allowed(user,PED); data=await request.form(); check_post(request,data)
    name=nonempty(data,'name',80); year=numeric(data,'school_year',2020,2100,True)
    grade=numeric(data,'grade',6,9,True); shift=nonempty(data,'shift',50)
    with connection() as db:
        if db.execute('SELECT id FROM classes WHERE name=? AND school_year=?',(name,year)).fetchone():
            return notify(request,'Turma já cadastrada neste ano.','/turmas')
        cur=db.execute('INSERT INTO classes(name,school_year,grade,shift) VALUES(?,?,?,?)',(name,year,grade,shift))
        audit(db,user,'criar','classes',cur.lastrowid)
    return notify(request,'Turma cadastrada com sucesso.','/turmas')


@app.get('/alunos')
def students_page(request:Request,user=Depends(authenticated)):
    allowed(user,ACADEMIC|MIL)
    with connection() as db:
        classes=db.execute('SELECT * FROM classes WHERE deleted_at IS NULL ORDER BY school_year DESC,grade,name').fetchall()
        students=db.execute("""SELECT s.*,c.name class_name,c.school_year FROM students s
            LEFT JOIN enrollments e ON e.student_id=s.id AND e.status='ativa' AND e.deleted_at IS NULL
            LEFT JOIN classes c ON c.id=e.class_id AND c.deleted_at IS NULL WHERE s.deleted_at IS NULL ORDER BY s.name""").fetchall()
    return render(request,'students.html',students=students,classes=classes,can_edit=user['role'] in PED)


@app.get('/alunos/{student_id}')
def student_profile(request:Request,student_id:int,user=Depends(authenticated)):
    allowed(user,ACADEMIC|MIL)
    with connection() as db:
        student=db.execute('SELECT * FROM students WHERE id=? AND deleted_at IS NULL',(student_id,)).fetchone()
        if not student: raise HTTPException(404,'Aluno não encontrado')
        classes=db.execute('SELECT * FROM classes WHERE deleted_at IS NULL ORDER BY school_year DESC,grade,name').fetchall()
        enrollments=db.execute('''SELECT en.*, c.name class_name,c.grade,c.shift,c.school_year
          FROM enrollments en JOIN classes c ON c.id=en.class_id
          WHERE en.student_id=? AND en.deleted_at IS NULL AND c.deleted_at IS NULL ORDER BY c.school_year DESC,en.id DESC''',(student_id,)).fetchall()
        results=db.execute('''SELECT r.*,a.title,a.school_year,a.trimester,a.grade,a.status assessment_status,
             a.key_p,a.key_m,a.key_g,
             a.deleted_at,c.name class_name FROM assessment_results r
             JOIN assessments a ON a.id=r.assessment_id JOIN classes c ON c.id=r.class_id
             WHERE r.student_id=? AND r.deleted_at IS NULL AND a.deleted_at IS NULL AND c.deleted_at IS NULL ORDER BY a.school_year DESC,a.trimester DESC,a.id DESC''',(student_id,)).fetchall()
        elite=db.execute('SELECT * FROM elite_evaluations WHERE student_id=? AND deleted_at IS NULL ORDER BY school_year DESC,trimester DESC',
                         (student_id,)).fetchall()
        inspections=db.execute('''SELECT i.*,c.name class_name, i.behavior+i.order_drill+i.punctuality+i.rules+i.conservation+i.cooperation total
          FROM inspections i JOIN classes c ON c.id=i.class_id
          WHERE i.deleted_at IS NULL AND c.deleted_at IS NULL AND i.class_id IN (SELECT class_id FROM enrollments WHERE student_id=? AND deleted_at IS NULL)
          ORDER BY i.date DESC''',(student_id,)).fetchall()
        evolution=db.execute('''SELECT e.*,c.name class_name FROM evolution e JOIN classes c ON c.id=e.class_id
          WHERE e.deleted_at IS NULL AND c.deleted_at IS NULL AND e.class_id IN (SELECT class_id FROM enrollments WHERE student_id=? AND deleted_at IS NULL)
          ORDER BY e.school_year DESC,e.trimester DESC''',(student_id,)).fetchall()
        plans=db.execute('''SELECT p.*,c.name class_name FROM plans p JOIN classes c ON c.id=p.class_id
          WHERE p.deleted_at IS NULL AND c.deleted_at IS NULL AND p.class_id IN (SELECT class_id FROM enrollments WHERE student_id=? AND deleted_at IS NULL)
          ORDER BY p.school_year DESC,p.trimester DESC''',(student_id,)).fetchall()
        occurrences=db.execute('''SELECT o.*,COUNT(a.id) action_count FROM occurrences o
          LEFT JOIN occurrence_actions a ON a.occurrence_id=o.id AND a.deleted_at IS NULL
          WHERE o.student_id=? AND o.deleted_at IS NULL GROUP BY o.id ORDER BY o.incident_date DESC,o.id DESC''',(student_id,)).fetchall() if user['role'] in ('admin','militar') else []
        deductions=db.execute('''SELECT d.*,u.full_name author_name FROM behavior_deductions d
               JOIN users u ON u.id=d.created_by WHERE d.student_id=? AND d.deleted_at IS NULL
               ORDER BY d.event_date DESC,d.id DESC''',(student_id,)).fetchall()
        behavior=behavior_summary(db,student_id)
        behavior_svg=behavior_chart(behavior)
        has_validated=bool(db.execute('''SELECT 1 FROM assessment_results r JOIN assessments a ON a.id=r.assessment_id
          WHERE r.student_id=? AND a.status='validado' AND a.deleted_at IS NULL AND a.deleted_at IS NULL LIMIT 1''',(student_id,)).fetchone())
    return render(request,'student_detail.html',student=student,classes=classes,
                  enrollments=enrollments,results=results,elite=elite,inspections=inspections,
                  evolution=evolution,plans=plans,is_admin=user['role']=='admin',has_validated=has_validated,
                  occurrences=occurrences,can_view_occurrences=user['role'] in ('admin','militar'),
                  deductions=deductions if user['role'] in ('admin','militar') else [],
                  behavior=behavior if user['role'] in ('admin','militar') else None,
                  behavior_svg=behavior_svg if user['role'] in ('admin','militar') else None)


@app.post('/alunos/{student_id}/editar')
async def edit_student(request:Request,student_id:int,user=Depends(authenticated)):
    allowed(user,ADMIN)
    form=await request.form();check_post(request,form)
    name=nonempty(form,'name',160);registration=nonempty(form,'registration',40)
    target_class=numeric(form,'class_id',1,999999,True)
    active=1 if str(form.get('active',''))=='1' else 0
    with connection() as db:
        old=db.execute('SELECT * FROM students WHERE id=?',(student_id,)).fetchone()
        cl=db.execute('SELECT * FROM classes WHERE id=? AND deleted_at IS NULL',(target_class,)).fetchone()
        if not old or old['deleted_at'] or not cl or cl['deleted_at']:raise HTTPException(400,'Aluno ou turma inexistente/arquivada')
        if db.execute('SELECT id FROM students WHERE registration=? AND id!=?',(registration,student_id)).fetchone():
            raise HTTPException(400,'Matrícula já pertence a outro aluno')
        validated=db.execute('''SELECT 1 FROM assessment_results r JOIN assessments a ON a.id=r.assessment_id
           WHERE r.student_id=? AND a.status='validado' AND a.deleted_at IS NULL AND a.deleted_at IS NULL LIMIT 1''',(student_id,)).fetchone()
        why=require_admin_password(db,user,form)
        enrolls=db.execute('SELECT * FROM enrollments WHERE student_id=? ORDER BY id',(student_id,)).fetchall()
        old_enroll=[dict(x) for x in enrolls]
        db.execute('UPDATE students SET name=?,registration=?,active=? WHERE id=?',(name,registration,active,student_id))
        current=[x for x in enrolls if x['status']=='ativa']
        if not current or any(x['class_id']!=target_class for x in current):
            db.execute("UPDATE enrollments SET status='transferida' WHERE student_id=? AND status='ativa'",(student_id,))
            existing=db.execute('SELECT id FROM enrollments WHERE student_id=? AND class_id=?',(student_id,target_class)).fetchone()
            if existing: db.execute("UPDATE enrollments SET status='ativa' WHERE id=?",(existing['id'],))
            else: db.execute('INSERT INTO enrollments(student_id,class_id) VALUES(?,?)',(student_id,target_class))
        new=db.execute('SELECT * FROM students WHERE id=?',(student_id,)).fetchone()
        current_enroll=[dict(x) for x in db.execute('SELECT * FROM enrollments WHERE student_id=? ORDER BY id',(student_id,))]
        log_change(db,user,'editar','students',student_id,old,new,
                   matriculas={'antes':old_enroll,'depois':current_enroll},justificativa=why)
    return notify(request,'Dados do aluno atualizados e registrados na auditoria.',f'/alunos/{student_id}')


@app.post('/alunos')
async def add_student(request:Request,user=Depends(authenticated)):
    allowed(user,PED); data=await request.form(); check_post(request,data)
    name=nonempty(data,'name',160); registration=nonempty(data,'registration',40)
    class_id=numeric(data,'class_id',1,999999,True)
    with connection() as db:
        cl=db.execute('SELECT * FROM classes WHERE id=? AND deleted_at IS NULL',(class_id,)).fetchone()
        if not cl: raise HTTPException(400,'Turma inexistente')
        if db.execute('SELECT id FROM students WHERE registration=?',(registration,)).fetchone():
            return notify(request,'Matrícula já existente.','/alunos')
        cur=db.execute('INSERT INTO students(registration,name) VALUES(?,?)',(registration,name))
        sid=cur.lastrowid
        db.execute('INSERT INTO enrollments(student_id,class_id) VALUES(?,?)',(sid,class_id))
        audit(db,user,'criar','students',sid)
    return notify(request,'Aluno cadastrado.','/alunos')


@app.post('/alunos/importar')
async def import_students(request:Request,user=Depends(authenticated)):
    allowed(user,PED); data=await request.form(); check_post(request,data)
    uploaded=data.get('csv_file')
    if uploaded is None: raise HTTPException(400,'Envie um arquivo CSV')
    raw=await uploaded.read(1_000_001)
    if len(raw)>1_000_000: raise HTTPException(400,'CSV deve ter no máximo 1 MB')
    try:
        text=raw.decode('utf-8-sig'); reader=csv.DictReader(io.StringIO(text),delimiter=';')
        if not {'matricula','nome','turma_id'}<=set(reader.fieldnames or []):
            raise ValueError('Colunas obrigatórias: matricula;nome;turma_id')
        records=list(reader)
        if len(records)>3000: raise ValueError('Máximo de 3000 alunos por importação')
    except (UnicodeError,ValueError,csv.Error) as exc:
        raise HTTPException(400,f'CSV inválido: {exc}')
    added=0; skipped=0
    with connection() as db:
        for r in records:
            reg=r['matricula'].strip()[:40]; name=r['nome'].strip()[:160]
            try: cid=int(r['turma_id'])
            except ValueError: skipped+=1;continue
            if not reg or not name or not db.execute('SELECT id FROM classes WHERE id=?',(cid,)).fetchone():
                skipped+=1;continue
            if db.execute('SELECT id FROM students WHERE registration=?',(reg,)).fetchone():
                skipped+=1;continue
            cur=db.execute('INSERT INTO students(registration,name) VALUES(?,?)',(reg,name))
            db.execute('INSERT INTO enrollments(student_id,class_id) VALUES(?,?)',(cur.lastrowid,cid))
            added+=1
        audit(db,user,'importar_csv','students',details={'adicionados':added,'ignorados':skipped})
    return notify(request,f'Importados {added} alunos; {skipped} registros ignorados.','/alunos')


@app.get('/simulados')
def assessment_page(request:Request,user=Depends(authenticated)):
    with connection() as db:
        assessments=db.execute("""SELECT a.*,COUNT(r.id) total_records,
               SUM(CASE WHEN r.attendance='presente' THEN 1 ELSE 0 END) present_count
               FROM assessments a LEFT JOIN assessment_results r ON r.assessment_id=a.id AND r.deleted_at IS NULL
               WHERE a.deleted_at IS NULL
               GROUP BY a.id ORDER BY a.school_year DESC,a.trimester,a.grade""").fetchall()
    return render(request,'assessments.html',assessments=assessments,can_edit=user['role'] in ACADEMIC,
                  is_admin=user['role']=='admin')


@app.get('/simulados/novo')
def new_assessment_page(request:Request,user=Depends(authenticated)):
    allowed(user,ACADEMIC)
    return render(request,'assessment_new.html')


def normalize_key(value):
    key=re.sub(r'\s+', '', str(value or '')).upper()
    if key and (len(key)!=25 or not re.fullmatch('[ABCDE]{25}',key)):
        raise HTTPException(400,'Cada gabarito deve conter exatamente 25 letras A-E')
    return key or None


def parse_skills(value):
    txt=str(value or '').strip()
    if not txt: return None
    skills=[s.strip()[:120] for s in txt.splitlines()]
    if len(skills)!=25: raise HTTPException(400,'Informe exatamente 25 habilidades, uma por questão (ou deixe vazio)')
    return json.dumps(skills,ensure_ascii=False)


@app.post('/simulados/novo')
async def add_assessment(request:Request,user=Depends(authenticated)):
    allowed(user,ACADEMIC); data=await request.form(); check_post(request,data)
    year=numeric(data,'school_year',2020,2100,True); tri=numeric(data,'trimester',1,3,True)
    grade=numeric(data,'grade',6,9,True); title=nonempty(data,'title',150)
    keys=[normalize_key(data.get(f'key_{x}')) for x in ('p','m','g')]
    if any(keys) and not all(keys): raise HTTPException(400,'Para correção por gabarito, informe as três disciplinas')
    skills=[parse_skills(data.get(f'skills_{x}')) for x in ('p','m','g')]
    if any(skills) and not all(keys): raise HTTPException(400,'Habilidades exigem gabaritos cadastrados')
    with connection() as db:
        existing=db.execute('SELECT * FROM assessments WHERE school_year=? AND trimester=? AND grade=?',(year,tri,grade)).fetchone()
        if existing:
            return notify(request,('Há um simulado arquivado nesta série e trimestre. O administrador pode restaurá-lo e editá-lo.'
                                   if existing['deleted_at'] else 'Já existe um simulado nesta série e trimestre.'),'/simulados')
        cur=db.execute("""INSERT INTO assessments(title,school_year,trimester,grade,date,key_p,key_m,key_g,skills_p,skills_m,skills_g,created_by)
            VALUES(?,?,?,?,?,?,?,?,?,?,?,?)""",(title,year,tri,grade,str(data.get('date','')) or None,*keys,*skills,user['id']))
        audit(db,user,'criar','assessments',cur.lastrowid)
    return notify(request,'Simulado criado. Lance os resultados.','/simulados/'+str(cur.lastrowid))


@app.get('/simulados/{assessment_id}')
def assessment_detail(request:Request,assessment_id:int,user=Depends(authenticated)):
    allowed(user,ACADEMIC|MIL)
    with connection() as db:
        a=db.execute('SELECT * FROM assessments WHERE id=?',(assessment_id,)).fetchone()
        if not a or a['deleted_at']: raise HTTPException(404,'Simulado não encontrado')
        students=db.execute("""SELECT s.id,s.name,s.registration,c.name class_name,c.id class_id,r.attendance,r.correct_p,r.correct_m,r.correct_g,
            r.answers_p,r.answers_m,r.answers_g,r.second_attempt
            FROM enrollments en JOIN students s ON s.id=en.student_id JOIN classes c ON c.id=en.class_id AND c.deleted_at IS NULL
            LEFT JOIN assessment_results r ON r.student_id=s.id AND r.assessment_id=?
            WHERE c.school_year=? AND c.grade=? AND en.status='ativa' AND en.deleted_at IS NULL AND s.active=1 AND s.deleted_at IS NULL ORDER BY c.name,s.name""",
            (assessment_id,a['school_year'],a['grade'])).fetchall()
        skills=skill_metrics(db,assessment_id)
    return render(request,'assessment_detail.html',assessment=a,students=students,
                  can_edit=(user['role']=='admin' or (a['status']=='rascunho' and user['role'] in ACADEMIC)),
                  can_validate=(user['role'] in PED),skill_metrics=skills,is_admin=user['role']=='admin')


@app.post('/simulados/{assessment_id}/resultado/{student_id}')
async def save_result(request:Request,assessment_id:int,student_id:int,user=Depends(authenticated)):
    allowed(user,ACADEMIC); form=await request.form(); check_post(request,form)
    status=str(form.get('attendance',''))
    if status not in ('presente','justificada','nao_justificada'): raise HTTPException(400,'Presença inválida')
    with connection() as db:
        a=db.execute('SELECT * FROM assessments WHERE id=?',(assessment_id,)).fetchone()
        if not a or a['deleted_at']: raise HTTPException(400,'Simulado inexistente ou arquivado')
        existing_result=db.execute('SELECT * FROM assessment_results WHERE assessment_id=? AND student_id=?',
                                   (assessment_id,student_id)).fetchone()
        if a['status']=='validado' or existing_result:
            justification=require_admin_password(db,user,form)
        elif a['status']!='rascunho':
            raise HTTPException(400,'Estado de simulado desconhecido')
        else: justification=None
        enrolled=db.execute("""SELECT c.id FROM enrollments en JOIN classes c ON c.id=en.class_id
             JOIN students s ON s.id=en.student_id WHERE en.student_id=? AND en.deleted_at IS NULL AND c.deleted_at IS NULL AND c.school_year=? AND c.grade=? AND en.status='ativa' AND s.active=1""",
             (student_id,a['school_year'],a['grade'])).fetchone()
        old=db.execute('SELECT * FROM assessment_results WHERE assessment_id=? AND student_id=?',(assessment_id,student_id)).fetchone()
        if not enrolled and old and a['status']=='validado':
            enrolled={'id':old['class_id']}  # Retificação histórica após transferência de turma.
        if not enrolled: raise HTTPException(400,'Estudante não pertence ao conjunto de turmas deste simulado')
        counts=[]; answers=[]
        if status=='presente':
            for subj in ('p','m','g'):
                key=a[f'key_{subj}']
                if key:
                    ans=re.sub(r'\s+','',str(form.get(f'answers_{subj}',''))).upper()
                    if len(ans)!=25 or not re.fullmatch('[ABCDE-]{25}',ans):
                        raise HTTPException(400,'Respostas precisam ter 25 letras A-E (use - para questões em branco)')
                    answers.append(ans); counts.append(sum(c==x for c,x in zip(key,ans)))
                else:
                    counts.append(numeric(form,f'correct_{subj}',0,25,True));answers.append(None)
        else:
            counts=[None]*3;answers=[None]*3
        second=1 if form.get('second_attempt') else 0
        db.execute("""INSERT INTO assessment_results(assessment_id,student_id,class_id,attendance,correct_p,correct_m,correct_g,answers_p,answers_m,answers_g,second_attempt,updated_at)
            VALUES(?,?,?,?,?,?,?,?,?,?,?,CURRENT_TIMESTAMP) ON CONFLICT(assessment_id,student_id)
            DO UPDATE SET class_id=excluded.class_id,attendance=excluded.attendance,correct_p=excluded.correct_p,
            correct_m=excluded.correct_m,correct_g=excluded.correct_g,answers_p=excluded.answers_p,
            answers_m=excluded.answers_m,answers_g=excluded.answers_g,second_attempt=excluded.second_attempt,updated_at=CURRENT_TIMESTAMP""",
            (assessment_id,student_id,enrolled['id'],status,*counts,*answers,second))
        new=db.execute('SELECT * FROM assessment_results WHERE assessment_id=? AND student_id=?',(assessment_id,student_id)).fetchone()
        log_change(db,user,'retificar_validado' if justification else 'lancar_resultado',
                   'assessment_results',new['id'],old,new,simulado=assessment_id,
                   justificativa=justification)
    target=f'/alunos/{student_id}' if form.get('_from_student') and user['role']=='admin' else f'/simulados/{assessment_id}'
    return notify(request,'Resultado atualizado e auditado.',target)


@app.post('/simulados/{assessment_id}/validar')
async def validate_assessment(request:Request,assessment_id:int,user=Depends(authenticated)):
    allowed(user,PED); form=await request.form(); check_post(request,form)
    with connection() as db:
        a=db.execute('SELECT * FROM assessments WHERE id=?',(assessment_id,)).fetchone()
        if not a or a['status']!='rascunho': raise HTTPException(400,'Simulado não está em rascunho')
        total=db.execute('SELECT count(*) n FROM assessment_results WHERE assessment_id=?',(assessment_id,)).fetchone()['n']
        if not total: raise HTTPException(400,'Não é possível validar simulado sem registros')
        pending=db.execute("""SELECT count(*) n FROM enrollments e JOIN students s ON s.id=e.student_id
            JOIN classes c ON c.id=e.class_id
            LEFT JOIN assessment_results ar ON ar.student_id=s.id AND ar.assessment_id=?
            WHERE c.school_year=? AND c.grade=? AND c.deleted_at IS NULL AND e.status='ativa' AND e.deleted_at IS NULL AND s.active=1 AND s.deleted_at IS NULL AND ar.id IS NULL""",
            (assessment_id,a['school_year'],a['grade'])).fetchone()['n']
        if pending: raise HTTPException(400,f'Antes de validar, registre presença ou ausência de todos os alunos: {pending} pendente(s)')
        db.execute("UPDATE assessments SET status='validado',validated_by=?,validated_at=CURRENT_TIMESTAMP WHERE id=?",(user['id'],assessment_id))
        audit(db,user,'validar','assessments',assessment_id)
    return notify(request,'Simulado validado e consolidado.','/simulados/'+str(assessment_id))


@app.get('/vistorias')
def inspections_page(request:Request,user=Depends(authenticated)):
    allowed(user,INSPECTION|PED)
    with connection() as db:
        classes=db.execute('SELECT * FROM classes WHERE deleted_at IS NULL ORDER BY school_year DESC,grade,name').fetchall()
        inspections=db.execute("""SELECT i.*,c.name class_name,c.grade,
            i.behavior+i.order_drill+i.punctuality+i.rules+i.conservation+i.cooperation score
            FROM inspections i JOIN classes c ON c.id=i.class_id AND c.deleted_at IS NULL
            WHERE i.deleted_at IS NULL ORDER BY i.date DESC,i.id DESC""").fetchall()
    return render(request,'inspections.html',classes=classes,inspections=inspections,
                  fields=FIELDS,can_edit=user['role'] in INSPECTION,can_validate=user['role'] in MIL,
                  is_admin=user['role']=='admin')


@app.post('/vistorias')
async def add_inspection(request:Request,user=Depends(authenticated)):
    allowed(user,INSPECTION); form=await request.form(); check_post(request,form)
    cid=numeric(form,'class_id',1,999999,True)
    tri=numeric(form,'trimester',1,3,True); bim=numeric(form,'bimester',1,4,True)
    scores=[numeric(form,name,0,maxscore) for name,maxscore,_ in FIELDS]
    note=str(form.get('notes','')).strip()[:2000]
    with connection() as db:
        cl=db.execute('SELECT * FROM classes WHERE id=? AND deleted_at IS NULL',(cid,)).fetchone()
        if not cl: raise HTTPException(400,'Turma inválida')
        cur=db.execute("""INSERT INTO inspections(class_id,school_year,bimester,trimester,date,behavior,order_drill,punctuality,rules,conservation,cooperation,notes,evaluator)
             VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)""",(cid,cl['school_year'],bim,tri,nonempty(form,'date',10),*scores,note,user['id']))
        audit(db,user,'criar','inspections',cur.lastrowid)
    return notify(request,'Vistoria registrada em rascunho.','/vistorias')


@app.post('/vistorias/{inspection_id}/validar')
async def validate_inspection(request:Request,inspection_id:int,user=Depends(authenticated)):
    allowed(user,MIL); form=await request.form();check_post(request,form)
    with connection() as db:
        cur=db.execute("UPDATE inspections SET status='validado',validated_by=?,validated_at=CURRENT_TIMESTAMP WHERE id=? AND status='rascunho' AND deleted_at IS NULL",(user['id'],inspection_id))
        if cur.rowcount!=1: raise HTTPException(400,'Vistoria indisponível')
        audit(db,user,'validar','inspections',inspection_id)
    return notify(request,'Vistoria validada.','/vistorias')


@app.get('/evolucao')
def evolution_page(request:Request,user=Depends(authenticated)):
    allowed(user,WRITE)
    with connection() as db:
        rubrics=db.execute('SELECT * FROM rubrics WHERE deleted_at IS NULL ORDER BY id DESC').fetchall()
        classes=db.execute('SELECT * FROM classes WHERE deleted_at IS NULL ORDER BY school_year DESC,grade,name').fetchall()
        rows=db.execute('''SELECT e.*,c.name class_name,r.title rubric_title FROM evolution e
             JOIN classes c ON c.id=e.class_id JOIN rubrics r ON r.id=e.rubric_id WHERE e.deleted_at IS NULL AND c.deleted_at IS NULL AND r.deleted_at IS NULL ORDER BY e.school_year DESC,e.trimester DESC,c.name''').fetchall()
    return render(request,'evolution.html',rubrics=rubrics,classes=classes,rows=rows,
                  can_approve=user['role'] in PED,can_edit=user['role'] in WRITE,is_admin=user['role']=='admin')


@app.post('/evolucao/rubrica')
async def add_rubric(request:Request,user=Depends(authenticated)):
    allowed(user,PED); form=await request.form();check_post(request,form)
    title=nonempty(form,'title',160); desc=nonempty(form,'description',5000)
    with connection() as db:
        cur=db.execute('INSERT INTO rubrics(title,description) VALUES(?,?)',(title,desc))
        audit(db,user,'criar','rubrics',cur.lastrowid)
    return notify(request,'Ficha criada; aguarda aprovação.','/evolucao')


@app.post('/evolucao/rubrica/{rubric_id}/aprovar')
async def approve_rubric(request:Request,rubric_id:int,user=Depends(authenticated)):
    allowed(user,PED); form=await request.form(); check_post(request,form)
    with connection() as db:
        cur=db.execute("UPDATE rubrics SET status='aprovada',approved_by=?,approved_at=CURRENT_TIMESTAMP WHERE id=? AND status='rascunho' AND deleted_at IS NULL",(user['id'],rubric_id))
        if not cur.rowcount: raise HTTPException(400,'Ficha inválida')
        audit(db,user,'aprovar','rubrics',rubric_id)
    return notify(request,'Ficha aprovada para utilização.','/evolucao')


@app.post('/evolucao')
async def add_evolution(request:Request,user=Depends(authenticated)):
    allowed(user,WRITE); form=await request.form();check_post(request,form)
    cid=numeric(form,'class_id',1,99999,True);tri=numeric(form,'trimester',1,3,True)
    score=numeric(form,'score',0,100);rubric=numeric(form,'rubric_id',1,99999,True)
    evidence=nonempty(form,'evidence',3000)
    with connection() as db:
        cl=db.execute('SELECT * FROM classes WHERE id=? AND deleted_at IS NULL',(cid,)).fetchone()
        if not cl: raise HTTPException(400,'Turma inválida')
        r=db.execute("SELECT id FROM rubrics WHERE id=? AND status='aprovada' AND deleted_at IS NULL",(rubric,)).fetchone()
        if not r: raise HTTPException(400,'Selecione uma ficha aprovada')
        if db.execute('SELECT id FROM evolution WHERE class_id=? AND school_year=? AND trimester=?',(cid,cl['school_year'],tri)).fetchone():
            return notify(request,'Já existe um registro para esta turma e trimestre.','/evolucao')
        cur=db.execute('INSERT INTO evolution(class_id,school_year,trimester,score,evidence,rubric_id,created_by) VALUES(?,?,?,?,?,?,?)',
                       (cid,cl['school_year'],tri,score,evidence,rubric,user['id']))
        audit(db,user,'criar','evolution',cur.lastrowid)
    return notify(request,'Indicador registrado e aguardando validação.','/evolucao')


@app.post('/evolucao/{e_id}/validar')
async def validate_evolution(request:Request,e_id:int,user=Depends(authenticated)):
    allowed(user,PED); form=await request.form();check_post(request,form)
    with connection() as db:
        cur=db.execute("""UPDATE evolution SET status='validado',validated_by=?,validated_at=CURRENT_TIMESTAMP
          WHERE id=? AND status='rascunho' AND deleted_at IS NULL AND rubric_id IN (SELECT id FROM rubrics WHERE status='aprovada')""",(user['id'],e_id))
        if not cur.rowcount: raise HTTPException(400,'Registro indisponível')
        audit(db,user,'validar','evolution',e_id)
    return notify(request,'Indicador de evolução validado.','/evolucao')


@app.get('/ranking')
def ranking_page(request:Request,user=Depends(authenticated)):
    year,tri=year_trimester(request)
    with connection() as db:
        general=rankings(db,year,tri)
        by_grade={grade:rankings(db,year,tri,grade) for grade in (6,7,8,9)}
        classes=db.execute('SELECT * FROM classes WHERE school_year=? AND deleted_at IS NULL ORDER BY grade,name',(year,)).fetchall()
        pending=[class_metrics(db,c,tri) for c in classes]
        pending=[r for r in pending if r['R'] is None]
    return render(request,'ranking.html',year=year,tri=tri,general=general,by_grade=by_grade,pending=pending)


@app.get('/pelotao')
def elite_page(request:Request,user=Depends(authenticated)):
    allowed(user,PED|MIL)
    year,tri=year_trimester(request)
    with connection() as db:
        students=db.execute("""SELECT s.id,s.name,s.registration,c.name class_name FROM students s
          JOIN enrollments en ON en.student_id=s.id AND en.status='ativa'
          JOIN classes c ON c.id=en.class_id WHERE c.school_year=? AND s.active=1 AND s.deleted_at IS NULL AND en.deleted_at IS NULL AND c.deleted_at IS NULL ORDER BY s.name""",(year,)).fetchall()
        items=db.execute("""SELECT ee.*,s.name student_name,c.name class_name FROM elite_evaluations ee
         JOIN students s ON s.id=ee.student_id LEFT JOIN enrollments en ON en.student_id=s.id AND en.status='ativa'
         LEFT JOIN classes c ON c.id=en.class_id AND c.school_year=ee.school_year AND c.deleted_at IS NULL
         WHERE ee.school_year=? AND ee.trimester=? AND ee.deleted_at IS NULL AND s.deleted_at IS NULL ORDER BY s.name""",(year,tri)).fetchall()
        rows=[dict(item)|elite_score(db,item) for item in items]
        state=db.execute("SELECT value FROM settings WHERE key='elite_criteria_approved'").fetchone()
    return render(request,'elite.html',year=year,tri=tri,students=students,rows=rows,
                  approved=(state and state['value']=='1'), can_edit=user['role'] in PED|MIL,
                  can_decide=user['role'] in PED,is_admin=user['role']=='admin')


@app.post('/pelotao/criterios')
async def approve_elite_criteria(request:Request,user=Depends(authenticated)):
    allowed(user,PED); form=await request.form();check_post(request,form)
    with connection() as db:
        db.execute("UPDATE settings SET value='1' WHERE key='elite_criteria_approved'")
        audit(db,user,'aprovar','elite_criteria',details={'approved':'proposta do documento'})
    return notify(request,'Critérios propostos marcados como aprovados pela gestão.','/pelotao')


@app.post('/pelotao')
async def add_elite(request:Request,user=Depends(authenticated)):
    allowed(user,PED|MIL); form=await request.form();check_post(request,form)
    sid=numeric(form,'student_id',1,999999,True)
    year=numeric(form,'school_year',2020,2100,True);tri=numeric(form,'trimester',1,3,True)
    c=numeric(form,'conduct',0,100);o=numeric(form,'drill',0,100);f=numeric(form,'frequency',0,100)
    training=int(bool(form.get('training'))); blocked=int(bool(form.get('blocked')))
    notes=str(form.get('notes','')).strip()[:1500]
    with connection() as db:
        if not db.execute('SELECT id FROM students WHERE id=? AND deleted_at IS NULL',(sid,)).fetchone(): raise HTTPException(400,'Aluno inexistente')
        old=db.execute('SELECT * FROM elite_evaluations WHERE student_id=? AND school_year=? AND trimester=?',
                       (sid,year,tri)).fetchone()
        if old:
            why=require_admin_password(db,user,form)
        else: why=None
        db.execute("""INSERT INTO elite_evaluations(student_id,school_year,trimester,conduct,drill,frequency,training,blocked,notes)
           VALUES(?,?,?,?,?,?,?,?,?) ON CONFLICT(student_id,school_year,trimester) DO UPDATE SET
           conduct=excluded.conduct,drill=excluded.drill,frequency=excluded.frequency,training=excluded.training,
           blocked=excluded.blocked,notes=excluded.notes,decision='pendente',reviewed_by=NULL""",(sid,year,tri,c,o,f,training,blocked,notes))
        new=db.execute('SELECT * FROM elite_evaluations WHERE student_id=? AND school_year=? AND trimester=?',(sid,year,tri)).fetchone()
        log_change(db,user,'retificar_pelotao_existente' if why else 'avaliar','elite_evaluations',new['id'],old,new,
                   ano=year,trimestre=tri,justificativa=why)
    return notify(request,'Avaliação registrada. A decisão permanece pendente.','/pelotao?'+urlencode({'ano':year,'trimestre':tri}))


@app.post('/pelotao/{item_id}/decidir')
async def decide_elite(request:Request,item_id:int,user=Depends(authenticated)):
    allowed(user,PED); form=await request.form();check_post(request,form)
    decision=str(form.get('decision',''))
    if decision not in ('aprovado','nao_aprovado'): raise HTTPException(400,'Decisão inválida')
    with connection() as db:
        row=db.execute('SELECT * FROM elite_evaluations WHERE id=?',(item_id,)).fetchone()
        if not row: raise HTTPException(400,'Candidato inexistente')
        why=require_admin_password(db,user,form) if row['decision']!='pendente' else None
        calc=elite_score(db,row)
        if decision=='aprovado' and calc['eligible'] is not True:
            raise HTTPException(400,'Não é possível aprovar sem atender aos requisitos aprovados')
        db.execute('UPDATE elite_evaluations SET decision=?,reviewed_by=? WHERE id=?',(decision,user['id'],item_id))
        new=db.execute('SELECT * FROM elite_evaluations WHERE id=?',(item_id,)).fetchone()
        log_change(db,user,'retificar_decisao' if why else 'decidir','elite_evaluations',item_id,row,new,justificativa=why)
    return notify(request,'Decisão registrada.','/pelotao?'+urlencode({'ano':row['school_year'],'trimestre':row['trimester']}))


@app.get('/planos')
def plans_page(request:Request,user=Depends(authenticated)):
    allowed(user,ACADEMIC|MIL)
    with connection() as db:
        classes=db.execute('SELECT * FROM classes WHERE deleted_at IS NULL ORDER BY school_year DESC,grade,name').fetchall()
        plans=db.execute('SELECT p.*,c.name class_name FROM plans p JOIN classes c ON c.id=p.class_id AND c.deleted_at IS NULL WHERE p.deleted_at IS NULL ORDER BY p.deadline DESC,p.id DESC').fetchall()
    return render(request,'plans.html',classes=classes,plans=plans,can_edit=user['role'] in ACADEMIC,is_admin=user['role']=='admin')


@app.post('/planos')
async def add_plan(request:Request,user=Depends(authenticated)):
    allowed(user,ACADEMIC);form=await request.form();check_post(request,form)
    cid=numeric(form,'class_id',1,999999,True);tri=numeric(form,'trimester',1,3,True)
    with connection() as db:
        cl=db.execute('SELECT * FROM classes WHERE id=? AND deleted_at IS NULL',(cid,)).fetchone()
        if not cl: raise HTTPException(400,'Turma inválida')
        values=[nonempty(form,k,500 if k!='action' else 2000) for k in ('difficulty','target','action','owner','deadline','metric')]
        cur=db.execute("""INSERT INTO plans(class_id,school_year,trimester,difficulty,target,action,owner,deadline,metric,created_by)
            VALUES(?,?,?,?,?,?,?,?,?,?)""",(cid,cl['school_year'],tri,*values,user['id']))
        audit(db,user,'criar','plans',cur.lastrowid)
    return notify(request,'Plano de melhoria criado.','/planos')


@app.post('/planos/{plan_id}/status')
async def update_plan(request:Request,plan_id:int,user=Depends(authenticated)):
    allowed(user,ADMIN);form=await request.form();check_post(request,form)
    status=str(form.get('status','')); result=str(form.get('result','')).strip()[:1500]
    if status not in ('planejada','em_andamento','concluida'): raise HTTPException(400,'Status inválido')
    with connection() as db:
        old=db.execute('SELECT * FROM plans WHERE id=? AND deleted_at IS NULL',(plan_id,)).fetchone()
        if not old: raise HTTPException(404,'Plano não encontrado')
        why=require_admin_password(db,user,form)
        cur=db.execute('UPDATE plans SET status=?,result=? WHERE id=?',(status,result,plan_id))
        if not cur.rowcount: raise HTTPException(400,'Plano inexistente')
        new=db.execute('SELECT * FROM plans WHERE id=?',(plan_id,)).fetchone()
        log_change(db,user,'atualizar','plans',plan_id,old,new,justificativa=why)
    return notify(request,'Plano atualizado.','/planos')


@app.get('/relatorios')
def reports_page(request:Request,user=Depends(authenticated)):
    year,tri=year_trimester(request)
    return render(request,'reports.html',year=year,tri=tri)


@app.get('/relatorios/{kind}')
def report_download(request:Request,kind:str,user=Depends(authenticated)):
    year,tri=year_trimester(request)
    if kind not in ('pdf','xlsx','csv'): raise HTTPException(404,'Formato inexistente')
    with connection() as db:
        rows=rankings(db,year,tri)
        classes=db.execute('SELECT * FROM classes WHERE school_year=? AND deleted_at IS NULL ORDER BY grade,name',(year,)).fetchall()
        metrics=[class_metrics(db,c,tri) for c in classes]
        plans=db.execute("SELECT p.*,c.name class_name FROM plans p JOIN classes c ON c.id=p.class_id WHERE p.school_year=? AND p.trimester=? AND p.deleted_at IS NULL",
                         (year,tri)).fetchall()
    if kind=='pdf':
        binary=build_pdf(year,tri,metrics,rows,plans)
        typ='application/pdf'
    elif kind=='xlsx':
        binary=build_xlsx(year,tri,metrics,rows,plans)
        typ='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'
    else:
        buf=io.StringIO();writer=csv.writer(buf,delimiter=';')
        writer.writerow(['Turma','Série','Matriculados','Participantes','Português','Matemática','Conhecimentos Gerais','Acadêmico','Disciplina','Evolução','Resultado','Situação'])
        for x in metrics:
            writer.writerow([spreadsheet_safe(x['name']),x['grade'],x['enrolled'],x['participants'],
                             *[f'{x[k]:.2f}'.replace('.',',') if x[k] is not None else '' for k in ('P','M','G','A','D','E','R')],
                             'Completo' if x['R'] is not None else 'Pendente'])
        binary=('\ufeff'+buf.getvalue()).encode('utf-8');typ='text/csv; charset=utf-8'
    return StreamingResponse(io.BytesIO(binary),media_type=typ,headers={'Content-Disposition':f'attachment; filename="SER_{year}_T{tri}.{kind}"',
                                                                        'Cache-Control':'no-store'})


@app.get('/relatorios/analitico/{kind}')
def progress_report(request:Request,kind:str,user=Depends(authenticated)):
    # Série histórica por intervalo definido, sem mapear semestres.
    if kind not in ('pdf','xlsx'): raise HTTPException(404,'Formato inexistente')
    try:
        year=int(request.query_params.get('ano',date.today().year))
        start=int(request.query_params.get('inicio','1'))
        end=int(request.query_params.get('fim','3'))
    except ValueError: raise HTTPException(400,'Parâmetros inválidos')
    if not 2020<=year<=2100 or not 1<=start<end<=3: raise HTTPException(400,'Escolha um intervalo válido de pelo menos dois trimestres')
    with connection() as db:
        classes=db.execute('SELECT * FROM classes WHERE school_year=? AND deleted_at IS NULL ORDER BY grade,name',(year,)).fetchall()
        rows=[]
        for cl in classes:
            items=[class_metrics(db,cl,t) for t in range(start,end+1)]
            values=[i['A'] for i in items if i['A'] is not None]
            ds=[i['D'] for i in items if i['D'] is not None]
            es=[i['E'] for i in items if i['E'] is not None]
            rows.append({'name':cl['name'],'grade':cl['grade'],'trimesters':items,
                         'delta':values[-1]-values[0] if len(values)>=2 else None,
                         'd_avg':sum(ds)/len(ds) if ds else None,'e_avg':sum(es)/len(es) if es else None,
                         'complete':sum(i['R'] is not None for i in items)})
        plans=db.execute("""SELECT p.*, c.name class_name FROM plans p JOIN classes c ON c.id=p.class_id
            WHERE p.school_year=? AND p.trimester BETWEEN ? AND ? AND p.deleted_at IS NULL AND c.deleted_at IS NULL ORDER BY p.trimester,c.name""",(year,start,end)).fetchall()
    if kind=='pdf': binary=build_progress_pdf(year,start,end,rows,plans);typ='application/pdf'
    else:
        binary=build_progress_xlsx(year,start,end,rows,plans)
        typ='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'
    return StreamingResponse(io.BytesIO(binary),media_type=typ,headers={
        'Content-Disposition':f'attachment; filename="SER_Evolucao_{year}_T{start}-T{end}.{kind}"',
        'Cache-Control':'no-store'})


@app.get('/assembleias')
def decisions_page(request:Request,user=Depends(authenticated)):
    allowed(user,WRITE)
    with connection() as db:
        rows=db.execute('SELECT * FROM decisions WHERE deleted_at IS NULL ORDER BY date DESC,id DESC').fetchall()
    return render(request,'decisions.html',rows=rows,can_edit=user['role'] in WRITE,is_admin=user['role']=='admin')


@app.post('/assembleias')
async def add_decision(request:Request,user=Depends(authenticated)):
    allowed(user,WRITE); form=await request.form();check_post(request,form)
    year=numeric(form,'school_year',2020,2100,True);semester=numeric(form,'semester',1,2,True)
    values=[str(form.get(k,'')).strip()[:4000] for k in ('participants','agenda','decisions','owners','deadlines')]
    with connection() as db:
        cur=db.execute("INSERT INTO decisions(school_year,semester,date,participants,agenda,decisions,owners,deadlines,created_by) VALUES(?,?,?,?,?,?,?,?,?)",
                       (year,semester,nonempty(form,'date',10),*values,user['id']))
        audit(db,user,'criar','decisions',cur.lastrowid)
    return notify(request,'Assembleia registrada.','/assembleias')


@app.get('/usuarios')
def users_page(request:Request,user=Depends(authenticated)):
    allowed(user,ADMIN)
    with connection() as db:
        rows=db.execute('SELECT id,username,full_name,role,active,created_at FROM users ORDER BY full_name').fetchall()
    return render(request,'users.html',users=rows)


@app.post('/usuarios')
async def add_user(request:Request,user=Depends(authenticated)):
    allowed(user,ADMIN);form=await request.form();check_post(request,form)
    uname=nonempty(form,'username',50).lower();name=nonempty(form,'full_name',150)
    pwd=nonempty(form,'password',200);role=str(form.get('role',''))
    if role not in ROLES or len(pwd)<12: raise HTTPException(400,'Perfil inválido ou senha com menos de 12 caracteres')
    if not re.fullmatch(r'[a-z0-9._-]{3,50}',uname): raise HTTPException(400,'Usuário: use 3 a 50 letras minúsculas, números, ponto, _ ou -')
    with connection() as db:
        if db.execute('SELECT id FROM users WHERE username=?',(uname,)).fetchone():
            return notify(request,'Esse nome de usuário já existe.','/usuarios')
        cur=db.execute('INSERT INTO users(username,full_name,password_hash,role) VALUES(?,?,?,?)',
                       (uname,name,password_hash(pwd),role))
        audit(db,user,'criar','users',cur.lastrowid,{'role':role})
    return notify(request,'Usuário criado.','/usuarios')


@app.post('/usuarios/{uid}/alternar')
async def switch_user(request:Request,uid:int,user=Depends(authenticated)):
    allowed(user,ADMIN);form=await request.form();check_post(request,form)
    if uid==user['id']: raise HTTPException(400,'Não é permitido desativar sua própria conta')
    with connection() as db:
        row=db.execute('SELECT id,username,full_name,role,active FROM users WHERE id=?',(uid,)).fetchone()
        if not row: raise HTTPException(400,'Usuário não encontrado')
        why=require_admin_password(db,user,form)
        db.execute('UPDATE users SET active=? WHERE id=?',(0 if row['active'] else 1,uid))
        new=db.execute('SELECT id,username,full_name,role,active FROM users WHERE id=?',(uid,)).fetchone()
        log_change(db,user,'alternar_status','users',uid,row,new,justificativa=why)
    return notify(request,'Situação do usuário alterada.','/usuarios')


@app.get('/auditoria')
def audit_page(request:Request,user=Depends(authenticated)):
    allowed(user,ADMIN)
    with connection() as db:
        rows=db.execute("""SELECT a.*,u.full_name FROM audit_log a LEFT JOIN users u ON a.user_id=u.id
            ORDER BY a.id DESC LIMIT 300""").fetchall()
    return render(request,'audit.html',rows=rows)

# --- Administração de simulados e retificações controladas ---
@app.get('/simulados/{assessment_id}/editar')
def edit_assessment_page(request:Request,assessment_id:int,user=Depends(authenticated)):
    allowed(user,ADMIN)
    with connection() as db:
        a=db.execute('SELECT * FROM assessments WHERE id=?',(assessment_id,)).fetchone()
        if not a or a['deleted_at']:raise HTTPException(404,'Simulado não encontrado')
        result_count=db.execute('SELECT COUNT(*) n FROM assessment_results WHERE assessment_id=?',(assessment_id,)).fetchone()['n']
    return render(request,'assessment_edit.html',assessment=a,result_count=result_count)


@app.post('/simulados/{assessment_id}/editar')
async def edit_assessment(request:Request,assessment_id:int,user=Depends(authenticated)):
    allowed(user,ADMIN);form=await request.form();check_post(request,form)
    title=nonempty(form,'title',150)
    year=numeric(form,'school_year',2020,2100,True)
    tri=numeric(form,'trimester',1,3,True)
    grade=numeric(form,'grade',6,9,True)
    keys=[normalize_key(form.get(f'key_{k}')) for k in ('p','m','g')]
    skills=[parse_skills(form.get(f'skills_{k}')) for k in ('p','m','g')]
    if any(keys) and not all(keys):raise HTTPException(400,'Cadastre os três gabaritos ou nenhum')
    if any(skills) and not all(keys):raise HTTPException(400,'Habilidades dependem de gabaritos')
    dt=str(form.get('date','')).strip() or None
    if dt:
        try:date.fromisoformat(dt)
        except ValueError:raise HTTPException(400,'Data inválida')
    with connection() as db:
        old=db.execute('SELECT * FROM assessments WHERE id=?',(assessment_id,)).fetchone()
        if not old or old['deleted_at']:raise HTTPException(404,'Simulado não encontrado')
        why=require_admin_password(db,user,form)
        count=db.execute('SELECT COUNT(*) n FROM assessment_results WHERE assessment_id=?',(assessment_id,)).fetchone()['n']
        if count and (old['school_year'],old['trimester'],old['grade'])!=(year,tri,grade):
            raise HTTPException(400,'Não é permitido mudar série, ano ou trimestre com resultados vinculados. Preserve o histórico.')
        if db.execute('SELECT id FROM assessments WHERE school_year=? AND trimester=? AND grade=? AND id!=?',
                      (year,tri,grade,assessment_id)).fetchone():
            raise HTTPException(400,'Já existe simulado para essa série e trimestre')
        db.execute('''UPDATE assessments SET title=?,school_year=?,trimester=?,grade=?,date=?,
            key_p=?,key_m=?,key_g=?,skills_p=?,skills_m=?,skills_g=? WHERE id=?''',
            (title,year,tri,grade,dt,*keys,*skills,assessment_id))
        new=db.execute('SELECT * FROM assessments WHERE id=?',(assessment_id,)).fetchone()
        log_change(db,user,'retificar_simulado_validado' if why else 'editar_simulado',
                   'assessments',assessment_id,old,new,justificativa=why)
        # Recalcular acertos de respostas armazenadas sempre que um gabarito mudar.
        for code in ('p','m','g'):
            if old[f'key_{code}']==new[f'key_{code}']:continue
            stored=db.execute(f'''SELECT * FROM assessment_results WHERE assessment_id=? AND answers_{code} IS NOT NULL''',
                              (assessment_id,)).fetchall()
            for previous in stored:
                key=new[f'key_{code}'];answers=previous[f'answers_{code}']
                if key:
                    points=sum(x==y for x,y in zip(key,answers))
                    db.execute(f'UPDATE assessment_results SET correct_{code}=?,updated_at=CURRENT_TIMESTAMP WHERE id=?',
                               (points,previous['id']))
                else:
                    db.execute(f'UPDATE assessment_results SET answers_{code}=NULL,updated_at=CURRENT_TIMESTAMP WHERE id=?',
                               (previous['id'],))
                changed=db.execute('SELECT * FROM assessment_results WHERE id=?',(previous['id'],)).fetchone()
                log_change(db,user,'recalcular_por_gabarito','assessment_results',previous['id'],previous,changed,
                           justificativa=why,simulado=assessment_id)
    return notify(request,'Simulado atualizado. Mudanças de gabarito foram recalculadas e auditadas.',
                  f'/simulados/{assessment_id}')


@app.post('/simulados/{assessment_id}/excluir')
async def archive_assessment(request:Request,assessment_id:int,user=Depends(authenticated)):
    allowed(user,ADMIN);form=await request.form();check_post(request,form)
    with connection() as db:
        old=db.execute('SELECT * FROM assessments WHERE id=?',(assessment_id,)).fetchone()
        if not old or old['deleted_at']:raise HTTPException(400,'Simulado não encontrado ou já arquivado')
        why=require_admin_password(db,user,form)
        db.execute('UPDATE assessments SET deleted_at=CURRENT_TIMESTAMP,deleted_by=? WHERE id=?',
                   (user['id'],assessment_id))
        new=db.execute('SELECT * FROM assessments WHERE id=?',(assessment_id,)).fetchone()
        log_change(db,user,'arquivar_simulado','assessments',assessment_id,old,new,justificativa=why,
                   observacao='Exclusão lógica; respostas permanecem preservadas para restauração')
    return notify(request,'Simulado retirado dos indicadores e arquivado (pode ser restaurado).','/simulados')


@app.post('/simulados/{assessment_id}/restaurar')
async def restore_assessment(request:Request,assessment_id:int,user=Depends(authenticated)):
    allowed(user,ADMIN);form=await request.form();check_post(request,form)
    with connection() as db:
        old=db.execute('SELECT * FROM assessments WHERE id=?',(assessment_id,)).fetchone()
        if not old or not old['deleted_at']:raise HTTPException(400,'Simulado não está arquivado')
        why=require_admin_password(db,user,form)
        db.execute('UPDATE assessments SET deleted_at=NULL,deleted_by=NULL WHERE id=?',(assessment_id,))
        new=db.execute('SELECT * FROM assessments WHERE id=?',(assessment_id,)).fetchone()
        log_change(db,user,'restaurar_simulado','assessments',assessment_id,old,new,justificativa=why)
    return notify(request,'Simulado restaurado e auditado.',f'/simulados/{assessment_id}')


@app.post('/vistorias/{inspection_id}/editar')
async def edit_inspection(request:Request,inspection_id:int,user=Depends(authenticated)):
    allowed(user,ADMIN);form=await request.form();check_post(request,form)
    scores=[numeric(form,key,0,maxscore) for key,maxscore,_ in FIELDS]
    bim=numeric(form,'bimester',1,4,True);tri=numeric(form,'trimester',1,3,True)
    notes=str(form.get('notes','')).strip()[:2000];dt=nonempty(form,'date',10)
    try:date.fromisoformat(dt)
    except ValueError:raise HTTPException(400,'Data inválida')
    with connection() as db:
        old=db.execute('SELECT * FROM inspections WHERE id=?',(inspection_id,)).fetchone()
        if not old:raise HTTPException(404,'Vistoria não encontrada')
        why=require_admin_password(db,user,form)
        db.execute('''UPDATE inspections SET bimester=?,trimester=?,date=?,behavior=?,order_drill=?,punctuality=?,
                    rules=?,conservation=?,cooperation=?,notes=? WHERE id=?''',(bim,tri,dt,*scores,notes,inspection_id))
        new=db.execute('SELECT * FROM inspections WHERE id=?',(inspection_id,)).fetchone()
        log_change(db,user,'retificar_vistoria_validada' if why else 'editar_vistoria',
                   'inspections',inspection_id,old,new,justificativa=why)
    return notify(request,'Vistoria atualizada com auditoria.','/vistorias')


@app.post('/evolucao/{e_id}/editar')
async def edit_evolution(request:Request,e_id:int,user=Depends(authenticated)):
    allowed(user,ADMIN);form=await request.form();check_post(request,form)
    score=numeric(form,'score',0,100);evidence=nonempty(form,'evidence',3000)
    with connection() as db:
        old=db.execute('SELECT * FROM evolution WHERE id=?',(e_id,)).fetchone()
        if not old:raise HTTPException(404,'Registro de evolução não encontrado')
        why=require_admin_password(db,user,form)
        db.execute('UPDATE evolution SET score=?,evidence=? WHERE id=?',(score,evidence,e_id))
        new=db.execute('SELECT * FROM evolution WHERE id=?',(e_id,)).fetchone()
        log_change(db,user,'retificar_evolucao_validada' if why else 'editar_evolucao',
                   'evolution',e_id,old,new,justificativa=why)
    return notify(request,'Indicador atualizado e auditado.','/evolucao')


@app.post('/pelotao/{item_id}/editar')
async def edit_elite_decision(request:Request,item_id:int,user=Depends(authenticated)):
    allowed(user,ADMIN);form=await request.form();check_post(request,form)
    with connection() as db:
        old=db.execute('SELECT * FROM elite_evaluations WHERE id=?',(item_id,)).fetchone()
        if not old:raise HTTPException(404,'Avaliação inexistente')
        why=require_admin_password(db,user,form)
        values=[numeric(form,k,0,100) for k in ('conduct','drill','frequency')]
        notes=str(form.get('notes','')).strip()[:1500]
        db.execute('''UPDATE elite_evaluations SET conduct=?,drill=?,frequency=?,training=?,blocked=?,notes=?,
          decision='pendente',reviewed_by=NULL WHERE id=?''',
          (*values,int(bool(form.get('training'))),int(bool(form.get('blocked'))),notes,item_id))
        new=db.execute('SELECT * FROM elite_evaluations WHERE id=?',(item_id,)).fetchone()
        log_change(db,user,'retificar_pelotao_decidido' if why else 'editar_pelotao',
                   'elite_evaluations',item_id,old,new,justificativa=why)
    return notify(request,'Avaliação retificada e decisão devolvida a pendente.',
                  '/pelotao?'+urlencode({'ano':old['school_year'],'trimestre':old['trimester']}))


@app.post('/evolucao/rubrica/{rubric_id}/editar')
async def edit_rubric(request:Request,rubric_id:int,user=Depends(authenticated)):
    allowed(user,ADMIN);form=await request.form();check_post(request,form)
    title=nonempty(form,'title',160);description=nonempty(form,'description',5000)
    with connection() as db:
        old=db.execute('SELECT * FROM rubrics WHERE id=?',(rubric_id,)).fetchone()
        if not old:raise HTTPException(404,'Ficha inexistente')
        why=require_admin_password(db,user,form)
        db.execute('UPDATE rubrics SET title=?,description=? WHERE id=?',(title,description,rubric_id))
        new=db.execute('SELECT * FROM rubrics WHERE id=?',(rubric_id,)).fetchone()
        log_change(db,user,'retificar_ficha_aprovada' if why else 'editar_ficha','rubrics',rubric_id,old,new,
                   justificativa=why)
    return notify(request,'Ficha atualizada e registrada na auditoria.','/evolucao')


@app.post('/turmas/{class_id}/editar')
async def edit_class(request:Request,class_id:int,user=Depends(authenticated)):
    allowed(user,ADMIN);form=await request.form();check_post(request,form)
    name=nonempty(form,'name',80);shift=nonempty(form,'shift',50)
    grade=numeric(form,'grade',6,9,True);year=numeric(form,'school_year',2020,2100,True)
    with connection() as db:
        old=db.execute('SELECT * FROM classes WHERE id=?',(class_id,)).fetchone()
        if not old:raise HTTPException(404,'Turma não encontrada')
        has_enroll=db.execute('SELECT 1 FROM enrollments WHERE class_id=? LIMIT 1',(class_id,)).fetchone()
        if has_enroll and (old['grade'],old['school_year'])!=(grade,year):
            raise HTTPException(400,'Série/ano não podem mudar em turma que já recebeu alunos. Crie uma nova turma.')
        validated=db.execute('''SELECT 1 FROM assessment_results r JOIN assessments a ON a.id=r.assessment_id
                 WHERE r.class_id=? AND a.status='validado' LIMIT 1''',(class_id,)).fetchone()
        why=require_admin_password(db,user,form)
        if db.execute('SELECT 1 FROM classes WHERE name=? AND school_year=? AND id!=?',
                      (name,year,class_id)).fetchone():raise HTTPException(400,'Nome de turma já utilizado neste ano')
        db.execute('UPDATE classes SET name=?,shift=?,grade=?,school_year=? WHERE id=?',
                   (name,shift,grade,year,class_id))
        new=db.execute('SELECT * FROM classes WHERE id=?',(class_id,)).fetchone()
        log_change(db,user,'editar_turma','classes',class_id,old,new,justificativa=why)
    return notify(request,'Dados da turma atualizados e auditados.','/turmas')


@app.post('/planos/{plan_id}/editar')
async def edit_plan(request:Request,plan_id:int,user=Depends(authenticated)):
    allowed(user,ADMIN);form=await request.form();check_post(request,form)
    values=[nonempty(form,k,2000 if k=='action' else 500) for k in
            ('difficulty','target','action','owner','deadline','metric')]
    with connection() as db:
        old=db.execute('SELECT * FROM plans WHERE id=?',(plan_id,)).fetchone()
        if not old:raise HTTPException(404,'Plano não encontrado')
        why=require_admin_password(db,user,form)
        db.execute('''UPDATE plans SET difficulty=?,target=?,action=?,owner=?,deadline=?,metric=?
          WHERE id=?''',(*values,plan_id))
        new=db.execute('SELECT * FROM plans WHERE id=?',(plan_id,)).fetchone()
        log_change(db,user,'editar_plano','plans',plan_id,old,new,justificativa=why)
    return notify(request,'Plano atualizado e auditado.','/planos')


@app.post('/assembleias/{decision_id}/editar')
async def edit_decision(request:Request,decision_id:int,user=Depends(authenticated)):
    allowed(user,ADMIN);form=await request.form();check_post(request,form)
    vals=[str(form.get(k,'')).strip()[:4000] for k in ('participants','agenda','decisions','owners','deadlines')]
    with connection() as db:
        old=db.execute('SELECT * FROM decisions WHERE id=?',(decision_id,)).fetchone()
        if not old:raise HTTPException(404,'Assembleia não encontrada')
        why=require_admin_password(db,user,form)
        db.execute('''UPDATE decisions SET participants=?,agenda=?,decisions=?,owners=?,deadlines=? WHERE id=?''',
                   (*vals,decision_id))
        new=db.execute('SELECT * FROM decisions WHERE id=?',(decision_id,)).fetchone()
        log_change(db,user,'retificar_ata','decisions',decision_id,old,new,justificativa=why)
    return notify(request,'Assembleia retificada e auditada.','/assembleias')

# --------------------------------------------------------------------
# SIMR 3: fotografia privada dos alunos e ocorrências disciplinares.
# --------------------------------------------------------------------
@app.get('/alunos/{student_id}/foto')
def student_photo(student_id:int, user=Depends(authenticated)):
    allowed(user,ACADEMIC|MIL)
    with connection() as db:
        student=db.execute('SELECT photo_path FROM students WHERE id=? AND deleted_at IS NULL',(student_id,)).fetchone()
    if not student or not student['photo_path']:
        raise HTTPException(404,'Foto não cadastrada')
    return FileResponse(get_stored_image(student['photo_path']),media_type='image/jpeg',
                        headers={'Cache-Control':'private, no-store','X-Content-Type-Options':'nosniff'})


@app.post('/alunos/{student_id}/foto')
async def upload_student_photo(request:Request,student_id:int,user=Depends(authenticated)):
    allowed(user,ADMIN)
    form=await request.form();check_post(request,form)
    with connection() as db:
        old=db.execute('SELECT * FROM students WHERE id=?',(student_id,)).fetchone()
        if not old: raise HTTPException(404,'Aluno não encontrado')
        has_validated=db.execute('''SELECT 1 FROM assessment_results ar JOIN assessments a ON a.id=ar.assessment_id
                                  WHERE ar.student_id=? AND a.status='validado' LIMIT 1''',(student_id,)).fetchone()
        why=require_admin_password(db,user,form)
        previous_sha=None
        if old['photo_path']:
            previous_photo=get_stored_image(old['photo_path'])
            previous_sha=hashlib.sha256(previous_photo.read_bytes()).hexdigest()
        uploaded=form.get('photo')
        meta=await store_image(uploaded,'alunos')
        db.execute('UPDATE students SET photo_path=? WHERE id=?',(meta['storage_path'],student_id))
        audit(db,user,'alterar_foto_aluno','students',student_id,{
             'anterior':{'caminho':old['photo_path'],'sha256':previous_sha},
             'novo':{'caminho':meta['storage_path'],'sha256':meta['sha256'],
                     'tamanho_bytes':meta['size_bytes']},'justificativa':why})
    return notify(request,'Foto do aluno cadastrada com segurança e auditoria.',f'/alunos/{student_id}')


def occurrence_detail_data(db,occurrence_id:int):
    record=db.execute('''SELECT o.*,s.name student_name,s.registration,
        uc.full_name creator_name,uv.full_name validator_name
        FROM occurrences o JOIN students s ON s.id=o.student_id AND s.deleted_at IS NULL
        LEFT JOIN users uc ON uc.id=o.created_by
        LEFT JOIN users uv ON uv.id=o.validated_by WHERE o.id=? AND o.deleted_at IS NULL''',(occurrence_id,)).fetchone()
    if not record: raise HTTPException(404,'Ocorrência não encontrada')
    return record


@app.get('/ocorrencias')
def occurrences_list(request:Request,user=Depends(authenticated)):
    allowed(user,{'admin','militar'})
    with connection() as db:
        occurrences=db.execute('''SELECT o.*,s.name student_name,s.registration,
            (SELECT COUNT(*) FROM occurrence_actions a WHERE a.occurrence_id=o.id AND a.deleted_at IS NULL) action_count
            FROM occurrences o JOIN students s ON s.id=o.student_id
            WHERE o.deleted_at IS NULL AND s.deleted_at IS NULL ORDER BY o.incident_date DESC,o.id DESC LIMIT 350''').fetchall()
    return render(request,'occurrences.html',occurrences=occurrences,is_admin=user['role']=='admin',
                  infraction_text=infraction_text)


@app.get('/ocorrencias/nova')
def occurrence_new(request:Request,user=Depends(authenticated)):
    allowed(user,ADMIN)
    student_value=request.query_params.get('aluno','')
    with connection() as db:
        students=db.execute('SELECT id,name,registration FROM students WHERE active=1 AND deleted_at IS NULL ORDER BY name').fetchall()
    return render(request,'occurrence_new.html',students=students,student_value=student_value)


@app.post('/ocorrencias')
async def create_occurrence(request:Request,user=Depends(authenticated)):
    allowed(user,ADMIN)
    form=await request.form();check_post(request,form)
    student_id=numeric(form,'student_id',1,999999999,True)
    when=validate_incident_date(str(form.get('incident_date','')))
    infraction=str(form.get('infraction_type','')).strip()
    if infraction not in ('leve','media','grave','eliminatoria'):
        raise HTTPException(400,'Tipo de infração inválido')
    description=nonempty(form,'description',10000)
    with connection() as db:
        if not db.execute('SELECT id FROM students WHERE id=? AND active=1 AND deleted_at IS NULL',(student_id,)).fetchone():
            raise HTTPException(400,'Aluno ativo não encontrado')
        cursor=db.execute('''INSERT INTO occurrences(student_id,incident_date,infraction_type,description,created_by)
                             VALUES(?,?,?,?,?)''',(student_id,when,infraction,description,user['id']))
        incident_id=cursor.lastrowid
        audit(db,user,'criar_ocorrencia','occurrences',incident_id,{
            'aluno_id':student_id,'data':when,'classificacao':infraction,'descricao':description})
    return notify(request,'Ocorrência salva em rascunho. Cadastre as medidas e valide quando estiver correto.',
                  f'/ocorrencias/{incident_id}')


@app.get('/ocorrencias/anexos/{attachment_id}')
def occurrence_attachment(attachment_id:int,user=Depends(authenticated)):
    allowed(user,{'admin','militar'})
    with connection() as db:
        attachment=db.execute('SELECT * FROM occurrence_attachments WHERE id=? AND deleted_at IS NULL',(attachment_id,)).fetchone()
    if not attachment: raise HTTPException(404,'Anexo não encontrado')
    return FileResponse(get_stored_image(attachment['storage_path']),media_type='image/jpeg',
                        headers={'Cache-Control':'private, no-store','X-Content-Type-Options':'nosniff'})


@app.get('/ocorrencias/{occurrence_id}')
def occurrence_detail(request:Request,occurrence_id:int,user=Depends(authenticated)):
    allowed(user,{'admin','militar'})
    with connection() as db:
        occurrence=occurrence_detail_data(db,occurrence_id)
        actions=db.execute('''SELECT a.*,u.full_name author_name FROM occurrence_actions a
           JOIN users u ON a.created_by=u.id WHERE occurrence_id=? AND a.deleted_at IS NULL ORDER BY a.id''',(occurrence_id,)).fetchall()
        attachments=db.execute('''SELECT * FROM occurrence_attachments WHERE occurrence_id=? AND deleted_at IS NULL ORDER BY id''',
                               (occurrence_id,)).fetchall()
    return render(request,'occurrence_detail.html',incident=occurrence,actions=actions,
                  attachments=attachments,is_admin=user['role']=='admin',infraction_text=infraction_text)


@app.post('/ocorrencias/{occurrence_id}/editar')
async def edit_occurrence(request:Request,occurrence_id:int,user=Depends(authenticated)):
    allowed(user,ADMIN)
    form=await request.form();check_post(request,form)
    when=validate_incident_date(str(form.get('incident_date','')))
    kind=str(form.get('infraction_type','')).strip()
    if kind not in ('leve','media','grave','eliminatoria'):raise HTTPException(400,'Tipo de infração inválido')
    description=nonempty(form,'description',10000)
    with connection() as db:
        old=occurrence_detail_data(db,occurrence_id)
        why=require_admin_password(db,user,form)
        db.execute('''UPDATE occurrences SET incident_date=?,infraction_type=?,description=?,updated_at=CURRENT_TIMESTAMP
                      WHERE id=?''',(when,kind,description,occurrence_id))
        new=occurrence_detail_data(db,occurrence_id)
        log_change(db,user,'retificar_ocorrencia_validada' if why else 'editar_ocorrencia',
                   'occurrences',occurrence_id,old,new,justificativa=why)
    return notify(request,'Ocorrência atualizada e auditada.',f'/ocorrencias/{occurrence_id}')


@app.post('/ocorrencias/{occurrence_id}/acoes')
async def add_occurrence_action(request:Request,occurrence_id:int,user=Depends(authenticated)):
    allowed(user,ADMIN)
    form=await request.form();check_post(request,form)
    action_type=str(form.get('action_type','')).strip()
    if action_type not in ('advertencia','suspensao'): raise HTTPException(400,'Medida inválida')
    description=nonempty(form,'description',10000)
    days=numeric(form,'suspension_days',1,365,True) if action_type=='suspensao' else None
    with connection() as db:
        incident=occurrence_detail_data(db,occurrence_id)
        why=require_admin_password(db,user,form)
        upload=form.get('attachment')
        meta=await store_image(upload,'ocorrencias') if getattr(upload,'filename',None) else None
        cursor=db.execute('''INSERT INTO occurrence_actions(occurrence_id,action_type,description,suspension_days,created_by)
                             VALUES(?,?,?,?,?)''',(occurrence_id,action_type,description,days,user['id']))
        action_id=cursor.lastrowid
        attachment_id=None
        if meta:
            cur=db.execute('''INSERT INTO occurrence_attachments
                  (occurrence_id,action_id,storage_path,filename,mime_type,size_bytes,sha256,created_by)
                  VALUES(?,?,?,?,?,?,?,?)''',(occurrence_id,action_id,meta['storage_path'],meta['filename'],
                               meta['mime_type'],meta['size_bytes'],meta['sha256'],user['id']))
            attachment_id=cur.lastrowid
        audit(db,user,'adicionar_medida_validada' if why else 'adicionar_medida','occurrence_actions',action_id,
              {'ocorrencia_id':occurrence_id,'tipo':action_type,'descricao':description,
               'dias_suspensao':days,'anexo_id':attachment_id,'anexo_sha256':meta['sha256'] if meta else None,
               'justificativa':why})
    return notify(request,'Medida registrada com auditoria.',f'/ocorrencias/{occurrence_id}')


@app.post('/ocorrencias/{occurrence_id}/acoes/{action_id}/editar')
async def edit_occurrence_action(request:Request,occurrence_id:int,action_id:int,user=Depends(authenticated)):
    allowed(user,ADMIN)
    form=await request.form();check_post(request,form)
    kind=str(form.get('action_type','')).strip()
    if kind not in ('advertencia','suspensao'):raise HTTPException(400,'Tipo de medida inválido')
    description=nonempty(form,'description',10000)
    days=numeric(form,'suspension_days',1,365,True) if kind=='suspensao' else None
    with connection() as db:
        incident=occurrence_detail_data(db,occurrence_id)
        old=db.execute('SELECT * FROM occurrence_actions WHERE id=? AND occurrence_id=?',
                       (action_id,occurrence_id)).fetchone()
        if not old:raise HTTPException(404,'Medida não encontrada')
        why=require_admin_password(db,user,form)
        db.execute('''UPDATE occurrence_actions SET action_type=?,description=?,suspension_days=? WHERE id=?''',
                   (kind,description,days,action_id))
        new=db.execute('SELECT * FROM occurrence_actions WHERE id=?',(action_id,)).fetchone()
        log_change(db,user,'retificar_medida_validada' if why else 'editar_medida',
                   'occurrence_actions',action_id,old,new,ocorrencia_id=occurrence_id,justificativa=why)
    return notify(request,'Medida editada e auditada.',f'/ocorrencias/{occurrence_id}')


@app.post('/ocorrencias/{occurrence_id}/acoes/{action_id}/anexos')
async def add_occurrence_attachment(request:Request,occurrence_id:int,action_id:int,user=Depends(authenticated)):
    allowed(user,ADMIN)
    form=await request.form();check_post(request,form)
    with connection() as db:
        incident=occurrence_detail_data(db,occurrence_id)
        act=db.execute('SELECT id FROM occurrence_actions WHERE id=? AND occurrence_id=?',
                       (action_id,occurrence_id)).fetchone()
        if not act:raise HTTPException(404,'Medida não encontrada')
        why=require_admin_password(db,user,form)
        meta=await store_image(form.get('attachment'),'ocorrencias')
        cur=db.execute('''INSERT INTO occurrence_attachments
            (occurrence_id,action_id,storage_path,filename,mime_type,size_bytes,sha256,created_by)
            VALUES(?,?,?,?,?,?,?,?)''',(occurrence_id,action_id,meta['storage_path'],meta['filename'],
                              meta['mime_type'],meta['size_bytes'],meta['sha256'],user['id']))
        audit(db,user,'anexar_evidencia','occurrence_attachments',cur.lastrowid,
              {'ocorrencia_id':occurrence_id,'medida_id':action_id,'sha256':meta['sha256'],'justificativa':why})
    return notify(request,'Foto anexada e registrada na auditoria.',f'/ocorrencias/{occurrence_id}')


@app.post('/ocorrencias/{occurrence_id}/validar')
async def validate_occurrence(request:Request,occurrence_id:int,user=Depends(authenticated)):
    allowed(user,ADMIN)
    form=await request.form();check_post(request,form)
    with connection() as db:
        old=occurrence_detail_data(db,occurrence_id)
        if old['status']=='validado':raise HTTPException(400,'Ocorrência já está validada')
        why=require_admin_password(db,user,form)
        db.execute('''UPDATE occurrences SET status='validado',validated_by=?,
                      validated_at=CURRENT_TIMESTAMP,updated_at=CURRENT_TIMESTAMP WHERE id=?''',
                   (user['id'],occurrence_id))
        new=occurrence_detail_data(db,occurrence_id)
        log_change(db,user,'validar_ocorrencia','occurrences',occurrence_id,old,new,justificativa=why)
    return notify(request,'Ocorrência validada. Alterações posteriores exigem senha e justificativa.',
                  f'/ocorrencias/{occurrence_id}')


@app.get('/ocorrencias/{occurrence_id}/boletim.pdf')
def occurrence_boletim(occurrence_id:int,user=Depends(authenticated)):
    allowed(user,{'admin','militar'})
    with connection() as db:
        occurrence=occurrence_detail_data(db,occurrence_id)
        student=db.execute('SELECT * FROM students WHERE id=?',(occurrence['student_id'],)).fetchone()
        actions=db.execute('SELECT * FROM occurrence_actions WHERE occurrence_id=? AND deleted_at IS NULL ORDER BY id',
                           (occurrence_id,)).fetchall()
        attachments=db.execute('SELECT * FROM occurrence_attachments WHERE occurrence_id=? AND deleted_at IS NULL ORDER BY id',
                               (occurrence_id,)).fetchall()
        content=occurrence_pdf(occurrence,student,actions,attachments)
        audit(db,user,'emitir_boletim','occurrences',occurrence_id,{'status':occurrence['status']})
    return Response(content,media_type='application/pdf',
                    headers={'Content-Disposition':f'attachment; filename="boletim_ocorrencia_{occurrence_id:06}.pdf"',
                             'Cache-Control':'private, no-store','X-Content-Type-Options':'nosniff'})

# ---------------------------------------------------------------------------
# SIMR 4.0: Administração auditável e indicador de registros comportamentais.
# ---------------------------------------------------------------------------
# Nenhuma operação administrativa apaga linhas do banco fisicamente.
ARCHIVE_TYPES = {
    'classes':'/turmas', 'students':'/alunos', 'enrollments':'/administracao',
    'assessments':'/simulados', 'assessment_results':'/administracao',
    'inspections':'/vistorias', 'rubrics':'/evolucao', 'evolution':'/evolucao',
    'plans':'/planos', 'elite_evaluations':'/pelotao', 'decisions':'/assembleias',
    'adjustments':'/administracao', 'occurrences':'/ocorrencias',
    'occurrence_actions':'/administracao', 'occurrence_attachments':'/administracao',
    'behavior_deductions':'/administracao'
}


@app.get('/administracao')
@app.get('/auditoria/arquivados')
def admin_data_page(request:Request,user=Depends(authenticated)):
    """Centro unificado de ativos e arquivados. Nunca mistura arquivados nas listas operacionais."""
    allowed(user, ADMIN)
    entity=request.query_params.get('tipo','classes')
    status=request.query_params.get('estado','arquivados')
    if entity not in ARCHIVE_TYPES: raise HTTPException(400,'Tipo de registro inválido')
    if status not in ('ativos','arquivados'): raise HTTPException(400,'Filtro de situação inválido')
    target='IS NULL' if status=='ativos' else 'IS NOT NULL'
    with connection() as db:
        records=db.execute(f'SELECT * FROM {entity} WHERE deleted_at {target} ORDER BY id DESC LIMIT 150').fetchall()
        totals={r['state']:r['n'] for r in db.execute(f'''SELECT CASE WHEN deleted_at IS NULL THEN 'ativos' ELSE 'arquivados' END state,
                                   COUNT(*) n FROM {entity} GROUP BY state''').fetchall()}
        all_archived=sum(db.execute(f'SELECT COUNT(*) FROM {k} WHERE deleted_at IS NOT NULL').fetchone()[0]
                         for k in ARCHIVE_TYPES)
    # Não mostra senhas, conteúdo dos gabaritos, identificadores de armazenamento privado.
    summaries={
      'classes':('name','grade','school_year'), 'students':('name','registration','active'),
      'enrollments':('student_id','class_id','status'),
      'assessments':('title','status','school_year'),
      'assessment_results':('assessment_id','student_id','attendance'),
      'inspections':('class_id','date','status'), 'rubrics':('title','status'),
      'evolution':('class_id','score','status'), 'plans':('class_id','difficulty','status'),
      'elite_evaluations':('student_id','decision','school_year'),
      'decisions':('school_year','semester','date'),
      'adjustments':('class_id','delta','justification'),
      'occurrences':('student_id','incident_date','status'),
      'occurrence_actions':('occurrence_id','action_type','suspension_days'),
      'occurrence_attachments':('occurrence_id','action_id','filename'),
      'behavior_deductions':('student_id','event_date','points')}
    return render(request,'admin_data.html',records=records,entity=entity,types=ARCHIVE_TYPES,
                  summary_fields=summaries[entity],status=status,totals=totals,all_archived=all_archived)


@app.post('/administracao/{entity}/{row_id}/{operation}')
async def admin_archive(request:Request,entity:str,row_id:int,operation:str,user=Depends(authenticated)):
    allowed(user,ADMIN)
    if entity not in ARCHIVE_TYPES or operation not in ('arquivar','restaurar'):
        raise HTTPException(404,'Operação não permitida')
    form=await request.form();check_post(request,form)
    with connection() as db:
        justification=require_admin_password(db,user,form)
        old=db.execute(f'SELECT * FROM {entity} WHERE id=?',(row_id,)).fetchone()
        if not old:raise HTTPException(404,'Registro não encontrado')
        if (old['deleted_at'] is None) != (operation=='arquivar'):
            raise HTTPException(400,'Registro já está neste estado')
        if entity=='classes' and operation=='restaurar':
            duplicate=db.execute('''SELECT id FROM classes WHERE name=? AND school_year=?
                                   AND deleted_at IS NULL AND id!=?''',
                                 (old['name'],old['school_year'],row_id)).fetchone()
            if duplicate:raise HTTPException(400,'Conflito com outra turma ativa')
        if operation=='arquivar':
            db.execute(f'UPDATE {entity} SET deleted_at=CURRENT_TIMESTAMP, deleted_by=? WHERE id=?',
                       (user['id'],row_id))
        else:
            db.execute(f'UPDATE {entity} SET deleted_at=NULL, deleted_by=NULL WHERE id=?',(row_id,))
        new=db.execute(f'SELECT * FROM {entity} WHERE id=?',(row_id,)).fetchone()
        log_change(db,user,operation,entity,row_id,old,new,
                   justificativa=justification,exclusao_logica=True)
    return notify(request,f'Registro {"arquivado" if operation=="arquivar" else "restaurado"} com auditoria.',
                  '/auditoria/arquivados?'+urlencode({'tipo':entity,'estado':'arquivados' if operation=='arquivar' else 'ativos'}))


@app.post('/administracao/campos/{entity}/{row_id}')
async def admin_edit_additional(request:Request,entity:str,row_id:int,user=Depends(authenticated)):
    """Edição complementar de relações e ajustes sem alterar chaves históricas."""
    allowed(user,ADMIN)
    if entity not in ('enrollments','adjustments'):
        raise HTTPException(404,'Use o formulário próprio para este tipo de registro')
    form=await request.form();check_post(request,form)
    with connection() as db:
        why=require_admin_password(db,user,form)
        old=db.execute(f'SELECT * FROM {entity} WHERE id=? AND deleted_at IS NULL',(row_id,)).fetchone()
        if not old:raise HTTPException(404,'Registro não encontrado ou arquivado')
        if entity=='enrollments':
            status=str(form.get('status',''))
            if status not in ('ativa','transferida','desligada'):
                raise HTTPException(400,'Situação inválida')
            if status=='ativa':
                db.execute("UPDATE enrollments SET status='transferida' WHERE student_id=? AND status='ativa' AND id!=?",
                           (old['student_id'],row_id))
            db.execute('UPDATE enrollments SET status=? WHERE id=?',(status,row_id))
        else:
            delta=numeric(form,'delta',-3,3)
            why_detail=nonempty(form,'adjustment_reason',1500)
            db.execute('UPDATE adjustments SET delta=?,justification=? WHERE id=?',
                       (delta,why_detail,row_id))
        new=db.execute(f'SELECT * FROM {entity} WHERE id=?',(row_id,)).fetchone()
        log_change(db,user,'editar',entity,row_id,old,new,justificativa=why)
    return notify(request,'Registro atualizado e auditado.',
                  '/auditoria/arquivados?'+urlencode({'tipo':entity,'estado':'ativos'}))


@app.post('/alunos/{student_id}/descontos')
async def create_behavior_deduction(request:Request,student_id:int,user=Depends(authenticated)):
    allowed(user,ADMIN)
    form=await request.form();check_post(request,form)
    dt=validate_incident_date(str(form.get('event_date','')))
    pts=numeric(form,'points',0.01,100)
    reason=nonempty(form,'reason',3000)
    with connection() as db:
        why=require_admin_password(db,user,form)
        student=db.execute('SELECT * FROM students WHERE id=? AND deleted_at IS NULL',(student_id,)).fetchone()
        if not student:raise HTTPException(404,'Aluno não encontrado ou arquivado')
        cur=db.execute('''INSERT INTO behavior_deductions(student_id,event_date,points,reason,created_by)
                          VALUES(?,?,?,?,?)''',(student_id,dt,pts,reason,user['id']))
        row=db.execute('SELECT * FROM behavior_deductions WHERE id=?',(cur.lastrowid,)).fetchone()
        log_change(db,user,'lancar_desconto_individual','behavior_deductions',cur.lastrowid,None,row,
                   justificativa=why)
    return notify(request,'Registro de desconto individual cadastrado e auditado.',f'/alunos/{student_id}')


@app.post('/alunos/{student_id}/descontos/{deduction_id}/editar')
async def edit_behavior_deduction(request:Request,student_id:int,deduction_id:int,user=Depends(authenticated)):
    allowed(user,ADMIN)
    form=await request.form();check_post(request,form)
    dt=validate_incident_date(str(form.get('event_date','')))
    pts=numeric(form,'points',0.01,100)
    reason=nonempty(form,'reason',3000)
    with connection() as db:
        why=require_admin_password(db,user,form)
        old=db.execute('''SELECT * FROM behavior_deductions
                          WHERE id=? AND student_id=? AND deleted_at IS NULL''',
                       (deduction_id,student_id)).fetchone()
        if not old:raise HTTPException(404,'Desconto não encontrado')
        db.execute('UPDATE behavior_deductions SET event_date=?,points=?,reason=? WHERE id=?',
                   (dt,pts,reason,deduction_id))
        new=db.execute('SELECT * FROM behavior_deductions WHERE id=?',(deduction_id,)).fetchone()
        log_change(db,user,'editar_desconto_individual','behavior_deductions',deduction_id,old,new,
                   justificativa=why)
    return notify(request,'Desconto atualizado e auditado.',f'/alunos/{student_id}')


@app.post('/ocorrencias/anexos/{attachment_id}/substituir')
async def replace_attachment(request:Request,attachment_id:int,user=Depends(authenticated)):
    allowed(user,ADMIN)
    form=await request.form();check_post(request,form)
    with connection() as db:
        why=require_admin_password(db,user,form)
        old=db.execute('''SELECT * FROM occurrence_attachments WHERE id=? AND deleted_at IS NULL''',
                       (attachment_id,)).fetchone()
        if not old:raise HTTPException(404,'Anexo não encontrado')
        # Mantém o arquivo original em armazenamento privado para eventual revisão/auditoria.
        meta=await store_image(form.get('attachment'),'ocorrencias')
        db.execute('''UPDATE occurrence_attachments SET storage_path=?,filename=?,mime_type=?,size_bytes=?,sha256=?
                      WHERE id=?''',(meta['storage_path'],meta['filename'],meta['mime_type'],
                                   meta['size_bytes'],meta['sha256'],attachment_id))
        new=db.execute('SELECT * FROM occurrence_attachments WHERE id=?',(attachment_id,)).fetchone()
        log_change(db,user,'substituir_anexo','occurrence_attachments',attachment_id,old,new,
                   justificativa=why)
    return notify(request,'Anexo substituído com auditoria; original preservado para revisão.',
                  f'/ocorrencias/{old["occurrence_id"]}')


@app.post('/usuarios/{uid}/editar')
async def edit_user_account(request:Request,uid:int,user=Depends(authenticated)):
    allowed(user,ADMIN)
    form=await request.form();check_post(request,form)
    full_name=nonempty(form,'full_name',160)
    role=str(form.get('role','')).strip()
    if role not in ROLES:raise HTTPException(400,'Perfil inexistente')
    if uid==user['id'] and role!='admin':
        raise HTTPException(400,'Não é permitido retirar o próprio perfil de administrador')
    with connection() as db:
        why=require_admin_password(db,user,form)
        old=db.execute('SELECT id,username,full_name,role,active FROM users WHERE id=?',(uid,)).fetchone()
        if not old:raise HTTPException(404,'Conta não encontrada')
        db.execute('UPDATE users SET full_name=?,role=? WHERE id=?',(full_name,role,uid))
        new=db.execute('SELECT id,username,full_name,role,active FROM users WHERE id=?',(uid,)).fetchone()
        log_change(db,user,'editar_usuario','users',uid,old,new,justificativa=why)
    return notify(request,'Conta atualizada com auditoria.','/usuarios')

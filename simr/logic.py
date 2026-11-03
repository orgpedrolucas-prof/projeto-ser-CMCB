"""Cálculos oficiais do Projeto Ensino Militar de Resultados.

Métricas com valor ausente permanecem None. Não inventamos zero para faltas
ou indicadores não validados. Dados classificados apenas após validação.
"""
from statistics import mean

FIELDS = [('behavior',25,'Comportamento e respeito'),('order_drill',25,'Ordem Unida'),
          ('punctuality',15,'Pontualidade e organização'),('rules',15,'Normas e apresentação'),
          ('conservation',10,'Conservação'),('cooperation',10,'Cooperação')]


def academic(db, class_id, year, trimester):
    cl = db.execute('SELECT * FROM classes WHERE id=?', (class_id,)).fetchone()
    if cl is None or cl['deleted_at'] is not None:
        return None
    assessment = db.execute("SELECT * FROM assessments WHERE school_year=? AND grade=? AND trimester=? AND status='validado' AND deleted_at IS NULL",
                            (year, cl['grade'], trimester)).fetchone()
    enrolled = db.execute("SELECT count(*) n FROM enrollments e JOIN students s ON s.id=e.student_id WHERE e.class_id=? AND e.deleted_at IS NULL AND e.status='ativa' AND s.active=1 AND s.deleted_at IS NULL", (class_id,)).fetchone()['n']
    if not assessment:
        return {'assessment': None,'enrolled':enrolled,'participants':0,'justified':0,'unjustified':0,'p':None,'m':None,'g':None,'a':None}
    rows = db.execute('SELECT r.* FROM assessment_results r JOIN students s ON s.id=r.student_id WHERE r.assessment_id=? AND r.class_id=? AND r.deleted_at IS NULL AND s.deleted_at IS NULL', (assessment['id'],class_id)).fetchall()
    present = [r for r in rows if r['attendance']=='presente' and all(r[k] is not None for k in ['correct_p','correct_m','correct_g'])]
    justified = sum(r['attendance']=='justificada' for r in rows)
    unjustified = sum(r['attendance']=='nao_justificada' for r in rows)
    if not present:
        return {'assessment':assessment,'enrolled':enrolled,'participants':0,'justified':justified,'unjustified':unjustified,'p':None,'m':None,'g':None,'a':None}
    p = mean(r['correct_p'] * 4 for r in present)
    m = mean(r['correct_m'] * 4 for r in present)
    g = mean(r['correct_g'] * 4 for r in present)
    return {'assessment':assessment,'enrolled':enrolled,'participants':len(present), 'justified':justified,'unjustified':unjustified,
            'p':p,'m':m,'g':g,'a':(p+m+g)/3}


def class_metrics(db, cl, trimester):
    year = cl['school_year']
    acad = academic(db,cl['id'],year,trimester)
    inspections = db.execute("SELECT * FROM inspections WHERE class_id=? AND school_year=? AND trimester=? AND status='validado' AND deleted_at IS NULL",
                             (cl['id'],year,trimester)).fetchall()
    d = mean(sum(row[f] for f, _, _ in FIELDS) for row in inspections) if inspections else None
    e_row = db.execute("SELECT e.score FROM evolution e JOIN rubrics r ON r.id=e.rubric_id WHERE e.class_id=? AND e.school_year=? AND e.trimester=? AND e.status='validado' AND e.deleted_at IS NULL AND r.status='aprovada' AND r.deleted_at IS NULL AND r.deleted_at IS NULL",
                       (cl['id'],year,trimester)).fetchone()
    ev = float(e_row['score']) if e_row else None
    a = acad['a']
    result = .6*a+.3*d+.1*ev if all(x is not None for x in (a,d,ev)) else None
    old_a = academic(db, cl['id'],year,trimester-1)['a'] if trimester > 1 else None
    evolution = a-old_a if a is not None and old_a is not None else None
    return dict(cl)|dict(A=a,D=d,E=ev,R=result,P=acad['p'],M=acad['m'],G=acad['g'],
                         evolution=evolution, enrolled=acad['enrolled'], participants=acad['participants'],
                         justified=acad['justified'],unjustified=acad['unjustified'],
                         inspection_count=len(inspections), assessment_ready=bool(acad['assessment']),
                         participation=(100*acad['participants']/acad['enrolled']) if acad['enrolled'] else None)


def rankings(db,year,trimester,grade=None):
    classes=db.execute('SELECT * FROM classes WHERE school_year=? AND deleted_at IS NULL '+('AND grade=? ' if grade else '')+'ORDER BY grade,name',
                       ((year,grade) if grade else (year,))).fetchall()
    data=[class_metrics(db,cl,trimester) for cl in classes]
    data=[r for r in data if r['R'] is not None]
    # Desempates: A, M, P, D, evolução (nula quando não há período anterior).
    def criteria(r):
        return (r['R'], r['A'],r['M'],r['P'],r['D'],r['evolution'] if r['evolution'] is not None else float('-inf'))
    data.sort(key=lambda r: tuple(-v for v in criteria(r)))
    last=None; rank=0
    for pos, row in enumerate(data,1):
        c=criteria(row)
        if last is None or any(abs(v-w)>1e-8 for v,w in zip(c,last) if v != float('-inf') and w != float('-inf')) or any((v==float('-inf')) != (w==float('-inf')) for v,w in zip(c,last)):
            rank=pos
        row['rank']=rank
        last=c
    return data


def student_academic(db,student_id,year,trimester):
    row = db.execute("""SELECT r.*, a.status FROM assessment_results r JOIN assessments a ON a.id=r.assessment_id
       WHERE r.student_id=? AND a.school_year=? AND a.trimester=? AND a.status='validado' AND a.deleted_at IS NULL AND r.deleted_at IS NULL AND r.attendance='presente'
       ORDER BY a.id DESC LIMIT 1""",(student_id,year,trimester)).fetchone()
    if not row or any(row[k] is None for k in ('correct_p','correct_m','correct_g')):
        return None
    sub=[row[k]*4 for k in ('correct_p','correct_m','correct_g')]
    return {'p':sub[0], 'm':sub[1],'g':sub[2], 's':mean(sub)}


def elite_score(db, row):
    acad = student_academic(db,row['student_id'],row['school_year'],row['trimester'])
    approved = db.execute("SELECT value FROM settings WHERE key='elite_criteria_approved'").fetchone()
    if not acad:
        return {'score':None,'eligible':None,'reason':'Simulado validado ainda não disponível', 'academic':None}
    score = .4*acad['s'] + .25*row['conduct'] + .2*row['drill'] + .15*row['frequency']
    if not approved or approved['value']!='1':
        eligible=None; reason='Critérios de elegibilidade ainda não aprovados'
    elif score<70:
        eligible=False; reason='Pontuação inferior a 70'
    elif min(acad['p'],acad['m'],acad['g'])<60:
        eligible=False; reason='Disciplina com nota inferior a 60'
    elif row['blocked']:
        eligible=False; reason='Impedimento disciplinar registrado'
    elif not row['training']:
        eligible=False; reason='Treinamento obrigatório pendente'
    else:
        eligible=True; reason='Requisitos quantitativos atendidos; requer decisão humana'
    return dict(score=score,eligible=eligible,reason=reason,academic=acad)


def skill_metrics(db, assessment_id):
    """Calcula acertos por habilidade com base no gabarito e nas respostas completas."""
    import json
    a=db.execute("SELECT * FROM assessments WHERE id=? AND status='validado' AND deleted_at IS NULL",(assessment_id,)).fetchone()
    if not a or not all(a[k] for k in ('key_p','key_m','key_g')):
        return []
    results=db.execute("SELECT * FROM assessment_results WHERE assessment_id=? AND attendance='presente' AND deleted_at IS NULL",(assessment_id,)).fetchall()
    output={}
    for discipline,code in [('Português','p'),('Matemática','m'),('Conhecimentos Gerais','g')]:
        key=a[f'key_{code}']
        skills=json.loads(a[f'skills_{code}'] or '[]')
        for r in results:
            ans=r[f'answers_{code}']
            if not ans or len(ans)!=25: continue
            for i,(correct,got) in enumerate(zip(key,ans)):
                label=skills[i] if i<len(skills) and skills[i] else f'Questão {i+1}'
                name=(discipline,label)
                if name not in output: output[name]=[0,0]
                output[name][1]+=1
                output[name][0]+= int(correct==got)
    return [{'discipline':d,'skill':s,'correct':v[0],'attempted':v[1],'percentage':100*v[0]/v[1]} for (d,s),v in output.items()]

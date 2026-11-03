"""Indicadores administrativos de registros: não avaliam temperamento/saúde."""
from collections import defaultdict


def behavior_summary(db, student_id):
    occurrences=db.execute("""SELECT incident_date date, id FROM occurrences
      WHERE student_id=? AND status='validado' AND deleted_at IS NULL ORDER BY incident_date,id""",
      (student_id,)).fetchall()
    deductions=db.execute("""SELECT event_date date, id, points FROM behavior_deductions
      WHERE student_id=? AND deleted_at IS NULL ORDER BY event_date,id""",(student_id,)).fetchall()
    per_day=defaultdict(lambda: {'occurrences':0,'points':0.0})
    for row in occurrences: per_day[row['date']]['occurrences'] += 1
    for row in deductions: per_day[row['date']]['points'] += row['points']
    count=0; points=0.0; timeline=[]
    for day,v in sorted(per_day.items()):
        count += v['occurrences']; points += v['points']
        timeline.append({'date':day,'occurrences':count,'points':round(points,2),
                         'new_occurrences':v['occurrences'],'new_points':round(v['points'],2)})
    # Sem diagnósticos ou rotulação de personalidade.
    label='Estável no indicador (sem registros)' if not count and not points else 'Registros para acompanhamento'
    return dict(occurrences=count,points=round(points,2),balance=round(max(0,100-points),2),label=label,timeline=timeline)


def behavior_chart(summary):
    """Linhas separadas em escalas independentes; pontos não formam uma nota psicológica."""
    timeline=summary['timeline']
    if not timeline: return None
    width=650; height=225; margin=38; n=len(timeline)
    max_occ=max(1,summary['occurrences']); max_pts=max(1,summary['points'])
    def points(key,maximum):
        # Inclui a linha de base no início, para visualizar crescimento inclusive com 1 evento.
        result=[f'{margin:.1f},{height-28:.1f}']
        for i,row in enumerate(timeline,1):
            x=margin+i*(width-2*margin)/n
            y=height-28-(row[key]/maximum)*(height-54)
            result.append(f'{x:.1f},{y:.1f}')
        return ' '.join(result)
    return {'occurrences_path':points('occurrences',max_occ), 'points_path':points('points',max_pts),
            'start':timeline[0]['date'], 'end':timeline[-1]['date'], 'count':n}

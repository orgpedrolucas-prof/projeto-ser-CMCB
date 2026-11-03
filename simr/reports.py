"""Exportações oficiais: PDF e Excel, com dados reais, sem inferir pontuações pendentes."""
from io import BytesIO
from xml.sax.saxutils import escape
from reportlab.platypus import SimpleDocTemplate, Paragraph, Table, TableStyle, Spacer, KeepTogether
from reportlab.lib import colors
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.enums import TA_CENTER
from reportlab.lib.pagesizes import A4, landscape
from reportlab.graphics.shapes import Drawing, Rect, String
from openpyxl import Workbook
from openpyxl.styles import Font, PatternFill, Alignment
from openpyxl.utils import get_column_letter

NAVY=colors.HexColor('#183652')
GOLD=colors.HexColor('#b28c45')

def fnum(n):
    return f'{n:.1f}' if n is not None else 'Pendente'

def spreadsheet_safe(value):
    # Avoid CSV/Excel formula injection through class names and notes.
    if isinstance(value,str) and value.lstrip().startswith(('=','+','-','@')):
        return "'" + value
    return value

def safe_row(row):
    return [spreadsheet_safe(v) for v in row]

def report_table(data,widths=None,header=True):
    tab=Table(data,colWidths=widths,repeatRows=1 if header else 0,hAlign='LEFT')
    style=[('VALIGN',(0,0),(-1,-1),'MIDDLE'),('BOTTOMPADDING',(0,0),(-1,-1),8),
           ('TOPPADDING',(0,0),(-1,-1),8),('LINEBELOW',(0,-1),(-1,-1),.4,colors.HexColor('#d0d8df')),
           ('ROWBACKGROUNDS',(0,1),(-1,-1),[colors.white,colors.HexColor('#f1f5f9')]),
           ('FONTSIZE',(0,0),(-1,-1),8)]
    if header: style.extend([('BACKGROUND',(0,0),(-1,0),NAVY),('TEXTCOLOR',(0,0),(-1,0),colors.white),('FONTNAME',(0,0),(-1,0),'Helvetica-Bold')])
    tab.setStyle(TableStyle(style));return tab

def bar_chart(metrics):
    valid=[(row['name'],row['R']) for row in metrics if row['R'] is not None][:12]
    if not valid: return None
    height=len(valid)*22+35
    chart=Drawing(750,height)
    for i,(name,value) in enumerate(valid):
        y=height-34-i*22
        chart.add(String(5,y,escape(name[:17]),fontSize=9,fillColor=NAVY))
        chart.add(Rect(130,y-3,round(320*value/100,1),11,fillColor=GOLD,strokeColor=None))
        chart.add(String(460,y,f'{value:.1f}',fontSize=9,fillColor=NAVY))
    return chart

def build_pdf(year,trimester,metrics,ranking,plans):
    output=BytesIO()
    doc=SimpleDocTemplate(output,pagesize=landscape(A4),rightMargin=35,leftMargin=35,topMargin=38,bottomMargin=40,
                          title=f'SER - Relatório {year} T{trimester}')
    styles=getSampleStyleSheet()
    styles.add(ParagraphStyle(name='MainTitleSER',parent=styles['Title'],fontSize=18,textColor=NAVY,spaceAfter=9))
    styles.add(ParagraphStyle(name='SmallSER',parent=styles['Normal'],fontSize=9,leading=12))
    story=[Paragraph('SER | SISTEMA ESCOLAR DE RESULTADOS',styles['MainTitleSER']),
           Paragraph(f'Relatório trimestral de indicadores • Ano letivo {year} • {trimester}º trimestre',styles['Heading2']),
           Paragraph('Conhecimento • Disciplina • Mérito • Excelência',styles['SmallSER']),Spacer(1,14)]
    story.append(Paragraph('Consolidação por turma',styles['Heading2']))
    data=[['Turma','Série','Participação','Português','Matemática','Gerais','Acadêmico (A)','Disciplina (D)','Evolução (E)','Resultado (R)']]
    for x in metrics:
        data.append([x['name'][:22],f"{x['grade']}º",f"{x['participants']}/{x['enrolled']}",
                     fnum(x['P']),fnum(x['M']),fnum(x['G']),fnum(x['A']),fnum(x['D']),fnum(x['E']),fnum(x['R'])])
    if len(data)==1:data.append(['Nenhum registro']+['']*9)
    story.append(report_table(data,[125,38,73,70,70,62,85,85,75,85]))
    story.append(Spacer(1,18))
    story.append(Paragraph('Resultados consolidados (somente indicadores validados)',styles['Heading2']))
    chart=bar_chart(metrics)
    if chart:
        chart.scale(.8,.8)
        chart.width*=.8;chart.height*=.8
        story.append(chart)
    else:story.append(Paragraph('Não há turmas com os três índices validados neste período.',styles['SmallSER']))
    story.append(Spacer(1,15))
    story.append(Paragraph('Ranking geral de turmas',styles['Heading2']))
    rankdata=[['Posição','Turma','Série','Resultado','Índice acadêmico','Índice disciplinar']]
    rankdata.extend([[f"{x['rank']}º",x['name'],f"{x['grade']}º",fnum(x['R']),fnum(x['A']),fnum(x['D'])] for x in ranking])
    if len(rankdata)==1:rankdata.append(['Sem classificações consolidadas']+['']*5)
    story.append(report_table(rankdata,[85,180,55,80,110,110]))
    story.append(Spacer(1,18))
    story.append(Paragraph('Planos de intervenção cadastrados',styles['Heading2']))
    for p in plans:
        vals=(f"{p['class_name']}: {p['difficulty']} | Meta: {p['target']} | Ação: {p['action']} | "
              f"Responsável: {p['owner']} | Prazo: {p['deadline']} | Status: {p['status']}")
        story.append(Paragraph(escape(vals[:1500]),styles['SmallSER']))
        story.append(Spacer(1,6))
    if not plans:story.append(Paragraph('Nenhum plano de intervenção cadastrado para o período.',styles['SmallSER']))
    story.append(Spacer(1,18))
    story.append(Paragraph('Metodologia: A = (P + M + G) / 3. R = A × 0,60 + D × 0,30 + E × 0,10. '
                           'Indicadores não validados permanecem pendentes. '
                           'Notas acadêmicas e disciplinares deste projeto não substituem notas escolares oficiais.',styles['SmallSER']))
    story.append(Spacer(1,22))
    story.append(Paragraph('Gestão Militar: ____________________________________    Gestão Pedagógica: ____________________________________',styles['SmallSER']))
    def footer(canvas,doc):
        canvas.setFont('Helvetica',8);canvas.setFillColor(NAVY)
        canvas.drawString(35,20,f'SER • {year} • {trimester}º trimestre • Uso institucional restrito')
        canvas.drawRightString(landscape(A4)[0]-35,20,f'Página {doc.page}')
    doc.build(story,onFirstPage=footer,onLaterPages=footer)
    return output.getvalue()


def build_xlsx(year,trimester,metrics,ranking,plans):
    wb=Workbook();ws=wb.active;ws.title='Indicadores'
    ws.append([f'SER - {year} - {trimester}º trimestre'])
    ws.append(['Turma','Série','Matriculados','Participantes','Justificados','Português','Matemática','Gerais',
               'Acadêmico A','Disciplinar D','Evolução E','Resultado R','Situação'])
    for x in metrics:
        ws.append(safe_row([x['name'],x['grade'],x['enrolled'],x['participants'],x['justified'],
                   x['P'],x['M'],x['G'],x['A'],x['D'],x['E'],x['R'],
                   'Completo' if x['R'] is not None else 'Pendente']))
    ranking_ws=wb.create_sheet('Ranking Geral')
    ranking_ws.append(['Posição','Turma','Série','Resultado','Acadêmico','Matemática','Português','Disciplinar','Evolução acadêmica'])
    for x in ranking:
        ranking_ws.append(safe_row([x['rank'],x['name'],x['grade'],x['R'],x['A'],x['M'],x['P'],x['D'],x['evolution']]))
    plan_ws=wb.create_sheet('Planos de Melhoria')
    plan_ws.append(['Turma','Principal dificuldade','Meta','Ação','Responsável','Prazo','Indicador','Situação','Resultado'])
    for p in plans:
        plan_ws.append(safe_row([p['class_name'],p['difficulty'],p['target'],p['action'],p['owner'],p['deadline'],p['metric'],p['status'],p['result']]))
    for sheet in wb:
        sheet.freeze_panes='B3' if sheet.title=='Indicadores' else 'B2'
        headrow=2 if sheet.title=='Indicadores' else 1
        for cell in sheet[headrow]:
            cell.fill=PatternFill('solid',fgColor='183652');cell.font=Font(bold=True,color='FFFFFF')
            cell.alignment=Alignment(wrap_text=True)
        sheet.auto_filter.ref=f'A{headrow}:{get_column_letter(sheet.max_column)}{sheet.max_row}'
        for column in sheet.columns:
            letter=get_column_letter(column[0].column)
            maxlength=max(len(str(c.value or '')) for c in column)
            sheet.column_dimensions[letter].width=min(max(12,maxlength+3),48)
    output=BytesIO();wb.save(output);return output.getvalue()


def build_progress_pdf(year, start, end, rows, plans):
    """Relatório comparativo entre períodos informados, sem presumir mapeamento de semestres."""
    output=BytesIO()
    doc=SimpleDocTemplate(output,pagesize=landscape(A4),rightMargin=35,leftMargin=35,topMargin=38,bottomMargin=40,
                          title=f'SER - Evolução {year} T{start}-T{end}')
    styles=getSampleStyleSheet()
    styles.add(ParagraphStyle(name='ProgressTitle',parent=styles['Title'],fontSize=18,textColor=NAVY,spaceAfter=10))
    story=[Paragraph('SER | RELATÓRIO COMPARATIVO DE EVOLUÇÃO',styles['ProgressTitle']),
           Paragraph(f'Ano letivo {year} • Trimestres {start} a {end} (intervalo escolhido pela gestão)',styles['Heading2']),
           Paragraph('Os indicadores ausentes permanecem pendentes. A variação é expressa em pontos na escala 0–100.',styles['Normal']),Spacer(1,15)]
    hdr=['Turma','Série']+[f'A T{t}' for t in range(start,end+1)]+['Δ acadêmico','D médio','E médio','Trimestres completos']
    data=[hdr]
    for row in rows:
        line=[row['name'],f"{row['grade']}º"]
        line.extend([fnum(x['A']) for x in row['trimesters']])
        line += [fnum(row['delta']),fnum(row['d_avg']),fnum(row['e_avg']),str(row['complete'])+f'/{end-start+1}']
        data.append(line)
    if len(data)==1:data.append(['Sem turmas']+['']*(len(hdr)-1))
    col_width=[136,45]+[72]*(end-start+1)+[93,81,81,93]
    story.append(report_table(data,col_width))
    story.append(Spacer(1,22))
    story.append(Paragraph('Intervenções registradas nos períodos selecionados',styles['Heading2']))
    if not plans:story.append(Paragraph('Nenhuma intervenção cadastrada.',styles['Normal']))
    else:
        for p in plans:
            content=f"Turma {p['class_name']} (T{p['trimester']}): {p['difficulty']} | Meta: {p['target']} | Ação: {p['action']} | Status: {p['status']} | Resultado: {p['result'] or 'Pendente'}"
            story.append(Paragraph(escape(content[:1500]),styles['Normal']));story.append(Spacer(1,8))
    story.append(Spacer(1,18))
    story.append(Paragraph('Nota metodológica: este relatório é comparativo e não cria ranking semestral oficial; '
         'qualquer consolidação semestral e mapeamento bimestral/trimestral requer aprovação institucional. '
         'Linha de base: primeiro índice acadêmico disponível no intervalo informado.',styles['Normal']))
    def footer(canvas,doc):
        canvas.setFont('Helvetica',8);canvas.drawString(35,20,f'SER — Relatório comparativo {year} — uso institucional')
        canvas.drawRightString(landscape(A4)[0]-35,20,f'Página {doc.page}')
    doc.build(story,onFirstPage=footer,onLaterPages=footer)
    return output.getvalue()


def build_progress_xlsx(year,start,end,rows,plans):
    wb=Workbook();ws=wb.active;ws.title='Evolução Acadêmica'
    header=['Turma','Série']+[f'A T{t}' for t in range(start,end+1)]+['Variação em pontos','D médio','E médio','Períodos completos']
    ws.append(header)
    for row in rows:
        ws.append(safe_row([row['name'],row['grade']]+[x['A'] for x in row['trimesters']]+[row['delta'],row['d_avg'],row['e_avg'],row['complete']]))
    tab=wb.create_sheet('Série Histórica')
    tab.append(['Turma','Série','Trimestre','Acadêmico','Disciplinar','Evolução','Resultado','Participantes','Matriculados'])
    for r in rows:
        for x,t in zip(r['trimesters'],range(start,end+1)):
            tab.append(safe_row([r['name'],r['grade'],t,x['A'],x['D'],x['E'],x['R'],x['participants'],x['enrolled']]))
    ps=wb.create_sheet('Intervenções')
    ps.append(['Turma','Trimestre','Dificuldade','Meta','Ação','Responsável','Prazo','Status','Resultado'])
    for p in plans:
        ps.append(safe_row([p['class_name'],p['trimester'],p['difficulty'],p['target'],p['action'],p['owner'],p['deadline'],p['status'],p['result']]))
    for sheet in wb:
        sheet.freeze_panes='B2'
        for cell in sheet[1]:
            cell.fill=PatternFill('solid',fgColor='183652');cell.font=Font(bold=True,color='FFFFFF')
        sheet.auto_filter.ref=f'A1:{get_column_letter(sheet.max_column)}{sheet.max_row}'
        for column in sheet.columns:
            sheet.column_dimensions[get_column_letter(column[0].column)].width=min(max(12,max(len(str(c.value or '')) for c in column)+3),50)
    out=BytesIO();wb.save(out);return out.getvalue()

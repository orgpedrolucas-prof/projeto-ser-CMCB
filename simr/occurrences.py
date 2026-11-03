"""Imagens privadas e boletim de ocorrência escolar. Sem exposição em arquivos estáticos."""
from __future__ import annotations
import hashlib
import io
import secrets
from pathlib import Path
from datetime import date
from xml.sax.saxutils import escape

from fastapi import HTTPException
from PIL import Image, ImageOps, UnidentifiedImageError
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.enums import TA_CENTER
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle, Image as PdfImage, PageBreak, KeepTogether

from .db import INSTANCE

MEDIA_ROOT = INSTANCE / 'midias_privadas'
MAX_IMAGE_BYTES = 5 * 1024 * 1024
Image.MAX_IMAGE_PIXELS = 15_000_000
INFRACTIONS = {'leve': 'Leve', 'media': 'Média', 'grave': 'Grave', 'eliminatoria': 'Eliminatória'}
ACTIONS = {'advertencia':'Advertência', 'suspensao':'Suspensão'}


def validate_incident_date(value: str) -> str:
    try:
        raw = value.strip()
        d = date.fromisoformat(raw)
        if d.isoformat()!=raw or not 2000 <= d.year <= 2100:
            raise ValueError()
        return raw
    except (ValueError, AttributeError):
        raise HTTPException(400, 'Data da ocorrência inválida (AAAA-MM-DD).')


def infraction_text(value: str) -> str:
    return INFRACTIONS.get(value, value)


async def store_image(upload, folder: str):
    """Aceita JPG/PNG reais, reencoda para JPG sem metadados e grava fora de /static.

    Retorna metadados a serem commitados somente depois que toda validação for concluída.
    """
    if folder not in ('alunos','ocorrencias'):
        raise ValueError('Pasta desconhecida')
    if not upload or not getattr(upload, 'filename', None):
        raise HTTPException(400, 'Selecione uma foto JPG ou PNG.')
    raw = await upload.read(MAX_IMAGE_BYTES+1)
    if not raw or len(raw)>MAX_IMAGE_BYTES:
        raise HTTPException(400, 'A imagem deve ter no máximo 5 MB.')
    try:
        src=Image.open(io.BytesIO(raw))
        if src.format not in ('JPEG','PNG'):
            raise ValueError('Tipo de imagem não permitido')
        if src.width * src.height > 15_000_000:
            raise ValueError('Resolução de imagem acima do limite permitido')
        src.load()
        src=ImageOps.exif_transpose(src)
        src.thumbnail((1400,1400), Image.Resampling.LANCZOS)
        if src.mode=='RGBA':
            background=Image.new('RGB',src.size,'white')
            background.paste(src,mask=src.getchannel('A'))
            src=background
        else:
            src=src.convert('RGB')
        out=io.BytesIO()
        src.save(out,format='JPEG',quality=87,optimize=True)
        normalized=out.getvalue()
    except (ValueError, OSError, UnidentifiedImageError, Image.DecompressionBombError):
        raise HTTPException(400,'Imagem inválida. Envie uma foto JPG ou PNG válida.')
    subdir=MEDIA_ROOT/folder
    subdir.mkdir(parents=True,exist_ok=True)
    storage_path=f'{folder}/{secrets.token_hex(20)}.jpg'
    path=MEDIA_ROOT/storage_path
    with path.open('xb') as f:
        f.write(normalized)
    path.chmod(0o600)
    return {
        'storage_path':storage_path,
        'filename':Path(upload.filename).name[:140],
        'mime_type':'image/jpeg',
        'size_bytes':len(normalized),
        'sha256':hashlib.sha256(normalized).hexdigest()
    }


def get_stored_image(storage_path: str) -> Path:
    """Caminho somente dentro da pasta de mídia privada."""
    root=MEDIA_ROOT.resolve()
    target=(root / storage_path).resolve()
    if not target.is_relative_to(root) or not target.is_file() or target.suffix!='.jpg':
        raise HTTPException(404, 'Imagem não encontrada.')
    return target


def occurrence_pdf(occurrence, student, actions, attachments):
    """Gera boletim administrativo com assinaturas e anexos fotográficos."""
    result=io.BytesIO()
    doc=SimpleDocTemplate(result,pagesize=A4,rightMargin=46,leftMargin=46,topMargin=40,bottomMargin=47,
                          title=f'Boletim de ocorrência escolar {occurrence["id"]}')
    styles=getSampleStyleSheet()
    navy=colors.HexColor('#173653')
    styles.add(ParagraphStyle(name='BoTitle',parent=styles['Heading1'],fontSize=15,leading=20,alignment=TA_CENTER,textColor=navy,spaceAfter=10))
    styles.add(ParagraphStyle(name='BoSection',parent=styles['Heading2'],fontSize=11,leading=15,textColor=navy,spaceBefore=14,spaceAfter=7))
    styles.add(ParagraphStyle(name='BoBody',parent=styles['Normal'],fontSize=10.5,leading=15,spaceAfter=7))
    styles.add(ParagraphStyle(name='BoSmall',parent=styles['Normal'],fontSize=8.5,leading=12))
    styles.add(ParagraphStyle(name='BoSignature',parent=styles['Normal'],fontSize=9,leading=13,alignment=TA_CENTER))
    def paragraph(txt, style='BoBody'):
        txt=escape(str(txt)).replace('\n','<br/>')
        return Paragraph(txt,styles[style])
    story=[Paragraph('CORPO DE BOMBEIROS MILITAR | COLÉGIO MILITAR',styles['BoTitle']),
           Paragraph('CMCB UNIDADE LXI - BANDEIRA TRIBUZZI',styles['BoTitle']),
           paragraph('BOLETIM DE OCORRÊNCIA ESCOLAR — REGISTRO ADMINISTRATIVO','BoSection')]
    identification=[
        [paragraph('Nº do boletim','BoSmall'),paragraph(f'{occurrence["id"]:06d}','BoSmall'),paragraph('Situação','BoSmall'),paragraph('VALIDADO' if occurrence['status']=='validado' else 'RASCUNHO','BoSmall')],
        [paragraph('Aluno(a)','BoSmall'),paragraph(student['name'],'BoSmall'),paragraph('Matrícula','BoSmall'),paragraph(student['registration'],'BoSmall')],
        [paragraph('Data do fato','BoSmall'),paragraph(occurrence['incident_date'],'BoSmall'),paragraph('Infração','BoSmall'),paragraph(infraction_text(occurrence['infraction_type']),'BoSmall')],
    ]
    tab=Table(identification,colWidths=[76,195,65,131],hAlign='LEFT')
    tab.setStyle(TableStyle([('VALIGN',(0,0),(-1,-1),'TOP'),('ROWBACKGROUNDS',(0,0),(-1,-1),[colors.HexColor('#eff3f7'),colors.white]),
                             ('LINEBELOW',(0,0),(-1,-1),.35,colors.HexColor('#d8e0e8')),('TOPPADDING',(0,0),(-1,-1),8),('BOTTOMPADDING',(0,0),(-1,-1),8)]))
    story += [tab,Paragraph('1. DESCRIÇÃO DA OCORRÊNCIA',styles['BoSection']),paragraph(occurrence['description'])]
    story.append(Paragraph('2. MEDIDAS / REGISTROS ASSOCIADOS',styles['BoSection']))
    if not actions:
        story.append(paragraph('Nenhuma advertência ou suspensão foi registrada neste boletim.'))
    for index, action in enumerate(actions,1):
        heading=f'{index}. {ACTIONS[action["action_type"]]}'
        if action['action_type']=='suspensao':
            heading+=f' — {action["suspension_days"]} dia(s)'
        story.append(paragraph(heading))
        story.append(paragraph(action['description']))
    story.append(Paragraph('3. ANEXOS E REGISTRO',styles['BoSection']))
    story.append(paragraph(f'Anexos fotográficos associados: {len(attachments)}. As fotografias constam nas páginas seguintes, quando existentes.'))
    story.append(paragraph('O registro da ocorrência e de suas medidas não substitui análise institucional nem significa penalidade automática.'))
    signature_block=[Spacer(1,12),Paragraph('4. CIÊNCIA E ASSINATURAS',styles['BoSection']),
        paragraph('Os espaços abaixo destinam-se à ciência do registro, sem presumir concordância com o seu conteúdo.')]
    signatures=[('Aluno(a)',),('Responsável legal',),('Subcomandante do CMCB UNI LXI BANDEIRA TRIBUZZI',)]
    for (label,) in signatures:
        signature_block += [Spacer(1,20),Paragraph('_____________________________________________________________',styles['BoSignature']),
                  Paragraph(escape(label),styles['BoSignature']),Spacer(1,3)]
    signature_block += [Spacer(1,5),paragraph('Local e data da ciência: ________________________________________','BoSmall'),
              paragraph('Observações sobre ciência/recusa de assinatura: __________________________________________','BoSmall')]
    story.append(KeepTogether(signature_block))
    if attachments:
        story.append(PageBreak())
        story.append(Paragraph('ANEXOS FOTOGRÁFICOS - ACESSO RESTRITO',styles['BoTitle']))
        for index,attachment in enumerate(attachments,1):
            action_type=next((ACTIONS[a['action_type']] for a in actions if a['id']==attachment['action_id']),'Registro')
            story.append(Paragraph(f'Anexo {index} — {escape(action_type)}',styles['BoSection']))
            try:
                img_path=get_stored_image(attachment['storage_path'])
                with Image.open(img_path) as pic:
                    w,h=pic.size
                ratio=min(450/w,380/h,1)
                story.append(PdfImage(str(img_path),width=w*ratio,height=h*ratio))
            except (HTTPException,OSError):
                story.append(paragraph('Anexo não disponível no armazenamento local.'))
            story.append(Spacer(1,9))
    def footer(canvas, document):
        canvas.setFont('Helvetica',8)
        canvas.setFillColor(navy)
        canvas.drawString(46,23,'SER • CMCB UNI LXI BANDEIRA TRIBUZZI • DOCUMENTO RESTRITO')
        canvas.drawRightString(A4[0]-46,23,f'Página {document.page}')
    doc.build(story,onFirstPage=footer,onLaterPages=footer)
    return result.getvalue()

"""Persistência SQLite, criação de tabelas e gestão de credenciais."""
import os
import sqlite3
import json
import secrets
import hashlib
import hmac
from contextlib import contextmanager
from pathlib import Path
from datetime import datetime, timezone

BASE = Path(__file__).resolve().parent.parent
INSTANCE = Path(os.getenv("SIMR_INSTANCE", str(BASE / "instance")))
INSTANCE.mkdir(parents=True, exist_ok=True)
DB_PATH = Path(os.getenv("SIMR_DATABASE", str(INSTANCE / "simr.db")))

SCHEMA = """
PRAGMA foreign_keys=ON;
CREATE TABLE IF NOT EXISTS users (
 id INTEGER PRIMARY KEY, username TEXT UNIQUE NOT NULL, full_name TEXT NOT NULL,
 password_hash TEXT NOT NULL, role TEXT NOT NULL CHECK(role IN ('admin','militar','pedagogia','professor','monitor','consulta')),
 active INTEGER NOT NULL DEFAULT 1, created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS classes (
 id INTEGER PRIMARY KEY, name TEXT NOT NULL, grade INTEGER NOT NULL CHECK(grade BETWEEN 6 AND 9),
 school_year INTEGER NOT NULL, shift TEXT NOT NULL DEFAULT 'Matutino',
 UNIQUE (name, school_year)
);
CREATE TABLE IF NOT EXISTS students (
 id INTEGER PRIMARY KEY, registration TEXT UNIQUE NOT NULL, name TEXT NOT NULL,
 active INTEGER NOT NULL DEFAULT 1, created_at TEXT DEFAULT CURRENT_TIMESTAMP, photo_path TEXT
);
CREATE TABLE IF NOT EXISTS enrollments (
 id INTEGER PRIMARY KEY, student_id INTEGER NOT NULL REFERENCES students(id),
 class_id INTEGER NOT NULL REFERENCES classes(id), status TEXT NOT NULL DEFAULT 'ativa'
 CHECK (status IN ('ativa','transferida','desligada')), enrolled_at TEXT DEFAULT CURRENT_TIMESTAMP,
 UNIQUE(student_id, class_id)
);
CREATE TABLE IF NOT EXISTS assessments (
 id INTEGER PRIMARY KEY, title TEXT NOT NULL, school_year INTEGER NOT NULL,
 trimester INTEGER NOT NULL CHECK(trimester BETWEEN 1 AND 3),
 grade INTEGER NOT NULL CHECK(grade BETWEEN 6 AND 9),
 date TEXT, key_p TEXT, key_m TEXT, key_g TEXT, skills_p TEXT, skills_m TEXT, skills_g TEXT,
 status TEXT NOT NULL DEFAULT 'rascunho' CHECK (status IN ('rascunho','validado')),
 created_by INTEGER REFERENCES users(id), validated_by INTEGER REFERENCES users(id),
 validated_at TEXT, deleted_at TEXT, deleted_by INTEGER REFERENCES users(id), UNIQUE(school_year,trimester,grade)
);
CREATE TABLE IF NOT EXISTS assessment_results (
 id INTEGER PRIMARY KEY, assessment_id INTEGER NOT NULL REFERENCES assessments(id),
 student_id INTEGER NOT NULL REFERENCES students(id), class_id INTEGER NOT NULL REFERENCES classes(id),
 attendance TEXT NOT NULL CHECK(attendance IN ('presente','justificada','nao_justificada')),
 correct_p INTEGER CHECK(correct_p BETWEEN 0 AND 25),
 correct_m INTEGER CHECK(correct_m BETWEEN 0 AND 25),
 correct_g INTEGER CHECK(correct_g BETWEEN 0 AND 25),
 answers_p TEXT, answers_m TEXT, answers_g TEXT,
 second_attempt INTEGER NOT NULL DEFAULT 0,
 updated_at TEXT DEFAULT CURRENT_TIMESTAMP,
 UNIQUE(assessment_id, student_id)
);
CREATE TABLE IF NOT EXISTS inspections (
 id INTEGER PRIMARY KEY, class_id INTEGER NOT NULL REFERENCES classes(id),
 school_year INTEGER NOT NULL, bimester INTEGER NOT NULL CHECK(bimester BETWEEN 1 AND 4),
 trimester INTEGER NOT NULL CHECK(trimester BETWEEN 1 AND 3),
 date TEXT NOT NULL, behavior REAL NOT NULL CHECK(behavior BETWEEN 0 AND 25),
 order_drill REAL NOT NULL CHECK(order_drill BETWEEN 0 AND 25),
 punctuality REAL NOT NULL CHECK(punctuality BETWEEN 0 AND 15),
 rules REAL NOT NULL CHECK(rules BETWEEN 0 AND 15),
 conservation REAL NOT NULL CHECK(conservation BETWEEN 0 AND 10),
 cooperation REAL NOT NULL CHECK(cooperation BETWEEN 0 AND 10),
 notes TEXT NOT NULL DEFAULT '', evaluator INTEGER REFERENCES users(id),
 status TEXT NOT NULL DEFAULT 'rascunho' CHECK(status IN ('rascunho','validado')),
 validated_by INTEGER REFERENCES users(id), validated_at TEXT
);
CREATE TABLE IF NOT EXISTS rubrics (
 id INTEGER PRIMARY KEY, title TEXT NOT NULL, description TEXT NOT NULL,
 status TEXT NOT NULL DEFAULT 'rascunho' CHECK(status IN ('rascunho','aprovada')),
 approved_by INTEGER REFERENCES users(id), approved_at TEXT
);
CREATE TABLE IF NOT EXISTS evolution (
 id INTEGER PRIMARY KEY, class_id INTEGER NOT NULL REFERENCES classes(id),
 school_year INTEGER NOT NULL, trimester INTEGER NOT NULL CHECK(trimester BETWEEN 1 AND 3),
 score REAL NOT NULL CHECK(score BETWEEN 0 AND 100),
 evidence TEXT NOT NULL, rubric_id INTEGER NOT NULL REFERENCES rubrics(id),
 status TEXT NOT NULL DEFAULT 'rascunho' CHECK(status IN ('rascunho','validado')),
 created_by INTEGER REFERENCES users(id), validated_by INTEGER REFERENCES users(id),
 validated_at TEXT, UNIQUE(class_id,school_year,trimester)
);
CREATE TABLE IF NOT EXISTS plans (
 id INTEGER PRIMARY KEY, class_id INTEGER NOT NULL REFERENCES classes(id),
 school_year INTEGER NOT NULL, trimester INTEGER NOT NULL CHECK(trimester BETWEEN 1 AND 3),
 difficulty TEXT NOT NULL, target TEXT NOT NULL, action TEXT NOT NULL,
 owner TEXT NOT NULL, deadline TEXT NOT NULL, metric TEXT NOT NULL,
 status TEXT NOT NULL DEFAULT 'planejada' CHECK(status IN ('planejada','em_andamento','concluida')),
 result TEXT DEFAULT '', created_by INTEGER REFERENCES users(id)
);
CREATE TABLE IF NOT EXISTS elite_evaluations (
 id INTEGER PRIMARY KEY, student_id INTEGER NOT NULL REFERENCES students(id),
 school_year INTEGER NOT NULL, trimester INTEGER NOT NULL CHECK(trimester BETWEEN 1 AND 3),
 conduct REAL NOT NULL CHECK(conduct BETWEEN 0 AND 100),
 drill REAL NOT NULL CHECK(drill BETWEEN 0 AND 100),
 frequency REAL NOT NULL CHECK(frequency BETWEEN 0 AND 100),
 training INTEGER NOT NULL DEFAULT 0, blocked INTEGER NOT NULL DEFAULT 0,
 notes TEXT NOT NULL DEFAULT '', decision TEXT NOT NULL DEFAULT 'pendente'
 CHECK (decision IN ('pendente','aprovado','nao_aprovado')),
 reviewed_by INTEGER REFERENCES users(id),
 UNIQUE(student_id,school_year,trimester)
);
CREATE TABLE IF NOT EXISTS decisions (
 id INTEGER PRIMARY KEY, school_year INTEGER NOT NULL, semester INTEGER NOT NULL CHECK(semester BETWEEN 1 AND 2),
 date TEXT NOT NULL, participants TEXT NOT NULL DEFAULT '', agenda TEXT NOT NULL DEFAULT '',
 decisions TEXT NOT NULL DEFAULT '', owners TEXT NOT NULL DEFAULT '', deadlines TEXT NOT NULL DEFAULT '',
 created_by INTEGER REFERENCES users(id)
);
CREATE TABLE IF NOT EXISTS adjustments (
 id INTEGER PRIMARY KEY, class_id INTEGER NOT NULL REFERENCES classes(id),
 school_year INTEGER NOT NULL, semester INTEGER NOT NULL CHECK(semester BETWEEN 1 AND 2),
 delta REAL NOT NULL CHECK(delta BETWEEN -3 AND 3), justification TEXT NOT NULL,
 decision_id INTEGER NOT NULL REFERENCES decisions(id), approved_by INTEGER NOT NULL REFERENCES users(id),
 created_at TEXT DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS occurrences (
 id INTEGER PRIMARY KEY,
 student_id INTEGER NOT NULL REFERENCES students(id),
 incident_date TEXT NOT NULL,
 infraction_type TEXT NOT NULL CHECK(infraction_type IN ('leve','media','grave','eliminatoria')),
 description TEXT NOT NULL,
 status TEXT NOT NULL DEFAULT 'rascunho' CHECK(status IN ('rascunho','validado')),
 created_by INTEGER NOT NULL REFERENCES users(id),
 validated_by INTEGER REFERENCES users(id),
 validated_at TEXT,
 created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
 updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS occurrence_actions (
 id INTEGER PRIMARY KEY,
 occurrence_id INTEGER NOT NULL REFERENCES occurrences(id),
 action_type TEXT NOT NULL CHECK(action_type IN ('advertencia','suspensao')),
 description TEXT NOT NULL,
 suspension_days INTEGER CHECK(suspension_days IS NULL OR (suspension_days BETWEEN 1 AND 365)),
 created_by INTEGER NOT NULL REFERENCES users(id),
 created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
 CHECK ((action_type='advertencia' AND suspension_days IS NULL) OR
        (action_type='suspensao' AND suspension_days IS NOT NULL))
);
CREATE TABLE IF NOT EXISTS occurrence_attachments (
 id INTEGER PRIMARY KEY,
 occurrence_id INTEGER NOT NULL REFERENCES occurrences(id),
 action_id INTEGER NOT NULL REFERENCES occurrence_actions(id),
 storage_path TEXT NOT NULL,
 filename TEXT NOT NULL,
 mime_type TEXT NOT NULL CHECK(mime_type='image/jpeg'),
 size_bytes INTEGER NOT NULL,
 sha256 TEXT NOT NULL,
 created_by INTEGER NOT NULL REFERENCES users(id),
 created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_occurrences_student ON occurrences(student_id,incident_date);
CREATE INDEX IF NOT EXISTS idx_occurrence_actions ON occurrence_actions(occurrence_id);
CREATE INDEX IF NOT EXISTS idx_occurrence_attachments ON occurrence_attachments(occurrence_id);
CREATE TABLE IF NOT EXISTS behavior_deductions (
 id INTEGER PRIMARY KEY, student_id INTEGER NOT NULL REFERENCES students(id),
 event_date TEXT NOT NULL, points REAL NOT NULL CHECK(points>0 AND points<=100),
 reason TEXT NOT NULL, created_by INTEGER NOT NULL REFERENCES users(id),
 created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
 deleted_at TEXT, deleted_by INTEGER REFERENCES users(id)
);
CREATE INDEX IF NOT EXISTS idx_behavior_deductions_student ON behavior_deductions(student_id,event_date);
CREATE TABLE IF NOT EXISTS settings (
 key TEXT PRIMARY KEY, value TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS audit_log (
 id INTEGER PRIMARY KEY, user_id INTEGER REFERENCES users(id), action TEXT NOT NULL,
 object_type TEXT NOT NULL, object_id INTEGER, details TEXT NOT NULL DEFAULT '{}',
 created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_assessment_results_assessment ON assessment_results(assessment_id,class_id);
CREATE INDEX IF NOT EXISTS idx_inspections_period ON inspections(class_id,school_year,trimester,status);
CREATE INDEX IF NOT EXISTS idx_evolution_period ON evolution(class_id,school_year,trimester,status);
CREATE INDEX IF NOT EXISTS idx_enrollments_class ON enrollments(class_id,status);
"""

@contextmanager
def connection():
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(DB_PATH, timeout=15)
    db.row_factory = sqlite3.Row
    db.execute('PRAGMA foreign_keys=ON')
    db.execute('PRAGMA busy_timeout=10000')
    try:
        yield db
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()

def initialize():
    with connection() as db:
        db.executescript(SCHEMA)
        # Atualização não destrutiva para bases existentes, inclusive as da versão anterior.
        student_columns={row['name'] for row in db.execute('PRAGMA table_info(students)')}
        if 'photo_path' not in student_columns:
            db.execute('ALTER TABLE students ADD COLUMN photo_path TEXT')
        columns={row['name'] for row in db.execute('PRAGMA table_info(assessments)')}
        if 'deleted_at' not in columns:
            db.execute('ALTER TABLE assessments ADD COLUMN deleted_at TEXT')
        if 'deleted_by' not in columns:
            db.execute('ALTER TABLE assessments ADD COLUMN deleted_by INTEGER REFERENCES users(id)')
        # Arquivamento reversível: nada é apagado fisicamente, inclusive evidências.
        for table in ('classes','students','enrollments','assessments','assessment_results',
                      'inspections','rubrics','evolution','plans','elite_evaluations',
                      'decisions','adjustments','occurrences','occurrence_actions',
                      'occurrence_attachments','behavior_deductions'):
            existing={row['name'] for row in db.execute(f'PRAGMA table_info({table})')}
            if 'deleted_at' not in existing:
                db.execute(f'ALTER TABLE {table} ADD COLUMN deleted_at TEXT')
            if 'deleted_by' not in existing:
                db.execute(f'ALTER TABLE {table} ADD COLUMN deleted_by INTEGER REFERENCES users(id)')
        db.execute("INSERT OR IGNORE INTO settings(key,value) VALUES('elite_criteria_approved','0')")

def password_hash(password: str) -> str:
    salt = secrets.token_bytes(16)
    iterations = 390000
    digest = hashlib.pbkdf2_hmac('sha256', password.encode(), salt, iterations)
    return f'pbkdf2_sha256${iterations}${salt.hex()}${digest.hex()}'

def verify_password(password: str, encoded: str) -> bool:
    try:
        _, i, salt, digest = encoded.split('$')
        candidate = hashlib.pbkdf2_hmac('sha256', password.encode(), bytes.fromhex(salt), int(i))
        return hmac.compare_digest(candidate, bytes.fromhex(digest))
    except (ValueError, TypeError):
        return False

def audit(db, user, action, obj, obj_id=None, details=None):
    db.execute('INSERT INTO audit_log(user_id,action,object_type,object_id,details) VALUES(?,?,?,?,?)',
               (user['id'] if user else None, action, obj, obj_id, json.dumps(details or {}, ensure_ascii=False)))

def session_secret():
    provided = os.getenv('SIMR_SECRET_KEY')
    if provided:
        if len(provided) < 32:
            raise ValueError('SIMR_SECRET_KEY precisa ter pelo menos 32 caracteres')
        return provided
    keyfile = INSTANCE / '.session_secret'
    if not keyfile.exists():
        try:
            fd = os.open(keyfile, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(fd, 'w') as f:
                f.write(secrets.token_urlsafe(48))
        except FileExistsError:
            pass
    return keyfile.read_text().strip()

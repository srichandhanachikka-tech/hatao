from contextlib import contextmanager
from fastapi import FastAPI, UploadFile, File, HTTPException
from fastapi.responses import FileResponse
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field
from pathlib import Path
from datetime import datetime, timezone
import sqlite3, hashlib, secrets, json

BASE = Path(__file__).resolve().parent.parent
DB = BASE / 'ai39.db'
UP = BASE / 'uploads'
FE = BASE / 'frontend'
UP.mkdir(exist_ok=True)
STORES = ['Original Memory', 'Summary Store', 'Embedding Store', 'Cache', 'Downstream Copy']
app = FastAPI(title='AI39 Memory Deletion Verification Tool', version='1.0')
app.add_middleware(CORSMiddleware, allow_origins=['*'], allow_methods=['*'], allow_headers=['*'])

def now():
    return datetime.now(timezone.utc).isoformat(timespec='seconds')

@contextmanager
def db():
    c = sqlite3.connect(str(DB), timeout=30)
    c.row_factory = sqlite3.Row
    try:
        c.execute('PRAGMA busy_timeout=30000')
        yield c
        c.commit()
    except Exception:
        c.rollback()
        raise
    finally:
        c.close()

def aid(prefix):
    return f'{prefix}-{secrets.token_hex(4).upper()}'

def ahash(x):
    return hashlib.sha256((x or '').encode()).hexdigest()

def asdict(row):
    return dict(row) if row is not None else None

def retrieval_label(code):
    if code == 'YES':
        return 'YES — Memory is still retrievable'
    return 'NO — Memory cannot be retrieved'

def audit(mid, event, details=''):
    with db() as c:
        c.execute(
            'INSERT INTO audit(memory_id,event,details,timestamp) VALUES(?,?,?,?)',
            (mid, event, details, now()),
        )

def score_stores(store_rows):
    results = []
    for r in store_rows:
        name = r['store_name'] if isinstance(r, sqlite3.Row) or isinstance(r, dict) else r[0]
        present = bool(r['present'] if isinstance(r, sqlite3.Row) or isinstance(r, dict) else r[1])
        result = 'FOUND' if present else 'DELETED'
        conf = 0.82 if name == 'Embedding Store' and present else (0.95 if present else 1.0)
        evidence = (
            f'Post-deletion retrieval test found a residual representation in {name}.'
            if present else
            f'No retrievable synthetic memory found in {name}.'
        )
        results.append({
            'store_name': name,
            'result': result,
            'retrievable': int(present),
            'confidence': conf,
            'evidence': evidence,
        })
    total = len(results)
    deleted = sum(x['result'] == 'DELETED' for x in results)
    residual = total - deleted
    completeness = round((deleted / total) * 100, 2) if total else 0.0
    retrieval = 'YES' if residual else 'NO'
    risk = 'LOW' if residual == 0 else ('MEDIUM' if residual == 1 else 'HIGH')
    status = 'VERIFIED' if residual == 0 else 'HUMAN_REVIEW_REQUIRED'
    residual_stores = [x['store_name'] for x in results if x['result'] == 'FOUND']
    return {
        'results': results,
        'total_count': total,
        'verified_count': deleted,
        'uncertain_count': 0,
        'completeness': completeness,
        'retrieval': retrieval,
        'retrieval_label': retrieval_label(retrieval),
        'risk': risk,
        'status': status,
        'residual_stores': residual_stores,
        'coverage': f'{total}/{len(STORES)} stores checked',
    }

def init():
    with db() as c:
        c.executescript('''
        CREATE TABLE IF NOT EXISTS memories(id TEXT PRIMARY KEY,content TEXT,filename TEXT,mime_type TEXT,created_at TEXT,deleted_at TEXT,status TEXT);
        CREATE TABLE IF NOT EXISTS memory_versions(id INTEGER PRIMARY KEY AUTOINCREMENT,memory_id TEXT,version INTEGER,content TEXT,filename TEXT,action TEXT,created_at TEXT);
        CREATE TABLE IF NOT EXISTS stores(id INTEGER PRIMARY KEY AUTOINCREMENT,memory_id TEXT,store_name TEXT,present INTEGER,content_hash TEXT,updated_at TEXT);
        CREATE TABLE IF NOT EXISTS deletion_requests(id TEXT PRIMARY KEY,memory_id TEXT,requested_at TEXT,status TEXT);
        CREATE TABLE IF NOT EXISTS verifications(id TEXT PRIMARY KEY,memory_id TEXT,deletion_request_id TEXT,created_at TEXT,completeness REAL,retrieval TEXT,risk TEXT,status TEXT,verified_count INTEGER,total_count INTEGER,uncertain_count INTEGER);
        CREATE TABLE IF NOT EXISTS verification_results(id INTEGER PRIMARY KEY AUTOINCREMENT,verification_id TEXT,store_name TEXT,result TEXT,retrievable INTEGER,confidence REAL,evidence TEXT,checked_at TEXT);
        CREATE TABLE IF NOT EXISTS reviews(id INTEGER PRIMARY KEY AUTOINCREMENT,verification_id TEXT,reviewer TEXT,decision TEXT,comments TEXT,created_at TEXT);
        CREATE TABLE IF NOT EXISTS audit(id INTEGER PRIMARY KEY AUTOINCREMENT,memory_id TEXT,event TEXT,details TEXT,timestamp TEXT);
        ''')

@app.on_event('startup')
def startup():
    init()

class MemoryIn(BaseModel):
    content: str = Field(default='', max_length=10000)
    filename: str | None = None
    mime_type: str | None = None

class ReviewIn(BaseModel):
    verification_id: str
    reviewer: str = Field(min_length=1, max_length=100)
    decision: str = Field(pattern='^(CONFIRM_FINDING|INVESTIGATE)$')
    comments: str = Field(default='', max_length=2000)

def create_memory(content='', filename=None, mime=None):
    mid = aid('MEM')
    t = now()
    h = ahash(content or filename or mid)
    with db() as c:
        c.execute('INSERT INTO memories VALUES(?,?,?,?,?,?,?)', (mid, content, filename, mime, t, None, 'ACTIVE'))
        c.execute(
            'INSERT INTO memory_versions(memory_id,version,content,filename,action,created_at) VALUES(?,?,?,?,?,?)',
            (mid, 1, content, filename, 'CREATED', t),
        )
        for s in STORES:
            c.execute(
                'INSERT INTO stores(memory_id,store_name,present,content_hash,updated_at) VALUES(?,?,?,?,?)',
                (mid, s, 1, h, t),
            )
    audit(mid, 'MEMORY_CREATED', 'Synthetic/approved test memory created.')
    audit(mid, 'MEMORY_PROPAGATED', 'Memory propagated to 5 simulated stores.')
    return mid

def memory(mid):
    with db() as c:
        m = c.execute('SELECT * FROM memories WHERE id=?', (mid,)).fetchone()
        if not m:
            raise HTTPException(404, 'Memory not found.')
        s = c.execute(
            'SELECT store_name,present,updated_at FROM stores WHERE memory_id=? ORDER BY id',
            (mid,),
        ).fetchall()
        v = c.execute(
            'SELECT version,action,created_at,filename FROM memory_versions WHERE memory_id=? ORDER BY version',
            (mid,),
        ).fetchall()
        return {'memory': asdict(m), 'stores': [asdict(x) for x in s], 'versions': [asdict(x) for x in v]}

def set_store_state(mid, residual_stores):
    t = now()
    residual = set(residual_stores or [])
    with db() as c:
        for s in STORES:
            c.execute(
                'UPDATE stores SET present=?, updated_at=? WHERE memory_id=? AND store_name=?',
                (1 if s in residual else 0, t, mid, s),
            )

def register_deletion(mid, wipe=False):
    m = memory(mid)['memory']
    did = aid('DEL')
    t = now()
    with db() as c:
        c.execute('INSERT INTO deletion_requests VALUES(?,?,?,?)', (did, mid, t, 'PROCESSING'))
        v = c.execute(
            'SELECT COALESCE(MAX(version),0)+1 n FROM memory_versions WHERE memory_id=?',
            (mid,),
        ).fetchone()['n']
        c.execute(
            'INSERT INTO memory_versions(memory_id,version,content,filename,action,created_at) VALUES(?,?,?,?,?,?)',
            (mid, v, m['content'], m['filename'], 'DELETION_REQUESTED', t),
        )
        c.execute("UPDATE memories SET status='DELETION_REQUESTED',deleted_at=? WHERE id=?", (t, mid))
    audit(mid, 'DELETION_REQUESTED', f'Deletion request {did} issued at {t} for memory {mid}.')
    if wipe:
        set_store_state(mid, [])
        audit(mid, 'DELETION_PROPAGATED', 'Simulated deletion processed across 5 storage paths.')
    return {'deletion_request_id': did, 'memory_id': mid, 'status': 'PROCESSING', 'requested_at': t}

def attach_labels(payload):
    v = payload['verification']
    residual = [x['store_name'] for x in payload['results'] if x['result'] == 'FOUND']
    v['retrieval_label'] = retrieval_label(v.get('retrieval'))
    v['residual_stores'] = residual
    v['coverage'] = f"{v.get('total_count') or 0}/{len(STORES)} stores checked"
    payload['retrieval_label'] = v['retrieval_label']
    payload['residual_stores'] = residual
    payload['coverage'] = v['coverage']
    return payload

def run_verify(mid, did=None):
    memory(mid)
    with db() as c:
        rows = c.execute('SELECT * FROM stores WHERE memory_id=? ORDER BY id', (mid,)).fetchall()
        store_rows = [asdict(x) for x in rows]
    scored = score_stores(store_rows)
    vid = aid('VER')
    t = now()
    with db() as c:
        c.execute(
            'INSERT INTO verifications VALUES(?,?,?,?,?,?,?,?,?,?,?)',
            (
                vid, mid, did, t, scored['completeness'], scored['retrieval'], scored['risk'],
                scored['status'], scored['verified_count'], scored['total_count'], scored['uncertain_count'],
            ),
        )
        for r in scored['results']:
            c.execute(
                'INSERT INTO verification_results(verification_id,store_name,result,retrievable,confidence,evidence,checked_at) VALUES(?,?,?,?,?,?,?)',
                (vid, r['store_name'], r['result'], r['retrievable'], r['confidence'], r['evidence'], t),
            )
        if did:
            c.execute("UPDATE deletion_requests SET status='VERIFIED' WHERE id=?", (did,))
    audit(mid, 'VERIFICATION_STARTED', f'Verification {vid} started.')
    audit(mid, 'VERIFICATION_SCAN_STARTED', f'Verification {vid} scanned {scored["total_count"]} simulated stores.')
    for r in scored['results']:
        audit(mid, 'STORE_CHECKED', f'{r["store_name"]}: {r["result"]}.')
        if r['retrievable']:
            audit(mid, 'RESIDUAL_DETECTED', f'Residual memory detected in {r["store_name"]}.')
    audit(
        mid,
        'VERIFICATION_COMPLETED',
        f'Completeness {scored["completeness"]}%; retrieval {scored["retrieval"]}; risk {scored["risk"]}.',
    )
    audit(
        mid,
        'VERIFICATION_RESULT_GENERATED',
        f'Result {vid}: {scored["coverage"]}; residual stores: {", ".join(scored["residual_stores"]) or "none"}.',
    )
    return attach_labels(verify_detail(vid))

def verify_detail(vid):
    with db() as c:
        v = c.execute('SELECT * FROM verifications WHERE id=?', (vid,)).fetchone()
        if not v:
            raise HTTPException(404, 'Verification not found.')
        r = c.execute(
            'SELECT store_name,result,retrievable,confidence,evidence,checked_at FROM verification_results WHERE verification_id=? ORDER BY id',
            (vid,),
        ).fetchall()
        rv = c.execute(
            'SELECT reviewer,decision,comments,created_at FROM reviews WHERE verification_id=? ORDER BY id',
            (vid,),
        ).fetchall()
        payload = {'verification': asdict(v), 'results': [asdict(x) for x in r], 'reviews': [asdict(x) for x in rv]}
    return attach_labels(payload)

@app.get('/')
def home():
    return FileResponse(FE / 'index.html')

@app.get('/style.css')
def css():
    return FileResponse(FE / 'style.css', media_type='text/css')

@app.get('/app.js')
def js():
    return FileResponse(FE / 'app.js', media_type='application/javascript')

@app.get('/api/health')
def health():
    return {'ok': True, 'service': 'AI39'}

@app.get('/api/memories')
def memories():
    with db() as c:
        rows = c.execute('''SELECT m.*,
            (SELECT completeness FROM verifications v WHERE v.memory_id=m.id ORDER BY v.created_at DESC LIMIT 1) completeness,
            (SELECT risk FROM verifications v WHERE v.memory_id=m.id ORDER BY v.created_at DESC LIMIT 1) risk
            FROM memories m ORDER BY created_at DESC''').fetchall()
        return [asdict(x) for x in rows]

@app.get('/api/memories/{mid}')
def get_memory(mid):
    return memory(mid)

@app.post('/api/memories')
def post_memory(p: MemoryIn):
    if not p.content.strip() and not p.filename:
        raise HTTPException(400, 'Provide synthetic text or a test image.')
    return memory(create_memory(p.content.strip(), p.filename, p.mime_type))

@app.post('/api/memories/image')
async def image(file: UploadFile = File(...)):
    if file.content_type not in {'image/png', 'image/jpeg', 'image/webp', 'image/gif'}:
        raise HTTPException(400, 'Only PNG, JPEG, WEBP, or GIF images are accepted.')
    data = await file.read()
    if len(data) > 5 * 1024 * 1024:
        raise HTTPException(400, 'Image must be 5 MB or smaller.')
    name = Path(file.filename or 'test-image').name
    mid = create_memory('', name, file.content_type)
    (UP / f'{mid}_{name}').write_bytes(data)
    audit(mid, 'TEST_IMAGE_STORED', f'Synthetic/approved test image stored as {name}.')
    return memory(mid)

@app.post('/api/deletion-requests/{mid}')
def deletion(mid):
    d = register_deletion(mid, wipe=True)
    d['verification'] = run_verify(mid, d['deletion_request_id'])
    d['status'] = 'VERIFIED'
    d['memory'] = memory(mid)
    return d

@app.post('/api/verifications/{mid}')
def verification(mid):
    did = None
    with db() as c:
        row = c.execute(
            'SELECT id FROM deletion_requests WHERE memory_id=? ORDER BY requested_at DESC LIMIT 1',
            (mid,),
        ).fetchone()
        if row:
            did = row['id']
    return run_verify(mid, did)

@app.get('/api/verifications/{vid}')
def verification_detail(vid):
    return verify_detail(vid)

@app.get('/api/memories/{mid}/verifications')
def mem_verifications(mid):
    with db() as c:
        r = c.execute('SELECT * FROM verifications WHERE memory_id=? ORDER BY created_at DESC', (mid,)).fetchall()
        return [asdict(x) for x in r]

@app.post('/api/reviews')
def review(p: ReviewIn):
    with db() as c:
        v = c.execute('SELECT * FROM verifications WHERE id=?', (p.verification_id,)).fetchone()
        if not v:
            raise HTTPException(404, 'Verification not found.')
        mid = v['memory_id']
        t = now()
        c.execute(
            'INSERT INTO reviews(verification_id,reviewer,decision,comments,created_at) VALUES(?,?,?,?,?)',
            (p.verification_id, p.reviewer.strip(), p.decision, p.comments.strip(), t),
        )
    audit(mid, 'REVIEW_COMPLETED', f'Reviewer {p.reviewer.strip()} recorded decision {p.decision}.')
    audit(mid, 'REVIEWER_ACTION', f'{p.reviewer.strip()}: {p.decision}. {p.comments.strip()}'.strip())
    return {'ok': True, 'created_at': t}

@app.get('/api/audit')
def audit_get(memory_id: str | None = None):
    with db() as c:
        if memory_id:
            r = c.execute('SELECT * FROM audit WHERE memory_id=? ORDER BY id DESC', (memory_id,)).fetchall()
        else:
            r = c.execute('SELECT * FROM audit ORDER BY id DESC').fetchall()
        return [asdict(x) for x in r]

@app.get('/api/dashboard')
def dashboard():
    with db() as c:
        a = c.execute('SELECT COUNT(*) n FROM memories').fetchone()['n']
        b = c.execute("SELECT COUNT(*) n FROM verifications WHERE status='VERIFIED'").fetchone()['n']
        r = c.execute("SELECT COUNT(*) n FROM verifications WHERE retrieval='YES'").fetchone()['n']
        h = c.execute("SELECT COUNT(*) n FROM verifications WHERE status='HUMAN_REVIEW_REQUIRED'").fetchone()['n']
        latest = c.execute('SELECT * FROM verifications ORDER BY created_at DESC LIMIT 1').fetchone()
    latest_payload = None
    if latest:
        detail = verify_detail(latest['id'])
        latest_payload = {
            **detail['verification'],
            'results': detail['results'],
            'retrieval_label': detail['retrieval_label'],
            'residual_stores': detail['residual_stores'],
            'coverage': detail['coverage'],
        }
    return {
        'total_memories': a,
        'verified_tests': b,
        'residual_risk_tests': r,
        'human_review_tests': h,
        'latest_verification': latest_payload,
    }

SCENARIOS = {
    'complete': ('Complete deletion', []),
    'embedding': ('Embedding residual', ['Embedding Store']),
    'downstream': ('Downstream residual', ['Downstream Copy']),
    'multiple': ('Multiple residuals', ['Embedding Store', 'Downstream Copy']),
}

def run_labeled_case(name, residual_stores, persist=True):
    expected = 'YES' if residual_stores else 'NO'
    if persist:
        mid = create_memory(f'Synthetic labeled case {name}: PROJECT-AURORA-123')
        d = register_deletion(mid, wipe=False)
        set_store_state(mid, residual_stores)
        audit(mid, 'DEMO_SCENARIO_PREPARED', f'Scenario {name}: residual stores {residual_stores or "none"}.')
        scored = run_verify(mid, d['deletion_request_id'])
        actual = scored['verification']['retrieval']
        return {
            'name': name,
            'expected': expected,
            'actual': actual,
            'pass': actual == expected,
            'completeness': scored['verification']['completeness'],
            'risk': scored['verification']['risk'],
            'residual_stores': scored['residual_stores'],
            'memory_id': mid,
        }
    present_map = [{'store_name': s, 'present': int(s in residual_stores)} for s in STORES]
    scored = score_stores(present_map)
    return {
        'name': name,
        'expected': expected,
        'actual': scored['retrieval'],
        'pass': scored['retrieval'] == expected,
        'completeness': scored['completeness'],
        'risk': scored['risk'],
        'residual_stores': scored['residual_stores'],
    }

@app.get('/api/evaluation')
def evaluation():
    cases = [run_labeled_case(title, residuals, persist=False) for title, residuals in [
        ('Complete deletion', []),
        ('Embedding residual', ['Embedding Store']),
        ('Downstream residual', ['Downstream Copy']),
        ('Multiple residuals', ['Embedding Store', 'Downstream Copy']),
    ]]
    tp = sum(c['expected'] == 'YES' and c['actual'] == 'YES' for c in cases)
    fp = sum(c['expected'] == 'NO' and c['actual'] == 'YES' for c in cases)
    tn = sum(c['expected'] == 'NO' and c['actual'] == 'NO' for c in cases)
    fn = sum(c['expected'] == 'YES' and c['actual'] == 'NO' for c in cases)
    precision = tp / (tp + fp) if tp + fp else 0
    recall = tp / (tp + fn) if tp + fn else 0
    return {
        'cases': [
            {
                'name': c['name'],
                'expected': c['expected'],
                'actual': c['actual'],
                'pass': c['pass'],
                'description': 'Labeled synthetic control inspected by the verification engine.',
                'completeness': c['completeness'],
                'risk': c['risk'],
                'residual_stores': c['residual_stores'],
            }
            for c in cases
        ],
        'confusion_matrix': {'tp': tp, 'fp': fp, 'tn': tn, 'fn': fn},
        'precision': precision,
        'recall': recall,
        'failure_cases': [
            'Complete deletion',
            'Summary residual',
            'Embedding residual',
            'Cache residual',
            'Downstream residual',
            'Multiple residual stores',
        ],
        'note': 'Metrics are calculated from labeled synthetic control scenarios run through the same verification engine.',
    }

@app.post('/api/demo/scenario/{scenario}')
def scenario(scenario):
    if scenario not in SCENARIOS:
        raise HTTPException(400, 'Unknown scenario.')
    title, residuals = SCENARIOS[scenario]
    mid = create_memory(f'Synthetic demo secret for scenario {scenario}: PROJECT-AURORA-123')
    d = register_deletion(mid, wipe=False)
    set_store_state(mid, residuals)
    audit(mid, 'DEMO_SCENARIO_PREPARED', f'Scenario {title}: residual stores {residuals or "none"}.')
    return {'memory': memory(mid), 'verification': run_verify(mid, d['deletion_request_id'])}

@app.get('/api/reports/{vid}')
def report(vid):
    v = verify_detail(vid)
    m = memory(v['verification']['memory_id'])
    return {
        'title': 'AI39 Memory Deletion Verification Report',
        'generated_at': now(),
        'scope': 'This prototype demonstrates a verification methodology using a controlled synthetic AI-memory environment.',
        'disclaimer': 'Synthetic / approved test data only. This report does not claim deletion from ChatGPT, Gemini, Claude, or another third-party AI service.',
        'memory': m,
        'verification': v,
        'audit': audit_get(m['memory']['id']),
    }

@app.post('/api/reports/{vid}/download')
def download_report(vid):
    p = BASE / f'AI39_{vid}_report.json'
    p.write_text(json.dumps(report(vid), indent=2), encoding='utf-8')
    return FileResponse(p, filename=p.name, media_type='application/json')

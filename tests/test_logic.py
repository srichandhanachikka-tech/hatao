def classify(present):
    total = len(present)
    deleted = sum(not x for x in present)
    residual = total - deleted
    return round(deleted / total * 100, 2), 'YES' if residual else 'NO', 'LOW' if residual == 0 else ('MEDIUM' if residual == 1 else 'HIGH')

assert classify([False] * 5) == (100.0, 'NO', 'LOW')
assert classify([False, False, True, False, False]) == (80.0, 'YES', 'MEDIUM')
assert classify([False, True, False, False, True]) == (60.0, 'YES', 'HIGH')

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / 'backend'))
from main import score_stores, STORES, init, create_memory, register_deletion, set_store_state, run_verify, retrieval_label

init()
s = score_stores([{'store_name': n, 'present': 0} for n in STORES])
assert s['completeness'] == 100.0 and s['retrieval'] == 'NO' and s['risk'] == 'LOW'
s = score_stores([{'store_name': n, 'present': int(n == 'Embedding Store')} for n in STORES])
assert s['completeness'] == 80.0 and s['retrieval'] == 'YES' and s['risk'] == 'MEDIUM'
s = score_stores([{'store_name': n, 'present': int(n in ('Embedding Store', 'Downstream Copy'))} for n in STORES])
assert s['completeness'] == 60.0 and s['retrieval'] == 'YES' and s['risk'] == 'HIGH'
assert retrieval_label('YES') == 'YES — Memory is still retrievable'
assert retrieval_label('NO') == 'NO — Memory cannot be retrieved'

mid = create_memory('synthetic autonomous lock-test')
d = register_deletion(mid, wipe=True)
v = run_verify(mid, d['deletion_request_id'])
assert v['verification']['completeness'] == 100.0
assert v['verification']['retrieval'] == 'NO'
assert all(r['result'] == 'DELETED' for r in v['results'])
print('All offline core tests passed.')

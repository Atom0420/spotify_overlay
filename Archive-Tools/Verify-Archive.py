from pathlib import Path
import hashlib,json
r=Path(__file__).resolve().parents[1]
m=json.loads((r/'SOURCE-MANIFEST.json').read_text(encoding='utf-8'))
bad=[x['path'] for x in m['files'] if not (r/x['path']).is_file() or hashlib.sha256((r/x['path']).read_bytes()).hexdigest()!=x['sha256']]
if bad:raise SystemExit('Missing or modified: '+', '.join(bad))
print('Verified',len(m['files']),'imported files.')

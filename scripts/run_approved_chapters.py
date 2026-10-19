"""Run exactly the two user-approved chapters through the live Library API."""
from pathlib import Path
import sys
import time
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import httpx
from app.storage import DATA, ROOT, read_json, write_json


def run():
    manga = next(m for m in read_json(DATA/'library.json') if m['folder']=='manga/Mushoku Tensei - Jobless Reincarnation')
    assert manga['direction']=='rtl', 'Expected right-to-left reading direction.'
    summaries=[]
    with httpx.Client(base_url='http://127.0.0.1:8000',timeout=15) as client:
        # Complete the smaller chapter first, then process Chapter 1.
        for name in ('Chapter 12','Chapter 1'):
            response=client.post(f'/api/manga/{manga["id"]}/chapters/{name}/start')
            response.raise_for_status()
            previous=None
            while True:
                state=client.get('/api/library').json()
                entry=next(m for m in state['manga'] if m['id']==manga['id'])
                chapter=next(c for c in entry['chapters'] if c['name']==name)
                job=chapter['job']
                progress=(chapter['done'],chapter['flagged'],job.get('current_page'),job.get('status'))
                if progress!=previous:
                    print(f'{name}: {chapter["done"]}/{chapter["total"]} valid, {chapter["flagged"]} flagged; {job.get("current_page") or job.get("status")}',flush=True)
                    previous=progress
                if not state['busy']:
                    if job.get('status') not in {'complete','flagged'}:
                        raise RuntimeError(f'{name} did not finish: {job.get("message")}')
                    summary=job['summary']
                    summaries.append(summary)
                    print(summary,flush=True)
                    break
                time.sleep(5)
    write_json(ROOT/'reports/approved-chapters.json',{'reading_direction':'rtl','chapters':summaries,'scope':['Chapter 1','Chapter 12']})
    print('Both approved chapters finished. No other chapter was started.',flush=True)

if __name__=='__main__':run()

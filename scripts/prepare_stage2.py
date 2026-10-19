"""Prepare only the two approved chapters from saved panel JSON. No model calls."""
from pathlib import Path
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from app.storage import DATA,ROOT,read_json,write_json
from app.review import prepare_chapter

if __name__=='__main__':
    manga_id=read_json(DATA/'model-validation.json')['allowed_manga_id']
    reports=[]
    for chapter in ('Chapter 1','Chapter 12'):
        result=prepare_chapter(manga_id,chapter)['report']
        reports.append(result)
        print(f'{chapter}: pages={result["pages"]}, panels={result["panels"]}, flagged pages={len(result["flagged"])}',flush=True)
        for flagged in result['flagged']:
            print(f'  {flagged["page"]}: {"; ".join(flagged["reasons"])}',flush=True)
    write_json(ROOT/'reports/stage2-panels.json',{'chapters':reports})

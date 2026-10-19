"""Prepare only Chapter 1 exports from existing labeled pages."""
from pathlib import Path
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from app import storage,narration

if __name__=='__main__':
    manga_id=storage.read_json(storage.DATA/'model-validation.json')['allowed_manga_id']
    batches=narration.batches(manga_id,'Chapter 1')
    for batch in batches:
        path=narration.export_zip(manga_id,'Chapter 1',batch['id'],batch['fingerprint'])
        print(f'{batch["id"]}: {batch["pages"][0]}–{batch["pages"][-1]}, {len(batch["pages"])} pages, {len(batch["panel_ids"])} panels; {path}',flush=True)
    storage.write_json(narration.root(manga_id)/'batches.json',batches)
    print(narration.prompt_file(manga_id,'Chapter 1'))

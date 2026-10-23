"""Test the real Batch 1 manifest in temporary storage, never the real narration file."""
import json
from pathlib import Path
import sys
import tempfile
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from app import storage,review,narration


def run():
    project=storage.ROOT
    mid=storage.read_json(storage.DATA/'model-validation.json')['allowed_manga_id']
    manga,chapter_pages=review.review_context(mid,'Chapter 1')
    batch=narration.find_batch(mid,'Chapter 1','batch-001')
    originals={page.name:storage.read_json(review.record_path(manga,'Chapter 1',page)) for page in chapter_pages if page.name in batch['pages']}
    master=(project/'prompts/narration_master.md').read_text(encoding='utf-8')
    ids=batch['panel_ids']
    report={'batch':'batch-001','manifest_panels':len(ids),'kind':'Controlled structural tests, not AI-generated manga narration.','checks':{}}
    with tempfile.TemporaryDirectory(prefix='.stage3-beats-test-',dir=project) as tmp:
        testroot=Path(tmp)
        with patch.object(storage,'ROOT',testroot),patch.object(storage,'DATA',testroot/'data'),patch.object(storage,'OUTPUTS',testroot/'outputs'):
            storage.write_json(storage.DATA/'library.json',[manga]);storage.write_json(storage.DATA/'model-validation.json',{'allowed_manga_id':mid})
            (testroot/'prompts').mkdir();(testroot/'prompts/narration_master.md').write_text(master, encoding='utf-8')
            folder=testroot/manga['folder']/'Chapter 1';folder.mkdir(parents=True)
            for name,record in originals.items():
                (folder/name).touch()  # Only filenames/metadata are needed; no image processing.
                storage.write_json(review.record_path(manga,'Chapter 1',folder/name),record)
            assert narration.find_batch(mid,'Chapter 1','batch-001')['fingerprint']==batch['fingerprint']
            payload={'cast':[],'beats':[
                {'panel_ids':ids[:3],'narration':'CONTROLLED TEST TEXT: one beat may cover several selected panels.','unsure':''},
                {'panel_ids':ids[10:12],'narration':'CONTROLLED TEST TEXT: intermediate panels can be skipped.','unsure':''},
                {'panel_ids':[ids[-1]],'narration':'CONTROLLED TEST TEXT: leave the ongoing action open.','unsure':''}],
                'continuity':{'summary':'CONTROLLED TEST CONTEXT, not manga story facts.','open_threads':['CONTROLLED TEST unresolved thread.']}}
            def submit(p,correction=False):return narration.import_reply(mid,'Chapter 1','batch-001',batch['fingerprint'],json.dumps(p),correction)
            assert submit(payload)['ok'];report['checks']['selected_beats_and_skips']=True
            initial=narration.state_path(mid).read_bytes()
            bad=json.loads(json.dumps(payload));bad['beats'][1]['panel_ids']=['p999-999']
            failure=submit(bad)
            assert not failure['ok'] and 'p999-999' in failure['issues']
            assert narration.state_path(mid).read_bytes()==initial
            report['checks']['unknown_id_rejected_without_commit']=True
            fix={'beats':[dict(payload['beats'][1],replace_beat=2)]}
            assert submit(fix,True)['ok'];report['checks']['targeted_beat_correction']=True
            repeat=json.loads(json.dumps(payload));repeat['beats'][1]['panel_ids']=[ids[0]]
            assert not submit(repeat)['ok'];report['checks']['repeated_id_rejected']=True
            reverse=json.loads(json.dumps(payload));reverse['beats'][0]['panel_ids']=list(reversed(ids[:3]))
            assert not submit(reverse)['ok'];report['checks']['out_of_order_rejected']=True
            assert json.dumps(ids) in narration.prompt_text(mid,'Chapter 1')
            assert 'FINAL_RECAP: false' in narration.prompt_text(mid,'Chapter 1')
            report['checks']['manifest_and_no_default_closing']=True
            report.update(selected_panels=6,skipped_panels=len(ids)-6,beats=3,real_narration_modified=False)
    storage.write_json(project/'reports/stage3-beats-test.json',report)
    print(json.dumps(report,indent=2))

if __name__=='__main__':run()

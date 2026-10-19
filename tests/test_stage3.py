import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import uuid
import zipfile
from PIL import Image
from fastapi.testclient import TestClient
from app import storage,review,narration,main

MASTER=(storage.ROOT/'prompts/narration_master.md').read_text()


class NarrationTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(dir=storage.ROOT);self.root=Path(self.tmp.name)
        self.patches=[patch.object(storage,'ROOT',self.root),patch.object(storage,'DATA',self.root/'data'),patch.object(storage,'OUTPUTS',self.root/'outputs')]
        for p in self.patches:p.start()
        (self.root/'prompts').mkdir();(self.root/'prompts/narration_master.md').write_text(MASTER)
        self.manga={'id':'test','folder':'manga/Example','name':'Example','direction':'rtl'}
        storage.write_json(storage.DATA/'library.json',[self.manga])
        storage.write_json(storage.DATA/'model-validation.json',{'allowed_manga_id':'test'})
        folder=self.root/'manga/Example/Chapter 1';folder.mkdir(parents=True)
        for i in range(1,24):
            page=folder/f'{i:03}.png';Image.new('RGB',(100,200),'white').save(page)
            review.generate_page(self.manga,'Chapter 1',page,[[55,0,100,200],[0,0,45,200]])
        self.batches=narration.batches('test','Chapter 1');self.client=TestClient(main.app)

    def tearDown(self):
        for p in reversed(self.patches):p.stop()
        self.tmp.cleanup()

    def payload(self,batch_index=0):
        ids=self.batches[batch_index]['panel_ids']
        return {'cast':[],'beats':[{'panel_ids':ids[:2],'narration':'Controlled test text, not manga narration.','unsure':''},
                                  {'panel_ids':[ids[-1]],'narration':'Controlled continuation for testing.','unsure':''}],
                'continuity':{'summary':'Controlled test context.','open_threads':['Controlled unresolved thread.']}}

    def submit(self,payload,batch_index=0,corrections=False,**kwargs):
        b=self.batches[batch_index]
        return narration.import_reply('test','Chapter 1',b['id'],b['fingerprint'],json.dumps(payload),corrections,**kwargs)

    def test_selected_beats_skip_panels_and_merge(self):
        result=self.submit(self.payload())
        self.assertTrue(result['ok']);self.assertEqual(result['imported_beats'],2)
        self.assertEqual(result['selected_panels'],3);self.assertEqual(result['skipped_panels'],37)
        saved=narration.load_state('test')['chapters']['Chapter 1']
        self.assertEqual(saved['beats'],self.payload()['beats'])
        self.assertEqual(saved['batches']['batch-001']['continuity'],self.payload()['continuity'])

    def test_unknown_repeated_and_order_violations(self):
        ids=self.batches[0]['panel_ids']
        cases=[([{'panel_ids':['p999-1'],'narration':''}],{'p999-1'}),
               ([{'panel_ids':[ids[0],ids[0]],'narration':''}],{ids[0]}),
               ([{'panel_ids':[ids[1],ids[0]],'narration':''}],{ids[0],ids[1]}),
               ([{'panel_ids':[ids[4]],'narration':''},{'panel_ids':[ids[2]],'narration':''}],{ids[2],ids[4]}),
               ([{'panel_ids':[ids[0]],'narration':''},{'panel_ids':[ids[0]],'narration':''}],{ids[0]})]
        for beats,wrong in cases:
            p=self.payload();p['beats']=beats
            result=self.submit(p);self.assertFalse(result['ok']);self.assertEqual(set(result['issues']),wrong)
            self.assertFalse(narration.state_path('test').exists())

    def test_word_budget_scales_with_selected_panels(self):
        p=self.payload();p['beats'][0]['narration']='word '*80
        self.assertTrue(self.submit(p)['ok'])
        before=narration.state_path('test').read_bytes()
        p['beats'][0]['narration']='word '*81
        result=self.submit(p);self.assertFalse(result['ok']);self.assertIn('80-word',result['redo_message'])
        self.assertEqual(narration.state_path('test').read_bytes(),before)

    def test_targeted_correction_preserves_other_beats_and_metadata(self):
        ids=self.batches[0]['panel_ids'];p=self.payload()
        p['beats'].insert(1,{'panel_ids':[ids[3],'p999-1'],'narration':'Controlled invalid test beat.','unsure':''})
        result=self.submit(p);self.assertEqual(result['fix_beats'],[2]);self.assertIn('replace_beat',result['redo_message'])
        self.assertFalse(narration.state_path('test').exists())
        correction={'beats':[{'replace_beat':2,'panel_ids':[ids[3],ids[5]],'narration':'Controlled corrected test beat.','unsure':''}]}
        self.assertTrue(self.submit(correction,corrections=True)['ok'])
        saved=narration.load_state('test')['chapters']['Chapter 1']['beats']
        self.assertEqual(saved[0],p['beats'][0]);self.assertEqual(saved[2],p['beats'][2])
        self.assertEqual(saved[1]['panel_ids'],[ids[3],ids[5]])

    def test_context_in_next_prompt_and_changed_predecessor_invalidates(self):
        with self.assertRaises(FileExistsError):narration.prompt_text('test','Chapter 1','batch-002')
        first=self.payload();cast_name=uuid.uuid4().hex
        first['cast']=[{'name':cast_name,'gender':'','age_group':'','role':'','look':'Imported test value'}]
        self.assertTrue(self.submit(first)['ok'])
        before=narration.story_context('test','Chapter 1','batch-002')['token']
        prompt=narration.prompt_text('test','Chapter 1','batch-002')
        self.assertIn(first['beats'][-1]['narration'],prompt)
        self.assertIn(first['continuity']['summary'],prompt);self.assertIn(cast_name,prompt)
        self.assertIn('FINAL_RECAP: false',prompt)
        self.assertTrue(self.submit(self.payload(1),1,context_token=before)['ok'])
        self.assertTrue(all(b['imported'] for b in narration.status('test','Chapter 1')['batches']))
        first['beats'][-1]['narration']='Changed controlled test ending.'
        self.assertTrue(self.submit(first)['ok'])
        self.assertFalse(narration.status('test','Chapter 1')['batches'][1]['imported'])
        with self.assertRaises(FileExistsError):self.submit(self.payload(1),1,context_token=before)

    def test_prompt_manifest_grounding_and_final_choice(self):
        prompt=narration.prompt_text('test','Chapter 1')
        self.assertTrue(prompt.startswith(MASTER));self.assertIn(json.dumps(self.batches[0]['panel_ids']),prompt)
        self.assertIn('Never invent',prompt);self.assertIn('Do not invent dialogue',prompt)
        self.assertIn('FINAL_RECAP: false',prompt)
        self.assertIn('FINAL_RECAP: true',narration.prompt_text('test','Chapter 1',final_recap=True))
        self.assertTrue(self.submit(self.payload(),final_recap=True)['ok'])
        self.assertTrue(narration.status('test','Chapter 1')['batches'][0]['final_recap'])

    def test_bad_json_legacy_and_metadata_do_not_commit(self):
        b=self.batches[0]
        for reply in ('not JSON','{"beats":[],"beats":[]}','[]','{"panels":[]}'):
            result=narration.import_reply('test','Chapter 1',b['id'],b['fingerprint'],reply)
            self.assertFalse(result['ok']);self.assertFalse(narration.state_path('test').exists())
        p=self.payload();del p['continuity']
        self.assertFalse(self.submit(p)['ok'])
        self.assertTrue(self.submit({'beats':[],'continuity':self.payload()['continuity']},corrections=True)['ok'])

    def test_zip_unchanged_and_chapter_scope(self):
        b=self.batches[0];path=narration.export_zip('test','Chapter 1',b['id'],b['fingerprint'])
        with zipfile.ZipFile(path) as archive:self.assertEqual(len(archive.namelist()),20)
        url='/api/narration/test/Chapter%201';self.assertEqual(self.client.get(url).status_code,200)
        self.assertEqual(self.client.get(url+'/prompt?batch_id=batch-002').status_code,409)
        response=self.client.post(url+'/batch-001/import',json={'fingerprint':b['fingerprint'],'reply':json.dumps(self.payload())})
        self.assertEqual(response.status_code,200);self.assertEqual(response.json()['selected_panels'],3)
        self.assertEqual(self.client.get('/api/narration/test/Chapter%2012/prompt').status_code,403)
        self.assertEqual(self.client.post('/api/narration/test/Chapter%2012/batch-001/import',json={'fingerprint':b['fingerprint'],'reply':json.dumps(self.payload())}).status_code,403)

if __name__=='__main__':unittest.main()

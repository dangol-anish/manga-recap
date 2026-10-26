import copy
import unittest
from unittest.mock import patch
from app import shorts, shorts_render, shorts_review, storage, alignment
from tests import test_shorts as fixtures


class ShortsReviewTests(unittest.TestCase):
    setUp=fixtures.ShortsTests.setUp
    tearDown=fixtures.ShortsTests.tearDown
    ready=fixtures.ShortsTests.ready
    detect=fixtures.ShortsTests.detect
    prepare=fixtures.ShortsTests.prepare
    payload=fixtures.ShortsTests.payload
    status=fixtures.ShortsTests.status
    submit=fixtures.ShortsTests.submit
    reply=fixtures.ShortsTests.reply
    import_proposals=fixtures.ShortsTests.import_proposals
    imported=fixtures.ShortsTests.imported

    def setup_review(self):
        self.imported();self.saved=shorts.load(self.pid);self.short=self.saved['shorts'][0]
        self.url=self.api+'/shorts/'+self.short['id']+'/review'

    def add_clips(self):
        folder=shorts.output(self.pid);folder.mkdir(parents=True,exist_ok=True)
        for i,row in enumerate(self.short['segments']):
            path=folder/f'fixture-{i}.mp3';path.write_bytes(b'isolated fixture, never decoded or played')
            words=alignment.script_tokens(row['narration']);step=9/len(words)
            self.short['clips'][str(i)]={'file':path.name,'sha256':shorts.checksum(path),'token':shorts.clip_token(self.saved,self.short,i),
                'duration':9.,'words':[{'text':row['narration'][a:b],'start':j*step,'end':(j+.8)*step} for j,(_,a,b) in enumerate(words)]}
        shorts.save(self.saved)

    def test_review_read_only_with_exact_original_sources_and_no_generation(self):
        self.setup_review();before={p:p.read_bytes() for p in self.root.rglob('*') if p.is_file()}
        with patch.object(alignment,'speech',side_effect=AssertionError('No TTS')),patch.object(shorts_render,'render',side_effect=AssertionError('No render')):
            response=self.client.get(self.url)
        self.assertEqual(response.status_code,200,response.text);data=response.json()
        self.assertEqual(response.headers['cache-control'],'no-store')
        self.assertFalse(data['all_measured']);self.assertEqual(data['measured_segments'],0)
        self.assertEqual(data['opening'],self.short['segments'][0]['narration'])
        self.assertEqual(data['segments'][0]['source_narration'],self.saved['source']['chapters'][0]['beats'][0]['narration'])
        self.assertEqual(data['segments'][0]['panel_id'],self.pid_panel)
        self.assertEqual(before,{p:p.read_bytes() for p in self.root.rglob('*') if p.is_file()})
        self.assertNotIn(str(self.root),response.text)

    def test_estimates_use_frozen_speed_not_new_defaults(self):
        self.setup_review();a=shorts_review.review(self.saved,self.short)
        self.saved['pacing']['speed']*=2;b=shorts_review.review(self.saved,self.short)
        self.assertAlmostEqual(a['duration']/2,b['duration'])
        self.assertIsNotNone(b['estimated_range'])

    def test_matching_clips_use_actual_timing_and_renderer_caption_groups(self):
        self.setup_review();self.add_clips();response=self.client.get(self.url);data=response.json()
        self.assertTrue(data['all_measured']);self.assertEqual(data['duration'],36);self.assertIsNone(data['estimated_range'])
        self.assertEqual(data['segments'][1]['start'],9)
        self.assertEqual(data['segments'][1]['captions'],shorts_render.caption_groups(self.short['clips']['1']['words'],9,18))
        self.assertTrue(any('same panel' in n['text'] for n in data['notices']))

    def test_changed_clip_or_script_falls_back_to_explicit_estimate(self):
        self.setup_review();self.add_clips()
        path=shorts.output(self.pid)/self.short['clips']['0']['file'];path.write_bytes(b'changed')
        data=self.client.get(self.url).json()
        self.assertFalse(data['segments'][0]['measured']);self.assertFalse(data['all_measured'])
        self.assertEqual(data['measured_segments'],3)

    def test_source_freshness_and_unknown_short_are_rejected(self):
        self.setup_review()
        from app import narration_stage5 as story
        state=story.load_state(self.mid);state['chapters'][self.chapter]['batches']['batch-001']['beats'][0]['narration']='Changed.'
        storage.write_json(story.state_path(self.mid),state)
        self.assertEqual(self.client.get(self.url).status_code,409)
        self.assertEqual(self.client.get(self.api+'/shorts/'+'a'*32+'/review').status_code,400)

    def test_uncertainty_and_length_checks_do_not_rewrite_script(self):
        self.setup_review();original=copy.deepcopy(self.short)
        self.saved['source']['chapters'][0]['beats'][0]['unsure']='Identity unclear.'
        # Exercise advisory formatting on this deliberately synthetic source;
        # source-integrity failures are covered separately.
        with patch.object(shorts,'fresh'):data=shorts_review.review(self.saved,self.short)
        self.assertTrue(any('uncertainty' in n['text'] for n in data['notices']))
        self.assertTrue(any('outside 30' in n['text'] for n in data['notices']))
        self.assertEqual(self.short,original)

    def test_context_presentation_changes_do_not_invalidate_original_projects(self):
        self.setup_review();before=copy.deepcopy(self.saved)
        live=shorts.snapshot(self.mid,[self.chapter]);live['chapters'][0]['context_before']['instructions']='New presentation'
        live['chapters'][0]['context_before']['pinned_memory']=[{'text':'A user review'}]
        with patch.object(shorts,'snapshot',return_value=live):shorts.fresh(self.saved)
        self.assertEqual(self.saved,before)
        live['chapters'][0]['context_before']['token']='changed-history'
        with patch.object(shorts,'snapshot',return_value=live):
            with self.assertRaises(FileExistsError):shorts.fresh(self.saved)

    def test_tampered_source_never_passes_compatibility_check(self):
        self.setup_review();self.saved['source']['chapters'][0]['beats'][0]['narration']='Tampered.'
        with self.assertRaises(FileExistsError):shorts.fresh(self.saved)

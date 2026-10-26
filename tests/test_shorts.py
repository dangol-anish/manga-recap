import copy
import json
from pathlib import Path
import shutil
import subprocess
import threading
import unittest
from unittest.mock import patch

from app import shorts, shorts_render, storage, alignment, video
from tests import test_narration_stage5 as story_tests


class ShortsTests(unittest.TestCase):
    ready = story_tests.StoryTests.ready
    detect = story_tests.StoryTests.detect
    prepare = story_tests.StoryTests.prepare
    payload = story_tests.StoryTests.payload
    status = story_tests.StoryTests.status
    submit = story_tests.StoryTests.submit

    def setUp(self):
        story_tests.StoryTests.setUp(self)
        shorts.active = shorts.worker = None; shorts.cancel.clear()
        self.ready(); self.assertEqual(self.submit().status_code, 200)
        master = Path(__file__).resolve().parents[1] / 'prompts/shorts_master.md'
        (self.root / 'prompts/shorts_master.md').write_text(master.read_text(encoding='utf-8'), encoding='utf-8')
        self.before = {p: p.read_bytes() for base in (storage.DATA, storage.OUTPUTS, self.root / 'manga') for p in base.rglob('*') if p.is_file()}
        self.project = shorts.create(self.mid, [self.chapter], 'Test shorts')
        self.pid = self.project['id']; self.api = '/api/shorts/projects/' + self.pid
        self.pid_panel = shorts.load(self.pid)['source']['chapters'][0]['beats'][0]['panel_ids'][0]

    def tearDown(self):
        shorts.cancel.set()
        if shorts.worker and shorts.worker.is_alive(): shorts.worker.join(15)
        shorts.active = shorts.worker = None; shorts.cancel.clear()
        story_tests.StoryTests.tearDown(self)

    def reply(self):
        # Four separately spoken panels; repeated holds are permitted explicitly.
        return {'receipt': copy.deepcopy(self.project['receipt']), 'shorts': [{'title': 'A moment changes everything',
            'why_it_works': 'A clear setup and payoff from the supplied story.', 'segments': [
                {'chapter': self.chapter, 'source_beat': 1, 'panel_id': self.pid_panel,
                 'narration': 'This is a synthetic test narration with enough words to exercise a complete short script safely.'}
                for _ in range(4)]}]}

    def import_proposals(self, payload=None, **kwargs):
        return self.client.post(self.api + '/import', json={'revision': shorts.load(self.pid)['revision'], 'reply': json.dumps(payload or self.reply()), **kwargs})

    def imported(self):
        response = self.import_proposals(); self.assertEqual(response.status_code, 200, response.text)
        self.assertTrue(response.json()['ok']); return response.json()['project']

    def assert_sources_safe(self):
        for path, data in self.before.items(): self.assertEqual(path.read_bytes(), data, str(path))

    def test_package_prompt_and_opening_are_read_only(self):
        with patch.object(alignment, 'speech', side_effect=AssertionError('No automatic speech')):
            response = self.client.get(self.api + '/package'); self.assertEqual(response.status_code, 200)
            bundle = response.json(); self.assertEqual(bundle['receipt'], self.project['receipt'])
            beat = bundle['source']['chapters'][0]['beats'][0]
            self.assertNotIn('panels', beat); self.assertEqual(beat['panel_ids'], [self.pid_panel])
            self.assertNotIn(str(self.root), response.text)
            self.assertIn('1–40', self.client.get(self.api + '/prompt').json()['prompt'])
            self.assertTrue(self.client.get('/api/shorts/catalog').json()[0]['chapters'][0]['shorts_ready'])
            self.assertEqual(self.client.get(self.api).status_code, 200)
        self.assertFalse(shorts.output(self.pid).exists()); self.assert_sources_safe()

    def test_real_multiple_chapter_package_keeps_namespaces_and_context(self):
        from app import detection
        first = self.chapter
        second = self.source.parent / 'Chapter 2'; shutil.copytree(self.source, second)
        self.ident = next(c['id'] for c in detection.catalog()['manga'][0]['chapters'] if c['name'] == 'Chapter 2')
        self.detect(); self.ready(); self.assertEqual(self.chapter, 'Chapter 2')
        self.assertEqual(self.submit().status_code, 200)
        self.before = {p: p.read_bytes() for base in (storage.DATA, storage.OUTPUTS, self.root / 'manga') for p in base.rglob('*') if p.is_file()}
        project = shorts.create(self.mid, ['Chapter 2', first], 'Two chapters')
        package = shorts.bundle(shorts.load(project['id']))
        self.assertEqual([c['chapter'] for c in package['source']['chapters']], ['Chapter 1', 'Chapter 2'])
        self.assertEqual([c['beats'][0]['panel_ids'][0] for c in package['source']['chapters']], [self.pid_panel, self.pid_panel])
        self.assertTrue(package['source']['chapters'][1]['context_before'])
        self.assert_sources_safe()

    def test_voice_defaults_are_frozen_per_project(self):
        project = shorts.load(self.pid); short = self.imported()['shorts'][0]
        token = shorts.clip_token(project, short, 0)
        with patch.object(shorts.audio, 'DEFAULT_VOICE', 'changed'), patch.object(shorts.audio, 'DEFAULT_PACING', {'speed': 2}):
            self.assertEqual(shorts.clip_token(project, short, 0), token)
        self.assertEqual(project['voice'], 'en-US-BrianMultilingualNeural')
        self.assertEqual(project['pacing']['speed'], 1.0)

    def test_zero_proposals_and_atomic_failure(self):
        project = self.imported(); before = (shorts.root(self.pid) / 'project.json').read_bytes()
        bad = self.reply(); bad['shorts'][0]['segments'][0]['panel_id'] = 'p9999-9'
        response = self.import_proposals(bad).json(); self.assertFalse(response['ok']); self.assertIn('p9999-9', ' '.join(response['errors']))
        self.assertEqual((shorts.root(self.pid) / 'project.json').read_bytes(), before)
        payload = self.reply(); payload['shorts'] = []
        self.assertTrue(self.import_proposals(payload).json()['ok']); self.assertTrue(list((shorts.root(self.pid) / 'history').glob('*.json')))
        self.assert_sources_safe()

    def test_receipt_duplicate_keys_nan_and_stale_revision(self):
        before = (shorts.root(self.pid) / 'project.json').read_bytes()
        wrong = self.reply(); wrong['receipt']['project_id'] = '0' * 32
        self.assertFalse(self.import_proposals(wrong).json()['ok'])
        for text in ('{"receipt":{},"receipt":{},"shorts":[]}', '{"receipt":NaN,"shorts":[]}'):
            response = self.client.post(self.api + '/import', json={'revision': self.project['revision'], 'reply': text})
            self.assertFalse(response.json()['ok'])
        self.assertEqual((shorts.root(self.pid) / 'project.json').read_bytes(), before)
        self.imported()
        self.assertEqual(self.client.post(self.api + '/import', json={'revision': self.project['revision'], 'reply': json.dumps(self.reply())}).status_code, 409)

    def test_chapter_qualified_ids_and_source_order(self):
        project = shorts.load(self.pid); other = copy.deepcopy(project['source']['chapters'][0]); other['chapter'] = 'Chapter 2'; project['source']['chapters'].append(other)
        payload = self.reply(); payload['shorts'][0]['segments'][-1]['chapter'] = 'Chapter 2'
        self.assertEqual(shorts.validate(project, payload)[0], [])  # identical panel IDs resolve per chapter
        payload['shorts'][0]['segments'][0]['chapter'] = 'Chapter 2'
        self.assertIn('backwards', ' '.join(shorts.validate(project, payload)[0]))
        payload['shorts'][0]['segments'][0]['chapter'] = ['bad']
        self.assertTrue(shorts.validate(project, payload)[0])

    def test_source_changes_block_import_and_keep_saved_files(self):
        current = self.imported(); stored = shorts.load(self.pid)
        source = stored['source']['chapters'][0]['beats'][0]['panels'][0]
        path = storage.ROOT / source['crop']; path.write_bytes(path.read_bytes() + b'changed')
        response = self.client.get(self.api).json(); self.assertTrue(response['stale'])
        self.assertEqual(self.client.get(self.api + '/package').status_code, 409)
        self.assertEqual(self.import_proposals().status_code, 409)
        self.assertEqual(self.client.post(self.api + '/create', json={'revision': current['revision'], 'short_ids': [current['shorts'][0]['id']]}).status_code, 409)
        self.assertEqual(shorts.load(self.pid)['shorts'], stored['shorts'])

    def test_visual_check_corrects_one_proposal_only(self):
        payload = self.reply(); payload['shorts'].append(copy.deepcopy(payload['shorts'][0])); current = self.import_proposals(payload).json()['project']; first, second = current['shorts']
        with patch.object(shorts, 'open_system_folder') as opened:
            check = self.client.post(self.api + '/shorts/' + first['id'] + '/visual-check').json()
            self.assertEqual(check['pages'], 1); opened.assert_called_once()
            self.assertIn('chapter', check['prompt']); folder = opened.call_args.args[0]
            self.assertEqual(len(list(folder.glob('*.png'))), 1)
        response = self.import_proposals(replace_id=first['id']).json()
        self.assertTrue(response['ok']); self.assertEqual(response['project']['shorts'][1]['id'], second['id'])
        self.assert_sources_safe()

    def test_script_edits_keep_source_and_other_proposals(self):
        current = self.imported(); short = current['shorts'][0]
        segments = copy.deepcopy(short['segments']); segments[0]['narration'] = 'An updated synthetic narration keeps this story segment grounded while using a slightly different opening phrase.'
        response = self.client.post(self.api + '/shorts/' + short['id'] + '/edit', json={'revision': current['revision'], 'segments': segments})
        self.assertTrue(response.json()['ok']); self.assertEqual(response.json()['project']['shorts'][0]['segments'], segments)
        self.assert_sources_safe()

    def test_stop_recovery_and_heavy_task_exclusion(self):
        from app.tasks import ensure_idle
        current = shorts.load(self.pid); current['job'] = {'status': 'running'}; shorts.save(current); shorts.recover()
        self.assertEqual(shorts.load(self.pid)['job']['status'], 'paused'); self.assertIsNone(shorts.active)
        shorts.active = self.pid
        with self.assertRaises(FileExistsError): ensure_idle()
        response = self.client.post(self.api + '/stop', json={}); self.assertEqual(response.status_code, 200); self.assertTrue(shorts.cancel.is_set())
        shorts.active = None
        with patch('app.stitch.active', {'id': 'busy'}):
            with self.assertRaises(FileExistsError): shorts.create(self.mid, [self.chapter], 'blocked')

    @unittest.skipUnless(shutil.which('ffmpeg') and shutil.which('ffprobe'), 'FFmpeg required')
    def test_real_vertical_video_retry_and_source_preservation(self):
        current = self.imported(); short = current['shorts'][0]; sample = self.root / 'sample.mp3'
        subprocess.run(['ffmpeg', '-v', 'error', '-f', 'lavfi', '-i', 'sine=frequency=500:duration=1.3', '-ar', '24000', '-ac', '1', '-codec:a', 'libmp3lame', str(sample)], check=True, capture_output=True)
        calls = []
        def synth(text, target, pacing, voice):
            calls.append(text); shutil.copyfile(sample, target); duration = len(video.pcm(sample)) / 48000
            tokens = alignment.script_tokens(text); step = duration / len(tokens)
            return {'duration': duration, 'words': [{'text': text[a:b], 'start': i * step, 'end': (i + .8) * step} for i, (_, a, b) in enumerate(tokens)]}
        with patch.object(alignment, 'speech', side_effect=synth), patch.object(shorts, 'MIN_SECONDS', 1):
            response = self.client.post(self.api + '/create', json={'revision': current['revision'], 'short_ids': [short['id']]})
            self.assertEqual(response.status_code, 200, response.text); shorts.worker.join(30); self.assertIsNone(shorts.active)
            saved = shorts.load(self.pid); self.assertEqual(saved['job']['status'], 'complete', saved['job'])
            result = shorts.result_path(saved, saved['shorts'][0]); info = json.loads(subprocess.check_output(['ffprobe', '-v', 'error', '-show_streams', '-of', 'json', str(result)]))
            visual = next(s for s in info['streams'] if s['codec_type'] == 'video'); self.assertEqual((visual['width'], visual['height']), (720, 1280))
            self.assertEqual(len(calls), 4)
            response = self.client.get(self.api + '/shorts/' + short['id'] + '/video', headers={'Range': 'bytes=0-99'}); self.assertEqual(response.status_code, 206)
            shorts.start(self.pid, current['revision'], [short['id']]); shorts.worker.join(10); self.assertEqual(len(calls), 4)
        self.assert_sources_safe()

    @unittest.skipUnless(shutil.which('ffmpeg'), 'FFmpeg required')
    def test_voice_failure_saves_completed_clips_for_retry(self):
        current = self.imported(); sid = current['shorts'][0]['id']; sample = self.root / 'short.mp3'
        subprocess.run(['ffmpeg', '-v', 'error', '-f', 'lavfi', '-i', 'sine=duration=1', '-ar', '24000', '-ac', '1', str(sample)], check=True, capture_output=True)
        calls = []
        def synth(text, target, pacing, voice):
            calls.append(text)
            if len(calls) == 2: raise RuntimeError('offline')
            shutil.copyfile(sample, target); duration = len(video.pcm(sample)) / 48000; tokens = alignment.script_tokens(text); step = duration / len(tokens)
            return {'words': [{'text': text[a:b], 'start': i*step, 'end': (i+.8)*step} for i, (_,a,b) in enumerate(tokens)]}
        with patch.object(alignment, 'speech', side_effect=synth):
            shorts.start(self.pid, current['revision'], [sid]); shorts.worker.join(10)
        saved = shorts.load(self.pid); self.assertEqual(saved['job']['status'], 'failed'); self.assertEqual(len(saved['shorts'][0]['clips']), 1)
        self.assertTrue(shorts.clip_ready(self.pid, saved['shorts'][0]['clips']['0'], shorts.clip_token(saved, saved['shorts'][0], 0)))
        self.assert_sources_safe()

    def test_paths_cannot_escape_and_schema_is_ai_studio_compatible(self):
        for pid in ('..', 'x' * 32, '../' + self.pid):
            with self.assertRaises(ValueError): shorts.root(pid)
        with self.assertRaises(ValueError): shorts.safe(storage.ROOT / '..' / 'other')
        self.assertNotIn('additionalProperties', json.dumps(shorts.schema()))
        self.assertEqual(self.client.get('/api/shorts/schema').status_code, 200)

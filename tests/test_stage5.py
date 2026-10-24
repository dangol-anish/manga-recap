import copy
import json
import shutil
import subprocess
import tempfile
import unittest
import wave
from pathlib import Path
from unittest.mock import patch
from PIL import Image
from fastapi.testclient import TestClient
from app import storage, video, main


class VideoTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(dir=storage.ROOT);self.root=Path(self.tmp.name)
        self.output=patch.object(storage,'OUTPUTS',self.root);self.output.start()
        self.wav=self.root/'test.wav'
        with wave.open(str(self.wav),'wb') as f:
            f.setparams((1,2,24000,0,'NONE','not compressed'));f.writeframes(b'\0\0'*48000)
        self.panels=[]
        for i,color in enumerate(['red','blue']):
            path=self.root/f'{i}.png';Image.new('RGB',(80,160),color).save(path)
            self.panels.append({'id':f'p001-{i+1}','revision':'revision','url':'/test','_crop':str(path)})
        self.beats=[{'number':1,'token':'a'*64,'narration':'Controlled audio test.','panel_ids':['p001-1','p001-2'],
                     'revision':'revision','silent':False,'duration':2.0,'_audio':str(self.wav),'panels':self.panels}]
        self.source=patch.object(video,'source',side_effect=lambda *args:(copy.deepcopy(self.beats),'a'*64));self.source.start()
        video.jobs.clear()

    def tearDown(self):
        self.source.stop();self.output.stop();self.tmp.cleanup();video.jobs.clear()

    def test_equal_timing_and_save(self):
        initial=video.timeline('test','Chapter 1');self.assertEqual(initial['beats'][0]['switches'],[1.0])
        changed=video.save('test','Chapter 1','a'*64,1,[.6])
        self.assertEqual(changed['beats'][0]['switches'],[.6]);self.assertNotEqual(initial['render_token'],changed['render_token'])
        self.assertNotIn('_audio',changed['beats'][0]);self.assertNotIn('_crop',changed['beats'][0]['panels'][0])
        self.assertEqual(video.timeline('test','Chapter 1')['beats'][0]['switches'],[.6])

    def test_invalid_times_and_stale_requests(self):
        for values in [[],[-1],[0],[2],[float('nan')],[True],[1,1]]:
            with self.assertRaises(ValueError):video.save('test','Chapter 1','a'*64,1,values)
        with self.assertRaises(FileExistsError):video.save('test','Chapter 1','b'*64,1,[.5])
        with self.assertRaises(FileExistsError):video.start('test','Chapter 1','b'*64)
        with self.assertRaises(FileNotFoundError):video.asset('test','Chapter 1',video.timeline('test','Chapter 1')['render_token'])

    def test_sources_reset_timing_and_stale_export(self):
        video.save('test','Chapter 1','a'*64,1,[.5])
        with patch.object(video,'source',side_effect=lambda *args:(copy.deepcopy(self.beats),'b'*64)):
            state=video.timeline('test','Chapter 1');self.assertTrue(state['timing_reset']);self.assertEqual(state['beats'][0]['switches'],[1])
            with self.assertRaises(FileExistsError):video.asset('test','Chapter 1','a'*64)

    def test_other_chapter_blocked(self):
        self.source.stop()
        try:
            c=TestClient(main.app)
            self.assertEqual(c.get('/video/test/Chapter%2012').status_code,404)
            self.assertEqual(c.get('/api/video/test/Chapter%2012').status_code,404)
            self.assertEqual(c.post('/api/video/test/Chapter%2012/timing',json={'source_token':'a'*64,'number':1,'switches':[]}).status_code,404)
            self.assertEqual(c.post('/api/video/test/Chapter%2012/export',json={'render_token':'a'*64}).status_code,404)
            self.assertEqual(c.get('/api/video/test/Chapter%2012/mp4?token='+'a'*64).status_code,404)
        finally:self.source.start()

    def test_real_mp4_switches_and_audio_stream(self):
        if not shutil.which('ffmpeg'):self.skipTest('FFmpeg not installed')
        video.save('test','Chapter 1','a'*64,1,[.6])
        state=video.timeline('test','Chapter 1');video.jobs['test']={'running':True,'duration':2,'message':''}
        video.run('test','Chapter 1',state)
        self.assertFalse(video.jobs['test'].get('error'),video.jobs['test'])
        path=video.asset('test','Chapter 1',state['render_token'])
        for t,channel in [(.2,0),(1.2,2)]:
            data=subprocess.run(['ffmpeg','-v','error','-ss',str(t),'-i',str(path),'-frames:v','1','-vf','scale=1:1','-f','rawvideo','-pix_fmt','rgb24','-'],capture_output=True,check=True).stdout
            self.assertGreater(data[channel],max(data[(channel+1)%3],data[(channel+2)%3])+30)
        response=TestClient(main.app).get('/api/video/test/Chapter%201/mp4?token='+state['render_token'],headers={'Range':'bytes=0-99'})
        self.assertEqual(response.status_code,206);self.assertEqual(len(response.content),100)
        video.save('test','Chapter 1','a'*64,1,[.8])
        with self.assertRaises(FileExistsError):video.asset('test','Chapter 1',state['render_token'])

    def test_changed_timing_during_render_cannot_publish(self):
        if not shutil.which('ffmpeg'):self.skipTest('FFmpeg not installed')
        state=video.timeline('test','Chapter 1');video.save('test','Chapter 1','a'*64,1,[.6])
        video.jobs['test']={'running':True,'duration':2,'message':''}
        video.run('test','Chapter 1',state)
        self.assertTrue(video.jobs['test']['error']);self.assertFalse(video.export_path('test',state).exists())

    def test_current_panel_background_shadow_and_sharp_center(self):
        import numpy as np
        image=Image.new('RGB',(400,800),(200,140,80))
        frame=video.compose_frame(image)
        self.assertEqual(frame.size,(video.WIDTH,video.HEIGHT))
        fitted_width=round(400*(round(video.HEIGHT*.92)/800))
        x=(video.WIDTH-fitted_width)//2
        self.assertEqual(frame.getpixel((video.WIDTH//2,video.HEIGHT//2)),(200,140,80))
        background=frame.getpixel((20,video.HEIGHT//2))
        self.assertTrue(all(20<c<150 for c in background))
        self.assertLess(sum(frame.getpixel((x-12,video.HEIGHT//2))),sum(background))
        # High-frequency art stays sharp centrally and loses detail in the background.
        from PIL import ImageDraw
        stripes=Image.new('RGB',(400,800),'white');draw=ImageDraw.Draw(stripes)
        for n in range(0,400,16):draw.rectangle((n,0,n+7,799),fill='black')
        pixels=np.asarray(video.compose_frame(stripes)).astype(float)
        self.assertGreater(pixels[100:600,500:780].std(),85)
        self.assertLess(pixels[100:600,20:100].std(),15)

    def test_visual_style_invalidates_export_without_resetting_timing(self):
        video.save('test','Chapter 1','a'*64,1,[.6])
        before=video.timeline('test','Chapter 1')
        with patch.dict(video.VISUAL_STYLE,{'blur':32}):
            after=video.timeline('test','Chapter 1')
        self.assertNotEqual(before['render_token'],after['render_token'])
        self.assertEqual(before['source_token'],after['source_token'])
        self.assertEqual(before['beats'][0]['switches'],after['beats'][0]['switches'])
        self.assertFalse(after['timing_reset'])

    def test_composed_frame_api_uses_current_selected_panel_and_cache(self):
        for p in self.panels:p['_frame']=str(self.root/'frames'/(p['id']+'.png'))
        c=TestClient(main.app);base='/api/video/test/Chapter%201/frame'
        params={'panel_id':'p001-1','revision':'revision','style':video.digest(video.VISUAL_STYLE)}
        response=c.get(base,params=params)
        self.assertEqual(response.status_code,200);self.assertEqual(response.headers['content-type'],'image/png')
        import io
        with Image.open(io.BytesIO(response.content)) as frame:self.assertEqual(frame.size,(1280,720))
        cached=Path(self.panels[0]['_frame']);modified=cached.stat().st_mtime_ns
        self.assertEqual(c.get(base,params=params).status_code,200)
        self.assertEqual(cached.stat().st_mtime_ns,modified)
        self.assertEqual(c.get(base,params=dict(params,panel_id='p999-1')).status_code,404)
        self.assertEqual(c.get(base,params=dict(params,revision='old')).status_code,404)
        self.assertEqual(c.get(base,params=dict(params,style='old')).status_code,409)
        self.source.stop()
        try:self.assertEqual(c.get('/api/video/test/Chapter%2012/frame',params=params).status_code,404)
        finally:self.source.start()

    def test_motion_uses_cumulative_cut_frames_without_drift(self):
        beats=[]
        for n in range(20):
            beats.append({'number':n+1,'duration':1.03,'switches':[.17,.63],
                          'panels':[{'id':f'p{n:03}-{i}'} for i in range(3)]})
        plan=video.motion_plan({'beats':beats})
        self.assertEqual(sum(p['frames'] for p in plan),round(20*1.03*24))
        self.assertEqual(plan[-1]['start_frame']+plan[-1]['frames'],round(20*1.03*24))
        self.assertEqual([p['kind'] for p in plan[:4]],video.MOTION_STYLE['cycle'])
        self.assertTrue(all(p['frames']>=1 for p in plan))
        self.assertLess(video.motion_amount('zoom_in',.25),video.motion_amount('zoom_in',8))

    def test_motion_keeps_whole_foreground_inside_canvas(self):
        # Full-width and full-height panels both have the approved 92% fit.
        for kind in video.MOTION_STYLE['cycle']:
            amount=video.motion_amount(kind,30)
            for progress in (0,.5,1):
                zoom=1+amount*(progress if kind=='zoom_in' else 1-progress if kind=='zoom_out' else 1)
                offset=abs((2*progress-1)*amount/2) if kind.startswith('slide') else 0
                self.assertLess(.92*zoom/2+offset,.5)
                self.assertLess(.92*zoom/2,.5)

    def test_motion_change_invalidates_export_preserving_timing(self):
        video.save('test','Chapter 1','a'*64,1,[.6])
        before=video.timeline('test','Chapter 1')
        with patch.dict(video.MOTION_STYLE,{'zoom_max':.035}):after=video.timeline('test','Chapter 1')
        self.assertNotEqual(before['render_token'],after['render_token'])
        self.assertEqual(before['source_token'],after['source_token'])
        self.assertEqual(before['beats'][0]['switches'],after['beats'][0]['switches'])
        self.assertFalse(after['timing_reset'])

    def test_real_export_has_all_four_motion_directions(self):
        import numpy as np
        if not shutil.which('ffmpeg'):self.skipTest('FFmpeg not installed')
        white=self.root/'white.png';Image.new('RGB',(80,160),'white').save(white)
        panels=[{'id':f'p001-{i+1}','revision':'test','_crop':str(white),'motion_kind':kind}
                for i,kind in enumerate(video.MOTION_STYLE['cycle'])]
        with wave.open(str(self.wav),'wb') as f:
            f.setparams((1,2,24000,0,'NONE','not compressed'));f.writeframes(b'\0\0'*(8*24000))
        state={'duration':8,'beats':[dict(self.beats[0],duration=8,switches=[2,4,6],panels=panels)]}
        work=self.root/'motion-test';work.mkdir();video.render_to(state,work)
        selection='+'.join(f'eq(n,{n})' for n in [1,46,49,94,97,142,145,190])
        data=subprocess.check_output(['ffmpeg','-nostdin','-v','error','-i',str(work/'chapter.mp4'),
              '-vf',f"select='{selection}',crop=1280:2:0:360,format=gray",'-fps_mode','vfr','-f','rawvideo','-'],timeout=60)
        rows=np.frombuffer(data,dtype=np.uint8).reshape(8,2,1280).mean(axis=1)
        extents=[np.where(row>220)[0] for row in rows]
        widths=[int(x[-1]-x[0]) for x in extents];centers=[float((x[-1]+x[0])/2) for x in extents]
        self.assertGreaterEqual(widths[1]-widths[0],2)  # Zoom in.
        self.assertGreaterEqual(widths[2]-widths[3],2)  # Zoom out.
        self.assertGreaterEqual(centers[5]-centers[4],5)  # From left toward right.
        self.assertGreaterEqual(centers[6]-centers[7],5)  # From right toward left.
        self.assertTrue(all(x[0]>0 and x[-1]<1279 for x in extents))

    def test_slow_motion_has_no_pixel_jumps_or_repeated_backtracking(self):
        import numpy as np
        if not shutil.which('ffmpeg'):self.skipTest('FFmpeg not installed')
        white=self.root/'edge.png';Image.new('RGB',(400,800),'white').save(white)
        panels=[{'id':f'p001-{i+1}','_crop':str(white),'motion_kind':kind}
                for i,kind in enumerate(video.MOTION_STYLE['cycle'])]
        with wave.open(str(self.wav),'wb') as out:
            out.setparams((1,2,24000,0,'NONE','not compressed'));out.writeframes(b'\0\0'*(24*24000))
        state={'duration':24,'beats':[dict(self.beats[0],duration=24,switches=[6,12,18],panels=panels)]}
        work=self.root/'temporal-test';work.mkdir()
        # Measure camera movement alone; dissolve mixtures are tested separately.
        # Lossy codec edge noise varies across FFmpeg/x264 builds. Use lossless
        # H.264 here so the metric measures camera sampling, not compression.
        execute=subprocess.run
        def lossless(command,**kwargs):
            command=list(command)
            if '-crf' in command:command[command.index('-crf')+1]='0'
            return execute(command,**kwargs)
        with patch.dict(video.TRANSITION_STYLE,{'frames':0}),patch.object(video.subprocess,'run',side_effect=lossless):
            video.render_to(state,work)
        data=subprocess.check_output(['ffmpeg','-nostdin','-v','error','-i',str(work/'chapter.mp4'),
              '-vf','crop=1280:2:0:360,format=gray','-f','rawvideo','-'],timeout=60)
        rows=np.frombuffer(data,dtype=np.uint8).reshape(4,144,2,1280).mean(axis=2)
        for kind,sequence in zip(video.MOTION_STYLE['cycle'],rows):
            positions=[]
            for row in sequence:
                gradient=np.maximum(0,np.diff(row));gradient[:420]=0;gradient[530:]=0
                positions.append(float(np.sum(np.arange(len(gradient))*gradient)/gradient.sum()))
            changes=np.diff(positions)
            ideal=np.linspace(positions[0],positions[-1],144)
            self.assertLess(float(np.sqrt(np.mean((positions-ideal)**2))),.30,kind)
            # Allow the intended per-frame travel plus subpixel codec noise.
            step=abs(positions[-1]-positions[0])/143
            self.assertLess(float(np.max(np.abs(changes))),step+.5,kind)
            direction=1 if kind in ('zoom_out','slide_from_left') else -1
            self.assertLessEqual(int(np.sum(changes*direction<-.08)),5,kind)

    def test_crossfade_starts_at_saved_cut_without_shifting_audio_or_frames(self):
        import numpy as np
        if not shutil.which('ffmpeg'):self.skipTest('FFmpeg not installed')
        state=video.timeline('test','Chapter 1')
        work=self.root/'dissolve';work.mkdir();video.render_to(state,work)
        data=subprocess.check_output(['ffmpeg','-nostdin','-v','error','-i',str(work/'chapter.mp4'),
            '-vf','crop=2:2:640:360,format=rgb24','-f','rawvideo','-'],timeout=30)
        colors=np.frombuffer(data,dtype=np.uint8).reshape(-1,4,3).mean(axis=1)
        self.assertEqual(len(colors),48)
        self.assertGreater(colors[23,0],220);self.assertLess(colors[23,2],30)
        for n in range(24,28):
            self.assertGreater(colors[n,0],20);self.assertGreater(colors[n,2],20)
        self.assertTrue(all(colors[n+1,2]>colors[n,2]+20 for n in range(23,28)))
        self.assertGreater(colors[28,2],220);self.assertLess(colors[28,0],30)
        self.assertEqual(state['beats'][0]['switches'],[1.0])

    def test_crossfade_short_holds_and_cross_beat_boundary(self):
        plan=video.motion_plan({'beats':[
            {'duration':1,'switches':[],'panels':[self.panels[0]]},
            {'duration':1,'switches':[2/24],'panels':self.panels}]})
        fades=video.transition_plan(plan)
        self.assertEqual(fades[0],{'start_frame':24,'frames':1})
        self.assertEqual(fades[1],{'start_frame':26,'frames':1})
        self.assertEqual(sum(p['frames'] for p in plan),48)
        video.save('test','Chapter 1','a'*64,1,[.6])
        before=video.timeline('test','Chapter 1')
        with patch.dict(video.TRANSITION_STYLE,{'frames':3}):after=video.timeline('test','Chapter 1')
        self.assertNotEqual(before['render_token'],after['render_token'])
        self.assertEqual(before['source_token'],after['source_token'])
        self.assertEqual(before['beats'][0]['switches'],after['beats'][0]['switches'])

    def test_motion_filter_handles_a_full_chapter(self):
        if not shutil.which('ffmpeg'):self.skipTest('FFmpeg not installed')
        panels=[dict(self.panels[0],id=f'p{i:03}-1') for i in range(116)]
        beat=dict(self.beats[0],duration=116,switches=list(range(1,116)),panels=panels)
        plan=video.motion_plan({'beats':[beat]})
        graph=self.root/'full-chapter.ffscript'
        graph.write_text(video.render_filter(plan),encoding='utf-8')
        self.assertGreater(graph.stat().st_size,32767)
        result=subprocess.run(['ffmpeg','-nostdin','-v','error','-i',self.panels[0]['_crop'],
            '-/filter_complex',str(graph),'-map','[visual]','-filter_complex_threads','1','-filter_threads','1','-threads','1','-frames:v','1','-f','null','-'],
            capture_output=True,text=True,timeout=30, encoding='utf-8', errors='replace')
        self.assertEqual(result.returncode,0,result.stderr[:900])

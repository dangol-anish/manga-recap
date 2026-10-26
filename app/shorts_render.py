"""Lightweight vertical Shorts compositor. No long-form MP4s are read."""
import json
import math
from pathlib import Path
import shutil
import subprocess
import time
import wave

from PIL import Image, ImageOps, ImageFilter, ImageEnhance, ImageDraw, ImageFont
from app import video, storage

WIDTH, HEIGHT, FPS = 720, 1280, 30
STYLE_VERSION = 1


def compose(crop, target):
    with Image.open(crop) as opened:
        image = opened.convert('RGB')
        canvas = ImageEnhance.Brightness(ImageOps.fit(image, (WIDTH, HEIGHT), Image.Resampling.LANCZOS)
                    .filter(ImageFilter.GaussianBlur(26))).enhance(.35)
        # Reserve space for captions and platform controls. Never crop the sharp panel.
        front = ImageOps.contain(image, (620, 840), Image.Resampling.LANCZOS)
        x, y = (WIDTH - front.width) // 2, 100 + (840 - front.height) // 2
        shadow = Image.new('L', (WIDTH, HEIGHT))
        ImageDraw.Draw(shadow).rounded_rectangle((x - 6, y + 10, x + front.width + 6, y + front.height + 14), radius=10, fill=210)
        canvas.paste((0, 0, 0), (0, 0, WIDTH, HEIGHT), shadow.filter(ImageFilter.GaussianBlur(20)))
        canvas.paste(front, (x, y)); canvas.save(target)


def caption_groups(words, offset, end):
    groups = []; chunk = []
    for word in words:
        if chunk and (len(chunk) >= 4 or len(' '.join(w['text'] for w in chunk + [word])) > 30):
            groups.append(chunk); chunk = []
        chunk.append(word)
    if chunk: groups.append(chunk)
    return [{'start': offset + group[0]['start'],
             'end': offset + groups[i + 1][0]['start'] if i + 1 < len(groups) else end,
             'text': ' '.join(w['text'] for w in group)} for i, group in enumerate(groups)]


def caption_image(text, path):
    canvas = Image.new('RGBA', (WIDTH, 210)); draw = ImageDraw.Draw(canvas)
    try: font = ImageFont.truetype('DejaVuSans.ttf', 42)
    except OSError: font = ImageFont.load_default(size=42)
    words = text.split(); lines = []; line = ''
    for word in words:
        trial = (line + ' ' + word).strip()
        if line and draw.textlength(trial, font=font) > 540:
            lines.append(line); line = word
        else: line = trial
    if line: lines.append(line)
    # Imported segments are bounded; handle a long single word without clipping.
    while any(draw.textlength(line, font=font) > 560 for line in lines) and font.size > 18:
        font = font.font_variant(size=font.size - 2)
    for i, line in enumerate(lines):
        x = (WIDTH - draw.textlength(line, font=font)) / 2
        draw.text((x, 20 + i * (font.size + 9)), line, font=font,
                  fill='#7de2d1' if i == len(lines) - 1 else '#fffafb', stroke_width=3, stroke_fill='#131515')
    canvas.save(path)


def execute(command, work, cancel, progress, duration):
    with (work / 'ffmpeg.log').open('w', encoding='utf-8') as log:
        process = subprocess.Popen(command, cwd=work, stdout=subprocess.DEVNULL, stderr=log)
        started = time.monotonic()
        try:
            while process.poll() is None:
                if cancel.wait(.25): raise InterruptedError()
                if time.monotonic() - started > 1800: raise TimeoutError('Short creation exceeded 30 minutes.')
                path = work / 'progress.txt'
                if path.is_file():
                    for line in path.read_text(encoding='utf-8').splitlines():
                        if line.startswith('out_time_us='):
                            try: progress(min(.99, int(line.split('=')[1]) / 1e6 / duration))
                            except ValueError: pass
            if process.returncode:
                raise ValueError('Could not create the short: ' + (work / 'ffmpeg.log').read_text(encoding='utf-8')[-600:])
        finally:
            if process.poll() is None:
                process.terminate()
                try: process.wait(5)
                except subprocess.TimeoutExpired: process.kill(); process.wait(5)


def render(project, short, work, cancel, progress):
    from app import shorts
    shots = []; cues = []; elapsed = 0; frames = []
    with wave.open(str(work / 'voice.wav'), 'wb') as track:
        track.setparams((1, 2, 24000, 0, 'NONE', 'not compressed'))
        for i, row in enumerate(short['segments']):
            if cancel.is_set(): raise InterruptedError()
            clip = short['clips'][str(i)]
            data = video.pcm(shorts.safe(shorts.output(project['id']) / clip['file']))
            duration = len(data) / 48000
            if abs(duration - clip['duration']) > .01: raise ValueError('A saved voice clip changed. Create the short again.')
            track.writeframes(data)
            image = work / f'panel{i:03d}.png'
            source = shorts.panel(project, row)
            crop = shorts.safe(storage.ROOT / source['crop'])
            if shorts.checksum(crop) != source['crop_sha256']: raise FileExistsError('A selected panel changed.')
            compose(crop, image); frames.append("file '" + image.name + "'")
            start = math.floor(elapsed * FPS + .5); end = math.floor((elapsed + duration) * FPS + .5)
            if end <= start: raise ValueError('A spoken segment is too short for a video frame.')
            # Fractional camera movement, stronger than the long-form defaults.
            kind = ('zoom_in', 'slide_from_left', 'zoom_out', 'slide_from_right')[i % 4]
            shots.append({'start_frame': start, 'frames': end - start, 'amount': min(.055, duration * .018), 'kind': kind})
            cues.extend(caption_groups(clip['words'], elapsed, elapsed + duration))
            elapsed += duration
    if cancel.is_set(): raise InterruptedError()
    (work / 'panels.txt').write_text('ffconcat version 1.0\n' + '\n'.join(frames) + '\n', encoding='utf-8')
    caption_lines = ['ffconcat version 1.0']; cursor = 0; count = 0
    def add_caption(text, duration):
        nonlocal count
        if duration <= 0: return
        path = work / f'caption{count:04d}.png'; caption_image(text, path)
        caption_lines.extend(["file '" + path.name + "'", f'duration {duration:.9f}']); count += 1
    for cue in cues:
        add_caption('', cue['start'] - cursor)
        add_caption(cue['text'], cue['end'] - cue['start']); cursor = cue['end']
    add_caption('', elapsed - cursor)
    caption_lines.append("file 'caption" + f'{count - 1:04d}' + ".png'")
    (work / 'captions.txt').write_text('\n'.join(caption_lines) + '\n', encoding='utf-8')
    storage.write_json(work / 'captions.json', cues)
    graph = '[0:v]' + video.motion_filter(shots, width=WIDTH, height=HEIGHT, fps=FPS) + '[visual];[2:v]fps=' + str(FPS) + ',format=rgba[captions];[visual][captions]overlay=x=0:y=980:eof_action=pass:format=auto[out]'
    (work / 'visual.ffscript').write_text(graph, encoding='utf-8')
    command = ['ffmpeg', '-nostdin', '-hide_banner', '-loglevel', 'error', '-y',
        '-f', 'concat', '-safe', '1', '-i', 'panels.txt', '-i', 'voice.wav',
        '-f', 'concat', '-safe', '1', '-i', 'captions.txt', '-/filter_complex', 'visual.ffscript',
        '-map', '[out]', '-map', '1:a:0', '-c:v', 'libx264', '-preset', 'veryfast', '-crf', '21',
        '-pix_fmt', 'yuv420p', '-r', str(FPS), '-fps_mode', 'cfr', '-threads', '2', '-filter_complex_threads', '1',
        '-c:a', 'aac', '-b:a', '128k', '-t', str(elapsed), '-movflags', '+faststart', '-progress', 'progress.txt', 'short.mp4']
    execute(command, work, cancel, progress, elapsed)
    if cancel.is_set(): raise InterruptedError()
    probe = subprocess.run(['ffprobe', '-v', 'error', '-show_streams', '-show_format', '-of', 'json', str(work / 'short.mp4')],
                           capture_output=True, text=True, encoding='utf-8', errors='replace', check=True, timeout=30)
    info = json.loads(probe.stdout); streams = info['streams']; visual = next(s for s in streams if s['codec_type'] == 'video')
    if visual['width'] != WIDTH or visual['height'] != HEIGHT or {s['codec_type'] for s in streams} != {'video', 'audio'} or abs(float(info['format']['duration']) - elapsed) > .15:
        raise ValueError('The short failed picture/audio validation.')
    for stream in streams:
        if abs(float(stream['duration']) - elapsed) > .15: raise ValueError('Picture and audio lengths do not match.')
    return work / 'short.mp4'

"""CPU-only CTC alignment of a frozen English script; no speech is synthesized."""
import math
import re
import subprocess
import unicodedata
import numpy as np
from app import errors, storage, alignment

MODEL = 'facebook/wav2vec2-base-960h'
REVISION = '22aad52d435eb6dbaf354bdad9b0da84ce7d6156'


def number_text(n):
    small = 'zero one two three four five six seven eight nine ten eleven twelve thirteen fourteen fifteen sixteen seventeen eighteen nineteen'.split()
    tens = ['', '', 'twenty', 'thirty', 'forty', 'fifty', 'sixty', 'seventy', 'eighty', 'ninety']
    if n < 20: return small[n]
    if n < 100: return tens[n // 10] + (' ' + small[n % 10] if n % 10 else '')
    if n < 1000: return small[n // 100] + ' hundred' + (' ' + number_text(n % 100) if n % 100 else '')
    for value, name in ((10**9, 'billion'), (10**6, 'million'), (1000, 'thousand')):
        if n >= value: return number_text(n // value) + ' ' + name + (' ' + number_text(n % value) if n % value else '')


def normalized(text):
    text = unicodedata.normalize('NFKD', text).encode('ascii', 'ignore').decode().upper().replace('’', "'")
    if text.isdigit(): text = number_text(int(text)) if len(text) <= 9 else ' '.join(number_text(int(c)) for c in text)
    return re.sub("[^A-Z']", '', text.upper())


def ctc_words(logp, text, vocabulary, blank, seconds_per_frame, cancel=None):
    """Viterbi CTC path; bounded uint8 backtracking, no fabricated equal timings."""
    tokens = alignment.script_tokens(text); labels = []; owners = []
    for i, (_, start, end) in enumerate(tokens):
        word = normalized(text[start:end])
        if not word: raise ValueError('Local timing supports English narration. Could not align: ' + text[start:end])
        if labels: labels.append(vocabulary['|']); owners.append(None)
        for char in word:
            if char not in vocabulary: raise ValueError('Unsupported timing character: ' + char)
            labels.append(vocabulary[char]); owners.append(i)
    if not labels: raise ValueError('No spoken words to align.')
    target = np.full(2 * len(labels) + 1, blank, dtype=np.int32); target[1::2] = labels
    frames, states = len(logp), len(target)
    if frames * states > 550_000_000: raise ValueError('This section is too large for local timing on a small laptop. Use shorter sections.')
    previous = np.full(states, -np.inf, dtype=np.float32); previous[0] = 0
    parents = np.zeros((frames, states), dtype=np.uint8)
    skips = np.zeros(states, dtype=bool); skips[2:] = (target[2:] != blank) & (target[2:] != target[:-2])
    for t in range(frames):
        if cancel and t % 100 == 0 and cancel.is_set(): raise InterruptedError('Timing stopped.')
        best = previous.copy(); choice = np.zeros(states, dtype=np.uint8)
        forward = np.r_[-np.inf, previous[:-1]]; take = forward > best
        best[take] = forward[take]; choice[take] = 1
        forward = np.r_[-np.inf, -np.inf, previous[:-2]]; take = skips & (forward > best)
        best[take] = forward[take]; choice[take] = 2
        previous = best + logp[t, target]; parents[t] = choice
    state = states - 1 if previous[-1] >= previous[-2] else states - 2
    if not math.isfinite(float(previous[state])): raise ValueError('The recording does not contain enough speech to align the full script.')
    starts = np.full(len(tokens), frames, dtype=np.int32); ends = np.zeros(len(tokens), dtype=np.int32)
    scores = np.zeros(len(tokens)); counts = np.zeros(len(tokens))
    for t in range(frames - 1, -1, -1):
        if state % 2:
            index = (state - 1) // 2; owner = owners[index]
            if owner is not None:
                starts[owner] = min(starts[owner], t); ends[owner] = max(ends[owner], t + 1)
                scores[owner] += logp[t, target[state]]; counts[owner] += 1
        state -= int(parents[t, state])
    result = []
    for i, (_, a, b) in enumerate(tokens):
        confidence = float(scores[i] / max(1, counts[i]))
        if not counts[i] or confidence < -6.5:
            raise ValueError('Could not confidently match the spoken word “' + text[a:b] + '”. Audio is saved; retry local timing or review this section. No new Gemini request is needed.')
        result.append({'text': text[a:b], 'char_start': a, 'char_end': b, 'start': float(starts[i] * seconds_per_frame),
                       'end': float(ends[i] * seconds_per_frame), 'confidence': confidence})
    return result


class Aligner:
    def __init__(self):
        import torch
        from transformers import Wav2Vec2Processor, Wav2Vec2ForCTC
        directory = storage.ROOT / 'vendor' / 'speech-alignment'
        if directory.is_symlink(): raise ValueError('Unsafe speech timing model folder.')
        directory.mkdir(parents=True, exist_ok=True)
        self.torch = torch
        self.processor = Wav2Vec2Processor.from_pretrained(MODEL, revision=REVISION, cache_dir=str(directory))
        self.model = Wav2Vec2ForCTC.from_pretrained(MODEL, revision=REVISION, cache_dir=str(directory), use_safetensors=True).eval().cpu()
        self.vocabulary = self.processor.tokenizer.get_vocab()

    def align(self, path, text, cancel, progress=lambda message: None):
        result = subprocess.run(['ffmpeg', '-nostdin', '-v', 'error', '-i', str(path), '-f', 'f32le', '-ac', '1', '-ar', '16000', '-'],
                                capture_output=True, timeout=120)
        errors.checked_process(result,'Decode saved recording for local timing',path)
        samples = np.frombuffer(result.stdout, dtype='<f4'); chunks = []; torch = self.torch
        if len(samples) < 16000: raise ValueError('The recording is too short for this script.')
        old_threads = torch.get_num_threads(); torch.set_num_threads(2)
        try:
            for start in range(0, len(samples), 320000):
                if cancel.is_set(): raise InterruptedError('Timing stopped.')
                end = min(len(samples), start + 320000); left = max(0, start - 8000); right = min(len(samples), end + 8000)
                values = self.processor(samples[left:right].copy(), sampling_rate=16000, return_tensors='pt').input_values
                with torch.inference_mode(): emissions = self.model(values).logits[0].log_softmax(-1).numpy()
                first = round((start - left) / 320); count = math.ceil((end - start) / 320)
                cropped = emissions[first:first + count]
                # Pad the final sub-frame only; every earlier 20s chunk has context on both sides.
                if len(cropped) < count: cropped = np.concatenate([cropped, np.repeat(cropped[-1:], count - len(cropped), axis=0)])
                chunks.append(cropped); progress(f'Matching speech to panels: {round(end / len(samples) * 100)}%')
            return ctc_words(np.concatenate(chunks), text, self.vocabulary, self.model.config.pad_token_id, .02, cancel)
        finally: torch.set_num_threads(old_threads)

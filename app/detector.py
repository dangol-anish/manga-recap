"""Normal Magi v3 detection; retain only panel labels, never run associations/OCR."""
import importlib.util
import os
import sys
import signal
from pathlib import Path
import time
import threading

from app.storage import ROOT

MODEL_ID = 'ragavsachdeva/magiv3'
REVISION = '49c73a225122d53adbaa26d53868be81a57706e2'
PROMPT = 'Find all panels, texts, characters, and speech-bubble tails in the image.'


def cpu_threads():
    """Use a bounded Windows CPU budget; retain the existing Mac setting."""
    available = os.cpu_count() or 2
    default = min(12, max(2, available * 3 // 4)) if sys.platform == 'win32' else 2
    value = os.environ.get('MAGI_CPU_THREADS', str(default))
    try:
        requested = int(value)
    except ValueError:
        raise ValueError('MAGI_CPU_THREADS must be a positive whole number.') from None
    if requested < 1:
        raise ValueError('MAGI_CPU_THREADS must be a positive whole number.')
    return min(requested, available)

# All runtime caches belong to this project.
os.environ['HF_HOME'] = str(ROOT / '.cache' / 'huggingface')
os.environ['TORCH_HOME'] = str(ROOT / '.cache' / 'torch')
os.environ['MPLCONFIGDIR'] = str(ROOT / '.cache' / 'matplotlib')
os.environ['TOKENIZERS_PARALLELISM'] = 'false'
os.environ['HF_HUB_OFFLINE'] = '1'


class Detector:
    def __init__(self):
        import torch
        from transformers import AutoModelForCausalLM, AutoProcessor
        torch.set_num_threads(cpu_threads())
        self.torch = torch
        # MPS uses shared RAM; use a single half-precision page at a time.
        self.device = 'mps' if torch.backends.mps.is_available() else 'cpu'
        self.dtype = torch.float16 if self.device == 'mps' else torch.float32
        source = ROOT / 'vendor' / 'magiv3'
        if not (source / 'model.safetensors').exists():
            raise RuntimeError('Magi v3 is not installed. Run python -m scripts.install_model with this repository’s virtual-environment Python. See the setup guide.')
        started = time.perf_counter()
        self.processor = AutoProcessor.from_pretrained(source, trust_remote_code=True, local_files_only=True)
        self.model = AutoModelForCausalLM.from_pretrained(
            source, trust_remote_code=True, local_files_only=True,
            torch_dtype=self.dtype, attn_implementation='eager',
        ).to(self.device).eval()
        spec = importlib.util.spec_from_file_location('official_magi_utils', source / 'utils.py')
        self.utils = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.utils)
        self.load_seconds = time.perf_counter() - started
        print(f'Magi v3 {REVISION}, device={self.device}, cpu_threads={torch.get_num_threads()}, load={self.load_seconds:.2f}s', flush=True)

    def detect(self, image, direction):
        torch = self.torch
        inputs = self.processor(
            batch_input_text=[PROMPT], batch_images=[image],
            batch_input_list_of_list_of_bboxes=[[]], padding=True,
            truncation=True, max_input_length_including_image_tokens=1024,
            max_output_length=1024, return_tensors='pt',
            dtype=self.dtype, device=self.device,
        )
        with torch.inference_mode():
            ids = self.model.generate(
                input_ids=inputs['input_ids'], pixel_values=inputs['pixel_values'],
                max_new_tokens=1024, do_sample=False, num_beams=3,
            )
        if ids.shape[-1] >= 1024 and ids[0, -1].item() != self.processor.tokenizer.eos_token_id:
            raise RuntimeError('Panel generation exceeded the token limit; no partial JSON saved.')
        texts, groups, spans = self.processor.postprocess_output(ids.clone(), [image])
        boxes = retain_panels(texts[0], groups[0], spans[0])
        # Discard all raw mixed detection output immediately. It is never persisted.
        del texts, groups, spans, ids, inputs
        width, height = image.size
        from app.validation import panel_errors
        if panel_errors(boxes, width, height):
            return boxes  # Caller flags the page; invalid boxes never reach the sorter.
        # Official sorter is RTL. Mirror coordinates solely for LTR ordering.
        ordering_boxes = boxes if direction == 'rtl' else [[width-x2, y1, width-x1, y2] for x1,y1,x2,y2 in boxes]
        # Bound pathological overlapping layouts in the official graph sorter.
        def ordering_timeout(signum, frame):
            raise TimeoutError('Magi panel ordering exceeded 20 seconds; no JSON saved.')
        has_alarm = hasattr(signal, 'SIGALRM')
        timer = None
        if has_alarm:
            previous_handler = signal.signal(signal.SIGALRM, ordering_timeout)
            signal.alarm(20)
        else:
            # Windows has no SIGALRM. A watchdog ends only the isolated worker
            # if official ordering hangs; previously committed pages remain safe.
            def abort_ordering():
                print('Panel ordering exceeded 20 seconds; stopping worker without a partial page.', flush=True)
                os._exit(124)
            timer = threading.Timer(20, abort_ordering)
            timer.daemon = True
            timer.start()
        try:
            order = self.utils.sort_panels(ordering_boxes) if boxes else []
        finally:
            if has_alarm:
                signal.alarm(0)
                signal.signal(signal.SIGALRM, previous_handler)
            elif timer is not None:
                timer.cancel()
        if sorted(order) != list(range(len(boxes))):
            raise RuntimeError('Invalid panel reading order.')
        return [boxes[i] for i in order]


def retain_panels(text, groups, spans):
    """Match the official postprocessor's category position, without retaining other boxes."""
    boxes = []
    for (_, end), group in zip(spans, groups):
        if text[end:end + 7] != '<panel>':
            continue
        for coordinates in group:
            boxes.append(list(map(float, coordinates)))
    return boxes

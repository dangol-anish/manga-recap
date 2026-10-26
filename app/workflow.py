"""Read-only chapter readiness, using the same freshness checks as each stage."""
from urllib.parse import urlencode

from fastapi import APIRouter, HTTPException
from app import detection, downloader, errors, renumbering, storage
from app import panel_preparation as panels, narration_stage5 as story
from app import speech_stage6 as speech, video_stage7 as cinema
from app.read_cache import read_snapshot

router = APIRouter(prefix='/api/workflow')
STEPS = ((1, 'Images', '/'), (2, 'Numbering', '/renumbering'),
         (3, 'Detection', '/detection'), (4, 'Prepared panels', '/panels'),
         (5, 'Narration', '/story'), (6, 'Audio', '/speech'), (7, 'Video', '/cinema'))


def catalog():
    return [{'id': m['id'], 'name': m['name'], 'chapters':
             [{'id': c['id'], 'name': c['name']} for c in m['chapters']]}
            for m in detection.catalog()['manga']]


def chapter_status(mid, ident):
    group = next((m for m in detection.catalog()['manga'] if m['id'] == mid), None)
    item = next((c for c in group['chapters'] if c['id'] == ident), None) if group else None
    if item is None:
        raise HTTPException(404, 'Chapter folder was not found. Refresh chapter progress.')
    chapter = {**item, 'manga_id': mid}
    query = urlencode({'manga': mid, 'chapter': item['name'], 'chapter_id': ident})
    steps = [{'stage': n, 'name': label, 'state': 'waiting', 'detail': 'Finish the preceding step first.',
              'url': path + '?' + query} for n, label, path in STEPS]
    def done(n, detail):
        steps[n-1].update(state='ready', detail=detail)
    def stop(n, detail):
        steps[n-1].update(state='next', detail=detail)
    stage = 1
    try:
        folder = renumbering.folder_path(item['folder'])
        files = renumbering.images(folder)
        done(1, f'{len(files)} images available. Check unwanted images yourself before renumbering.')
        owner = storage.read_json(folder / '.download-owner.json', {})
        download = None
        if owner.get('job_id') and not renumbering.marker(folder):
            job = storage.read_json(downloader.record_path('jobs', owner['job_id']), {})
            download = next((c for c in job.get('chapters', []) if c['folder'] == item['folder']), None)
        if download and download.get('status') != 'complete':
            stop(1, 'This chapter download is unfinished. Resume or retry it in Stage 1 before continuing.')
        elif item['pending']:
            stop(2, 'Renumbering was interrupted. Continue it in Stage 2; your images are kept.')
        elif not files:
            stage = 1
            stop(1, 'No images are available. Finish downloading this chapter first.')
        else:
            stage = 2
            numbered = all(p.stem == f'{i:04d}' for i, p in enumerate(files))
            steps[1].update(state='ready' if numbered else 'next', detail=(
                'Numbered from 0000 with no gaps. This does not confirm unwanted images were removed.'
                if numbered else 'Check for unwanted images, then renumber the remaining images from 0000.'))
            # Existing downstream work remains usable even if it predates renumbering.
            report = storage.read_json(detection.output(chapter) / 'report.json', {})
            stage = 3
            if report.get('status') != 'complete' or {r['page'] for r in report.get('page_results', [])} != {p.name for p in files}:
                stop(3 if numbered else 2, 'Run detection for all current chapter images.' if numbered else steps[1]['detail'])
                if numbered and not detection.ready()['ready']:
                    stop(3, 'Panel detection is not installed or validated yet. Complete the model setup before starting Stage 3. See the installation guide.')
            else:
                info, current_files, report = panels.context(ident)
                for page in current_files:
                    panels.binding(info, page, report)
                flagged = sum(r['status'] != 'valid' for r in report['page_results'])
                done(3, f'{len(files)} pages checked; {flagged} flagged. Preparation keeps flagged pages available for review.')
                stage = 4
                batches = story.batches(mid, item['name'])
                done(4, f'{len(files)} labeled pages and their crops are current.')
                stage = 5
                narration = story.status(mid, item['name'])
                missing = [b for b in narration['batches'] if not b['imported']]
                if missing:
                    first = missing[0]
                    stop(5, f'{len(narration["batches"])-len(missing)}/{len(narration["batches"])} batches imported. '
                         + (first['blocked_reason'] if not first['ready'] else f'Import {first["id"]}'
                            + (' again because its saved reply is outdated.' if first.get('stale') else ' next.')))
                elif not batches:
                    stop(5, 'No narration batches are available for these pages.')
                else:
                    done(5, f'All {len(batches)} batches are imported and current.')
                    stage = 6
                    audio = speech.status(mid, item['name'])
                    if audio['ready'] != audio['spoken']:
                        stop(6, f'{audio["ready"]}/{audio["spoken"]} spoken beats ready. Create or resume audio in Stage 6.')
                    else:
                        done(6, f'{audio["ready"]} spoken beats ready.' if audio['spoken'] else 'Silent chapter; no speech is needed.')
                        stage = 7
                        video = cinema.status(mid, item['name'])
                        if video['ready']:
                            done(7, 'Your current video is ready to watch and save.')
                        else:
                            job = video.get('job') or {}
                            stop(7, job.get('message') if job.get('status') in {'running', 'failed', 'interrupted'}
                                 else 'Create the video from your current audio and panel timing.')
    except (ValueError, OSError, KeyError, TypeError) as exc:
        if isinstance(exc, (ValueError, FileExistsError)) and not isinstance(exc, errors.SavedDataError):
            stop(stage, errors.clean(exc))
        else:
            detail = errors.describe(exc, 'workflow', 'Check chapter progress', item['name'])
            stop(stage, detail['reason'] + ' ' + detail['action'] + ' Reference: ' + detail['reference'])
    # Numbering is a recommendation for old chapters with current detections.
    if steps[2]['state'] == 'ready' and steps[1]['state'] == 'next':
        steps[1].update(state='optional', detail='Existing detection uses these filenames. Renumber only if you intend to update later work.')
    next_step = next((s for s in steps if s['state'] == 'next'), steps[-1])
    return {'manga_id': mid, 'manga': group['name'], 'chapter': item['name'], 'steps': steps,
            'complete': steps[-1]['state'] == 'ready', 'next': next_step,
            'note': 'Checking progress never starts processing. Refresh after completing a step.'}


@router.get('/catalog')
def catalog_api():
    with read_snapshot():
        return catalog()


@router.get('/{mid}/{ident}')
def status_api(mid: str, ident: str):
    with read_snapshot():
        return chapter_status(mid, ident)

"""Authenticated project console and configuration APIs shared by phone and web."""
import asyncio
import json
from pathlib import Path


def install_routes(service, root, authenticate, production, store, commit, reload):
    from fastapi import Depends, HTTPException, Request
    from fastapi.responses import FileResponse, HTMLResponse
    from pydantic import ValidationError
    from autonomy import Automation, Schedule
    from resources import ResourceStore, MAX_BUNDLE, MAX_FILE
    from youtube import YouTube

    automation, youtube, resources = Automation(store), YouTube(store), ResourceStore(root)
    mutation_lock = asyncio.Lock()

    @service.get('/studio')
    async def console():
        file = Path(__file__).parent / 'studio.html'
        return HTMLResponse((file if file.exists() else Path('/opt/studio.html')).read_text())

    @service.get('/settings', dependencies=[Depends(authenticate)])
    async def settings():
        def read():
            return {'schedule': automation.config(), 'history': automation.history(),
                    'lastRun': store.get('last-run'), 'resources': resources.list(),
                    'youtube': youtube.status(), 'active': production.active}
        return await asyncio.to_thread(read)

    @service.put('/schedule', dependencies=[Depends(authenticate)])
    async def schedule(request: Request):
        try:
            body = await request.json()
            config = Schedule.model_validate(body)
            if config.publish and not await asyncio.to_thread(lambda: youtube.status()['connected']):
                raise ValueError('Connect YouTube before enabling automatic publishing')
            available = {r['id'] for r in resources.list()}
            if not set(config.resources).issubset(available):
                raise ValueError('A selected reference folder no longer exists')
            return await asyncio.to_thread(automation.save, config.model_dump())
        except (ValueError, ValidationError) as error:
            raise HTTPException(422, str(error)[:500]) from None

    @service.post('/resources', dependencies=[Depends(authenticate)])
    async def upload(request: Request):
        length = request.headers.get('content-length', '')
        if not length.isdigit() or int(length) > MAX_BUNDLE + 1024 * 1024:
            raise HTTPException(413, 'Upload at most 100 MB in one request')
        async with mutation_lock:
            if await asyncio.to_thread(lambda: production.active):
                raise HTTPException(409, 'Wait for the current film before changing reference folders')
            try:
                async with request.form(max_files=100, max_fields=10) as form:
                    files = form.getlist('files')
                    if not files:
                        raise ValueError('Choose files or a folder ZIP first')
                    if form.get('rights') != 'true':
                        raise ValueError('Confirm that you may use the uploaded materials')
                    data, total = [], 0
                    for file in files:
                        if not hasattr(file, 'read'):
                            raise ValueError('Invalid file upload')
                        content = await file.read(MAX_FILE + 1)
                        total += len(content)
                        if len(content) > MAX_FILE or total > MAX_BUNDLE:
                            raise ValueError('Use files up to 35 MB and a total up to 100 MB')
                        data.append((file.filename, content))
                    result = await asyncio.to_thread(resources.ingest, data, True)
                await asyncio.to_thread(commit)
                return result
            except (ValueError, OSError, KeyError) as error:
                raise HTTPException(422, str(error)[:400]) from None

    @service.post('/youtube/client', dependencies=[Depends(authenticate)])
    async def youtube_client(request: Request):
        try:
            raw = await request.body()
            if len(raw) > 12000:
                raise ValueError('OAuth client JSON is too large')
            return await asyncio.to_thread(youtube.configure, json.loads(raw))
        except (ValueError, AttributeError) as error:
            raise HTTPException(422, str(error)[:400]) from None

    @service.post('/youtube/connect', dependencies=[Depends(authenticate)])
    async def youtube_connect(request: Request):
        try:
            return await asyncio.to_thread(youtube.begin, str(request.base_url).rstrip('/') + '/youtube/callback')
        except ValueError as error:
            raise HTTPException(422, str(error)) from None

    @service.get('/youtube/callback')
    async def youtube_callback(state: str = '', code: str = '', error: str = ''):
        if error:
            return HTMLResponse('<h1>YouTube connection was cancelled</h1><p>Return to ShortsStudio. No channel was connected.</p>', status_code=400)
        try:
            await asyncio.to_thread(youtube.complete, state, code)
            return HTMLResponse('<h1>YouTube connected</h1><p>Return to ShortsStudio to choose visibility and enable publishing.</p><a href="/studio">Return to Studio</a>')
        except Exception:
            return HTMLResponse('<h1>YouTube connection did not finish</h1><p>Return to Studio and start a new connection attempt.</p>', status_code=400)

    @service.delete('/youtube/connection', dependencies=[Depends(authenticate)])
    async def youtube_disconnect():
        saved = automation.config()['settings']
        saved['publish'] = False
        await asyncio.to_thread(automation.save, saved)
        return await asyncio.to_thread(youtube.disconnect)

    @service.get('/productions/{job_id}/sources', dependencies=[Depends(authenticate)])
    async def sources(job_id: str):
        try:
            await asyncio.to_thread(production.status, job_id)
            if hasattr(production, 'prepare_download'):
                await production.prepare_download()
            path = production.root / job_id / 'sources.json'
            if not path.is_file():
                raise FileNotFoundError()
            return FileResponse(path, media_type='application/json', filename='ShortsStudio-sources.json')
        except (FileNotFoundError, ValueError):
            raise HTTPException(404, 'Sources are not available for this film') from None

    @service.post('/productions/{job_id}/publish', dependencies=[Depends(authenticate)])
    async def publish(job_id: str, request: Request):
        try:
            body = await request.json()
            privacy = body.get('privacy', 'private')
            if privacy not in ('private', 'unlisted', 'public'):
                raise ValueError('Invalid visibility')
            state = await asyncio.to_thread(production.status, job_id)
            if state['state'] != 'ready':
                raise ValueError('Wait for this film to finish')
            if hasattr(production, 'prepare_download'):
                await production.prepare_download()
            folder = production.root / job_id
            return await asyncio.to_thread(youtube.upload, job_id, folder / 'video.mp4',
                                          json.loads((folder / 'design.json').read_text()), privacy)
        except (ValueError, FileNotFoundError, RuntimeError) as error:
            raise HTTPException(409, str(error)[:400]) from None

    return automation, youtube, resources

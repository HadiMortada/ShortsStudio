"""Paste this entire file into Modal Playground and Run once to deploy.

No API key. The official CLI owns its credentials in a private Modal Volume.
"""
import modal

app = modal.App('shorts-studio-setup')


def director_model():
    from typing import Literal, Annotated
    from pydantic import BaseModel, ConfigDict, Field, StringConstraints, model_validator
    Color = Annotated[str,StringConstraints(pattern=r'^#[0-9a-fA-F]{6}$')]

    class Strict(BaseModel):
        model_config = ConfigDict(extra='forbid',allow_inf_nan=False)

    class Frame(Strict):
        t: float = Field(ge=0,le=1)
        x: float = Field(ge=-1,le=2)
        y: float = Field(ge=-1,le=2)
        scale: float = Field(default=1,ge=0,le=4)
        rotation: float = Field(default=0,ge=-720,le=720)
        opacity: float = Field(default=1,ge=0,le=1)
        easing: Literal['linear','smooth','out','spring'] = 'smooth'

    class Point(Strict):
        x: float = Field(ge=0,le=1)
        y: float = Field(ge=0,le=1)

    class Layer(Strict):
        kind: Literal['text','rect','circle','line','polygon']
        text: str = Field(default='',max_length=240)
        color: Color
        width: float = Field(ge=.001,le=2)
        height: float = Field(ge=.001,le=2)
        fontSize: float = Field(default=36,ge=10,le=100)
        bold: bool = True
        align: Literal['left','center','right'] = 'center'
        font: Literal['sans','serif','mono'] = 'sans'
        stroke: float = Field(default=0,ge=0,le=16)
        radius: float = Field(default=0,ge=0,le=100)
        points: list[Point] = Field(default_factory=list,max_length=16)
        frames: list[Frame] = Field(min_length=2,max_length=8)

        @model_validator(mode='after')
        def usable(self):
            if self.kind=='text' and not self.text.strip(): raise ValueError('Text layer needs text')
            if self.kind=='polygon' and len(self.points)<3: raise ValueError('Polygon needs at least three points')
            times=[f.t for f in self.frames]
            if times[0]!=0 or times[-1]!=1 or any(a>=b for a,b in zip(times,times[1:])):
                raise ValueError('Layer keyframes must start at 0, end at 1 and increase strictly')
            return self

    class Scene(Strict):
        duration: float = Field(ge=.5,le=30)
        background: list[Color] = Field(min_length=2,max_length=2)
        layers: list[Layer] = Field(min_length=1,max_length=12)

    class Plan(Strict):
        version: Literal[1]
        title: str = Field(min_length=1,max_length=120)
        direction: str = Field(min_length=1,max_length=500)
        language: str = Field(min_length=2,max_length=10,pattern=r'^[a-z]{2,3}(?:-[A-Za-z]{2,4})?$')
        narration: str = Field(min_length=1,max_length=3600)
        scenes: list[Scene] = Field(min_length=2,max_length=8)

        @model_validator(mode='after')
        def readable(self):
            if not all(v.strip() for v in (self.title,self.direction,self.narration)):
                raise ValueError('Title, creative direction and narration cannot be blank')
            return self

    return Plan


class PhoneLogin:
    """A bounded CLI session owned by the server, not a phone connection."""
    def __init__(self, binary, env, lock, commit):
        self.binary, self.env, self.lock, self.commit = binary, env, lock, commit
        self.task = self.process = self.master = None
        self.session_id = ''
        self.raw = self.submitted = ''
        self.message = 'Start official Claude login.'
        self.deadline = 0

    def snapshot(self):
        import re
        import time
        # Clean the accumulated buffer so split terminal escape sequences work too.
        output = re.sub(r'\x1b\][^\x07\x1b]*(?:\x07|\x1b\\)', '', self.raw)
        output = re.sub(r'\x1b\[[0-?]*[ -/]*[@-~]', '', output)
        if self.submitted:
            output = output.replace(self.submitted, '[code submitted]')
        return {'sessionId':self.session_id, 'active':bool(self.task and not self.task.done()),
            'output':output, 'message':self.message,
            'expiresIn':max(0, int(self.deadline - time.monotonic()))}

    async def start(self):
        import asyncio
        import os
        import pty
        import secrets
        import termios
        import time
        if self.task and not self.task.done():
            return self.snapshot()
        if self.lock.locked():
            raise RuntimeError('Claude is busy. Wait for the current request.')
        await self.lock.acquire()
        self.session_id = secrets.token_hex(16)
        self.raw = self.submitted = ''
        self.message = 'Opening official Claude login. You have five minutes to authorize.'
        self.deadline = time.monotonic() + 300
        master, slave = pty.openpty()
        try:
            attrs = termios.tcgetattr(slave)
            attrs[3] &= ~termios.ECHO
            termios.tcsetattr(slave, termios.TCSANOW, attrs)
            self.process = await asyncio.create_subprocess_exec(self.binary,'auth','login',env=self.env,
                stdin=slave,stdout=slave,stderr=slave,start_new_session=True)
            os.set_blocking(master,False)
        except BaseException:
            os.close(master)
            self.lock.release()
            raise
        finally:
            os.close(slave)
        self.master = master
        self.task = asyncio.create_task(self.supervise())
        # Transfer ownership before yielding: a cancelled HTTP request must not
        # close the terminal or release the lock owned by the supervisor.
        await asyncio.sleep(0)
        return self.snapshot()

    async def supervise(self):
        import asyncio
        import os
        import signal
        import time
        try:
            while time.monotonic() < self.deadline:
                try:
                    data = os.read(self.master,16384)
                    if not data:
                        break
                    self.raw = (self.raw + data.decode(errors='replace'))[-50000:]
                except BlockingIOError:
                    if self.process.returncode is not None:
                        break
                    await asyncio.sleep(.05)
                except OSError:
                    break
            else:
                self.message = 'Login expired. Start official Claude login again for a new code.'
            if not self.message.startswith('Login expired'):
                self.message = 'Login process finished. If not signed in, start again for a new code.'
        finally:
            try:
                if self.process.returncode is None:
                    try:
                        os.killpg(self.process.pid,signal.SIGKILL)
                    except ProcessLookupError:
                        pass
                await self.process.wait()
                os.close(self.master)
                self.master = None
                await asyncio.to_thread(self.commit)
            finally:
                self.lock.release()

    def send(self, session_id, code):
        import os
        if session_id != self.session_id or not self.task or self.task.done() or self.master is None or self.process.returncode is not None:
            raise RuntimeError('Login session ended. Start official Claude login again, then use its new code.')
        self.submitted = code
        try:
            # A PTY Enter key is carriage return, including when the CLI uses raw mode.
            payload = (code+'\r').encode()
            if os.write(self.master,payload) != len(payload):
                raise OSError('Incomplete terminal write')
        except OSError:
            raise RuntimeError('Could not send the code. Start official Claude login again.') from None
        self.message = 'Code sent to Claude. Waiting for verification…'

    async def close(self):
        import asyncio
        if self.task and not self.task.done():
            self.task.cancel()
            await asyncio.gather(self.task,return_exceptions=True)

PAGE = r'''<!doctype html><html><head><meta name="viewport" content="width=device-width,initial-scale=1"><meta name="referrer" content="no-referrer"><title>Connect Shorts Studio</title><style>
[hidden]{display:none!important}body{background:#0b0c14;color:#eee;font:16px system-ui;max-width:650px;margin:24px auto;padding:18px}button,input,a{font:inherit}button,.button{background:#a38bff;color:#101018;border:0;border-radius:12px;padding:14px;display:inline-block;margin:8px 0;text-decoration:none}input{box-sizing:border-box;width:100%;padding:14px;background:#202130;color:white;border:1px solid #626078;border-radius:12px}pre{white-space:pre-wrap;overflow-wrap:anywhere;background:#171925;padding:16px;border-radius:12px;font:13px monospace}a{color:#b6a6ff}#pair{color:#101018}small{color:#aab}h1{font-size:26px}</style></head><body>
<h1>Connect Claude to Shorts Studio</h1><p id="status" role="status">Checking your private connection…</p>
<a id="pair" class="button" hidden>Connect APK</a><br>
<button id="login">Start official Claude login</button><button id="refresh">Check login</button>
<p>Open the official authorization link below in your browser. Copy the complete code from Claude, return here, paste it, then tap Send code. Your login stays active for five minutes while you switch apps.</p>
<p><small>If your installed APK does not open the authorization button, open this same private setup link in Chrome and complete login there.</small></p>
<div id="links"></div><pre id="output"></pre>
<label for="code">Code copied from Claude</label><input id="code" placeholder="Official one-time code, if requested" autocomplete="off" autocapitalize="none" autocorrect="off" spellcheck="false"><button id="send">Send code to Claude</button>
<p id="notice" role="status" aria-live="polite"></p>
<p><small>This is a restricted terminal running only claude auth login. Keep this setup link private. Video rendering remains on your phone.</small></p>
<script>
const key=new URLSearchParams(location.hash.slice(1)).get('key')||sessionStorage.getItem('studio-key');
if(key){sessionStorage.setItem('studio-key',key);history.replaceState(null,'',location.pathname)}
const status=document.getElementById('status'),output=document.getElementById('output'),notice=document.getElementById('notice');
const send=document.getElementById('send'),start=document.getElementById('login');
let sessionId='',pollTimer,busy=false,checking=false,active=false;
if(key){const p=document.getElementById('pair');p.hidden=false;p.href='shortsstudio://connect?endpoint='+encodeURIComponent(location.origin)+'&key='+encodeURIComponent(key)}
async function api(path,body){if(!key)throw new Error('Missing private connection key. Open the complete setup link ending in #key=…');const options={headers:{Authorization:'Bearer '+key},signal:AbortSignal.timeout(30000)};
if(body!==undefined){options.method='POST';options.headers['Content-Type']='application/json';options.body=JSON.stringify(body)}
const r=await fetch(path,options);const j=await r.json();if(!r.ok)throw new Error(j.detail||'Request failed ('+r.status+')');return j}
function render(j){sessionId=j.sessionId;active=j.active;output.textContent=j.output;start.textContent=active?'Resume current login':'Start official Claude login';
const links=document.getElementById('links');links.replaceChildren();const seen=new Set();
for(const u of j.output.match(/https:\/\/[^\s\u001b]+/g)||[]){try{const url=new URL(u),h=url.hostname;
if(!['claude.ai','claude.com'].some(d=>h===d||h.endsWith('.'+d))||seen.has(u))continue;
seen.add(u);const a=document.createElement('a');a.textContent='Open official Claude authorization ↗';a.href=u;a.target='_blank';a.rel='noopener noreferrer';links.append(a,document.createElement('br'))}catch(e){}}
return j}
async function check(){if(checking)return;checking=true;clearTimeout(pollTimer);try{
const j=render(await api('/login'));
if(j.active){status.textContent='Login active. Complete Claude authorization, then send its code here. '+Math.ceil(j.expiresIn/60)+' minute(s) remaining.'}
else{const s=await api('/status');status.textContent=s.loggedIn?'Claude signed in. Return to the APK.':j.sessionId?j.message:'Server paired; Claude is not signed in yet.'}
}catch(e){status.textContent='Connection unavailable: '+e.message}finally{checking=false;if(active)pollTimer=setTimeout(check,1500)}}
document.getElementById('refresh').onclick=check;
start.onclick=async()=>{start.disabled=true;notice.textContent='Starting login…';try{const j=render(await api('/login/start',{}));notice.textContent=j.message;await check()}catch(e){notice.textContent=e.message}finally{start.disabled=false}};
send.onclick=async()=>{if(busy)return;const c=document.getElementById('code'),value=c.value.trim();
if(!value){notice.textContent='Paste the complete code copied from Claude first.';return}
if(!sessionId){notice.textContent='Start official Claude login first, then use its new code.';return}
busy=true;send.disabled=true;send.textContent='Sending code…';notice.textContent='Sending code to Claude…';
try{const j=await api('/login/code',{sessionId,code:value});if(!j.accepted)throw new Error('Claude did not receive the code. Try again.');c.value='';notice.textContent=j.message;await check()}
catch(e){notice.textContent=e.message}finally{busy=false;send.disabled=false;send.textContent='Send code to Claude'}};
document.addEventListener('visibilitychange',()=>{if(key&&document.visibilityState==='visible')check()});
if(key){check()}else{status.textContent='This link is incomplete. Open the private setup link containing #key=… to connect Shorts Studio.';start.disabled=true;send.disabled=true;document.getElementById('refresh').disabled=true;document.getElementById('code').disabled=true;}
</script></body></html>'''


async def run_cli(args, *, env, stdin=None, timeout=180):
    import asyncio
    import os
    import signal
    process = await asyncio.create_subprocess_exec(
        *args, env=env, stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
        start_new_session=True)
    try:
        out, err = await asyncio.wait_for(process.communicate(stdin.encode() if stdin else None), timeout)
        return process.returncode, out.decode(errors='replace'), err.decode(errors='replace')
    finally:
        if process.returncode is None:
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            await process.wait()


def create_service(root=None, *, run=run_cli, commit=lambda: None):
    import asyncio
    import hmac
    import json
    import os
    import re
    from contextlib import asynccontextmanager
    from pathlib import Path
    from fastapi import FastAPI, Depends, Header, HTTPException
    from fastapi.responses import HTMLResponse
    from pydantic import BaseModel, Field, field_validator

    root = Path(root or '/studio')
    home = root / 'home'
    home.mkdir(parents=True, exist_ok=True, mode=0o700)
    os.chmod(home, 0o700)
    env = {k:v for k,v in os.environ.items() if k not in (
        'ANTHROPIC_API_KEY','ANTHROPIC_AUTH_TOKEN','ANTHROPIC_BASE_URL',
        'CLAUDE_CODE_OAUTH_TOKEN','CLAUDE_CODE_OAUTH_TOKEN_FILE_DESCRIPTOR',
        'CLAUDE_CODE_USE_BEDROCK','CLAUDE_CODE_USE_VERTEX','CLAUDE_CODE_USE_FOUNDRY','STUDIO_ACCESS')}
    env.update(HOME=str(home), TERM='xterm-256color', NO_COLOR='1')
    binary = '/root/.local/bin/claude'
    lock = asyncio.Lock()
    login = PhoneLogin(binary, env, lock, commit)
    @asynccontextmanager
    async def lifespan(_):
        try:
            yield
        finally:
            await login.close()
    service = FastAPI(docs_url=None, redoc_url=None, openapi_url=None, lifespan=lifespan)

    def authorized(value):
        key = os.environ.get('STUDIO_ACCESS','')
        return bool(key and value and hmac.compare_digest(value, key))

    def authenticate(authorization: str | None = Header(default=None)):
        if not os.environ.get('STUDIO_ACCESS'):
            raise HTTPException(503, 'Server access is not configured')
        if not authorization or not authorization.startswith('Bearer ') or not authorized(authorization[7:]):
            raise HTTPException(401, 'Open your private setup link to pair the APK')

    @service.middleware('http')
    async def headers(request, call_next):
        response = await call_next(request)
        response.headers.update({'Cache-Control':'no-store','Referrer-Policy':'no-referrer',
            'X-Content-Type-Options':'nosniff','X-Frame-Options':'DENY',
            'Content-Security-Policy':"default-src 'none'; script-src 'unsafe-inline'; style-src 'unsafe-inline'; connect-src 'self'; base-uri 'none'; frame-ancestors 'none'; form-action 'none'"})
        return response

    @service.get('/')
    async def page():
        return HTMLResponse(PAGE)

    async def logged_in():
        code, out, _ = await run([binary,'auth','status'],env=env,timeout=20)
        try:
            return code == 0 and json.loads(out).get('loggedIn') is True
        except (ValueError, AttributeError):
            return False

    @service.get('/status', dependencies=[Depends(authenticate)])
    async def status():
        if lock.locked():
            return {'loggedIn':False, 'busy':True}
        async with lock:
            return {'loggedIn':await logged_in(), 'busy':False}

    @service.get('/login', dependencies=[Depends(authenticate)])
    async def login_status():
        return login.snapshot()

    @service.post('/login/start', dependencies=[Depends(authenticate)])
    async def login_start():
        try:
            return await login.start()
        except RuntimeError as error:
            raise HTTPException(409,str(error)) from None

    class LoginCode(BaseModel):
        sessionId: str = Field(min_length=1,max_length=64)
        code: str = Field(min_length=1,max_length=1024)

        @field_validator('code')
        @classmethod
        def single_line(cls,value):
            value = value.strip()
            if not value or any(ord(c)<32 or ord(c)>126 for c in value):
                raise ValueError('Paste the complete one-line code copied from Claude')
            return value

    async def login_code(body, _=Depends(authenticate)):
        try:
            login.send(body.sessionId,body.code)
        except RuntimeError as error:
            raise HTTPException(409,str(error)) from None
        return {'accepted':True, 'message':'Code sent to Claude. Waiting for verification…'}

    login_code.__annotations__['body'] = LoginCode
    service.post('/login/code')(login_code)

    class Generate(BaseModel):
        topic: str = Field(min_length=1, max_length=500)

        @field_validator('topic')
        @classmethod
        def not_blank(cls, value):
            if not value.strip():
                raise ValueError('Enter a topic')
            return value.strip()

    # Avoid postponed annotations: FastAPI resolves this local model directly.
    async def generate(body, _=Depends(authenticate)):
        if lock.locked():
            raise HTTPException(409, 'Claude is busy; finish login or wait for your script')
        async with lock:
            if not await logged_in():
                raise HTTPException(409, 'Sign in to Claude from Accounts first')
            prompt = ('Return ONLY JSON with narration (a short factual 30-second narration) '
                      'and scenes (4 to 6 brief on-screen headings). No markdown. '
                      'Do not use tools or invent facts. Viewer topic follows:\n'+body.topic)
            try:
                code, out, _ = await run([binary,'-p','--output-format','text',
                    '--no-session-persistence'],env=env,stdin=prompt,timeout=180)
                if code:
                    raise HTTPException(502, 'Claude failed. Check your sign-in and subscription usage limits.')
                clean = re.sub(r'^```(?:json)?\s*|\s*```$', '', out.strip())
                plan = json.loads(clean)
                if not isinstance(plan.get('narration'),str) or not plan['narration'].strip() or not isinstance(plan.get('scenes'),list) or not plan['scenes'] or not all(isinstance(x,str) for x in plan['scenes']):
                    raise ValueError('Incomplete script')
                return {'text':json.dumps({'narration':plan['narration'][:4000], 'scenes':plan['scenes'][:8]})}
            except asyncio.TimeoutError:
                raise HTTPException(504, 'Claude took too long. Try again.')
            except (ValueError, AttributeError, TypeError):
                raise HTTPException(502, 'Claude returned an invalid script. Try again.')
            finally:
                await asyncio.to_thread(commit)

    generate.__annotations__['body'] = Generate
    service.post('/generate')(generate)

    class Brief(BaseModel):
        brief: str = Field(min_length=1,max_length=4000)
        seconds: int = Field(default=30,ge=15,le=60)

        @field_validator('brief')
        @classmethod
        def not_blank(cls,value):
            if not value.strip(): raise ValueError('Describe the motion graphic you want')
            return value.strip()

    async def direct(body, _=Depends(authenticate)):
        if lock.locked():
            raise HTTPException(409,'Claude is busy. Finish login or wait for your video design.')
        async with lock:
            if not await logged_in():
                raise HTTPException(409,'Sign in to Claude from Accounts first')
            Plan=director_model()
            prompt=('You are the creative director and motion designer of a finished portrait motion graphic. '
                'Act autonomously: choose the visual language, composition, typography, palette, pacing, '
                'scene transitions and every layer\'s animation keyframes. Do not ask the user to select a style. '
                'Create a bespoke composition for the brief, not the same title-card template for every topic. '
                'Return ONLY a JSON motion timeline matching this schema. No markdown. '
                'Use 3-6 scenes and around 3-7 layers per scene when appropriate; represent diagrams and icons '
                'with circles, rectangles, lines and polygons. Layer order is back to front. '
                'Coordinates x/y are normalized CENTER positions relative to a 432x768 portrait canvas. '
                'width/height are normalized against the full canvas. fontSize, stroke and radius are pixels '
                'on that reference canvas. Polygon points are normalized inside their layer box. '
                'Keyframe t runs from 0 to 1 within each scene, including both endpoints. '
                'The easing on the destination keyframe controls interpolation. Animate opacity/position/scale/rotation '
                'with purposeful staggered entrances and exits; use scene-end fades where useful. '
                'Keep text readable inside safe margins x=.08..92,y=.08..90; avoid overlaps and tiny text. '
                'Use visual metaphors specific to the brief. Review your composition and keyframes for readability '
                'and consistency before returning the finished timeline. No external files, URLs, code or assets. '
                'Write concise factual narration in the language requested by the brief; avoid invented statistics. '
                f'Total scene durations must sum to {body.seconds} seconds. Keep narration near '
                f'{body.seconds*2} words so it fits. Schema:\n'+json.dumps(Plan.model_json_schema())+
                '\nUSER CREATIVE BRIEF:\n'+body.brief)
            args=[binary,'-p','--model','claude-opus-5-5','--effort','high','--output-format','text',
                '--no-session-persistence']
            try:
                for attempt in range(2):
                    code,out,err=await run(args,env=env,stdin=prompt,timeout=240)
                    if code:
                        raise HTTPException(502,'Opus 5.5 could not complete the design. Check model access and your Claude usage limits; no other model was substituted.')
                    try:
                        if len(out)>180000: raise ValueError('Timeline is too large')
                        clean=re.sub(r'^```(?:json)?\s*|\s*```$','',out.strip())
                        plan=Plan.model_validate_json(clean)
                        if abs(sum(s.duration for s in plan.scenes)-body.seconds)>.5:
                            raise ValueError('Scene durations do not match the requested length')
                        return {'model':'claude-opus-5-5','plan':plan.model_dump()}
                    except ValueError:
                        if attempt:
                            raise HTTPException(502,'Claude returned an invalid motion timeline after repair. Try a clearer brief.') from None
                        prompt+='\nYour previous timeline was invalid. Correct it to the schema, use finite numbers, strictly ordered keyframes from 0 to 1, valid colors and the exact total duration. Return only the corrected JSON.\n'+out[:180000]
            except asyncio.TimeoutError:
                raise HTTPException(504,'Opus 5.5 took too long designing the video. Try again.') from None
            finally:
                await asyncio.to_thread(commit)

    direct.__annotations__['body']=Brief
    service.post('/direct')(direct)

    return service


def build_cloud(key):
    import sys
    cloud = modal.App('shorts-studio-cloud')
    volume = modal.Volume.from_name('shorts-studio-claude-home',create_if_missing=True)
    # Serialized functions must match the Python that runs Modal Playground.
    python_version = f'{sys.version_info[0]}.{sys.version_info[1]}'
    image = (modal.Image.debian_slim(python_version=python_version)
        .apt_install('curl','git','ca-certificates')
        .pip_install('fastapi[standard]>=0.115,<1')
        .run_commands('curl -fsSL https://claude.ai/install.sh -o /tmp/install-claude.sh',
                      'bash /tmp/install-claude.sh'))
    # Serialized factories retain references to helpers in this module. Unlike
    # ordinary Modal functions, their defining file is not mounted automatically.
    image = image.add_local_python_source(__name__)

    @cloud.function(image=image,serialized=True,memory=4096,timeout=600,
        max_containers=1,min_containers=0,scaledown_window=360,
        volumes={'/studio':volume},secrets=[modal.Secret.from_dict({'STUDIO_ACCESS':key})])
    @modal.concurrent(max_inputs=8)
    @modal.asgi_app()
    def web():
        return create_service(commit=volume.commit)

    return cloud,web


@app.local_entrypoint()
def deploy():
    import secrets
    # Hex avoids ambiguous O/0 and I/l when copying output from a phone screenshot.
    key = secrets.token_hex(24)
    cloud, web = build_cloud(key)
    # Do not overlap revisions that share the same official CLI credential Volume.
    cloud.deploy(strategy='recreate')
    print('\nOPEN THIS PRIVATE SETUP LINK ON YOUR PHONE:')
    print(web.get_web_url()+'/#key='+key)
    print('Tap Connect APK, confirm, then sign in to Claude from Accounts.')
    print('Re-running setup rotates the app connection key; pair the APK again.')


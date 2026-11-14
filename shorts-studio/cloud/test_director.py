import copy
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from fastapi.testclient import TestClient
from modal_bridge import create_service


def example_plan():
    return {'version':1,'title':'Ideas in motion','direction':'Warm editorial typography with orbiting circles',
        'language':'en','narration':'An idea becomes a story when its parts move together.',
        'scenes':[{'duration':7.5,'background':['#101020','#202040'],'layers':[
            {'kind':'text','text':'Ideas move','color':'#ffffff','width':.8,'height':.3,'fontSize':48,
             'frames':[{'t':0,'x':.5,'y':.6,'opacity':0},{'t':.2,'x':.5,'y':.4,'opacity':1},{'t':1,'x':.5,'y':.4,'opacity':0}]}]}
            for _ in range(2)]}


class DirectorTests(unittest.TestCase):
    def setUp(self):
        usage = patch('claude_usage.require_usage', return_value={'sessionPercent': 20, 'weeklyPercent': 30})
        self.usage = usage.start()
        self.addCleanup(usage.stop)
        self.secret=patch.dict(os.environ,{'STUDIO_ACCESS':'test-access-key'});self.secret.start()
        self.addCleanup(self.secret.stop)
        self.tmp=tempfile.TemporaryDirectory()
        self.calls=[]
        self.responses=[json.dumps(example_plan())]
        async def run(args,**kwargs):
            self.calls.append((args,kwargs))
            if 'status' in args:return 0,'{"loggedIn":true}',''
            return 0,self.responses.pop(0),''
        self.client=TestClient(create_service(Path(self.tmp.name),run=run))
        self.headers={'Authorization':'Bearer test-access-key'}

    def tearDown(self): self.tmp.cleanup()

    def direct(self):
        return self.client.post('/direct',headers=self.headers,json={'brief':'Introduce a new AI model. Choose the art direction yourself.','seconds':15})

    def test_opus_designs_the_actual_timeline(self):
        response=self.direct()
        self.assertEqual(response.status_code,200,response.text)
        self.assertEqual(response.json()['model'],'claude-opus-5-5')
        plan=response.json()['plan']
        self.assertEqual(plan['scenes'][0]['layers'][0]['frames'][1]['y'],.4)
        args,kwargs=self.calls[-1]
        self.assertEqual(args[args.index('--model')+1],'claude-opus-5-5')
        self.assertEqual(args[args.index('--effort')+1],'high')
        self.assertNotIn('--fallback-model',args)
        self.assertNotIn('--tools',args)
        self.assertNotIn('--disallowedTools',args)
        self.assertIn('Introduce a new AI model',kwargs['stdin'])
        self.assertIn('keyframes',kwargs['stdin'])

    def test_invalid_timeline_is_repaired_before_rendering(self):
        bad=example_plan();bad['scenes'][0]['layers'][0]['frames'][1]['x']=float('nan')
        self.responses=[json.dumps(bad),json.dumps(example_plan())]
        response=self.direct()
        self.assertEqual(response.status_code,200,response.text)
        self.assertEqual(len([a for a,k in self.calls if '-p' in a]),2)

    def test_bad_geometry_is_not_silently_replaced_with_a_template(self):
        bad=example_plan();bad['scenes'][0]['layers'][0]['kind']='arbitrary-code'
        self.responses=[json.dumps(bad),json.dumps(bad)]
        self.assertEqual(self.direct().status_code,502)

    def test_usage_gate_blocks_before_design(self):
        from claude_usage import UsageBlocked
        self.usage.side_effect = UsageBlocked('Video creation paused: usage could not be verified.')
        self.assertEqual(self.direct().status_code, 409)
        self.assertFalse(any('-p' in args for args, _ in self.calls))

    def test_director_requires_pairing(self):
        self.assertEqual(self.client.post('/direct',json={'brief':'Test','seconds':15}).status_code,401)
        self.assertEqual(self.calls,[])

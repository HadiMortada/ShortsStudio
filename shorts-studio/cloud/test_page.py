import json
import re
import subprocess
import unittest
from modal_bridge import PAGE


class PageTests(unittest.TestCase):
    def test_code_submission_has_acknowledgement_and_visible_failure(self):
        script = re.search(r'<script>(.*?)</script>', PAGE, re.S).group(1)
        harness = r'''
const vm=require('node:vm'),assert=require('node:assert/strict');
const nodes=new Map();
function node(){return {textContent:'',value:'',hidden:false,disabled:false,children:[],append(...x){this.children.push(...x)},replaceChildren(){this.children=[]}}}
const element=id=>{if(!nodes.has(id))nodes.set(id,node());return nodes.get(id)};
let requests=[],failure=false,active=true;
const document={getElementById:element,createElement:()=>node(),addEventListener(){},visibilityState:'visible'};
const session={sessionId:'session-1',active:true,message:'Waiting for code',output:'https://claude.com/oauth/authorize?state=test-state https://claude.com.evil.test/oauth',expiresIn:200};
const context={document,URL,URLSearchParams,sessionStorage:{getItem:()=> 'test-key',setItem(){}},
 location:{hash:'',origin:'https://test.modal.run',pathname:'/'},history:{replaceState(){}},
 setTimeout:()=>0,clearTimeout(){},AbortSignal,console,
 fetch:async(path,options={})=>{requests.push([path,options]);
   if(path==='/login/code')return {ok:!failure,status:failure?409:200,json:async()=>failure?{detail:'Login session ended. Start again.'}:{accepted:true,message:'Code sent to Claude. Waiting for verification…'}};
   return {ok:true,json:async()=>path==='/status'?{loggedIn:false,busy:active}:session};
 }};
vm.createContext(context);vm.runInContext(SCRIPT,context);
async function settle(){for(let i=0;i<8;i++)await Promise.resolve()}
(async()=>{
 await settle();
 assert.equal(element('links').children.filter(n=>n.href).length,1,'only official domain gets a link');
 assert.equal(element('links').children.find(n=>n.href).href,'https://claude.com/oauth/authorize?state=test-state');
 element('code').value='test-code#test-state'; failure=true;
 await element('send').onclick(); await settle();
 assert.equal(element('code').value,'test-code#test-state','failed submission preserves code');
 assert.match(element('notice').textContent,/session ended/i,'failure must be visible');
 assert.equal(element('send').disabled,false,'button recovers after failure');
 failure=false; await element('send').onclick(); await settle();
 assert.equal(element('code').value,'','clear only after server acknowledgement');
 assert.match(element('notice').textContent,/Code sent/);
 const body=JSON.parse(requests.find(r=>r[0]==='/login/code')[1].body);
 assert.equal(body.sessionId,'session-1'); assert.equal(body.code,'test-code#test-state');
})().catch(e=>{console.error(e);process.exitCode=1});
'''.replace('SCRIPT', json.dumps(script))
        result = subprocess.run(['node','-e',harness],capture_output=True,text=True)
        self.assertEqual(result.returncode,0,result.stderr)

    def test_incomplete_link_disables_actions_without_requests(self):
        script = re.search(r'<script>(.*?)</script>', PAGE, re.S).group(1)
        harness = r"""
const vm=require('node:vm'),assert=require('node:assert/strict');
const nodes=new Map();
const element=id=>{if(!nodes.has(id))nodes.set(id,{textContent:'',hidden:id==='pair',disabled:false});return nodes.get(id)};
let requests=0;
const context={document:{getElementById:element,addEventListener(){}},URLSearchParams,
sessionStorage:{getItem:()=>null},location:{hash:''},AbortSignal,
fetch:()=>{requests++;throw new Error('unexpected request')}};
vm.createContext(context);vm.runInContext(SCRIPT,context);
assert.equal(requests,0);
assert.match(element('status').textContent,/incomplete/i);
for(const id of ['login','send','refresh','code'])assert.equal(element(id).disabled,true,id);
assert.equal(element('pair').hidden,true);
""".replace('SCRIPT', json.dumps(script))
        result = subprocess.run(['node','-e',harness],capture_output=True,text=True)
        self.assertEqual(result.returncode,0,result.stderr)
        self.assertIn('[hidden]{display:none!important}', PAGE)


if __name__ == '__main__':
    unittest.main()


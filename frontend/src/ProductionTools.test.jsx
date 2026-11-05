import React from 'react';
import {afterEach,expect,it,vi} from 'vitest';
import {cleanup,render,screen,waitFor} from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import ProductionTools from './ProductionTools.jsx';
afterEach(cleanup);
const id='f40e5d84-f717-4113-b7ba-55a72f4ed667';
function api(){return vi.fn(async(path,options)=>{
 if(path==='/tools/readiness')return {status:'ready',checks:[],disk_free_gb:100,memory_available_gb:80};
 if(path==='/tools/speech-profiles')return {profiles:[]};
 if(path==='/dashboard')return {jobs:[{job_id:id,title:'Source video',video_exists:true,status:'approved'}]};
 if(path==='/tools/jobs'&&!options?.method)return {jobs:[]};
 if(path==='/tools/jobs')return {tool_job_id:id,status:'queued'};
 if(path==='/tools/stock/search')return {results:[{candidate_id:id,id:'12',kind:'video',attribution:'Video by Creator on Pexels',page_url:'https://www.pexels.com/video/12',width:720,height:1280}]};
 if(path==='/tools/research-dossiers')return {dossier_id:id,topic:'Cafe research'};
 throw new Error(path);
});}
it('does not start work until a person chooses a tool',async()=>{
 const request=api();render(<ProductionTools request={request} onNavigate={vi.fn()}/>);
 await screen.findByText('Readiness · ready');
 expect(request.mock.calls.filter(([,o])=>o?.method==='POST')).toHaveLength(0);
 expect(screen.getByRole('button',{name:'Generate narration sample'})).toBeDisabled();
});
it('requires source-rights confirmation before licensed download',async()=>{
 const user=userEvent.setup(),request=api();render(<ProductionTools request={request}/>);
 await user.type(screen.getByLabelText('Search licensed media'),'cafe');await user.click(screen.getByRole('button',{name:'Search Pexels'}));
 const download=await screen.findByRole('button',{name:'Download licensed asset'});expect(download).toBeDisabled();
 await user.click(screen.getByRole('checkbox',{name:/reviewed the license/}));await user.click(download);
 await waitFor(()=>expect(request.mock.calls.some(([p,o])=>p==='/tools/jobs'&&o?.method==='POST')).toBe(true));
 const call=request.mock.calls.find(([p,o])=>p==='/tools/jobs'&&o?.method==='POST');
 expect(JSON.parse(call[1].body)).toMatchObject({kind:'stock',candidate_id:id,confirmed:true,rights_confirmed:true});
 expect(request.mock.calls.some(([p])=>/publish|approve/.test(p))).toBe(false);
});
it('splits research source URLs by newline and keeps factual review explicit',async()=>{
 const user=userEvent.setup(),request=api();render(<ProductionTools request={request}/>);
 await user.type(screen.getByLabelText('Research topic'),'Cafe research');
 await user.type(screen.getByLabelText('Source-supported claim'),'A documented cafe claim.');
 await user.type(screen.getByLabelText('Primary-source URLs, one per line'),'https://example.org/one\nhttps://example.org/two');
 const save=screen.getByRole('button',{name:'Save reviewed dossier'});expect(save).toBeDisabled();
 await user.click(screen.getByRole('checkbox',{name:/read these sources/}));await user.click(save);
 const call=request.mock.calls.find(([p,o])=>p==='/tools/research-dossiers'&&o?.method==='POST');
 expect(JSON.parse(call[1].body).claims[0].sources).toEqual(['https://example.org/one','https://example.org/two']);
 await screen.findByText(/Saved “Cafe research”/);
});

it('invalidates research confirmation when its claim changes',async()=>{
 const user=userEvent.setup(),request=api();render(<ProductionTools request={request}/>);
 await user.type(screen.getByLabelText('Research topic'),'Cafe research');
 await user.type(screen.getByLabelText('Source-supported claim'),'A checked claim.');
 await user.type(screen.getByLabelText('Primary-source URLs, one per line'),'https://example.org/one');
 await user.click(screen.getByRole('checkbox',{name:/read these sources/}));
 const save=screen.getByRole('button',{name:'Save reviewed dossier'});expect(save).toBeEnabled();
 await user.type(screen.getByLabelText('Source-supported claim'),' Changed.');
 expect(save).toBeDisabled();
 expect(request.mock.calls.some(([p,o])=>p==='/tools/research-dossiers'&&o?.method==='POST')).toBe(false);
});

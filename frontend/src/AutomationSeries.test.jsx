import React from 'react';
import {afterEach,expect,it,vi} from 'vitest';
import {cleanup,render,screen,waitFor} from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import AutomationSeries from './AutomationSeries.jsx';
afterEach(cleanup);
function row(){return {series_id:'one',title:'The Drowned Bell',status:'awaiting_cast',planning_status:'completed',stage:'awaiting_cast',updated_at:'version1',premise:'A fictional coastal mystery.',receipts:[],bible:{characters:[{character_id:'ada',name:'Ada',description:'Coastal engineer',wardrobe:'Navy coat',voice_notes:'Low voice'}]},episodes:Array.from({length:30},(_,i)=>({ep_no:i+1,title:`Record ${i+1}`,hook:`Concrete coastal danger ${i+1}`,phase:'setup',duration_seconds:450}))};}
function api(){const value=row();return vi.fn(async(path,options)=>{
 if(path==='/automation/series'&&!options)return {series:[value]};
 if(path==='/generation/cast-packages')return {packages:[{package_id:'cast',name:'Approved coastal actors'}]};
 if(path==='/automation/series/one/outline')return {...value,episodes:JSON.parse(options.body).episodes,updated_at:'version2'};
 if(path==='/automation/series/one/approve-cast')return {...value,status:'active'};
 if(path==='/automation/series'&&options)return {...value,status:'planning',planning_status:'queued',episodes:[]};
 if(path==='/automation/series/one')return value;
 throw new Error(path);
});}
it('shows 30 hooks and requires explicit cast confirmation before activation',async()=>{
 const request=api(),user=userEvent.setup();render(<AutomationSeries request={request}/>);
 await user.click(await screen.findByRole('button',{name:/The Drowned Bell/}));
 expect(screen.getAllByRole('button',{name:/^Day \d+:/})).toHaveLength(30);
 expect(screen.getByText('Concrete coastal danger 30')).toBeInTheDocument();
 const approve=screen.getByRole('button',{name:'Approve cast and activate series'});expect(approve).toBeDisabled();
 await user.selectOptions(screen.getByLabelText('Approved cast package'),'cast');expect(approve).toBeDisabled();
 await user.click(screen.getByRole('checkbox',{name:/inspected the reference images/}));expect(approve).toBeEnabled();await user.click(approve);
 expect(JSON.parse(request.mock.calls.find(([p])=>p.endsWith('/approve-cast'))[1].body)).toMatchObject({confirmed:true,package_id:'cast',expected_updated_at:'version1'});
 expect(request.mock.calls.some(([p])=>/publish|\/run$/.test(p))).toBe(false);
});
it('stages an episode edit and sends all 30 with the original version',async()=>{
 const request=api(),user=userEvent.setup();render(<AutomationSeries request={request}/>);
 await user.click(await screen.findByRole('button',{name:/The Drowned Bell/}));
 const editor=screen.getByRole('textbox',{name:'Day 1 outline JSON'});await user.clear(editor);
 await user.paste(JSON.stringify({...row().episodes[0],title:'Revised arrival'}));await user.click(screen.getByRole('button',{name:'Stage day edit'}));
 await user.click(screen.getByRole('button',{name:'Save validated outline'}));
 await waitFor(()=>expect(request.mock.calls.some(([p])=>p.endsWith('/outline'))).toBe(true));
 const sent=JSON.parse(request.mock.calls.find(([p])=>p.endsWith('/outline'))[1].body);
 expect(sent.episodes).toHaveLength(30);expect(sent.episodes[0].title).toBe('Revised arrival');expect(sent.expected_updated_at).toBe('version1');
});
it('requires local planning consent and submits one persisted request ID',async()=>{
 const request=api(),user=userEvent.setup();render(<AutomationSeries request={request}/>);
 const button=screen.getByRole('button',{name:'Plan a new 30-episode series'});expect(button).toBeDisabled();
 await user.click(screen.getByRole('checkbox',{name:/authorize local story planning only/}));await user.click(button);
 const post=request.mock.calls.find(([p,o])=>p==='/automation/series'&&o?.method==='POST');
 expect(JSON.parse(post[1].body)).toMatchObject({genre:'mystery drama',confirmed:true});expect(JSON.parse(post[1].body).request_id).toBeTruthy();
});
it('retains the same request ID after an uncertain browser response',async()=>{
 const normal=api();let posts=0;
 const request=vi.fn(async(path,options)=>{if(path==='/automation/series'&&options?.method==='POST'&&posts++===0)throw new Error('Response lost; inspect history');return normal(path,options);});
 const user=userEvent.setup();render(<AutomationSeries request={request}/>);
 await user.click(screen.getByRole('checkbox',{name:/authorize local story planning only/}));
 await user.click(screen.getByRole('button',{name:'Plan a new 30-episode series'}));await screen.findByRole('alert');
 await user.click(screen.getByRole('button',{name:'Plan a new 30-episode series'}));
 const calls=request.mock.calls.filter(([p,o])=>p==='/automation/series'&&o?.method==='POST');expect(calls).toHaveLength(2);
 expect(JSON.parse(calls[0][1].body).request_id).toBe(JSON.parse(calls[1][1].body).request_id);
});
it('requires authorization to revise a known failed outline while preserving its source ID',async()=>{
 const normal=api();const request=vi.fn(async(path,options)=>path==='/automation/series/one'?{...row(),status:'planning',planning_status:'failed',episodes:row().episodes.slice(0,5)}:normal(path,options));
 const user=userEvent.setup();render(<AutomationSeries request={request}/>);
 await user.click(await screen.findByRole('button',{name:/The Drowned Bell/}));
 const revise=screen.getByRole('button',{name:'Revise remaining outline'});expect(revise).toBeDisabled();
 await user.click(screen.getByRole('checkbox',{name:/authorize local story planning only/}));await user.click(revise);
 const call=request.mock.calls.find(([p,o])=>p==='/automation/series'&&o?.method==='POST');
 expect(JSON.parse(call[1].body).source_series_id).toBe('one');expect(JSON.parse(call[1].body).confirmed).toBe(true);
});

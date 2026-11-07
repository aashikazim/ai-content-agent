import React from 'react';
import {afterEach,expect,it,vi} from 'vitest';
import {cleanup,render,screen,waitFor} from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import Automation from './Automation.jsx';
afterEach(cleanup);
function api(enabled=true){return vi.fn(async(path,options)=>{
 if(path==='/automation/lanes')return {enabled,lanes:{}};
 if(path.startsWith('/automation/plan/')&&path.endsWith('/run'))return {run:{status:'queued'}};
 if(path.startsWith('/automation/plan/'))return {slots:[],run:null};
 if(path.startsWith('/automation/reports/'))return {markdown:'No report yet.'};
 if(path==='/automation/plan-preview')return {topics:[{topic_id:'one',title:'An original topic',category:'comedy',reasons:['quota comedy']}],backups:[],warnings:[]};
 throw new Error(path);
});}
it('loads a day without creating work and previews without rendering',async()=>{
 const request=api(),user=userEvent.setup();render(<Automation request={request}/>);
 await screen.findByRole('button',{name:'Create daily plan and queue Shorts'});
 expect(screen.getByRole('button',{name:'Create daily plan and queue Shorts'})).toBeDisabled();
 expect(request.mock.calls.every(([,options])=>!options?.method)).toBe(true);
 await user.click(screen.getByRole('button',{name:'Preview topics'}));await screen.findByText('An original topic');
 expect(request.mock.calls.filter(([p,o])=>o?.method==='POST').map(([p])=>p)).toEqual(['/automation/plan-preview']);
});
it('requires local batch consent and never approves or publishes',async()=>{
 const request=api(),user=userEvent.setup();render(<Automation request={request}/>);
 await user.click(screen.getByRole('checkbox',{name:/authorize this local rendering batch/}));
 const button=screen.getByRole('button',{name:'Create daily plan and queue Shorts'});
 await waitFor(()=>expect(button).toBeEnabled());await user.click(button);
 const call=request.mock.calls.find(([p])=>p.endsWith('/run'));
 expect(JSON.parse(call[1].body)).toEqual({confirmed:true});
 expect(request.mock.calls.some(([p])=>/approve|publish/.test(p))).toBe(false);
});
it('keeps run disabled when automation is disabled',async()=>{
 render(<Automation request={api(false)}/>);await screen.findByText(/Automation is disabled/);
 expect(screen.getByRole('button',{name:'Create daily plan and queue Shorts'})).toBeDisabled();
});

import React from 'react';
import {afterEach,expect,it,vi} from 'vitest';
import {cleanup,render,screen,waitFor} from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import AutomationGovernor from './AutomationGovernor.jsx';
afterEach(cleanup);
function requests(){return vi.fn(async(path,options)=>{if(options)return {};if(path.endsWith('/events'))return {events:[]};if(path.endsWith('/authorizations'))return {authorizations:[]};return {enabled:true,dry_run:true,killed:false,platforms:[],actions:[],lanes:{shorts_daily:{enabled:true,approval_mode:'manual'}}};});}
it('defaults manual and requires explicit consent to change approval mode',async()=>{
 const request=requests(),user=userEvent.setup();render(<AutomationGovernor request={request}/>);
 const button=await screen.findByRole('button',{name:'Enable auto with gates'});expect(button).toBeDisabled();
 expect(screen.getByText(/DRY RUN/)).toBeInTheDocument();
 await user.click(screen.getByRole('checkbox'));await user.click(button);
 await waitFor(()=>expect(request.mock.calls.some(([p])=>p.endsWith('/approval'))).toBe(true));
 const [,options]=request.mock.calls.find(([p])=>p.endsWith('/approval'));
 expect(JSON.parse(options.body)).toEqual({confirmed:true,approval_mode:'auto_with_gates'});
 expect(request.mock.calls.some(([p])=>p.endsWith('/publish'))).toBe(false);
});
it('kill switch is available immediately without an enabling checkbox',async()=>{
 const request=requests(),user=userEvent.setup();render(<AutomationGovernor request={request}/>);
 await user.click(screen.getByRole('button',{name:'Stop Governor now'}));
 await waitFor(()=>expect(request.mock.calls.some(([p])=>p.endsWith('/state'))).toBe(true));
 const [,options]=request.mock.calls.find(([p])=>p.endsWith('/state'));
 expect(JSON.parse(options.body)).toMatchObject({confirmed:true,scope:'global',kill_switch:true});
});

it('kill remains usable while a Governor evaluation is awaiting completion',async()=>{
 const base=requests();let finish;const pending=new Promise(resolve=>{finish=resolve;});
 const request=vi.fn((path,options)=>path.endsWith('/tick')?pending:base(path,options));
 const user=userEvent.setup();render(<AutomationGovernor request={request}/>);
 await screen.findByText(/DRY RUN/);await user.click(screen.getByRole('checkbox'));
 await user.click(screen.getByRole('button',{name:'Evaluate configured Governor'}));
 expect(screen.getByRole('button',{name:'Stop Governor now'})).toBeEnabled();
 await user.click(screen.getByRole('button',{name:'Stop Governor now'}));
 await waitFor(()=>expect(request.mock.calls.some(([p])=>p.endsWith('/state'))).toBe(true));
 finish({status:'killed'});
});

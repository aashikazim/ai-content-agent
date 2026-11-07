import React from 'react';
import {afterEach,expect,it,vi} from 'vitest';
import {cleanup,render,screen,waitFor} from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import AutomationEpisodes from './AutomationEpisodes.jsx';
afterEach(cleanup);
it('requires local rendering consent and never approves or publishes',async()=>{
 const request=vi.fn(async(path,options)=>options?{status:'queued'}:{episodes:[{episode_id:'one',ep_no:1,status:'outlined'}]});
 const user=userEvent.setup();render(<AutomationEpisodes request={request} seriesId="story"/>);
 await user.click(screen.getByText('Episode production and buffer'));
 const button=await screen.findByRole('button',{name:'Queue episode 1'});expect(button).toBeDisabled();
 await user.click(screen.getByRole('checkbox',{name:/authorize local episode generation/}));await user.click(button);
 await waitFor(()=>expect(request.mock.calls.some(([p])=>p.endsWith('/1/run'))).toBe(true));
 expect(JSON.parse(request.mock.calls.find(([p])=>p.endsWith('/1/run'))[1].body)).toEqual({confirmed:true});
 expect(request.mock.calls.some(([p])=>/publish|approve/.test(p))).toBe(false);
});
it('never queues an episode that already has a production intent',async()=>{
 const request=vi.fn(async()=>({episodes:[{episode_id:'one',ep_no:1,status:'outlined',production_status:'needs_reconciliation'}]}));
 const user=userEvent.setup();render(<AutomationEpisodes request={request} seriesId="story"/>);await user.click(screen.getByText('Episode production and buffer'));
 await user.click(screen.getByRole('checkbox',{name:/authorize local episode generation/}));
 expect(await screen.findByRole('button',{name:'Queue episode 1'})).toBeDisabled();
 expect(request.mock.calls.every(([,options])=>!options)).toBe(true);
});

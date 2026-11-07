import React from 'react';
import {afterEach,expect,it,vi} from 'vitest';
import {cleanup,render,screen} from '@testing-library/react';
import AutomationOperations from './AutomationOperations.jsx';
afterEach(cleanup);
it('shows read-only operations with measured and seed labels',async()=>{
 const request=vi.fn(async()=>({initialized:true,at:'fixture time',queue:[{kind:'automation',status:'queued',count:2}],workers:[],attention:[],events:[],series:[],measurements:[{model:'ltx-2.5',mode:'text',is_seed:1,samples:1,seconds_per_video_second:29,neural_seconds:29}]}));
 render(<AutomationOperations request={request}/>);
 expect(await screen.findByText(/Historical seed/)).toBeInTheDocument();
 expect(screen.getByText(/No worker heartbeat/)).toBeInTheDocument();
 expect(request.mock.calls.every(([,options])=>!options)).toBe(true);
});
it('explains uninitialized storage without requesting generation',async()=>{
 const request=vi.fn(async()=>({initialized:false,queue:[],workers:[],attention:[],events:[],series:[],measurements:[]}));
 render(<AutomationOperations request={request}/>);
 expect(await screen.findByText(/has not been initialized/)).toBeInTheDocument();
 expect(request).toHaveBeenCalledTimes(1);
});

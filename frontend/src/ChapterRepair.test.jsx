import React from 'react';
import {afterEach,expect,it,vi} from 'vitest';
import {cleanup,render,screen} from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import ChapterRepair from './ChapterRepair.jsx';
afterEach(cleanup);
const parent={attempt_id:'parent-fixture',part_revisions:[]};
const part={part_number:2,status:'failed',error:'Duration did not match'};
function api(lost=false){return vi.fn(async(path,options)=>{
  if(path.endsWith('/quote'))return {quote_id:'quote-fixture',maximum_cost_usd:'1.06',prior_reserved_usd:'5.30'};
  if(path.endsWith('/parts/revisions')){if(lost)throw new Error('Acknowledgement lost');return {attempt_id:'revision-fixture',status:'queued'};}
  if(path.endsWith('/reconcile'))return {...parent,parts:[{...part,status:'failed'}]};
  if(path.endsWith('/revision-fixture'))return {status:'queued'};
  throw new Error(path);
});}
it('requires fresh cost consent and sends one separately quoted chapter revision',async()=>{
  const user=userEvent.setup(),request=api();render(<ChapterRepair request={request} parent={parent} part={part} onRefresh={vi.fn()}/>);
  await user.click(screen.getByText(/Resolve chapter 2/));
  await user.click(screen.getByRole('button',{name:'Estimate this chapter revision'}));
  expect(screen.getByRole('button',{name:'Generate this chapter revision'})).toBeDisabled();
  await user.click(screen.getByRole('checkbox',{name:/I authorize this one new chapter revision/}));
  await user.type(screen.getByLabelText('Chapter revision instructions'),' Keep the final clue.');
  expect(screen.queryByRole('button',{name:'Generate this chapter revision'})).not.toBeInTheDocument();
  await user.click(screen.getByRole('button',{name:'Estimate this chapter revision'}));
  await user.click(screen.getByRole('checkbox',{name:/I authorize this one new chapter revision/}));
  await user.click(screen.getByRole('button',{name:'Generate this chapter revision'}));
  expect(request.mock.calls.filter(([path])=>path.endsWith('/parts/revisions'))).toHaveLength(1);
  expect(request.mock.calls.some(([path])=>/publish|\/continue$/.test(path))).toBe(false);
});
it('does not retry a chapter revision after a lost acknowledgement',async()=>{
  const user=userEvent.setup(),request=api(true);render(<ChapterRepair request={request} parent={parent} part={part} onRefresh={vi.fn()}/>);
  await user.click(screen.getByText(/Resolve chapter 2/));
  await user.click(screen.getByRole('button',{name:'Estimate this chapter revision'}));
  await user.click(screen.getByRole('checkbox',{name:/I authorize this one new chapter revision/}));
  await user.click(screen.getByRole('button',{name:'Generate this chapter revision'}));
  expect(await screen.findByText(/Submission outcome is unknown/)).toBeVisible();
  expect(screen.getByRole('button',{name:'Generate this chapter revision'})).toBeDisabled();
  expect(request.mock.calls.filter(([path])=>path.endsWith('/parts/revisions'))).toHaveLength(1);
});
it('closes an uncertain chapter only after inspection and never generates a replacement automatically',async()=>{
  const user=userEvent.setup(),request=api(),onRefresh=vi.fn();
  render(<ChapterRepair request={request} parent={parent} part={{...part,status:'needs_reconciliation'}} onRefresh={onRefresh}/>);
  await user.click(screen.getByText(/Resolve chapter 2/));
  const close=screen.getByRole('button',{name:'Close uncertain chapter without retry'});
  expect(close).toBeDisabled();
  await user.type(screen.getByLabelText('Chapter inspection notes'),'Inspected provider usage.');
  await user.click(screen.getByRole('checkbox',{name:/I inspected this chapter/}));await user.click(close);
  expect(onRefresh).toHaveBeenCalled();
  expect(request.mock.calls.map(([path])=>path)).toEqual(['/generation/scripts/parent-fixture/parts/2/reconcile']);
});

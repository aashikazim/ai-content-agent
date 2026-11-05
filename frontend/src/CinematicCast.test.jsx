import React from 'react';
import {afterEach,expect,it,vi} from 'vitest';
import {cleanup,render,screen,waitFor} from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import CinematicCast from './CinematicCast.jsx';
afterEach(cleanup);
const ref='da7fb41f-eafa-493d-9b28-31358dc98852';
const identifier='da7fb41f-eafa-493d-9b28-31358dc98853';
const reference={reference_id:ref,character_id:'alex',name:'Alex',description:'Dark curly hair',wardrobe:'Blue jacket',voice_notes:'Calm',image_url:'/fixture.png'};
it('freezes a cast only after inspection and invalidates consent when the outfit changes',async()=>{
  const user=userEvent.setup(),onChange=vi.fn();let saved=null;
  const request=vi.fn(async(path,options)=>{
    if(path==='/generation/references')return {references:[reference]};
    if(path==='/generation/cast-packages' && options?.method==='POST') {saved=JSON.parse(options.body);return {...saved,package_id:identifier};}
    if(path==='/generation/cast-packages')return {packages:saved ? [{...saved,package_id:identifier}]:[]};
    throw new Error(path);
  });
  render(<CinematicCast request={request} value={null} onChange={onChange}/>);
  await user.click(screen.getByText('Create a cast package'));
  await user.click(await screen.findByRole('checkbox',{name:/Alex · alex/}));
  const save=screen.getByRole('button',{name:'Save approved cast package'});
  expect(save).toBeDisabled();
  await user.selectOptions(screen.getByLabelText('Fixed local voice for alex'),'en-us+m1');
  await user.click(screen.getByRole('checkbox',{name:/I inspected the actors/}));
  expect(save).toBeEnabled();
  await user.type(screen.getByLabelText('Fixed outfit for alex'),' and a scarf');
  expect(save).toBeDisabled();
  await user.click(screen.getByRole('checkbox',{name:/I inspected the actors/}));
  await user.click(save);
  await waitFor(()=>expect(onChange).toHaveBeenCalledWith(identifier));
  expect(saved.characters[0]).toMatchObject({character_id:'alex',reference_ids:[ref],primary_reference_id:ref,tts_voice:'en-us+m1',wardrobe:'Blue jacket and a scarf'});
  expect(request.mock.calls.filter(([,options])=>options?.method==='POST')).toHaveLength(1);
});

it('chooses an existing immutable package without submitting work',async()=>{
  const user=userEvent.setup(),onChange=vi.fn();
  const request=vi.fn(async path=>path==='/generation/references'?{references:[reference]}:{packages:[{package_id:identifier,name:'Office cast',characters:[{name:'Alex',character_id:'alex',wardrobe:'Blue jacket',reference_ids:[ref],primary_reference_id:ref}]}]});
  render(<CinematicCast request={request} value={null} onChange={onChange}/>);
  await screen.findByRole('option',{name:/Office cast/});
  await user.selectOptions(screen.getByLabelText('Approved cast package'),identifier);
  expect(onChange).toHaveBeenCalledWith(identifier);
  expect(request.mock.calls.every(([,options])=>!options?.method)).toBe(true);
});

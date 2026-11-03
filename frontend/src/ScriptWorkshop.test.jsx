import React from 'react';
import { afterEach, expect, it, vi } from 'vitest';
import { cleanup, render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import ScriptWorkshop, { PaidScriptAuthorization } from './ScriptWorkshop.jsx';
afterEach(cleanup);
const cloud = { script_provider: 'openai', script_model: 'gpt-6.1-sol', topic: 'Original idea' };
const quote = { paid: true, quote_id: 'quote-fixture', maximum_cost_usd: '0.21' };
const identifier = 'ba7fb41f-eafa-493d-9b28-31358dc98848';
const submissions = request => request.mock.calls.filter(([path, options]) => path === '/generation/scripts' && options?.method === 'POST');
function api(submit = async () => ({ attempt_id: identifier, status: 'completed' })) {
  return vi.fn(async (path, options) => {
    if (path === '/generation/script-quotes') return quote;
    if (path === '/generation/scripts' && options?.method === 'POST') return submit();
    if (path === '/generation/scripts') return { attempts: [] };
    throw new Error(`Unexpected ${path}`);
  });
}
it('requires an explicit quote and checkbox and clears authorization after settings change', async () => {
  const user = userEvent.setup(); const request = api(); const onChange = vi.fn();
  const props = { request, configuration: cloud, context: {}, purpose: 'script', onChange };
  const { rerender } = render(<PaidScriptAuthorization {...props} />);
  expect(request).not.toHaveBeenCalled();
  await user.click(screen.getByRole('button', { name: /Check access & estimate/ }));
  expect(onChange).not.toHaveBeenCalledWith({ quote_id: quote.quote_id, confirmed: true });
  await user.click(await screen.findByRole('checkbox'));
  expect(onChange).toHaveBeenLastCalledWith({ quote_id: quote.quote_id, confirmed: true });
  rerender(<PaidScriptAuthorization {...props} configuration={{ ...cloud, topic: 'Changed' }} />);
  await waitFor(() => expect(onChange).toHaveBeenLastCalledWith(null));
  expect(screen.queryByRole('checkbox')).not.toBeInTheDocument();
});
it('sends one authorized draft and clears the consumed quote without publishing', async () => {
  const user = userEvent.setup(); const request = api();
  render(<ScriptWorkshop configuration={cloud} request={request} category="science" region="us" limit={5} />);
  expect(screen.getByRole('button', { name: 'Generate script draft' })).toBeDisabled();
  await user.click(screen.getByRole('button', { name: /Check access & estimate/ }));
  await user.click(await screen.findByRole('checkbox'));
  await user.click(screen.getByRole('button', { name: 'Generate script draft' }));
  await screen.findByText('Script status: completed');
  expect(submissions(request)).toHaveLength(1);
  expect(JSON.parse(submissions(request)[0][1].body)).toMatchObject({ purpose: 'script', quote_id: quote.quote_id, confirmed: true, region: 'US' });
  expect(screen.queryByRole('checkbox')).not.toBeInTheDocument();
  expect(screen.getByRole('button', { name: 'Generate script draft' })).toBeDisabled();
  expect(request.mock.calls.some(([path]) => /review|publish/.test(path))).toBe(false);
});
it('blocks another submission after a lost acknowledgement without retrying', async () => {
  const user = userEvent.setup(); const request = api(async () => { throw new Error('Connection interrupted'); });
  render(<ScriptWorkshop configuration={{ script_provider: 'ollama' }} request={request} category="science" region="US" limit={5} />);
  await user.click(screen.getByRole('button', { name: 'Generate script draft' }));
  await screen.findByText(/Request outcome is unknown/);
  expect(screen.getByRole('button', { name: 'Generate script draft' })).toBeDisabled();
  await user.click(screen.getByRole('button', { name: 'Refresh script history' }));
  expect(submissions(request)).toHaveLength(1);
  expect(screen.getByRole('button', { name: 'Allow a separately authorized new request' })).toBeDisabled();
});
it('sends revision instructions only after an explicit request and preserves the original', async () => {
  const user = userEvent.setup(); const request = api(); const original = request.getMockImplementation();
  request.mockImplementation(async (path, options) => !options?.method && path === '/generation/scripts'
    ? { attempts: [{ attempt_id: 'parent-fixture', model: 'qwen3:8b', status: 'completed', production_plan: { content_plan: { title: 'Original draft' } } }] }
    : original(path, options));
  render(<ScriptWorkshop configuration={{ script_provider: 'ollama' }} request={request} category="science" region="US" limit={5} />);
  await screen.findByRole('option', { name: 'Original draft' });
  await user.selectOptions(screen.getByLabelText('Revise a completed draft'), 'parent-fixture');
  await user.type(screen.getByLabelText('Revision instructions (optional)'), 'Improve the opening');
  expect(submissions(request)).toHaveLength(0);
  await user.click(screen.getByRole('button', { name: 'Generate script draft' }));
  await waitFor(() => expect(submissions(request)).toHaveLength(1));
  expect(JSON.parse(submissions(request)[0][1].body)).toMatchObject({ parent_attempt_id: 'parent-fixture', revision_instructions: 'Improve the opening' });
});

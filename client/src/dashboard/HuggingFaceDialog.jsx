import { useRef, useState } from 'react';
import { api } from '../api.js';
import { Field, Modal, Spinner } from '../ui.jsx';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { NativeSelect } from '@/components/ui/native-select';

const INPUT_NAMES = ['question', 'input', 'prompt', 'query', 'instruction', 'text'];
const REPLY_NAMES = ['answer', 'output', 'response', 'completion', 'reply', 'target', 'label'];
const MAX_ROWS = 200;

function guessColumn(columns, names, avoid) {
  const options = columns.filter((column) => column !== avoid);
  const match = names.find((name) => options.includes(name));
  return match ?? options[0] ?? '';
}

function datasetName(value) {
  return value
    .trim()
    .replace(/^https?:\/\/(www\.)?huggingface\.co\/datasets\//, '')
    .replace(/[?#].*$/, '')
    .split('/')
    .slice(0, 2)
    .join('/');
}

export default function HuggingFaceDialog({ projectId, onClose }) {
  const [dataset, setDataset] = useState('');
  const [preview, setPreview] = useState(null);
  const [inputColumn, setInputColumn] = useState('');
  const [replyColumn, setReplyColumn] = useState('');
  const [grading, setGrading] = useState('meaning');
  const [testSplit, setTestSplit] = useState('dev');
  const [rowCount, setRowCount] = useState('20');
  const [pending, setPending] = useState(false);
  const [error, setError] = useState('');
  const [done, setDone] = useState(null);
  const imported = useRef(false);

  function close() {
    if (!pending) onClose(imported.current);
  }

  function changeDataset(value) {
    setDataset(value);
    setPreview(null);
    setDone(null);
  }

  async function load(option) {
    const name = datasetName(dataset);
    if (!name) {
      setError('Enter a dataset name.');
      return;
    }
    setPending(true);
    setError('');
    setDone(null);
    try {
      const params = new URLSearchParams({ dataset: name, ...option });
      const data = await api(`/v1/huggingface/datasets?${params}`);
      setPreview(data);
      const input = guessColumn(data.columns, INPUT_NAMES);
      setInputColumn(input);
      setReplyColumn(guessColumn(data.columns, REPLY_NAMES, input));
    } catch (e) {
      setPreview(null);
      setError(e?.message ?? 'Could not load the dataset.');
    } finally {
      setPending(false);
    }
  }

  async function submit(event) {
    event.preventDefault();
    if (pending || !preview) return;
    if (inputColumn === replyColumn) {
      setError('Choose different columns for the input and the expected reply.');
      return;
    }
    setPending(true);
    setError('');
    try {
      const result = await api(`/v1/projects/${projectId}/test-cases/huggingface`, {
        method: 'POST',
        body: JSON.stringify({
          dataset: preview.dataset,
          config: preview.config,
          split: preview.split,
          input_column: inputColumn,
          expected_column: replyColumn,
          grading,
          test_split: testSplit,
          limit: Number(rowCount),
        }),
      });
      imported.current = true;
      setDone(result);
    } catch (e) {
      setError(e?.message ?? 'Could not import the dataset.');
    } finally {
      setPending(false);
    }
  }

  const sample = preview?.rows?.[0];
  const columnField = (label, value, onChange) => (
    <Field label={label}>
      {(id) => (
        <NativeSelect wrapperClassName="w-full" id={id} value={value} onChange={(e) => onChange(e.target.value)} disabled={pending}>
          {preview.columns.map((column) => (
            <option key={column} value={column}>
              {column}
            </option>
          ))}
        </NativeSelect>
      )}
    </Field>
  );

  return (
    <Modal title="Import from Hugging Face" onClose={close}>
      <form onSubmit={submit}>
        <Field label="Public dataset">
          {(id) => (
            <Input
              id={id}
              value={dataset}
              placeholder="e.g. rag-datasets/rag-mini-wikipedia"
              onChange={(e) => changeDataset(e.target.value)}
              disabled={pending}
              autoComplete="off"
            />
          )}
        </Field>
        <p>
          <Button type="button" variant="outline" onClick={() => load({})} disabled={pending}>
            {pending && !preview ? 'Loading…' : 'Load dataset'}
          </Button>
        </p>
        {preview ? (
          <>
            <Field label="Subset and split">
              {(id) => (
                <NativeSelect wrapperClassName="w-full"
                  id={id}
                  value={`${preview.config}\n${preview.split}`}
                  onChange={(e) => {
                    const [config, split] = e.target.value.split('\n');
                    load({ config, split });
                  }}
                  disabled={pending}
                >
                  {preview.options.map((o) => (
                    <option key={`${o.config}\n${o.split}`} value={`${o.config}\n${o.split}`}>
                      {o.config} / {o.split}
                    </option>
                  ))}
                </NativeSelect>
              )}
            </Field>
            {columnField('Input column', inputColumn, setInputColumn)}
            {columnField('Expected reply column', replyColumn, setReplyColumn)}
            {sample ? (
              <div className="card hf-sample">
                <p className="muted">First row</p>
                <p>Input: {String(sample[inputColumn] ?? '—').slice(0, 200)}</p>
                <p>Expected: {String(sample[replyColumn] ?? '—').slice(0, 200)}</p>
              </div>
            ) : null}
            <Field label="Grading">
              {(id) => (
                <NativeSelect wrapperClassName="w-full" id={id} value={grading} onChange={(e) => setGrading(e.target.value)} disabled={pending}>
                  <option value="meaning">Same meaning (graded by the model)</option>
                  <option value="contains">Must contain the exact text</option>
                </NativeSelect>
              )}
            </Field>
            <Field label="Test group">
              {(id) => (
                <NativeSelect wrapperClassName="w-full" id={id} value={testSplit} onChange={(e) => setTestSplit(e.target.value)} disabled={pending}>
                  <option value="dev">Dev</option>
                  <option value="holdout">Holdout</option>
                </NativeSelect>
              )}
            </Field>
            <Field label={`Rows to import${preview.total ? ` (${preview.total} available)` : ''}`}>
              {(id) => (
                <Input
                  id={id}
                  type="number"
                  min="1"
                  max={MAX_ROWS}
                  step="1"
                  required
                  value={rowCount}
                  onChange={(e) => setRowCount(e.target.value)}
                  disabled={pending}
                />
              )}
            </Field>
          </>
        ) : null}
        {error ? (
          <p className="field-error" role="alert">
            {error}
          </p>
        ) : null}
        {pending ? <Spinner label={preview ? 'Working…' : 'Loading dataset…'} /> : null}
        {done ? (
          <p className="good-text" role="status">
            Imported {done.imported} tests, skipped {done.skipped} empty or duplicate rows. Use Run
            evaluation to test the agent on them.
          </p>
        ) : null}
        <div className="modal-actions">
          <Button type="button" variant="outline" onClick={close} disabled={pending}>
            {done ? 'Done' : 'Cancel'}
          </Button>
          <Button type="submit" variant="default" disabled={pending || !preview}>
            {pending && preview ? 'Working…' : 'Import tests'}
          </Button>
        </div>
      </form>
    </Modal>
  );
}

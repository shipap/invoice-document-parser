const $ = (id) => document.getElementById(id);
let extracted = null;
const headers = () => $('token').value ? {Authorization: 'Bearer ' + $('token').value} : {};
function selectFile(file) {
  if (!file) return;
  $('filename').textContent = file.name;
}
$('file').addEventListener('change', () => selectFile($('file').files[0]));
for (const event of ['dragenter', 'dragover']) $('dropzone').addEventListener(event, (e) => {
  e.preventDefault(); $('dropzone').classList.add('active');
});
for (const event of ['dragleave', 'drop']) $('dropzone').addEventListener(event, (e) => {
  e.preventDefault(); $('dropzone').classList.remove('active');
});
$('dropzone').addEventListener('drop', (e) => {
  if (e.dataTransfer.files.length) {
    $('file').files = e.dataTransfer.files; selectFile($('file').files[0]);
  }
});
function message(text, error = false) {
  $('status').textContent = text; $('status').className = error ? 'error' : '';
}
function download(blob, filename) {
  const url = URL.createObjectURL(blob); const a = document.createElement('a');
  a.href = url; a.download = filename; a.click(); setTimeout(() => URL.revokeObjectURL(url), 1000);
}
function render(result) {
  $('fields').replaceChildren(); $('warnings').replaceChildren(); $('items').replaceChildren();
  for (const warning of result.warnings) {
    const p = document.createElement('p'); p.textContent = warning; $('warnings').append(p);
  }
  for (const [key, value] of Object.entries(result.invoice)) {
    if (key === 'line_items') continue;
    const group = document.createElement('div'); const dt = document.createElement('dt');
    const dd = document.createElement('dd'); dt.textContent = key.replaceAll('_', ' ');
    dd.textContent = value ?? 'Not found'; group.append(dt, dd); $('fields').append(group);
  }
  for (const item of result.invoice.line_items) {
    const row = document.createElement('tr');
    for (const key of ['description', 'quantity', 'unit_price', 'tax', 'total']) {
      const cell = document.createElement('td'); cell.textContent = item[key] ?? '—'; row.append(cell);
    }
    $('items').append(row);
  }
  $('results').hidden = false; $('result-badge').textContent = result.warnings.length ? 'Review needed' : 'Ready for review';
}
$('upload-form').addEventListener('submit', async (e) => {
  e.preventDefault(); const file = $('file').files[0]; if (!file) return;
  extracted = null; $('results').hidden = true; $('result-badge').textContent = 'Processing';
  $('submit').disabled = true; message('Reading the PDF and checking invoice fields…');
  try {
    const form = new FormData(); form.append('file', file);
    const response = await fetch('/api/extract', {method: 'POST', body: form, headers: headers()});
    const data = await response.json();
    if (!response.ok) throw new Error(data.error?.message || 'Document could not be processed.');
    extracted = data; render(data); message('Extracted from ' + data.pages + ' page(s). Review before exporting.');
  } catch (error) { message(error.message, true); $('result-badge').textContent = 'Extraction failed'; }
  finally { $('submit').disabled = false; }
});
$('json-download').addEventListener('click', () => {
  if (extracted) download(new Blob([JSON.stringify(extracted, null, 2)], {type: 'application/json'}), 'invoice.json');
});
$('csv-download').addEventListener('click', async () => {
  if (!extracted) return;
  $('csv-download').disabled = true;
  try {
    const response = await fetch('/api/export/csv', {method: 'POST', headers: {...headers(), 'Content-Type': 'application/json'}, body: JSON.stringify(extracted.invoice)});
    if (!response.ok) { const data = await response.json(); throw new Error(data.error?.message || 'CSV export failed.'); }
    download(await response.blob(), 'invoice.csv');
  } catch (error) { message(error.message, true); }
  finally { $('csv-download').disabled = false; }
});
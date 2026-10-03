import json
from pathlib import Path
import tempfile
import threading
from urllib.request import Request, urlopen
from urllib.error import HTTPError
from friskoli_cad.replay_service import ReplayServer
from test_task_longrun import body, wait


def test_http_pause_checkpoint_upload_resume_and_paged_arrays():
    with tempfile.TemporaryDirectory() as directory:
        server = ReplayServer(('127.0.0.1',0),task_directory=Path(directory))
        thread = threading.Thread(target=server.serve_forever,daemon=True)
        thread.start()
        base = f'http://127.0.0.1:{server.server_port}'
        def request(path, value=None, raw=None, key=None, method=None):
            headers = {}
            if value is not None:
                raw = json.dumps(value).encode()
                headers['Content-Type'] = 'application/json'
            elif raw is not None:
                headers['Content-Type'] = 'application/vnd.friskoli.checkpoint+zip'
            if key: headers['Idempotency-Key'] = key
            try:
                response = urlopen(Request(base+path,data=raw,headers=headers,method=method),timeout=20)
            except HTTPError as error:
                response = error
            with response:
                content = response.read()
                return response.status, json.loads(content) if 'json' in response.headers.get('Content-Type','') else content
        try:
            submission = body(server.task_service,4)
            status, task = request('/api/runs',submission,key='http-modern')
            assert status == 202
            status,_ = request(f'/api/runs/{task["run_id"]}/pause',method='POST')
            assert status in (200,202)
            assert wait(server.task_service,task['run_id'])['status'] == 'paused'
            status, raw = request(f'/api/runs/{task["run_id"]}/checkpoint')
            assert status == 200 and raw.startswith(b'PK')
            status, staged = request('/api/runs/checkpoint-import',raw=raw)
            assert status == 201, staged
            status, child = request('/api/runs/from-checkpoint',{'checkpoint_id':staged['checkpoint_id'],'submission':submission},key='http-import')
            assert status == 202, child
            completed = wait(server.task_service,child['run_id'])
            assert completed['status'] == 'completed', completed
            status, manifest = request(f'/api/runs/{child["run_id"]}/result?offset=0&limit=1')
            assert status == 200 and len(manifest['chunks']) == 1 and manifest['next_chunk_offset'] == 1
            status, chunk = request(manifest['chunks'][0]['href'])
            assert status == 200
            field = next(iter(chunk['frames'][0]['concentrations'].values()))
            status, data = request(field['array']['segments'][0]['href'])
            assert status == 200 and len(data) == field['array']['bytes']
            status, error = request('/api/runs/checkpoint-import',raw=b'invalid zip')
            assert status == 422
        finally:
            server.shutdown()
            thread.join(5)
            server.server_close()

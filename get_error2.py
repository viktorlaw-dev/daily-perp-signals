import requests, sys, zipfile, io

token = sys.argv[1]
headers = {'Authorization': f'Bearer {token}', 'Accept': 'application/vnd.github+json'}
run_id = sys.argv[2]

r = requests.get(f'https://api.github.com/repos/viktorlaw-dev/daily-perp-signals/actions/runs/{run_id}/logs', headers=headers, stream=True, allow_redirects=True)
r.raise_for_status()

z = zipfile.ZipFile(io.BytesIO(r.content))
for name in z.namelist():
    if 'Run scanner' in name or '6_Run' in name:
        content = z.read(name).decode('utf-8', errors='replace')
        with open('actions_error2.log', 'w', encoding='utf-8') as f:
            f.write(content)
        print(f'Wrote {name} ({len(content)} chars)')

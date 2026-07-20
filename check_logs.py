import requests, sys

token = sys.argv[1]
headers = {'Authorization': f'Bearer {token}', 'Accept': 'application/vnd.github+json'}
run_id = sys.argv[2]

r = requests.get(f'https://api.github.com/repos/viktorlaw-dev/daily-perp-signals/actions/runs/{run_id}/jobs', headers=headers)
jobs = r.json()
for job in jobs.get('jobs', []):
    for step in job.get('steps', []):
        print(f"{step['name']}: {step['conclusion']}")
